"""Agent main loop: plan → tool call → observe, with context compaction.

A single AgentRunner can process several user-defined tasks in sequence,
sharing the same conversation context so insights from earlier tasks inform
later ones. The model decides when each task is done by calling the
`finish_task` tool.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from kdv.agent.context import ConversationContext
from kdv.agent.tools import (
    ChartSpec,
    Insight,
    Tool,
    ToolContext,
    ToolRegistry,
    build_default_registry,
)
from kdv.agent.trace import TraceEvent
from kdv.config.models import LLMPreset
from kdv.llm.client import LLMClient, LLMError
from kdv.llm.tokens import context_window_for, estimate_tokens
from kdv.viz.column_stats import summarize_columns

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """\
你是一名严谨的数据分析智能体（Agent），可以调用工具来探索和分析用户提供的结构化数据集。

【数据集元信息】
- 行数：{n_rows}
- 列名：{column_list}

【你的工作流程（必须遵守）】
1. 收到任务后，先用 `list_columns` 获取 schema 全貌（如果尚未获取）。
2. 用 `sample_rows` 看几行真实数据，建立直觉。
3. 通过组合 `describe_column` / `aggregate` / `filter_rows` / `correlate` /
   `distinct_values` / `text_search` 收集证据。
4. 把发现保存为图表（`add_chart`）和洞察（`record_insight`），让结果页可视化。
5. 任务完成时调用 `finish_task`，给出 Markdown 总结。

【准则】
- 一次只调用一个工具；不要凭空臆造数据，所有结论必须来自工具返回值。
- 工具返回越简短越好——你之后还会处理多个任务，注意 token 预算。
- 当用户给的任务比较模糊时，自行拆解为可验证的子问题。
- 优先调用 `add_chart` 和 `record_insight` 把发现固化下来；最终的 finish_task
  summary 仅是收尾。
