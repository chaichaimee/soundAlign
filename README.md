![NVDA Logo](https://www.nvaccess.org/files/nvda/documentation/userGuide/images/nvda.ico)

# soundAlign

Hear where every sound comes from. Place NVDA add-on beeps and progress indicators exactly where you want them in the stereo field.

**author:** chai chaimee

**url:** https://github.com/chaichaimee/soundAlign

## Introduction

soundAlign gives you full control over the direction and character of the beeps NVDA and its add-ons produce. Instead of every beep sounding the same in the centre, you decide whether each one plays from the left, the centre or the right speaker.

It also replaces the standard progress bar beep with a smooth, customizable progress tone that can sweep from left to right (or right to left) as a task moves from 0% to 100%, so you can follow a long operation by ear. Spoken percentage announcements, volume ducking while NVDA speaks, a "still working" heartbeat, per-add-on rules and ready-made sound presets make it easy to tune everything to your own taste.

## Hot Keys

**NVDA+Windows+S**

- Single Tap : Open soundAlign settings
- Double Tap : Toggle soundAlign on and off
- Triple Tap : Speak a short status summary

## Features

### 1. Directional Add-on Beeps

Beeps made by NVDA add-ons are sorted into three pitch bands. Each band has its own direction setting, which can be Left, Center or Right.

- **Low Frequency** : beeps below 500 Hz. Default direction is Left.
- **Medium Frequency** : beeps from 500 Hz up to just below 1500 Hz. Default direction is Center.
- **High Frequency** : beeps of 1500 Hz and above. Default direction is Right.

Example: with the defaults, a low warning beep from one add-on is heard on the left, while a high confirmation beep from another is heard on the right.

### 2. Progress Indicator

NVDA's progress bar beeps are re-rendered as a smooth progress tone. Choose how it is placed with the **Progress indicator** setting:

- **Left**, **Center** or **Right** : the tone stays at one fixed position.
- **Left to Right** : the tone starts at the left and moves to the right as the percentage rises. This is the default.
- **Right to Left** : the tone starts at the right and moves to the left as the percentage rises.

Example: while a large file is being copied, you hear the tone rise in pitch and travel across your headphones until it reaches the right ear at 100%.

### 3. Progress Announcements

Progress can also be reported in speech as "N% complete". You control how often this happens:

- **Speech announcement interval** : 1%, 2%, 5% or 10%. Default is 10%.
- **Beep announcement interval** : 1%, 2%, 5% or 10%. Default is 5%. Progress tones are only played at this interval, plus always at 0% and 100%, which keeps quick batch jobs from producing a flood of overlapping beeps.
- **Time-based announcement** : announces the percentage every set number of seconds, from 1 to 60. Set it to 0 to disable it.
- **Mixed mode (speech + beep)** : combines spoken percentage with the progress beep.

### 4. Progress Tone Customization

Shape the progress tone to your liking:

- **Progress tone** : Sine, Triangle, Sawtooth, Square or Original Tone Beep. Original Tone Beep keeps NVDA's own beep and simply pans it.
- **Fade algorithm** : Cosine, Gaussian, Linear, Exponential, Logarithmic, S-Curve, Sine, Quarter Sine, Half Sine, Square Root, Cubic Root or Quadratic.
- **Progress volume (%)** : volume of the progress tone, 0 to 100.
- **Addon beep volume (%)** : volume of add-on beeps, 0 to 100. It is independent from the progress volume.
- **Master volume (%)** : a slider, 0 to 100, applied to the progress tone.
- **Minimum frequency** : from 110 Hz to 300 Hz in steps of 10 Hz.
- **Maximum frequency** : from 1200 Hz to 1760 Hz in steps of 10 Hz.
- **Add reverb to progress tones** : adds a soft reverb tail. It is off by default because it uses more processing power.

The minimum frequency must always be lower than the maximum frequency, otherwise soundAlign warns you and will not save the settings. The up and down arrows on the percentage fields change the value in steps of 5.

### 5. Sound Presets

Presets fill in the tone, fade, volume, master volume, frequency range and reverb settings in one move, so you do not have to understand every option. The built-in choices are:

- **Custom (keep my current settings)** : leaves everything as it is. This is selected whenever the panel opens.
- **Balanced (recommended)** : a plain sine sweep.
- **Subtle / quiet** : a softer, narrower sweep.
- **Prominent / clear** : a louder, wider sweep with reverb.

You can also keep your own presets. To save one:

1. Adjust the progress tone settings until they sound right.
2. Type a name in **New preset name**.
3. Activate **Save current settings as preset**.
4. If the name already exists, confirm that you want to overwrite it. Names used by the built-in presets cannot be reused.
5. soundAlign announces that the preset was saved, and it appears in the **Progress sound preset** list from then on.

### 6. Exclude Add-ons

Some add-ons are better left alone. The **Exclude these add-ons from SoundAlign** list shows every installed add-on with a check box. Beeps from a checked add-on bypass soundAlign completely and play exactly as that add-on intended. Each time you toggle an entry, NVDA announces it as checked or not checked.

### 7. Direction Override per Add-on

Instead of letting pitch decide the direction, you can pin all beeps of a specific add-on to one side. To set this up:

1. Open the soundAlign settings with NVDA+Windows+S.
2. In **Add-on for direction override**, choose the add-on.
3. In **Override direction**, choose Left, Center or Right.
4. Activate **Add/update override**. The entry appears in **Current direction overrides**.
5. To remove a rule, select it in the list and activate **Remove selected override**.
6. Press OK to save your changes.

Example: set a chat add-on to Right and every beep it makes is heard from the right, whatever its pitch.

### 8. Auto-Duck While NVDA Speaks

When enabled, the progress tone is lowered while NVDA is speaking and restored as soon as speech finishes, so the tone never competes with Say All or dictation. Use **Duck progress tone volume while NVDA is speaking** to turn it on or off, and **Duck amount** to choose how many percent lower the tone becomes. It is on by default at 50%.

### 9. Heartbeat for Stalled Progress

For long operations, the heartbeat tells you the task is still alive. Enable **Announce a heartbeat when progress stalls** and set **Heartbeat interval** from 3 to 60 seconds. It is off by default.

1. While progress sits at the same percentage, soundAlign says "Still working" followed by the current percentage once the interval has passed.
2. If the percentage still does not move for a longer period, it says "Operation may be stuck" followed by the current percentage.
3. As soon as the percentage changes, the heartbeat is ready to announce again for the next stall.

### 10. Test All Settings

Activate **Test All Settings** to hear your configuration before saving it. The test always uses the values currently shown in the panel, even if they are not saved yet.

1. A low, a medium and a high frequency add-on beep play in turn, each from its configured direction.
2. A full progress sweep from 0% to 100% then plays with your chosen progress direction, tone, fade, volumes and frequency range.
3. When it ends, your real saved settings are restored.

If a test is already running, NVDA reports "Test already in progress".

### 11. Toggle On and Off

A double tap on NVDA+Windows+S switches soundAlign between on and off and announces "SoundAlign on" or "SoundAlign off". While it is off, every beep plays exactly as NVDA normally produces it. The choice is saved.

### 12. Status Summary

A triple tap on NVDA+Windows+S speaks one short sentence that tells you whether soundAlign is on or off, how many add-ons are excluded, how many direction overrides exist, and whether audio output is ready.

### 13. Settings Panel

All options live in a **SoundAlign** category of NVDA's Settings dialog. A single tap on NVDA+Windows+S opens the Settings dialog directly on this category. Your settings are stored in the NVDA configuration folder under ChaiChaimee\\soundAlign, and settings from earlier layouts are moved there automatically.

### 14. Automatic Update Check

Once a day, shortly after NVDA starts, soundAlign checks GitHub for a newer release. If one exists, a dialog offers **Update now** or **Later**. Choosing Update now downloads the release and hands it to NVDA's own add-on installer, which asks for confirmation and a restart. If the download fails, the releases page opens in your browser instead. The check is skipped when the add-on was installed from the Add-on Store.

## Support Me

If this tool has made your life easier, consider fueling the next update with a small donation.

[![Support me](https://img.shields.io/badge/Donate-Support%20Me-blue?style=for-the-badge&logo=stripe)](https://buy.stripe.com/dRm9AU1xQ3Ds22N6VK1VK01)

Your support means the world. Let's build something great together

© 2026 Chai Chaimee NVDA Add-on Released under GNU GPL
