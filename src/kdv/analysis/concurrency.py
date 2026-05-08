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

    Cancellation is responsive AND total: when `cancel_event` is set, every
    in-flight task is .cancel()-ed, and CancelledError is swallowed inside
    each runner so the caller gets a clean list of TaskOutcome rather than
    a CancelledError propagating out (which would skip Python's
    `except Exception` handlers and leave UI state inconsistent).
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))
    task_list = list(tasks)

    async def runner(idx: int, fn: Callable[[], Awaitable[Any]]) -> TaskOutcome:
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        try:
            if cancel_event is not None and cancel_event.is_set():
                return TaskOutcome(index=idx, result=None, error="cancelled", duration_ms=0)
            async with sem:
                if cancel_event is not None and cancel_event.is_set():
                    return TaskOutcome(index=idx, result=None, error="cancelled", duration_ms=0)
                t0 = loop.time()
                try:
                    result = await fn()
                    err: str | None = None
                except asyncio.CancelledError:
                    # Caught here so it doesn't escape the `async with sem:`
                    return TaskOutcome(
                        index=idx, result=None, error="cancelled",
                        duration_ms=int((loop.time() - t0) * 1000),
                    )
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
        except asyncio.CancelledError:
            # Belt-and-braces: cancellation can also arrive while waiting
            # to acquire the semaphore. Convert to a clean cancelled outcome.
            return TaskOutcome(
                index=idx, result=None, error="cancelled",
                duration_ms=int((loop.time() - t0) * 1000),
            )

    pending: list[asyncio.Task] = [
        asyncio.create_task(runner(idx, fn)) for idx, fn in task_list
    ]

    watcher: asyncio.Task | None = None
    if cancel_event is not None:
        async def _watch_cancel() -> None:
            try:
                await cancel_event.wait()
            except asyncio.CancelledError:
                return
            for t in pending:
                if not t.done():
                    t.cancel()
        watcher = asyncio.create_task(_watch_cancel())

    # gather ensures we await every task — even cancelled ones — and
    # `return_exceptions=True` keeps a stray exception (or stray
    # CancelledError, on Python ≥3.8) from blowing up the loop.
    raw = await asyncio.gather(*pending, return_exceptions=True)
    outcomes: list[TaskOutcome] = []
    for i, r in enumerate(raw):
        if isinstance(r, TaskOutcome):
            outcomes.append(r)
        elif isinstance(r, asyncio.CancelledError):
            outcomes.append(TaskOutcome(index=i, result=None, error="cancelled", duration_ms=0))
        else:
            outcomes.append(TaskOutcome(index=i, result=None, error=str(r), duration_ms=0))

    if watcher is not None and not watcher.done():
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):
            pass

    return outcomes
