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
class ToolContext:
    """Runtime state passed to every tool handler."""

    columns: list[str]
    rows: list[dict[str, Any]]
    stats: dict[str, ColumnStats]
    charts: list[ChartSpec] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)


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


def _to_float(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- list_columns -----------------------------------------------------
def _t_list_columns(ctx: ToolContext, args: dict[str, Any]) -> str:
    lines = [f"数据集 {len(ctx.rows)} 行，共 {len(ctx.columns)} 列："]
    for col in ctx.columns:
        s = ctx.stats.get(col)
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
    n = int(args.get("n", 5))
    n = max(1, min(n, 50))
    cols = args.get("columns") or ctx.columns
    if isinstance(cols, str):
        cols = [c.strip() for c in cols.split(",")]
    cols = [c for c in cols if c in ctx.columns]
    if not cols:
        cols = ctx.columns

    rows = ctx.rows[:n]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    body = []
    for r in rows:
        body.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|")[:80] for c in cols) + " |")
    return _truncate("\n".join([head, sep] + body))


# ---- describe_column ---------------------------------------------------
def _t_describe(ctx: ToolContext, args: dict[str, Any]) -> str:
    col = args.get("column", "")
    if col not in ctx.stats:
        return f"列 `{col}` 不存在。可用列：{', '.join(ctx.columns)}"
    s = ctx.stats[col]
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
    col = args.get("column", "")
    op = args.get("op", "mean")
    group_by = args.get("group_by", "")
    if col not in ctx.columns:
        return f"列 `{col}` 不存在"

    if not group_by:
        nums = [_to_float(r.get(col)) for r in ctx.rows]
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
        return f"{op}({col}) = {v}"

    if group_by not in ctx.columns:
        return f"分组列 `{group_by}` 不存在"
    buckets: dict[str, list[float]] = {}
    for r in ctx.rows:
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
        "| 分组 | " + op + " | 计数 |\n|---|---|---|\n"
        + "\n".join(f"| {k} | {v:.2f} | {n} |" for k, v, n in rows[:30])
    )


# ---- filter_rows -------------------------------------------------------
def _t_filter(ctx: ToolContext, args: dict[str, Any]) -> str:
    col = args.get("column", "")
    op = args.get("op", "eq")
    value = args.get("value", "")
    n_show = int(args.get("show", 5))
    if col not in ctx.columns:
        return f"列 `{col}` 不存在"

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

    matches = [r for r in ctx.rows if keep(r.get(col))]
    sample = matches[:n_show]
    body = "\n".join(json.dumps(r, ensure_ascii=False, default=str)[:200] for r in sample)
    return f"匹配 {len(matches)}/{len(ctx.rows)} 行。前 {len(sample)} 条：\n{body}"


# ---- correlate ---------------------------------------------------------
def _t_correlate(ctx: ToolContext, args: dict[str, Any]) -> str:
    a = args.get("col_a", "")
    b = args.get("col_b", "")
    if a not in ctx.columns or b not in ctx.columns:
        return f"列名错误。可用列：{', '.join(ctx.columns)}"
    pairs: list[tuple[float, float]] = []
    for r in ctx.rows:
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
    col = args.get("column", "")
    if col not in ctx.columns:
        return f"列 `{col}` 不存在"
    c = Counter(str(r.get(col, "")).strip() for r in ctx.rows if r.get(col) not in (None, ""))
    items = c.most_common(50)
    return _truncate(
        f"`{col}` 共 {len(c)} 个不同值（Top 50 见下）：\n"
        + "\n".join(f"- {k}  ×{v}" for k, v in items)
    )


# ---- text_search -------------------------------------------------------
def _t_text_search(ctx: ToolContext, args: dict[str, Any]) -> str:
    pattern = args.get("pattern", "")
    col = args.get("column", "")
    flags = re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return f"正则错误：{e}"
    candidate_cols = [col] if col and col in ctx.columns else ctx.columns
    matches = []
    for i, r in enumerate(ctx.rows):
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
    kind = args.get("kind", "")
    cols = args.get("columns", [])
    if isinstance(cols, str):
        cols = [c.strip() for c in cols.split(",") if c.strip()]
    title = args.get("title", "") or f"{kind} chart"
    rationale = args.get("rationale", "")
    if kind not in _CHART_KINDS:
        return f"图表类型不支持：{kind}。可选：{sorted(_CHART_KINDS)}"
    bad = [c for c in cols if c not in ctx.columns]
    if bad and kind not in ("stat_summary", "kpi"):
        return f"未知列：{bad}。可用列：{ctx.columns}"
    ctx.charts.append(
        ChartSpec(kind=kind, columns=cols, title=title, rationale=rationale,
                  params=args.get("params") or {})
    )
    return f"已添加图表：{kind}({cols}) 标题=「{title}」"


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
    group_col = args.get("group_column", "")
    value_col = args.get("value_column", "")
    external = args.get("external_values", {})
    agg = args.get("agg", "sum")
    label = args.get("label", "对账")
    auto_chart = bool(args.get("auto_chart", True))

    if group_col not in ctx.columns:
        return f"分组列 `{group_col}` 不存在"
    if value_col not in ctx.columns:
        return f"数值列 `{value_col}` 不存在"
    if not isinstance(external, dict) or not external:
        return "external_values 必须是 {分组名: 外部数值} 的 JSON 对象"

    buckets: dict[str, list[float]] = {}
    for r in ctx.rows:
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
                "internal": [(k, iv) for k, iv, *_ in rows],
                "external": [(k, ev) for k, _, ev, *_ in rows],
                "diff": [(k, dv) for k, _, _, dv, _ in rows],
            },
        ))

    return _truncate("\n".join(lines))


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
        name="list_columns",
        description="列出数据集所有列、类型与基础统计。**首次必须先调用以获得 schema 概览。**",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_t_list_columns,
    ))

    reg.register(Tool(
        name="sample_rows",
        description="返回前 N 行数据（默认 5），可指定列。",
        parameters={
            "type": "object",
            "properties": {
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
            "properties": {"column": {"type": "string"}},
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
            "properties": {"column": {"type": "string"}},
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
