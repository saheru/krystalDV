"""Krystal Data Vision — Windows LLM data analysis tool."""
from __future__ import annotations

__app_name__ = "Krystal Data Vision"

# Base semantic version. Bump by hand when there's a real release; the
# CI build also stamps a date + short-sha suffix so every artifact is
# uniquely identifiable.
_BASE_VERSION = "0.1.0"


def _resolve_version() -> str:
    # 1) Build-time stamp — CI writes src/kdv/_buildinfo.py before pyinstaller
    #    runs (see .github/workflows/build-*.yml). PyInstaller bundles it
    #    next to this file.
    try:
        from kdv import _buildinfo  # type: ignore[attr-defined]

        date = getattr(_buildinfo, "BUILD_DATE", "") or ""
        sha = getattr(_buildinfo, "COMMIT_SHA", "") or ""
        count = getattr(_buildinfo, "COMMIT_COUNT", 0) or 0
        if sha:
            tail = ".".join(p for p in (date, sha) if p)
            return f"{_BASE_VERSION}.dev{count}+{tail}" if tail else f"{_BASE_VERSION}.dev{count}"
    except ImportError:
        pass

    # 2) Dev fallback — try to read git directly so a fresh checkout still
    #    gets a useful build label.
    try:
        import subprocess
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        sha = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            .decode()
            .strip()
        )
        if sha:
            return f"{_BASE_VERSION}-dev+{sha}"
    except Exception:
        pass

    # 3) Last resort.
    return f"{_BASE_VERSION}-dev"


__version__ = _resolve_version()
