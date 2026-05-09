"""Export an ExportPayload to a styled .docx report.

Layout:
    1. Cover (large title + subtitle + metadata block)
    2. KPI summary (4-up grid via a borderless table)
    3. Executive summary (Markdown rendered as paragraphs/headings)
    4. Charts — one per page, each with title + caption
    5. Insights cards (collapsed list)
    6. Data sample table
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from kdv.export.payload import ExportPayload, png_dimensions


# ---- color palette (matches in-app QSS) ------------------------------
PRIMARY = RGBColor(0x5B, 0x6C, 0xFF)
TEXT = RGBColor(0x1F, 0x29, 0x37)
TEXT_MUTED = RGBColor(0x6B, 0x72, 0x80)
ACCENT_BG = RGBColor(0xEE, 0xF1, 0xFF)
SUCCESS = RGBColor(0x10, 0xB9, 0x81)
WARNING = RGBColor(0xF5, 0x9E, 0x0B)
DANGER = RGBColor(0xEF, 0x44, 0x44)


def _set_cell_bg(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _set_cell_borders(cell, *, color: str = "FFFFFF", size: int = 0) -> None:
    """Optional — used to remove visible borders on KPI tables."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single" if size > 0 else "nil")
        e.set(qn("w:sz"), str(size))
        e.set(qn("w:color"), color)
        borders.append(e)
    tc_pr.append(borders)


def _heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = True
    run.font.color.rgb = TEXT
    run.font.size = Pt({1: 22, 2: 16, 3: 13}.get(level, 13))
    if level == 1:
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(8)
    else:
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(4)


