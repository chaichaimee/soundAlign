# overlay_loader.py
# Dual-architecture binary loader for NVDA add-ons.
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License.

import os
import sys
import shutil


def _is_64bit_process():
	"""Return ``True`` when the current Python process is 64-bit."""
	return sys.maxsize > 2**32


def _get_architecture_subdir():
	"""Return the architecture-specific tools subdirectory name."""
	return "x64" if _is_64bit_process() else "x86"


def _add_dll_directory(path):
	"""Register a directory for Windows DLL resolution when supported."""
	if hasattr(os, "add_dll_directory"):
		try:
			os.add_dll_directory(path)
		except (OSError, FileNotFoundError):
			pass


def _log(msg):
	"""Write loader diagnostics to standard output for NVDA log capture."""
	import builtins
	builtins.print(f"[overlay_loader] {msg}")


def overlayBinaries():
	"""Expose bundled binary modules for the current process architecture."""
	base_dir = os.path.dirname(os.path.abspath(__file__))
	tools_dir = os.path.join(base_dir, "tools")
	arch = _get_architecture_subdir()
	src_arch_dir = os.path.join(tools_dir, arch)
	src_pkg_dir = os.path.join(src_arch_dir, "pyaudiowpatch")
	legacy_pkg_dir = os.path.join(tools_dir, "pyaudiowpatch")

	_log(f"Architecture: {arch}")
	_log(f"Base dir: {base_dir}")

	old_root_pkg = os.path.join(base_dir, "pyaudiowpatch")
	if os.path.isdir(old_root_pkg):
		_log(f"Removing old root package: {old_root_pkg}")
		shutil.rmtree(old_root_pkg, ignore_errors=True)

	if os.path.isdir(src_pkg_dir):
		module_parent_dir = src_arch_dir
		package_dir = src_pkg_dir
		_log(f"Using architecture package: {package_dir}")
	elif os.path.isdir(legacy_pkg_dir):
		module_parent_dir = tools_dir
		package_dir = legacy_pkg_dir
		_log(f"Using legacy tools package: {package_dir}")
	else:
		_log(f"WARNING: Source package not found: {src_pkg_dir}")
		_log("overlayBinaries completed without a binary package.")
		return

	if package_dir not in sys.path:
		sys.path.insert(0, package_dir)
		_log(f"Added {package_dir} to sys.path")
	if module_parent_dir not in sys.path:
		sys.path.insert(0, module_parent_dir)
		_log(f"Added {module_parent_dir} to sys.path")

	_add_dll_directory(package_dir)
	_add_dll_directory(module_parent_dir)
	_log("overlayBinaries completed.")


# Called immediately.
overlayBinaries()
