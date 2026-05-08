"""OpenAI-compatible async LLM client with structured output support."""
from __future__ import annotations

import json
import logging
import re
import ssl
from dataclasses import dataclass, field
from typing import Any

import httpx

from kdv.config.models import LLMPreset
from kdv.llm.retry import RetryableLLMError, retry_async
from kdv.llm.schema import FieldSpec, build_batch_tool_spec, build_tool_spec

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Non-retryable LLM error (auth, schema, etc.)."""


@dataclass
class LLMResponse:
    parsed: dict[str, Any] | None
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    used_function_calling: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BatchResponse:
    items: list[dict[str, Any]]
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    used_function_calling: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient:
    """Thin OpenAI-compatible client. One instance per preset.

    Use as `async with LLMClient(...) as client:` so the underlying httpx
    AsyncClient is properly closed.
    """

    def __init__(self, preset: LLMPreset, api_key: str) -> None:
        if not api_key:
            raise LLMError("API key 为空，请先在配置页填写并保存。")
        self.preset = preset
        self._api_key = api_key
        self._http: httpx.AsyncClient | None = None
        # Sticky flag: once a proxy proves it can't handle `tools` (empty
        # 200 or 400 mentioning tool/function), all subsequent calls in
        # this client instance skip tools and use prompt-only mode.
        self._tools_unsupported: bool = False

    async def __aenter__(self) -> "LLMClient":
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # Some proxies default to text/event-stream when this is missing,
            # which we then can't json.loads(). Be explicit.
            "Accept": "application/json",
            **self.preset.extra_headers,
        }
        self._http = httpx.AsyncClient(
            base_url=self.preset.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(self.preset.timeout_seconds),
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    async def test_connection(self) -> tuple[bool, str, str]:
        """Send a tiny chat completion to verify base URL/key/model.

        Returns `(ok, message, fc_support)` where fc_support is one of
        "yes" / "no" / "unknown". The function-calling probe is best-effort
        and never fails the connection test.
        """
        try:
            resp = await self.chat(
                system_prompt="You are a helper. Reply with a single word.",
                user_prompt="ping",
                schema_fields=None,
                temperature=0,
                max_tokens=8,
            )
            text = (resp.text or "").strip()[:80]
        except LLMError as e:
            return False, f"连接失败：{e}", "unknown"
        except RetryableLLMError as e:
            return False, f"连接失败（可重试）：{e}", "unknown"
        except Exception as e:  # pragma: no cover
            logger.exception("test_connection unexpected error")
            return False, f"连接失败：{e}", "unknown"

        fc = await self._probe_function_calling()
        suffix = {
            "yes": " · 支持工具调用 ✅（Agent 模式可用）",
            "no": " · 不支持工具调用 ⚠（Agent 模式不可用，请用其他模式）",
            "unknown": "",
        }[fc]
        return True, f"连接成功，模型回复：{text or '<empty>'}{suffix}", fc

    async def _probe_function_calling(self) -> str:
        """Send a minimal tool-using request and check if proxy honours it.

        Returns "yes" if the response includes a tool_call; "no" if the
        proxy returns 400/empty/text-only; "unknown" if we couldn't tell.
        """
        if self._http is None:
            return "unknown"
        probe_tool = {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo back the user's input as-is.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        }
        body = {
            "model": self.preset.model,
            "messages": [
                {"role": "user", "content": "Please call echo with text='ok'."},
            ],
            "temperature": 0,
            "max_tokens": 64,
            "tools": [probe_tool],
            "tool_choice": {"type": "function", "function": {"name": "echo"}},
            "stream": False,
        }
        try:
            r = await self._http.post("/chat/completions", json=body)
        except Exception:
            return "unknown"
        if r.status_code >= 400:
            return "no"
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError):
            # Empty body / non-JSON 200 → strong signal proxy dropped the tools.
            return "no"
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return "unknown"
        tcs = msg.get("tool_calls") or []
        if tcs:
            return "yes"
        # If no tool_calls came back, model didn't call the tool — treat
        # as no FC support (a working FC stack would have called it given
        # `tool_choice` forces it).
        return "no"

    # ------------------------------------------------------------------
    async def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_fields: list[FieldSpec] | None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Run one chat completion. If schema_fields given, attempt structured output."""

        async def _call() -> LLMResponse:
            return await self._chat_once(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_fields=schema_fields,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await retry_async(_call, max_attempts=self.preset.max_retries)

    async def _chat_once(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_fields: list[FieldSpec] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        if self._http is None:
            raise LLMError("LLMClient 未进入 async context")

        body: dict[str, Any] = {
            "model": self.preset.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.preset.temperature if temperature is None else temperature,
            "max_tokens": self.preset.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }

        mode = self.preset.structured_mode
        # If a previous call from this client proved the proxy ignores tools,
        # don't bother sending them again — go straight to prompt mode.
        if self._tools_unsupported and mode == "auto":
            return await self._chat_prompt_fallback(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_fields=schema_fields,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        use_fc = bool(schema_fields) and mode in ("auto", "function_calling")
        if use_fc:
            tool = build_tool_spec(schema_fields, "emit_analysis")
            body["tools"] = [tool]
            body["tool_choice"] = {"type": "function", "function": {"name": "emit_analysis"}}

        try:
            r = await self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise RetryableLLMError(f"超时：{e}") from e
        except (httpx.NetworkError, httpx.RemoteProtocolError) as e:
            raise RetryableLLMError(f"网络错误：{type(e).__name__}: {e}") from e
        except ssl.SSLError as e:
            raise RetryableLLMError(f"SSL 错误：{e}") from e
        except OSError as e:
            raise RetryableLLMError(f"系统/连接错误：{e}") from e

        if r.status_code in (408, 425, 429) or 500 <= r.status_code < 600:
            raise RetryableLLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code == 401 or r.status_code == 403:
            raise LLMError(f"鉴权失败（HTTP {r.status_code}），请检查 API key 与 base URL。")
        if r.status_code >= 400:
            # 400 with tool_choice may mean provider doesn't support function calling
            txt = r.text[:500]
            if use_fc and ("tool" in txt.lower() or "function" in txt.lower()):
                if mode == "auto":
                    logger.info("provider rejected tools; falling back to prompt mode")
                    return await self._chat_prompt_fallback(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        schema_fields=schema_fields,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
            raise LLMError(f"HTTP {r.status_code}: {txt}")

        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raw = (r.text or "").strip()[:300] or "<empty body>"
            empty = (raw == "<empty body>")
            # If we sent tools and the proxy responded with HTTP 200 +
            # empty body, the proxy almost certainly doesn't support
            # OpenAI function calling (common with Chinese Claude proxies).
            # Mark tools as unsupported and retry without them.
            if empty and use_fc and self.preset.structured_mode == "auto":
                self._tools_unsupported = True
                logger.info(
                    "empty 200 with tools enabled → proxy lacks function-calling; "
                    "switching client to prompt-mode for the rest of this run"
                )
                return await self._chat_prompt_fallback(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_fields=schema_fields,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            hint = "（代理过载——建议把『并发』调到 3-5 再试）" if empty else ""
            raise RetryableLLMError(
                f"响应不是 JSON（HTTP {r.status_code}）: {raw!r}{hint}"
            ) from e
        return _parse_chat_response(data, expect_tool=use_fc)

    async def chat_batch(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_fields: list[FieldSpec],
        expected_n: int,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> "BatchResponse":
        """One LLM call returning N structured rows. Falls back to prompt mode
        if the proxy can't handle tools (same `_tools_unsupported` flag as `chat`).

        Returns BatchResponse with `items` list (length should equal expected_n,
        but caller must validate). On parsing failure, raises LLMError.
        """

        async def _call() -> "BatchResponse":
            return await self._chat_batch_once(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_fields=schema_fields,
                expected_n=expected_n,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await retry_async(_call, max_attempts=self.preset.max_retries)

    async def _chat_batch_once(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_fields: list[FieldSpec],
        expected_n: int,
        temperature: float | None,
        max_tokens: int | None,
    ) -> "BatchResponse":
        if self._http is None:
            raise LLMError("LLMClient 未进入 async context")

        body: dict[str, Any] = {
            "model": self.preset.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.preset.temperature if temperature is None else temperature,
            "max_tokens": self.preset.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        mode = self.preset.structured_mode
        use_fc = (not self._tools_unsupported) and mode in ("auto", "function_calling")
        if use_fc:
            body["tools"] = [build_batch_tool_spec(schema_fields, "emit_batch")]
            body["tool_choice"] = {"type": "function", "function": {"name": "emit_batch"}}

        try:
            r = await self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise RetryableLLMError(f"超时：{e}") from e
        except (httpx.NetworkError, httpx.RemoteProtocolError) as e:
            raise RetryableLLMError(f"网络错误：{type(e).__name__}: {e}") from e
        except ssl.SSLError as e:
            raise RetryableLLMError(f"SSL 错误：{e}") from e
        except OSError as e:
            raise RetryableLLMError(f"系统/连接错误：{e}") from e

        if r.status_code in (408, 425, 429) or 500 <= r.status_code < 600:
            raise RetryableLLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code == 401 or r.status_code == 403:
            raise LLMError(f"鉴权失败（HTTP {r.status_code}）。")
        if r.status_code >= 400:
            txt = r.text[:500]
            if use_fc and ("tool" in txt.lower() or "function" in txt.lower()) and mode == "auto":
                self._tools_unsupported = True
                # Re-call without tools — easier to just recurse
                return await self._chat_batch_once(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_fields=schema_fields,
                    expected_n=expected_n,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            raise LLMError(f"HTTP {r.status_code}: {txt}")

        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raw = (r.text or "").strip()[:300] or "<empty body>"
            empty = (raw == "<empty body>")
            if empty and use_fc and mode == "auto":
                self._tools_unsupported = True
                return await self._chat_batch_once(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_fields=schema_fields,
                    expected_n=expected_n,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            raise RetryableLLMError(f"响应不是 JSON（HTTP {r.status_code}）: {raw!r}") from e

        # Parse the response and pull out the items array
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as e:
            raise LLMError(f"响应缺少 choices：{data}") from e
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        items: list[dict[str, Any]] | None = None
        used_fc = False
        text = message.get("content") or ""

        if use_fc:
            tcs = message.get("tool_calls") or []
            if tcs:
                try:
                    args = tcs[0]["function"]["arguments"]
                    parsed = json.loads(args) if isinstance(args, str) else args
                    if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
                        items = parsed["results"]
                        used_fc = True
                except Exception:
                    logger.exception("failed to parse batch tool args")

        if items is None and text:
            items = _extract_json_array_from_text(text)

        if items is None:
            raise LLMError(
                f"批量响应未能解析为数组。原始回复前 200 字：{(text or '<空>')[:200]}"
            )

        return BatchResponse(
            items=items,
            text=text,
            raw=data,
            used_function_calling=used_fc,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

    async def chat_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> LLMResponse:
        """Multi-turn chat with a list of available tools (agent loop).

        Unlike `chat()`, the conversation history is passed verbatim and the
        model picks among the supplied tools — or returns plain text — at will.
        """

        async def _call() -> LLMResponse:
            return await self._chat_with_tools_once(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
            )

        return await retry_async(_call, max_attempts=self.preset.max_retries)

    async def _chat_with_tools_once(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float | None,
        max_tokens: int | None,
        tool_choice: str | dict[str, Any],
    ) -> LLMResponse:
        if self._http is None:
            raise LLMError("LLMClient 未进入 async context")
        body: dict[str, Any] = {
            "model": self.preset.model,
            "messages": messages,
            "temperature": self.preset.temperature if temperature is None else temperature,
            "max_tokens": self.preset.max_tokens if max_tokens is None else max_tokens,
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": False,
        }
        try:
            r = await self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise RetryableLLMError(f"超时：{e}") from e
        except (httpx.NetworkError, httpx.RemoteProtocolError) as e:
            raise RetryableLLMError(f"网络错误：{type(e).__name__}: {e}") from e
        except ssl.SSLError as e:
            raise RetryableLLMError(f"SSL 错误：{e}") from e
        except OSError as e:
            raise RetryableLLMError(f"系统/连接错误：{e}") from e

        if r.status_code in (408, 425, 429) or 500 <= r.status_code < 600:
            raise RetryableLLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code == 401 or r.status_code == 403:
            raise LLMError(f"鉴权失败（HTTP {r.status_code}）。")
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raw = (r.text or "").strip()[:300] or "<empty body>"
            extra = ""
            if raw == "<empty body>":
                extra = (
                    "（你的 LLM 代理可能不支持 OpenAI function calling。"
                    "Agent 模式必须靠工具调用——请换一个支持的端点，"
                    "或把『结构化模式』设为 prompt 走逐行/汇总模式）"
                )
            raise RetryableLLMError(
                f"响应不是 JSON（HTTP {r.status_code}）: {raw!r}{extra}"
            ) from e
        return _parse_chat_response(data, expect_tool=True)

    async def _chat_prompt_fallback(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_fields: list[FieldSpec] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Re-run without tools; rely on prompt to coerce JSON output."""
        if self._http is None:
            raise LLMError("LLMClient 未进入 async context")

        body: dict[str, Any] = {
            "model": self.preset.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.preset.temperature if temperature is None else temperature,
            "max_tokens": self.preset.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        try:
            r = await self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise RetryableLLMError(f"超时：{e}") from e
        except (httpx.NetworkError, httpx.RemoteProtocolError) as e:
            raise RetryableLLMError(f"网络错误：{type(e).__name__}: {e}") from e
        except ssl.SSLError as e:
            raise RetryableLLMError(f"SSL 错误：{e}") from e
        except OSError as e:
            raise RetryableLLMError(f"系统/连接错误：{e}") from e
        if r.status_code in (408, 425, 429) or 500 <= r.status_code < 600:
            raise RetryableLLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raw = (r.text or "").strip()[:300] or "<empty body>"
            raise RetryableLLMError(
                f"响应不是 JSON（HTTP {r.status_code}）: {raw!r}"
            ) from e
        return _parse_chat_response(data, expect_tool=False)


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)
_RAW_JSON_OBJ = re.compile(r"(\{(?:[^{}]|(?:\{[^{}]*\}))*\})", re.DOTALL)
_RAW_JSON_ARR = re.compile(r"(\[[\s\S]*\])", re.DOTALL)


def _extract_json_array_from_text(text: str) -> list[dict[str, Any]] | None:
    """Extract a JSON array of objects from raw text (for batch responses)."""
    if not text:
        return None
    candidates: list[str] = []
    m = _FENCED_JSON.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text.strip())
    m = _RAW_JSON_ARR.search(text)
    if m:
        candidates.append(m.group(1))
    for c in candidates:
        try:
            obj = json.loads(c)
        except Exception:
            continue
        if isinstance(obj, list) and all(isinstance(x, dict) for x in obj):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("results"), list):
            results = obj["results"]
            if all(isinstance(x, dict) for x in results):
                return results
    return None


def _parse_chat_response(data: dict[str, Any], *, expect_tool: bool) -> LLMResponse:
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError) as e:
        raise LLMError(f"响应缺少 choices 字段：{data}") from e
    message = choice.get("message") or {}
    usage = data.get("usage") or {}

    parsed: dict[str, Any] | None = None
    used_fc = False
    text = message.get("content") or ""
    tool_calls = list(message.get("tool_calls") or [])

    if expect_tool and tool_calls:
        try:
            args = tool_calls[0]["function"]["arguments"]
            parsed = json.loads(args) if isinstance(args, str) else args
            used_fc = True
        except Exception:
            logger.exception("failed to parse tool_calls arguments")

    if parsed is None and text:
        parsed = _extract_json_from_text(text)

    return LLMResponse(
        parsed=parsed,
        text=text,
        raw=data,
        used_function_calling=used_fc,
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        tool_calls=tool_calls,
    )


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from raw text, tolerant of fences and prose."""
    if not text:
        return None
    m = _FENCED_JSON.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
    try:
        obj = json.loads(text.strip())
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = _RAW_JSON_OBJ.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None
