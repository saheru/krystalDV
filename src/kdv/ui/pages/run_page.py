"""Run page — pick preset+model, upload data, configure, run with progress."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import qasync
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kdv.analysis.projects import ProjectSnapshot
from kdv.analysis.runner import AnalysisRunner, RunProgress
from kdv.excel.reader import ExcelTable, read_excel
from kdv.excel.writer import write_results
from kdv.ui import helpers as h
from kdv.ui.animations import animate_int_value, reveal_height, shake
from kdv.ui.state import AppState


class RunPage(QWidget):
    run_completed = Signal(object)  # emits RunResult

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self._data: ExcelTable | None = None
        self._cancel_event: asyncio.Event | None = None
        self._build()
        self.refresh_pickers()

    # ---- layout ----------------------------------------------------------
    def _build(self) -> None:
        # The whole page lives in a vertical scroll area so sections can
        # breathe instead of compressing into one screen height.
        root, _page = h.make_scroll_page(self)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(h.heading("运行分析", level=1))
        head.addStretch(1)
        quick_btn = h.ghost_button("⚡ 无模型快速分析")
        quick_btn.setToolTip("跳过模型，直接对数据 Excel 做整表汇总分析")
        quick_btn.clicked.connect(self._switch_to_quick_mode)
        head.addWidget(quick_btn)
        root.addLayout(head)
        intro = h.muted(
            "选择 LLM 配置 + 数据 Excel 即可分析。模型可选——选『（无模型）』将跳过逐行结构化输出，"
            "直接生成整表 Markdown 洞察。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # ---- pickers row (two equal-width cards) ----------------------
        pickers = QHBoxLayout()
        pickers.setSpacing(16)

        preset_card = h.make_card()
        ph = QHBoxLayout()
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addWidget(h.heading("LLM 配置", level=3))
        ph.addStretch(1)
        preset_card.layout().addLayout(ph)
        self.preset_picker = QComboBox()
        self.preset_picker.setMinimumHeight(36)
        self.preset_picker.currentIndexChanged.connect(self._on_pickers_changed)
        preset_card.layout().addWidget(self.preset_picker)
        self.preset_meta = h.muted("")
        self.preset_meta.setWordWrap(True)
        preset_card.layout().addWidget(self.preset_meta)
        preset_card.layout().addStretch(1)

        model_card = h.make_card()
        mh = QHBoxLayout()
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(h.heading("分析模型", level=3))
        mh.addStretch(1)
        mh.addWidget(h.badge("可选", "info"))
        model_card.layout().addLayout(mh)
        self.model_picker = QComboBox()
        self.model_picker.setMinimumHeight(36)
        self.model_picker.currentIndexChanged.connect(self._on_pickers_changed)
        model_card.layout().addWidget(self.model_picker)
        self.model_meta = h.muted("")
        self.model_meta.setWordWrap(True)
        model_card.layout().addWidget(self.model_meta)
        model_card.layout().addStretch(1)

        pickers.addWidget(preset_card, 1)
        pickers.addWidget(model_card, 1)
        root.addLayout(pickers)

        # ---- data card --------------------------------------------------
        data_card = h.make_card()
        data_head = QHBoxLayout()
        data_head.addWidget(h.heading("输入数据", level=3))
        data_head.addStretch(1)
        self.upload_btn = h.primary_button("📂 上传数据 Excel")
        self.upload_btn.clicked.connect(self._on_upload)
        data_head.addWidget(self.upload_btn)
        data_card.layout().addLayout(data_head)
        self.data_meta = h.muted("尚未上传数据。")
        data_card.layout().addWidget(self.data_meta)
        self.data_preview = QTableWidget(0, 0)
        self.data_preview.verticalHeader().setVisible(False)
        self.data_preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.data_preview.setEditTriggers(QTableWidget.NoEditTriggers)
        self.data_preview.setMaximumHeight(220)
        self.data_preview.setAlternatingRowColors(True)
        data_card.layout().addWidget(self.data_preview)
        root.addWidget(data_card)

        # ---- mode + goal card -------------------------------------------
        mode_card = h.make_card()
        mode_card.layout().addWidget(h.heading("分析模式", level=3))
        mode_row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self._mode_buttons: dict[str, QPushButton] = {}
        for key, label, desc in [
            ("row_by_row", "逐行分析", "每行调用一次 LLM，输出结构化结果"),
            ("summary", "整表汇总", "整体洞察 / 趋势 / 异常（Markdown）"),
            ("both", "二者都做", "先逐行结构化，再做整表汇总"),
        ]:
            btn = QPushButton(f"{label}\n\n{desc}")
            btn.setCheckable(True)
            btn.setMinimumHeight(72)
            btn.setStyleSheet(self._mode_button_qss())
            btn.toggled.connect(self._restyle_mode_buttons)
            self.mode_group.addButton(btn)
            self._mode_buttons[key] = btn
            mode_row.addWidget(btn)
        self._mode_buttons["row_by_row"].setChecked(True)
        mode_card.layout().addLayout(mode_row)

        goal_label = h.muted("分析目标（可选 / 无模型时必填）")
        mode_card.layout().addWidget(goal_label)
        self.extra_goal = QPlainTextEdit()
        self.extra_goal.setPlaceholderText(
            "可选：本次运行的临时分析目标（覆盖模型默认目标）。无模型快速分析时这里必填——会作为 LLM 的核心问题。"
        )
        self.extra_goal.setMinimumHeight(96)
        mode_card.layout().addWidget(self.extra_goal)
        root.addWidget(mode_card)

        # ---- run + progress -----------------------------------------------
        run_row = QHBoxLayout()
        self.run_btn = h.primary_button("▶ 开始分析")
        self.run_btn.setMinimumHeight(46)
        self.run_btn.clicked.connect(self._on_run)
        self.cancel_btn = h.danger_button("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        run_row.addWidget(self.run_btn, 1)
        run_row.addWidget(self.cancel_btn)
        root.addLayout(run_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%  %v / %m")
        root.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setPlaceholderText("运行日志会显示在这里…")
        self.log.setMinimumHeight(180)
        root.addWidget(self.log)

    def _mode_button_qss(self) -> str:
        return """
            QPushButton {
                background: white;
                color: #374151;
                border: 1.5px solid #E5E7EB;
                border-radius: 12px;
                padding: 14px 16px;
                text-align: left;
                font-weight: 500;
                min-height: 56px;
            }
            QPushButton:hover {
                border-color: #5B6CFF;
                color: #5B6CFF;
                background: #F8F9FF;
            }
            QPushButton:checked {
                background: #5B6CFF;
                color: white;
                border: 1.5px solid #5B6CFF;
                font-weight: 600;
            }
            QPushButton:checked:hover {
                background: #4F5DE8;
                border-color: #4F5DE8;
            }
            QPushButton:disabled {
                color: #9CA3AF;
                background: #F9FAFB;
                border-color: #E5E7EB;
            }
        """

    def _restyle_mode_buttons(self) -> None:
        for b in self._mode_buttons.values():
            b.style().unpolish(b)
            b.style().polish(b)

    def _selected_mode(self) -> str:
        for k, b in self._mode_buttons.items():
            if b.isChecked():
                return k
        return "row_by_row"

    # ---- pickers ---------------------------------------------------------
    def refresh_pickers(self) -> None:
        cur_p = self.state.selected_preset()
        cur_m = self.state.selected_model()
        self.preset_picker.blockSignals(True)
        self.model_picker.blockSignals(True)
        self.preset_picker.clear()
        self.model_picker.clear()
        for p in self.state.presets.list():
            self.preset_picker.addItem(f"{p.name} · {p.model}", p.id)
        # First slot in model picker = "无模型" (ad-hoc summary-only).
        self.model_picker.addItem("（无模型 · 直接整表汇总分析）", "")
        for m in self.state.models.list():
            self.model_picker.addItem(f"{m.name} · {len(m.output_fields)} 字段", m.id)
        if cur_p:
            i = self.preset_picker.findData(cur_p.id)
            if i >= 0:
                self.preset_picker.setCurrentIndex(i)
        if cur_m:
            i = self.model_picker.findData(cur_m.id)
            if i >= 0:
                self.model_picker.setCurrentIndex(i)
        self.preset_picker.blockSignals(False)
        self.model_picker.blockSignals(False)
        self._on_pickers_changed()

    def _on_pickers_changed(self) -> None:
        pid = self.preset_picker.currentData()
        mid = self.model_picker.currentData()
        if pid:
            self.state.settings.update(last_preset_id=pid)
            p = self.state.presets.get(pid)
            self.preset_meta.setText(
                f"{p.base_url}  ·  并发 {p.max_concurrency}  ·  最大 tokens {p.max_tokens}"
                if p
                else ""
            )
        if mid:
            self.state.settings.update(last_model_id=mid)
            m = self.state.models.get(mid)
            self.model_meta.setText(
                f"模板：{m.system_template}  ·  目标：{(m.analysis_goal or '—')[:40]}"
                if m
                else ""
            )
        else:
            self.state.settings.update(last_model_id="")
            self.model_meta.setText(
                "无模型快速分析：跳过逐行结构化输出，整表一次性给出 Markdown 洞察。"
            )

        # When no model, only summary mode is sensible — disable the others.
        no_model = not mid
        if no_model:
            for key in ("row_by_row", "both"):
                btn = self._mode_buttons.get(key)
                if btn:
                    btn.setEnabled(False)
                    if btn.isChecked():
                        btn.setChecked(False)
            self._mode_buttons["summary"].setEnabled(True)
            self._mode_buttons["summary"].setChecked(True)
        else:
            for btn in self._mode_buttons.values():
                btn.setEnabled(True)

    # ---- data ------------------------------------------------------------
    def _on_upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据 Excel", "", "Excel 文件 (*.xlsx *.xlsm)"
        )
        if not path:
            return
        try:
            tbl = read_excel(path)
        except Exception as e:  # noqa: BLE001
            h.toast(self.window(), f"读取失败：{e}", "danger")
            return
        self._data = tbl
        self.data_meta.setText(
            f"已加载：{Path(path).name} · sheet={tbl.sheet_name} · "
            f"{len(tbl.rows)} 行 × {len(tbl.columns)} 列"
        )
        self._fill_preview(tbl)

    def _fill_preview(self, tbl: ExcelTable) -> None:
        cols = tbl.columns
        rows = tbl.head(5)
        self.data_preview.setColumnCount(len(cols))
        self.data_preview.setHorizontalHeaderLabels(cols)
        self.data_preview.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, c in enumerate(cols):
                v = r.get(c)
                self.data_preview.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))

    # ---- run -------------------------------------------------------------
    def _append_log(self, line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}] {line}")

    def _switch_to_quick_mode(self) -> None:
        """Set the picker to ad-hoc mode and focus the goal box."""
        idx = self.model_picker.findData("")
        if idx >= 0:
            self.model_picker.setCurrentIndex(idx)
        self.extra_goal.setFocus()
        h.toast(self.window(), "已切换到无模型快速分析。请填写分析目标，然后上传数据 Excel。", "info")

    @qasync.asyncSlot()
    async def _on_run(self) -> None:
        pid = self.preset_picker.currentData()
        mid = self.model_picker.currentData()
        if not pid:
            h.toast(self.window(), "请先选择 LLM 配置", "warning")
            shake(self.preset_picker)
            return
        if not self._data or not self._data.rows:
            h.toast(self.window(), "请上传数据 Excel", "warning")
            shake(self.upload_btn)
            return
        preset = self.state.presets.get(pid)
        model = self.state.models.get(mid) if mid else None
        if not preset:
            h.toast(self.window(), "LLM 配置已不存在", "danger")
            return
        api_key = self.state.get_api_key(preset)
        if not api_key:
            h.toast(self.window(), "API key 为空，请到配置页填写并保存", "danger")
            return
        if model is not None and not model.output_fields:
            h.toast(self.window(), "模型未定义任何输出字段", "warning")
            return

        ad_hoc_goal = ""
        if model is not None and self.extra_goal.toPlainText().strip():
            model = model.model_copy(update={"analysis_goal": self.extra_goal.toPlainText().strip()})
        elif model is None:
            ad_hoc_goal = self.extra_goal.toPlainText().strip()
            if not ad_hoc_goal:
                h.toast(
                    self.window(),
                    "无模型分析需要填写一个『分析目标』描述你希望 LLM 关注什么。",
                    "warning",
                )
                return

        mode = self._selected_mode()
        if model is None and mode != "summary":
            mode = "summary"
        total = len(self._data.rows)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._cancel_event = asyncio.Event()

        self._append_log(f"开始：{total} 行，模式 {mode}，并发 {preset.max_concurrency}")

        runner = AnalysisRunner(
            preset=preset,
            api_key=api_key,
            model=model,
            ad_hoc_goal=ad_hoc_goal,
        )

        def on_progress(p: RunProgress) -> None:
            # Smooth animated progress
            cur = self.progress.value()
            if p.completed != cur:
                animate_int_value(self.progress, b"value", cur, p.completed, duration_ms=240)
            if p.last_error:
                self._append_log(f"行 {p.last_index} 失败：{p.last_error[:120]}")
            else:
                self._append_log(
                    f"行 {p.last_index} 完成 ({p.last_duration_ms} ms, "
                    f"tokens {p.prompt_tokens_total}+{p.completion_tokens_total})"
                )

        try:
            result = await runner.run(
                columns=self._data.columns,
                rows=self._data.rows,
                mode=mode,  # type: ignore[arg-type]
                on_progress=on_progress,
                cancel_event=self._cancel_event,
            )
        except Exception as e:  # noqa: BLE001
            self._append_log(f"运行异常：{e}")
            h.toast(self.window(), f"运行异常：{e}", "danger")
            self.run_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            return

        self._append_log(
            f"完成：成功 {sum(1 for o, e in zip(result.row_outputs, result.row_errors) if o and not e)} "
            f"/ 失败 {sum(1 for e in result.row_errors if e)} "
            f"/ {result.duration_ms_total} ms / tokens {result.prompt_tokens_total}+{result.completion_tokens_total}"
        )
        self.state.last_run = result
        # Auto-save as a project
        try:
            src_path = self._data.source_path if self._data else ""
            project_name = (
                f"{Path(src_path).stem if src_path else '分析'}"
                f" · {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            snap = ProjectSnapshot(
                name=project_name,
                preset_id=preset.id,
                preset_name=preset.name,
                model_id=preset.model,
                analysis_model_id=(model.id if model else ""),
                analysis_model_name=(model.name if model else "（无模型）"),
                mode=mode,
                source_path=src_path,
                columns=list(result.columns),
                rows=list(result.rows),
                row_outputs=list(result.row_outputs),
                row_errors=list(result.row_errors),
                summary_markdown=result.summary_markdown,
                prompt_tokens_total=result.prompt_tokens_total,
                completion_tokens_total=result.completion_tokens_total,
                duration_ms_total=result.duration_ms_total,
            )
            self.state.projects.save(snap)
            self._append_log(f"已保存为项目：{project_name}")
        except Exception as e:  # noqa: BLE001
            self._append_log(f"保存项目失败：{e}")

        self.run_completed.emit(result)
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        # default save next to source
        try:
            src = Path(self._data.source_path)
            out_path = src.with_name(f"{src.stem}_分析结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
            write_results(
                output_path=out_path,
                input_columns=self._data.columns,
                rows=self._data.rows,
                output_fields=model.output_fields if model else [],
                row_outputs=result.row_outputs,
                row_errors=result.row_errors,
                summary_markdown=result.summary_markdown,
                meta={
                    "preset": preset.name,
                    "model": preset.model,
                    "analysis_model": model.name if model else "（无模型 · 整表汇总）",
                    "mode": mode,
                    "duration_ms": result.duration_ms_total,
                },
            )
            self._append_log(f"已写入结果文件：{out_path}")
            h.toast(self.window(), f"已生成结果 Excel：{out_path.name}", "success")
        except Exception as e:  # noqa: BLE001
            self._append_log(f"写出 Excel 失败：{e}")
            h.toast(self.window(), f"写出 Excel 失败：{e}", "danger")

    def _on_cancel(self) -> None:
        if self._cancel_event:
            self._cancel_event.set()
            self._append_log("已请求取消，等待进行中的任务结束…")
