"""Tests for batch-mode prompt building + JSON array extraction."""
from __future__ import annotations

from kdv.llm.client import _extract_json_array_from_text
from kdv.llm.prompts import build_batch_prompt
from kdv.llm.schema import FieldSpec, build_batch_tool_spec


def test_extract_plain_array():
    assert _extract_json_array_from_text('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]


def test_extract_fenced_array():
    text = "```json\n[{\"a\": 1}]\n```"
    assert _extract_json_array_from_text(text) == [{"a": 1}]


def test_extract_object_with_results_key():
    text = '{"results": [{"a": 1}, {"a": 2}]}'
    assert _extract_json_array_from_text(text) == [{"a": 1}, {"a": 2}]


def test_extract_returns_none_on_garbage():
    assert _extract_json_array_from_text("not json at all") is None


def test_extract_returns_none_when_array_holds_non_dicts():
    assert _extract_json_array_from_text("[1, 2, 3]") is None


def test_batch_tool_spec_wraps_items():
    fields = [FieldSpec(name="label", type="string"), FieldSpec(name="score", type="number")]
    spec = build_batch_tool_spec(fields)
    assert spec["function"]["name"] == "emit_batch"
    params = spec["function"]["parameters"]
    assert params["required"] == ["results"]
    assert params["properties"]["results"]["type"] == "array"
    item = params["properties"]["results"]["items"]
    assert "label" in item["properties"]
    assert "score" in item["properties"]


def test_build_batch_prompt_includes_count_and_rows():
    fields = [FieldSpec(name="情感", type="enum", enum_values=["正", "负"])]
    rows = [{"内容": "好评"}, {"内容": "差评"}, {"内容": "中性"}]
    sys_p, user_p = build_batch_prompt(
        system_extra="你是分析师",
        analysis_goal="判断情感",
        schema_fields=fields,
        rows=rows,
        use_function_calling=False,
    )
    # Each row appears once
    assert "好评" in user_p
    assert "差评" in user_p
    assert "中性" in user_p
    # Schema described once
    assert "情感" in sys_p
    # Count mentioned
    assert "3" in user_p
    # JSON array instruction (since FC off)
    assert "JSON 数组" in sys_p


def test_build_batch_prompt_function_calling_uses_tool_instruction():
    fields = [FieldSpec(name="x", type="string")]
    rows = [{"a": 1}]
    sys_p, _ = build_batch_prompt(
        system_extra="",
        analysis_goal="",
        schema_fields=fields,
        rows=rows,
        use_function_calling=True,
    )
    assert "emit_batch" in sys_p
