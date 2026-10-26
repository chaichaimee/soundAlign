# soundAlignCore.py

import sys
import os
import shutil
import threading
import time
import json
import re
import globalPluginHandler
import gui
import wx
import config
import tones
import ui
import synthDriverHandler
import speech
import addonHandler
from logHandler import log
import scriptHandler
from scriptHandler import script
import api
import UIAHandler
from NVDAObjects import NVDAObject
import winsound
from core import callLater

# ------------------------------------------------------------
# NOTE: earlier revisions of this add-on bundled 'pyaudiowpatch' (under
# tools/x86 and tools/x64) and opened a separate PyAudio/WASAPI output
# stream for progress tones and panned wave files, running in parallel to
# NVDA's own audio engine. That redundant second audio path turned out to
# be the root cause of persistent crackling, missing panning, and doubled
# sound reported during testing. SoundProcessor (soundUtils.py) now plays
# everything through nvwave.WavePlayer -- the same class tones.py itself
# uses for tones.beep -- so there is exactly one audio path, matching
# NVDA's own. pyaudiowpatch and the tools/x86 & tools/x64 folders are no
# longer used at all; installTasks.py now removes them if present from a
# previous install.
# ------------------------------------------------------------

plugin_dir = os.path.dirname(__file__)
if plugin_dir not in sys.path:
	sys.path.insert(0, plugin_dir)

from .soundUtils import (
	SoundProcessor,
	fraction_to_gain,
	LEFT,
	RIGHT,
	LEFT_TO_RIGHT,
	RIGHT_TO_LEFT,
	PROGRESS_INDICATOR,
	ADDON_BEEP,
	CENTER,
	TONE_SINE,
	TONE_SAWTOOTH,
	TONE_TRIANGLE,
	TONE_SQUARE
)
from . import update

addonHandler.initTranslation()

sound_context = threading.local()

# SoundAlign is scoped to tones.beep-based sounds only: addon beeps and the
# progress indicator. Wave-file cues (NVDA's built-in "Error sounds" and
# other .wav-based UI sounds) are intentionally not handled -- panning
# those required intercepting nvwave.playWaveFile and re-rendering the
# audio through a separate pipeline, which proved unreliable across
# several attempts (unpanned/doubled playback). Keeping scope to tones.beep
# keeps the add-on simple and reliable.
DEFAULT_SETTINGS = {
	"progressDirection": LEFT_TO_RIGHT,
	"addonBeepDirectionA": LEFT,
	"addonBeepDirectionC": CENTER,
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
	# Off by default: it adds real CPU cost to progress-tone generation.
	# Users can opt in from the settings panel once they've confirmed
	# their system handles the base feature set well.
	"progressReverb": False,
	"masterVolume": 100,
	"beepVolume": 100,
	# Internal (folder) names of add-ons that should NOT have their
	# tones.beep()/winsound.Beep() calls processed by SoundAlign at all --
	# see GlobalPlugin._getCallingAddonName and safeBeep. Empty by default
	# so nothing is excluded unless the user opts in from Settings.
	"excludedAddons": [],
	# Per-add-on beep direction override: internalName -> LEFT/CENTER/RIGHT.
	# Takes priority over the low/medium/high-frequency direction mapping
	# for that specific add-on's beeps (see GlobalPlugin.getDirection).
	"addonDirectionOverrides": {},
	# User-saved progress-tone presets, keyed by the name the user typed --
	# same value shape as PROGRESS_PRESETS' built-in entries.
	"customPresets": {},
	# Auto-duck: temporarily lowers progress-tone volume while NVDA speech
	# is actually producing audio, restoring it once speech finishes, so a
	# progress sweep doesn't compete with SayAll/dictation. See
	# GlobalPlugin._onSynthPreSpeak/_onSynthDoneSpeaking.
	"autoDuckEnabled": True,
	"autoDuckPercent": 50,
	# Heartbeat: periodic reminder that a long-running operation is still
	# in progress, and an escalated "may be stuck" notice if it stalls at
	# the same percent for too long. See GlobalPlugin._heartbeatTick.
	"heartbeatEnabled": False,
	"heartbeatIntervalSeconds": 8,
	"heartbeatStuckThresholdSeconds": 20
}

# Addon beeps below this frequency are treated as "Low Frequency"; at or
# above ADDON_BEEP_HIGH_MIN_FREQ as "High Frequency"; everything in
# between as "Medium Frequency" -- three bands instead of two, for a bit
# more spatial variety among addon-beep pitches.
ADDON_BEEP_LOW_MAX_FREQ = 500
ADDON_BEEP_HIGH_MIN_FREQ = 1500

# NVDA's own core computes the pitch for progress-bar beeps linearly across
# a FIXED 110-1760Hz range, entirely independent of this add-on's own
# "Minimum/Maximum frequency" settings (which only control the pitch of
# SoundAlign's own re-rendered progress tone). These two ranges used to be
# conflated -- decoding NVDA's incoming hz using the user's own configured
# render range -- which broke as soon as they didn't match (e.g. picking
# the "Subtle" preset's 300-900Hz range), producing wildly wrong percent
# values and therefore an erratic, non-monotonic pan sweep. Always decode
# using NVDA's real, fixed range instead.
NVDA_PROGRESS_MIN_PITCH = 110
NVDA_PROGRESS_MAX_PITCH = 1760

# Step size applied to percent-based spin controls' up/down arrows (e.g.
# Volume %); see SoundAlignSettingsPanel._onPercentSpinChanged.
PERCENT_SPIN_STEP = 5

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

# Simple presets for the progress-tone settings, meant for people who just
# want something that sounds good without understanding what "fade
# algorithm" or "harmonics" mean. Picking a preset fills in the individual
# controls below with sensible values -- it doesn't hide or replace them,
# so anyone who wants to fine-tune further still can, starting from a good
# baseline instead of from scratch.
PROGRESS_PRESETS = {
	# Matches this add-on's original, simplest sound: a plain sine sweep.
	"balanced": {
		"waveformType": 0,  # Sine
		"fadeAlgorithm": "cosine",
		"volume": 0.5,
		"masterVolume": 100,
		"minFrequency": 110,
		"maxFrequency": 1760,
		"progressReverb": False,
	},
	"subtle": {
		"waveformType": 0,  # Sine
		"fadeAlgorithm": "sine",
		"volume": 0.3,
		"masterVolume": 60,
		"minFrequency": 300,
		"maxFrequency": 900,
		"progressReverb": False,
	},
	"prominent": {
		"waveformType": 0,  # Sine
		"fadeAlgorithm": "cosine",
		"volume": 0.8,
		"masterVolume": 100,
		"minFrequency": 150,
		"maxFrequency": 1600,
		"progressReverb": True,
	},
}

