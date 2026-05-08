"""Cross-platform application paths.

User-data dirs on Windows:
  config:  %APPDATA%/krystaldatavision
  cache:   %LOCALAPPDATA%/krystaldatavision/Cache
"""
from __future__ import annotations

from pathlib import Path

from platformdirs import PlatformDirs

_dirs = PlatformDirs(appname="krystaldatavision", appauthor=False, roaming=True)


def config_dir() -> Path:
    p = Path(_dirs.user_config_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = Path(_dirs.user_data_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = Path(_dirs.user_cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def presets_file() -> Path:
    return config_dir() / "presets.json"


def models_file() -> Path:
    return config_dir() / "models.json"


def settings_file() -> Path:
    return config_dir() / "settings.json"


def runs_db() -> Path:
    return cache_dir() / "runs.sqlite"


def assets_dir() -> Path:
    """Locate bundled assets (works in dev and PyInstaller)."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2] / "assets"
