"""Pydantic config models for presets and global settings."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.utcnow().isoformat()


class LLMPreset(BaseModel):
    """One LLM endpoint configuration. The API key itself is stored in the OS
    keyring under service `krystaldatavision`, key `llm:<preset_id>`."""

    id: str = Field(default_factory=_new_id)
    name: str = "新建预设"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    # Output token cap. Used directly for per-row analysis; summary mode
    # automatically lifts this to at least 4096 so long reports aren't truncated.
    max_tokens: int = 4096
    timeout_seconds: int = 60
    max_concurrency: int = 5
    max_retries: int = 3
    structured_mode: Literal["auto", "function_calling", "prompt"] = "auto"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    last_test_status: Literal["unknown", "ok", "fail"] = "unknown"
    last_test_message: str = ""
    last_test_at: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class AppSettings(BaseModel):
    """Global app-level settings, persisted to settings.json."""

    theme: Literal["light", "dark", "system"] = "system"
    language: Literal["zh", "en"] = "zh"
    sidebar_collapsed: bool = False
    last_preset_id: str = ""
    last_model_id: str = ""
    window_width: int = 1280
    window_height: int = 800
