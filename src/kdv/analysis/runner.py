"""Top-level analysis orchestration.

Two modes:
- row_by_row: each input row is sent to the LLM in parallel, structured output
  is merged into a result dict aligned with the input row.
- summary: the full table (sampled) is sent in a single call, returning either
  a structured object (if output_fields given) or Markdown text.

Both can run together (mode = "both"): per-row first, then summary using inputs
plus per-row outputs as context.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from kdv.analysis.cache import RunCache, hash_row
from kdv.analysis.concurrency import TaskOutcome, run_bounded
from kdv.analysis.model import AnalysisModel
from kdv.config.models import LLMPreset
from kdv.llm.client import LLMClient, LLMError
from kdv.llm.prompts import SYSTEM_TEMPLATES, build_row_prompt, build_summary_prompt
from kdv.llm.tokens import context_window_for, estimate_tokens

logger = logging.getLogger(__name__)


Mode = Literal["row_by_row", "summary", "both"]


@dataclass
class RunProgress:
    completed: int
    total: int
    last_index: int
    last_error: str | None
    last_duration_ms: int
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0


@dataclass
class RunResult:
    run_id: str
    mode: Mode
    columns: list[str]
    rows: list[dict[str, Any]]
    row_outputs: list[dict[str, Any] | None]
    row_errors: list[str | None]
    summary_markdown: str | None
    summary_structured: dict[str, Any] | None
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    duration_ms_total: int = 0
    cancelled: bool = False
    column_to_field: dict[str, str] = field(default_factory=dict)


class AnalysisRunner:
    """Orchestrates a single analysis run."""

    def __init__(
        self,
        *,
        preset: LLMPreset,
        api_key: str,
        model: AnalysisModel | None,
        column_map: dict[str, str] | None = None,
        ad_hoc_goal: str = "",
    ) -> None:
        """`model` can be None for ad-hoc summary-only analysis.

        `ad_hoc_goal` is used when model is None to convey the user's question.
        """
        self.preset = preset
        self.api_key = api_key
        self.model = model
        self.column_map = column_map or {}
        self.ad_hoc_goal = ad_hoc_goal

    def _system_prompt(self) -> str:
        if self.model is None:
            return SYSTEM_TEMPLATES["general"]
        base = SYSTEM_TEMPLATES.get(self.model.system_template, SYSTEM_TEMPLATES["general"])
        if self.model.custom_system_prompt.strip():
            return f"{base}\n\n{self.model.custom_system_prompt.strip()}"
        return base

    def _analysis_goal(self) -> str:
        return (self.model.analysis_goal if self.model else "") or self.ad_hoc_goal

    def _output_fields(self):
        return self.model.output_fields if self.model else []

    def _model_id(self) -> str:
        return self.model.id if self.model else "ad_hoc"

    async def run(
        self,
        *,
        columns: list[str],
        rows: list[dict[str, Any]],
        mode: Mode = "row_by_row",
        on_progress: Callable[[RunProgress], None] | None = None,
        cancel_event: asyncio.Event | None = None,
        run_id: str | None = None,
        cache: RunCache | None = None,
    ) -> RunResult:
        run_id = run_id or uuid.uuid4().hex[:16]
        owns_cache = cache is None
        cache = cache or RunCache()

        # Ad-hoc analysis (no model) only supports summary mode.
        if self.model is None and mode != "summary":
            mode = "summary"

        loop = asyncio.get_event_loop()
        t_start = loop.time()
        try:
            cache.start_run(
                run_id,
                preset_id=self.preset.id,
                model_id=self._model_id(),
                total_rows=len(rows),
                mode=mode,
            )

            row_outputs: list[dict[str, Any] | None] = [None] * len(rows)
            row_errors: list[str | None] = [None] * len(rows)
            prompt_total = 0
            completion_total = 0

            cancelled = False

            if mode in ("row_by_row", "both"):
                async with LLMClient(self.preset, self.api_key) as client:
                    completed = 0

                    def _build_task(idx: int):
                        async def _do() -> dict[str, Any]:
                            row = rows[idx]
                            h = hash_row(row)
                            cached = cache.get_row(run_id, idx, h)
                            if cached is not None:
                                return {
                                    "output": cached,
                                    "prompt_tokens": 0,
                                    "completion_tokens": 0,
                                    "input_hash": h,
                                    "from_cache": True,
                                }
                            sys_p, user_p = build_row_prompt(
                                system_extra=self._system_prompt(),
                                analysis_goal=self._analysis_goal(),
                                schema_fields=self._output_fields(),
                                row=row,
                                use_function_calling=(
                                    self.preset.structured_mode != "prompt"
                                ),
                            )
                            resp = await client.chat(
                                system_prompt=sys_p,
                                user_prompt=user_p,
                                schema_fields=self._output_fields(),
                            )
                            if resp.parsed is None:
                                raise LLMError("LLM 未返回可解析的结构化结果")
                            return {
                                "output": resp.parsed,
                                "prompt_tokens": resp.prompt_tokens,
                                "completion_tokens": resp.completion_tokens,
                                "input_hash": h,
                                "from_cache": False,
                            }

                        return _do

                    def _on_progress(outcome: TaskOutcome) -> None:
                        nonlocal completed, prompt_total, completion_total
                        completed += 1
                        if outcome.error:
                            row_errors[outcome.index] = outcome.error
                            cache.put_row(
                                run_id,
                                outcome.index,
                                input_hash=hash_row(rows[outcome.index]),
                                output=None,
                                error=outcome.error,
                                duration_ms=outcome.duration_ms,
                            )
                        else:
                            payload = outcome.result or {}
                            row_outputs[outcome.index] = payload.get("output")
                            pt = int(payload.get("prompt_tokens", 0))
                            ct = int(payload.get("completion_tokens", 0))
                            prompt_total += pt
                            completion_total += ct
                            if not payload.get("from_cache"):
                                cache.put_row(
                                    run_id,
                                    outcome.index,
                                    input_hash=payload.get("input_hash", ""),
                                    output=payload.get("output"),
                                    error=None,
                                    prompt_tokens=pt,
                                    completion_tokens=ct,
                                    duration_ms=outcome.duration_ms,
                                )
                        if on_progress is not None:
                            on_progress(
                                RunProgress(
                                    completed=completed,
                                    total=len(rows),
                                    last_index=outcome.index,
                                    last_error=outcome.error,
                                    last_duration_ms=outcome.duration_ms,
                                    prompt_tokens_total=prompt_total,
                                    completion_tokens_total=completion_total,
                                )
                            )

                    tasks = [(i, _build_task(i)) for i in range(len(rows))]
                    await run_bounded(
                        tasks,
                        max_concurrency=self.preset.max_concurrency,
                        on_progress=_on_progress,
                        cancel_event=cancel_event,
                    )

                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True

            summary_md: str | None = None
            summary_struct: dict[str, Any] | None = None

            if mode in ("summary", "both") and not cancelled:
                merged_columns = list(columns)
                merged_rows: list[dict[str, Any]] = []
                out_field_names = [f.name for f in self._output_fields()]
                if mode == "both" and out_field_names:
                    for col in out_field_names:
                        merged_columns.append(f"分析:{col}")
                    for i, row in enumerate(rows):
                        m = dict(row)
                        out = row_outputs[i] or {}
                        for col in out_field_names:
                            m[f"分析:{col}"] = out.get(col, "")
                        merged_rows.append(m)
                else:
                    merged_rows = list(rows)

                summary_max = max(4096, self.preset.max_tokens)
                async with LLMClient(self.preset, self.api_key) as client:
                    try:
                        summary_md, pt, ct = await self._run_summary_with_chunking(
                            client=client,
                            columns=merged_columns,
                            rows=merged_rows,
                            summary_max_tokens=summary_max,
                        )
                        prompt_total += pt
                        completion_total += ct
                    except Exception as e:  # noqa: BLE001
                        logger.exception("summary call failed")
                        summary_md = f"_整表汇总分析失败：{e}_"

            cache.finish_run(run_id, summary_md)

            return RunResult(
                run_id=run_id,
                mode=mode,
                columns=columns,
                rows=rows,
                row_outputs=row_outputs,
                row_errors=row_errors,
                summary_markdown=summary_md,
                summary_structured=summary_struct,
                prompt_tokens_total=prompt_total,
                completion_tokens_total=completion_total,
                duration_ms_total=int((loop.time() - t_start) * 1000),
                cancelled=cancelled,
                column_to_field=dict(self.column_map),
            )
        finally:
            if owns_cache:
                cache.close()

    # ------------------------------------------------------------------
    async def _run_summary_with_chunking(
        self,
        *,
        client: LLMClient,
        columns: list[str],
        rows: list[dict[str, Any]],
        summary_max_tokens: int,
    ) -> tuple[str, int, int]:
        """Token-aware summary. Splits into chunks and map-reduces if needed.

        Returns (markdown, prompt_tokens, completion_tokens).
        """
        ctx_window = context_window_for(self.preset.model)
        # Reserve room for system + user wrapping + the summary itself.
        budget = max(2048, ctx_window - summary_max_tokens - 1500)
        chunks = _split_rows_by_token_budget(rows, columns, budget)

        if len(chunks) == 1:
            sys_p, user_p = build_summary_prompt(
                system_extra=self._system_prompt(),
                analysis_goal=self._analysis_goal(),
                columns=columns,
                rows=chunks[0],
                output_fields=None,
                use_function_calling=False,
                sample_limit=len(chunks[0]),
            )
            resp = await client.chat(
                system_prompt=sys_p,
                user_prompt=user_p,
                schema_fields=None,
                max_tokens=summary_max_tokens,
            )
            return resp.text or "", resp.prompt_tokens, resp.completion_tokens

        logger.info(
            "summary chunking: %d rows → %d chunks (window=%d, budget=%d)",
            len(rows), len(chunks), ctx_window, budget,
        )
        partial_summaries: list[str] = []
        total_pt = 0
        total_ct = 0

        # ---- map: per-chunk summary -------------------------------------
        for i, chunk in enumerate(chunks):
            sys_extra = (
                self._system_prompt()
                + f"\n\n（这是数据集的第 {i + 1}/{len(chunks)} 个分块，共 {len(rows)} 行。"
                "请只从这个分块中提取关键观察，不要泛泛总结整个数据集——后续会有合并步骤。）"
            )
            sys_p, user_p = build_summary_prompt(
                system_extra=sys_extra,
                analysis_goal=self._analysis_goal(),
                columns=columns,
                rows=chunk,
                output_fields=None,
                use_function_calling=False,
                sample_limit=len(chunk),
            )
            resp = await client.chat(
                system_prompt=sys_p,
                user_prompt=user_p,
                schema_fields=None,
                max_tokens=min(2048, summary_max_tokens),
            )
            partial_summaries.append(resp.text or "")
            total_pt += resp.prompt_tokens
            total_ct += resp.completion_tokens

        # ---- reduce: synthesize -----------------------------------------
        joined = "\n\n".join(
            f"## 分块 {i + 1}/{len(chunks)} 的发现\n\n{s}"
            for i, s in enumerate(partial_summaries)
        )
        reduce_sys = (
            self._system_prompt()
            + "\n\n你将看到对同一数据集多个分块的独立分析。请整合成一份统一的 Markdown 报告："
            "## 关键洞察 / ## 数据质量观察 / ## 分布与异常 / ## 建议行动。避免照抄分块原文。"
        )
        if self._analysis_goal():
            reduce_sys += f"\n\n分析目标：{self._analysis_goal()}"
        resp = await client.chat(
            system_prompt=reduce_sys,
            user_prompt=joined,
            schema_fields=None,
            max_tokens=summary_max_tokens,
        )
        total_pt += resp.prompt_tokens
        total_ct += resp.completion_tokens
        final = (resp.text or "").rstrip()
        if final:
            final += (
                f"\n\n---\n\n_本报告基于 {len(chunks)} 个数据分块（共 {len(rows)} 行）的 map-reduce 分析合成。_"
            )
        return final, total_pt, total_ct


def _row_token_estimate(row: dict[str, Any], columns: list[str]) -> int:
    """Approximate tokens used to render this row inside the summary prompt."""
    parts = []
    for c in columns:
        v = row.get(c)
        if v is None or v == "":
            parts.append("")
            continue
        s = str(v)
        if len(s) > 200:
            s = s[:200] + "…"
        parts.append(s)
    return estimate_tokens(" | ".join(parts))


def _split_rows_by_token_budget(
    rows: list[dict[str, Any]], columns: list[str], budget: int
) -> list[list[dict[str, Any]]]:
    """Split rows into consecutive chunks each ≤ `budget` tokens of content.

    Header tokens are counted once per chunk; per-row estimates use a 200-char
    truncation to mirror what the prompt builder does.
    """
    if not rows:
        return [[]]
    header_tokens = estimate_tokens(" | ".join(columns)) + 16  # markdown overhead
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_tokens = header_tokens
    for r in rows:
        rt = _row_token_estimate(r, columns) + 4  # | bars overhead
        if cur and cur_tokens + rt > budget:
            chunks.append(cur)
            cur = []
            cur_tokens = header_tokens
        cur.append(r)
        cur_tokens += rt
    if cur:
        chunks.append(cur)
    if not chunks:
        chunks = [list(rows)]
    return chunks
