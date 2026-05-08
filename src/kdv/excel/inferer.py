"""Infer FieldSpec list from a sample Excel sheet.

Heuristics per column:
- numeric (int / float)
- date (recognized iso strings or datetime objects upstream)
- enum (small set of repeated values)
- array (list-separator characters)
- otherwise string
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from kdv.excel.reader import ExcelTable
from kdv.llm.schema import FieldSpec


_DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$"),
    re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$"),
    re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$"),
    re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"),
]
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+(\.\d+)?$")
_ARRAY_SEPS = ("、", ";", "；", "/", " | ", ", ")


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _looks_like_date(v: Any) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    if not isinstance(v, str):
        return False
    s = v.strip()
    return any(p.match(s) for p in _DATE_PATTERNS)


def _looks_like_int(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, float) and v.is_integer():
        return True
    if isinstance(v, str) and _INT_RE.match(v.strip()):
        return True
    return False


def _looks_like_float(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str) and _FLOAT_RE.match(v.strip()):
        return True
    return False


def _has_array_sep(s: str) -> bool:
    return any(sep in s for sep in _ARRAY_SEPS)


def _infer_one(name: str, values: list[Any]) -> FieldSpec:
    non_blank = [v for v in values if not _is_blank(v)]
    sample = next((v for v in non_blank), "")
    example = "" if _is_blank(sample) else str(sample)[:80]

    if not non_blank:
        return FieldSpec(name=name, type="string", description="", example="")

    n = len(non_blank)

    # date?
    if all(_looks_like_date(v) for v in non_blank):
        return FieldSpec(name=name, type="date", description="日期/时间", example=example)

    # numeric?
    if all(_looks_like_float(v) for v in non_blank):
        if all(_looks_like_int(v) for v in non_blank):
            return FieldSpec(name=name, type="integer", description="整数", example=example)
        return FieldSpec(name=name, type="number", description="数值", example=example)

    # boolean?
    bool_set = {"true", "false", "是", "否", "yes", "no"}
    if all(isinstance(v, bool) or (isinstance(v, str) and v.strip().lower() in bool_set) for v in non_blank):
        return FieldSpec(name=name, type="boolean", description="布尔", example=example)

    # array (list separators)? — check before enum so multi-valued strings win
    if all(isinstance(v, str) and _has_array_sep(v) for v in non_blank):
        return FieldSpec(name=name, type="array", description="多值字符串数组", example=example)

    # enum: requires actual repetition — distinct count must be strictly less
    # than sample count, capped at a small cardinality, with short values.
    str_vals = [str(v).strip() for v in non_blank]
    distinct = sorted(set(str_vals))
    has_repeats = len(distinct) < n
    if (
        has_repeats
        and 1 < len(distinct) <= min(8, max(2, n // 2))
        and all(len(s) <= 30 for s in distinct)
    ):
        return FieldSpec(
            name=name,
            type="enum",
            description="枚举",
            enum_values=distinct,
            example=example,
        )

    return FieldSpec(name=name, type="string", description="", example=example)


def infer_fields_from_sample(table: ExcelTable) -> list[FieldSpec]:
    """Infer one FieldSpec per column from the sample table."""
    if not table.columns:
        return []
    fields: list[FieldSpec] = []
    for col in table.columns:
        values = table.column_values(col)
        fields.append(_infer_one(col, values))
    return fields
