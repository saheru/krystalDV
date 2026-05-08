from pathlib import Path

from kdv.export.docx_export import export_docx
from kdv.export.payload import ChartImage, ExportPayload
from kdv.export.pptx_export import export_pptx


def _payload() -> ExportPayload:
    return ExportPayload(
        title="测试报告",
        subtitle="子标题",
        preset_name="my-preset",
        model_id="gpt-4o-mini",
        analysis_model_name="客服情感",
        mode="summary",
        kpis=[("总行数", "100"), ("成功率", "98.5%")],
        summary_markdown="## 概览\n\n- 第一条洞察\n- 第二条洞察\n\n### 细节\n\n这是一段文本。",
        sample_columns=["a", "b", "c"],
        sample_rows=[{"a": 1, "b": "x", "c": 3.14}, {"a": 2, "b": "y", "c": 2.71}],
        insights=[("关键警告", "**重要**：这里需要注意", "warning")],
        charts=[ChartImage(title="t1", rationale="why", png_bytes=b"")],
    )


def test_export_docx_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "report.docx"
    export_docx(_payload(), out)
    assert out.exists()
    assert out.stat().st_size > 5000  # not empty


def test_export_pptx_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "report.pptx"
    export_pptx(_payload(), out)
    assert out.exists()
    assert out.stat().st_size > 5000


def test_export_docx_with_no_charts_or_insights(tmp_path: Path) -> None:
    p = ExportPayload(title="空", subtitle="测试")
    out = tmp_path / "empty.docx"
    export_docx(p, out)
    assert out.exists()
