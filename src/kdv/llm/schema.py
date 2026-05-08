"""Convert inferred field metadata to JSON Schema and OpenAI tool specs."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


FieldType = Literal["string", "number", "integer", "boolean", "enum", "array", "date"]


class FieldSpec(BaseModel):
    """One output field definition for an analysis model."""

    name: str
    type: FieldType = "string"
    description: str = ""
    required: bool = True
    enum_values: list[str] = Field(default_factory=list)
    example: str = ""


def _field_to_json_schema(f: FieldSpec) -> dict[str, Any]:
    desc = f.description or f.name
    if f.example:
        desc = f"{desc}（示例：{f.example}）" if desc else f"示例：{f.example}"

    if f.type == "string":
        return {"type": "string", "description": desc}
    if f.type == "number":
        return {"type": "number", "description": desc}
    if f.type == "integer":
        return {"type": "integer", "description": desc}
    if f.type == "boolean":
        return {"type": "boolean", "description": desc}
    if f.type == "date":
        return {"type": "string", "format": "date-time", "description": desc}
    if f.type == "enum":
        return {
            "type": "string",
            "enum": list(f.enum_values) or [""],
            "description": desc,
        }
    if f.type == "array":
        return {
            "type": "array",
            "items": {"type": "string"},
            "description": desc,
        }
    return {"type": "string", "description": desc}


def json_schema_from_fields(fields: list[FieldSpec]) -> dict[str, Any]:
    """Build a JSON Schema 'object' from a list of FieldSpecs."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in fields:
        properties[f.name] = _field_to_json_schema(f)
        if f.required:
            required.append(f.name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def build_tool_spec(fields: list[FieldSpec], tool_name: str = "emit_analysis") -> dict[str, Any]:
    """Wrap the schema as an OpenAI 'tools' entry for function calling."""
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "输出本次数据分析的结构化结果，所有字段必须严格按定义填充。",
            "parameters": json_schema_from_fields(fields),
        },
    }


def schema_describe_for_prompt(fields: list[FieldSpec]) -> str:
    """Produce a human-readable schema description for prompt fallback."""
    lines = []
    for f in fields:
        bits = [f"- `{f.name}` ({f.type}"]
        if f.type == "enum" and f.enum_values:
            bits.append(f", 取值 {f.enum_values}")
        bits.append(")")
        if f.description:
            bits.append(f"：{f.description}")
        if f.example:
            bits.append(f"（示例：{f.example}）")
        if f.required:
            bits.append(" [必填]")
        lines.append("".join(bits))
    return "\n".join(lines)
