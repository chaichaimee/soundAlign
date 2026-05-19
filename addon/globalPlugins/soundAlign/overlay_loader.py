# overlay_loader.py
# Dual-architecture binary loader for NVDA add-ons
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License.

import os
import sys
import shutil

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

def _log_error(msg):
	try:
		from logHandler import log
		log.error(f"[overlay_loader] {msg}")
	except ImportError:
		import builtins
		builtins.print(f"[overlay_loader] ERROR: {msg}")

def _log_warning(msg):
	try:
		from logHandler import log
		log.warning(f"[overlay_loader] {msg}")
	except ImportError:
		import builtins
		builtins.print(f"[overlay_loader] WARNING: {msg}")

def _remove_module_from_cache(module_name):
	try:
		if module_name in sys.modules:
			del sys.modules[module_name]
	except Exception as e:
		_log_warning(f"Failed to remove {module_name} from cache: {e}")

def overlayBinaries():
	base_dir = os.path.dirname(os.path.abspath(__file__))
	tools_dir = os.path.join(base_dir, "tools")
	
	if not os.path.isdir(tools_dir):
		_log_warning(f"Tools directory not found at {tools_dir}, skipping binary load")
		return
	
	arch = _get_architecture_subdir()
	src_arch_dir = os.path.join(tools_dir, arch)
	src_pkg_dir = os.path.join(src_arch_dir, "pyaudiowpatch")
	dst_pkg_dir = os.path.join(tools_dir, "pyaudiowpatch")
	
	if not os.path.isdir(src_pkg_dir):
		_log_warning(f"Binary package not found for {arch} at {src_pkg_dir}")
		if os.path.exists(dst_pkg_dir):
			shutil.rmtree(dst_pkg_dir, ignore_errors=True)
		return
	
	if os.path.exists(dst_pkg_dir):
		shutil.rmtree(dst_pkg_dir, ignore_errors=True)
	
	shutil.copytree(src_pkg_dir, dst_pkg_dir)
	
	if tools_dir not in sys.path:
		sys.path.insert(0, tools_dir)
	
	if os.path.isdir(dst_pkg_dir):
		if dst_pkg_dir not in sys.path:
			sys.path.insert(0, dst_pkg_dir)
		_add_dll_directory(dst_pkg_dir)
		
		for module_name in ['pyaudiowpatch', '_portaudiowpatch']:
			_remove_module_from_cache(module_name)
		
		try:
			import pyaudiowpatch
			_log_warning(f"Successfully loaded pyaudiowpatch for {arch}")
		except ImportError as e:
			_log_warning(f"pyaudiowpatch import failed: {e}")
		except Exception as e:
			_log_warning(f"Unexpected error during import: {e}")

overlayBinaries()