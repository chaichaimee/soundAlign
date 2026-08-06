# soundUtils.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import threading
import queue
import math
import time
import array
import nvwave
import config
from logHandler import log

LEFT = 0
CENTER = 1
RIGHT = 2
LEFT_TO_RIGHT = 3
RIGHT_TO_LEFT = 4


def fraction_to_gain(fraction):
	"""
	Maps a 0.0-1.0 volume fraction to an audio gain using a square-law
	curve instead of using the fraction directly as a linear amplitude
	multiplier. Human loudness perception is much closer to logarithmic
	than linear: a straight linear multiplier barely changes perceived
	loudness across the top half of the range and barely attenuates
	anything in the bottom half either -- e.g. 50% sounding almost as
	loud as 80%, and 20% still sounding loud, is exactly what a linear
	multiplier produces. Squaring the fraction (a standard, simple
	approximation of a perceptual/audio-taper curve) makes each step of
	the control correspond much more evenly to a step in perceived
	loudness.
	"""
	fraction = max(0.0, min(1.0, fraction))
	return fraction * fraction

# SoundAlign only handles sounds played via tones.beep now (addon beeps and
# the progress indicator). Wave-file cues (NVDA's built-in error/start/exit/
# browse-focus-mode sounds) are intentionally not handled at all -- panning
# those required intercepting nvwave.playWaveFile and re-rendering the audio
# separately, which proved unreliable across multiple attempts. Keeping
# scope to tones.beep keeps this add-on simple and reliable.
PROGRESS_INDICATOR = "progress_indicator"
ADDON_BEEP = "addon_beep"

TONE_SINE = [1.0]
TONE_TRIANGLE = [1.0, 0.0, 1/9, 0.0, 1/25, 0.0, 1/49]
TONE_SAWTOOTH = [1.0, 1/2, 1/3, 1/4, 1/5, 1/6, 1/7]
TONE_SQUARE = [1.0, 0, 1/3, 0, 1/5, 0, 1/7, 0, 1/9, 0, 1/11]

SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2
PAN_BOOST_FACTOR = 1.0

# --- Progress-tone engine ---------------------------------------------
# This has gone through several designs:
#  1. Discrete chunks per NVDA beep event -> audible clicks at every
#     chunk boundary.
#  2. One continuous background generator, gliding frequency/pan/
#     amplitude toward the latest target -> click-free, and sweeps
#     nicely for short operations, but for anything that keeps running
#     for a while (e.g. converting a large video) it becomes one
#     unbroken drone, which is fatiguing to listen to.
#  3. A continuous/discrete hybrid that switched to ticking after a
#     fixed number of seconds -> the threshold was measured per audio
#     session, but a session gets torn down and restarted on every gap
#     between real progress updates longer than the inactivity timeout,
#     so for operations with naturally sparse updates the "how long has
#     this been running" clock kept resetting and the continuous mode
#     never actually handed off -- plus every one of those restarts
#     replayed the "sweep from the start" behaviour, which sounded
#     chaotic and repetitive over a long operation.
#
# This is the current, simpler design: every progress update renders and
# queues ONE short, self-contained "tick" -- gliding from wherever the
# previous tick left off to the new target, holding briefly, then fading
# back to silence -- and nothing plays in between updates at all. No
# continuous background loop, no "is this finished yet?" timeout logic:
# each tick is inherently self-terminating, so there's nothing to hang
# open or need to be wrapped up. Frequent updates naturally sound like a
# connected sweep (each tick glides in from the last one); infrequent
# updates naturally sound like separate discrete beeps. Either way nothing
# ever drones on continuously.
_TICK_SECONDS = 0.2
_TICK_ATTACK_FRACTION = 0.15
_TICK_DECAY_FRACTION = 0.35
# A silence longer than this before the next progress update is treated
# as a genuinely new, unrelated operation (so the next tick starts fresh
# from this direction's beginning again) rather than a continuation of
# the same one (which resumes from wherever the last tick left off).
_TASK_RESET_GAP = 3.0

