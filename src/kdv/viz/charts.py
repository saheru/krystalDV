"""Chart widget factory.

pyqtgraph for interactive charts (bar/line/area/scatter/histogram/box/radar);
matplotlib (embedded as FigureCanvasQTAgg) for the more elaborate ones
(pie/donut/heatmap/treemap/wordcloud/pivot heatmap).

All factories return a QWidget — the result page just lays them out in a grid.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kdv.viz.column_stats import ColumnStats


# ----------------------------------------------------------------------
# Palette (single hardcoded modern palette — no theme switching)
# ----------------------------------------------------------------------
PALETTE = [
    "#5B6CFF",
    "#10B981",
    "#F59E0B",
    "#EF4444",
    "#3B82F6",
    "#8B5CF6",
    "#EC4899",
    "#14B8A6",
    "#F97316",
    "#84CC16",
]
BG = "#FFFFFF"
FG = "#1F2937"
GRID = "#E5E7EB"

pg.setConfigOption("background", BG)
pg.setConfigOption("foreground", FG)
pg.setConfigOption("antialias", True)


def _color(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def _to_floats(values: list[Any]) -> list[float]:
    out: list[float] = []
    for v in values:
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _to_strings(values: list[Any]) -> list[str]:
    return [str(v) for v in values if v not in (None, "")]


def _try_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(v, fmt)
            except ValueError:
                continue
    return None


# ----------------------------------------------------------------------
def _new_plot() -> pg.PlotWidget:
    p = pg.PlotWidget()
    p.setBackground(BG)
    p.showGrid(x=True, y=True, alpha=0.18)
    p.getAxis("left").setPen(GRID)
    p.getAxis("bottom").setPen(GRID)
    p.getAxis("left").setTextPen(FG)
    p.getAxis("bottom").setTextPen(FG)
    return p


# ----------------------------------------------------------------------
def make_bar(values: list[Any], *, horizontal: bool = False, color_idx: int = 0) -> QWidget:
    """Vertical or horizontal bar chart of a categorical column's frequencies."""
    items = Counter(_to_strings(values)).most_common(15)
    if not items:
        return _empty("无可绘制数据")
    labels = [k for k, _ in items]
    counts = [c for _, c in items]
    p = _new_plot()
    xs = list(range(len(labels)))
    color = _color(color_idx)
    if horizontal:
        bg = pg.BarGraphItem(x0=0, y=xs, height=0.7, width=counts, brush=color, pen=color)
        p.addItem(bg)
        p.getAxis("left").setTicks([list(zip(xs, labels))])
        p.invertY(True)
    else:
        bg = pg.BarGraphItem(x=xs, height=counts, width=0.7, brush=color, pen=color)
        p.addItem(bg)
        p.getAxis("bottom").setTicks([list(zip(xs, labels))])
    return p


def make_grouped_bar(
    cat_values: list[Any], num_values: list[Any], *, agg: str = "mean", color_idx: int = 1
) -> QWidget:
    """Bar chart of an aggregate of `num_values` grouped by `cat_values`."""
    pairs = [
        (str(c), float(n))
        for c, n in zip(cat_values, num_values)
        if c not in (None, "") and _is_num(n)
    ]
    if not pairs:
        return _empty("无可绘制数据")
    groups: dict[str, list[float]] = {}
    for c, n in pairs:
        groups.setdefault(c, []).append(n)
    items = []
    for k, vs in groups.items():
        if agg == "sum":
            items.append((k, sum(vs)))
        elif agg == "max":
            items.append((k, max(vs)))
        elif agg == "min":
            items.append((k, min(vs)))
        elif agg == "count":
            items.append((k, len(vs)))
        else:
            items.append((k, sum(vs) / len(vs)))
    items.sort(key=lambda x: x[1], reverse=True)
    items = items[:15]
    p = _new_plot()
    xs = list(range(len(items)))
    bg = pg.BarGraphItem(
        x=xs, height=[v for _, v in items], width=0.7, brush=_color(color_idx), pen=_color(color_idx)
    )
    p.addItem(bg)
    p.getAxis("bottom").setTicks([list(zip(xs, [k for k, _ in items]))])
    return p


