# __init__.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import sys
import os
import shutil
import threading
import types
import math
import array
import time
import json
import re
import queueHandler
import eventHandler
from queue import Queue
import globalPluginHandler
import gui
import wx
import config
import tones
import ui
import synthDriverHandler
import speech
import globalVars
import addonHandler
from logHandler import log
import scriptHandler
from scriptHandler import script
import nvwave
import api
import controlTypes
import UIAHandler
from NVDAObjects import NVDAObject
import winsound
import comtypes
import comtypes.client

from . import overlay_loader

plugin_dir = os.path.dirname(__file__)
if plugin_dir not in sys.path:
	sys.path.insert(0, plugin_dir)

from .soundUtils import (
	SoundProcessor,
	LEFT,
	RIGHT,
	LEFT_TO_RIGHT,
	RIGHT_TO_LEFT,
	ERROR_WARNING,
	SOUND_EFFECTS,
	PROGRESS_INDICATOR,
	ADDON_BEEP,
	CENTER,
	TONE_SINE,
	TONE_SAWTOOTH,
	TONE_TRIANGLE,
	TONE_SQUARE
)

addonHandler.initTranslation()

sound_context = threading.local()

DEFAULT_SETTINGS = {
	"errorDirection": LEFT,
	"effectsDirection": LEFT,
	"progressDirection": LEFT_TO_RIGHT,
	"addonBeepDirectionA": LEFT,
	"addonBeepDirectionB": RIGHT,
	"isActive": True,
	"waveformType": 0,
	"fadeAlgorithm": "cosine",
	"volume": 0.5,
	"minFrequency": 110,
	"maxFrequency": 1760,
	"speechPercentageInterval": 10,
	"beepPercentageInterval": 5,
	"timeBasedInterval": 0,
	"mixedMode": False,
	"smoothPanning": True,
	"masterVolume": 100
}

DEFAULT_ADDON_BEEP_FREQ = 1000

WAVEFORM_NAMES = {
	0: _("Sine"),
	1: _("Triangle"),
	2: _("Sawtooth"),
	3: _("Square"),
	4: _("Original Tone Beep")
}

WAVEFORM_MAP = {
	0: TONE_SINE,
	1: TONE_TRIANGLE,
	2: TONE_SAWTOOTH,
	3: TONE_SQUARE
}

FADE_ALGORITHMS = [
	"cosine",
	"gaussian",
	"linear",
	"exponential",
	"logarithmic",
	"s_curve",
	"sine",
	"quarter_sine",
	"half_sine",
	"square_root",
	"cubic_root",
	"quadratic"
]

FADE_NAMES = {
	"cosine": _("Cosine"),
	"gaussian": _("Gaussian"),
	"linear": _("Linear"),
	"exponential": _("Exponential"),
	"logarithmic": _("Logarithmic"),
	"s_curve": _("S-Curve"),
	"sine": _("Sine"),
	"quarter_sine": _("Quarter Sine"),
	"half_sine": _("Half Sine"),
	"square_root": _("Square Root"),
	"cubic_root": _("Cubic Root"),
	"quadratic": _("Quadratic")
}

try:
	import pyaudiowpatch as pyaudio
except ImportError as e:
	log.error(f"SoundAlign: Failed to import pyaudiowpatch: {e}")
	pyaudio = None

def loadSettings():
	old_settings_path = os.path.join(config.getUserDefaultConfigPath(), "soundAlign.json")
	new_settings_path = os.path.join(config.getUserDefaultConfigPath(), "ChaiChaimee", "soundAlign.json")
	settings = DEFAULT_SETTINGS.copy()
	
	if os.path.exists(new_settings_path):
		settings_path = new_settings_path
		migrate = False
	elif os.path.exists(old_settings_path):
		settings_path = old_settings_path
		migrate = True
	else:
		settings_path = None
		migrate = False

	if settings_path and os.path.exists(settings_path):
		try:
			with open(settings_path, "r", encoding="utf-8") as f:
				userSettings = json.load(f)
			for key in settings:
				if key in userSettings:
					settings[key] = userSettings[key]
			if migrate:
				try:
					os.makedirs(os.path.dirname(new_settings_path), exist_ok=True)
					shutil.move(old_settings_path, new_settings_path)
					log.info(f"SoundAlign: Migrated settings from {old_settings_path} to {new_settings_path}")
				except Exception as e:
					log.error(f"SoundAlign: Failed to migrate settings to new location: {e}")
		except json.JSONDecodeError as e:
			log.error(f"SoundAlign: Error loading settings: JSON file is corrupted or empty ({e}). Using default settings.")
			saveSettings(DEFAULT_SETTINGS)
		except Exception as e:
			log.error(f"SoundAlign: Error loading settings: {e}")
	else:
		saveSettings(settings)
	
	return settings

