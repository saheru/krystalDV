"""Prompt templates for row-level and whole-table analysis."""
from __future__ import annotations

from typing import Any

from kdv.llm.schema import FieldSpec, schema_describe_for_prompt


SYSTEM_TEMPLATES: dict[str, str] = {
    "general": "你是一名严谨的数据分析师，会根据用户提供的字段结构对每条数据做客观、简洁、可量化的分析。",
    "sentiment": "你是一名客户体验专家，擅长从客服会话/评论文本中提取情感倾向、关键问题点与改进建议。",
    "ticket_classify": "你是一名工单分类专家，能根据描述准确归类工单类别、优先级、责任团队，并提取关键实体。",
    "survey_open": "你是一名调研分析师，擅长从开放题回答中归纳主题、提取观点、识别情绪与建议。",
    "general_eng": "You are a rigorous data analyst. Analyze each input row strictly according to the schema.",
}


def build_row_prompt(
    *,
    system_extra: str,
    analysis_goal: str,
    schema_fields: list[FieldSpec],
    row: dict[str, Any],
    use_function_calling: bool,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a single-row analysis."""
    schema_desc = schema_describe_for_prompt(schema_fields)
    system_parts = [system_extra.strip(), "\n输出字段定义：\n" + schema_desc]
    if not use_function_calling:
        system_parts.append(
            "\n严格要求：仅输出一个 JSON 对象，键为上述字段名，值符合类型定义。不要输出任何额外文本、注释或 Markdown 围栏。"
        )
    if analysis_goal:
        system_parts.append(f"\n本次分析目标：{analysis_goal}")
    system_prompt = "\n".join(p for p in system_parts if p)

    row_lines = [f"- {k}: {_format_value(v)}" for k, v in row.items()]
    user_prompt = "请分析以下数据条目：\n" + "\n".join(row_lines)
    return system_prompt, user_prompt


def build_batch_prompt(
    *,
    system_extra: str,
    analysis_goal: str,
    schema_fields: list[FieldSpec],
    rows: list[dict[str, Any]],
    use_function_calling: bool,
) -> tuple[str, str]:
    """Build a single prompt for analysing N rows in one LLM call.

    The model is instructed to return an array of N objects, one per input row,
    in the same order. The schema description is sent ONCE per batch instead of
    once per row — that's where the token saving comes from.
    """
    schema_desc = schema_describe_for_prompt(schema_fields)
    parts = [system_extra.strip(), "\n输出字段定义（每条数据要按此结构输出）：\n" + schema_desc]
    if not use_function_calling:
        parts.append(
            "严格要求：输出一个 JSON 数组，元素数量必须严格等于输入数据的条数，"
            "顺序与输入一一对应。每个元素是一个对象，键为上述字段名。"
            "不要输出任何额外文本、注释或 Markdown 围栏。"
        )
    else:
        parts.append(
            "请通过 emit_batch 工具输出一个 results 数组，长度严格等于输入条数，顺序对齐。"
        )
    if analysis_goal:
        parts.append(f"\n本次分析目标：{analysis_goal}")
    system_prompt = "\n".join(p for p in parts if p)

    user_lines = [f"共 {len(rows)} 条数据：\n"]
    for i, r in enumerate(rows):
        user_lines.append(f"--- 数据 #{i + 1} ---")
        for k, v in r.items():
            user_lines.append(f"  {k}: {_format_value(v)}")
        user_lines.append("")
    user_prompt = "\n".join(user_lines)
    return system_prompt, user_prompt


def build_summary_prompt(
    *,
    system_extra: str,
    analysis_goal: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    output_fields: list[FieldSpec] | None = None,
    use_function_calling: bool = False,
    sample_limit: int = 200,
) -> tuple[str, str]:
    """Build a whole-table summary prompt (with sampling)."""
    sampled = rows[: max(1, sample_limit)]
    table_md = _rows_to_markdown_table(columns, sampled)
    truncated_note = ""
    if len(rows) > len(sampled):
        truncated_note = f"\n\n（注：原始共 {len(rows)} 行，已采样前 {len(sampled)} 行进行分析。）"

    system_parts = [system_extra.strip()]
    if output_fields:
        system_parts.append("输出结构定义：\n" + schema_describe_for_prompt(output_fields))
        if not use_function_calling:
            system_parts.append("仅输出一个 JSON 对象，不要输出任何其他文本或 Markdown 围栏。")
    else:
        system_parts.append(
            "请输出一份 Markdown 报告，包含以下章节：## 关键洞察 / ## 数据质量观察 / ## 分布与异常 / ## 建议行动。"
        )
    if analysis_goal:
        system_parts.append(f"分析目标：{analysis_goal}")
    system_prompt = "\n\n".join(p for p in system_parts if p)

    user_prompt = f"以下是数据集 ({len(sampled)} 行 × {len(columns)} 列)：\n\n{table_md}{truncated_note}"
    return system_prompt, user_prompt


def _format_value(v: Any) -> str:
    if v is None:
        return ""
    s = str(v)
    if len(s) > 1000:
        return s[:1000] + " …"
    return s


def _rows_to_markdown_table(columns: list[str], rows: list[dict[str, Any]]) -> str:
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    body_lines = []
    for r in rows:
        cells = []
        for c in columns:
            v = _format_value(r.get(c, "")).replace("|", "\\|").replace("\n", " ")
            if len(v) > 200:
                v = v[:200] + "…"
            cells.append(v)
        body_lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, sep, *body_lines])