def make_line(x_values: list[Any], y_values: list[Any], *, color_idx: int = 0) -> QWidget:
    pairs = [(x, y) for x, y in zip(x_values, y_values) if _is_num(y)]
    if not pairs:
        return _empty("无可绘制数据")
    xs = [_x_axis(p[0], i) for i, p in enumerate(pairs)]
    ys = [float(p[1]) for p in pairs]
    p = _new_plot()
    p.plot(xs, ys, pen=pg.mkPen(_color(color_idx), width=2), symbol="o", symbolSize=6,
           symbolBrush=_color(color_idx), symbolPen=_color(color_idx))
    return p


def make_area(x_values: list[Any], y_values: list[Any], *, color_idx: int = 0) -> QWidget:
    pairs = [(x, y) for x, y in zip(x_values, y_values) if _is_num(y)]
    if not pairs:
        return _empty("无可绘制数据")
    xs = [_x_axis(p[0], i) for i, p in enumerate(pairs)]
    ys = [float(p[1]) for p in pairs]
    p = _new_plot()
    color = QColor(_color(color_idx))
    color.setAlpha(80)
    fill = pg.FillBetweenItem(
        pg.PlotDataItem(xs, ys),
        pg.PlotDataItem(xs, [0] * len(xs)),
        brush=color,
    )
    p.addItem(fill)
    p.plot(xs, ys, pen=pg.mkPen(_color(color_idx), width=2))
    return p


def make_scatter(x_values: list[Any], y_values: list[Any], *, color_idx: int = 2) -> QWidget:
    pairs = [(x, y) for x, y in zip(x_values, y_values) if _is_num(x) and _is_num(y)]
    if not pairs:
        return _empty("无可绘制数据")
    xs = [float(p[0]) for p in pairs]
    ys = [float(p[1]) for p in pairs]
    p = _new_plot()
    s = pg.ScatterPlotItem(
        xs, ys, pen=None, brush=_color(color_idx), size=8, symbol="o"
    )
    p.addItem(s)
    return p


def make_histogram(values: list[Any], *, bins: int = 20, color_idx: int = 0) -> QWidget:
    nums = _to_floats(values)
    if not nums:
        return _empty("无数值")
    arr = np.array(nums)
    y, x = np.histogram(arr, bins=min(bins, max(5, int(math.sqrt(len(arr))))))
    p = _new_plot()
    bg = pg.BarGraphItem(
        x=(x[:-1] + x[1:]) / 2,
        height=y,
        width=(x[1] - x[0]) * 0.95,
        brush=_color(color_idx),
        pen=_color(color_idx),
    )
    p.addItem(bg)
    return p


def make_box(values: list[Any], *, color_idx: int = 0) -> QWidget:
    """Single-column boxplot drawn manually with pyqtgraph primitives."""
    nums = _to_floats(values)
    if not nums:
        return _empty("无数值")
    arr = np.array(nums)
    q1, med, q3 = np.percentile(arr, [25, 50, 75])
    iqr = q3 - q1
    lo = max(arr.min(), q1 - 1.5 * iqr)
    hi = min(arr.max(), q3 + 1.5 * iqr)

    p = _new_plot()
    color = _color(color_idx)
    box = pg.BarGraphItem(x=[1], y0=[q1], height=[q3 - q1], width=0.4, brush=color, pen=color)
    p.addItem(box)
    p.plot([1], [med], pen=None, symbol="-", symbolSize=30, symbolPen=pg.mkPen(FG, width=2))
    # whiskers
    p.plot([1, 1], [lo, q1], pen=pg.mkPen(FG, width=1))
    p.plot([1, 1], [q3, hi], pen=pg.mkPen(FG, width=1))
    # outliers
    out = arr[(arr < lo) | (arr > hi)]
    if len(out):
        p.plot([1] * len(out), out.tolist(), pen=None, symbol="o", symbolSize=5,
               symbolBrush="#EF4444", symbolPen="#EF4444")
    p.getAxis("bottom").setTicks([[(1, "")]])
    return p