def _muted_p(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.color.rgb = TEXT_MUTED
    r.font.size = Pt(10)


def _body_p(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.color.rgb = TEXT
    r.font.size = Pt(11)


_FENCE = re.compile(r"^```")


def _render_markdown(doc: Document, md_text: str) -> None:
    """Lightweight Markdown → docx renderer covering headings, lists, paragraphs.

    Not a full Markdown parser; sufficient for the LLM-generated reports
    we emit (which use only a small subset).
    """
    in_code = False
    for raw in (md_text or "").splitlines():
        line = raw.rstrip()
        if _FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            p = doc.add_paragraph()
            r = p.add_run(line)
            r.font.name = "Menlo"
            r.font.size = Pt(10)
            r.font.color.rgb = TEXT_MUTED
            continue
        if not line.strip():
            doc.add_paragraph()
            continue
        if line.startswith("### "):
            _heading(doc, line[4:].strip(), level=3)
            continue
        if line.startswith("## "):
            _heading(doc, line[3:].strip(), level=2)
            continue
        if line.startswith("# "):
            _heading(doc, line[2:].strip(), level=1)
            continue
        if line.startswith(("- ", "* ")):
            p = doc.add_paragraph(style="List Bullet")
            _add_inline_runs(p, line[2:].strip())
            continue
        m = re.match(r"^\d+\.\s+(.*)", line)
        if m:
            p = doc.add_paragraph(style="List Number")
            _add_inline_runs(p, m.group(1))
            continue
        # Plain paragraph
        p = doc.add_paragraph()
        _add_inline_runs(p, line)


_INLINE = re.compile(r"(\*\*[^\*]+\*\*|\*[^\*]+\*|`[^`]+`)")


def _add_inline_runs(p, text: str) -> None:
    """Bold (**…**), italic (*…*), inline-code (`…`), keep plain everywhere else."""
    parts = _INLINE.split(text)
    for part in parts:
        if not part:
            continue
        run = p.add_run()
        run.font.size = Pt(11)
        run.font.color.rgb = TEXT
        if part.startswith("**") and part.endswith("**"):
            run.bold = True
            run.text = part[2:-2]
        elif part.startswith("*") and part.endswith("*"):
            run.italic = True
            run.text = part[1:-1]
        elif part.startswith("`") and part.endswith("`"):
            run.font.name = "Menlo"
            run.font.size = Pt(10)
            run.text = part[1:-1]
            run.font.color.rgb = PRIMARY
        else:
            run.text = part


# ----------------------------------------------------------------------
def _add_cover(doc: Document, payload: ExportPayload) -> None:
    section = doc.sections[0]
    section.top_margin = Cm(2.4)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.4)
    section.right_margin = Cm(2.4)

    # Brand strip
    p = doc.add_paragraph()
    r = p.add_run("◆  Krystal Data Vision")
    r.font.color.rgb = PRIMARY
    r.font.size = Pt(11)
    r.bold = True

    # Title
    title_p = doc.add_paragraph()
    tr = title_p.add_run(payload.title)
    tr.bold = True
    tr.font.size = Pt(32)
    tr.font.color.rgb = TEXT
    title_p.paragraph_format.space_before = Pt(80)
    title_p.paragraph_format.space_after = Pt(8)

    if payload.subtitle:
        sp = doc.add_paragraph()
        sr = sp.add_run(payload.subtitle)
        sr.font.size = Pt(14)
        sr.font.color.rgb = TEXT_MUTED

    # Metadata
    meta_lines = [
        ("生成时间", payload.generated_at),
        ("LLM 配置", payload.preset_name or "—"),
        ("使用模型", payload.model_id or "—"),
        ("分析模型", payload.analysis_model_name or "—"),
        ("分析模式", payload.mode or "—"),
    ]
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(72)
    table = doc.add_table(rows=len(meta_lines), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, (k, v) in enumerate(meta_lines):
        kc = table.cell(i, 0)
        vc = table.cell(i, 1)
        kc.width = Cm(3.6)
        vc.width = Cm(11.0)
        kp = kc.paragraphs[0]
        kr = kp.add_run(k)
        kr.font.color.rgb = TEXT_MUTED
        kr.font.size = Pt(10)
        vp = vc.paragraphs[0]
        vr = vp.add_run(str(v))
        vr.font.color.rgb = TEXT
        vr.font.size = Pt(11)
        vr.bold = True
        _set_cell_borders(kc)
        _set_cell_borders(vc)

    doc.add_page_break()


def _add_kpis(doc: Document, payload: ExportPayload) -> None:
    if not payload.kpis:
        return
    _heading(doc, "总览", level=1)
    cols = min(4, max(1, len(payload.kpis)))
    rows = (len(payload.kpis) + cols - 1) // cols
    table = doc.add_table(rows=rows, cols=cols)
    table.autofit = True
    flat: list = list(payload.kpis) + [("", "")] * (rows * cols - len(payload.kpis))
    for r in range(rows):
        for c in range(cols):
            label, value = flat[r * cols + c]
            cell = table.cell(r, c)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            _set_cell_bg(cell, "F7F8FA")
            _set_cell_borders(cell)
            # Value
            vp = cell.paragraphs[0]
            vp.alignment = WD_ALIGN_PARAGRAPH.LEFT
            vr = vp.add_run(value)
            vr.bold = True
            vr.font.size = Pt(20)
            vr.font.color.rgb = PRIMARY
            # Label
            lp = cell.add_paragraph()
            lr = lp.add_run(label)
            lr.font.color.rgb = TEXT_MUTED
            lr.font.size = Pt(9)


def _add_summary(doc: Document, payload: ExportPayload) -> None:
    if not payload.summary_markdown:
        return
    _heading(doc, "整表汇总洞察", level=1)
    _render_markdown(doc, payload.summary_markdown)


def _add_insights(doc: Document, payload: ExportPayload) -> None:
    if not payload.insights:
        return
    _heading(doc, "关键洞察", level=1)
    for title, body, severity in payload.insights:
        sev_color = {"warning": WARNING, "success": SUCCESS}.get(severity, PRIMARY)
        if title:
            p = doc.add_paragraph()
            r = p.add_run(f"●  {title}")
            r.bold = True
            r.font.size = Pt(12)
            r.font.color.rgb = sev_color
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(2)
        _render_markdown(doc, body)


# A4 portrait minus 2.4cm + 2.4cm side margins ≈ 16.2cm usable width.
# Page height with 2.4cm top + 2.0cm bottom margins ≈ 24.3cm usable, but we
# leave room for the chart heading + analysis paragraph below, so cap at 17cm.
_DOC_CHART_MAX_W_CM = 15.5
_DOC_CHART_MAX_H_CM = 17.0


def _add_chart_picture(doc: Document, png: bytes) -> None:
    """Insert a chart image while keeping it within the page box.

    Previously we passed only `width=Cm(15.5)`, which lets python-docx
    derive height — fine for landscape charts but tall charts (e.g.
    many-category horizontal bars) would push past the bottom margin
    and get clipped onto the next page. Now we cap whichever dimension
    is the limiting one.
    """
    dims = png_dimensions(png)
    if dims is None:
        doc.add_picture(io.BytesIO(png), width=Cm(_DOC_CHART_MAX_W_CM))
        return
    iw, ih = dims
    img_aspect = iw / ih
    box_aspect = _DOC_CHART_MAX_W_CM / _DOC_CHART_MAX_H_CM
    if img_aspect >= box_aspect:
        # Wider than the box → width is the limit; height stays inside.
        doc.add_picture(io.BytesIO(png), width=Cm(_DOC_CHART_MAX_W_CM))
    else:
        # Taller than the box → height is the limit (prevents page-clip).
        doc.add_picture(io.BytesIO(png), height=Cm(_DOC_CHART_MAX_H_CM))
    # Center the image — `add_picture` places it in a new paragraph at the
    # end of the document, so we can grab and re-align that paragraph.
    if doc.paragraphs:
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER


def _add_charts(doc: Document, payload: ExportPayload) -> None:
    if not payload.charts:
        return
    doc.add_page_break()
    _heading(doc, "图表", level=1)
    for ci in payload.charts:
        _heading(doc, ci.title, level=2)
        if ci.png_bytes:
            _add_chart_picture(doc, ci.png_bytes)
        else:
            _muted_p(doc, "（图表截图获取失败，请重新点击导出）")
        # When the rationale is short → small caption only.
        # When it's long (LLM-generated) → full sub-heading + paragraph.
        if ci.rationale:
            if len(ci.rationale) > 30:
                p = doc.add_paragraph()
                run = p.add_run("📊 分析说明")
                run.bold = True
                run.font.color.rgb = PRIMARY
                run.font.size = Pt(12)
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(2)
                _render_markdown(doc, ci.rationale)
            else:
                _muted_p(doc, ci.rationale)
        doc.add_paragraph()


def _add_credits(doc: Document, payload: ExportPayload) -> None:
    from kdv import __version__

    doc.add_page_break()
    _heading(doc, "关于", level=1)
    p = doc.add_paragraph()
    r = p.add_run("Krystal Data Vision")
    r.bold = True
    r.font.size = Pt(20)
    r.font.color.rgb = PRIMARY

    _muted_p(doc, "LLM 驱动的 Excel 数据分析工具")

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(20)

    info_table = doc.add_table(rows=3, cols=2)
    info_table.alignment = WD_TABLE_ALIGNMENT.LEFT
    rows_data = [
        ("版本", f"v{__version__}"),
        ("作品", "Leah Yao 作品"),
        ("鸣谢", "Chris Chen"),
    ]
    for i, (k, v) in enumerate(rows_data):
        kc = info_table.cell(i, 0)
        vc = info_table.cell(i, 1)
        kc.width = Cm(2.6)
        vc.width = Cm(12.0)
        kp = kc.paragraphs[0]
        kr = kp.add_run(k)
        kr.font.color.rgb = TEXT_MUTED
        kr.font.size = Pt(10)
        vp = vc.paragraphs[0]
        vr = vp.add_run(v)
        vr.font.color.rgb = TEXT
        vr.font.size = Pt(11)
        vr.bold = True
        _set_cell_borders(kc)
        _set_cell_borders(vc)


def _add_data_sample(doc: Document, payload: ExportPayload) -> None:
    if not payload.sample_rows or not payload.sample_columns:
        return
    doc.add_page_break()
    _heading(doc, "数据样本", level=1)
    _muted_p(doc, f"前 {len(payload.sample_rows)} 行数据")

    cols = payload.sample_columns
    table = doc.add_table(rows=1 + len(payload.sample_rows), cols=len(cols))
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0]
    for j, c in enumerate(cols):
        cell = hdr.cells[j]
        _set_cell_bg(cell, "5B6CFF")
        run = cell.paragraphs[0].add_run(str(c))
        run.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        run.font.size = Pt(10)
    for i, row in enumerate(payload.sample_rows, start=1):
        for j, c in enumerate(cols):
            v = row.get(c)
            cell = table.rows[i].cells[j]
            cell.text = "" if v is None else str(v)[:160]
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
                    r.font.color.rgb = TEXT


# ======================================================================
def export_docx(payload: ExportPayload, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    # Set base style
    base = doc.styles["Normal"]
    base.font.name = "PingFang SC"
    base.font.size = Pt(11)

    _add_cover(doc, payload)
    _add_kpis(doc, payload)
    _add_summary(doc, payload)
    _add_insights(doc, payload)
    _add_charts(doc, payload)
    _add_data_sample(doc, payload)
    _add_credits(doc, payload)

    doc.save(output_path)
    return output_path