"""


@dataclass
class AgentTrace:
    events: list[TraceEvent] = field(default_factory=list)

    def add(self, e: TraceEvent) -> None:
        self.events.append(e)

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events]


@dataclass
class AgentResult:
    run_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    charts: list[ChartSpec]
    insights: list[Insight]
    task_summaries: list[dict[str, str]]   # [{"task": str, "summary": str}]
    trace: AgentTrace
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    duration_ms_total: int = 0
    cancelled: bool = False
    aborted_reason: str = ""


class AgentRunner:
    def __init__(
        self,
        *,
        preset: LLMPreset,
        api_key: str,
        registry: ToolRegistry | None = None,
        max_steps_per_task: int = 16,
    ) -> None:
        self.preset = preset
        self.api_key = api_key
        self.registry = registry or build_default_registry()
        self.max_steps_per_task = max_steps_per_task

    async def run(
        self,
        *,
        columns: list[str],
        rows: list[dict[str, Any]],
        tasks: list[str],
        on_event: Callable[[TraceEvent], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AgentResult:
        run_id = uuid.uuid4().hex[:16]
        trace = AgentTrace()

        def emit(e: TraceEvent) -> None:
            trace.add(e)
            if on_event is not None:
                try:
                    on_event(e)
                except Exception:
                    logger.exception("on_event handler raised")

        stats = summarize_columns(columns, rows)
        tool_ctx = ToolContext(columns=columns, rows=rows, stats=stats)

        ctx = ConversationContext(
            model=self.preset.model,
            output_reserve_tokens=max(2048, self.preset.max_tokens),
            keep_recent=8,
        )
        ctx.add_system(
            SYSTEM_PROMPT_TEMPLATE.format(
                n_rows=len(rows),
                column_list=", ".join(columns) or "(无)",
            )
        )

        loop = asyncio.get_event_loop()
        t_start = loop.time()
        prompt_tokens = 0
        completion_tokens = 0
        task_summaries: list[dict[str, str]] = []

        async with LLMClient(self.preset, self.api_key) as client:
            async def _summarize(text: str) -> str:
                resp = await client.chat(
                    system_prompt="You summarize agent conversation history concisely in Chinese.",
                    user_prompt=text,
                    schema_fields=None,
                    temperature=0,
                    max_tokens=600,
                )
                return resp.text.strip()

            for task_idx, task in enumerate(tasks):
                if cancel_event is not None and cancel_event.is_set():
                    return self._make_result(
                        run_id, columns, rows, tool_ctx, task_summaries,
                        trace, prompt_tokens, completion_tokens,
                        int((loop.time() - t_start) * 1000),
                        cancelled=True,
                    )

                emit(TraceEvent(kind="task_start", text=task,
                                detail={"index": task_idx, "total": len(tasks)}))

                ctx.add_user(f"# 任务 {task_idx + 1}/{len(tasks)}\n\n{task}\n\n请按工作流程开始。")

                steps_used, pt, ct, summary, abort_reason = await self._run_one_task(
                    client=client,
                    ctx=ctx,
                    tool_ctx=tool_ctx,
                    emit=emit,
                    cancel_event=cancel_event,
                    async_summarizer=_summarize,
                )
                prompt_tokens += pt
                completion_tokens += ct
                task_summaries.append({"task": task, "summary": summary})
                emit(TraceEvent(
                    kind="task_end",
                    text=summary,
                    detail={"steps": steps_used, "abort": abort_reason},
                ))

                if abort_reason and "step_limit" not in abort_reason:
                    # If aborted for hard reason (token blowup, repeated errors), stop entire run.
                    return self._make_result(
                        run_id, columns, rows, tool_ctx, task_summaries,
                        trace, prompt_tokens, completion_tokens,
                        int((loop.time() - t_start) * 1000),
                        cancelled=False, aborted_reason=abort_reason,
                    )

        return self._make_result(
            run_id, columns, rows, tool_ctx, task_summaries,
            trace, prompt_tokens, completion_tokens,
            int((loop.time() - t_start) * 1000),
        )

    async def _run_one_task(
        self,
        *,
        client: LLMClient,
        ctx: ConversationContext,
        tool_ctx: ToolContext,
        emit: Callable[[TraceEvent], None],
        cancel_event: asyncio.Event | None,
        async_summarizer: Callable[[str], Any],
    ) -> tuple[int, int, int, str, str]:
        prompt_tokens = 0
        completion_tokens = 0
        steps = 0
        summary = ""
        abort_reason = ""

        for step in range(self.max_steps_per_task):
            steps = step + 1
            if cancel_event is not None and cancel_event.is_set():
                abort_reason = "cancelled"
                break

            # ---- compact if needed --------------------------------------
            if ctx.needs_compaction():
                emit(TraceEvent(kind="compaction",
                                text=f"上下文 {ctx.total_tokens()} tokens 接近预算，压缩中…"))
                # Try compaction with retries on transient errors. If it
                # ultimately fails, just continue with un-compacted context
                # — the next chat call may still succeed if the network
                # blip clears up.
                for cc_attempt in range(1, 4):
                    try:
                        await ctx.compact_async(async_summarizer)
                        break
                    except Exception as e:  # noqa: BLE001
                        if cc_attempt == 3:
                            logger.exception("compaction failed after retries")
                            emit(TraceEvent(kind="error",
                                            text=f"压缩失败（已重试 3 次）：{e}"))
                            break
                        emit(TraceEvent(
                            kind="error",
                            text=f"压缩失败重试中（第 {cc_attempt} 次）：{str(e)[:80]}",
                        ))
                        await asyncio.sleep(2 * cc_attempt)

            # ---- LLM call (with extra outer retry for transient blips) -
            resp = None
            outer_attempts = 4
            transient_error: Exception | None = None
            for attempt in range(1, outer_attempts + 1):
                try:
                    resp = await client.chat_with_tools(
                        messages=ctx.to_openai(),
                        tools=self.registry.to_openai_specs(),
                        temperature=self.preset.temperature,
                        max_tokens=self.preset.max_tokens,
                    )
                    transient_error = None
                    break
                except LLMError as e:
                    abort_reason = f"llm_error: {e}"
                    emit(TraceEvent(kind="error", text=str(e)))
                    transient_error = None
                    resp = None
                    break
                except Exception as e:  # noqa: BLE001
                    transient_error = e
                    msg = str(e)
                    looks_transient = any(
                        s in msg for s in ("SSL", "网络", "超时", "TimeoutException", "RemoteProtocol", "OSError")
                    )
                    if not looks_transient or attempt == outer_attempts:
                        # give up
                        abort_reason = f"unexpected: {e}"
                        logger.exception("agent step crashed (final)")
                        emit(TraceEvent(kind="error", text=str(e)))
                        break
                    logger.warning(
                        "agent step transient error %d/%d: %s; backing off",
                        attempt, outer_attempts, e,
                    )
                    emit(TraceEvent(
                        kind="error",
                        text=f"传输错误（第 {attempt} 次重试）：{msg[:120]}",
                    ))
                    await asyncio.sleep(min(2 ** attempt, 12))
            if resp is None:
                # we already emitted the error in the loop; break the step loop
                break

            prompt_tokens += resp.prompt_tokens
            completion_tokens += resp.completion_tokens

            # ---- record assistant turn ----------------------------------
            ctx.add_assistant(resp.text or "", tool_calls=resp.tool_calls or [])

            if resp.text and not resp.tool_calls:
                emit(TraceEvent(kind="thought", text=resp.text[:600]))

            # ---- handle tool calls --------------------------------------
            if not resp.tool_calls:
                # No tool call AND no finish_task → push it to wrap up.
                ctx.add_user(
                    "请用 `finish_task` 给出本任务的 Markdown 结论摘要并结束当前任务。"
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

                emit(TraceEvent(kind="tool_call", text=name,
                                detail={"args": args}))

                tool: Tool | None = self.registry.get(name)
                if tool is None:
                    result = f"工具 `{name}` 不存在。可用：{[t.name for t in self.registry.all()]}"
                else:
                    try:
                        result = tool.handler(tool_ctx, args)
                    except Exception as e:  # noqa: BLE001
                        logger.exception("tool %s raised", name)
                        result = f"工具调用异常：{e}"

                ctx.add_tool_result(
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
            ctx.add_user(
                f"（已用满 {self.max_steps_per_task} 步预算，请立刻调用 `finish_task` 总结。）"
            )

        if not summary:
            # Fall back: ask one more time for a finish summary
            try:
                resp = await client.chat(
                    system_prompt="基于已收集的信息直接给出 Markdown 任务结论。",
                    user_prompt="请用一段简短 Markdown 总结当前任务的发现。",
                    schema_fields=None,
                    max_tokens=600,
                )
                summary = resp.text.strip()
            except Exception:
                summary = "（未能生成有效总结）"

        return steps, prompt_tokens, completion_tokens, summary, abort_reason

    def _make_result(
        self,
        run_id, columns, rows, tool_ctx, task_summaries, trace,
        prompt_tokens, completion_tokens, dur_ms, *,
        cancelled: bool = False, aborted_reason: str = "",
    ) -> AgentResult:
        return AgentResult(
            run_id=run_id,
            columns=columns,
            rows=rows,
            charts=list(tool_ctx.charts),
            insights=list(tool_ctx.insights),
            task_summaries=task_summaries,
            trace=trace,
            prompt_tokens_total=prompt_tokens,
            completion_tokens_total=completion_tokens,
            duration_ms_total=dur_ms,
            cancelled=cancelled,
            aborted_reason=aborted_reason,
        )