def make_radar(field_means: list[tuple[str, float]]) -> QWidget:
    """Radar (web) chart for multi-dim normalized comparison."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

    if not field_means:
        return _empty("无数据")
    labels = [k for k, _ in field_means]
    values = [v for _, v in field_means]
    vmin, vmax = min(values), max(values) if values else (0, 1)
    rng = vmax - vmin if vmax != vmin else 1.0
    norm = [(v - vmin) / rng for v in values]
    norm += norm[:1]
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    angles += angles[:1]

    fig = Figure(figsize=(4, 4), tight_layout=True, facecolor=BG)
    ax = fig.add_subplot(111, polar=True)
    ax.set_facecolor(BG)
    ax.plot(angles, norm, color=PALETTE[0], linewidth=2)
    ax.fill(angles, norm, color=PALETTE[0], alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, color=FG, fontsize=9)
    ax.set_yticklabels([])
    ax.spines["polar"].set_color(GRID)
    return _wrap_canvas(FigureCanvas(fig))


def make_pie(values: list[Any], *, donut: bool = False) -> QWidget:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

    items = Counter(_to_strings(values)).most_common(10)
    if not items:
        return _empty("无可绘制数据")
    labels = [k for k, _ in items]
    counts = [c for _, c in items]
    fig = Figure(figsize=(4, 4), tight_layout=True, facecolor=BG)
    ax = fig.add_subplot(111)
    wedge_kwargs = {"width": 0.45, "edgecolor": BG} if donut else {"edgecolor": BG}
    ax.pie(counts, labels=labels, autopct="%1.0f%%", colors=PALETTE[: len(items)],
           textprops={"color": FG, "fontsize": 9}, wedgeprops=wedge_kwargs, startangle=90)
    ax.axis("equal")
    return _wrap_canvas(FigureCanvas(fig))


def make_heatmap_corr(num_columns: dict[str, list[Any]]) -> QWidget:
    """Pearson correlation heatmap from {col_name: list of numeric values}."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

    cols = []
    series: list[np.ndarray] = []
    for name, vals in num_columns.items():
        nums = _to_floats(vals)
        if len(nums) < 2:
            continue
        cols.append(name)
        series.append(np.array(nums))
    if len(cols) < 2:
        return _empty("可计算相关性的数值列不足")
    n = min(len(s) for s in series)
    mat = np.zeros((len(cols), len(cols)))
    truncated = [s[:n] for s in series]
    for i in range(len(cols)):
        for j in range(len(cols)):
            xi = truncated[i]
            xj = truncated[j]
            if xi.std() == 0 or xj.std() == 0:
                mat[i][j] = 0
            else:
                mat[i][j] = np.corrcoef(xi, xj)[0, 1]

    fig = Figure(figsize=(5, 4.5), tight_layout=True, facecolor=BG)
    ax = fig.add_subplot(111)
    im = ax.imshow(mat, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(cols)))
    ax.set_yticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=45, ha="right", color=FG, fontsize=9)
    ax.set_yticklabels(cols, color=FG, fontsize=9)
    for i in range(len(cols)):
        for j in range(len(cols)):
            ax.text(j, i, f"{mat[i][j]:.2f}", ha="center", va="center",
                    color="white" if abs(mat[i][j]) > 0.5 else FG, fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.045)
    return _wrap_canvas(FigureCanvas(fig))


def make_treemap(cat_values: list[Any], num_values: list[Any]) -> QWidget:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

    pairs = [
        (str(c), float(n))
        for c, n in zip(cat_values, num_values)
        if c not in (None, "") and _is_num(n)
    ]
    if not pairs:
        return _empty("无可绘制数据")
    groups: dict[str, float] = {}
    for c, n in pairs:
        groups[c] = groups.get(c, 0) + n
    items = sorted(groups.items(), key=lambda x: x[1], reverse=True)[:12]

    try:
        import squarify  # type: ignore
    except ImportError:
        return _treemap_fallback(items)

    labels = [f"{k}\n{v:.0f}" for k, v in items]
    sizes = [v for _, v in items]
    fig = Figure(figsize=(5, 4), tight_layout=True, facecolor=BG)
    ax = fig.add_subplot(111)
    squarify.plot(sizes=sizes, label=labels, ax=ax, color=PALETTE[: len(items)],
                  edgecolor=BG, text_kwargs={"color": "white", "fontsize": 9})
    ax.axis("off")
    return _wrap_canvas(FigureCanvas(fig))


def _treemap_fallback(items: list[tuple[str, float]]) -> QWidget:
    """Simple grid fallback when `squarify` isn't installed."""
    w = QWidget()
    grid = QHBoxLayout(w)
    grid.setSpacing(2)
    grid.setContentsMargins(0, 0, 0, 0)
    total = sum(v for _, v in items) or 1
    for i, (k, v) in enumerate(items):
        cell = QLabel(f"{k}\n{v:.0f}")
        cell.setAlignment(Qt.AlignCenter)
        cell.setStyleSheet(
            f"background:{_color(i)}; color:white; font-weight:600; padding:6px; border-radius:6px;"
        )
        cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cell.setMinimumHeight(40)
        grid.addWidget(cell, stretch=max(1, int(v / total * 100)))
    return w


