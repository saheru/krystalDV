"""Export an ExportPayload to a styled .pptx deck.

Slide flow:
    1. Cover (gradient-style strip + title + metadata)
    2. KPI overview (4-up tiles)
    3. Summary insights — Markdown converted to bullet text
    4. Per-chart slides (one chart per slide, full-width)
    5. Data sample slide (table)
    6. Closing / credits slide
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Cm, Emu, Inches, Pt

from kdv.export.payload import ExportPayload


PRIMARY = RGBColor(0x5B, 0x6C, 0xFF)
PRIMARY_LIGHT = RGBColor(0x8B, 0x97, 0xFF)
TEXT = RGBColor(0x1F, 0x29, 0x37)
TEXT_MUTED = RGBColor(0x6B, 0x72, 0x80)
ACCENT_BG = RGBColor(0xEE, 0xF1, 0xFF)
SOFT_BG = RGBColor(0xF7, 0xF8, 0xFA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
SUCCESS = RGBColor(0x10, 0xB9, 0x81)
WARNING = RGBColor(0xF5, 0x9E, 0x0B)


# 16:9 widescreen — uses default Presentation() which is 16:9 in modern python-pptx
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def _add_blank_slide(prs: Presentation):
    blank_layout = prs.slide_layouts[6]  # 6 = blank
    return prs.slides.add_slide(blank_layout)


def _set_solid_fill(shape, rgb: RGBColor) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb
    shape.line.fill.background()


def _add_text(
    slide,
    *,
    text: str,
    left, top, width, height,
    size: int = 14,
    bold: bool = False,
    color: RGBColor = TEXT,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    if not text:
        text = " "
    lines = str(text).splitlines() or [""]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.bold = bold
        run.font.name = "PingFang SC"
    return tb


def _add_rect(slide, *, left, top, width, height, fill: RGBColor):
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    sh.line.fill.background()
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    return sh


def _add_rounded_rect(slide, *, left, top, width, height, fill: RGBColor):
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    sh.adjustments[0] = 0.10
    sh.line.fill.background()
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    return sh


# ----------------------------------------------------------------------
def _slide_cover(prs: Presentation, payload: ExportPayload) -> None:
    s = _add_blank_slide(prs)
    # Background
    _add_rect(s, left=0, top=0, width=SLIDE_W, height=SLIDE_H, fill=WHITE)
    # Top accent stripe
    _add_rect(s, left=0, top=0, width=SLIDE_W, height=Inches(0.45), fill=PRIMARY)
    # Brand
    _add_text(
        s,
        text="◆  Krystal Data Vision",
        left=Inches(0.5), top=Inches(0.95), width=Inches(7), height=Inches(0.5),
        size=14, bold=True, color=PRIMARY,
    )
    # Title
    _add_text(
        s,
        text=payload.title or "数据分析报告",
        left=Inches(0.5), top=Inches(2.5), width=Inches(12), height=Inches(1.4),
        size=44, bold=True, color=TEXT,
    )
    if payload.subtitle:
        _add_text(
            s,
            text=payload.subtitle,
            left=Inches(0.5), top=Inches(3.9), width=Inches(12), height=Inches(0.8),
            size=18, color=TEXT_MUTED,
        )
    # Metadata block
    meta = [
        f"生成时间    {payload.generated_at}",
        f"LLM 配置    {payload.preset_name or '—'}",
        f"使用模型    {payload.model_id or '—'}",
        f"分析模型    {payload.analysis_model_name or '—'}",
        f"分析模式    {payload.mode or '—'}",
    ]
    _add_text(
        s,
        text="\n".join(meta),
        left=Inches(0.5), top=Inches(5.6), width=Inches(8), height=Inches(1.8),
        size=12, color=TEXT_MUTED,
    )
    # Bottom accent stripe
    _add_rect(s, left=0, top=SLIDE_H - Inches(0.18), width=SLIDE_W, height=Inches(0.18), fill=PRIMARY_LIGHT)


def _slide_kpis(prs: Presentation, payload: ExportPayload) -> None:
    if not payload.kpis:
        return
    s = _add_blank_slide(prs)
    _add_section_header(s, title="总览 KPI")

    items = payload.kpis[:6]
    cols = min(3, len(items))
    rows = (len(items) + cols - 1) // cols
    margin = Inches(0.5)
    gap = Inches(0.25)
    avail_w = SLIDE_W - margin * 2 - gap * (cols - 1)
    cell_w = avail_w / cols
    cell_h = Inches(1.6)
    top0 = Inches(2.0)

    for i, (label, value) in enumerate(items):
        r = i // cols
        c = i % cols
        x = margin + c * (cell_w + gap)
        y = top0 + r * (cell_h + gap)
        _add_rounded_rect(s, left=x, top=y, width=cell_w, height=cell_h, fill=SOFT_BG)
        _add_text(
            s, text=value,
            left=x + Inches(0.25), top=y + Inches(0.25),
            width=cell_w - Inches(0.5), height=Inches(0.85),
            size=32, bold=True, color=PRIMARY,
        )
        _add_text(
            s, text=label,
            left=x + Inches(0.25), top=y + Inches(1.0),
            width=cell_w - Inches(0.5), height=Inches(0.45),
            size=11, color=TEXT_MUTED,
        )


def _add_section_header(slide, *, title: str) -> None:
    _add_rect(slide, left=Inches(0.5), top=Inches(0.6), width=Inches(0.18), height=Inches(0.6), fill=PRIMARY)
    _add_text(
        slide, text=title,
        left=Inches(0.85), top=Inches(0.55), width=Inches(11), height=Inches(0.7),
        size=24, bold=True, color=TEXT,
    )


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_INLINE = re.compile(r"(\*\*[^\*]+\*\*|\*[^\*]+\*|`[^`]+`)")


def _slide_summary(prs: Presentation, payload: ExportPayload) -> None:
    if not payload.summary_markdown:
        return
    # Split markdown into sections by ## headings; one slide per section
    sections = _split_markdown_into_sections(payload.summary_markdown)
    if not sections:
        sections = [("整表汇总洞察", payload.summary_markdown)]
    for sec_title, body in sections:
        s = _add_blank_slide(prs)
        _add_section_header(s, title=sec_title or "洞察")
        tb = s.shapes.add_textbox(Inches(0.85), Inches(1.6), Inches(11.6), Inches(5.4))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_top = Emu(0)
        first = True
        for line in body.splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            if line.startswith("### "):
                p = tf.paragraphs[0] if first else tf.add_paragraph()
                p.text = ""
                run = p.add_run()
                run.text = line[4:].strip()
                run.font.bold = True
                run.font.size = Pt(15)
                run.font.color.rgb = PRIMARY
                run.font.name = "PingFang SC"
                first = False
                continue
            if line.startswith(("- ", "* ")):
                content = line[2:].strip()
                p = tf.paragraphs[0] if first else tf.add_paragraph()
                p.text = ""
                p.level = 0
                _add_inline_runs_pptx(p, "•  " + content)
                first = False
                continue
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            p.text = ""
            _add_inline_runs_pptx(p, line)
            first = False


def _split_markdown_into_sections(md: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    cur_title = ""
    cur_body: list[str] = []
    for raw in md.splitlines():
        m = _HEADING_RE.match(raw.strip())
        if m and len(m.group(1)) <= 2:
            if cur_body:
                sections.append((cur_title, "\n".join(cur_body)))
            cur_title = m.group(2).strip()
            cur_body = []
        else:
            cur_body.append(raw)
    if cur_body:
        sections.append((cur_title, "\n".join(cur_body)))
    return [(t, b) for (t, b) in sections if b.strip()]


def _add_inline_runs_pptx(paragraph, text: str) -> None:
    parts = _INLINE.split(text)
    for part in parts:
        if not part:
            continue
        run = paragraph.add_run()
        run.font.size = Pt(13)
        run.font.color.rgb = TEXT
        run.font.name = "PingFang SC"
        if part.startswith("**") and part.endswith("**"):
            run.font.bold = True
            run.text = part[2:-2]
        elif part.startswith("*") and part.endswith("*"):
            run.font.italic = True
            run.text = part[1:-1]
        elif part.startswith("`") and part.endswith("`"):
            run.font.name = "Menlo"
            run.font.size = Pt(12)
            run.font.color.rgb = PRIMARY
            run.text = part[1:-1]
        else:
            run.text = part


def _slide_insights(prs: Presentation, payload: ExportPayload) -> None:
    if not payload.insights:
        return
    s = _add_blank_slide(prs)
    _add_section_header(s, title="关键洞察")
    top = Inches(1.6)
    for title, body, severity in payload.insights[:6]:
        sev_color = {"warning": WARNING, "success": SUCCESS}.get(severity, PRIMARY)
        _add_rect(s, left=Inches(0.5), top=top, width=Inches(0.06), height=Inches(0.9), fill=sev_color)
        if title:
            _add_text(
                s, text=title,
                left=Inches(0.7), top=top - Inches(0.05),
                width=Inches(11.5), height=Inches(0.4),
                size=14, bold=True, color=sev_color,
            )
        _add_text(
            s, text=body[:400],
            left=Inches(0.7), top=top + Inches(0.32),
            width=Inches(11.5), height=Inches(0.6),
            size=11, color=TEXT,
        )
        top += Inches(1.0)


def _slide_chart(prs: Presentation, *, title: str, rationale: str, png: bytes) -> None:
    s = _add_blank_slide(prs)
    _add_section_header(s, title=title)
    if rationale:
        _add_text(
            s, text=rationale,
            left=Inches(0.85), top=Inches(1.25),
            width=Inches(11.5), height=Inches(0.4),
            size=11, color=TEXT_MUTED,
        )
    if png:
        # Place image centred horizontally with max-width 12 in
        img_left = Inches(0.85)
        img_top = Inches(1.8)
        img_width = Inches(11.5)
        img_height = Inches(5.2)
        s.shapes.add_picture(io.BytesIO(png), img_left, img_top,
                             width=img_width, height=img_height)


def _slide_data_sample(prs: Presentation, payload: ExportPayload) -> None:
    if not payload.sample_rows or not payload.sample_columns:
        return
    s = _add_blank_slide(prs)
    _add_section_header(s, title="数据样本")
    cols = payload.sample_columns
    rows_n = min(8, len(payload.sample_rows))
    cols_n = min(len(cols), 8)
    visible_cols = cols[:cols_n]

    table_shape = s.shapes.add_table(
        rows=rows_n + 1, cols=cols_n,
        left=Inches(0.5), top=Inches(1.6),
        width=SLIDE_W - Inches(1.0), height=Inches(0.5) + Inches(0.4) * rows_n,
    )
    tbl = table_shape.table
    for j, c in enumerate(visible_cols):
        cell = tbl.cell(0, j)
        cell.fill.solid()
        cell.fill.fore_color.rgb = PRIMARY
        for p in cell.text_frame.paragraphs:
            p.text = ""
        run = cell.text_frame.paragraphs[0].add_run()
        run.text = str(c)
        run.font.bold = True
        run.font.color.rgb = WHITE
        run.font.size = Pt(11)
        run.font.name = "PingFang SC"
    for i, row in enumerate(payload.sample_rows[:rows_n], start=1):
        for j, c in enumerate(visible_cols):
            v = row.get(c)
            cell = tbl.cell(i, j)
            for p in cell.text_frame.paragraphs:
                p.text = ""
            run = cell.text_frame.paragraphs[0].add_run()
            run.text = "" if v is None else str(v)[:80]
            run.font.size = Pt(10)
            run.font.color.rgb = TEXT
            run.font.name = "PingFang SC"


def _slide_credits(prs: Presentation, payload: ExportPayload) -> None:
    from kdv import __version__

    s = _add_blank_slide(prs)
    _add_rect(s, left=0, top=0, width=SLIDE_W, height=SLIDE_H, fill=PRIMARY)
    _add_text(
        s, text="◆ Krystal Data Vision",
        left=Inches(0.5), top=Inches(2.6), width=Inches(12), height=Inches(0.8),
        size=18, bold=True, color=WHITE, align=PP_ALIGN.CENTER,
    )
    _add_text(
        s, text="感谢使用",
        left=0, top=Inches(3.4), width=SLIDE_W, height=Inches(0.8),
        size=44, bold=True, color=WHITE, align=PP_ALIGN.CENTER,
    )
    _add_text(
        s,
        text=f"v{__version__}  ·  Leah Yao 作品  ·  鸣谢 Chris Chen",
        left=0, top=Inches(4.4), width=SLIDE_W, height=Inches(0.6),
        size=14, color=WHITE, align=PP_ALIGN.CENTER,
    )


# ======================================================================
def export_pptx(payload: ExportPayload, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    _slide_cover(prs, payload)
    _slide_kpis(prs, payload)
    _slide_summary(prs, payload)
    _slide_insights(prs, payload)
    for ci in payload.charts:
        _slide_chart(prs, title=ci.title, rationale=ci.rationale, png=ci.png_bytes)
    _slide_data_sample(prs, payload)
    _slide_credits(prs, payload)

    prs.save(output_path)
    return output_path
