"""Export-report dialog: pick which charts to include, optionally let the
LLM write a paragraph of analysis next to each chart, and generate the
final Word / PowerPoint document.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import qasync
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from kdv.export.docx_export import export_docx
from kdv.export.payload import ChartImage, ExportPayload, capture_widget_png
from kdv.export.pptx_export import export_pptx
from kdv.llm.client import LLMClient
from kdv.ui import helpers as h
from kdv.ui import style


CHART_ANALYSIS_PROMPT = """你是数据分析报告写作助手。请根据下面的图表标题、数据描述和原始数据片段，用 1–2 段简洁中文（80–150 字）写出该图表的分析说明。
要点：
1. 第一句直接陈述图表表达的核心结论（数字、趋势、对比）。
2. 第二段（可选）提一句业务含义或建议，禁止泛泛而谈。
3. 不要重复图表标题。不要 "如图所示" 之类的废话。

图表标题：{title}
图表说明（推荐理由）：{rationale}
图表类型：{kind}
关联列：{cols}

部分原始数据样本：
{sample_data}

请直接输出分析正文，不要包含 Markdown 标题或图表名。"""


class ExportReportDialog(QDialog):
    """User picks charts + LLM-analysis option, then we generate the doc."""

    def __init__(
        self,
        *,
        parent: QWidget,
        chart_records: list[tuple[str, str, QWidget]],
        result_columns: list[str],
        result_rows: list[dict[str, Any]],
        result_summary: str | None,
        kpis: list[tuple[str, str]],
        meta: dict[str, Any],
        preset,
        api_key: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出分析报告")
        self.setMinimumSize(620, 640)

        self._chart_records = chart_records
        self._result_columns = result_columns
        self._result_rows = result_rows
        self._result_summary = result_summary or ""
        self._kpis = kpis
        self._meta = meta
        self._preset = preset
        self._api_key = api_key
        self._chart_checks: list[QCheckBox] = []
        self._format = "docx"

        self._build()

    # ---- UI ------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 18)
        outer.setSpacing(14)

        outer.addWidget(h.heading("导出分析报告", level=2))
        outer.addWidget(h.muted(
            "勾选要导出的图表，可选让 LLM 为每张图写分析说明。"
            "Word / PPT 会自动排版图表 + 文字 + 汇总分析。"
        ))

        # Format toggle
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(12)
        fmt_label = QLabel("输出格式：")
        fmt_label.setStyleSheet("font-weight: 600;")
        fmt_row.addWidget(fmt_label)
        self._fmt_group = QButtonGroup(self)
        self._docx_radio = QRadioButton("Word (.docx)")
        self._pptx_radio = QRadioButton("PowerPoint (.pptx)")
        self._docx_radio.setChecked(True)
        self._fmt_group.addButton(self._docx_radio)
        self._fmt_group.addButton(self._pptx_radio)
        fmt_row.addWidget(self._docx_radio)
        fmt_row.addWidget(self._pptx_radio)
        fmt_row.addStretch(1)
        outer.addLayout(fmt_row)

        # Options
        self._llm_analysis_check = QCheckBox("调用 LLM 为每张图生成分析文字（推荐）")
        self._llm_analysis_check.setChecked(True)
        self._llm_analysis_check.setStyleSheet("font-weight: 500;")
        self._include_summary_check = QCheckBox("包含整表汇总分析（结尾章节）")
        self._include_summary_check.setChecked(True)
        outer.addWidget(self._llm_analysis_check)
        outer.addWidget(self._include_summary_check)

        # Chart picker
        outer.addWidget(h.heading("选择图表", level=3))

        ctrl_row = QHBoxLayout()
        all_btn = h.ghost_button("全选")
        all_btn.clicked.connect(self._select_all)
        none_btn = h.ghost_button("全不选")
        none_btn.clicked.connect(self._select_none)
        ctrl_row.addWidget(all_btn)
        ctrl_row.addWidget(none_btn)
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(h.muted(f"共 {len(self._chart_records)} 张"))
        outer.addLayout(ctrl_row)

        # Scrollable list of charts with checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMinimumHeight(240)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(6)
        for idx, (title, rationale, _w) in enumerate(self._chart_records):
            row = QFrame()
            row.setStyleSheet(
                f"background: {style.BG_CARD}; border: 1px solid {style.BORDER}; "
                "border-radius: 8px;"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 8, 12, 8)
            cb = QCheckBox(title)
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 13px;")
            self._chart_checks.append(cb)
            rl.addWidget(cb, 1)
            if rationale:
                tag = h.badge(rationale, "info")
                rl.addWidget(tag)
            inner_lay.addWidget(row)
        inner_lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Progress + status
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        outer.addWidget(self._progress)

        self._status = h.muted("")
        outer.addWidget(self._status)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Cancel)
        self._go_btn = h.primary_button("生成报告 →")
        self._go_btn.clicked.connect(self._on_go)
        btns.addButton(self._go_btn, QDialogButtonBox.AcceptRole)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    def _select_all(self) -> None:
        for cb in self._chart_checks:
            cb.setChecked(True)

    def _select_none(self) -> None:
        for cb in self._chart_checks:
            cb.setChecked(False)

    # ---- generation ----------------------------------------------------
    @qasync.asyncSlot()
    async def _on_go(self) -> None:
        selected = [
            i for i, cb in enumerate(self._chart_checks) if cb.isChecked()
        ]
        if not selected:
            h.toast(self, "请至少勾选一张图表", "warning")
            return
        fmt = "docx" if self._docx_radio.isChecked() else "pptx"
        suggested = f"分析报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}"
        ext = "Word 文档 (*.docx)" if fmt == "docx" else "PowerPoint (*.pptx)"
        path, _ = QFileDialog.getSaveFileName(self, "保存为", suggested, ext)
        if not path:
            return

        self._go_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status.setText("正在抓取图表截图…")

        try:
            charts: list[ChartImage] = []
            n = len(selected)

            # Step 1: capture every selected chart to PNG
            for step, idx in enumerate(selected):
                title, rationale, body = self._chart_records[idx]
                # Force layout/paint before grabbing
                body.setVisible(True)
                body.repaint()
                from PySide6.QtWidgets import QApplication

                QApplication.processEvents()
                try:
                    png = capture_widget_png(body, scale=2.0)
                except Exception:
                    png = b""
                charts.append(ChartImage(title=title, rationale=rationale, png_bytes=png))
                self._progress.setValue(int((step + 1) / n * 30))
                self._status.setText(f"抓取图表 {step + 1}/{n}…")
                QApplication.processEvents()

            # Step 2: optionally LLM-analyse each chart
            llm_analyses: list[str] = []
            if self._llm_analysis_check.isChecked() and self._preset and self._api_key:
                async with LLMClient(self._preset, self._api_key) as client:
                    for step, idx in enumerate(selected):
                        title, rationale, _w = self._chart_records[idx]
                        sample = self._sample_data_for_chart(idx)
                        prompt = CHART_ANALYSIS_PROMPT.format(
                            title=title,
                            rationale=rationale or "(无)",
                            kind="分析图表",
                            cols=", ".join(self._result_columns[:8]),
                            sample_data=sample,
                        )
                        try:
                            resp = await client.chat(
                                system_prompt="你是中文数据分析报告写作助手。",
                                user_prompt=prompt,
                                schema_fields=None,
                                temperature=0.4,
                                max_tokens=400,
                            )
                            llm_analyses.append((resp.text or "").strip())
                        except Exception as e:  # noqa: BLE001
                            llm_analyses.append(f"_（LLM 分析失败：{e}）_")
                        prog = 30 + int((step + 1) / n * 60)
                        self._progress.setValue(prog)
                        self._status.setText(f"LLM 分析图表 {step + 1}/{n}…")
                        from PySide6.QtWidgets import QApplication

                        QApplication.processEvents()
            else:
                llm_analyses = [""] * len(charts)

            # Inject the LLM analysis as the chart's rationale (so it
            # shows up next to the image in both docx and pptx exports).
            for ci, llm_text in zip(charts, llm_analyses):
                if llm_text:
                    ci.rationale = llm_text

            # Step 3: build payload
            self._status.setText("写出文档…")
            self._progress.setValue(95)
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()

            payload = ExportPayload(
                title="Krystal Data Vision 分析报告",
                subtitle=str(self._meta.get("subtitle", "")),
                preset_name=str(self._meta.get("preset_name", "")),
                model_id=str(self._meta.get("model_id", "")),
                analysis_model_name=str(self._meta.get("analysis_model_name", "")),
                mode=str(self._meta.get("mode", "")),
                kpis=list(self._kpis),
                summary_markdown=(
                    self._result_summary
                    if self._include_summary_check.isChecked()
                    else ""
                ),
                charts=charts,
                sample_columns=list(self._result_columns)[:8],
                sample_rows=list(self._result_rows[:8]),
                insights=[],
            )

            if fmt == "docx":
                export_docx(payload, path)
            else:
                export_pptx(payload, path)

            self._progress.setValue(100)
            self._status.setText(f"已生成：{path}")
            h.toast(self.parent() or self, f"已导出：{Path(path).name}", "success")
            self.accept()
        except Exception as e:  # noqa: BLE001
            import logging

            logging.exception("export failed")
            h.toast(self, f"导出失败：{e}", "danger")
            self._go_btn.setEnabled(True)

    def _sample_data_for_chart(self, idx: int) -> str:
        """Return a small text sample from the dataset for the LLM prompt."""
        rows = self._result_rows[:6]
        cols = self._result_columns[:8]
        if not rows:
            return "(无样本)"
        lines = ["| " + " | ".join(cols) + " |"]
        lines.append("|" + "|".join(["---"] * len(cols)) + "|")
        for r in rows:
            cells = [str(r.get(c, ""))[:50].replace("|", "\\|") for c in cols]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)
