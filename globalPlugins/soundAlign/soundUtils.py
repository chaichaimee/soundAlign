# soundUtils.py

import threading
import queue
import math
import time
import os
import sys
import array
from logHandler import log
try:
    import pythoncom
    import win32api
    import winsound
    import ctypes
    import comtypes
    import comtypes.client
except ImportError as e:
    log.warning(f"SoundUtils: Failed to import COM libraries ({e}). Progress tracking on some applications may not work.")
    pythoncom = None
    win32api = None
    winsound = None
    ctypes = None
    comtypes = None

# Import the missing module
import controlTypes

# Constants for sound types and directions
LEFT = 0
CENTER = 1
RIGHT = 2
LEFT_TO_RIGHT = 3
RIGHT_TO_LEFT = 4

ERROR_WARNING = "error_warning"
SOUND_EFFECTS = "sound_effects"
PROGRESS_INDICATOR = "progress_indicator"
ADDON_BEEP = "addon_beep"

# Constants for waveform types
TONE_SINE = [1.0]
TONE_TRIANGLE = [1.0, 0.0, 1/9, 0.0, 1/25, 0.0, 1/49]
TONE_SAWTOOTH = [1.0, 1/2, 1/3, 1/4, 1/5, 1/6, 1/7]

# Audio settings
SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2
FORMAT = 8  # paInt16
CHUNK_SIZE = 1024

# Global boost factor for pan mode
PAN_BOOST_FACTOR = 1.0

