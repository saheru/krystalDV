"""Tool registry + dataset-aware tools the agent can call.

Each tool is a tuple of (OpenAI function-spec, Python handler). Handlers
receive the live `ToolContext` (dataset + accumulated insights/charts) and
return a string that goes back to the LLM as the tool result.

Tools are intentionally read-only over the user's data and side-effect-free
besides recording charts/insights in the ToolContext. No filesystem, no
network, no eval — keeps "agent mode" safe to run on private data.
"""
from __future__ import annotations

import json
import logging
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from kdv.viz.column_stats import ColumnStats, summarize_columns

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
@dataclass
class ChartSpec:
    """A chart the agent decided is worth showing on the result page."""

    kind: str           # bar / bar_h / line / area / scatter / hist / box / pie /
                        # donut / heatmap_corr / treemap / wordcloud / pivot / radar
    columns: list[str]
    title: str
    rationale: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Insight:
    """A short Markdown bullet/paragraph the agent wants to surface."""

    title: str = ""
    body: str = ""
    severity: str = "info"   # info | warning | success


@dataclass
class TableSlot:
    """One queryable table inside a multi-table workbook."""

    table_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    stats: dict[str, ColumnStats]
    sheet_name: str = ""
    source_path: str = ""


@dataclass
class ToolContext:
    """Runtime state passed to every tool handler.

    Supports multi-table workbooks: every tool that reads data accepts an
    optional `table` argument naming one of `tables[*].table_id`; if absent,
    the first table is used. Single-table back-compat: callers that pass
    `columns=` / `rows=` / `stats=` get a one-slot context, and the legacy
    `ctx.columns / .rows / .stats` accessors still work via the property
    proxies below.
    """

    tables: list[TableSlot] = field(default_factory=list)
    charts: list[ChartSpec] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    # Back-compat constructor inputs — collapsed into a single TableSlot
    # in __post_init__ if `tables` is empty.
    columns: list[str] | None = None  # type: ignore[assignment]
    rows: list[dict[str, Any]] | None = None  # type: ignore[assignment]
    stats: dict[str, ColumnStats] | None = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # If caller passed legacy single-table kwargs (columns/rows/stats)
        # but no `tables`, build a one-slot table list. Either form works.
        if not self.tables and (self.columns is not None or self.rows is not None):
            self.tables = [TableSlot(
                table_id="default",
                columns=list(self.columns or []),
                rows=list(self.rows or []),
                stats=dict(self.stats or {}),
            )]
        # Now make columns/rows/stats live aliases of the primary table.
        # We deliberately set attributes (not properties) so existing tool
        # code that does `ctx.charts.append(...)` etc. keeps fast access.
        self._refresh_aliases()

    def _refresh_aliases(self) -> None:
        primary = self.tables[0] if self.tables else None
        self.columns = primary.columns if primary else []  # type: ignore[assignment]
        self.rows = primary.rows if primary else []  # type: ignore[assignment]
        self.stats = primary.stats if primary else {}  # type: ignore[assignment]

    def get_table(self, table_id: str | None = None) -> TableSlot | None:
        """Resolve a `table` tool argument to a TableSlot.

        `None` / empty / missing → first table (default). Unknown id → None;
        callers turn that into an error message for the LLM.
        """
        if not self.tables:
            return None
        if not table_id:
            return self.tables[0]
        for t in self.tables:
            if t.table_id == table_id:
                return t
        return None

    def list_table_ids(self) -> list[str]:
        return [t.table_id for t in self.tables]


# ----------------------------------------------------------------------
ToolHandler = Callable[[ToolContext, dict[str, Any]], str]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema for `function.parameters`
    handler: ToolHandler

    def to_openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai_specs(self) -> list[dict[str, Any]]:
        return [t.to_openai_spec() for t in self._tools.values()]


# ======================================================================
# default tools
# ======================================================================


def _truncate(s: str, n: int = 4000) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n…[truncated, full output {len(s)} chars]"


def _resolve_table(ctx: ToolContext, args: dict[str, Any]) -> tuple[TableSlot | None, str]:
    """Pick the table targeted by a tool call; either return the slot or the
    error string the LLM should see."""
    tid = (args.get("table") or args.get("table_id") or "").strip()
    slot = ctx.get_table(tid or None)
    if slot is None:
        if not ctx.tables:
            return None, "未加载任何数据表"
        return None, (
            f"找不到表 `{tid}`。可用表：{', '.join(ctx.list_table_ids())}"
        )
    return slot, ""


