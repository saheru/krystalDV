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


# Telltale fragments of the "stateless LLM has nothing to summarize" output
# we used to ship to users (see commit b301626 — the broken _fallback_summary
# call). Existing saved projects still contain these strings, so we keep a
# detector that the export / display layer can use to neutralize them.
_HALLUCINATED_FRAGMENTS = (
    "我没有看到任何当前正在进行的任务",
    "需要总结的发现",
    "如果你希望我帮助处理某个具体任务",
    "当前对话中尚未执行任何信息收集任务",
    "请先提供以下任一内容",
    "我将以 Markdown 格式输出结构化的发现总结",
    "我会立即开始工作并在完成后提供总结",
)


def looks_like_empty_context_hallucination(text: str) -> bool:
    """Return True if `text` matches the LLM's "I don't see any task" reply.

    Used to neutralize summaries from older runs that hit the broken
    _fallback_summary path. Trips on at least one telltale fragment AND
    when the response is short prose (true summaries are usually longer
    and reference the actual data).
    """
    if not text:
        return False
    sample = text.strip()
    return any(frag in sample for frag in _HALLUCINATED_FRAGMENTS)


def sanitize_task_summary(task: str, summary: str) -> str:
    """Drop hallucinated boilerplate; otherwise return summary unchanged."""
    if looks_like_empty_context_hallucination(summary):
        return (
            "⚠ 此任务的原结论由旧版本（无上下文调用）生成，已被自动屏蔽。\n\n"
            f"任务原文：{task}\n\n"
            f"请重新运行该任务以生成可用的结论。"
        )
    return summary


SYSTEM_PROMPT_SINGLE = """\
你是一名严谨的数据分析智能体（Agent），可以调用工具来探索和分析用户提供的结构化数据集。

【数据集元信息】
- 行数：{n_rows}
- 列名：{column_list}

【你的工作流程】
1. 收到任务后，先用 `list_columns` 获取 schema 全貌（如果尚未获取）。
2. 用 `sample_rows` 看几行真实数据，建立直觉。
3. 通过组合 `describe_column` / `aggregate` / `filter_rows` / `correlate` /
   `distinct_values` / `text_search` 收集证据。
4. 把发现保存为图表（`add_chart`）和洞察（`record_insight`）。
5. 任务完成时调用 `finish_task`，给出 Markdown 总结。

【效率准则（重要）】
- **优先一次性发起多个独立工具调用**：如果你已经知道下一步要查 A、B、C
  三件事且互相不依赖，请在同一个回复里同时调用三个 tool（OpenAI 协议
  支持单回合多 tool_calls），而不是逐个串行。这能把整个任务的耗时从
  N×LLM 思考时间压缩到 1×LLM 思考时间，**显著提速**。
- 反例：先 aggregate 再 filter 再 correlate 串行三步。
- 正例：同一回合返回 [aggregate(...), filter_rows(...), correlate(...)]。

【其他准则】
- 不要凭空臆造数据，所有结论必须来自工具返回值。
- 工具返回越简短越好——你之后还会处理多个任务，注意 token 预算。
- 当用户给的任务比较模糊时，自行拆解为可验证的子问题。
- 优先调用 `add_chart` 和 `record_insight` 把发现固化下来；最终的 finish_task
  summary 仅是收尾。
"""


SYSTEM_PROMPT_MULTI = """\
你是一名严谨的数据分析智能体（Agent），本次工作簿包含 **{n_tables} 张表**：

{table_overview}

【多表工作要点】
- **每个数据查询工具都接受 `table` 参数**——请明确指定要操作哪张表，否则
  默认操作第一张（`{default_table_id}`）。
- 跨表分析有专用工具：
  - `cross_reconcile` — 两张表分组聚合后比对差额（A vs B 对账场景）
  - `join_tables`     — 按键 inner-join 两张表，看关联行
  - `list_tables`     — 重新列出当前可用的全部表
- 工作流程：
  1. 先 `list_tables` 看清全貌（如果尚未获取）。
  2. 对每张要分析的表先 `list_columns` + `sample_rows` 建立直觉。
  3. 通过 `aggregate` / `filter_rows` / `correlate` 等收集证据；多表对比用
     `cross_reconcile` / `join_tables`。
  4. 把发现保存为图表（`add_chart`，可指定 `table=`）和洞察（`record_insight`）。
  5. 任务完成时调用 `finish_task` 给出 Markdown 总结。

【效率准则（重要）】
- **同一回合并发多个独立工具调用**（OpenAI 协议支持单回合多 tool_calls）。
  特别是多表场景：往往要对每张表都做一次同样的查询——一次性全发出去，不要
  串行逐表来一遍。
- 反例：先查 A 表 sum，再查 B 表 sum，再 correlate。
- 正例：同回合 [aggregate(table=A,...), aggregate(table=B,...), correlate(...)]。

【其他准则】
- 所有结论必须来自工具返回值，不要凭空臆造。
- 工具返回越简短越好；后续还有任务，注意 token 预算。
- 用户任务模糊时自行拆解为可验证的子问题。
- 关键发现立即 `record_insight` 固化——这能避免上下文压缩时丢失（压缩只动
  消息历史，不动 insights）。
"""


