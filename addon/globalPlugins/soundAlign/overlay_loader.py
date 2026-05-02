# overlay_loader.py
# Dual-architecture binary loader for NVDA add-ons
# Cleans up unused architecture folders after deployment.
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License.

import os
import sys
import shutil
import importlib

def _is_64bit_process():
	return sys.maxsize > 2**32

def _get_architecture_subdir():
	return "x64" if _is_64bit_process() else "x86"

def _add_dll_directory(path):
	if hasattr(os, 'add_dll_directory'):
		try:
			os.add_dll_directory(path)
		except (OSError, FileNotFoundError):
			pass

def _log(msg):
	import builtins
	builtins.print(f"[overlay_loader] {msg}")

def _remove_module_from_cache(module_name):
	try:
		if module_name in sys.modules:
			del sys.modules[module_name]
			_log(f"Removed {module_name} from module cache")
	except Exception as e:
		_log(f"Failed to remove {module_name} from cache: {e}")

def overlayBinaries():
	base_dir = os.path.dirname(os.path.abspath(__file__))
	tools_dir = os.path.join(base_dir, "tools")
	arch = _get_architecture_subdir()
	src_arch_dir = os.path.join(tools_dir, arch)
	src_pkg_dir = os.path.join(src_arch_dir, "pyaudiowpatch")
	dst_pkg_dir = os.path.join(tools_dir, "pyaudiowpatch")

	_log(f"Architecture: {arch}")
	_log(f"Base dir: {base_dir}")

	# --- 1. Remove old pyaudiowpatch at root (if exists) ---
	old_root_pkg = os.path.join(base_dir, "pyaudiowpatch")
	if os.path.exists(old_root_pkg):
		_log(f"Removing old root package: {old_root_pkg}")
		shutil.rmtree(old_root_pkg, ignore_errors=True)

	# --- 2. Copy pyaudiowpatch from the correct architecture to tools/pyaudiowpatch ---
	if not os.path.isdir(src_pkg_dir):
		_log(f"WARNING: Source package not found: {src_pkg_dir}. Skipping copy.")
	else:
		# Remove old tools/pyaudiowpatch (if exists) to always get the new version
		if os.path.exists(dst_pkg_dir):
			_log(f"Removing old package: {dst_pkg_dir}")
			shutil.rmtree(dst_pkg_dir, ignore_errors=True)

		shutil.copytree(src_pkg_dir, dst_pkg_dir)
		_log(f"Copied {src_pkg_dir} -> {dst_pkg_dir}")

		# --- 3. Delete x86 and x64 folders ---
		for arch_folder in ["x86", "x64"]:
			arch_path = os.path.join(tools_dir, arch_folder)
			if os.path.exists(arch_path):
				_log(f"Removing {arch_path}")
				shutil.rmtree(arch_path, ignore_errors=True)

	# --- 4. Add tools directory and pyaudiowpatch package to sys.path ---
	if tools_dir not in sys.path:
		sys.path.insert(0, tools_dir)
		_log(f"Added {tools_dir} to sys.path")

	if os.path.isdir(dst_pkg_dir):
		if dst_pkg_dir not in sys.path:
			sys.path.insert(0, dst_pkg_dir)
			_log(f"Added {dst_pkg_dir} to sys.path")
		_add_dll_directory(dst_pkg_dir)

		# --- 5. Pre-clean module cache for pyaudiowpatch and its dependencies ---
		for module_name in ['pyaudiowpatch', '_portaudiowpatch']:
			_remove_module_from_cache(module_name)

		# --- 6. Pre-import pyaudiowpatch to verify it works ---
		try:
			import pyaudiowpatch
			_log("pyaudiowpatch pre-import successful")
		except ImportError as e:
			_log(f"WARNING: pyaudiowpatch pre-import failed: {e}")
		except Exception as e:
			_log(f"WARNING: Unexpected error during pyaudiowpatch pre-import: {e}")

	_log("overlayBinaries completed.")

# Called immediately
overlayBinaries()