def make_wordcloud(texts: list[Any]) -> QWidget:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

    text = " ".join(str(t) for t in texts if t)
    if not text.strip():
        return _empty("无文本")

    try:
        from wordcloud import WordCloud  # type: ignore

        wc = WordCloud(
            width=600, height=400, background_color="white",
            colormap="viridis", max_words=80,
        ).generate(text)
        fig = Figure(figsize=(5, 3.5), tight_layout=True, facecolor=BG)
        ax = fig.add_subplot(111)
        ax.imshow(wc, interpolation="bilinear")
        ax.axis("off")
        return _wrap_canvas(FigureCanvas(fig))
    except ImportError:
        return _wordcloud_fallback(texts)


def _wordcloud_fallback(texts: list[Any]) -> QWidget:
    """Top-N words shown as styled tag list when `wordcloud` is unavailable."""
    import re

    words: Counter[str] = Counter()
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[\w一-鿿]+", str(t)):
            if len(w) >= 2:
                words[w] += 1
    items = words.most_common(40)
    if not items:
        return _empty("无文本词频")
    max_c = items[0][1]
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)
    layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    from PySide6.QtWidgets import QGridLayout

    grid = QGridLayout()
    grid.setSpacing(8)
    for i, (w, c) in enumerate(items):
        size = 12 + int((c / max_c) * 18)
        lbl = QLabel(w)
        lbl.setStyleSheet(
            f"color:{_color(i)}; font-size:{size}px; font-weight:600; padding:2px 6px;"
        )
        grid.addWidget(lbl, i // 8, i % 8)
    container.setLayout(grid)
    return container


def make_stat_summary(stats: dict[str, ColumnStats]) -> QWidget:
    """Tabular field-by-field statistics."""
    table = QTableWidget()
    headers = ["字段", "类型", "计数", "缺失", "唯一", "Min", "Max", "均值", "中位", "Top"]
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(stats))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    for i, s in enumerate(stats.values()):
        cells = [
            s.name,
            s.kind,
            str(s.count),
            str(s.null_count),
            str(s.distinct_count),
            f"{s.min:.2f}" if s.min is not None else "",
            f"{s.max:.2f}" if s.max is not None else "",
            f"{s.mean:.2f}" if s.mean is not None else "",
            f"{s.median:.2f}" if s.median is not None else "",
            ", ".join(f"{k}({c})" for k, c in (s.top_values or [])[:3]),
        ]
        for j, v in enumerate(cells):
            it = QTableWidgetItem(v)
            if j > 1:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(i, j, it)
    table.resizeColumnsToContents()
    return table


def make_pivot(
    rows: list[dict[str, Any]], *, row_field: str, value_field: str, agg: str = "mean"
) -> QWidget:
    """Simple 1-dim pivot: agg of `value_field` over groups of `row_field`."""
    groups: dict[str, list[float]] = {}
    for r in rows:
        k = str(r.get(row_field, ""))
        v = r.get(value_field)
        if not _is_num(v):
            continue
        groups.setdefault(k, []).append(float(v))

    table = QTableWidget()
    headers = [row_field, f"{agg}({value_field})", "计数"]
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(groups))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)

    items = []
    for k, vs in groups.items():
        if agg == "sum":
            agg_v = sum(vs)
        elif agg == "max":
            agg_v = max(vs)
        elif agg == "min":
            agg_v = min(vs)
        elif agg == "count":
            agg_v = len(vs)
        else:
            agg_v = sum(vs) / len(vs)
        items.append((k, agg_v, len(vs)))
    items.sort(key=lambda x: x[1], reverse=True)
    for i, (k, v, c) in enumerate(items):
        table.setItem(i, 0, QTableWidgetItem(k))
        table.setItem(i, 1, QTableWidgetItem(f"{v:.2f}"))
        table.setItem(i, 2, QTableWidgetItem(str(c)))
    table.resizeColumnsToContents()
    return table


# ----------------------------------------------------------------------
def _wrap_canvas(canvas) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(canvas)
    canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w


def _empty(text: str) -> QWidget:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet("color:#9CA3AF; font-size:13px; padding:32px;")
    return lbl


def _is_num(v: Any) -> bool:
    if v is None or v == "" or isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _x_axis(v: Any, idx: int) -> float:
    """Best-effort conversion of an x value (date or number) to float."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    dt = _try_dt(v)
    if dt is not None:
        return dt.timestamp()
    return float(idx)
