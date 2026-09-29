# -*- mode: python ; coding: utf-8 -*-
"""Two executables: a windowed GUI and a console worker for live output."""

import os
import sys
from PyInstaller.utils.hooks import collect_data_files

try:
    import tkinter  # noqa: F401 - fail instead of shipping a GUI without Tk
except ImportError as exc:
    raise SystemExit(f"Tk is required to build the GUI: {exc}") from exc

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
name = "FindPhotoDates"
helper_name = "FindPhotoDatesWorker"
icon = os.path.join(SPECPATH, "findphotodates.icns" if sys.platform == "darwin" else "findphotodates.ico")
icon = icon if os.path.isfile(icon) else None

gui = Analysis(
    [os.path.join(ROOT, "findphotodates_gui.py")],
    pathex=[ROOT],
    binaries=[],
    datas=collect_data_files("customtkinter"),
    hiddenimports=["findphotodates", "check_photo_backups"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
worker = Analysis(
    [os.path.join(ROOT, "findphotodates_gui_worker.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=["check_photo_backups"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

gui_pyz = PYZ(gui.pure)
worker_pyz = PYZ(worker.pure)
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True,
              name=name, console=False, icon=icon, debug=False, strip=False, upx=False)
worker_exe = EXE(worker_pyz, worker.scripts, [], exclude_binaries=True,
                 name=helper_name, console=True, icon=icon, debug=False, strip=False, upx=False)
collection = COLLECT(gui_exe, worker_exe, gui.binaries, gui.datas,
                     worker.binaries, worker.datas,
                     name=name, strip=False, upx=False)
if sys.platform == "darwin":
    app = BUNDLE(collection, name=name + ".app", icon=icon,
                 bundle_identifier="com.alanrockefeller.findphotodates")
