"""Result page — KPI cards + chart grid + insights + raw table.

Built dynamically from the latest RunResult in AppState.
"""
from __future__ import annotations

from typing import Any

import markdown as md
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from kdv.analysis.runner import RunResult
from kdv.ui import helpers as h
from kdv.ui.animations import fade_in, slide_in
from kdv.ui.state import AppState
from kdv.viz import charts as ch
from kdv.viz.column_stats import summarize_columns
from kdv.viz.recommender import ChartSuggestion, recommend_charts


class ResultPage(QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self._build_empty()

    # ---- empty placeholder ----------------------------------------------
    def _build_empty(self) -> None:
        if self.layout():
            self._clear_layout()
            return self._render_empty()
        self._main = QVBoxLayout(self)
        self._main.setContentsMargins(24, 20, 24, 20)
        self._main.setSpacing(16)
        self._render_empty()

    def _clear_layout(self) -> None:
        while self._main.count():
            it = self._main.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            else:
                lay = it.layout()
                if lay is not None:
                    self._delete_layout(lay)

    def _delete_layout(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            else:
                child = it.layout()
                if child is not None:
                    self._delete_layout(child)

    def _render_empty(self) -> None:
        es = h.empty_state(
            "还没有分析结果",
            "在『运行』页面选择配置与模型，上传数据 Excel 开始分析吧。",
        )
        self._main.addWidget(es, 1)

    # ---- main entry ------------------------------------------------------
    def render_result(self, result: RunResult) -> None:
        self._clear_layout()

        head = QHBoxLayout()
        head.addWidget(h.heading("分析结果", level=1))
        head.addStretch(1)
        head.addWidget(h.muted(f"运行 ID: {result.run_id}"))
        self._main.addLayout(head)

        # ---- KPI strip --------------------------------------------------
        kpis = self._build_kpis(result)
        self._main.addWidget(kpis)

        # ---- main split: charts (left) + insights (right) --------------
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)

        # left scroll: chart grid
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.NoFrame)
        left_inner = QWidget()
        self._chart_grid = QGridLayout(left_inner)
        self._chart_grid.setContentsMargins(0, 0, 0, 0)
        self._chart_grid.setSpacing(16)
        left_scroll.setWidget(left_inner)
        split.addWidget(left_scroll)

        # right: insights + summary
        right_card = h.make_card()
        right_card.layout().addWidget(h.heading("整表汇总洞察", level=2))
        self._insights = QTextBrowser()
        self._insights.setOpenExternalLinks(True)
        self._insights.setStyleSheet(
            "background: white; border: none; padding: 4px;"
            "font-size: 13px; line-height: 1.65;"
        )
        right_card.layout().addWidget(self._insights, 1)
        split.addWidget(right_card)
        split.setSizes([900, 480])

        self._main.addWidget(split, 1)

        # ---- bottom tabs: raw data + errors -----------------------------
        tabs = QTabWidget()
        tabs.addTab(self._build_data_table(result), "原始数据 + 输出")
        tabs.addTab(self._build_error_table(result), f"错误（{sum(1 for e in result.row_errors if e)}）")
        self._main.addWidget(tabs)

        self._populate_charts(result)
        self._populate_insights(result)
        fade_in(self)

    # ---- KPIs -----------------------------------------------------------
    def _build_kpis(self, result: RunResult) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        total = len(result.rows)
        has_per_row = any(o is not None for o in result.row_outputs)
        tokens = result.prompt_tokens_total + result.completion_tokens_total
        cols = len(result.columns)

        if has_per_row:
            success = sum(1 for o, e in zip(result.row_outputs, result.row_errors) if o and not e)
            fail = total - success
            success_rate = (success / total * 100) if total else 0
            avg_ms = result.duration_ms_total // max(total, 1)
            kpis = [
                ("总行数", str(total), "#5B6CFF"),
                ("成功", f"{success}", "#10B981"),
                ("失败", f"{fail}", "#EF4444"),
                ("成功率", f"{success_rate:.1f}%", "#5B6CFF"),
                ("平均耗时", f"{avg_ms} ms", "#3B82F6"),
                ("Token 用量", f"{tokens:,}", "#F59E0B"),
            ]
        else:
            # Ad-hoc / summary-only run — emphasise dataset shape and total cost.
            kpis = [
                ("数据行数", str(total), "#5B6CFF"),
                ("数据列数", str(cols), "#3B82F6"),
                ("耗时", f"{result.duration_ms_total // 1000} s", "#10B981"),
                ("Prompt tokens", f"{result.prompt_tokens_total:,}", "#F59E0B"),
                ("Completion tokens", f"{result.completion_tokens_total:,}", "#EC4899"),
                ("分析模式", result.mode, "#8B5CF6"),
            ]
        for label, value, accent in kpis:
            row.addWidget(self._kpi_card(label, value, accent), 1)
        return wrap

    def _kpi_card(self, label: str, value: str, accent: str) -> QWidget:
        card = h.make_card(padding=16)
        v = QLabel(value)
        v.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {accent};")
        l = QLabel(label)
        l.setStyleSheet("color: #6B7280; font-size: 12px;")
        card.layout().setSpacing(4)
        card.layout().addWidget(v)
        card.layout().addWidget(l)
        return card

    # ---- charts ---------------------------------------------------------
    def _populate_charts(self, result: RunResult) -> None:
        # merge input rows + per-row outputs into a single columnar dataset
        merged_columns, merged_rows = _merge_rows(result)
        stats = summarize_columns(merged_columns, merged_rows)
        suggestions = recommend_charts(stats)

        for i, s in enumerate(suggestions):
            widget = self._build_chart(s, merged_columns, merged_rows, stats)
            if widget is None:
                continue
            card = self._wrap_chart_card(s.title, s.rationale, widget)
            self._chart_grid.addWidget(card, i // 2, i % 2)
            slide_in(card, direction="up", duration_ms=240)

    def _wrap_chart_card(self, title: str, rationale: str, body: QWidget) -> QWidget:
        card = h.make_card(padding=14)
        head = QHBoxLayout()
        head.addWidget(h.heading(title, level=3))
        if rationale:
            tag = h.badge(rationale, "info")
            head.addWidget(tag)
        head.addStretch(1)
        card.layout().addLayout(head)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body.setMinimumHeight(280)
        card.layout().addWidget(body, 1)
        return card

    def _build_chart(
        self,
        s: ChartSuggestion,
        columns: list[str],
        rows: list[dict[str, Any]],
        stats,
    ) -> QWidget | None:
        kind = s.kind
        cols = s.columns

        def col_vals(name: str) -> list[Any]:
            return [r.get(name) for r in rows]

        try:
            if kind == "kpi":
                # already shown in top KPI strip; show a quick "data quality" card
                return ch.make_stat_summary({k: v for k, v in stats.items() if v.kind != "empty"})
            if kind == "stat_summary":
                return ch.make_stat_summary(stats)
            if kind == "donut":
                return ch.make_pie(col_vals(cols[0]), donut=True)
            if kind == "pie":
                return ch.make_pie(col_vals(cols[0]))
            if kind == "bar":
                if len(cols) == 2:
                    agg = (s.params or {}).get("agg", "mean")
                    return ch.make_grouped_bar(col_vals(cols[0]), col_vals(cols[1]), agg=agg)
                return ch.make_bar(col_vals(cols[0]))
            if kind == "bar_h":
                return ch.make_bar(col_vals(cols[0]), horizontal=True)
            if kind == "line":
                return ch.make_line(col_vals(cols[0]), col_vals(cols[1]))
            if kind == "area":
                return ch.make_area(col_vals(cols[0]), col_vals(cols[1]))
            if kind == "scatter":
                return ch.make_scatter(col_vals(cols[0]), col_vals(cols[1]))
            if kind == "histogram":
                return ch.make_histogram(col_vals(cols[0]))
            if kind == "box":
                return ch.make_box(col_vals(cols[0]))
            if kind == "heatmap_corr":
                return ch.make_heatmap_corr({c: col_vals(c) for c in cols})
            if kind == "radar":
                pairs = []
                for c in cols:
                    s_ = stats.get(c)
                    if s_ and s_.mean is not None:
                        pairs.append((c, s_.mean))
                return ch.make_radar(pairs)
            if kind == "treemap":
                return ch.make_treemap(col_vals(cols[0]), col_vals(cols[1]))
            if kind == "wordcloud":
                return ch.make_wordcloud(col_vals(cols[0]))
            if kind == "timeseries":
                # group by date and average
                from collections import defaultdict
                from datetime import datetime as _dt

                buckets: dict[str, list[float]] = defaultdict(list)
                for ts, v in zip(col_vals(cols[0]), col_vals(cols[1])):
                    if ts in (None, "") or v in (None, ""):
                        continue
                    key = str(ts)[:10]
                    try:
                        buckets[key].append(float(v))
                    except (TypeError, ValueError):
                        continue
                xs = sorted(buckets.keys())
                ys = [sum(buckets[k]) / len(buckets[k]) for k in xs]
                if not xs:
                    return None
                return ch.make_line(xs, ys)
            if kind == "pivot":
                p = s.params or {}
                return ch.make_pivot(
                    rows,
                    row_field=p.get("row", cols[0]),
                    value_field=p.get("value", cols[1] if len(cols) > 1 else cols[0]),
                    agg=p.get("agg", "mean"),
                )
        except Exception:  # noqa: BLE001 — never let one chart crash the page
            return h._empty(f"图表 {kind} 渲染失败")  # type: ignore[attr-defined]
        return None

    # ---- insights -------------------------------------------------------
    def _populate_insights(self, result: RunResult) -> None:
        if result.summary_markdown:
            html = md.markdown(
                result.summary_markdown, extensions=["tables", "fenced_code"]
            )
            self._insights.setHtml(_INSIGHT_CSS + html)
        else:
            self._insights.setHtml(
                _INSIGHT_CSS
                + "<p style='color:#9CA3AF; padding: 24px;'>本次运行未生成整表汇总。"
                "在『运行』页选择 <b>整表汇总</b> 或 <b>二者都做</b> 模式即可。</p>"
            )

    # ---- bottom tabs ----------------------------------------------------
    def _build_data_table(self, result: RunResult) -> QWidget:
        cols = list(result.columns) + [f.name for f in self._output_fields()] + ["错误"]
        table = QTableWidget(len(result.rows), len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        for i, row in enumerate(result.rows):
            j = 0
            for c in result.columns:
                v = row.get(c)
                table.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))
                j += 1
            out = result.row_outputs[i] or {}
            for f in self._output_fields():
                v = out.get(f.name)
                table.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))
                j += 1
            err = result.row_errors[i] or ""
            table.setItem(i, j, QTableWidgetItem(err))
        return table

    def _build_error_table(self, result: RunResult) -> QWidget:
        rows = [(i, e) for i, e in enumerate(result.row_errors) if e]
        table = QTableWidget(len(rows), 2)
        table.setHorizontalHeaderLabels(["行号", "错误信息"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, (idx, err) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(str(idx)))
            table.setItem(i, 1, QTableWidgetItem(err))
        return table

    def _output_fields(self):
        m = self.state.selected_model()
        return m.output_fields if m else []


_INSIGHT_CSS = """
<style>
body { font-family: 'PingFang SC','Microsoft YaHei','Segoe UI',sans-serif; color:#1F2937; line-height:1.65; }
h1,h2,h3 { color:#111827; margin-top: 16px; }
h1 { font-size: 18px; }
h2 { font-size: 16px; }
h3 { font-size: 14px; }
p, li { font-size: 13px; }
code { background:#F3F4F6; padding:2px 6px; border-radius:4px; font-size:12px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { border: 1px solid #E5E7EB; padding: 6px 8px; font-size: 12px; text-align: left; }
th { background: #F9FAFB; }
strong { color: #5B6CFF; }
</style>
"""


def _merge_rows(result: RunResult) -> tuple[list[str], list[dict[str, Any]]]:
    """Combine input columns with output fields for visualization."""
    out_cols: list[str] = []
    seen = set(result.columns)
    for o in result.row_outputs:
        if not o:
            continue
        for k in o.keys():
            if k not in seen:
                out_cols.append(k)
                seen.add(k)
    columns = list(result.columns) + out_cols
    rows: list[dict[str, Any]] = []
    for row, out in zip(result.rows, result.row_outputs):
        merged: dict[str, Any] = dict(row)
        if out:
            for k, v in out.items():
                merged[k] = v
        rows.append(merged)
    return columns, rows
