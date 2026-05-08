"""Token counting / budgeting helpers.

Tries `tiktoken` first (accurate for OpenAI-family BPEs); falls back to a
character-based heuristic that's reasonable for Chinese + English mixed text.

Heuristic: ~1 token per 3 chars for CJK-heavy text, ~1 per 4 for English.
We use a safe blended ratio of 3.5.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

_HAS_TIKTOKEN: bool | None = None
_TIKTOKEN_ENCODER = None


def _try_tiktoken():
    """Lazy-load tiktoken; cache result."""
    global _HAS_TIKTOKEN, _TIKTOKEN_ENCODER
    if _HAS_TIKTOKEN is not None:
        return _TIKTOKEN_ENCODER if _HAS_TIKTOKEN else None
    try:
        import tiktoken  # type: ignore

        # cl100k_base covers GPT-3.5/4/4o families. For other models the
        # heuristic stays close enough.
        _TIKTOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
        _HAS_TIKTOKEN = True
    except Exception:
        _HAS_TIKTOKEN = False
        _TIKTOKEN_ENCODER = None
    return _TIKTOKEN_ENCODER


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens for `text`. Cheap and approximate."""
    if not text:
        return 0
    enc = _try_tiktoken()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Heuristic: ~1 token / 3.5 chars across mixed CJK+English content.
    return max(1, int(len(text) / 3.5))


def estimate_messages(messages: Iterable[dict[str, Any]]) -> int:
    """Estimate tokens for a list of OpenAI-style messages."""
    total = 0
    for m in messages:
        total += 4  # per-message overhead (role, separators)
        for k, v in m.items():
            if v is None:
                continue
            if isinstance(v, str):
                total += estimate_tokens(v)
            else:
                total += estimate_tokens(json.dumps(v, ensure_ascii=False))
    total += 2  # priming tokens
    return total


# Reasonable per-model output context defaults. Real values vary; these are
# only used as a fallback when the user hasn't customised the preset.
DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-3.5-turbo": 16_000,
    # Anthropic via OpenAI-compatible proxies
    "claude-3-5-sonnet": 200_000,
    "claude-opus-4": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-coder": 64_000,
    # Moonshot / 智谱 / 通义
    "moonshot-v1-128k": 128_000,
    "glm-4": 128_000,
    "qwen-max": 32_000,
}


def context_window_for(model: str) -> int:
    """Best-effort lookup of context window. Falls back to a conservative 32k."""
    if not model:
        return 32_000
    m = model.lower()
    for key, n in DEFAULT_CONTEXT_WINDOWS.items():
        if key in m:
            return n
    return 32_000
