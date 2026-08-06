# soundUtils.py

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

# --- Continuous progress-tone engine tuning -------------------------------
# Earlier revisions generated progress audio as discrete ~100ms chunks, one
# per NVDA progress-beep event, each independently panned and queued. That
# produced an audible click at every chunk boundary, and for short
# operations (only a couple of progress events) the pan simply stopped
# wherever the last chunk happened to land instead of completing a sweep.
#
# The standard real-time-synthesis fix for both problems (per common
# practice for glide/portamento-based tone generators) is to run ONE
# continuous generator instead of discrete chunks, smoothly interpolating
# ("gliding") frequency and pan toward their latest target values instead
# of snapping to them.
#
# IMPORTANT (this is the part an earlier revision got wrong): NVDA's own
# nvwave.WavePlayer had its automatic internal buffering removed in recent
# versions (the 'buffered' constructor parameter was deprecated and then
# removed) -- so the caller is now fully responsible for keeping playback
# fed with a comfortable lookahead cushion. Generating one small block and
# immediately blocking on feed() for it, over and over with no cushion,
# means ANY tiny timing hiccup (GIL contention, a slightly slow block) is
# instantly an audible gap. The fix is the standard producer/consumer
# pattern: a GENERATOR thread renders blocks continuously and pushes them
# into a bounded queue running comfortably ahead of playback, while a
# separate FEEDER thread drains that queue into WavePlayer.feed(). Jitter
# in generation timing is absorbed by the queued cushion instead of
# reaching the speaker as a click.
_BLOCK_SECONDS = 0.1
# How many blocks to keep queued ahead of playback (0.1s * 5 = 500ms of
# lookahead cushion).
_QUEUE_LOOKAHEAD_BLOCKS = 5
# Portamento time constant: how quickly frequency/pan glide toward their
# latest target, spread over multiple blocks. Frequency/pan/amplitude are
# then interpolated per-SAMPLE within each block (see _render_block) so
# there is never a step at a block boundary, only a continuously moving
# value throughout.
#
# Shortened from 0.3s: with sparser progress updates (common for real
# copy operations, which often only report every few hundred ms rather
# than continuously), a longer glide meant the audible pan noticeably
# lagged behind each new target -- felt like a jump rather than a sweep,
# especially for short (few-second) operations with only a handful of
# updates total.
_GLIDE_SECONDS = 0.15
_FADE_IN_SECONDS = 0.03
_FADE_OUT_SECONDS = 0.25
# If no new progress update arrives for this long, treat the operation as
# finished: glide the rest of the way to this direction's natural end
# point and fade out, rather than cutting off abruptly.
#
# IMPORTANT: this must be comfortably longer than the normal gap between
# real progress updates, or a still-running operation gets misdetected as
# "finished" mid-sweep -- triggering a fade-out-and-stop, then a fresh
# fade-in-from-silence restart the moment the next real update arrives a
# moment later. That start/stop/restart cycle is what produced the
# jumpy, discontinuous, cut-off-sounding pan reported for short
# operations: 0.35s turned out to be shorter than several real-world
# progress dialogs' normal update interval.
_INACTIVITY_TIMEOUT = 0.8

