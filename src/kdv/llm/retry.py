"""Retry helpers for LLM calls."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, TypeVar

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryableLLMError(Exception):
    """Marker for transient errors that should trigger a retry (429/5xx/network)."""


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    initial_seconds: float = 1.0,
    max_seconds: float = 30.0,
) -> T:
    last_exc: Exception | None = None
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max(1, max_attempts)),
        wait=wait_exponential(multiplier=initial_seconds, max=max_seconds),
        retry=retry_if_exception_type(RetryableLLMError),
        reraise=True,
    ):
        with attempt:
            try:
                return await fn()
            except RetryableLLMError as e:
                last_exc = e
                logger.warning(
                    "LLM retryable error attempt=%d: %s", attempt.retry_state.attempt_number, e
                )
                raise
    if last_exc:
        raise last_exc
    raise RuntimeError("retry loop exited without result")
