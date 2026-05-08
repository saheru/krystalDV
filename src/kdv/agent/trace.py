"""Trace events emitted by the agent for UI display + persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


EventKind = Literal[
    "task_start",
    "thought",
    "tool_call",
    "tool_result",
    "final",
    "compaction",
    "error",
    "task_end",
]


@dataclass
class TraceEvent:
    kind: EventKind
    text: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "detail": self.detail,
            "ts": self.ts,
        }
