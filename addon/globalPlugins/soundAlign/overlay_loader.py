# overlay_loader.py
# Dual-architecture binary loader for NVDA add-ons
# Cleans up unused architecture folders after deployment.
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

def _log(msg):
    import builtins
    builtins.print(f"[overlay_loader] {msg}")

def overlayBinaries():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tools_dir = os.path.join(base_dir, "tools")
    arch = _get_architecture_subdir()
    src_arch_dir = os.path.join(tools_dir, arch)
    src_pkg_dir = os.path.join(src_arch_dir, "pyaudiowpatch")
    dst_pkg_dir = os.path.join(tools_dir, "pyaudiowpatch")

    _log(f"Architecture: {arch}")
    _log(f"Base dir: {base_dir}")

    # --- 1. ลบ pyaudiowpatch เก่าที่ root (ถ้ามี) ---
    old_root_pkg = os.path.join(base_dir, "pyaudiowpatch")
    if os.path.exists(old_root_pkg):
        _log(f"Removing old root package: {old_root_pkg}")
        shutil.rmtree(old_root_pkg, ignore_errors=True)

    # --- 2. คัดลอก pyaudiowpatch จากสถาปัตยกรรมที่ถูกต้องไปยัง tools/pyaudiowpatch ---
    if not os.path.isdir(src_pkg_dir):
        _log(f"WARNING: Source package not found: {src_pkg_dir}. Skipping copy.")
    else:
        # ลบ tools/pyaudiowpatch เดิม (ถ้ามี) เพื่อให้ได้เวอร์ชันใหม่เสมอ
        if os.path.exists(dst_pkg_dir):
            _log(f"Removing old package: {dst_pkg_dir}")
            shutil.rmtree(dst_pkg_dir, ignore_errors=True)

        shutil.copytree(src_pkg_dir, dst_pkg_dir)
        _log(f"Copied {src_pkg_dir} -> {dst_pkg_dir}")

        # --- 3. ลบโฟลเดอร์ x86 และ x64 ทิ้ง ---
        for arch_folder in ["x86", "x64"]:
            arch_path = os.path.join(tools_dir, arch_folder)
            if os.path.exists(arch_path):
                _log(f"Removing {arch_path}")
                shutil.rmtree(arch_path, ignore_errors=True)

    # --- 4. เพิ่ม tools directory ใน sys.path และ DLL search path ---
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
        _log(f"Added {tools_dir} to sys.path")

    _add_dll_directory(tools_dir)
    if os.path.isdir(dst_pkg_dir):
        _add_dll_directory(dst_pkg_dir)  # เผื่อมี .dll ใน package

    _log("overlayBinaries completed.")

# เรียกใช้ทันที
overlayBinaries()