def _table_param_schema() -> dict[str, Any]:
    """Common schema fragment for the `table` argument shared by all tools."""
    return {
        "type": "string",
        "description": (
            "目标表的 table_id，如 'invoices.xlsx/Sheet1'。"
            "省略则用第一张（默认）表。多表时务必明确指定，避免分析错对象。"
        ),
    }


def _to_float(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- list_tables -------------------------------------------------------
def _t_list_tables(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Multi-table overview — list every loaded table with rows/cols/source.

    Agent should call this FIRST when working with multi-table workbooks so
    it knows which `table` ids to pass to subsequent tools.
    """
    if not ctx.tables:
        return "没有加载任何数据表。"
    lines = [f"共 {len(ctx.tables)} 张表："]
    for t in ctx.tables:
        src = (t.source_path or "").split("/")[-1] or "(未知文件)"
        lines.append(
            f"- table_id=`{t.table_id}`  {len(t.rows)} 行 × {len(t.columns)} 列"
            f"  · 来源 {src}"
            + (f" / sheet={t.sheet_name}" if t.sheet_name else "")
        )
        # 列出前 8 列做一个 schema 提示
        col_preview = ", ".join(t.columns[:8]) + (" …" if len(t.columns) > 8 else "")
        lines.append(f"    列：{col_preview}")
    if len(ctx.tables) > 1:
        lines.append(
            "\n📌 工具调用务必带 `table=<table_id>`，否则默认操作第一张表 "
            f"(`{ctx.tables[0].table_id}`)。"
        )
    return "\n".join(lines)


# ---- list_columns -----------------------------------------------------
def _t_list_columns(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    lines = [f"表 `{slot.table_id}`：{len(slot.rows)} 行，共 {len(slot.columns)} 列："]
    for col in slot.columns:
        s = slot.stats.get(col)
        if not s:
            continue
        line = f"- `{col}` [{s.kind}] 计数={s.count} 缺失={s.null_count} 唯一={s.distinct_count}"
        if s.kind == "numeric" and s.min is not None:
            line += f" 范围=[{s.min:.2f}, {s.max:.2f}] 均值={s.mean:.2f}"
        elif s.kind in ("categorical", "boolean") and s.top_values:
            top = ", ".join(f"{k}({c})" for k, c in s.top_values[:5])
            line += f" Top={top}"
        elif s.kind == "datetime":
            line += f" 时间范围=[{s.earliest}, {s.latest}]"
        lines.append(line)
    return "\n".join(lines)


# ---- sample_rows -------------------------------------------------------
def _t_sample_rows(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    n = int(args.get("n", 5))
    n = max(1, min(n, 50))
    cols = args.get("columns") or slot.columns
    if isinstance(cols, str):
        cols = [c.strip() for c in cols.split(",")]
    cols = [c for c in cols if c in slot.columns]
    if not cols:
        cols = slot.columns

    rows = slot.rows[:n]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    body = []
    for r in rows:
        body.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|")[:80] for c in cols) + " |")
    return _truncate("\n".join([head, sep] + body))


# ---- describe_column ---------------------------------------------------
def _t_describe(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    col = args.get("column", "")
    if col not in slot.stats:
        return f"列 `{col}` 不存在于表 `{slot.table_id}`。可用列：{', '.join(slot.columns)}"
    s = slot.stats[col]
    out = {
        "name": s.name,
        "kind": s.kind,
        "count": s.count,
        "null_count": s.null_count,
        "distinct_count": s.distinct_count,
        "min": s.min,
        "max": s.max,
        "mean": s.mean,
        "median": s.median,
        "stdev": s.stdev,
        "top_values": s.top_values,
        "earliest": s.earliest,
        "latest": s.latest,
    }
    return json.dumps(out, ensure_ascii=False, default=str)


# ---- aggregate ---------------------------------------------------------
def _t_aggregate(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    col = args.get("column", "")
    op = args.get("op", "mean")
    group_by = args.get("group_by", "")
    if col not in slot.columns:
        return f"列 `{col}` 不存在于表 `{slot.table_id}`"

    if not group_by:
        nums = [_to_float(r.get(col)) for r in slot.rows]
        nums = [n for n in nums if n is not None]
        if not nums:
            return "该列无可用数值"
        if op == "sum":
            v = sum(nums)
        elif op == "max":
            v = max(nums)
        elif op == "min":
            v = min(nums)
        elif op == "count":
            v = len(nums)
        elif op == "median":
            v = statistics.median(nums)
        elif op == "stdev":
            v = statistics.pstdev(nums) if len(nums) > 1 else 0.0
        else:
            v = sum(nums) / len(nums)
        return f"{op}({col}) = {v}  (table={slot.table_id})"

    if group_by not in slot.columns:
        return f"分组列 `{group_by}` 不存在于表 `{slot.table_id}`"
    buckets: dict[str, list[float]] = {}
    for r in slot.rows:
        k = str(r.get(group_by, "")).strip()
        if not k:
            continue
        v = _to_float(r.get(col))
        if v is None:
            continue
        buckets.setdefault(k, []).append(v)
    rows = []
    for k, vs in buckets.items():
        if op == "sum":
            agg = sum(vs)
        elif op == "max":
            agg = max(vs)
        elif op == "min":
            agg = min(vs)
        elif op == "count":
            agg = len(vs)
        elif op == "median":
            agg = statistics.median(vs)
        else:
            agg = sum(vs) / len(vs)
        rows.append((k, agg, len(vs)))
    rows.sort(key=lambda x: x[1], reverse=True)
    return _truncate(
        f"表 `{slot.table_id}`：\n"
        + "| 分组 | " + op + " | 计数 |\n|---|---|---|\n"
        + "\n".join(f"| {k} | {v:.2f} | {n} |" for k, v, n in rows[:30])
    )


# ---- filter_rows -------------------------------------------------------
def _t_filter(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    col = args.get("column", "")
    op = args.get("op", "eq")
    value = args.get("value", "")
    n_show = int(args.get("show", 5))
    if col not in slot.columns:
        return f"列 `{col}` 不存在于表 `{slot.table_id}`"

    def keep(v) -> bool:
        if op == "eq":
            return str(v) == str(value)
        if op == "ne":
            return str(v) != str(value)
        if op == "contains":
            return str(value).lower() in str(v).lower()
        try:
            n = float(v)
            t = float(value)
        except (TypeError, ValueError):
            return False
        if op == "gt":
            return n > t
        if op == "gte":
            return n >= t
        if op == "lt":
            return n < t
        if op == "lte":
            return n <= t
        return False

    matches = [r for r in slot.rows if keep(r.get(col))]
    sample = matches[:n_show]
    body = "\n".join(json.dumps(r, ensure_ascii=False, default=str)[:200] for r in sample)
    return f"表 `{slot.table_id}` 匹配 {len(matches)}/{len(slot.rows)} 行。前 {len(sample)} 条：\n{body}"


# ---- correlate ---------------------------------------------------------
def _t_correlate(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    a = args.get("col_a", "")
    b = args.get("col_b", "")
    if a not in slot.columns or b not in slot.columns:
        return f"列名错误。表 `{slot.table_id}` 可用列：{', '.join(slot.columns)}"
    pairs: list[tuple[float, float]] = []
    for r in slot.rows:
        va = _to_float(r.get(a))
        vb = _to_float(r.get(b))
        if va is None or vb is None:
            continue
        pairs.append((va, vb))
    if len(pairs) < 3:
        return f"可比对的数值对 < 3，无法计算相关系数（共 {len(pairs)} 对）"
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n = len(pairs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in pairs)
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return f"`{a}` 或 `{b}` 标准差为 0，相关系数无意义"
    r = num / (dx * dy)
    strength = ("强" if abs(r) > 0.7 else "中" if abs(r) > 0.4 else "弱")
    direction = "正" if r > 0 else "负"
    return f"Pearson 相关系数(`{a}`, `{b}`) = {r:.4f}（{direction}相关，{strength}强度，n={n}）"


# ---- distinct_values ---------------------------------------------------
def _t_distinct(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    col = args.get("column", "")
    if col not in slot.columns:
        return f"列 `{col}` 不存在于表 `{slot.table_id}`"
    c = Counter(str(r.get(col, "")).strip() for r in slot.rows if r.get(col) not in (None, ""))
    items = c.most_common(50)
    return _truncate(
        f"表 `{slot.table_id}` 列 `{col}` 共 {len(c)} 个不同值（Top 50 见下）：\n"
        + "\n".join(f"- {k}  ×{v}" for k, v in items)
    )


# ---- text_search -------------------------------------------------------
def _t_text_search(ctx: ToolContext, args: dict[str, Any]) -> str:
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    pattern = args.get("pattern", "")
    col = args.get("column", "")
    flags = re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return f"正则错误：{e}"
    candidate_cols = [col] if col and col in slot.columns else slot.columns
    matches = []
    for i, r in enumerate(slot.rows):
        for c in candidate_cols:
            v = r.get(c)
            if v is None:
                continue
            if rx.search(str(v)):
                matches.append((i, c, str(v)[:120]))
                break
        if len(matches) >= 30:
            break
    return f"模式命中 {len(matches)} 行（前 30）：\n" + "\n".join(
        f"行{i} [{c}] {snippet}" for i, c, snippet in matches
    )


# ---- add_chart ---------------------------------------------------------
_CHART_KINDS = {
    "bar", "bar_h", "line", "area", "scatter", "histogram", "box",
    "pie", "donut", "radar", "heatmap_corr", "treemap", "wordcloud",
    "stat_summary", "pivot",
}


def _t_add_chart(ctx: ToolContext, args: dict[str, Any]) -> str:
    # Charts are bound to ONE table for column validation. The `table` arg
    # picks which one — defaults to the first.
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    kind = args.get("kind", "")
    cols = args.get("columns", [])
    if isinstance(cols, str):
        cols = [c.strip() for c in cols.split(",") if c.strip()]
    title = args.get("title", "") or f"{kind} chart"
    rationale = args.get("rationale", "")
    if kind not in _CHART_KINDS:
        return f"图表类型不支持：{kind}。可选：{sorted(_CHART_KINDS)}"
    bad = [c for c in cols if c not in slot.columns]
    if bad and kind not in ("stat_summary", "kpi"):
        return f"未知列：{bad}。表 `{slot.table_id}` 可用列：{slot.columns}"
    params = dict(args.get("params") or {})
    # Stash the source table in chart params so the result page can route
    # it to the right rows/columns at render time.
    params.setdefault("_table_id", slot.table_id)
    ctx.charts.append(
        ChartSpec(kind=kind, columns=cols, title=title, rationale=rationale,
                  params=params)
    )
    return f"已添加图表：{kind}({cols}) 标题=「{title}」 (来自 {slot.table_id})"


# ---- record_insight ----------------------------------------------------
def _t_insight(ctx: ToolContext, args: dict[str, Any]) -> str:
    title = args.get("title", "")
    body = args.get("body", "")
    severity = args.get("severity", "info")
    if severity not in ("info", "warning", "success"):
        severity = "info"
    if not body.strip():
        return "insight body 不能为空"
    ctx.insights.append(Insight(title=title, body=body, severity=severity))
    return f"已记录洞察：{title or body[:30]}"


# ---- reconcile (internal vs external) ---------------------------------
def _t_reconcile(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Compare an aggregated internal column against external values per group.

    Use case: user uploads internal cost data, types external bill amounts in
    chat, asks agent to compute per-group diff and total diff. The tool also
    auto-registers a horizontal bar chart of the diff.
    """
    slot, err = _resolve_table(ctx, args)
    if err:
        return err
    assert slot is not None
    group_col = args.get("group_column", "")
    value_col = args.get("value_column", "")
    external = args.get("external_values", {})
    agg = args.get("agg", "sum")
    label = args.get("label", "对账")
    auto_chart = bool(args.get("auto_chart", True))

    if group_col not in slot.columns:
        return f"分组列 `{group_col}` 不存在于表 `{slot.table_id}`"
    if value_col not in slot.columns:
        return f"数值列 `{value_col}` 不存在于表 `{slot.table_id}`"
    if not isinstance(external, dict) or not external:
        return "external_values 必须是 {分组名: 外部数值} 的 JSON 对象"

    buckets: dict[str, list[float]] = {}
    for r in slot.rows:
        k = str(r.get(group_col, "")).strip()
        if not k:
            continue
        v = _to_float(r.get(value_col))
        if v is None:
            continue
        buckets.setdefault(k, []).append(v)

    def aggv(vs: list[float]) -> float:
        if agg == "mean":
            return sum(vs) / len(vs)
        if agg == "max":
            return max(vs)
        if agg == "min":
            return min(vs)
        if agg == "median":
            return statistics.median(vs)
        if agg == "count":
            return float(len(vs))
        return float(sum(vs))

    rows: list[tuple[str, float, float, float, float]] = []
    seen: set[str] = set()
    for k, vs in buckets.items():
        seen.add(k)
        internal = aggv(vs)
        ext_raw = external.get(k)
        ext_v = _to_float(ext_raw)
        if ext_v is None:
            ext_v = 0.0 if ext_raw is None else float("nan")
        diff = internal - ext_v
        diff_pct = (diff / ext_v * 100) if ext_v not in (0, None) and not math.isnan(ext_v) else float("nan")
        rows.append((k, internal, ext_v, diff, diff_pct))

    # Include external-only groups (LLM-provided but no internal data)
    for k, ext_raw in external.items():
        if k in seen:
            continue
        ext_v = _to_float(ext_raw)
        if ext_v is None:
            continue
        rows.append((k, 0.0, ext_v, -ext_v, -100.0))

    rows.sort(key=lambda x: abs(x[3]), reverse=True)

    # Build markdown table
    lines = [
        f"| 分组 | 内部 {agg}({value_col}) | 外部值 | 差额 | 差额% |",
        "|---|---|---|---|---|",
    ]
    total_internal = 0.0
    total_external = 0.0
    for k, iv, ev, dv, pct in rows:
        total_internal += iv
        if not math.isnan(ev):
            total_external += ev
        pct_s = f"{pct:+.2f}%" if not math.isnan(pct) else "n/a"
        lines.append(f"| {k} | {iv:.2f} | {ev:.2f} | {dv:+.2f} | {pct_s} |")
    total_diff = total_internal - total_external
    total_pct = (total_diff / total_external * 100) if total_external else float("nan")
    lines.append(
        f"| **合计** | **{total_internal:.2f}** | **{total_external:.2f}** | "
        f"**{total_diff:+.2f}** | "
        f"**{total_pct:+.2f}%** |" if not math.isnan(total_pct) else
        f"| **合计** | **{total_internal:.2f}** | **{total_external:.2f}** | **{total_diff:+.2f}** | n/a |"
    )

    if auto_chart and rows:
        ctx.charts.append(ChartSpec(
            kind="reconcile_bar",
            columns=[group_col, value_col],
            title=f"{label}：{group_col} 内部 vs 外部",
            rationale=f"差额合计 {total_diff:+.2f}",
            params={
                "_table_id": slot.table_id,
                "internal": [(k, iv) for k, iv, *_ in rows],
                "external": [(k, ev) for k, _, ev, *_ in rows],
                "diff": [(k, dv) for k, _, _, dv, _ in rows],
            },
        ))

    return _truncate("\n".join(lines))


# ---- cross_reconcile (compare two tables) -----------------------------
def _t_cross_reconcile(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Reconcile two tables on a shared group key.

    Aggregate `value_column_a` per `group_column` in `table_a`, do the same
    for `table_b`, and compare the two per group. Useful for e.g. comparing
    last month's invoice vs this month's, or internal ledger vs external
    statement when both live in their own table.
    """
    tid_a = (args.get("table_a") or "").strip()
    tid_b = (args.get("table_b") or "").strip()
    if not tid_a or not tid_b:
        return "需提供 table_a 和 table_b（可用：" + ", ".join(ctx.list_table_ids()) + "）"
    a = ctx.get_table(tid_a)
    b = ctx.get_table(tid_b)
    if a is None:
        return f"找不到 table_a=`{tid_a}`"
    if b is None:
        return f"找不到 table_b=`{tid_b}`"
    group_col = args.get("group_column", "")
    val_a = args.get("value_column_a", "") or args.get("value_column", "")
    val_b = args.get("value_column_b", "") or args.get("value_column", "")
    op = args.get("op", "sum")
    label = args.get("label", "跨表对账")
    auto_chart = bool(args.get("auto_chart", True))
    if group_col not in a.columns:
        return f"`{group_col}` 不在表 `{a.table_id}` 中。a 列：{', '.join(a.columns)}"
    if group_col not in b.columns:
        return f"`{group_col}` 不在表 `{b.table_id}` 中。b 列：{', '.join(b.columns)}"
    if val_a not in a.columns:
        return f"`{val_a}` 不在表 `{a.table_id}` 中"
    if val_b not in b.columns:
        return f"`{val_b}` 不在表 `{b.table_id}` 中"

    def _aggregate(slot: TableSlot, gcol: str, vcol: str) -> dict[str, float]:
        buckets: dict[str, list[float]] = {}
        for r in slot.rows:
            k = str(r.get(gcol, "")).strip()
            if not k:
                continue
            v = _to_float(r.get(vcol))
            if v is None:
                continue
            buckets.setdefault(k, []).append(v)
        out: dict[str, float] = {}
        for k, vs in buckets.items():
            if op == "mean":
                out[k] = sum(vs) / len(vs)
            elif op == "max":
                out[k] = max(vs)
            elif op == "min":
                out[k] = min(vs)
            elif op == "count":
                out[k] = float(len(vs))
            elif op == "median":
                out[k] = statistics.median(vs)
            else:
                out[k] = float(sum(vs))
        return out

    agg_a = _aggregate(a, group_col, val_a)
    agg_b = _aggregate(b, group_col, val_b)
    keys = sorted(set(agg_a) | set(agg_b))
    rows = []
    total_a = 0.0
    total_b = 0.0
    for k in keys:
        va = agg_a.get(k, 0.0)
        vb = agg_b.get(k, 0.0)
        total_a += va
        total_b += vb
        diff = va - vb
        pct = (diff / vb * 100) if vb else float("nan")
        rows.append((k, va, vb, diff, pct))
    rows.sort(key=lambda x: abs(x[3]), reverse=True)

    lines = [
        f"跨表对账（{label}）：`{a.table_id}` vs `{b.table_id}` on `{group_col}`",
        f"| 分组 | A:{op}({val_a}) | B:{op}({val_b}) | 差额 (A−B) | 差额% |",
        "|---|---|---|---|---|",
    ]
    for k, va, vb, dv, pct in rows:
        pct_s = f"{pct:+.2f}%" if not math.isnan(pct) else "n/a"
        lines.append(f"| {k} | {va:.2f} | {vb:.2f} | {dv:+.2f} | {pct_s} |")
    total_diff = total_a - total_b
    total_pct = (total_diff / total_b * 100) if total_b else float("nan")
    pct_s = f"{total_pct:+.2f}%" if not math.isnan(total_pct) else "n/a"
    lines.append(
        f"| **合计** | **{total_a:.2f}** | **{total_b:.2f}** | "
        f"**{total_diff:+.2f}** | **{pct_s}** |"
    )

    if auto_chart and rows:
        ctx.charts.append(ChartSpec(
            kind="reconcile_bar",
            columns=[group_col, val_a],
            title=f"{label}：{a.table_id} vs {b.table_id}",
            rationale=f"差额合计 {total_diff:+.2f}",
            params={
                "_table_id": a.table_id,
                "internal": [(k, va) for k, va, *_ in rows],
                "external": [(k, vb) for k, _, vb, *_ in rows],
                "diff": [(k, dv) for k, _, _, dv, _ in rows],
            },
        ))
    return _truncate("\n".join(lines))


# ---- join_tables ------------------------------------------------------
def _t_join_tables(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Inner-join two tables on a key column and return a sample of the result.

    Lightweight — we don't materialise the join into ToolContext.tables; we
    just print enough rows to let the agent pick what to query next. For
    heavy joins the user should pre-process in Excel.
    """
    tid_a = (args.get("table_a") or "").strip()
    tid_b = (args.get("table_b") or "").strip()
    a = ctx.get_table(tid_a)
    b = ctx.get_table(tid_b)
    if a is None or b is None:
        return "需提供 table_a 和 table_b（可用：" + ", ".join(ctx.list_table_ids()) + "）"
    key_a = args.get("key_a") or args.get("key") or ""
    key_b = args.get("key_b") or args.get("key") or ""
    n_show = int(args.get("show", 10))
    n_show = max(1, min(n_show, 50))
    if key_a not in a.columns:
        return f"`{key_a}` 不在表 `{a.table_id}` 中"
    if key_b not in b.columns:
        return f"`{key_b}` 不在表 `{b.table_id}` 中"

    # Build lookup on b to keep the inner-join cost reasonable.
    bkey: dict[str, list[dict[str, Any]]] = {}
    for r in b.rows:
        k = str(r.get(key_b, "")).strip()
        if k:
            bkey.setdefault(k, []).append(r)

    # Disambiguate column names: a's columns keep their name; b's columns
    # get a `b.` prefix unless they're the join key (which we drop from b).
    out_cols = list(a.columns)
    b_renamed: dict[str, str] = {}
    for c in b.columns:
        if c == key_b:
            continue
        new_name = c if c not in a.columns else f"b.{c}"
        b_renamed[c] = new_name
        out_cols.append(new_name)

    matched_count = 0
    sample: list[dict[str, Any]] = []
    for ra in a.rows:
        k = str(ra.get(key_a, "")).strip()
        bs = bkey.get(k, [])
        if not bs:
            continue
        for rb in bs:
            matched_count += 1
            if len(sample) < n_show:
                merged: dict[str, Any] = dict(ra)
                for c, new_name in b_renamed.items():
                    merged[new_name] = rb.get(c)
                sample.append(merged)
        if matched_count >= 1000:
            break

    if not sample:
        return (
            f"`{a.table_id}` ⋈ `{b.table_id}` on {key_a}={key_b}：无匹配行。"
            f"先用 distinct_values 看两表的键值是否一致。"
        )

    head = "| " + " | ".join(out_cols[:8]) + " |"
    sep = "|" + "|".join(["---"] * min(len(out_cols), 8)) + "|"
    body = []
    for r in sample:
        body.append(
            "| " + " | ".join(
                str(r.get(c, "")).replace("|", "\\|")[:60] for c in out_cols[:8]
            ) + " |"
        )
    return _truncate(
        f"`{a.table_id}` ⋈ `{b.table_id}` on `{key_a}=={key_b}`，"
        f"共 {matched_count} 行匹配，前 {len(sample)} 条（仅前 8 列）：\n"
        + "\n".join([head, sep] + body)
    )


# ---- record_user_data --------------------------------------------------
def _t_record_user_data(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Stash structured key-value data the user provided in chat.

    Stored on the ToolContext for later tools/charts to reference. This is
    purely an aide-mémoire — the values also live in the conversation history.
    """
    label = args.get("label", "user_data")
    payload = args.get("data", {})
    if not isinstance(payload, dict):
        return "data 必须是 JSON 对象 {名字: 值}"
    if not hasattr(ctx, "_user_kv"):
        ctx._user_kv = {}  # type: ignore[attr-defined]
    ctx._user_kv[label] = payload  # type: ignore[attr-defined]
    return f"已记录用户数据 [{label}]：{json.dumps(payload, ensure_ascii=False)[:300]}"


# ---- clear_charts / clear_insights ------------------------------------
def _t_clear_charts(ctx: ToolContext, args: dict[str, Any]) -> str:
    n = len(ctx.charts)
    ctx.charts.clear()
    return f"已清空 {n} 个图表"


def _t_clear_insights(ctx: ToolContext, args: dict[str, Any]) -> str:
    n = len(ctx.insights)
    ctx.insights.clear()
    return f"已清空 {n} 条洞察"


# ---- finish ------------------------------------------------------------
def _t_finish(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Sentinel — handled in the runner loop, included here for symmetry."""
    return args.get("summary", "结束")


# ======================================================================
def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()

    reg.register(Tool(
        name="list_tables",
        description=(
            "列出本次工作簿里所有数据表（每个 sheet / 每个文件 = 一张表）。"
            "**多表场景必须先调用此工具拿到 table_id**，后续每个工具都靠 "
            "table 参数指定要操作哪一张。单表则不必。"
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_t_list_tables,
    ))

    reg.register(Tool(
        name="list_columns",
        description="列出某张表的所有列、类型与基础统计。**首次分析某张表前必须先调用以获得 schema 概览。**",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
            },
            "additionalProperties": False,
        },
        handler=_t_list_columns,
    ))

    reg.register(Tool(
        name="sample_rows",
        description="返回前 N 行数据（默认 5），可指定列。",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                "columns": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        handler=_t_sample_rows,
    ))

    reg.register(Tool(
        name="describe_column",
        description="查看某一列的详细统计（min/max/mean/median/topN）",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "column": {"type": "string"},
            },
            "required": ["column"],
        },
        handler=_t_describe,
    ))

    reg.register(Tool(
        name="aggregate",
        description="对数值列做聚合（sum/mean/median/max/min/stdev/count），可按另一列分组",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "column": {"type": "string", "description": "数值列"},
                "op": {
                    "type": "string",
                    "enum": ["sum", "mean", "median", "max", "min", "stdev", "count"],
                    "default": "mean",
                },
                "group_by": {"type": "string", "description": "可选：分组列"},
            },
            "required": ["column"],
        },
        handler=_t_aggregate,
    ))

    reg.register(Tool(
        name="filter_rows",
        description="筛选满足条件的行（eq/ne/gt/gte/lt/lte/contains），返回计数与样本",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "column": {"type": "string"},
                "op": {
                    "type": "string",
                    "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "contains"],
                },
                "value": {"description": "比较值（字符串或数字）"},
                "show": {"type": "integer", "default": 5},
            },
            "required": ["column", "op", "value"],
        },
        handler=_t_filter,
    ))

    reg.register(Tool(
        name="correlate",
        description="计算两列数值的 Pearson 相关系数",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "col_a": {"type": "string"},
                "col_b": {"type": "string"},
            },
            "required": ["col_a", "col_b"],
        },
        handler=_t_correlate,
    ))

    reg.register(Tool(
        name="distinct_values",
        description="返回某列的去重值与频次（前 50）",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "column": {"type": "string"},
            },
            "required": ["column"],
        },
        handler=_t_distinct,
    ))

    reg.register(Tool(
        name="text_search",
        description="在文本列里按正则搜索匹配行（不区分大小写）",
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "pattern": {"type": "string", "description": "Python 正则"},
                "column": {"type": "string", "description": "可选：限定列"},
            },
            "required": ["pattern"],
        },
        handler=_t_text_search,
    ))

    reg.register(Tool(
        name="add_chart",
        description=(
            "把一个图表注册到结果页。支持 kind: "
            "bar/bar_h/line/area/scatter/histogram/box/pie/donut/radar/"
            "heatmap_corr/treemap/wordcloud/stat_summary/pivot"
        ),
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "kind": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string"},
                "rationale": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["kind", "columns", "title"],
        },
        handler=_t_add_chart,
    ))

    reg.register(Tool(
        name="record_insight",
        description="把一条结论写到结果页『洞察』面板。Markdown 支持。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "severity": {"type": "string", "enum": ["info", "warning", "success"]},
            },
            "required": ["body"],
        },
        handler=_t_insight,
    ))

    reg.register(Tool(
        name="reconcile",
        description=(
            "对账/比对分析：把内部数据集按 `group_column` 分组聚合 `value_column`，"
            "和用户在对话中提供的 `external_values`（{分组名: 外部数值}）逐项比对，"
            "返回每组的差额、差额百分比和合计差额。**强烈推荐用于供应商对账、"
            "预算 vs 实际、计划 vs 完成等场景。** 自动注册一个对账图表。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "table": _table_param_schema(),
                "group_column": {"type": "string", "description": "用于分组的列（例如 渠道商/供应商）"},
                "value_column": {"type": "string", "description": "要聚合的数值列（例如 金额/成本）"},
                "external_values": {
                    "type": "object",
                    "description": "用户提供的外部值，键是分组名，值是外部金额",
                    "additionalProperties": {"type": "number"},
                },
                "agg": {
                    "type": "string",
                    "enum": ["sum", "mean", "median", "max", "min", "count"],
                    "default": "sum",
                },
                "label": {"type": "string", "description": "图表/报表标题前缀"},
                "auto_chart": {"type": "boolean", "default": True},
            },
            "required": ["group_column", "value_column", "external_values"],
        },
        handler=_t_reconcile,
    ))

    reg.register(Tool(
        name="cross_reconcile",
        description=(
            "**跨表对账**：在两张表上做聚合并按分组键 `group_column` 比对差额。"
            "用于 A 表 vs B 表场景：内部账 vs 外部对账单（各自存一张表）、"
            "上月 vs 本月、计划 vs 实际等。返回每组的差额、差额% 与合计差额。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "table_a": {"type": "string", "description": "A 表 table_id"},
                "table_b": {"type": "string", "description": "B 表 table_id"},
                "group_column": {"type": "string", "description": "两表共享的分组列名"},
                "value_column_a": {"type": "string", "description": "A 表的数值列；省略则用 value_column"},
                "value_column_b": {"type": "string", "description": "B 表的数值列；省略则用 value_column"},
                "value_column": {"type": "string", "description": "若两表数值列同名，可只填这个"},
                "op": {
                    "type": "string",
                    "enum": ["sum", "mean", "median", "max", "min", "count"],
                    "default": "sum",
                },
                "label": {"type": "string"},
                "auto_chart": {"type": "boolean", "default": True},
            },
            "required": ["table_a", "table_b", "group_column"],
        },
        handler=_t_cross_reconcile,
    ))

    reg.register(Tool(
        name="join_tables",
        description=(
            "**跨表连接**（inner-join）：用 `key_a` / `key_b` 做键，把 A 与 B 行匹配，"
            "返回前 N 条合并行（用于探查关联关系）。冲突列名会加 `b.` 前缀。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "table_a": {"type": "string"},
                "table_b": {"type": "string"},
                "key_a": {"type": "string", "description": "A 表的连接键"},
                "key_b": {"type": "string", "description": "B 表的连接键；如果两表同名可只填 key"},
                "key": {"type": "string", "description": "两表同名时的连接键（与 key_a/key_b 二选一）"},
                "show": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["table_a", "table_b"],
        },
        handler=_t_join_tables,
    ))

    reg.register(Tool(
        name="record_user_data",
        description=(
            "把用户在对话中口述的结构化数据（如外部账单、预算、目标值）记录下来。"
            "存到运行上下文供后续 `reconcile` 等工具使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "data": {"type": "object"},
            },
            "required": ["label", "data"],
        },
        handler=_t_record_user_data,
    ))

    reg.register(Tool(
        name="clear_charts",
        description="清空当前结果页所有图表（用户要求重新分析时使用）",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_t_clear_charts,
    ))

    reg.register(Tool(
        name="clear_insights",
        description="清空当前结果页所有洞察",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_t_clear_insights,
    ))

    reg.register(Tool(
        name="finish_task",
        description="**完成当前任务后调用。** 提供本任务的最终结论摘要（Markdown）。",
        parameters={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        handler=_t_finish,
    ))

    return reg
