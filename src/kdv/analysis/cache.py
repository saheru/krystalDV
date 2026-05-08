"""SQLite-backed result cache for resumable runs."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from kdv.paths import runs_db


def _connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or runs_db()
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS row_results (
            run_id TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            input_hash TEXT NOT NULL,
            output_json TEXT,
            error TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (run_id, row_index)
        );
        CREATE INDEX IF NOT EXISTS idx_row_results_run ON row_results(run_id);

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            preset_id TEXT,
            model_id TEXT,
            total_rows INTEGER DEFAULT 0,
            mode TEXT,
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT,
            summary_markdown TEXT
        );
        """
    )
    conn.commit()


def hash_row(row: dict[str, Any]) -> str:
    """Stable hash of a row's content for cache invalidation."""
    blob = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


class RunCache:
    """Per-run result cache. Pass `path=None` to use the default DB."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._conn = _connect(path)
        _ensure_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "RunCache":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------ runs --------------------------------------------------------
    def start_run(
        self,
        run_id: str,
        *,
        preset_id: str,
        model_id: str,
        total_rows: int,
        mode: str,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs(run_id,preset_id,model_id,total_rows,mode,started_at) "
            "VALUES (?,?,?,?,?,datetime('now'))",
            (run_id, preset_id, model_id, total_rows, mode),
        )
        self._conn.commit()

    def finish_run(self, run_id: str, summary_markdown: str | None) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at=datetime('now'), summary_markdown=? WHERE run_id=?",
            (summary_markdown, run_id),
        )
        self._conn.commit()

    # ------ rows --------------------------------------------------------
    def get_row(self, run_id: str, row_index: int, expected_hash: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT output_json, error, input_hash FROM row_results WHERE run_id=? AND row_index=?",
            (run_id, row_index),
        )
        r = cur.fetchone()
        if not r:
            return None
        if r["input_hash"] != expected_hash:
            return None
        if r["error"]:
            return None
        try:
            return json.loads(r["output_json"]) if r["output_json"] else None
        except Exception:
            return None

    def put_row(
        self,
        run_id: str,
        row_index: int,
        *,
        input_hash: str,
        output: dict[str, Any] | None,
        error: str | None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: int = 0,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO row_results
               (run_id,row_index,input_hash,output_json,error,
                prompt_tokens,completion_tokens,duration_ms)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                run_id,
                row_index,
                input_hash,
                json.dumps(output, ensure_ascii=False) if output is not None else None,
                error,
                prompt_tokens,
                completion_tokens,
                duration_ms,
            ),
        )
        self._conn.commit()

    def list_rows(self, run_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT row_index, output_json, error, prompt_tokens, completion_tokens, duration_ms "
            "FROM row_results WHERE run_id=? ORDER BY row_index",
            (run_id,),
        )
        out: list[dict[str, Any]] = []
        for r in cur.fetchall():
            out.append(
                {
                    "row_index": r["row_index"],
                    "output": json.loads(r["output_json"]) if r["output_json"] else None,
                    "error": r["error"],
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "duration_ms": r["duration_ms"],
                }
            )
        return out
