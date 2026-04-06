# soundUtils.py

import threading
import queue
import math
import time
import os
import sys
import array
import re
from logHandler import log

LEFT = 0
CENTER = 1
RIGHT = 2
LEFT_TO_RIGHT = 3
RIGHT_TO_LEFT = 4

ERROR_WARNING = "error_warning"
SOUND_EFFECTS = "sound_effects"
PROGRESS_INDICATOR = "progress_indicator"
ADDON_BEEP = "addon_beep"

TONE_SINE = [1.0]
TONE_TRIANGLE = [1.0, 0.0, 1/9, 0.0, 1/25, 0.0, 1/49]
TONE_SAWTOOTH = [1.0, 1/2, 1/3, 1/4, 1/5, 1/6, 1/7]
TONE_SQUARE = [1.0, 0, 1/3, 0, 1/5, 0, 1/7, 0, 1/9, 0, 1/11]

SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2
FORMAT = 8
CHUNK_SIZE = 1024

PAN_BOOST_FACTOR = 1.0

class SoundProcessor:

	def __init__(self, global_plugin, pyaudio_module):
		self.global_plugin = global_plugin
		self.pyaudio = pyaudio_module
		self.audio_queue = queue.Queue()
		self.player_thread = None
		self.pa_stream = None
		self.is_running = False
		self.last_progress_value = -1
		self.volume = 0.5
		self.master_volume = 1.0
		self.fade_algorithm = "cosine"
		self.harmonics = TONE_SINE
		self.min_frequency = 110
		self.max_frequency = 1760
		self.audio_duration = 0.1
		self.fade_ratio = 0.5
		self.last_update_time = time.time()
		self.last_focus_obj = None
		self._lock = threading.Lock()

		if self.pyaudio:
			try:
				self.p = self.pyaudio.PyAudio()
				self.start_player_thread()
			except Exception as e:
				log.error(f"SoundProcessor: Failed to initialize PyAudio: {e}")
				self.pyaudio = None
		else:
			log.error("SoundProcessor: PyAudio not available, sound generation disabled.")

	def start_player_thread(self):
		with self._lock:
			if not self.pyaudio or (self.player_thread and self.player_thread.is_alive()):
				return
			self.is_running = True
			self.player_thread = threading.Thread(target=self._audio_player_loop)
			self.player_thread.daemon = True
			self.player_thread.start()

	def stop(self):
		with self._lock:
			self.is_running = False
			if self.audio_queue.empty():
				self.audio_queue.put(None)
		if self.player_thread and self.player_thread.is_alive():
			self.player_thread.join(timeout=1.0)

		with self._lock:
			if self.pa_stream and self.pa_stream.is_active():
				self.pa_stream.stop_stream()
			if self.pa_stream:
				self.pa_stream.close()
			if self.pyaudio and hasattr(self, 'p'):
				self.p.terminate()

	def _audio_player_loop(self):
		with self._lock:
			if not self.pyaudio:
				return
			try:
				self.pa_stream = self.p.open(
					format=self.pyaudio.paInt16,
					channels=CHANNELS,
					rate=SAMPLE_RATE,
					output=True,
					frames_per_buffer=CHUNK_SIZE
				)
				self.pa_stream.start_stream()
			except Exception as e:
				log.error(f"SoundProcessor: Failed to open audio stream: {e}")
				self.is_running = False
				return

		while self.is_running:
			try:
				data = self.audio_queue.get(timeout=0.2)
				if data is None:
					break

				with self._lock:
					if self.pa_stream and self.pa_stream.is_active():
						self.pa_stream.write(data)
					self.audio_queue.task_done()
			except queue.Empty:
				continue
			except Exception as e:
				log.error(f"SoundProcessor: Error in audio player loop: {e}")
				break

	def flush_queue(self):
		with self._lock:
			while not self.audio_queue.empty():
				try:
					self.audio_queue.get_nowait()
					self.audio_queue.task_done()
				except queue.Empty:
					break

	def get_progress_percent(self, obj):
		try:
			if obj and hasattr(obj, 'value') and obj.value:
				match = re.search(r'(\d+)%', obj.value)
				if match:
					return int(match.group(1))
		except Exception as e:
			log.error(f"SoundProcessor: Error getting progress percent: {e}")
		return None

	def play_progress_sound(self, percent, direction):
		if not self.pyaudio or not self.is_running:
			return

		frequency = self.min_frequency + (self.max_frequency - self.min_frequency) * (percent / 100.0)
		samples = self._generate_tone(frequency, self.audio_duration)

		if direction == LEFT_TO_RIGHT:
			pan = percent / 100.0
		elif direction == RIGHT_TO_LEFT:
			pan = 1.0 - (percent / 100.0)
		else:
			pan = 0.5

		left_volume = self.volume * (1.0 - pan) * PAN_BOOST_FACTOR * self.master_volume
		right_volume = self.volume * pan * PAN_BOOST_FACTOR * self.master_volume

		stereo_samples = array.array('h')
		for i in range(len(samples)):
			stereo_samples.append(int(samples[i] * left_volume))
			stereo_samples.append(int(samples[i] * right_volume))

		fade_samples = self._apply_fade(stereo_samples, self.fade_ratio)

		try:
			self.audio_queue.put(fade_samples.tobytes())
		except Exception as e:
			log.error(f"SoundProcessor: Error queuing progress sound: {e}")

	def _generate_tone(self, frequency, duration):
		samples = array.array('h')
		num_samples = int(SAMPLE_RATE * duration)

		for i in range(num_samples):
			t = i / SAMPLE_RATE
			value = 0.0
			for j, amplitude in enumerate(self.harmonics):
				value += amplitude * math.sin(2 * math.pi * frequency * (j + 1) * t)
			value = max(min(value, 1.0), -1.0) * 32767
			samples.append(int(value))

		return samples

	def _apply_fade(self, samples, fade_ratio):
		fade_samples = array.array('h', samples)
		fade_length = int(len(samples) * fade_ratio / 2)

		for i in range(fade_length):
			t = i / fade_length if fade_length > 0 else 0

			if self.fade_algorithm == "cosine":
				fade = 0.5 * (1 - math.cos(math.pi * i / fade_length))
			elif self.fade_algorithm == "gaussian":
				fade = math.exp(-((i - fade_length) ** 2) / (2 * (fade_length / 3) ** 2))
			elif self.fade_algorithm == "linear":
				fade = t
			elif self.fade_algorithm == "exponential":
				fade = 1 - math.exp(-5 * t)
			elif self.fade_algorithm == "logarithmic":
				fade = math.log(1 + 9 * t) / math.log(10)
			elif self.fade_algorithm == "s_curve":
				fade = 1 / (1 + math.exp(-12 * (t - 0.5)))
			elif self.fade_algorithm == "sine":
				fade = math.sin(math.pi * t / 2)
			elif self.fade_algorithm == "quarter_sine":
				fade = math.sin(math.pi * t / 2)
			elif self.fade_algorithm == "half_sine":
				fade = (1 - math.cos(math.pi * t)) / 2
			elif self.fade_algorithm == "square_root":
				fade = math.sqrt(t)
			elif self.fade_algorithm == "cubic_root":
				fade = t ** (1/3)
			elif self.fade_algorithm == "quadratic":
				fade = t ** 2
			else:
				fade = t

			fade_samples[i] = int(fade_samples[i] * fade)
			fade_samples[-(i + 1)] = int(fade_samples[-(i + 1)] * fade)

		return fade_samples