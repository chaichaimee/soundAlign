# installTasks.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import os
import sys
import shutil
import logHandler


def onInstall():
	"""
	NVDA 2025.x still runs on 32-bit (x86) Python, while NVDA 2026.x runs on
	64-bit (x64) Python 3.13. Both architectures of the bundled
	pyaudiowpatch package are shipped under:
		globalPlugins\\soundAlign\\tools\\x86
		globalPlugins\\soundAlign\\tools\\x64
	i.e. inside the plugin's own package folder, NOT directly under the
	add-on root (where this installTasks.py itself lives, next to
	manifest.ini). This removes whichever architecture does NOT match the
	NVDA process currently performing the install, so only the
	architecture actually needed remains on disk.

	This replaces the previous 'overlay_loader.py' approach, which copied
	both architectures into NVDA's install directory at runtime and did
	not reliably work.
	"""
	addonDir = os.path.dirname(os.path.abspath(__file__))
	toolsDir = os.path.join(addonDir, "globalPlugins", "soundAlign", "tools")
	if not os.path.isdir(toolsDir):
		logHandler.log.debug(
			"soundAlign tools directory not found at %s; nothing to clean up." % toolsDir
		)
		return

	targetArch = "x64" if sys.maxsize > 2 ** 32 else "x86"
	unusedArch = "x86" if targetArch == "x64" else "x64"

	unusedArchPath = os.path.join(toolsDir, unusedArch)
	if os.path.isdir(unusedArchPath):
		try:
			shutil.rmtree(unusedArchPath)
		except (OSError, PermissionError):
			# The add-on still works with the matching architecture present;
			# log so the leftover folder can be investigated rather than
			# failing installation or silently leaving dead weight behind.
			logHandler.log.exception(
				"Failed to remove unused tools/%s folder during install." % unusedArch
			)
	else:
		logHandler.log.debug(
			"tools/%s already absent; nothing to clean up for this install." % unusedArch
		)

	# Best-effort cleanup of the old runtime-copy loader mechanism, in case
	# it is still present from a previous version of this add-on and left
	# stray files behind under the target architecture's tools folder.
	legacyLoaderPath = os.path.join(addonDir, "globalPlugins", "soundAlign", "overlay_loader.py")
	if os.path.isfile(legacyLoaderPath):
		try:
			os.remove(legacyLoaderPath)
		except (OSError, PermissionError):
			logHandler.log.exception(
				"Failed to remove legacy overlay_loader.py during install."
			)