# Short delay-line length used for the optional progress-tone reverb
# effect. Longer than a single tick by a wide margin so the buffer never
# wraps around within one.
_DELAY_SECONDS = 0.15
_DELAY_FEEDBACK = 0.20
_DELAY_MIX = 0.24


class SoundProcessor:

	def __init__(self, global_plugin):
		self.global_plugin = global_plugin
		# NVDA's own audio player (WASAPI-based), the exact same class
		# tones.py itself uses to play tones.beep. Earlier revisions of
		# this add-on opened a completely separate PyAudio/pyaudiowpatch
		# WASAPI stream running in parallel to NVDA's own audio engine --
		# that redundant second audio path is what caused the persistent
		# crackling, unpanned playback and doubled-sound bugs. Routing
		# through nvwave.WavePlayer instead means SoundAlign's synthesized
		# tones share the exact same, already battle-tested audio output
		# mechanism NVDA itself relies on for everything else, with no
		# separate/competing audio session.
		self.wave_player = None
		self.is_running = False
		self.volume = 0.5
		self.master_volume = 1.0
		self.fade_algorithm = "cosine"
		self.harmonics = TONE_SINE
		self.min_frequency = 110
		self.max_frequency = 1760
		self.reverb_enabled = False
		self._lock = threading.RLock()

		# Running oscillator phase (radians), incremented per-sample using
		# the instantaneous (per-sample interpolated) frequency. This is a
		# proper phase-integrated oscillator rather than amplitude =
		# sin(2*pi*f*t): with frequency itself changing continuously
		# (glide), only integrating it sample-by-sample keeps phase
		# genuinely continuous with no seams, at a block boundary or
		# anywhere else.
		self._tone_phase = 0.0

		# State for the optional progress-tone reverb delay line.
		delayLen = max(1, int(SAMPLE_RATE * _DELAY_SECONDS))
		self._delay_buffer_left = array.array('h', [0] * delayLen)
		self._delay_buffer_right = array.array('h', [0] * delayLen)
		self._delay_pos = 0

		# Progress-tick engine state. play_progress_sound() just records
		# the latest target and wakes the worker thread; the worker
		# renders and queues one tick per wake-up, then goes back to
		# waiting. A separate FEEDER thread drains the render queue into
		# wave_player.feed() (see _progress_feeder_loop) so a momentarily
		# slow render can't directly stall audio timing.
		self._progress_lock = threading.Lock()
		self._progress_pending = None
		self._progress_last_frequency = None
		self._progress_last_pan = 0.5
		self._progress_last_event_time = 0.0
		self._progress_worker_thread = None
		self._progress_feeder_thread = None
		self._progress_wake = threading.Event()
		self._progress_queue = queue.Queue(maxsize=32)

		try:
			try:
				outputDevice = config.conf["audio"]["outputDevice"]
			except KeyError:
				# Older NVDA config layout.
				outputDevice = config.conf["speech"]["outputDevice"]
			self.wave_player = nvwave.WavePlayer(
				channels=CHANNELS,
				samplesPerSec=SAMPLE_RATE,
				bitsPerSample=SAMPLE_WIDTH * 8,
				outputDevice=outputDevice,
				wantDucking=False,
			)
			self.is_running = True
		except Exception as e:
			log.error(f"SoundProcessor: nvwave.WavePlayer init failed: {e}")
			self.wave_player = None

	def stop(self):
		self.is_running = False
		self._progress_wake.set()

		try:
			self._progress_queue.put_nowait(None)
		except queue.Full:
			pass

		workerThread = self._progress_worker_thread
		if workerThread and workerThread.is_alive():
			workerThread.join(timeout=2.0)

		feederThread = self._progress_feeder_thread
		if feederThread and feederThread.is_alive():
			feederThread.join(timeout=2.0)

		with self._lock:
			if self.wave_player:
				try:
					self.wave_player.stop()
				except Exception as e:
					log.debug(f"SoundProcessor: WavePlayer stop error: {e}")
				# Matches nvwave/tones.py's own cleanup pattern: drop the
				# reference so WavePlayer.__del__ releases the underlying
				# WASAPI session.
				self.wave_player = None

	# NOTE: an object-value text-parsing percent lookup (keyed by id(obj))
	# used to live here and be tried before falling back to decoding the
	# hz NVDA itself passed to tones.beep. It was removed: id() can be
	# reused by an unrelated, later object once the original is garbage
	# collected, which could silently return a stale/wrong percent for a
	# completely different progress operation -- producing exactly the
	# non-monotonic, "jumps backward" pan behaviour reported. Decoding
	# straight from NVDA's own hz (see GlobalPlugin.safeBeep) is simpler
	# and doesn't have this failure mode.

	def play_progress_sound(self, percent, direction):
		"""
		Records the latest target frequency/pan and wakes the tick
		worker; cheap and non-blocking; safe to call directly from
		safeBeep regardless of which thread that runs on. See
		_progress_worker_loop for the actual rendering.
		"""
		if not self.wave_player:
			return

		frequency = self.min_frequency + (self.max_frequency - self.min_frequency) * (percent / 100.0)

		if direction == LEFT_TO_RIGHT:
			pan = percent / 100.0
		elif direction == RIGHT_TO_LEFT:
			pan = 1.0 - (percent / 100.0)
		elif direction == LEFT:
			pan = 0.0
		elif direction == RIGHT:
			pan = 1.0
		else:
			pan = 0.5

		now = time.monotonic()
		with self._progress_lock:
			gap = (now - self._progress_last_event_time) if self._progress_last_event_time else None
			isNewTask = (
				self._progress_last_frequency is None
				or gap is None
				or gap > _TASK_RESET_GAP
			)
			if isNewTask:
				# Genuinely new/unrelated operation (or the very first
				# ever): start the next tick from this direction's true
				# beginning. NVDA's very first progress update for an
				# operation is often already well underway -- e.g. a fast
				# copy can report its first update at 37% or even 85%
				# complete -- so starting from wherever that first value
				# happens to be would sound like playback "began in the
				# middle" instead of sweeping from the start.
				self._progress_last_frequency = self.min_frequency
				if direction == LEFT_TO_RIGHT or direction == LEFT:
					self._progress_last_pan = 0.0
				elif direction == RIGHT_TO_LEFT or direction == RIGHT:
					self._progress_last_pan = 1.0
				else:
					self._progress_last_pan = 0.5

			self._progress_pending = (frequency, pan)
			self._progress_last_event_time = now

		self._ensure_progress_worker()
		self._progress_wake.set()

	def _ensure_progress_worker(self):
		with self._lock:
			if not self._progress_worker_thread or not self._progress_worker_thread.is_alive():
				self._progress_worker_thread = threading.Thread(
					target=self._progress_worker_loop, daemon=True
				)
				self._progress_worker_thread.start()

			if not self._progress_feeder_thread or not self._progress_feeder_thread.is_alive():
				self._progress_feeder_thread = threading.Thread(
					target=self._progress_feeder_loop, daemon=True
				)
				self._progress_feeder_thread.start()

	def _progress_feeder_loop(self):
		"""
		Drains queued, pre-rendered ticks into wave_player.feed(). Kept
		completely separate from rendering so that a momentarily slow
		render (GIL contention, a heavier waveform, etc.) doesn't directly
		stall the feed timing.
		"""
		while self.is_running:
			try:
				data = self._progress_queue.get(timeout=0.5)
			except queue.Empty:
				continue
			if data is None:
				continue
			player = self.wave_player
			if not player:
				break
			try:
				player.feed(data)
			except Exception as e:
				log.error(f"SoundProcessor: feed error: {e}")
				break

	def _progress_worker_loop(self):
		"""
		Sleeps until play_progress_sound() signals a new target, then
		renders and queues exactly one tick for it and goes back to
		sleep. There is no per-block looping or timeout/"is this
		finished" logic here at all -- each tick is a short, complete,
		self-fading sound on its own, so there's nothing left open that
		would need to be wrapped up later.
		"""
		while self.is_running:
			triggered = self._progress_wake.wait(timeout=0.5)
			if not self.is_running:
				break
			if not triggered:
				continue
			self._progress_wake.clear()

			with self._progress_lock:
				pending = self._progress_pending
				self._progress_pending = None
				startFrequency = self._progress_last_frequency
				startPan = self._progress_last_pan

			if pending is None:
				continue

			targetFrequency, targetPan = pending
			tick = self._render_progress_tick(startFrequency, targetFrequency, startPan, targetPan)
			if not self._enqueue(tick):
				break

			with self._progress_lock:
				self._progress_last_frequency = targetFrequency
				self._progress_last_pan = targetPan

	def _render_progress_tick(self, startFrequency, endFrequency, startPan, endPan):
		"""
		One short, self-contained burst: glides frequency/pan from the
		previous tick's ending point to this one's target during the
		attack (so a run of frequent updates still feels like a connected
		sweep), holds briefly at the target, then fades back to silence
		during the decay -- so nothing is ever left "hanging open"
		waiting for a next update that might not arrive for a while.

		The attack/decay amplitude ramps are shaped by the user's chosen
		fade_algorithm (_fade_curve) rather than a plain straight line:
		_render_block itself only interpolates linearly within a single
		call, so each ramp is built from several small linear sub-steps
		whose endpoints follow the curve, closely approximating it.
		"""
		numSamples = max(1, int(SAMPLE_RATE * _TICK_SECONDS))
		attackSamples = max(1, int(numSamples * _TICK_ATTACK_FRACTION))
		decaySamples = max(1, int(numSamples * _TICK_DECAY_FRACTION))
		sustainSamples = max(1, numSamples - attackSamples - decaySamples)

		curveSteps = 6
		result = array.array('h')

		stepSamples = max(1, attackSamples // curveSteps)
		for i in range(curveSteps):
			t0 = i / curveSteps
			t1 = (i + 1) / curveSteps
			freq0 = startFrequency + (endFrequency - startFrequency) * t0
			freq1 = startFrequency + (endFrequency - startFrequency) * t1
			pan0 = startPan + (endPan - startPan) * t0
			pan1 = startPan + (endPan - startPan) * t1
			result += self._render_block(
				stepSamples, freq0, freq1, pan0, pan1, self._fade_curve(t0), self._fade_curve(t1)
			)

		result += self._render_block(
			sustainSamples, endFrequency, endFrequency, endPan, endPan, 1.0, 1.0
		)

		stepSamples = max(1, decaySamples // curveSteps)
		for i in range(curveSteps):
			t0 = i / curveSteps
			t1 = (i + 1) / curveSteps
			result += self._render_block(
				stepSamples, endFrequency, endFrequency, endPan, endPan,
				1.0 - self._fade_curve(t0), 1.0 - self._fade_curve(t1)
			)

		return result

	def _enqueue(self, block):
		if not self.is_running:
			return False
		try:
			# Blocks (briefly) if the lookahead cushion is already full,
			# which is fine -- it just means generation is running ahead
			# of playback exactly as intended, and naturally paces itself
			# back down to real-time.
			self._progress_queue.put(block.tobytes(), timeout=1.0)
			return True
		except queue.Full:
			log.debug("SoundProcessor: progress queue full, dropping a block")
			return True
		except Exception as e:
			log.error(f"SoundProcessor: enqueue error: {e}")
			return False

	def _render_block(self, numSamples, startFrequency, endFrequency, startPan, endPan, startAmplitude, endAmplitude):
		"""
		Renders one block with frequency, pan, and amplitude all
		interpolated per-sample from their "start" to "end" value (i.e.
		this block's beginning to its end) -- and the oscillator phase
		integrated sample-by-sample from the instantaneous frequency,
		rather than computed from frequency*absolute_time. Both of these
		matter for a genuinely click-free result: interpolating only
		per-block (constant value for the whole block, then snapping) is
		what produced the "still crackly" sound in an earlier revision;
		per-sample interpolation removes that step entirely, at any block
		size.
		"""
		freqStep = (endFrequency - startFrequency) / numSamples
		panStep = (endPan - startPan) / numSamples
		ampStep = (endAmplitude - startAmplitude) / numSamples

		frequency = startFrequency
		pan = startPan
		amplitude = startAmplitude
		phase = self._tone_phase
		twoPi = 2 * math.pi
		harmonics = self.harmonics
		volume = fraction_to_gain(self.volume) * PAN_BOOST_FACTOR * fraction_to_gain(self.master_volume)

		stereo = array.array('h')
		for _i in range(numSamples):
			phase += twoPi * frequency / SAMPLE_RATE
			value = 0.0
			for idx, amp in enumerate(harmonics):
				value += amp * math.sin(phase * (idx + 1))
			value = max(min(value, 1.0), -1.0)

			gain = volume * amplitude
			leftVol = (1.0 - pan) * gain
			rightVol = pan * gain
			stereo.append(int(value * 32767 * leftVol))
			stereo.append(int(value * 32767 * rightVol))

			frequency += freqStep
			pan += panStep
			amplitude += ampStep

		# Keep phase bounded so it stays numerically precise over long
		# sessions instead of growing without limit.
		self._tone_phase = phase % twoPi

		if self.reverb_enabled:
			stereo = self._apply_delay_reverb(stereo)

		return stereo

	def _apply_delay_reverb(self, stereo_samples):
		"""
		A small feedback delay line mixed back into the signal, giving the
		progress tone a soft reverb "tail". The delay buffer persists
		across blocks so the tail continues to build naturally over the
		whole continuous stream.
		"""
		delayLen = len(self._delay_buffer_left)
		if delayLen == 0:
			return stereo_samples

		out = array.array('h', stereo_samples)
		frameCount = len(out) // 2

		for n in range(frameCount):
			li = n * 2
			ri = li + 1
			idx = self._delay_pos % delayLen

			delayedLeft = self._delay_buffer_left[idx]
			delayedRight = self._delay_buffer_right[idx]

			newLeft = out[li] + delayedLeft * _DELAY_MIX
			newRight = out[ri] + delayedRight * _DELAY_MIX

			self._delay_buffer_left[idx] = int(max(-32768, min(32767, out[li] + delayedLeft * _DELAY_FEEDBACK)))
			self._delay_buffer_right[idx] = int(max(-32768, min(32767, out[ri] + delayedRight * _DELAY_FEEDBACK)))

			out[li] = int(max(-32768, min(32767, newLeft)))
			out[ri] = int(max(-32768, min(32767, newRight)))

			self._delay_pos += 1

		return out

	def _fade_curve(self, t):
		"""
		Maps a 0-1 progress fraction to a 0-1 amplitude using the selected
		fade_algorithm shape. Used to shape each progress tick's
		attack/decay envelope (see _render_progress_tick).
		"""
		t = max(0.0, min(1.0, t))
		algo = self.fade_algorithm

		if algo == "cosine":
			return 0.5 * (1 - math.cos(math.pi * t))
		elif algo == "gaussian":
			return math.exp(-((t - 1) ** 2) / (2 * (1 / 3) ** 2))
		elif algo == "linear":
			return t
		elif algo == "exponential":
			return 1 - math.exp(-5 * t)
		elif algo == "logarithmic":
			return math.log(1 + 9 * t) / math.log(10)
		elif algo == "s_curve":
			return 1 / (1 + math.exp(-12 * (t - 0.5)))
		elif algo in ("sine", "quarter_sine"):
			return math.sin(math.pi * t / 2)
		elif algo == "half_sine":
			return (1 - math.cos(math.pi * t)) / 2
		elif algo == "square_root":
			return math.sqrt(t)
		elif algo == "cubic_root":
			return t ** (1 / 3)
		elif algo == "quadratic":
			return t ** 2
		return t
