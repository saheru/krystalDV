"""Bounded concurrent task runner with progress + cancellation support."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable


@dataclass
class TaskOutcome:
    index: int
    result: Any
    error: str | None
    duration_ms: int


async def run_bounded(
    tasks: Iterable[tuple[int, Callable[[], Awaitable[Any]]]],
    *,
    max_concurrency: int,
    on_progress: Callable[[TaskOutcome], None] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> list[TaskOutcome]:
    """Run async callables with a semaphore. Returns outcomes in completion order.

    If `cancel_event` is set, no new tasks start; tasks already running run to completion.
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))
    outcomes: list[TaskOutcome] = []

    async def runner(idx: int, fn: Callable[[], Awaitable[Any]]) -> TaskOutcome:
        if cancel_event is not None and cancel_event.is_set():
            return TaskOutcome(index=idx, result=None, error="cancelled", duration_ms=0)
        async with sem:
            if cancel_event is not None and cancel_event.is_set():
                return TaskOutcome(index=idx, result=None, error="cancelled", duration_ms=0)
            loop = asyncio.get_event_loop()
            t0 = loop.time()
            try:
                result = await fn()
                err: str | None = None
            except Exception as e:  # noqa: BLE001
                result = None
                err = str(e)
            dt_ms = int((loop.time() - t0) * 1000)
            outcome = TaskOutcome(index=idx, result=result, error=err, duration_ms=dt_ms)
            if on_progress is not None:
                try:
                    on_progress(outcome)
                except Exception:  # noqa: BLE001
                    pass
            return outcome

    coros = [asyncio.create_task(runner(idx, fn)) for idx, fn in tasks]
    for c in asyncio.as_completed(coros):
        outcomes.append(await c)
    return outcomes
