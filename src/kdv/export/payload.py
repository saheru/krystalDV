"""Snapshot of the result page's content to feed into docx/pptx exporters.

Built up by `ResultPage` at export time: it captures each chart widget to
a PNG, gathers KPI labels/values, the summary markdown, and a small data
sample. The exporters consume `ExportPayload` and don't touch any UI state.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QRect, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget


@dataclass
class ChartImage:
    title: str
    rationale: str
    png_bytes: bytes


@dataclass
class ExportPayload:
    title: str = "Krystal Data Vision 分析报告"
    subtitle: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    preset_name: str = ""
    model_id: str = ""
    analysis_model_name: str = ""
    mode: str = ""

    kpis: list[tuple[str, str]] = field(default_factory=list)  # [(label, value), ...]
    summary_markdown: str = ""

    charts: list[ChartImage] = field(default_factory=list)

    sample_columns: list[str] = field(default_factory=list)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)

    insights: list[tuple[str, str, str]] = field(default_factory=list)
    # [(title, body_markdown, severity), ...]


# ----------------------------------------------------------------------
def capture_widget_png(widget: QWidget, *, scale: float = 2.0) -> bytes:
    """Render a QWidget to a high-DPI PNG and return raw bytes.

    Uses QWidget.grab() which captures the widget's actual on-screen
    appearance — works for both QWidget-based PyQtGraph plots and the
    embedded matplotlib FigureCanvas.
    """
    if widget is None:
        return b""
    size = widget.size()
    if size.width() <= 0 or size.height() <= 0:
        size = widget.sizeHint()
    if size.width() <= 0 or size.height() <= 0:
        size = QSize(640, 480)

    target = QSize(int(size.width() * scale), int(size.height() * scale))
    pix = QPixmap(target)
    pix.setDevicePixelRatio(scale)
    pix.fill(Qt.white)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    widget.render(painter, QRect(0, 0, size.width(), size.height()).topLeft())
    painter.end()

    buf = io.BytesIO()
    pix.save(buf, "PNG")
    return buf.getvalue()


def write_export_payload(payload: ExportPayload, *, debug_dir: Path | None = None) -> None:
    """Optional debug helper — dump captured PNGs to a directory."""
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(payload.charts):
        (debug_dir / f"chart_{i:02d}.png").write_bytes(c.png_bytes)