def saveSettings(settings):
	settingsPath = os.path.join(config.getUserDefaultConfigPath(), "ChaiChaimee", "soundAlign.json")
	try:
		os.makedirs(os.path.dirname(settingsPath), exist_ok=True)
		if not os.access(os.path.dirname(settingsPath), os.W_OK):
			log.error(f"SoundAlign: No write permission for {os.path.dirname(settingsPath)}")
			gui.messageBox(
				_("Cannot save settings. No write permission for configuration directory."),
				_("Save Error"),
				wx.OK | wx.ICON_ERROR
			)
			return False
		with open(settingsPath, "w", encoding="utf-8") as f:
			json.dump(settings, f, ensure_ascii=False, indent=4)
		return True
	except Exception as e:
		log.error(f"SoundAlign: Error saving settings: {e}")
		gui.messageBox(
			_("Failed to save settings. Please check permissions or config path."),
			_("Save Error"),
			wx.OK | wx.ICON_ERROR
		)
		return False

class SoundAlignSettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = _("SoundAlign")
	
	def makeSettings(self, settingsSizer):
		self.settings = loadSettings()
		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		directions = [
			("errorDirection", _("Error sounds:")),
			("effectsDirection", _("Sound effects:")),
			("addonBeepDirectionA", _("Addon beeps (Low Frequency):")),
			("addonBeepDirectionB", _("Addon beeps (High Frequency):")),
			("progressDirection", _("Progress indicator:"), True),
		]
		
		self.controls = {}
		
		for setting in directions:
			key = setting[0]
			label = setting[1]
			includeProgress = len(setting) > 2 and setting[2]
			
			choices = [_("Left"), _("Center"), _("Right")]
			if includeProgress:
				choices.extend([_("Left to Right"), _("Right to Left")])
			
			control = sHelper.addLabeledControl(
				label,
				wx.Choice,
				choices=choices
			)
			control.SetSelection(self.settings.get(key, DEFAULT_SETTINGS[key]))
			self.controls[key] = control
			
		waveform_choices = [name for name in WAVEFORM_NAMES.values()]
		self.waveformControl = sHelper.addLabeledControl(
			_("Progress tone:"),
			wx.Choice,
			choices=waveform_choices
		)
		waveform_type = self.settings.get("waveformType", 0)
		if waveform_type in WAVEFORM_NAMES:
			self.waveformControl.SetSelection(waveform_type)
		else:
			self.waveformControl.SetSelection(0)

		fade_choices = [FADE_NAMES[alg] for alg in FADE_ALGORITHMS]
		self.fadeAlgorithmControl = sHelper.addLabeledControl(
			_("Fade algorithm:"),
			wx.Choice,
			choices=fade_choices
		)
		current_fade = self.settings.get("fadeAlgorithm", "cosine")
		if current_fade in FADE_ALGORITHMS:
			self.fadeAlgorithmControl.SetSelection(FADE_ALGORITHMS.index(current_fade))
		else:
			self.fadeAlgorithmControl.SetSelection(0)

		volume_choices = [str(round(i * 0.1, 1)) for i in range(1, 11)]
		self.volumeControl = sHelper.addLabeledControl(
			_("Volume:"),
			wx.Choice,
			choices=volume_choices
		)
		volume = self.settings.get("volume", 0.5)
		volume_index = min(max(int(volume * 10) - 1, 0), 9)
		self.volumeControl.SetSelection(volume_index)

		master_volume_sizer = wx.BoxSizer(wx.HORIZONTAL)
		master_label = wx.StaticText(self, label=_("Master volume (%):"))
		self.masterVolumeControl = wx.Slider(
			self,
			value=self.settings.get("masterVolume", 100),
			minValue=0,
			maxValue=100,
			style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_LABELS
		)
		self.masterVolumeControl.SetTickFreq(10)
		master_volume_sizer.Add(master_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
		master_volume_sizer.Add(self.masterVolumeControl, 1, wx.EXPAND)
		sHelper.addItem(master_volume_sizer)

		min_freq_choices = [f"{freq}Hz" for freq in range(110, 301, 10)]
		self.minFrequencyControl = sHelper.addLabeledControl(
			_("Minimum frequency:"),
			wx.Choice,
			choices=min_freq_choices
		)
		min_freq = self.settings.get("minFrequency", 110)
		min_freq_index = min(max((min_freq - 110) // 10, 0), len(min_freq_choices) - 1)
		self.minFrequencyControl.SetSelection(min_freq_index)

		max_freq_choices = [f"{freq}Hz" for freq in range(1200, 1761, 10)]
		self.maxFrequencyControl = sHelper.addLabeledControl(
			_("Maximum frequency:"),
			wx.Choice,
			choices=max_freq_choices
		)
		max_freq = self.settings.get("maxFrequency", 1760)
		max_freq_index = min(max((max_freq - 1200) // 10, 0), len(max_freq_choices) - 1)
		self.maxFrequencyControl.SetSelection(max_freq_index)

		speech_intervals = ["1%", "2%", "5%", "10%"]
		self.speechIntervalControl = sHelper.addLabeledControl(
			_("Speech announcement interval:"),
			wx.Choice,
			choices=[_(interval) for interval in speech_intervals]
		)
		speech_interval = self.settings.get("speechPercentageInterval", 10)
		speech_index = {1: 0, 2: 1, 5: 2, 10: 3}.get(speech_interval, 3)
		self.speechIntervalControl.SetSelection(speech_index)

		beep_intervals = ["1%", "2%", "5%", "10%"]
		self.beepIntervalControl = sHelper.addLabeledControl(
			_("Beep announcement interval:"),
			wx.Choice,
			choices=[_(interval) for interval in beep_intervals]
		)
		beep_interval = self.settings.get("beepPercentageInterval", 5)
		beep_index = {1: 0, 2: 1, 5: 2, 10: 3}.get(beep_interval, 2)
		self.beepIntervalControl.SetSelection(beep_index)

		self.timeBasedControl = sHelper.addLabeledControl(
			_("Time-based announcement (seconds, 0=disabled):"),
			wx.SpinCtrl,
			min=0,
			max=60,
			initial=self.settings.get("timeBasedInterval", 0)
		)

		self.mixedModeControl = sHelper.addItem(wx.CheckBox(self, label=_("Mixed mode (speech + beep)")))
		self.mixedModeControl.SetValue(self.settings.get("mixedMode", False))

		self.smoothPanningControl = sHelper.addItem(wx.CheckBox(self, label=_("Smooth panning (reduces cracking)")))
		self.smoothPanningControl.SetValue(self.settings.get("smoothPanning", True))

		self.minFrequencyControl.Bind(wx.EVT_CHOICE, self.onFrequencyChange)
		self.maxFrequencyControl.Bind(wx.EVT_CHOICE, self.onFrequencyChange)

		testBtn = sHelper.addItem(wx.Button(self, label=_("Test All Settings")))
		testBtn.Bind(wx.EVT_BUTTON, self.onTest)

	def onFrequencyChange(self, evt):
		min_freq_index = self.minFrequencyControl.GetSelection()
		max_freq_index = self.maxFrequencyControl.GetSelection()
		
		if min_freq_index >= 0 and max_freq_index >= 0:
			min_freq = 110 + min_freq_index * 10
			max_freq = 1200 + max_freq_index * 10
			
			if min_freq >= max_freq:
				wx.CallAfter(
					gui.messageBox,
					_("Minimum frequency must be less than maximum frequency."),
					_("Frequency Range Error"),
					wx.OK | wx.ICON_WARNING
				)
		evt.Skip()

	def onTest(self, evt):
		if not hasattr(GlobalPlugin, 'instance') or GlobalPlugin.instance is None:
			log.error("SoundAlign: No GlobalPlugin instance for testing")
			return
		
		instance = GlobalPlugin.instance
		
		tests_beep = [
			("errorDirection", ERROR_WARNING, 600, 300),
			("effectsDirection", SOUND_EFFECTS, 1000, 100),
			("addonBeepDirectionA", ADDON_BEEP, 500, 500),
			("addonBeepDirectionB", ADDON_BEEP, 1500, 500),
		]

		for key, sound_type, freq, duration in tests_beep:
			direction = self.controls[key].GetSelection()
			instance.testBeep(freq, duration, direction, sound_type)
			time.sleep(0.6)
		
		progress_direction = self.controls["progressDirection"].GetSelection()
		waveform_type = self.waveformControl.GetSelection()
		instance.testProgress(progress_direction, waveform_type)

	def isValid(self):
		min_freq_index = self.minFrequencyControl.GetSelection()
		max_freq_index = self.maxFrequencyControl.GetSelection()
		
		if min_freq_index < 0 or max_freq_index < 0:
			gui.messageBox(
				_("Please select valid minimum and maximum frequencies."),
				_("Settings Error"),
				wx.OK | wx.ICON_ERROR
			)
			return False
		
		min_freq = 110 + min_freq_index * 10
		max_freq = 1200 + max_freq_index * 10
		
		if min_freq >= max_freq:
			gui.messageBox(
				_("Minimum frequency must be less than maximum frequency."),
				_("Frequency Range Error"),
				wx.OK | wx.ICON_ERROR
			)
			return False
		
		return True

	def onSave(self):
		settings = {key: control.GetSelection() for key, control in self.controls.items()}
		settings["waveformType"] = self.waveformControl.GetSelection()
		fade_index = self.fadeAlgorithmControl.GetSelection()
		if 0 <= fade_index < len(FADE_ALGORITHMS):
			settings["fadeAlgorithm"] = FADE_ALGORITHMS[fade_index]
		else:
			settings["fadeAlgorithm"] = "cosine"
		settings["volume"] = (self.volumeControl.GetSelection() + 1) * 0.1
		settings["masterVolume"] = self.masterVolumeControl.GetValue()
		settings["minFrequency"] = 110 + self.minFrequencyControl.GetSelection() * 10
		settings["maxFrequency"] = 1200 + self.maxFrequencyControl.GetSelection() * 10
		settings["isActive"] = True
		
		speech_intervals = {0: 1, 1: 2, 2: 5, 3: 10}
		settings["speechPercentageInterval"] = speech_intervals.get(self.speechIntervalControl.GetSelection(), 10)
		
		beep_intervals = {0: 1, 1: 2, 2: 5, 3: 10}
		settings["beepPercentageInterval"] = beep_intervals.get(self.beepIntervalControl.GetSelection(), 5)
		
		settings["timeBasedInterval"] = self.timeBasedControl.GetValue()
		settings["mixedMode"] = self.mixedModeControl.GetValue()
		settings["smoothPanning"] = self.smoothPanningControl.GetValue()
		
		if saveSettings(settings):
			if hasattr(GlobalPlugin, 'instance') and GlobalPlugin.instance:
				GlobalPlugin.instance.applySettings()
		else:
			gui.messageBox(
				_("Failed to save settings. Please check permissions or config path."),
				_("Save Error"),
				wx.OK | wx.ICON_ERROR
			)

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	instance = None
	scriptCategory = _("SoundAlign")
	
	def __init__(self):
		super(GlobalPlugin, self).__init__()
		GlobalPlugin.instance = self
		self.settings = loadSettings()
		
		self.originalBeep = tones.beep
		self.originalWinsoundBeep = winsound.Beep if winsound is not None else None
		
		self.sound_processor = None
		if pyaudio:
			try:
				self.sound_processor = SoundProcessor(self, pyaudio)
			except Exception as e:
				log.error(f"SoundAlign: Failed to initialize SoundProcessor: {e}")
		
		self.last_spoken_percent = -1
		self.last_beep_percent = -1
		self.last_time_announced = 0
		self.last_progress_object = None
		
		self.last_gesture_time = 0
		self.gesture_count = 0
		self.double_tap_threshold = 0.3
		self._settings_dialog_open = False
		
		self.setupHooks()
		self.registerSettingsPanel()
		
		wx.CallAfter(self.applySettings)

	def registerSettingsPanel(self):
		try:
			if SoundAlignSettingsPanel not in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
				gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(SoundAlignSettingsPanel)
		except Exception as e:
			log.error(f"SoundAlign: Error registering settings panel: {e}")

	def setupHooks(self):
		try:
			tones.beep = self.safeBeep
			if winsound is not None:
				winsound.Beep = self.safeBeepWinsound
			
			for addon in addonHandler.getRunningAddons():
				try:
					if hasattr(addon, 'module') and hasattr(addon.module, 'tones'):
						addon.module.tones.beep = self.safeBeep
					if winsound is not None and hasattr(addon, 'module') and hasattr(addon.module, 'winsound'):
						addon.module.winsound.Beep = self.safeBeepWinsound
				except Exception as e:
					log.error(f"SoundAlign: Failed to hook tones.beep or winsound.Beep for add-on {addon.name}: {e}")
		except Exception as e:
			log.error(f"SoundAlign: Error setting up hooks: {e}")

	def safeBeep(self, hz, length, left=50, right=50, *args, **kwargs):
		if not self.settings.get("isActive", True):
			return self.originalBeep(hz, length, left, right, *args, **kwargs)
		
		soundType = self.getSoundType(hz, length)
		direction = self.getDirection(soundType, hz)
		
		if soundType == PROGRESS_INDICATOR:
			if self.settings.get("waveformType") == 4:
				obj = api.getFocusObject()
				percent = self.sound_processor.get_progress_percent(obj) if self.sound_processor else None
				
				if not isinstance(percent, (int, float)) or percent < 0:
					if self.settings.get("maxFrequency") > self.settings.get("minFrequency"):
						percent = (hz - self.settings.get("minFrequency")) / (self.settings.get("maxFrequency") - self.settings.get("minFrequency")) * 100
					else:
						percent = 50
					percent = max(0, min(100, percent))
				
				self.handleProgressAnnouncements(percent, obj)
				
				pan_pos = percent / 100.0 if direction == LEFT_TO_RIGHT else 1.0 - (percent / 100.0) if direction == RIGHT_TO_LEFT else 0.5
				leftVol_dynamic = (1.0 - pan_pos) * 100
				rightVol_dynamic = pan_pos * 100
				master = self.settings.get("masterVolume", 100) / 100.0
				self.originalBeep(hz, length, left=int(leftVol_dynamic * master), right=int(rightVol_dynamic * master))
			else:
				obj = api.getFocusObject()
				percent = self.sound_processor.get_progress_percent(obj) if self.sound_processor else None
				
				if percent is None:
					percent = min(100, max(0, (hz - self.settings.get("minFrequency", 110)) / (self.settings.get("maxFrequency", 1760) - self.settings.get("minFrequency", 110)) * 100))
				
				self.handleProgressAnnouncements(percent, obj)
				
				if percent != getattr(sound_context, 'last_progress_value', -1):
					try:
						if self.sound_processor:
							self.sound_processor.play_progress_sound(percent, direction)
							sound_context.last_progress_value = percent
					except Exception as e:
						log.error(f"SoundAlign: Error playing progress sound: {e}. Falling back to original beep.")
						self.originalBeep(hz, length, left=left, right=right)
		else:
			leftVol, rightVol = self.getBalance(direction)
			master = self.settings.get("masterVolume", 100) / 100.0
			self.originalBeep(hz, length, left=int(leftVol * 100 * master), right=int(rightVol * 100 * master))
			
	def safeBeepWinsound(self, frequency, duration):
		if winsound is None or not self.settings.get("isActive", True):
			if self.originalWinsoundBeep:
				self.originalWinsoundBeep(frequency, duration)
			return
		
		self.safeBeep(frequency, duration, left=50, right=50)
		
	def handleProgressAnnouncements(self, percent, obj):
		if not isinstance(percent, (int, float)) or percent < 0:
			return
			
		current_time = time.time()
		time_interval = self.settings.get("timeBasedInterval", 0)
		speech_interval = self.settings.get("speechPercentageInterval", 10)
		beep_interval = self.settings.get("beepPercentageInterval", 5)
		mixed_mode = self.settings.get("mixedMode", False)
		
		if obj != self.last_progress_object:
			self.last_progress_object = obj
			self.last_spoken_percent = -1
			self.last_beep_percent = -1
			self.last_time_announced = 0
		
		if time_interval > 0 and current_time - self.last_time_announced >= time_interval:
			ui.message(_("{percent}% complete").format(percent=int(percent)))
			self.last_time_announced = current_time
			self.last_spoken_percent = percent
			return
		
		if mixed_mode:
			if percent % speech_interval == 0 and percent != self.last_spoken_percent:
				ui.message(_("{percent}% complete").format(percent=int(percent)))
				self.last_spoken_percent = percent
			elif percent % beep_interval == 0 and percent != self.last_beep_percent:
				self.last_beep_percent = percent
		else:
			if percent % speech_interval == 0 and percent != self.last_spoken_percent:
				ui.message(_("{percent}% complete").format(percent=int(percent)))
				self.last_spoken_percent = percent

	def getSoundType(self, hz, length):
		if hz == 600 and length == 300:
			return ERROR_WARNING
		
		if (110 <= hz <= 2000 and 15 <= length <= 60) or (hz == 2000 and length == 150):
			return PROGRESS_INDICATOR
			
		return ADDON_BEEP

	def getDirection(self, soundType, hz=None):
		if soundType == ADDON_BEEP:
			if hz is not None and hz < 1000:
				direction = self.settings.get("addonBeepDirectionA", LEFT)
			else:
				direction = self.settings.get("addonBeepDirectionB", RIGHT)
		else:
			settingMap = {
				ERROR_WARNING: "errorDirection",
				SOUND_EFFECTS: "effectsDirection",
				PROGRESS_INDICATOR: "progressDirection",
			}
			direction = self.settings.get(settingMap.get(soundType), LEFT_TO_RIGHT if soundType == PROGRESS_INDICATOR else CENTER)
		
		return direction

	def getBalance(self, direction):
		if direction == LEFT:
			return (1.0, 0.0)
		elif direction == CENTER:
			return (0.5, 0.5)
		elif direction == RIGHT:
			return (0.0, 1.0)
		elif direction == LEFT_TO_RIGHT or direction == RIGHT_TO_LEFT:
			return (0.5, 0.5)
		return (0.5, 0.5)

	def applySettings(self):
		try:
			self.settings = loadSettings()
			if self.sound_processor:
				waveform_type = self.settings.get("waveformType", 0)
				self.sound_processor.harmonics = WAVEFORM_MAP.get(waveform_type, TONE_SINE)
				self.sound_processor.fade_algorithm = self.settings.get("fadeAlgorithm", "cosine")
				self.sound_processor.volume = self.settings.get("volume", 0.5)
				self.sound_processor.master_volume = self.settings.get("masterVolume", 100) / 100.0
				self.sound_processor.min_frequency = self.settings.get("minFrequency", 110)
				self.sound_processor.max_frequency = self.settings.get("maxFrequency", 1760)
				self.sound_processor.smooth_panning = self.settings.get("smoothPanning", True)
				if not self.sound_processor.player_thread or not self.sound_processor.player_thread.is_alive():
					log.warning("SoundAlign: Player thread not running, restarting")
					self.sound_processor.start_player_thread()
			else:
				log.warning("SoundAlign: No sound processor available for settings application")
		except Exception as e:
			log.error(f"SoundAlign: Error applying settings: {e}")

	def testBeep(self, freq, duration, direction, soundType):
		leftVol, rightVol = self.getBalance(direction)
		master = self.settings.get("masterVolume", 100) / 100.0
		self.originalBeep(freq, duration, left=int(leftVol*100*master), right=int(rightVol*100*master))

	def testProgress(self, direction, waveform_type):
		if waveform_type == 4:
			for i in range(0, 101, 2):
				hz = self.settings.get("minFrequency") + (i / 100.0) * (self.settings.get("maxFrequency") - self.settings.get("minFrequency"))
				pan_pos = i / 100.0 if direction == LEFT_TO_RIGHT else 1.0 - (i / 100.0) if direction == RIGHT_TO_LEFT else 0.5
				leftVol_dynamic = (1.0 - pan_pos) * 100
				rightVol_dynamic = pan_pos * 100
				master = self.settings.get("masterVolume", 100) / 100.0
				self.originalBeep(int(hz), 40, left=int(leftVol_dynamic * master), right=int(rightVol_dynamic * master))
				time.sleep(0.02)
		else:
			if not self.sound_processor:
				log.error("SoundAlign: Cannot test progress, pyaudio not available.")
				ui.message(_("Progress test not available. Pyaudio not imported."))
				return
			
			original_harmonics = self.sound_processor.harmonics
			self.sound_processor.harmonics = WAVEFORM_MAP.get(waveform_type, TONE_SINE)
			original_master = self.sound_processor.master_volume
			self.sound_processor.master_volume = self.settings.get("masterVolume", 100) / 100.0
			
			self.sound_processor.flush_queue()
			for i in range(0, 101, 2):
				percent = i
				self.sound_processor.play_progress_sound(percent, direction=direction)
				time.sleep(0.02)
			
			self.sound_processor.harmonics = original_harmonics
			self.sound_processor.master_volume = original_master

	@script(
		description=_("Open SoundAlign settings (single tap) or toggle SoundAlign on/off (double tap)"),
		gesture="kb:NVDA+Windows+S"
	)
	def script_handleSoundAlign(self, gesture):
		current_time = time.time()
		
		if current_time - self.last_gesture_time < self.double_tap_threshold:
			self.gesture_count += 1
		else:
			self.gesture_count = 1
		
		self.last_gesture_time = current_time
		
		if self.gesture_count >= 2:
			self.settings['isActive'] = not self.settings.get('isActive', True)
			saveSettings(self.settings)
			self.applySettings()
			if self.settings['isActive']:
				ui.message(_("SoundAlign on"))
			else:
				ui.message(_("SoundAlign off"))
			self.gesture_count = 0
		else:
			def handleSingleTap():
				if self.gesture_count == 1 and not self._settings_dialog_open:
					self._settings_dialog_open = True
					wx.CallAfter(self._showSettingsDialog)
				self.gesture_count = 0
			
			wx.CallLater(int(self.double_tap_threshold * 1000), handleSingleTap)

	def _showSettingsDialog(self):
		try:
			if hasattr(gui.settingsDialogs.NVDASettingsDialog, '_instances'):
				for dlg in gui.settingsDialogs.NVDASettingsDialog._instances:
					if dlg and dlg.IsShown():
						dlg.Raise()
						self._settings_dialog_open = False
						return
			
			gui.mainFrame.popupSettingsDialog(
				gui.settingsDialogs.NVDASettingsDialog,
				SoundAlignSettingsPanel
			)
		except Exception as e:
			log.error(f"SoundAlign: Failed to show settings dialog: {e}")
		finally:
			wx.CallLater(500, self._resetSettingsDialogFlag)

	def _resetSettingsDialogFlag(self):
		self._settings_dialog_open = False

	def terminate(self):
		tones.beep = self.originalBeep
		if winsound is not None:
			winsound.Beep = self.originalWinsoundBeep
		
		if self.sound_processor:
			self.sound_processor.stop()
		
		GlobalPlugin.instance = None