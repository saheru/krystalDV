"""Column-level statistical summaries (used by KPIs, stats panel, recommender)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean, median, pstdev
from typing import Any, Literal

ColumnKind = Literal["numeric", "categorical", "boolean", "datetime", "text", "empty"]


@dataclass
class ColumnStats:
    name: str
    kind: ColumnKind
    count: int
    null_count: int
    distinct_count: int
    # numeric
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    stdev: float | None = None
    # categorical / text
    top_values: list[tuple[str, int]] | None = None
    avg_text_len: float | None = None
    # datetime
    earliest: str | None = None
    latest: str | None = None


def _try_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _try_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    if isinstance(v, str) and v:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(v, fmt)
            except ValueError:
                continue
    return None


def _classify(values: list[Any]) -> ColumnKind:
    non_null = [v for v in values if v not in (None, "")]
    if not non_null:
        return "empty"

    n = len(non_null)
    if all(isinstance(v, bool) for v in non_null):
        return "boolean"

    numeric_n = sum(1 for v in non_null if _try_float(v) is not None and not isinstance(v, bool))
    if numeric_n / n >= 0.9:
        return "numeric"

    dt_n = sum(1 for v in non_null if _try_dt(v) is not None)
    if dt_n / n >= 0.9:
        return "datetime"

    str_vals = [str(v) for v in non_null]
    avg_len = mean(len(s) for s in str_vals)
    distinct = len(set(str_vals))
    if avg_len > 30 or distinct > max(40, n // 2):
        return "text"
    return "categorical"


def _stats_for_column(name: str, values: list[Any]) -> ColumnStats:
    null_count = sum(1 for v in values if v in (None, ""))
    non_null = [v for v in values if v not in (None, "")]
    distinct = len({str(v) for v in non_null})
    kind = _classify(values)

    s = ColumnStats(
        name=name,
        kind=kind,
        count=len(values),
        null_count=null_count,
        distinct_count=distinct,
    )
    if not non_null:
        return s

    if kind == "numeric":
        nums = [f for f in (_try_float(v) for v in non_null) if f is not None]
        if nums:
            s.min = min(nums)
            s.max = max(nums)
            s.mean = mean(nums)
            s.median = median(nums)
            s.stdev = pstdev(nums) if len(nums) > 1 else 0.0
    elif kind == "datetime":
        dts = [d for d in (_try_dt(v) for v in non_null) if d is not None]
        if dts:
            s.earliest = min(dts).isoformat()
            s.latest = max(dts).isoformat()
    elif kind in ("categorical", "boolean"):
        c = Counter(str(v) for v in non_null)
        s.top_values = c.most_common(10)
    elif kind == "text":
        s.avg_text_len = mean(len(str(v)) for v in non_null)
        c = Counter(str(v) for v in non_null)
        s.top_values = c.most_common(5)
    return s


def summarize_columns(
    columns: list[str], rows: list[dict[str, Any]]
) -> dict[str, ColumnStats]:
    out: dict[str, ColumnStats] = {}
    for col in columns:
        out[col] = _stats_for_column(col, [r.get(col) for r in rows])
    return out
