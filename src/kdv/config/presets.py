"""CRUD store for LLM presets and global app settings (JSON-backed)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from kdv.config.models import AppSettings, LLMPreset
from kdv.paths import presets_file, settings_file

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to read %s", path)
        return {}


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class PresetStore:
    """In-memory cache + JSON persistence for LLM presets."""

    def __init__(self) -> None:
        self._presets: dict[str, LLMPreset] = {}
        self.load()

    # ---- persistence ------------------------------------------------------
    def load(self) -> None:
        raw = _read_json(presets_file())
        items = raw.get("presets", []) if isinstance(raw, dict) else raw
        out: dict[str, LLMPreset] = {}
        for item in items or []:
            try:
                p = LLMPreset.model_validate(item)
                out[p.id] = p
            except ValidationError:
                logger.exception("skipping invalid preset entry")
        self._presets = out

    def save(self) -> None:
        _write_json(
            presets_file(),
            {"presets": [p.model_dump() for p in self._presets.values()]},
        )

    # ---- CRUD -------------------------------------------------------------
    def list(self) -> list[LLMPreset]:
        return sorted(self._presets.values(), key=lambda p: p.created_at)

    def get(self, preset_id: str) -> LLMPreset | None:
        return self._presets.get(preset_id)

    def upsert(self, preset: LLMPreset) -> LLMPreset:
        from datetime import datetime

        preset.updated_at = datetime.utcnow().isoformat()
        self._presets[preset.id] = preset
        self.save()
        return preset

    def delete(self, preset_id: str) -> None:
        self._presets.pop(preset_id, None)
        self.save()

    def __iter__(self) -> Iterator[LLMPreset]:
        return iter(self.list())


class SettingsStore:
    """Global app settings (theme, last selections, window size)."""

    def __init__(self) -> None:
        self._settings = self._load()

    def _load(self) -> AppSettings:
        raw = _read_json(settings_file())
        if isinstance(raw, dict) and raw:
            try:
                return AppSettings.model_validate(raw)
            except ValidationError:
                logger.exception("invalid settings, resetting to defaults")
        return AppSettings()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def save(self) -> None:
        _write_json(settings_file(), self._settings.model_dump())

    def update(self, **fields) -> AppSettings:
        self._settings = self._settings.model_copy(update=fields)
        self.save()
        return self._settings