# Short delay-line length used for the optional progress-tone reverb
# effect. Longer than a single block by a wide margin so the buffer never
# wraps around within a fast-changing block.
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

		# Continuous progress-tone generator state. A GENERATOR thread
		# (_progress_generator_loop) renders blocks and pushes them into
		# this bounded queue; a separate FEEDER thread (_progress_feeder_loop)
		# drains it into wave_player.feed(). Keeping the queue running a
		# few blocks ahead of playback is what absorbs generation-timing
		# jitter instead of it reaching the speaker as a click.
		self._progress_lock = threading.Lock()
		self._progress_active = False
		self._progress_target_frequency = None
		self._progress_target_pan = 0.5
		self._progress_direction = None
		self._progress_last_update = 0.0
		self._progress_generator_thread = None
		self._progress_feeder_thread = None
		self._progress_queue = queue.Queue(maxsize=_QUEUE_LOOKAHEAD_BLOCKS * 2)

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
		with self._progress_lock:
			self._progress_active = False

		# Wake up the feeder thread if it's blocked waiting on an empty
		# queue, and the generator thread if it checks is_running promptly.
		try:
			self._progress_queue.put_nowait(None)
		except queue.Full:
			pass

		generatorThread = self._progress_generator_thread
		if generatorThread and generatorThread.is_alive():
			generatorThread.join(timeout=2.0)

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
		Updates the continuous progress-tone generator's target frequency
		and pan; cheap and non-blocking (just updates a few numbers under
		a lock), so it's safe to call directly from safeBeep regardless of
		which thread that runs on. The actual audio generation happens
		continuously on a dedicated background thread -- see
		_progress_generator_loop / _progress_feeder_loop.
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

		with self._progress_lock:
			self._progress_target_frequency = frequency
			self._progress_target_pan = pan
			self._progress_direction = direction
			self._progress_last_update = time.monotonic()
			wasActive = self._progress_active
			self._progress_active = True

		if not wasActive:
			self._start_progress_thread()

	def _start_progress_thread(self):
		with self._lock:
			if self._progress_generator_thread and self._progress_generator_thread.is_alive():
				return
			self._progress_generator_thread = threading.Thread(
				target=self._progress_generator_loop, daemon=True
			)
			self._progress_generator_thread.start()

			if not self._progress_feeder_thread or not self._progress_feeder_thread.is_alive():
				self._progress_feeder_thread = threading.Thread(
					target=self._progress_feeder_loop, daemon=True
				)
				self._progress_feeder_thread.start()

	def _progress_feeder_loop(self):
		"""
		Drains queued, pre-rendered blocks into wave_player.feed(). Kept
		completely separate from generation so that a momentarily slow
		render (GIL contention, a heavier waveform, etc.) doesn't directly
		stall the feed timing -- it just eats into the queued lookahead
		cushion instead of becoming an audible gap.
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

	def _progress_generator_loop(self):
		blockSamples = max(1, int(SAMPLE_RATE * _BLOCK_SECONDS))
		glideFactor = min(1.0, _BLOCK_SECONDS / _GLIDE_SECONDS)
		currentFrequency = None
		currentPan = 0.5
		currentAmplitude = 0.0
		fadeElapsed = 0.0
		fadingIn = True

		while self.is_running:
			with self._progress_lock:
				targetFrequency = self._progress_target_frequency
				targetPan = self._progress_target_pan
				direction = self._progress_direction
				lastUpdate = self._progress_last_update

			if targetFrequency is None:
				break

			if currentFrequency is None:
				# A fresh session (this thread just started for a new
				# operation). NVDA's very first progress update for an
				# operation is often already well underway -- e.g. a fast
				# copy can report its first update at 37% or even 85%
				# complete, because the underlying work outran how often
				# NVDA polls/reports it. Starting the audible sweep
				# directly at that first reported position made it sound
				# like playback "began in the middle" instead of sweeping
				# from the start. Always begin at this direction's true
				# starting point instead (low pitch, and the sweep's
				# starting side) and let the normal glide carry it from
				# there to the first real target -- so even a short,
				# already-partway-along operation still sounds like a
				# sweep from the beginning, matching test mode.
				currentFrequency = self.min_frequency
				if direction == LEFT_TO_RIGHT or direction == LEFT:
					currentPan = 0.0
				elif direction == RIGHT_TO_LEFT or direction == RIGHT:
					currentPan = 1.0
				else:
					currentPan = 0.5

			idleFor = time.monotonic() - lastUpdate
			if idleFor > _INACTIVITY_TIMEOUT:
				# No new progress update in a while -- treat the operation
				# as finished. Glide the rest of the way to this
				# direction's natural end point and fade out, instead of
				# just stopping wherever the last update happened to land.
				# This is what makes even a very short (few-second)
				# operation still sound like a complete sweep rather than
				# an abrupt cutoff.
				endPan = targetPan
				if direction == LEFT_TO_RIGHT:
					endPan = 1.0
				elif direction == RIGHT_TO_LEFT:
					endPan = 0.0
				self._render_wrapup(currentFrequency, currentPan, endPan, currentAmplitude, glideFactor, blockSamples)
				with self._progress_lock:
					self._progress_active = False
				break

			nextFrequency = currentFrequency + (targetFrequency - currentFrequency) * glideFactor
			nextPan = currentPan + (targetPan - currentPan) * glideFactor

			nextAmplitude = currentAmplitude
			if fadingIn:
				fadeElapsed += _BLOCK_SECONDS
				progress = min(1.0, fadeElapsed / _FADE_IN_SECONDS)
				nextAmplitude = self._fade_curve(progress)
				if progress >= 1.0:
					fadingIn = False
					nextAmplitude = 1.0

			# Frequency/pan/amplitude are interpolated per-SAMPLE from their
			# current value to next value inside _render_block -- so even
			# though targets only update once per block, the actual audio
			# never steps; it's a continuously moving value throughout.
			block = self._render_block(
				blockSamples, currentFrequency, nextFrequency, currentPan, nextPan, currentAmplitude, nextAmplitude
			)
			if not self._enqueue(block):
				break

			currentFrequency = nextFrequency
			currentPan = nextPan
			currentAmplitude = nextAmplitude

	def _render_wrapup(self, startFrequency, startPan, endPan, startAmplitude, glideFactor, blockSamples):
		steps = max(1, int(_FADE_OUT_SECONDS / _BLOCK_SECONDS))
		currentPan = startPan
		currentAmplitude = startAmplitude
		for i in range(steps):
			nextPan = currentPan + (endPan - currentPan) * glideFactor
			fadeProgress = (i + 1) / steps
			nextAmplitude = startAmplitude * self._fade_curve(1.0 - fadeProgress)
			block = self._render_block(
				blockSamples, startFrequency, startFrequency, currentPan, nextPan, currentAmplitude, nextAmplitude
			)
			if not self._enqueue(block):
				return
			currentPan = nextPan
			currentAmplitude = nextAmplitude

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
		volume = self.volume * PAN_BOOST_FACTOR * self.master_volume

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
		fade_algorithm shape. Used for the fade-in when the progress tone
		starts from silence and the fade-out during the end-of-operation
		wrap-up (see _render_wrapup).
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
