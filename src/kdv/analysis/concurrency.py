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

    Cancellation is responsive: when `cancel_event` is set, all in-flight
    asyncio tasks get `.cancel()`-ed so they don't keep retrying or
    waiting on slow HTTP responses.
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
            except asyncio.CancelledError:
                # Re-raise so the gather loop sees it; we surface as
                # outcome below in the cancellation path.
                raise
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

    pending: list[asyncio.Task] = [
        asyncio.create_task(runner(idx, fn)) for idx, fn in tasks
    ]

    # Watcher: when cancel_event fires, .cancel() every still-running task
    # so they bail out of httpx.post / asyncio.sleep / retry waits.
    watcher: asyncio.Task | None = None
    if cancel_event is not None:
        async def _watch_cancel() -> None:
            await cancel_event.wait()
            for t in pending:
                if not t.done():
                    t.cancel()
        watcher = asyncio.create_task(_watch_cancel())

    try:
        for done in asyncio.as_completed(pending):
            try:
                outcomes.append(await done)
            except asyncio.CancelledError:
                outcomes.append(
                    TaskOutcome(index=-1, result=None, error="cancelled", duration_ms=0)
                )
    finally:
        if watcher is not None and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass

    return outcomes
