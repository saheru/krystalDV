from pathlib import Path

import openpyxl

from kdv.excel.template import export_template_skeleton, load_output_template
from kdv.llm.schema import FieldSpec


def test_round_trip_skeleton(tmp_path: Path) -> None:
    fields = [
        FieldSpec(name="情绪", type="enum", enum_values=["正向", "负向", "中性"], description="客户情绪", required=True),
        FieldSpec(name="评分", type="integer", description="1-5 分", example="4"),
        FieldSpec(name="标签", type="array", description="多个关键词"),
    ]
    out = tmp_path / "tpl.xlsx"
    export_template_skeleton(out, fields)

    res = load_output_template(out)
    assert res.source == "explicit"
    by_name = {f.name: f for f in res.fields}
    assert by_name["情绪"].type == "enum"
    assert sorted(by_name["情绪"].enum_values) == ["中性", "正向", "负向"]
    assert by_name["评分"].type == "integer"
    assert by_name["标签"].type == "array"


def test_inferred_fallback_when_no_schema_sheet(tmp_path: Path) -> None:
    p = tmp_path / "sample_only.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["客户编号", "金额", "类别"])
    ws.append([1001, 12.5, "VIP"])
    ws.append([1002, 7.0, "普通"])
    ws.append([1003, 9.9, "VIP"])
    ws.append([1004, 3.2, "普通"])
    wb.save(p)

    res = load_output_template(p)
    assert res.source == "inferred"
    types = {f.name: f.type for f in res.fields}
    assert types["客户编号"] in ("integer", "number")
    assert types["金额"] in ("number", "integer")
    assert types["类别"] == "enum"
