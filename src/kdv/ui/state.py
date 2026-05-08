"""Shared in-memory app state passed between pages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kdv.analysis.model import AnalysisModel, ModelStore
from kdv.analysis.runner import RunResult
from kdv.config.models import LLMPreset
from kdv.config.presets import PresetStore, SettingsStore
from kdv.config.store import SecretStore


@dataclass
class AppState:
    presets: PresetStore = field(default_factory=PresetStore)
    models: ModelStore = field(default_factory=ModelStore)
    settings: SettingsStore = field(default_factory=SettingsStore)
    secrets: SecretStore = field(default_factory=SecretStore)
    last_run: RunResult | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def get_api_key(self, preset: LLMPreset) -> str:
        return self.secrets.get(SecretStore.llm_key(preset.id))

    def set_api_key(self, preset: LLMPreset, key: str) -> None:
        self.secrets.set(SecretStore.llm_key(preset.id), key)

    def selected_preset(self) -> LLMPreset | None:
        pid = self.settings.settings.last_preset_id
        if pid:
            p = self.presets.get(pid)
            if p:
                return p
        items = self.presets.list()
        return items[0] if items else None

    def selected_model(self) -> AnalysisModel | None:
        mid = self.settings.settings.last_model_id
        if mid:
            m = self.models.get(mid)
            if m:
                return m
        items = self.models.list()
        return items[0] if items else None
