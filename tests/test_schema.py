from kdv.llm.schema import (
    FieldSpec,
    build_tool_spec,
    json_schema_from_fields,
    schema_describe_for_prompt,
)


def test_json_schema_basic_types():
    fields = [
        FieldSpec(name="title", type="string", description="标题", required=True),
        FieldSpec(name="score", type="number", required=False),
        FieldSpec(name="cat", type="enum", enum_values=["A", "B"]),
        FieldSpec(name="tags", type="array"),
        FieldSpec(name="when", type="date"),
    ]
    schema = json_schema_from_fields(fields)
    assert schema["type"] == "object"
    assert schema["properties"]["title"]["type"] == "string"
    assert schema["properties"]["score"]["type"] == "number"
    assert schema["properties"]["cat"]["enum"] == ["A", "B"]
    assert schema["properties"]["tags"]["type"] == "array"
    assert schema["properties"]["when"]["format"] == "date-time"
    assert "title" in schema["required"]
    assert "score" not in schema.get("required", [])


def test_tool_spec_wraps_schema():
    fields = [FieldSpec(name="x")]
    spec = build_tool_spec(fields)
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "emit_analysis"
    assert spec["function"]["parameters"]["properties"]["x"]["type"] == "string"


def test_describe_for_prompt():
    fields = [FieldSpec(name="x", type="enum", enum_values=["a", "b"])]
    text = schema_describe_for_prompt(fields)
    assert "`x`" in text and "enum" in text and "a" in text and "b" in text
