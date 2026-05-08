"""Secure secret storage backed by the OS keyring (Windows Credential Manager).

Falls back to an obfuscated file in the config dir if keyring is unavailable.
The fallback is NOT secure cryptographically — it's only there so dev on
non-Windows hosts doesn't fail. On Windows the keyring backend is always present.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from kdv.paths import config_dir

logger = logging.getLogger(__name__)

SERVICE = "krystaldatavision"
_FALLBACK_FILE_NAME = ".secrets.fallback"


def _fallback_path() -> Path:
    return config_dir() / _FALLBACK_FILE_NAME


def _load_fallback() -> dict[str, str]:
    p = _fallback_path()
    if not p.exists():
        return {}
    try:
        raw = base64.b64decode(p.read_bytes()).decode("utf-8")
        return json.loads(raw)
    except Exception:
        logger.exception("failed to read fallback secret store")
        return {}


def _save_fallback(data: dict[str, str]) -> None:
    p = _fallback_path()
    raw = base64.b64encode(json.dumps(data).encode("utf-8"))
    p.write_bytes(raw)


class SecretStore:
    """Wraps the keyring API with a graceful fallback."""

    def __init__(self) -> None:
        try:
            import keyring  # noqa: F401

            self._keyring_available = True
        except Exception:
            logger.warning("keyring not available; using obfuscated file fallback")
            self._keyring_available = False

    def set(self, key: str, value: str) -> None:
        if self._keyring_available:
            import keyring

            try:
                keyring.set_password(SERVICE, key, value)
                return
            except Exception:
                logger.exception("keyring set failed; falling back to file")
                self._keyring_available = False
        data = _load_fallback()
        data[key] = value
        _save_fallback(data)

    def get(self, key: str) -> str:
        if self._keyring_available:
            import keyring

            try:
                v = keyring.get_password(SERVICE, key)
                if v is not None:
                    return v
            except Exception:
                logger.exception("keyring get failed; falling back to file")
                self._keyring_available = False
        return _load_fallback().get(key, "")

    def delete(self, key: str) -> None:
        if self._keyring_available:
            import keyring

            try:
                keyring.delete_password(SERVICE, key)
            except Exception:
                pass
        data = _load_fallback()
        if key in data:
            del data[key]
            _save_fallback(data)

    @staticmethod
    def llm_key(preset_id: str) -> str:
        return f"llm:{preset_id}"
