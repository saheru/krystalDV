"""Project store — persists a snapshot of every completed analysis run.

Lets users reopen past analyses (data + KPIs + charts/insights + summary
markdown + chat conversation) without rerunning the LLM.

Backed by SQLite so it survives across launches and shares the cache file
with `analysis.cache.RunCache`.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from kdv.paths import runs_db

logger = logging.getLogger(__name__)


def _connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or runs_db()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            preset_id TEXT,
            preset_name TEXT,
            model_id TEXT,
            analysis_model_id TEXT,
            analysis_model_name TEXT,
            mode TEXT,
            source_path TEXT,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


@dataclass
class ProjectMeta:
    project_id: str
    name: str
    created_at: str
    updated_at: str
    preset_name: str = ""
    model_id: str = ""
    analysis_model_name: str = ""
    mode: str = ""
    source_path: str = ""
    n_rows: int = 0
    n_cols: int = 0


@dataclass
class TableSnapshot:
    """One sheet (or one upload-source) inside a multi-table project.

    Stored alongside the legacy `columns`/`rows` fields so older code paths
    (single-table view) keep working — but agent runs persist every loaded
    table here for round-tripping.
    """
    table_id: str = ""
    sheet_name: str = ""
    source_path: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ProjectSnapshot:
    """The fully self-contained state of one analysis project."""

    project_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    name: str = "未命名项目"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    preset_id: str = ""
    preset_name: str = ""
    model_id: str = ""
    analysis_model_id: str = ""
    analysis_model_name: str = ""
    mode: str = ""
    source_path: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_outputs: list[dict[str, Any] | None] = field(default_factory=list)
    row_errors: list[str | None] = field(default_factory=list)
    summary_markdown: str | None = None
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    duration_ms_total: int = 0
    # Optional persisted UI artefacts:
    chart_specs: list[dict[str, Any]] = field(default_factory=list)
    insights: list[dict[str, Any]] = field(default_factory=list)
    chat_history: list[dict[str, Any]] = field(default_factory=list)
    # Multi-table workbook snapshot. For single-table projects this is empty
    # and `columns`/`rows` above are the source of truth (back-compat). For
    # agent or multi-sheet runs this holds every loaded sheet.
    tables: list[TableSnapshot] = field(default_factory=list)

    def to_payload_json(self) -> str:
        d = dict(self.__dict__)
        # Serialize tables explicitly so dataclass-as-dict stays JSON-friendly.
        d["tables"] = [t.__dict__ if isinstance(t, TableSnapshot) else t for t in self.tables]
        return json.dumps(d, ensure_ascii=False, default=str)

    @classmethod
    def from_payload_json(cls, blob: str) -> "ProjectSnapshot":
        d = json.loads(blob)
        # Backwards compatible: older snapshots have no `tables` field.
        raw_tables = d.pop("tables", None) or []
        # Drop unknown keys so future-vs-past schema mismatches don't crash.
        valid_keys = set(cls.__dataclass_fields__.keys())
        d = {k: v for k, v in d.items() if k in valid_keys}
        snap = cls(**d)
        snap.tables = [
            TableSnapshot(**t) if isinstance(t, dict) else t
            for t in raw_tables
        ]
        return snap


class ProjectStore:
    def __init__(self, path: Path | None = None) -> None:
        self._conn = _connect(path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ProjectStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ---- CRUD ---------------------------------------------------------
    def save(self, snap: ProjectSnapshot) -> None:
        snap.updated_at = datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            """INSERT OR REPLACE INTO projects
               (project_id,name,created_at,updated_at,preset_id,preset_name,
                model_id,analysis_model_id,analysis_model_name,mode,source_path,payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                snap.project_id,
                snap.name,
                snap.created_at,
                snap.updated_at,
                snap.preset_id,
                snap.preset_name,
                snap.model_id,
                snap.analysis_model_id,
                snap.analysis_model_name,
                snap.mode,
                snap.source_path,
                snap.to_payload_json(),
            ),
        )
        self._conn.commit()

    def list(self) -> list[ProjectMeta]:
        cur = self._conn.execute(
            """SELECT project_id,name,created_at,updated_at,preset_name,model_id,
                      analysis_model_name,mode,source_path,payload_json
               FROM projects ORDER BY datetime(updated_at) DESC"""
        )
        out: list[ProjectMeta] = []
        for r in cur.fetchall():
            try:
                blob = json.loads(r["payload_json"])
                n_rows = len(blob.get("rows") or [])
                n_cols = len(blob.get("columns") or [])
            except Exception:
                n_rows = n_cols = 0
            out.append(
                ProjectMeta(
                    project_id=r["project_id"],
                    name=r["name"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    preset_name=r["preset_name"] or "",
                    model_id=r["model_id"] or "",
                    analysis_model_name=r["analysis_model_name"] or "",
                    mode=r["mode"] or "",
                    source_path=r["source_path"] or "",
                    n_rows=n_rows,
                    n_cols=n_cols,
                )
            )
        return out

    def load(self, project_id: str) -> ProjectSnapshot | None:
        cur = self._conn.execute(
            "SELECT payload_json FROM projects WHERE project_id=?",
            (project_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        try:
            return ProjectSnapshot.from_payload_json(r["payload_json"])
        except Exception:
            logger.exception("failed to deserialize project %s", project_id)
            return None

    def rename(self, project_id: str, new_name: str) -> None:
        self._conn.execute(
            "UPDATE projects SET name=?, updated_at=? WHERE project_id=?",
            (new_name, datetime.now().isoformat(timespec="seconds"), project_id),
        )
        self._conn.commit()

    def delete(self, project_id: str) -> None:
        self._conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
        self._conn.commit()
