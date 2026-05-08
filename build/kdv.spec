# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Krystal Data Vision (Windows + macOS).

Windows build:
    pip install -e ".[dev]"
    pyinstaller build/kdv.spec --clean --noconfirm
    -> dist/kdv/kdv.exe   (double-click; no terminal)

macOS build:
    pip install -e ".[dev]"
    pyinstaller build/kdv.spec --clean --noconfirm
    -> dist/kdv.app       (double-click)
    -> dist/kdv/          (raw onefolder, can ignore)

The same spec auto-detects the host OS and produces the appropriate bundle.
PyInstaller cannot cross-compile — run on Windows for .exe, on Mac for .app.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

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

datas += collect_data_files("matplotlib")
datas += collect_data_files("openpyxl")
datas += collect_data_files("pyqtgraph")
try:
    datas += collect_data_files("qtawesome")
except Exception:
    pass

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

# --- icon resolution ----------------------------------------------------
icon_arg = None
if IS_WIN:
    p = ASSETS / "icons" / "kdv.ico"
    icon_arg = str(p) if p.exists() else None
elif IS_MAC:
    p = ASSETS / "icons" / "kdv.icns"
    icon_arg = str(p) if p.exists() else None

# --- exe + collect ------------------------------------------------------
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
    console=False,           # GUI only — no terminal window
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

# --- macOS .app bundle --------------------------------------------------
if IS_MAC:
    app = BUNDLE(
        coll,
        name="kdv.app",
        icon=icon_arg,
        bundle_identifier="com.krystaldatavision.kdv",
        info_plist={
            "CFBundleName": "Krystal Data Vision",
            "CFBundleDisplayName": "Krystal Data Vision",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            # Don't show in Dock as a separate "python" — this is a real app.
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSRequiresAquaSystemAppearance": False,  # follow system theme
            # Excel file association (optional, lets users drag-drop xlsx onto the app)
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Excel Workbook",
                    "CFBundleTypeRole": "Viewer",
                    "LSItemContentTypes": [
                        "org.openxmlformats.spreadsheetml.sheet",
                        "com.microsoft.excel.xls",
                    ],
                }
            ],
        },
    )