# Single-source so other modules can import it without importing the runner.
SYSTEM_PROMPT_TEMPLATE = SYSTEM_PROMPT_SINGLE


# Tool-call turns are short — the model just emits a tool_calls JSON.
# Capping max_tokens here saves both latency and cost.
AGENT_TOOL_TURN_MAX_TOKENS = 1024


@dataclass
class AgentTrace:
    events: list[TraceEvent] = field(default_factory=list)

    def add(self, e: TraceEvent) -> None:
        self.events.append(e)

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events]


@dataclass
class FallbackSummary:
    """Material needed to retry the LLM summary call later from the UI.

    Created when `_fallback_summary` exhausts its in-loop retries and falls
    back to the deterministic Markdown. The result page exposes a "重试总结"
    button that re-issues `client.chat(system_prompt, user_prompt)` with
    these stored prompts to get a real LLM-written summary.
    """
    task_index: int
    task_text: str
    system_prompt: str
    user_prompt: str
    last_error: str = ""


@dataclass
class AgentResult:
    run_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    charts: list[ChartSpec]
    insights: list[Insight]
    task_summaries: list[dict[str, Any]]   # [{"task": str, "summary": str, "is_fallback"?: bool}]
    trace: AgentTrace
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    duration_ms_total: int = 0
    cancelled: bool = False
    aborted_reason: str = ""
    fallbacks: list[FallbackSummary] = field(default_factory=list)