# (choice label, preset key). "custom" is always first/selected by default
# so simply opening the settings panel never silently overwrites anything.
_PROGRESS_PRESET_CHOICES = [
	(_("Custom (keep my current settings)"), "custom"),
	(_("Balanced (recommended)"), "balanced"),
	(_("Subtle / quiet"), "subtle"),
	(_("Prominent / clear"), "prominent"),
]

def getConfigDir():
	"""
	SoundAlign's own config folder: <configPath>\\ChaiChaimee\\soundAlign\\ --
	one path segment for "ChaiChaimee" and one for the add-on name, per the
	house config-path convention (Section 5.12). Any future JSON file this
	add-on needs (separate from the main settings file) belongs here too,
	so there is exactly one place to look.
	"""
	return os.path.join(config.getUserDefaultConfigPath(), "ChaiChaimee", "soundAlign")

SETTINGS_FILENAME = "soundAlign.json"

def _candidateSettingsPaths():
	"""
	Every settings-file location this add-on has ever used, current layout
	first and each prior layout after it -- oldest last -- per the
	two-layer (here: three-layer) config migration discipline (Section 13).
	"""
	currentPath = os.path.join(getConfigDir(), SETTINGS_FILENAME)
	priorFlatPath = os.path.join(config.getUserDefaultConfigPath(), "ChaiChaimee", "soundAlign.json")
	oldestPath = os.path.join(config.getUserDefaultConfigPath(), "soundAlign.json")
	return [currentPath, priorFlatPath, oldestPath]

def loadSettings():
	currentPath, priorFlatPath, oldestPath = _candidateSettingsPaths()
	settings = DEFAULT_SETTINGS.copy()

	settings_path = None
	migrate = False
	for candidatePath in (currentPath, priorFlatPath, oldestPath):
		if os.path.exists(candidatePath):
			settings_path = candidatePath
			migrate = candidatePath != currentPath
			break

	if settings_path:
		try:
			with open(settings_path, "r", encoding="utf-8") as f:
				userSettings = json.load(f)
			for key in settings:
				if key in userSettings:
					settings[key] = userSettings[key]
			if migrate:
				layoutName = "flat ChaiChaimee\\soundAlign.json" if settings_path == priorFlatPath else "original configPath\\soundAlign.json"
				try:
					os.makedirs(os.path.dirname(currentPath), exist_ok=True)
					shutil.move(settings_path, currentPath)
					log.debug(f"SoundAlign: Migrated settings from {layoutName} to {currentPath}")
				except Exception as e:
					log.error(f"SoundAlign: Failed to migrate settings from {layoutName}: {e}")
		except json.JSONDecodeError as e:
			log.error(f"SoundAlign: JSON corrupt ({e})")
			saveSettings(DEFAULT_SETTINGS)
		except Exception as e:
			log.error(f"SoundAlign: Load error: {e}")
	else:
		saveSettings(settings)

	return settings

def saveSettings(settings):
	settingsPath = os.path.join(getConfigDir(), SETTINGS_FILENAME)
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
		log.error(f"SoundAlign: Save error: {e}")
		gui.messageBox(
			_("Failed to save settings. Please check permissions or config path."),
			_("Save Error"),
			wx.OK | wx.ICON_ERROR
		)
		return False

def getInstalledAddonChoices():
	"""
	Returns (internalName, displayLabel) pairs for every currently loaded
	add-on except SoundAlign itself, sorted by displayLabel. internalName
	is the add-on's top-level package name -- the same name that appears
	as the second component of a "globalPlugins.<name>"/"appModules.<name>"
	module path -- which is what GlobalPlugin._getCallingAddonName
	produces and what actually gets stored in the excludedAddons setting.
	"""
	choices = []
	try:
		for addon in addonHandler.getAvailableAddons():
			try:
				internalName = addon.name
			except Exception:
				continue
			if not internalName or internalName == "soundAlign":
				continue
			try:
				displayLabel = addon.manifest.get("summary", internalName)
			except Exception:
				displayLabel = internalName
			choices.append((internalName, displayLabel))
	except Exception as e:
		log.error(f"SoundAlign: Error listing installed add-ons: {e}")
	choices.sort(key=lambda pair: pair[1].lower())
	return choices

# Bound on how many stack frames GlobalPlugin._getCallingAddonName will
# walk looking for the calling add-on's module, so a pathological call
# chain (or a caller that never resolves to an add-on module at all)
# still returns promptly instead of walking the whole stack every beep.
_MAX_CALLER_STACK_DEPTH = 25

# How often GlobalPlugin._heartbeatTick re-checks whether the current
# progress operation has stalled. Kept coarse (a few seconds) since this
# is just a timestamp comparison, not anything performance-sensitive.
_HEARTBEAT_CHECK_INTERVAL_MS = 3000

# (settingKey, label) -- each addon-beep row is a plain Left/Center/Right
# choice. Progress indicator is handled separately below because it
# sweeps continuously rather than sitting at a fixed side.
_DIRECTION_ROWS = [
	("addonBeepDirectionA", _("Addon beeps (Low Frequency):")),
	("addonBeepDirectionC", _("Addon beeps (Medium Frequency):")),
	("addonBeepDirectionB", _("Addon beeps (High Frequency):")),
]

class SoundAlignSettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = _("SoundAlign")

	def makeSettings(self, settingsSizer):
		self.settings = loadSettings()
		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		self.controls = {}
		# Tracks each step-by-5 spin control's last known value so a single
		# arrow-key/spin-button step (which wx always reports as a delta of
		# exactly +/-1) can be re-applied as a step of PERCENT_SPIN_STEP
		# instead. A directly typed value (any other delta) is left as-is.
		self._spinLastValues = {}

		for key, label in _DIRECTION_ROWS:
			choices = [_("Left"), _("Center"), _("Right")]
			control = sHelper.addLabeledControl(label, wx.Choice, choices=choices)
			control.SetSelection(self.settings.get(key, DEFAULT_SETTINGS[key]))
			self.controls[key] = control

		progressChoices = [_("Left"), _("Center"), _("Right"), _("Left to Right"), _("Right to Left")]
		self.progressControl = sHelper.addLabeledControl(
			_("Progress indicator:"),
			wx.Choice,
			choices=progressChoices
		)
		self.progressControl.SetSelection(self.settings.get("progressDirection", DEFAULT_SETTINGS["progressDirection"]))
		self.controls["progressDirection"] = self.progressControl

		builtinPresetChoices = [label for label, _key in _PROGRESS_PRESET_CHOICES]
		customPresetNames = sorted(self.settings.get("customPresets", {}).keys(), key=str.lower)
		self._presetKeyList = [key for _label, key in _PROGRESS_PRESET_CHOICES] + customPresetNames
		preset_choices = builtinPresetChoices + customPresetNames
		self.presetControl = sHelper.addLabeledControl(
			_("Progress sound preset:"),
			wx.Choice,
			choices=preset_choices
		)
		self.presetControl.SetSelection(0)
		self.presetControl.Bind(wx.EVT_CHOICE, self._onPresetChanged)

		self.presetNameControl = sHelper.addLabeledControl(
			_("New preset name:"),
			wx.TextCtrl
		)
		savePresetBtn = sHelper.addItem(wx.Button(self, label=_("Save current settings as preset")))
		savePresetBtn.Bind(wx.EVT_BUTTON, self.onSavePreset)

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

		self.volumeControl = sHelper.addLabeledControl(
			_("Progress volume (%):"),
			wx.SpinCtrl,
			min=0,
			max=100,
			initial=int(round(self.settings.get("volume", 0.5) * 100))
		)
		self._spinLastValues[self.volumeControl] = self.volumeControl.GetValue()
		self.volumeControl.Bind(wx.EVT_SPINCTRL, self._onPercentSpinChanged)

		# Deliberately its own SpinCtrl instance, independent from
		# volumeControl above: each spin button only ever adjusts the
		# _spinLastValues entry keyed by its own control, so raising or
		# lowering addon-beep volume never touches progress volume (or
		# vice versa).
		self.beepVolumeControl = sHelper.addLabeledControl(
			_("Addon beep volume (%):"),
			wx.SpinCtrl,
			min=0,
			max=100,
			initial=int(self.settings.get("beepVolume", 100))
		)
		self._spinLastValues[self.beepVolumeControl] = self.beepVolumeControl.GetValue()
		self.beepVolumeControl.Bind(wx.EVT_SPINCTRL, self._onPercentSpinChanged)

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
		# Arrow keys/Page Up-Down normally step a wx.Slider by 1/10; step by
		# PERCENT_SPIN_STEP (5) instead so it matches the Volume control's
		# granularity and is easier to land on a round number.
		self.masterVolumeControl.SetLineSize(PERCENT_SPIN_STEP)
		self.masterVolumeControl.SetPageSize(PERCENT_SPIN_STEP * 2)
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

		self.progressReverbControl = sHelper.addItem(
			wx.CheckBox(self, label=_("Add reverb to progress tones"))
		)
		self.progressReverbControl.SetValue(self.settings.get("progressReverb", False))

		self.minFrequencyControl.Bind(wx.EVT_CHOICE, self.onFrequencyChange)
		self.maxFrequencyControl.Bind(wx.EVT_CHOICE, self.onFrequencyChange)

		# Per-add-on exclude list: a checked add-on's tones.beep()/
		# winsound.Beep() calls bypass SoundAlign entirely (see
		# GlobalPlugin.safeBeep), so that add-on's own beeps play exactly
		# as it intended, independent of SoundAlign being active.
		# Computed once per dialog build and cached on self -- querying
		# addonHandler.getAvailableAddons() a second time for the override
		# list below was a confirmed watchdog-freeze contributor (NVDA's
		# own log showed a ~0.7s main-thread freeze while this panel was
		# being built).
		addonChoices = getInstalledAddonChoices()
		self._cachedAddonChoices = addonChoices
		self._excludedAddonInternalNames = [internalName for internalName, _label in addonChoices]
		excludedAddonLabels = [label for _internalName, label in addonChoices]
		self.excludedAddonsControl = sHelper.addLabeledControl(
			_("Exclude these add-ons from SoundAlign (their beeps will not be hooked):"),
			wx.CheckListBox,
			choices=excludedAddonLabels
		)
		currentlyExcluded = set(self.settings.get("excludedAddons", []))
		for index, internalName in enumerate(self._excludedAddonInternalNames):
			if internalName in currentlyExcluded:
				self.excludedAddonsControl.Check(index, True)
		# wx.CheckListBox toggling a box with Space doesn't reliably get
		# announced by NVDA on its own (the reported "no check/not check
		# sound" confusion) -- speak the new state explicitly on every
		# toggle so the user always hears what just happened.
		self.excludedAddonsControl.Bind(wx.EVT_CHECKLISTBOX, self._onExcludedAddonToggled)

		# Per-add-on beep direction override: independent of the exclude
		# list above -- the add-on is still hooked and processed by
		# SoundAlign, but its beeps always pan to the chosen direction
		# instead of being decided by frequency band (see
		# GlobalPlugin.getDirection).
		self._overrideAddonInternalNames = list(self._excludedAddonInternalNames)
		overrideAddonLabels = list(excludedAddonLabels)
		self._addonDirectionOverrides = dict(self.settings.get("addonDirectionOverrides", {}))
		self.overrideAddonControl = sHelper.addLabeledControl(
			_("Add-on for direction override:"),
			wx.Choice,
			choices=overrideAddonLabels
		)
		if overrideAddonLabels:
			self.overrideAddonControl.SetSelection(0)
		self.overrideDirectionControl = sHelper.addLabeledControl(
			_("Override direction:"),
			wx.Choice,
			choices=[_("Left"), _("Center"), _("Right")]
		)
		self.overrideDirectionControl.SetSelection(CENTER)
		addOverrideBtn = sHelper.addItem(wx.Button(self, label=_("Add/update override")))
		addOverrideBtn.Bind(wx.EVT_BUTTON, self.onAddDirectionOverride)

		self.overridesListControl = sHelper.addLabeledControl(
			_("Current direction overrides:"),
			wx.ListBox,
			choices=self._formatDirectionOverrideEntries()
		)
		removeOverrideBtn = sHelper.addItem(wx.Button(self, label=_("Remove selected override")))
		removeOverrideBtn.Bind(wx.EVT_BUTTON, self.onRemoveDirectionOverride)

		# Auto-duck: lower the progress tone while NVDA speech is actually
		# producing audio, so a progress sweep doesn't fight with SayAll or
		# dictation (see GlobalPlugin._onSynthPreSpeak/_onSynthDoneSpeaking).
		self.autoDuckControl = sHelper.addItem(
			wx.CheckBox(self, label=_("Duck progress tone volume while NVDA is speaking"))
		)
		self.autoDuckControl.SetValue(self.settings.get("autoDuckEnabled", True))
		self.autoDuckPercentControl = sHelper.addLabeledControl(
			_("Duck amount (% lower while speaking):"),
			wx.SpinCtrl,
			min=0,
			max=100,
			initial=self.settings.get("autoDuckPercent", 50)
		)

		# Heartbeat: periodic reminder for a long-running, seemingly-stalled
		# progress operation (see GlobalPlugin._heartbeatTick).
		self.heartbeatControl = sHelper.addItem(
			wx.CheckBox(self, label=_("Announce a heartbeat when progress stalls"))
		)
		self.heartbeatControl.SetValue(self.settings.get("heartbeatEnabled", False))
		self.heartbeatIntervalControl = sHelper.addLabeledControl(
			_("Heartbeat interval (seconds):"),
			wx.SpinCtrl,
			min=3,
			max=60,
			initial=self.settings.get("heartbeatIntervalSeconds", 8)
		)

		testBtn = sHelper.addItem(wx.Button(self, label=_("Test All Settings")))
		testBtn.Bind(wx.EVT_BUTTON, self.onTest)

	def _onPresetChanged(self, evt):
		selection = self.presetControl.GetSelection()
		if 0 <= selection < len(self._presetKeyList):
			presetKey = self._presetKeyList[selection]
		else:
			presetKey = "custom"

		if presetKey != "custom":
			preset = PROGRESS_PRESETS.get(presetKey) or self.settings.get("customPresets", {}).get(presetKey)
			if preset is None:
				evt.Skip()
				return

			self.waveformControl.SetSelection(preset["waveformType"])

			if preset["fadeAlgorithm"] in FADE_ALGORITHMS:
				self.fadeAlgorithmControl.SetSelection(FADE_ALGORITHMS.index(preset["fadeAlgorithm"]))

			volumePercent = min(max(int(round(preset["volume"] * 100)), 0), 100)
			self.volumeControl.SetValue(volumePercent)
			self._spinLastValues[self.volumeControl] = volumePercent

			self.masterVolumeControl.SetValue(preset["masterVolume"])

			minIndex = min(max((preset["minFrequency"] - 110) // 10, 0), self.minFrequencyControl.GetCount() - 1)
			self.minFrequencyControl.SetSelection(minIndex)

			maxIndex = min(max((preset["maxFrequency"] - 1200) // 10, 0), self.maxFrequencyControl.GetCount() - 1)
			self.maxFrequencyControl.SetSelection(maxIndex)

			self.progressReverbControl.SetValue(preset["progressReverb"])

		evt.Skip()

	def onSavePreset(self, evt):
		presetName = self.presetNameControl.GetValue().strip()
		if not presetName:
			gui.messageBox(
				_("Please type a name for the new preset first."),
				_("Preset Name Required"),
				wx.OK | wx.ICON_WARNING
			)
			return
		if presetName == "custom" or any(presetName == label for label, _key in _PROGRESS_PRESET_CHOICES):
			gui.messageBox(
				_("That name is reserved for a built-in preset. Please choose a different name."),
				_("Preset Name Reserved"),
				wx.OK | wx.ICON_WARNING
			)
			return

		customPresets = dict(self.settings.get("customPresets", {}))
		if presetName in customPresets:
			overwrite = gui.messageBox(
				_("A preset named \"{name}\" already exists. Overwrite it?").format(name=presetName),
				_("Overwrite Preset"),
				wx.YES_NO | wx.ICON_QUESTION
			)
			if overwrite != wx.YES:
				return

		collected = self._collectSettingsFromControls()
		customPresets[presetName] = {
			"waveformType": collected["waveformType"],
			"fadeAlgorithm": collected["fadeAlgorithm"],
			"volume": collected["volume"],
			"masterVolume": collected["masterVolume"],
			"minFrequency": collected["minFrequency"],
			"maxFrequency": collected["maxFrequency"],
			"progressReverb": collected["progressReverb"],
		}
		self.settings["customPresets"] = customPresets
		if not saveSettings({**loadSettings(), "customPresets": customPresets}):
			return

		if presetName not in self._presetKeyList:
			self._presetKeyList.append(presetName)
			self.presetControl.Append(presetName)
		self.presetControl.SetSelection(self._presetKeyList.index(presetName))
		ui.message(_("Preset \"{name}\" saved").format(name=presetName))

	def _onExcludedAddonToggled(self, evt):
		index = evt.GetInt()
		if 0 <= index < self.excludedAddonsControl.GetCount():
			isChecked = self.excludedAddonsControl.IsChecked(index)
			label = self.excludedAddonsControl.GetString(index)
			if isChecked:
				# Translators: reported when a checkbox-style list item is checked.
				ui.message(_("{addonName} checked").format(addonName=label))
			else:
				# Translators: reported when a checkbox-style list item is unchecked.
				ui.message(_("{addonName} not checked").format(addonName=label))
		evt.Skip()

	def _formatDirectionOverrideEntries(self):
		directionNames = {LEFT: _("Left"), CENTER: _("Center"), RIGHT: _("Right")}
		# Build the internalName->displayLabel lookup from the same
		# cached add-on list the choice controls were populated from (see
		# makeSettings), so entries read the same "friendly" name
		# everywhere in this panel without re-querying installed add-ons
		# on every call.
		addonChoices = getattr(self, "_cachedAddonChoices", None) or getInstalledAddonChoices()
		labelByInternalName = {internalName: label for internalName, label in addonChoices}
		entries = []
		for internalName, direction in self._addonDirectionOverrides.items():
			displayName = labelByInternalName.get(internalName, internalName)
			entries.append("{addonName}: {direction}".format(
				addonName=displayName,
				direction=directionNames.get(direction, _("Center"))
			))
		return entries

	def onAddDirectionOverride(self, evt):
		addonIndex = self.overrideAddonControl.GetSelection()
		if not (0 <= addonIndex < len(self._overrideAddonInternalNames)):
			return
		internalName = self._overrideAddonInternalNames[addonIndex]
		direction = self.overrideDirectionControl.GetSelection()
		if direction not in (LEFT, CENTER, RIGHT):
			direction = CENTER
		self._addonDirectionOverrides[internalName] = direction
		self.overridesListControl.Set(self._formatDirectionOverrideEntries())

	def onRemoveDirectionOverride(self, evt):
		selection = self.overridesListControl.GetSelection()
		if selection == wx.NOT_FOUND:
			return
		internalNames = list(self._addonDirectionOverrides.keys())
		if 0 <= selection < len(internalNames):
			del self._addonDirectionOverrides[internalNames[selection]]
			self.overridesListControl.Set(self._formatDirectionOverrideEntries())

	def _onPercentSpinChanged(self, evt):
		"""
		wx.SpinCtrl always reports a delta of exactly +/-1 for an
		arrow-button click or an up/down arrow key press, regardless of any
		'increment' setting (SpinCtrl, unlike SpinCtrlDouble, has none).
		To make the arrows/keys step by PERCENT_SPIN_STEP instead, a +/-1
		delta from the last known value is re-applied here as a
		PERCENT_SPIN_STEP-sized step; any other delta (i.e. a value typed
		directly) is left exactly as entered.
		"""
		spin = evt.GetEventObject()
		newValue = spin.GetValue()
		lastValue = self._spinLastValues.get(spin, newValue)
		delta = newValue - lastValue

		if delta == 1:
			adjusted = min(100, lastValue + PERCENT_SPIN_STEP)
		elif delta == -1:
			adjusted = max(0, lastValue - PERCENT_SPIN_STEP)
		else:
			adjusted = newValue

		if adjusted != newValue:
			spin.SetValue(adjusted)

		self._spinLastValues[spin] = adjusted
		evt.Skip()

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
		if hasattr(instance, '_test_running') and instance._test_running:
			ui.message(_("Test already in progress"))
			return

		# Use the CURRENT (possibly unsaved) values from this dialog's
		# controls, not whatever was last saved to disk -- otherwise
		# "Test All Settings" silently ignores anything changed since the
		# dialog was opened (e.g. a lower Volume%), which looked like the
		# volume control doing nothing at all.
		testSettings = self._collectSettingsFromControls()
		instance.start_test_sequence(testSettings)

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

	def _collectSettingsFromControls(self):
		settings = {key: control.GetSelection() for key, control in self.controls.items()}
		settings["waveformType"] = self.waveformControl.GetSelection()
		fade_index = self.fadeAlgorithmControl.GetSelection()
		if 0 <= fade_index < len(FADE_ALGORITHMS):
			settings["fadeAlgorithm"] = FADE_ALGORITHMS[fade_index]
		else:
			settings["fadeAlgorithm"] = "cosine"
		settings["volume"] = self.volumeControl.GetValue() / 100.0
		settings["beepVolume"] = self.beepVolumeControl.GetValue()
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
		settings["progressReverb"] = self.progressReverbControl.GetValue()
		settings["excludedAddons"] = [
			self._excludedAddonInternalNames[index]
			for index in range(self.excludedAddonsControl.GetCount())
			if self.excludedAddonsControl.IsChecked(index)
		]
		settings["addonDirectionOverrides"] = dict(self._addonDirectionOverrides)
		settings["autoDuckEnabled"] = self.autoDuckControl.GetValue()
		settings["autoDuckPercent"] = self.autoDuckPercentControl.GetValue()
		settings["heartbeatEnabled"] = self.heartbeatControl.GetValue()
		settings["heartbeatIntervalSeconds"] = self.heartbeatIntervalControl.GetValue()
		settings["customPresets"] = dict(self.settings.get("customPresets", {}))
		return settings

	def onSave(self):
		settings = self._collectSettingsFromControls()

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
		self._thread_lock = threading.RLock()
		self.settings = loadSettings()

		self.originalBeep = tones.beep
		self.originalWinsoundBeep = winsound.Beep if winsound is not None else None
		# Module name of this add-on's own __init__ (e.g. "globalPlugins.
		# soundAlign"), used by _getCallingAddonName to skip past
		# SoundAlign's own frames (e.g. safeBeepWinsound calling into
		# safeBeep) so an internal call is never misidentified as coming
		# from an excluded/non-excluded add-on.
		self._ownModulePrefix = GlobalPlugin.__module__

		self.sound_processor = None
		try:
			self.sound_processor = SoundProcessor(self)
			# wave_player is opened lazily on first actual use now (see
			# SoundProcessor._ensure_wave_player), so it's expected to
			# still be None right here -- is_running reflects whether the
			# processor is available for use at all.
			if not self.sound_processor.is_running:
				log.warning("SoundAlign: Audio output unavailable, progress/wave-file sounds disabled")
		except Exception as e:
			log.error(f"SoundAlign: Failed to initialize SoundProcessor: {e}")
			self.sound_processor = None

		self.last_spoken_percent = -1
		self.last_beep_percent = -1
		self.last_time_announced = 0
		self.last_progress_object = None

		# Multi-tap gesture state (single/double/triple tap of the same
		# gesture -- see script_handleSoundAlign), driven by NVDA's own
		# scriptHandler.getLastScriptRepeatCount() convention rather than a
		# bespoke timer, per house multi-tap discipline.
		self._pendingTapCount = 0
		self._tapTimer = None
		self._settings_dialog_open = False

		self._test_running = False
		self._test_sequence = []
		self._test_index = 0
		self._test_progress_values = []
		self._test_progress_idx = 0
		self._terminating = False

		# Auto-duck state (see _onSynthPreSpeak/_onSynthDoneSpeaking).
		self._baseMasterVolume = self.settings.get("masterVolume", 100) / 100.0
		self._isDucking = False
		synthDriverHandler.pre_synthSpeak.register(self._onSynthPreSpeak)
		synthDriverHandler.synthDoneSpeaking.register(self._onSynthDoneSpeaking)

		# Heartbeat state (see _heartbeatTick).
		self._last_progress_time = 0
		self._last_progress_percent = -1
		self._heartbeatAnnounced = False
		self._stuckAnnounced = False
		self._heartbeatAlive = True
		callLater(_HEARTBEAT_CHECK_INTERVAL_MS, self._heartbeatTick)

		self.setupHooks()
		self.registerSettingsPanel()
		update.start()

		callLater(100, self.applySettings)

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

			# NOTE: Python caches imported modules in sys.modules, so every
			# add-on that does 'import tones' / 'import winsound' is
			# holding a reference to the exact same module object patched
			# above -- there is no separate copy per add-on to patch.
		except Exception as e:
			log.error(f"SoundAlign: Setup hooks error: {e}")

	def _getCallingAddonName(self):
		"""
		Walks the raw call stack (frame-to-frame only -- no source-line
		lookups the way inspect.stack() does, since this runs on every
		single beep once any add-on is excluded) to find which installed
		add-on's module actually triggered this beep, so its calls can be
		passed straight through to originalBeep, untouched, per the
		per-add-on exclude list. Frames belonging to SoundAlign's own
		module are skipped so an internal call chain (e.g.
		safeBeepWinsound calling into safeBeep) is never misidentified as
		the caller. Returns None if no add-on module is found within the
		bounded walk (e.g. the call came from NVDA core itself).
		"""
		frame = sys._getframe(1)
		depth = 0
		try:
			while frame is not None and depth < _MAX_CALLER_STACK_DEPTH:
				moduleName = frame.f_globals.get("__name__", "")
				if moduleName and not moduleName.startswith(self._ownModulePrefix):
					if moduleName.startswith("globalPlugins.") or moduleName.startswith("appModules."):
						parts = moduleName.split(".")
						if len(parts) >= 2:
							return parts[1]
				frame = frame.f_back
				depth += 1
		finally:
			del frame
		return None

	def safeBeep(self, hz, length, left=50, right=50, *args, **kwargs):
		excludedAddons = self.settings.get("excludedAddons")
		addonOverrides = self.settings.get("addonDirectionOverrides")
		callerAddonName = None
		if excludedAddons or addonOverrides:
			callerAddonName = self._getCallingAddonName()
			if excludedAddons and callerAddonName and callerAddonName in excludedAddons:
				return self.originalBeep(hz, length, left, right, *args, **kwargs)

		if not self.settings.get("isActive", True):
			return self.originalBeep(hz, length, left, right, *args, **kwargs)

		soundType = self.getSoundType(hz, length)
		direction = self.getDirection(soundType, hz, callerAddonName)

		if soundType == PROGRESS_INDICATOR:
			# Decode NVDA's own progress-beep percent straight from the hz
			# it passed us, using NVDA's fixed internal pitch range (see
			# NVDA_PROGRESS_MIN_PITCH/MAX_PITCH above) -- not this add-on's
			# own configurable render range, and not a fragile
			# object-value text lookup keyed by Python object id() (which
			# could return a stale percent from an unrelated, earlier
			# progress dialog once its object's id got reused). This is
			# simple, deterministic, and always available since hz is
			# always passed to safeBeep.
			pitchSpan = NVDA_PROGRESS_MAX_PITCH - NVDA_PROGRESS_MIN_PITCH
			if pitchSpan > 0:
				percent = (hz - NVDA_PROGRESS_MIN_PITCH) / pitchSpan * 100
			else:
				percent = 50
			percent = max(0, min(100, percent))

			obj = api.getFocusObject()
			self.handleProgressAnnouncements(percent, obj)

			if self.settings.get("waveformType") == 4:
				if direction == LEFT:
					pan_pos = 0.0
				elif direction == CENTER:
					pan_pos = 0.5
				elif direction == RIGHT:
					pan_pos = 1.0
				elif direction == LEFT_TO_RIGHT:
					pan_pos = percent / 100.0
				elif direction == RIGHT_TO_LEFT:
					pan_pos = 1.0 - (percent / 100.0)
				else:
					pan_pos = 0.5

				leftVol_dynamic = (1.0 - pan_pos) * 100
				rightVol_dynamic = pan_pos * 100
				# Read master volume from sound_processor.master_volume --
				# not self.settings["masterVolume"] directly -- because
				# that attribute is what _onSynthPreSpeak/_onSynthDoneSpeaking
				# actually duck. Reading the static setting here instead was
				# a confirmed bug: this "Original Tone Beep" waveform path
				# bypassed ducking entirely regardless of autoDuckEnabled.
				if self.sound_processor:
					masterVolumeFraction = self.sound_processor.master_volume
				else:
					masterVolumeFraction = self.settings.get("masterVolume", 100) / 100.0
				master = fraction_to_gain(masterVolumeFraction)
				self.originalBeep(hz, length, left=int(leftVol_dynamic * master), right=int(rightVol_dynamic * master))
			else:
				beepInterval = self.settings.get("beepPercentageInterval", 5)
				intPercent = int(percent)
				# Rapid successive operations (e.g. converting a batch of
				# many small files, each firing its own quick 0-100%
				# sequence) previously rendered one tick per distinct
				# percent value regardless of how densely-packed those
				# values were, which is what produced the reported
				# "hundreds of overlapping beeps" during batch jobs.
				# Gating on the same interval already used for the speech
				# announcements -- while always letting 0% and 100%
				# through so the start/end of an operation is never
				# silently skipped -- cuts tick generation down to the
				# same cadence the user already configured for speech.
				shouldRenderTick = (
					intPercent <= 0
					or intPercent >= 100
					or (beepInterval > 0 and intPercent % beepInterval == 0)
				)
				if shouldRenderTick and percent != getattr(sound_context, 'last_progress_value', -1):
					try:
						if self.sound_processor:
							self.sound_processor.play_progress_sound(percent, direction)
							sound_context.last_progress_value = percent
					except Exception as e:
						log.error(f"SoundAlign: Progress sound error: {e}")
						self.originalBeep(hz, length, left=left, right=right)
		else:
			leftVol, rightVol = self.getBalance(direction)
			beepGain = fraction_to_gain(self.settings.get("beepVolume", 100) / 100.0)
			self.originalBeep(hz, length, left=int(leftVol * 100 * beepGain), right=int(rightVol * 100 * beepGain))

	def safeBeepWinsound(self, frequency, duration):
		if winsound is None or not self.settings.get("isActive", True):
			if self.originalWinsoundBeep:
				self.originalWinsoundBeep(frequency, duration)
			return

		self.safeBeep(frequency, duration, left=50, right=50)

	def handleProgressAnnouncements(self, percent, obj):
		if not isinstance(percent, (int, float)) or percent < 0 or percent > 100:
			return

		with self._thread_lock:
			current_time = time.time()
			time_interval = self.settings.get("timeBasedInterval", 0)
			speech_interval = self.settings.get("speechPercentageInterval", 10)
			beep_interval = self.settings.get("beepPercentageInterval", 5)
			mixed_mode = self.settings.get("mixedMode", False)

			# Heartbeat bookkeeping (see _heartbeatTick): any real progress
			# movement clears the "already announced" flags so a later stall
			# gets its own fresh heartbeat/stuck notice instead of staying
			# silenced by a stall from earlier in the same operation.
			if percent != self._last_progress_percent:
				self._heartbeatAnnounced = False
				self._stuckAnnounced = False
			self._last_progress_percent = percent
			self._last_progress_time = current_time

			if obj != self.last_progress_object:
				self.last_progress_object = obj
				self.last_spoken_percent = -1
				self.last_beep_percent = -1
				self.last_time_announced = 0

			int_percent = int(percent)

			if time_interval > 0:
				if current_time - self.last_time_announced >= time_interval:
					ui.message(_("{percent}% complete").format(percent=int_percent))
					self.last_time_announced = current_time
					self.last_spoken_percent = int_percent
					return

			if mixed_mode:
				if int_percent % speech_interval == 0 and int_percent != self.last_spoken_percent:
					ui.message(_("{percent}% complete").format(percent=int_percent))
					self.last_spoken_percent = int_percent
				elif int_percent % beep_interval == 0 and int_percent != self.last_beep_percent:
					self.last_beep_percent = int_percent
			else:
				if int_percent % speech_interval == 0 and int_percent != self.last_spoken_percent:
					ui.message(_("{percent}% complete").format(percent=int_percent))
					self.last_spoken_percent = int_percent

	def getSoundType(self, hz, length):
		if (110 <= hz <= 2000 and 15 <= length <= 60) or (hz == 2000 and length == 150):
			return PROGRESS_INDICATOR

		return ADDON_BEEP

	def getDirection(self, soundType, hz=None, callerAddonName=None):
		if soundType == ADDON_BEEP:
			overrides = self.settings.get("addonDirectionOverrides")
			if overrides and callerAddonName and callerAddonName in overrides:
				return overrides[callerAddonName]
			if hz is not None and hz < ADDON_BEEP_LOW_MAX_FREQ:
				direction = self.settings.get("addonBeepDirectionA", LEFT)
			elif hz is not None and hz >= ADDON_BEEP_HIGH_MIN_FREQ:
				direction = self.settings.get("addonBeepDirectionB", RIGHT)
			else:
				direction = self.settings.get("addonBeepDirectionC", CENTER)
		else:
			# The only other soundType in play is PROGRESS_INDICATOR.
			direction = self.settings.get("progressDirection", LEFT_TO_RIGHT)

		return direction

	def getBalance(self, direction):
		if direction == LEFT:
			return (1.0, 0.0)
		elif direction == RIGHT:
			return (0.0, 1.0)
		else:
			return (0.5, 0.5)

	def _applySoundProcessorSettings(self, settingsDict):
		"""
		Pushes every sound_processor-relevant value from settingsDict onto
		the actual SoundProcessor instance. Shared by applySettings() (the
		real, saved config) and the test flow (a live, possibly-unsaved
		settings dict from the currently open dialog) so both paths stay
		in sync and can't silently diverge -- an earlier bug had the test
		path only ever push master_volume, so changing the Volume%
		control had no audible effect when testing.
		"""
		if not self.sound_processor:
			return
		waveform_type = settingsDict.get("waveformType", 0)
		if waveform_type in WAVEFORM_MAP:
			self.sound_processor.harmonics = WAVEFORM_MAP[waveform_type]
		else:
			self.sound_processor.harmonics = TONE_SINE
		self.sound_processor.fade_algorithm = settingsDict.get("fadeAlgorithm", "cosine")
		self.sound_processor.volume = settingsDict.get("volume", 0.5)
		self.sound_processor.master_volume = settingsDict.get("masterVolume", 100) / 100.0
		self.sound_processor.min_frequency = settingsDict.get("minFrequency", 110)
		self.sound_processor.max_frequency = settingsDict.get("maxFrequency", 1760)
		self.sound_processor.reverb_enabled = settingsDict.get("progressReverb", False)

	def applySettings(self):
		try:
			self.settings = loadSettings()
			self._applySoundProcessorSettings(self.settings)
			self._baseMasterVolume = self.settings.get("masterVolume", 100) / 100.0
			if self._isDucking and self.sound_processor:
				duckPercent = self.settings.get("autoDuckPercent", 50)
				self.sound_processor.master_volume = self._baseMasterVolume * (1 - duckPercent / 100.0)
		except Exception as e:
			log.error(f"SoundAlign: Apply settings error: {e}")

	def _onSynthPreSpeak(self, speechSequence=None):
		if not self.settings.get("autoDuckEnabled", True) or not self.sound_processor:
			return
		try:
			duckPercent = self.settings.get("autoDuckPercent", 50)
			self.sound_processor.master_volume = self._baseMasterVolume * (1 - duckPercent / 100.0)
			self._isDucking = True
		except Exception as e:
			log.error(f"SoundAlign: Auto-duck start error: {e}")

	def _onSynthDoneSpeaking(self, synth=None):
		if not self._isDucking or not self.sound_processor:
			self._isDucking = False
			return
		try:
			self.sound_processor.master_volume = self._baseMasterVolume
		except Exception as e:
			log.error(f"SoundAlign: Auto-duck restore error: {e}")
		finally:
			self._isDucking = False

	def _heartbeatTick(self):
		if self._terminating or not self._heartbeatAlive:
			return
		try:
			if (
				self.settings.get("heartbeatEnabled", False)
				and self._last_progress_time > 0
				and 0 <= self._last_progress_percent < 100
			):
				elapsed = time.time() - self._last_progress_time
				heartbeatSeconds = self.settings.get("heartbeatIntervalSeconds", 8)
				stuckSeconds = self.settings.get("heartbeatStuckThresholdSeconds", 20)
				intPercent = int(self._last_progress_percent)
				if elapsed >= stuckSeconds and not self._stuckAnnounced:
					ui.message(_("Operation may be stuck at {percent}%").format(percent=intPercent))
					self._stuckAnnounced = True
				elif elapsed >= heartbeatSeconds and not self._heartbeatAnnounced:
					ui.message(_("Still working, {percent}%").format(percent=intPercent))
					self._heartbeatAnnounced = True
		except Exception as e:
			log.error(f"SoundAlign: Heartbeat check error: {e}")
		finally:
			if not self._terminating and self._heartbeatAlive:
				callLater(_HEARTBEAT_CHECK_INTERVAL_MS, self._heartbeatTick)

	def speakStatusSummary(self):
		"""
		"SoundAlign Doctor" short version -- a one-shot spoken summary of
		SoundAlign's current state, triggered by a triple-tap of the
		SoundAlign gesture (see script_handleSoundAlign).
		"""
		try:
			isActive = self.settings.get("isActive", True)
			excludedCount = len(self.settings.get("excludedAddons", []))
			overrideCount = len(self.settings.get("addonDirectionOverrides", {}))
			audioReady = bool(self.sound_processor and self.sound_processor.is_running)

			parts = [
				_("SoundAlign is {state}").format(state=_("on") if isActive else _("off")),
				_("{count} add-ons excluded").format(count=excludedCount),
				_("{count} direction overrides").format(count=overrideCount),
				_("audio output ready") if audioReady else _("audio output unavailable"),
			]
			summary = ", ".join(parts)
			# Output-safety cap (Section 5.11): this is built entirely from
			# small counts and fixed phrases so it should never be long,
			# but the cap is kept anyway as a hard guarantee.
			maxLength = 250
			if len(summary) > maxLength:
				summary = summary[:maxLength] + _("...truncated")
			ui.message(summary)
		except Exception as e:
			log.error(f"SoundAlign: Status summary error: {e}")
			ui.message(_("SoundAlign: could not read status"))

	def start_test_sequence(self, testSettings=None):
		if self._test_running:
			return
		self._test_running = True

		# While testSettings is provided (from the settings dialog), use it
		# in place of the real saved settings for the whole test sequence,
		# so Test All Settings reflects whatever is currently in the
		# dialog -- including anything not yet saved -- rather than
		# whatever was last written to disk. The real settings are
		# restored once the test finishes (see _finishTestSequence).
		if testSettings is not None:
			self._test_original_settings = self.settings
			self.settings = testSettings
		else:
			self._test_original_settings = None

		self._applySoundProcessorSettings(self.settings)

		self._test_sequence = [
			("addon_beep_low", 300, 500, self.settings.get("addonBeepDirectionA", LEFT)),
			("addon_beep_medium", 900, 500, self.settings.get("addonBeepDirectionC", CENTER)),
			("addon_beep_high", 1800, 500, self.settings.get("addonBeepDirectionB", RIGHT)),
		]
		self._test_index = 0
		self._run_next_test_beep()

	def _finishTestSequence(self):
		self._test_running = False
		if getattr(self, "_test_original_settings", None) is not None:
			self.settings = self._test_original_settings
			self._test_original_settings = None
			self._applySoundProcessorSettings(self.settings)

	def _run_next_test_beep(self):
		if self._terminating:
			return
		if self._test_index >= len(self._test_sequence):
			progress_direction = self.settings.get("progressDirection", LEFT_TO_RIGHT)
			waveform_type = self.settings.get("waveformType", 0)
			self._run_progress_test(progress_direction, waveform_type)
			return

		_, freq, duration, direction = self._test_sequence[self._test_index]
		leftVol, rightVol = self.getBalance(direction)
		beepGain = fraction_to_gain(self.settings.get("beepVolume", 100) / 100.0)
		self.originalBeep(freq, duration, left=int(leftVol*100*beepGain), right=int(rightVol*100*beepGain))
		self._test_index += 1
		callLater(600, self._run_next_test_beep)

	def _run_progress_test(self, direction, waveform_type):
		if waveform_type == 4:
			self._test_progress_values = list(range(0, 101, 2))
			self._test_progress_idx = 0
			self._test_progress_direction = direction
			self._run_next_progress_beep()
		else:
			if not self.sound_processor or not self.sound_processor.is_running:
				log.error("SoundAlign: Cannot test progress, audio output not available")
				ui.message(_("Progress test not available. Audio output could not be initialized."))
				self._finishTestSequence()
				return

			self._test_progress_values = list(range(0, 101, 2))
			self._test_progress_idx = 0
			self._test_progress_direction = direction
			self._run_next_progress_sound()

	def _run_next_progress_beep(self):
		if self._terminating:
			return
		if self._test_progress_idx >= len(self._test_progress_values):
			self._finishTestSequence()
			return
		percent = self._test_progress_values[self._test_progress_idx]
		hz = self.settings.get("minFrequency") + (percent / 100.0) * (self.settings.get("maxFrequency") - self.settings.get("minFrequency"))

		direction = self._test_progress_direction
		if direction == LEFT:
			pan_pos = 0.0
		elif direction == CENTER:
			pan_pos = 0.5
		elif direction == RIGHT:
			pan_pos = 1.0
		elif direction == LEFT_TO_RIGHT:
			pan_pos = percent / 100.0
		elif direction == RIGHT_TO_LEFT:
			pan_pos = 1.0 - (percent / 100.0)
		else:
			pan_pos = 0.5

		leftVol_dynamic = (1.0 - pan_pos) * 100
		rightVol_dynamic = pan_pos * 100
		master = fraction_to_gain(self.settings.get("masterVolume", 100) / 100.0)
		self.originalBeep(int(hz), 40, left=int(leftVol_dynamic * master), right=int(rightVol_dynamic * master))
		self._test_progress_idx += 1
		callLater(20, self._run_next_progress_beep)

	def _run_next_progress_sound(self):
		if self._terminating:
			return
		if self._test_progress_idx >= len(self._test_progress_values):
			self._finishTestSequence()
			return
		percent = self._test_progress_values[self._test_progress_idx]
		self.sound_processor.play_progress_sound(percent, direction=self._test_progress_direction)
		self._test_progress_idx += 1
		callLater(20, self._run_next_progress_sound)

	def testBeep(self, freq, duration, direction, soundType):
		leftVol, rightVol = self.getBalance(direction)
		if soundType == PROGRESS_INDICATOR:
			gain = fraction_to_gain(self.settings.get("masterVolume", 100) / 100.0)
		else:
			gain = fraction_to_gain(self.settings.get("beepVolume", 100) / 100.0)
		self.originalBeep(freq, duration, left=int(leftVol*100*gain), right=int(rightVol*100*gain))

	def testProgress(self, direction, waveform_type):
		self._run_progress_test(direction, waveform_type)

	@script(
		description=_("Open SoundAlign settings (single tap), toggle SoundAlign on/off (double tap), or speak a status summary (triple tap)"),
		gesture="kb:NVDA+Windows+S"
	)
	def script_handleSoundAlign(self, gesture):
		# The tap count comes from NVDA's own repeat counter. The action is
		# deferred until no further tap can join the burst, so single, double
		# and triple tap stay three exclusive actions. The wait restarts on
		# every tap and is measured from the latest one, matching how NVDA
		# itself decides whether a press is a repeat.
		self._pendingTapCount = scriptHandler.getLastScriptRepeatCount()
		self._stopTapTimer()
		self._tapTimer = callLater(self._getTapResolveDelayMs(), self._resolveMultiTap)

	def _getTapResolveDelayMs(self):
		timeoutMs = 500
		try:
			timeoutMs = int(config.conf["keyboard"]["multiPressTimeout"])
		except (KeyError, TypeError, ValueError):
			log.debugWarning("SoundAlign: multiPressTimeout unreadable, using 500 ms")
		return timeoutMs + 50

	def _stopTapTimer(self):
		timer = self._tapTimer
		self._tapTimer = None
		if timer is None:
			return
		try:
			timer.Stop()
		except RuntimeError:
			log.debugWarning("SoundAlign: tap timer already gone", exc_info=True)

	def _resolveMultiTap(self):
		self._tapTimer = None
		if self._terminating:
			return
		finalCount = self._pendingTapCount
		self._pendingTapCount = 0
		if finalCount == 0:
			if not self._settings_dialog_open:
				self._settings_dialog_open = True
				self._showSettingsDialog()
		elif finalCount == 1:
			self.settings['isActive'] = not self.settings.get('isActive', True)
			saveSettings(self.settings)
			self.applySettings()
			if self.settings['isActive']:
				ui.message(_("SoundAlign on"))
			else:
				ui.message(_("SoundAlign off"))
		else:
			self.speakStatusSummary()

	def _showSettingsDialog(self):
		if self._settings_dialog_open is False:
			return
		try:
			gui.mainFrame.popupSettingsDialog(
				gui.settingsDialogs.NVDASettingsDialog,
				SoundAlignSettingsPanel
			)
		except Exception as e:
			log.error(f"SoundAlign: Failed to show settings dialog: {e}")
		finally:
			callLater(500, self._resetSettingsDialogFlag)

	def _resetSettingsDialogFlag(self):
		self._settings_dialog_open = False

	def terminate(self):
		if self._terminating:
			return
		self._terminating = True
		self._heartbeatAlive = False
		self._stopTapTimer()
		update.stop()

		try:
			synthDriverHandler.pre_synthSpeak.unregister(self._onSynthPreSpeak)
			synthDriverHandler.synthDoneSpeaking.unregister(self._onSynthDoneSpeaking)
		except Exception as e:
			log.debug(f"SoundAlign: Unregister synth extension points error: {e}")

		try:
			if tones.beep == self.safeBeep:
				tones.beep = self.originalBeep
			else:
				log.warning("SoundAlign: tones.beep was wrapped by another add-on, leaving it in place")
		except Exception as e:
			log.debug(f"SoundAlign: Restore tones.beep error: {e}")

		try:
			if winsound is not None and self.originalWinsoundBeep:
				if winsound.Beep == self.safeBeepWinsound:
					winsound.Beep = self.originalWinsoundBeep
				else:
					log.warning("SoundAlign: winsound.Beep was wrapped by another add-on, leaving it in place")
		except Exception as e:
			log.debug(f"SoundAlign: Restore winsound.Beep error: {e}")

		if self.sound_processor:
			try:
				self.sound_processor.stop()
			except Exception as e:
				log.error(f"SoundAlign: SoundProcessor stop error: {e}")
			self.sound_processor = None

		try:
			if SoundAlignSettingsPanel in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
				gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(SoundAlignSettingsPanel)
		except Exception as e:
			log.debug(f"SoundAlign: Deregister settings panel error: {e}")

		GlobalPlugin.instance = None