class SoundProcessor:
    """A class to handle all sound generation and playback."""

    def __init__(self, global_plugin, pyaudio_module):
        self.global_plugin = global_plugin
        self.pyaudio = pyaudio_module
        self.audio_queue = queue.Queue()
        self.player_thread = None
        self.pa_stream = None
        self.is_running = False
        self.last_progress_value = -1
        self.volume = 0.5
        self.fade_algorithm = "cosine"
        self.harmonics = TONE_SINE
        self.min_frequency = 110
        self.max_frequency = 1760
        # Increased audio duration and fade ratio for smoother sound
        self.audio_duration = 0.1 # was 0.08
        self.fade_ratio = 0.5 # was 0.45
        self.last_update_time = time.time()
        self.last_focus_obj = None
        self._lock = threading.Lock()

        if self.pyaudio:
            try:
                self.p = self.pyaudio.PyAudio()
                if hasattr(self.pyaudio, 'get_version'):
                    log.info(f"SoundProcessor: PyAudio version {self.pyaudio.get_version()}")
                else:
                    log.warning("SoundProcessor: PyAudio module has no 'get_version' attribute. Skipping version check.")
                self.start_player_thread()
            except Exception as e:
                log.error(f"SoundProcessor: Failed to initialize PyAudio: {e}")
                self.pyaudio = None
        else:
            log.error("SoundProcessor: PyAudio not available, sound generation disabled.")

    def start_player_thread(self):
        """Start the audio player thread if not already running."""
        with self._lock:
            if not self.pyaudio or (self.player_thread and self.player_thread.is_alive()):
                return
            self.is_running = True
            self.player_thread = threading.Thread(target=self._audio_player_loop)
            self.player_thread.daemon = True
            self.player_thread.start()
            log.info("SoundProcessor: Audio player thread started.")

    def stop(self):
        """Stop the audio processor and clean up resources."""
        with self._lock:
            self.is_running = False
            # Wait for the thread to process the final None signal and exit cleanly
            if self.audio_queue.empty():
                self.audio_queue.put(None)
        if self.player_thread and self.player_thread.is_alive():
            self.player_thread.join(timeout=1.0)
            log.info("SoundProcessor: Player thread joined.")
        
        with self._lock:
            if self.pa_stream and self.pa_stream.is_active():
                self.pa_stream.stop_stream()
            if self.pa_stream:
                self.pa_stream.close()
            if self.pyaudio and hasattr(self, 'p'):
                self.p.terminate()
            log.info("SoundProcessor: Audio stream and PyAudio terminated.")

    def _audio_player_loop(self):
        """Main loop for playing audio from the queue."""
        with self._lock:
            if not self.pyaudio: return
            try:
                self.pa_stream = self.p.open(
                    format=self.pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=SAMPLE_RATE,
                    output=True,
                    frames_per_buffer=CHUNK_SIZE
                )
                self.pa_stream.start_stream()
                log.info("SoundProcessor: Audio stream opened and started.")
            except Exception as e:
                log.error(f"SoundProcessor: Failed to open audio stream: {e}")
                self.is_running = False
                return

        while self.is_running:
            try:
                # Get data with a timeout to allow the thread to check is_running flag
                data = self.audio_queue.get(timeout=0.2)
                if data is None:
                    log.info("SoundProcessor: Received None data, exiting loop.")
                    break
                
                with self._lock:
                    if self.pa_stream and self.pa_stream.is_active():
                        self.pa_stream.write(data)
                    else:
                        log.warning("SoundProcessor: Stream is not active, dropping audio data.")
            except queue.Empty:
                continue
            except Exception as e:
                log.error(f"SoundProcessor: Error in audio loop: {e}")
                break

    def get_progress_percent(self, obj):
        """Get progress percentage from various NVDA objects."""
        if obj is None:
            return None
            
        role = obj.role
        try:
            if role == controlTypes.ROLE_PROGRESSBAR:
                # Use UIA if available, otherwise fallback
                if hasattr(obj, 'UIAControl') and obj.UIAControl:
                    progress_value = obj.UIAControl.GetCurrentPropertyValue(
                        comtypes.client.GetModule("UIAutomationCore.dll").UIA_RangeValueValuePropertyId)
                    return progress_value
                else:
                    return obj.value
            elif role == controlTypes.ROLE_SLIDER or role == controlTypes.ROLE_SPINBUTTON:
                return obj.value
            elif hasattr(obj, "states") and controlTypes.STATE_BUSY in obj.states:
                # For busy states, we might not have a precise value, but we can return a symbolic one
                return -1 # Use a special value to indicate busy, which can be handled by the caller
        except Exception as e:
            # Handle potential exceptions gracefully and return None
            log.warning(f"SoundProcessor: Failed to get progress for object {obj.name}: {e}")
        
        return None

    def play_progress_sound(self, percent, direction, use_default_hz=False, force_hz=None):
        """Generate and play a progress tone based on percentage and direction."""
        if not self.pyaudio or not self.is_running:
            log.warning("SoundProcessor: Cannot play progress sound, module or thread not running.")
            return

        # Ensure percent is a valid number before calculation
        if percent is None or not isinstance(percent, (int, float)):
            log.error(f"SoundProcessor: Invalid percent value: {percent}. Skipping audio generation.")
            return

        # Handle the case where the progress bar is busy but has no percentage
        if percent == -1:
            frequency = self.min_frequency + (self.max_frequency - self.min_frequency) * 0.5
            pan = 0.5
        else:
            frequency = self.min_frequency + (self.max_frequency - self.min_frequency) * (percent / 100.0)
            if direction == LEFT_TO_RIGHT:
                pan = percent / 100.0
            elif direction == RIGHT_TO_LEFT:
                pan = 1.0 - (percent / 100.0)
            else: # CENTER
                pan = 0.5
        
        # Power law panning for smoother sound localization
        left_pan_factor = math.sqrt(1.0 - pan)
        right_pan_factor = math.sqrt(pan)
        
        frame_count = int(SAMPLE_RATE * self.audio_duration)
        samples = bytearray(frame_count * CHANNELS * SAMPLE_WIDTH)
        
        try:
            with self._lock:
                # Critical section: generate audio data
                samples_array = array.array('h')
                for i in range(frame_count):
                    t = float(i) / SAMPLE_RATE
                    
                    # Apply fade
                    fade_factor = self.get_fade_factor(i, frame_count)
                    
                    sample_value = 0.0
                    for k, amp in enumerate(self.harmonics):
                        if amp == 0: continue
                        f_k = frequency * (k + 1)
                        sample_value += amp * math.sin(2.0 * math.pi * f_k * t)
                    
                    sample_value *= fade_factor * self.volume
                    
                    # Apply pan and convert to 16-bit integer
                    left_sample = int(sample_value * left_pan_factor * 32767)
                    right_sample = int(sample_value * right_pan_factor * 32767)
                    
                    # Ensure samples are within 16-bit range
                    left_sample = max(-32768, min(32767, left_sample))
                    right_sample = max(-32768, min(32767, right_sample))
                    
                    samples_array.append(left_sample)
                    samples_array.append(right_sample)

                self.audio_queue.put(samples_array.tobytes())
        except Exception as e:
            log.error(f"SoundProcessor: Error generating audio data: {e}")
            
    def get_fade_factor(self, i, frame_count):
        """Calculate fade-in/fade-out factor."""
        fade_frames = int(frame_count * self.fade_ratio)
        if self.fade_algorithm == "cosine":
            if i < fade_frames:
                # Fade-in
                return 0.5 * (1 - math.cos(math.pi * i / fade_frames))
            elif i > frame_count - fade_frames:
                # Fade-out
                return 0.5 * (1 - math.cos(math.pi * (frame_count - i) / fade_frames))
            else:
                return 1.0
        elif self.fade_algorithm == "gaussian":
            # Simple linear fade for now as a placeholder
            if i < fade_frames:
                return i / fade_frames
            elif i > frame_count - fade_frames:
                return (frame_count - i) / fade_frames
            else:
                return 1.0
        else:
            return 1.0
            
    def flush_queue(self):
        """Clear the audio queue."""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