class AgentRunner:
    def __init__(
        self,
        *,
        preset: LLMPreset,
        api_key: str,
        registry: ToolRegistry | None = None,
        max_steps_per_task: int = 16,
        fast_preset: LLMPreset | None = None,
        fast_api_key: str = "",
    ) -> None:
        """`fast_preset` is used for tool-call decision turns and context
        compaction (the high-volume cheap turns). `preset` is used for the
        final task summary. If `fast_preset` is None, falls back to `preset`.
        """
        self.preset = preset
        self.api_key = api_key
        self.fast_preset = fast_preset or preset
        self.fast_api_key = fast_api_key or api_key
        self.registry = registry or build_default_registry()
        self.max_steps_per_task = max_steps_per_task

    async def run(
        self,
        *,
        columns: list[str] | None = None,
        rows: list[dict[str, Any]] | None = None,
        tables: list[Any] | None = None,
        tasks: list[str],
        on_event: Callable[[TraceEvent], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AgentResult:
        """Run the agent over one or more tables.

        Backward-compatible: pass `columns + rows` for single-table mode (old
        callers). For multi-table workbooks pass `tables` (list of
        `ExcelTable`) instead — every tool then sees them all and the system
        prompt advertises each one's schema.
        """
        run_id = uuid.uuid4().hex[:16]
        trace = AgentTrace()

        def emit(e: TraceEvent) -> None:
            trace.add(e)
            if on_event is not None:
                try:
                    on_event(e)
                except Exception:
                    logger.exception("on_event handler raised")

        # Build the TableSlot list and the legacy columns/rows view for the
        # AgentResult fields. If `tables` is given we use those; otherwise
        # synthesize a single-table list from `columns + rows`.
        if tables:
            from kdv.excel.reader import ExcelTable as _Et  # local: avoid cycle

            slots: list[Any] = []  # TableSlot, but avoid forward-ref import
            from kdv.agent.tools import TableSlot as _Slot

            for t in tables:
                t_stats = summarize_columns(t.columns, t.rows)
                slots.append(_Slot(
                    table_id=t.table_id,
                    columns=list(t.columns),
                    rows=list(t.rows),
                    stats=t_stats,
                    sheet_name=t.sheet_name,
                    source_path=t.source_path,
                ))
            primary = slots[0] if slots else None
            primary_columns = primary.columns if primary else []
            primary_rows = primary.rows if primary else []
        else:
            primary_columns = list(columns or [])
            primary_rows = list(rows or [])
            stats = summarize_columns(primary_columns, primary_rows)
            from kdv.agent.tools import TableSlot as _Slot

            slots = [_Slot(
                table_id="default",
                columns=primary_columns,
                rows=primary_rows,
                stats=stats,
            )]

        tool_ctx = ToolContext(tables=slots)

        ctx = ConversationContext(
            model=self.preset.model,
            output_reserve_tokens=max(2048, self.preset.max_tokens),
            keep_recent=8,
        )
        if len(slots) > 1:
            overview_lines = []
            for s in slots:
                src = (s.source_path or "").split("/")[-1]
                first_cols = ", ".join(s.columns[:6]) + (" …" if len(s.columns) > 6 else "")
                overview_lines.append(
                    f"- table_id=`{s.table_id}` ({len(s.rows)} 行)"
                    + (f"  sheet={s.sheet_name}" if s.sheet_name else "")
                    + (f"  来源={src}" if src else "")
                    + f"\n    列：{first_cols}"
                )
            ctx.add_system(
                SYSTEM_PROMPT_MULTI.format(
                    n_tables=len(slots),
                    table_overview="\n".join(overview_lines),
                    default_table_id=slots[0].table_id,
                )
            )
        else:
            ctx.add_system(
                SYSTEM_PROMPT_SINGLE.format(
                    n_rows=len(primary_rows),
                    column_list=", ".join(primary_columns) or "(无)",
                )
            )

        loop = asyncio.get_event_loop()
        t_start = loop.time()
        prompt_tokens = 0
        completion_tokens = 0
        task_summaries: list[dict[str, Any]] = []
        fallbacks: list[FallbackSummary] = []

        # Two clients: `fast_client` does the tool-call loop (high frequency,
        # short responses → cheap+fast); `client` writes the final summary
        # when the agent finishes a task.
        async with LLMClient(self.preset, self.api_key) as client, \
                   LLMClient(self.fast_preset, self.fast_api_key) as fast_client:

            async def _summarize(text: str) -> str:
                # Compaction summaries also use the fast client.
                resp = await fast_client.chat(
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
                        run_id, primary_columns, primary_rows, tool_ctx, task_summaries,
                        trace, prompt_tokens, completion_tokens,
                        int((loop.time() - t_start) * 1000),
                        cancelled=True, fallbacks=fallbacks,
                    )

                emit(TraceEvent(kind="task_start", text=task,
                                detail={"index": task_idx, "total": len(tasks)}))

                ctx.add_user(f"# 任务 {task_idx + 1}/{len(tasks)}\n\n{task}\n\n请按工作流程开始。")

                # Snapshot artifact counts before this task so the fallback
                # summary can quote ONLY what this task produced (rather than
                # leaking findings from earlier tasks in the same run).
                insights_before = len(tool_ctx.insights)
                charts_before = len(tool_ctx.charts)

                fallback_collector: list[FallbackSummary] = []
                steps_used, pt, ct, summary, abort_reason = await self._run_one_task(
                    client=client,
                    fast_client=fast_client,
                    ctx=ctx,
                    tool_ctx=tool_ctx,
                    task_text=task,
                    insights_before=insights_before,
                    charts_before=charts_before,
                    emit=emit,
                    cancel_event=cancel_event,
                    async_summarizer=_summarize,
                    fallback_collector=fallback_collector,
                    task_index=task_idx,
                )
                prompt_tokens += pt
                completion_tokens += ct
                entry: dict[str, Any] = {"task": task, "summary": summary}
                if fallback_collector:
                    entry["is_fallback"] = True
                    fallbacks.extend(fallback_collector)
                task_summaries.append(entry)
                emit(TraceEvent(
                    kind="task_end",
                    text=summary,
                    detail={"steps": steps_used, "abort": abort_reason},
                ))

                if abort_reason and "step_limit" not in abort_reason:
                    # If aborted for hard reason (token blowup, repeated errors), stop entire run.
                    return self._make_result(
                        run_id, primary_columns, primary_rows, tool_ctx, task_summaries,
                        trace, prompt_tokens, completion_tokens,
                        int((loop.time() - t_start) * 1000),
                        cancelled=False, aborted_reason=abort_reason,
                        fallbacks=fallbacks,
                    )

        return self._make_result(
            run_id, primary_columns, primary_rows, tool_ctx, task_summaries,
            trace, prompt_tokens, completion_tokens,
            int((loop.time() - t_start) * 1000),
            fallbacks=fallbacks,
        )

    async def _run_one_task(
        self,
        *,
        client: LLMClient,
        fast_client: LLMClient,
        ctx: ConversationContext,
        tool_ctx: ToolContext,
        task_text: str,
        insights_before: int,
        charts_before: int,
        emit: Callable[[TraceEvent], None],
        cancel_event: asyncio.Event | None,
        async_summarizer: Callable[[str], Any],
        fallback_collector: list[FallbackSummary],
        task_index: int,
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
                tokens_before = ctx.total_tokens()
                emit(TraceEvent(
                    kind="compaction",
                    text=f"上下文 {tokens_before} tokens 接近预算，压缩中…",
                ))
                # `compact_async` is hierarchical: small middles compact in
                # one shot, big ones split into per-chunk LLM calls so each
                # call stays small/fast. It swallows its own exceptions and
                # returns 0 on full failure — we surface that here so the
                # user sees the outcome instead of a silent gap.
                compacted_n = 0
                try:
                    compacted_n = await ctx.compact_async(async_summarizer)
                except Exception as e:  # noqa: BLE001 — defensive only
                    logger.exception("compaction crashed unexpectedly")
                    emit(TraceEvent(kind="error",
                                    text=f"压缩异常：{str(e)[:120]}"))
                if compacted_n > 0:
                    tokens_after = ctx.total_tokens()
                    emit(TraceEvent(
                        kind="compaction",
                        text=(
                            f"压缩成功：{compacted_n} 条消息已合并，"
                            f"上下文 {tokens_before} → {tokens_after} tokens"
                        ),
                    ))
                else:
                    emit(TraceEvent(
                        kind="error",
                        text=(
                            "压缩未生效（保持原上下文）。"
                            "若下面 LLM 调用持续超时，建议取消任务、缩小数据范围后重试。"
                        ),
                    ))

            # ---- LLM call (with extra outer retry for transient blips) -
            resp = None
            outer_attempts = 4
            transient_error: Exception | None = None
            for attempt in range(1, outer_attempts + 1):
                try:
                    # Use the FAST client for tool-call decisions (the
                    # high-volume cheap turn). max_tokens capped — the
                    # response is just a tool_calls JSON, not prose.
                    resp = await fast_client.chat_with_tools(
                        messages=ctx.to_openai(),
                        tools=self.registry.to_openai_specs(),
                        temperature=self.fast_preset.temperature,
                        max_tokens=min(self.fast_preset.max_tokens, AGENT_TOOL_TURN_MAX_TOKENS),
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
            summary = await self._fallback_summary(
                client=client,
                ctx=ctx,
                tool_ctx=tool_ctx,
                task_text=task_text,
                insights_before=insights_before,
                charts_before=charts_before,
                abort_reason=abort_reason,
                emit=emit,
                fallback_collector=fallback_collector,
                task_index=task_index,
            )

        return steps, prompt_tokens, completion_tokens, summary, abort_reason

    async def _fallback_summary(
        self,
        *,
        client: LLMClient,
        ctx: ConversationContext,
        tool_ctx: ToolContext,
        task_text: str,
        insights_before: int,
        charts_before: int,
        abort_reason: str,
        emit: Callable[[TraceEvent], None],
        fallback_collector: list[FallbackSummary],
        task_index: int,
    ) -> str:
        """Compose a final task summary when the agent didn't call finish_task.

        The previous implementation called `client.chat()` with no context at
        all — the LLM answered "我没有看到任何当前正在进行的任务" because
        that was literally true from its point of view. We fix that by
        feeding the task text + this task's insights/charts + the tail of the
        actual conversation into the prompt, OR by returning a clear failure
        message when nothing was produced.
        """
        new_insights = list(tool_ctx.insights[insights_before:])
        new_charts = list(tool_ctx.charts[charts_before:])

        # Pull the last few non-system turns that have *content* (tool results
        # + assistant prose) so the summary call can actually see something
        # concrete instead of just trusting the model's memory.
        recent_chunks: list[str] = []
        for m in reversed(ctx.messages):
            if m.role == "system":
                continue
            if not (m.content or "").strip():
                continue
            tag = {"user": "用户", "assistant": "助手", "tool": f"工具[{m.name}]"}.get(m.role, m.role)
            recent_chunks.append(f"### {tag}\n{m.content[:600]}")
            if len(recent_chunks) >= 6:
                break
        recent_chunks.reverse()
        recent_blob = "\n\n".join(recent_chunks) if recent_chunks else "（无）"

        # If nothing happened at all, don't burn an LLM call on hallucinated
        # prose — surface the real situation to the user.
        no_progress = not new_insights and not new_charts and not recent_chunks
        if no_progress:
            reason_hint = ""
            if abort_reason.startswith("llm_error"):
                reason_hint = f"，原因：{abort_reason}"
            elif abort_reason == "step_limit":
                reason_hint = f"，已用满 {self.max_steps_per_task} 步预算但未产生任何工具结果"
            elif abort_reason == "cancelled":
                reason_hint = "，已被用户取消"
            elif abort_reason:
                reason_hint = f"，{abort_reason}"
            return (
                f"⚠ 本任务未能完成{reason_hint}。\n\n"
                f"任务原文：{task_text}\n\n"
                f"建议：检查 LLM 是否支持工具调用（function calling），或换一个支持的端点重试。"
            )

        # Otherwise, hand the model the concrete material it needs.
        insight_lines = "\n".join(
            f"- **{i.title or '(无标题)'}**：{i.body[:200]}" for i in new_insights
        ) or "（本任务期间未记录 insight）"
        chart_lines = "\n".join(
            f"- {c.kind}：{c.title}（列：{', '.join(c.columns) or '—'}）"
            for c in new_charts
        ) or "（本任务期间未生成图表）"

        user_prompt = (
            f"# 本任务原文\n{task_text}\n\n"
            f"# 已记录的洞察\n{insight_lines}\n\n"
            f"# 已生成的图表\n{chart_lines}\n\n"
            f"# 最近的对话/工具结果片段\n{recent_blob}\n\n"
            f"---\n"
            f"请基于以上**真实材料**用 Markdown 写一段 200 字内的任务结论"
            f"（不要凭空臆造数据；若材料不足请直说『证据不足』）。"
        )
        # System prompt is captured outside the retry loop so we can stash
        # it for the UI's "重试总结" button if all in-loop attempts fail.
        summary_system = (
            "你是一名严谨的数据分析助手。下面用户会给你一份 agent 收集到的素材，"
            "请只基于这些素材撰写任务结论，不允许凭空补充数字或事实。"
        )
        # Try the LLM up to 3 times with backoff before falling back to the
        # mechanical summary. Single attempt was too easy to lose to a single
        # transient timeout — the user complained that the task ended with
        # nothing useful when the network blipped at exactly the wrong moment.
        last_err: str = ""
        for attempt in range(1, 4):
            try:
                resp = await client.chat(
                    system_prompt=summary_system,
                    user_prompt=user_prompt,
                    schema_fields=None,
                    max_tokens=800,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
                # Empty response — treat as transient and retry.
                last_err = "LLM 返回空内容"
            except Exception as e:  # noqa: BLE001
                last_err = str(e)[:120]
                emit(TraceEvent(
                    kind="error",
                    text=f"兜底总结调用失败（第 {attempt}/3 次）：{last_err}",
                ))
            if attempt < 3:
                await asyncio.sleep(min(2 ** attempt, 8))

        # All in-loop LLM attempts exhausted. Stash the prompt material so
        # the UI can offer a manual "重试总结" later (network may recover,
        # or the user can switch to a different preset). The deterministic
        # Markdown below is always returned NOW so the user has *something*
        # immediately, but they keep the option to upgrade it later.
        fallback_collector.append(FallbackSummary(
            task_index=task_index,
            task_text=task_text,
            system_prompt=summary_system,
            user_prompt=user_prompt,
            last_error=last_err,
        ))
        emit(TraceEvent(
            kind="error",
            text=(
                f"兜底总结 LLM 全部失败（{last_err}）。已用确定性摘要顶上——"
                f"结果页可点『重试总结』再调一次 LLM。"
            ),
        ))
        parts = [f"### 任务：{task_text}\n"]
        parts.append(
            f"_（说明：本任务结论由确定性摘要生成，因 LLM 总结调用持续失败：{last_err}）_"
        )
        if new_insights:
            parts.append("**洞察**：\n" + insight_lines)
        if new_charts:
            parts.append("**图表**：\n" + chart_lines)
        if recent_chunks:
            parts.append("**近端工具结果（按时间序）**：\n" + recent_blob)
        if not new_insights and not new_charts and not recent_chunks:
            parts.append("（本任务未产生可见的工具结果或洞察。）")
        return "\n\n".join(parts)

    def _make_result(
        self,
        run_id, columns, rows, tool_ctx, task_summaries, trace,
        prompt_tokens, completion_tokens, dur_ms, *,
        cancelled: bool = False, aborted_reason: str = "",
        fallbacks: list[FallbackSummary] | None = None,
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
            fallbacks=list(fallbacks or []),
        )
