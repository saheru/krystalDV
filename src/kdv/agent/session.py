"""Long-lived agent session for the result page chat.

Persists ConversationContext + ToolContext across multiple user turns so
charts/insights accumulate and the model retains memory of prior questions.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from kdv.agent.context import ConversationContext
from kdv.agent.tools import ChartSpec, Insight, ToolContext, ToolRegistry, build_default_registry
from kdv.agent.trace import TraceEvent
from kdv.config.models import LLMPreset
from kdv.llm.client import LLMClient, LLMError
from kdv.viz.column_stats import summarize_columns

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """\
你是一名交互式数据分析助手。用户已经在分析结果页面，会通过对话向你提出后续要求：
新增图表、筛选数据洞察、调整分析角度等。每条消息都是一次独立任务。

【数据集元信息】
- 行数：{n_rows}
- 列名：{column_list}

【工作要点】
- 调用 `add_chart` 注册新图表，调用 `record_insight` 写洞察——这两类调用会实时
  反映到用户的结果页 UI 上。
- 通过 `aggregate` / `filter_rows` / `correlate` / `distinct_values` 等工具收集证据。
- 每条消息处理完毕后，必须调用 `finish_task` 给一段 Markdown 回答总结，
  即使用户的请求很简单。
- 不要重复添加已有图表（用户可能已经看见过）；如果不确定，先用 `list_columns`
  快速回顾。
- 用中文与用户对话，专业、简洁，引用具体数字。
"""


@dataclass
class SessionTurnResult:
    """What one user→agent turn produced."""

    user_text: str
    assistant_summary: str
    new_charts: list[ChartSpec]
    new_insights: list[Insight]
    events: list[TraceEvent]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    aborted_reason: str = ""


@dataclass
class AgentSession:
    """A live conversation bound to a dataset. Reusable across many user turns."""

    preset: LLMPreset
    api_key: str
    columns: list[str]
    rows: list[dict[str, Any]]
    registry: ToolRegistry = field(default_factory=build_default_registry)
    max_steps_per_turn: int = 10

    _ctx: ConversationContext | None = None
    _tool_ctx: ToolContext | None = None

    def __post_init__(self) -> None:
        stats = summarize_columns(self.columns, self.rows)
        self._tool_ctx = ToolContext(columns=self.columns, rows=self.rows, stats=stats)
        self._ctx = ConversationContext(
            model=self.preset.model,
            output_reserve_tokens=max(2048, self.preset.max_tokens),
            keep_recent=10,
        )
        self._ctx.add_system(
            SYSTEM_PROMPT_TEMPLATE.format(
                n_rows=len(self.rows),
                column_list=", ".join(self.columns) or "(无)",
            )
        )

    @property
    def all_charts(self) -> list[ChartSpec]:
        return list(self._tool_ctx.charts) if self._tool_ctx else []

    @property
    def all_insights(self) -> list[Insight]:
        return list(self._tool_ctx.insights) if self._tool_ctx else []

    async def turn(
        self,
        user_text: str,
        *,
        on_event: Callable[[TraceEvent], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> SessionTurnResult:
        assert self._ctx is not None and self._tool_ctx is not None

        events: list[TraceEvent] = []

        def emit(e: TraceEvent) -> None:
            events.append(e)
            if on_event is not None:
                try:
                    on_event(e)
                except Exception:
                    logger.exception("on_event raised")

        before_charts = len(self._tool_ctx.charts)
        before_insights = len(self._tool_ctx.insights)

        self._ctx.add_user(user_text)
        emit(TraceEvent(kind="task_start", text=user_text))

        prompt_tokens = 0
        completion_tokens = 0
        summary = ""
        abort_reason = ""

        async with LLMClient(self.preset, self.api_key) as client:
            async def _summarize_async(text: str) -> str:
                resp = await client.chat(
                    system_prompt="Summarize agent history concisely.",
                    user_prompt=text,
                    schema_fields=None,
                    temperature=0,
                    max_tokens=600,
                )
                return resp.text.strip()

            for step in range(self.max_steps_per_turn):
                if cancel_event is not None and cancel_event.is_set():
                    abort_reason = "cancelled"
                    break

                if self._ctx.needs_compaction():
                    emit(TraceEvent(kind="compaction",
                                    text=f"上下文 {self._ctx.total_tokens()} tokens 接近预算，压缩中…"))
                    try:
                        await self._ctx.compact_async(_summarize_async)
                    except Exception:
                        logger.exception("compaction failed")

                try:
                    resp = await client.chat_with_tools(
                        messages=self._ctx.to_openai(),
                        tools=self.registry.to_openai_specs(),
                    )
                except LLMError as e:
                    abort_reason = f"llm_error: {e}"
                    emit(TraceEvent(kind="error", text=str(e)))
                    break
                except Exception as e:  # noqa: BLE001
                    abort_reason = f"unexpected: {e}"
                    logger.exception("agent step crashed")
                    emit(TraceEvent(kind="error", text=str(e)))
                    break

                prompt_tokens += resp.prompt_tokens
                completion_tokens += resp.completion_tokens

                self._ctx.add_assistant(resp.text or "", tool_calls=resp.tool_calls or [])

                if resp.text and not resp.tool_calls:
                    emit(TraceEvent(kind="thought", text=resp.text[:1000]))

                if not resp.tool_calls:
                    self._ctx.add_user(
                        "请用 `finish_task` 立即结束本次回答（提供 Markdown 总结）。"
                    )
                    continue

                finished = False
                for tc in resp.tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        if not isinstance(args, dict):
                            args = {}
                    except json.JSONDecodeError:
                        args = {}
                    emit(TraceEvent(kind="tool_call", text=name, detail={"args": args}))

                    tool = self.registry.get(name)
                    if tool is None:
                        result = f"工具 `{name}` 不存在"
                    else:
                        try:
                            result = tool.handler(self._tool_ctx, args)
                        except Exception as e:  # noqa: BLE001
                            logger.exception("tool %s raised", name)
                            result = f"工具异常：{e}"

                    self._ctx.add_tool_result(
                        tool_call_id=tc.get("id", ""),
                        name=name,
                        content=result,
                    )
                    emit(TraceEvent(kind="tool_result", text=result[:400],
                                    detail={"tool": name}))

                    if name == "finish_task":
                        summary = args.get("summary", "") or result
                        finished = True
                        break

                if finished:
                    break
            else:
                abort_reason = "step_limit"

            if not summary:
                try:
                    resp = await client.chat(
                        system_prompt="Provide a brief Markdown answer summary.",
                        user_prompt="基于已收集的信息给出简短 Markdown 回答。",
                        schema_fields=None,
                        max_tokens=600,
                    )
                    summary = resp.text.strip()
                except Exception:
                    summary = "（未生成有效回答）"

        new_charts = self._tool_ctx.charts[before_charts:]
        new_insights = self._tool_ctx.insights[before_insights:]
        emit(TraceEvent(kind="task_end", text=summary,
                        detail={"new_charts": len(new_charts),
                                "new_insights": len(new_insights),
                                "abort": abort_reason}))

        return SessionTurnResult(
            user_text=user_text,
            assistant_summary=summary,
            new_charts=new_charts,
            new_insights=new_insights,
            events=events,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            aborted_reason=abort_reason,
        )
