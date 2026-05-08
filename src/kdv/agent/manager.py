"""In-memory manager for concurrently-running Agent jobs.

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
        columns: list[str],
        rows: list[dict[str, Any]],
        registry: ToolRegistry | None = None,
        max_steps_per_task: int = 16,
    ) -> None:
        super().__init__()
        self.id: str = uuid.uuid4().hex[:12]
        self.name: str = name
        self.tasks: list[str] = list(tasks)
        self.preset = preset
        self.api_key = api_key
        self.columns = list(columns)
        self.rows = list(rows)
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
        )
        try:
            def _on_event(e: TraceEvent) -> None:
                self.events.append(e)
                self.event.emit(e)

            self.result = await runner.run(
                columns=self.columns,
                rows=self.rows,
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
            self.error = str(e)
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
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> AgentJob:
        job = AgentJob(
            name=name,
            tasks=tasks,
            preset=preset,
            api_key=api_key,
            columns=columns,
            rows=rows,
        )
        self._jobs.append(job)
        self.job_added.emit(job)
        # Schedule the async run on the qasync-bridged event loop.
        asyncio.ensure_future(job.run())
        return job
