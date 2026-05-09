"""In-memory manager for concurrently-running Agent jobs.

Includes `parse_tasks(text)` which intelligently splits a user paste into
discrete agent tasks — handling common formats like:
  - One task per line
  - Multi-line tasks separated by blank lines
  - Explicit "# 任务 1 / # 任务 2" section markers (Markdown comments stay
    out of the task body).

Each `AgentJob` is a Qt QObject that emits signals as the agent runs:
- `event`: per-step trace event (thoughts, tool calls, results, ...)
- `state_changed`: status transitions (running → done / cancelled / error)
- `finished`: emits the final AgentResult

`AgentManager` owns the list of jobs, spawns new ones, and emits a signal
when a job is added so the UI can react.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import qasync
from PySide6.QtCore import QObject, Signal

from kdv.agent.runner import AgentResult, AgentRunner
from kdv.agent.tools import ToolRegistry, build_default_registry
from kdv.agent.trace import TraceEvent
from kdv.config.models import LLMPreset

logger = logging.getLogger(__name__)


JobStatus = Literal["pending", "running", "done", "cancelled", "error"]


class AgentJob(QObject):
    """A single live agent run with its own cancel-event and event stream."""

    event = Signal(object)            # TraceEvent
    state_changed = Signal(str)       # new status
    finished = Signal(object)         # AgentResult | None

    def __init__(
        self,
        *,
        name: str,
        tasks: list[str],
        preset: LLMPreset,
        api_key: str,
        columns: list[str] | None = None,
        rows: list[dict[str, Any]] | None = None,
        tables: list[Any] | None = None,
        registry: ToolRegistry | None = None,
        max_steps_per_task: int = 16,
        fast_preset: LLMPreset | None = None,
        fast_api_key: str = "",
    ) -> None:
        """Either pass `tables` (list[ExcelTable], multi-sheet/multi-file) OR
        legacy `columns + rows` (single-table). The job stashes whatever it
        was given and re-uses it on .run() — and exposes a `columns`/`rows`
        view for downstream UI compatibility.
        """
        super().__init__()
        self.id: str = uuid.uuid4().hex[:12]
        self.name: str = name
        self.tasks: list[str] = list(tasks)
        self.preset = preset
        self.api_key = api_key
        self.fast_preset = fast_preset
        self.fast_api_key = fast_api_key
        # Multi-table path. If only single-table args were given, lift them
        # into a one-element list so downstream callers see a uniform shape.
        if tables:
            self.tables = list(tables)
        elif columns is not None and rows is not None:
            from kdv.excel.reader import ExcelTable as _Et

            self.tables = [_Et(
                columns=list(columns),
                rows=list(rows),
                sheet_name="",
                source_path="",
                table_id="default",
            )]
        else:
            self.tables = []
        # Single-table accessors (used by main_window result rendering).
        primary = self.tables[0] if self.tables else None
        self.columns = list(primary.columns) if primary else []
        self.rows = list(primary.rows) if primary else []
        self.registry = registry or build_default_registry()
        self.max_steps_per_task = max_steps_per_task

        self.status: JobStatus = "pending"
        self.error: str = ""
        self.events: list[TraceEvent] = []
        self.result: AgentResult | None = None
        self.started_at: str = ""
        self.finished_at: str = ""
        self.cancel_event = asyncio.Event()

    # ---- lifecycle ----------------------------------------------------
    def cancel(self) -> None:
        if self.status in ("done", "cancelled", "error"):
            return
        self.cancel_event.set()

    def _set_status(self, s: JobStatus) -> None:
        if self.status == s:
            return
        self.status = s
        self.state_changed.emit(s)

    async def run(self) -> None:
        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._set_status("running")
        runner = AgentRunner(
            preset=self.preset,
            api_key=self.api_key,
            registry=self.registry,
            max_steps_per_task=self.max_steps_per_task,
            fast_preset=self.fast_preset,
            fast_api_key=self.fast_api_key,
        )
        try:
            def _on_event(e: TraceEvent) -> None:
                self.events.append(e)
                self.event.emit(e)

            self.result = await runner.run(
                tables=self.tables if self.tables else None,
                columns=self.columns if not self.tables else None,
                rows=self.rows if not self.tables else None,
                tasks=self.tasks,
                on_event=_on_event,
                cancel_event=self.cancel_event,
            )
            if self.cancel_event.is_set():
                self._set_status("cancelled")
            else:
                self._set_status("done")
        except Exception as e:  # noqa: BLE001
            logger.exception("agent job %s crashed", self.id)
            raw = str(e)
            # Detect the "proxy ate the tools" pattern and rewrite the
            # error message into something actionable.
            if "<empty body>" in raw or (
                "function" in raw.lower() and "200" in raw
            ):
                self.error = (
                    "此 LLM 配置不支持 OpenAI function calling，Agent 模式无法运行。"
                    "请换成 OpenAI / DeepSeek / 智谱 GLM 等支持工具调用的端点，"
                    "或在『运行分析』页用逐行/汇总模式（不需要工具调用）。"
                )
            else:
                self.error = raw
            self._set_status("error")
        finally:
            self.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.finished.emit(self.result)


class AgentManager(QObject):
    """Owns the list of all running/finished agent jobs."""

    job_added = Signal(object)        # AgentJob
    job_removed = Signal(str)         # job id

    def __init__(self) -> None:
        super().__init__()
        self._jobs: list[AgentJob] = []

    def jobs(self) -> list[AgentJob]:
        return list(self._jobs)

    def get(self, job_id: str) -> AgentJob | None:
        return next((j for j in self._jobs if j.id == job_id), None)

    def remove(self, job_id: str) -> None:
        for i, j in enumerate(self._jobs):
            if j.id == job_id:
                if j.status == "running":
                    j.cancel()
                del self._jobs[i]
                self.job_removed.emit(job_id)
                return

    def spawn(
        self,
        *,
        name: str,
        tasks: list[str],
        preset: LLMPreset,
        api_key: str,
        columns: list[str] | None = None,
        rows: list[dict[str, Any]] | None = None,
        tables: list[Any] | None = None,
        fast_preset: LLMPreset | None = None,
        fast_api_key: str = "",
    ) -> AgentJob:
        job = AgentJob(
            name=name,
            tasks=tasks,
            preset=preset,
            api_key=api_key,
            columns=columns,
            rows=rows,
            tables=tables,
            fast_preset=fast_preset,
            fast_api_key=fast_api_key,
        )
        self._jobs.append(job)
        self.job_added.emit(job)
        # Schedule the async run on the qasync-bridged event loop.
        asyncio.ensure_future(job.run())
        return job


# ======================================================================
# Task-list parser: split a user paste into a list of agent tasks.
# ======================================================================

# Matches lines like "# 任务 1", "## 任务1", "任务一：", "Task 1", "1." (numbered),
# anywhere on the line. Used as a section delimiter.
_TASK_DELIM_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:任务|Task)\s*[一二三四五六七八九十\d]+\s*[:：.、]?\s*$",
    re.MULTILINE,
)


def parse_tasks(text: str) -> list[str]:
    """Split a free-form paste into discrete agent tasks.

    Resolution order:
      1. If '# 任务 N' / 'Task N' delimiters are present → split on them.
         Every non-empty body in between becomes one task.
      2. Else if blank-line-separated paragraphs exist → each paragraph
         (excluding lines starting with `#`, treated as Markdown headings)
         becomes one task.
      3. Else fall back to "one non-empty non-comment line = one task".
    """
    if not text or not text.strip():
        return []

    # ---- 1. explicit section delimiters ---------------------------
    first_match = _TASK_DELIM_RE.search(text)
    if first_match is not None:
        # Drop the preamble before the first delimiter — it's intro/help
        # text, not a task. (Common case: "## 单月对账(快速版)\n... # 任务 1 …".)
        post = text[first_match.start():]
        parts = _TASK_DELIM_RE.split(post)
        cleaned: list[str] = []
        for part in parts:
            body = _strip_markdown_headings(part).strip()
            if body:
                cleaned.append(body)
        if cleaned:
            return cleaned

    # ---- 2. blank-line-separated paragraphs -----------------------
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) > 1:
        out: list[str] = []
        for p in paragraphs:
            body = _strip_markdown_headings(p).strip()
            if body:
                out.append(body)
        if out:
            return out

    # ---- 3. line-by-line (skip Markdown comments / blank) --------
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _strip_markdown_headings(block: str) -> str:
    """Drop lines that are pure Markdown headings (`# foo`, `## bar`)."""
    return "\n".join(
        ln for ln in block.splitlines()
        if not re.match(r"^\s*#+\s+", ln)
    )
