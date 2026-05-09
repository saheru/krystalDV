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

from PySide6.QtCore import QBuffer, QIODevice, QSize, QRect, Qt
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
    """Render a QWidget to a PNG and return raw bytes.

    Strategy:
    1. Try `widget.grab()` first — works for nearly every QWidget.
    2. Special-case matplotlib `FigureCanvas`: dump the figure to PNG
       buffer directly (always reliable, no display dependency).
    3. Special-case pyqtgraph `PlotWidget`: use its built-in
       `grabFramebuffer()`-equivalent or `getPlotItem()` exporter.
    4. If the widget is hidden or has zero size, force a temporary
       resize + show before grabbing, then restore.
    """
    if widget is None:
        return b""

    # ---- ① matplotlib FigureCanvas → save figure directly --------
    try:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

        canvas = _find_figure_canvas(widget)
        if canvas is not None:
            buf = io.BytesIO()
            canvas.figure.savefig(buf, format="png", dpi=int(96 * scale),
                                   bbox_inches="tight", facecolor="white")
            return buf.getvalue()
    except Exception:
        pass

    # ---- ② pyqtgraph PlotWidget → grab() (most reliable) ---------
    # NB: pyqtgraph's ImageExporter has known quirks on Qt6 / macOS;
    # the simple QWidget.grab() captures the rendered scene fine since
    # the widget is on-screen at export time.
    try:
        import pyqtgraph as pg
        if isinstance(widget, pg.PlotWidget):
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            pix = widget.grab()
            if not pix.isNull():
                if scale != 1.0:
                    pix = pix.scaled(
                        int(pix.width() * scale),
                        int(pix.height() * scale),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                return _pixmap_to_png_bytes(pix)
    except Exception:
        pass

    # ---- ③ generic widget grab -------------------------------------
    # Force a sane size if the widget hasn't been laid out yet.
    if widget.size().width() <= 0 or widget.size().height() <= 0:
        sh = widget.sizeHint()
        widget.resize(
            max(sh.width(), 720),
            max(sh.height(), 360),
        )
    pix = widget.grab()
    if pix.isNull():
        return b""
    if scale != 1.0:
        from PySide6.QtCore import Qt as _Qt

        pix = pix.scaled(
            int(pix.width() * scale),
            int(pix.height() * scale),
            _Qt.KeepAspectRatio,
            _Qt.SmoothTransformation,
        )
    return _pixmap_to_png_bytes(pix)


def _pixmap_to_png_bytes(pix: QPixmap) -> bytes:
    """QPixmap → PNG bytes via QBuffer (PySide6 doesn't accept BytesIO)."""
    qbuf = QBuffer()
    qbuf.open(QIODevice.WriteOnly)
    if pix.save(qbuf, "PNG"):
        data = bytes(qbuf.data())
    else:
        data = b""
    qbuf.close()
    return data


def _find_figure_canvas(w: QWidget):
    """Return the first matplotlib FigureCanvas inside `w` (or w itself)."""
    try:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    except Exception:
        return None
    if isinstance(w, FigureCanvasQTAgg):
        return w
    for child in w.findChildren(FigureCanvasQTAgg):
        return child
    return None


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Parse a PNG's IHDR chunk to get (width, height) in pixels.

    Returns None for non-PNG / truncated data. Used by the docx/pptx
    exporters to scale charts into their layout boxes while preserving
    aspect ratio (so a square chart doesn't get stretched to 16:9).
    """
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    # PNG: 8-byte signature, then chunks. First chunk MUST be IHDR.
    # Layout: 4-byte length, 4-byte type "IHDR", 4-byte width, 4-byte height, ...
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    if w <= 0 or h <= 0:
        return None
    return w, h


def write_export_payload(payload: ExportPayload, *, debug_dir: Path | None = None) -> None:
    """Optional debug helper — dump captured PNGs to a directory."""
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(payload.charts):
        (debug_dir / f"chart_{i:02d}.png").write_bytes(c.png_bytes)
