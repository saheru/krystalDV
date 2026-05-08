"""Auto-recommend chart types from column stats."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from kdv.viz.column_stats import ColumnStats


ChartKind = Literal[
    "bar",
    "bar_h",
    "line",
    "area",
    "scatter",
    "histogram",
    "box",
    "pie",
    "donut",
    "radar",
    "heatmap_corr",
    "wordcloud",
    "treemap",
    "stat_summary",
    "kpi",
    "pivot",
    "timeseries",
]


@dataclass
class ChartSuggestion:
    kind: ChartKind
    columns: list[str]
    title: str
    rationale: str = ""
    params: dict = field(default_factory=dict)


def recommend_charts(stats: dict[str, ColumnStats]) -> list[ChartSuggestion]:
    """Pick a useful default set of charts/tools given column statistics."""
    suggestions: list[ChartSuggestion] = []
    numeric = [s for s in stats.values() if s.kind == "numeric"]
    cats = [s for s in stats.values() if s.kind in ("categorical", "boolean")]
    times = [s for s in stats.values() if s.kind == "datetime"]
    texts = [s for s in stats.values() if s.kind == "text"]

    # Always: KPI bar + stats summary
    suggestions.append(ChartSuggestion("kpi", [], "总览 KPI", "数据集核心指标"))
    suggestions.append(
        ChartSuggestion("stat_summary", [s.name for s in stats.values()], "字段统计摘要", "每个字段的统计")
    )

    # Categorical → pie/donut + horizontal bar
    for cat in cats[:3]:
        suggestions.append(
            ChartSuggestion("donut", [cat.name], f"{cat.name} 分布", f"{cat.distinct_count} 个类别")
        )
        suggestions.append(
            ChartSuggestion("bar_h", [cat.name], f"{cat.name} 频次（横向）", "Top 取值频次")
        )

    # Numeric → histogram + box
    for num in numeric[:3]:
        suggestions.append(
            ChartSuggestion("histogram", [num.name], f"{num.name} 直方图", "数值分布")
        )
        suggestions.append(
            ChartSuggestion("box", [num.name], f"{num.name} 箱线图", "中位数 / 四分位 / 离群")
        )

    # Two numerics → scatter
    if len(numeric) >= 2:
        a, b = numeric[0].name, numeric[1].name
        suggestions.append(
            ChartSuggestion("scatter", [a, b], f"{a} vs {b} 散点图", "双变量相关")
        )

    # Time + numeric → timeseries / line
    if times and numeric:
        t = times[0].name
        n = numeric[0].name
        suggestions.append(
            ChartSuggestion("timeseries", [t, n], f"{n} 随 {t} 变化", "时间序列")
        )
        suggestions.append(
            ChartSuggestion("area", [t, n], f"{n} 面积图（按 {t}）", "累积趋势")
        )

    # Categorical + numeric → grouped bar
    if cats and numeric:
        c = cats[0].name
        n = numeric[0].name
        suggestions.append(
            ChartSuggestion("bar", [c, n], f"{c} 各类的 {n} 均值", "类别对比", params={"agg": "mean"})
        )

    # ≥3 numeric → correlation heatmap + radar
    if len(numeric) >= 3:
        nums = [n.name for n in numeric[:8]]
        suggestions.append(
            ChartSuggestion("heatmap_corr", nums, "数值列相关性热力图", "Pearson 相关系数")
        )
        suggestions.append(
            ChartSuggestion("radar", nums[:6], "多维雷达图", "归一化后的多维对比")
        )

    # Text → wordcloud
    for t in texts[:1]:
        suggestions.append(
            ChartSuggestion("wordcloud", [t.name], f"{t.name} 词云", "高频词")
        )

    # Categorical + numeric → treemap
    if cats and numeric:
        suggestions.append(
            ChartSuggestion(
                "treemap", [cats[0].name, numeric[0].name], f"{cats[0].name} 占比矩形树图", "类别面积比"
            )
        )

    # Pivot tool
    if cats and numeric:
        suggestions.append(
            ChartSuggestion(
                "pivot",
                [cats[0].name, numeric[0].name],
                "透视表",
                "可下钻的多维聚合",
                params={"row": cats[0].name, "value": numeric[0].name, "agg": "mean"},
            )
        )

    return suggestions
