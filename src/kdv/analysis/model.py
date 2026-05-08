"""Analysis model definition (a saved template) + JSON-backed store."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, Field, ValidationError

from kdv.llm.schema import FieldSpec
from kdv.paths import models_file

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.utcnow().isoformat()


class AnalysisModel(BaseModel):
    """A saved analysis template: input columns + output schema + prompt config."""

    id: str = Field(default_factory=_new_id)
    name: str = "新建分析模型"
    description: str = ""
    system_template: Literal[
        "general", "sentiment", "ticket_classify", "survey_open", "general_eng"
    ] = "general"
    custom_system_prompt: str = ""
    analysis_goal: str = ""
    input_columns: list[str] = Field(default_factory=list)
    output_fields: list[FieldSpec] = Field(default_factory=list)
    sample_path: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class ModelStore:
    """JSON-backed CRUD store for AnalysisModel."""

    def __init__(self) -> None:
        self._models: dict[str, AnalysisModel] = {}
        self.load()

    def load(self) -> None:
        path = models_file()
        if not path.exists():
            self._models = {}
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("failed to read models.json")
            self._models = {}
            return
        items = raw.get("models", []) if isinstance(raw, dict) else raw
        out: dict[str, AnalysisModel] = {}
        for item in items or []:
            try:
                m = AnalysisModel.model_validate(item)
                out[m.id] = m
            except ValidationError:
                logger.exception("skipping invalid model entry")
        self._models = out

    def save(self) -> None:
        models_file().write_text(
            json.dumps(
                {"models": [m.model_dump() for m in self._models.values()]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def list(self) -> list[AnalysisModel]:
        return sorted(self._models.values(), key=lambda m: m.created_at)

    def get(self, model_id: str) -> AnalysisModel | None:
        return self._models.get(model_id)

    def upsert(self, model: AnalysisModel) -> AnalysisModel:
        model.updated_at = _now()
        self._models[model.id] = model
        self.save()
        return model

    def delete(self, model_id: str) -> None:
        self._models.pop(model_id, None)
        self.save()

    def __iter__(self) -> Iterator[AnalysisModel]:
        return iter(self.list())
