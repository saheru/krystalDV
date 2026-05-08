# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Krystal Data Vision.

Build (on Windows):
    pip install -e ".[dev]"
    pyinstaller build/kdv.spec --clean --noconfirm

Output:
    dist/kdv/kdv.exe        ← double-click to launch (no terminal)
    dist/kdv/_internal/...  ← bundled Python + libs

Distribute the entire `dist/kdv` folder to clients (zip it). The user just
double-clicks `kdv.exe` — no Python install needed, no terminal, no server.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --- paths ---------------------------------------------------------------
SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent
SRC = PROJECT_ROOT / "src"
ASSETS = PROJECT_ROOT / "assets"
ENTRY = PROJECT_ROOT / "main.py"

block_cipher = None

# --- data files (bundled assets) ----------------------------------------
datas = []
if ASSETS.exists():
    for f in ASSETS.rglob("*"):
        if f.is_file():
            rel = f.parent.relative_to(PROJECT_ROOT)
            datas.append((str(f), str(rel)))

# matplotlib data (mpl-data) and openpyxl/styles
datas += collect_data_files("matplotlib")
datas += collect_data_files("openpyxl")
datas += collect_data_files("pyqtgraph")
datas += collect_data_files("qtawesome")

# --- hidden imports -----------------------------------------------------
hiddenimports = []
hiddenimports += collect_submodules("matplotlib.backends")
hiddenimports += [
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_agg",
    "qasync",
    "darkdetect",
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.fail",
    "keyring.backends.null",
]

# --- excludes (trim size) -----------------------------------------------
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtTest",
    "tkinter",
    "scipy",
]

# --- analysis -----------------------------------------------------------
a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC), str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Resolve icon path (used on Windows for the exe)
icon_path = ASSETS / "icons" / "kdv.ico"
icon_arg = str(icon_path) if icon_path.exists() else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kdv",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # ← no terminal window — pure GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="kdv",
)
