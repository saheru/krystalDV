from kdv.excel.inferer import infer_fields_from_sample
from kdv.excel.reader import ExcelTable


def _table(columns, rows):
    return ExcelTable(columns=columns, rows=rows, sheet_name="t", source_path="")


def test_infer_numeric_and_int():
    t = _table(["a", "b"], [{"a": 1, "b": 1.5}, {"a": 2, "b": 2.7}])
    fs = {f.name: f for f in infer_fields_from_sample(t)}
    assert fs["a"].type == "integer"
    assert fs["b"].type == "number"


def test_infer_enum_with_repeats():
    rows = [{"k": v} for v in ("A", "B", "A", "B", "A")]
    t = _table(["k"], rows)
    f = infer_fields_from_sample(t)[0]
    assert f.type == "enum"
    assert sorted(f.enum_values) == ["A", "B"]


def test_infer_date_strings():
    t = _table(["d"], [{"d": "2024-01-01"}, {"d": "2024-02-15"}])
    f = infer_fields_from_sample(t)[0]
    assert f.type == "date"


def test_infer_array_when_separators_present():
    t = _table(["k"], [{"k": "a、b、c"}, {"k": "d、e"}])
    f = infer_fields_from_sample(t)[0]
    assert f.type == "array"


def test_infer_falls_back_to_string():
    t = _table(["k"], [{"k": "free text long sentence"}, {"k": "another text"}])
    f = infer_fields_from_sample(t)[0]
    assert f.type == "string"
