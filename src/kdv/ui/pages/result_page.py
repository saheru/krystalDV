"""Result page — KPI strip + chart grid + insights + data table + agent chat.

The chat panel hosts a long-lived AgentSession bound to the current dataset,
so users can iteratively request new charts / insights / reconciliations and
see the visualization grid update live.
"""
from __future__ import annotations

import asyncio
from typing import Any

import markdown as md
import qasync
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
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

from kdv.agent.session import AgentSession, SessionTurnResult
from kdv.agent.tools import ChartSpec, Insight
from kdv.agent.trace import TraceEvent
from kdv.analysis.runner import RunResult
from kdv.export.payload import ChartImage, ExportPayload, capture_widget_png
from kdv.export.docx_export import export_docx
from kdv.export.pptx_export import export_pptx
from kdv.ui import helpers as h
from kdv.ui import style
from kdv.ui.animations import count_up_label, reveal_height, stagger_reveal
from kdv.ui.state import AppState
from kdv.viz import charts as ch
from kdv.viz.column_stats import summarize_columns
from kdv.viz.recommender import ChartSuggestion, recommend_charts


class ResultPage(QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self._session: AgentSession | None = None
        self._chart_grid: QGridLayout | None = None
        self._chat_messages_lay: QVBoxLayout | None = None
        self._dynamic_charts: list[ChartSpec] = []
        self._dynamic_insights: list[Insight] = []
        self._auto_chart_count = 0
        # (title, rationale, body_widget) for exporting
        self._chart_records: list[tuple[str, str, QWidget]] = []
        self._current_result: RunResult | None = None
        self._build_empty()

    # ---- empty placeholder ----------------------------------------------
    def _build_empty(self) -> None:
        if self.layout():
            self._clear_layout()
        else:
            self._main = QVBoxLayout(self)
            self._main.setContentsMargins(24, 20, 24, 20)
            self._main.setSpacing(16)
        es = h.empty_state(
            "还没有分析结果",
            "在『运行』页面选择配置与模型，上传数据 Excel 开始分析吧。",
        )
        self._main.addWidget(es, 1)

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

    # ---- main entry ------------------------------------------------------
    def render_result(self, result: RunResult) -> None:
        self._clear_layout()
        self._dynamic_charts = []
        self._dynamic_insights = []
        self._auto_chart_count = 0
        self._chart_records = []
        self._current_result = result

        head = QHBoxLayout()
        head.addWidget(h.heading("分析结果", level=1))
        head.addStretch(1)
        export_word_btn = h.ghost_button("📄 导出 Word")
        export_word_btn.clicked.connect(self._on_export_word)
        export_ppt_btn = h.ghost_button("📊 导出 PPT")
        export_ppt_btn.clicked.connect(self._on_export_ppt)
        head.addWidget(export_word_btn)
        head.addWidget(export_ppt_btn)
        head.addWidget(h.muted(f"运行 ID: {result.run_id}"))
        self._main.addLayout(head)

        self._main.addWidget(self._build_kpis(result))

        # Main split: charts (left) + insights+chat (right)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)

        # ---- left: chart grid (scrollable) ------------------------------
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.NoFrame)
        self._left_inner = QWidget()
        grid_root = QVBoxLayout(self._left_inner)
        grid_root.setContentsMargins(0, 0, 0, 0)
        grid_root.setSpacing(12)

        self._charts_header = QHBoxLayout()
        self._charts_header.addWidget(h.heading("图表网格", level=3))
        self._charts_header.addStretch(1)
        grid_root.addLayout(self._charts_header)

        self._chart_grid = QGridLayout()
        self._chart_grid.setContentsMargins(0, 0, 0, 0)
        self._chart_grid.setSpacing(16)
        grid_root.addLayout(self._chart_grid)
        grid_root.addStretch(1)

        left_scroll.setWidget(self._left_inner)
        split.addWidget(left_scroll)

        # ---- right: tabs (insights / chat) ------------------------------
        right_tabs = QTabWidget()
        right_tabs.addTab(self._build_insights_panel(result), "📋 洞察")
        right_tabs.addTab(self._build_chat_panel(result), "💬 对话分析")
        right_tabs.setCurrentIndex(1)  # chat tab opens by default — primary interaction
        split.addWidget(right_tabs)
        split.setSizes([900, 540])

        self._main.addWidget(split, 1)

        # ---- bottom: raw data + errors ---------------------------------
        tabs = QTabWidget()
        tabs.addTab(self._build_data_table(result), "原始数据 + 输出")
        tabs.addTab(self._build_error_table(result), f"错误（{sum(1 for e in result.row_errors if e)}）")
        self._main.addWidget(tabs)

        # Populate auto-recommended charts
        self._populate_auto_charts(result)

        # Spin up agent session for live chat
        preset = self.state.selected_preset()
        if preset and self.state.get_api_key(preset):
            self._session = AgentSession(
                preset=preset,
                api_key=self.state.get_api_key(preset),
                columns=result.columns,
                rows=result.rows,
            )
            self._append_chat_bubble(
                "assistant",
                "您好！我已经加载了这份数据。可以让我做对账分析、新增图表、按某列筛选再分析等等——"
                "比如：\n\n"
                "- *把销售额按地区做柱状图*\n"
                "- *渠道商 A 这月对账单 12000、B 8500，做差异对比*\n"
                "- *找出金额异常高的前 10 行*\n",
            )
        else:
            self._append_chat_bubble(
                "assistant",
                "_未检测到可用的 LLM 配置，对话分析已禁用。请到『LLM 配置』页填写 API key。_",
            )

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
        v.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {accent};")
        l = QLabel(label)
        l.setStyleSheet("color: #6B7280; font-size: 12px;")
        card.layout().setSpacing(4)
        card.layout().addWidget(v)
        card.layout().addWidget(l)
        # If value is purely numeric (or % or comma-separated digits), animate count-up
        try:
            stripped = value.replace(",", "").rstrip("%").rstrip("s").rstrip(" ms").strip()
            target = float(stripped)
            unit = ""
            if "%" in value:
                unit = "%"
            elif " ms" in value:
                unit = " ms"
            elif " s" in value and "ms" not in value:
                unit = " s"
            decimals = 1 if "." in stripped else 0
            fmt = ("{:,." + str(decimals) + "f}") + unit
            count_up_label(v, start=0, end=target, duration_ms=750, fmt=fmt)
        except (ValueError, AttributeError):
            pass
        return card

    # ---- charts ---------------------------------------------------------
    def _populate_auto_charts(self, result: RunResult) -> None:
        merged_columns, merged_rows = _merge_rows(result)
        self._stats = summarize_columns(merged_columns, merged_rows)
        self._merged_columns = merged_columns
        self._merged_rows = merged_rows
        suggestions = recommend_charts(self._stats)

        for s in suggestions:
            widget = self._build_chart_from_suggestion(s, merged_columns, merged_rows, self._stats)
            if widget is None:
                continue
            card = self._wrap_chart_card(s.title, s.rationale, widget)
            self._add_chart_card_to_grid(card)
            self._auto_chart_count += 1

    def _add_chart_card_to_grid(self, card: QWidget) -> None:
        n = self._chart_grid.count()
        self._chart_grid.addWidget(card, n // 2, n % 2)
        # Smooth height reveal — no graphics effect, no input issues
        reveal_height(card, duration_ms=320)

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
        # Remember for export
        self._chart_records.append((title, rationale, body))
        return card

    def _build_chart_from_suggestion(
        self, s: ChartSuggestion, columns: list[str],
        rows: list[dict[str, Any]], stats,
    ) -> QWidget | None:
        try:
            return _build_chart_widget(s.kind, s.columns, s.params or {}, columns, rows, stats)
        except Exception:
            return h._empty(f"图表 {s.kind} 渲染失败")  # type: ignore[attr-defined]

    def _build_chart_from_spec(self, spec: ChartSpec) -> QWidget | None:
        try:
            return _build_chart_widget(
                spec.kind, spec.columns, spec.params or {},
                self._merged_columns, self._merged_rows, self._stats,
            )
        except Exception:
            return h._empty(f"图表 {spec.kind} 渲染失败")  # type: ignore[attr-defined]

    # ---- insights -------------------------------------------------------
    def _build_insights_panel(self, result: RunResult) -> QWidget:
        card = h.make_card()
        card.layout().addWidget(h.heading("整表汇总洞察", level=2))
        self._insights_browser = QTextBrowser()
        self._insights_browser.setOpenExternalLinks(True)
        self._insights_browser.setStyleSheet(
            "background: white; border: none; padding: 4px;"
            "font-size: 13px; line-height: 1.65;"
        )
        if result.summary_markdown:
            html = md.markdown(result.summary_markdown, extensions=["tables", "fenced_code"])
            self._insights_browser.setHtml(_INSIGHT_CSS + html)
        else:
            self._insights_browser.setHtml(
                _INSIGHT_CSS
                + "<p style='color:#9CA3AF; padding: 12px;'>本次运行未生成整表汇总。"
                "切到右上角『对话分析』提问获取动态洞察。</p>"
            )
        card.layout().addWidget(self._insights_browser, 1)
        return card

    def _append_dynamic_insight(self, ins: Insight) -> None:
        kind_color = {"info": "#3B82F6", "warning": "#F59E0B", "success": "#10B981"}
        col = kind_color.get(ins.severity, "#3B82F6")
        title_html = (
            f"<h3 style='color:{col}; margin-top:14px;'>🔎 {ins.title}</h3>"
            if ins.title else ""
        )
        body_html = md.markdown(ins.body, extensions=["tables", "fenced_code"])
        cur = self._insights_browser.toHtml()
        # Strip the auto-empty placeholder once a real insight arrives.
        if "本次运行未生成整表汇总" in cur:
            cur = _INSIGHT_CSS
        self._insights_browser.setHtml(cur + title_html + body_html)

    # ---- chat -----------------------------------------------------------
    def _build_chat_panel(self, result: RunResult) -> QWidget:
        card = h.make_card(padding=12)
        card.layout().addWidget(h.heading("对话分析", level=3))
        card.layout().addWidget(
            h.muted("和数据对话：让我新增图表、做对账比对、按条件筛选再分析等。")
        )

        # message list (scrollable)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        msgs_inner = QWidget()
        self._chat_messages_lay = QVBoxLayout(msgs_inner)
        self._chat_messages_lay.setContentsMargins(0, 0, 0, 0)
        self._chat_messages_lay.setSpacing(10)
        self._chat_messages_lay.addStretch(1)
        scroll.setWidget(msgs_inner)
        self._chat_scroll = scroll
        card.layout().addWidget(scroll, 1)

        # input box
        input_row = QHBoxLayout()
        self._chat_input = QPlainTextEdit()
        self._chat_input.setPlaceholderText(
            "提问或要求…  (Cmd/Ctrl+Enter 发送)"
        )
        self._chat_input.setFixedHeight(72)
        self._chat_input.installEventFilter(self)
        input_row.addWidget(self._chat_input, 1)

        send_col = QVBoxLayout()
        self._send_btn = h.primary_button("发送")
        self._send_btn.clicked.connect(self._on_send)
        self._cancel_btn = h.danger_button("取消")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        send_col.addWidget(self._send_btn)
        send_col.addWidget(self._cancel_btn)
        input_row.addLayout(send_col)

        card.layout().addLayout(input_row)
        return card

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent

        if obj is self._chat_input and event.type() == QEvent.KeyPress:
            from PySide6.QtCore import Qt as QtNs

            if event.key() in (QtNs.Key_Return, QtNs.Key_Enter):
                if event.modifiers() & (QtNs.ControlModifier | QtNs.MetaModifier):
                    self._on_send()
                    return True
        return super().eventFilter(obj, event)

    def _append_chat_bubble(self, role: str, text: str) -> QWidget:
        assert self._chat_messages_lay is not None
        bubble = QFrame()
        bubble.setProperty("card", False)
        if role == "user":
            color = style.PRIMARY
            bg = "#EEF1FF"
            align = Qt.AlignRight
            fg = style.TEXT
        elif role == "system":
            color = style.TEXT_MUTED
            bg = "#F3F4F6"
            align = Qt.AlignCenter
            fg = style.TEXT_MUTED
        else:
            color = "#10B981"
            bg = "#FFFFFF"
            align = Qt.AlignLeft
            fg = style.TEXT
        bubble.setStyleSheet(
            f"background: {bg}; border: 1px solid {style.BORDER}; "
            f"border-radius: 12px; padding: 10px 14px;"
        )
        lay = QVBoxLayout(bubble)
        lay.setContentsMargins(0, 0, 0, 0)
        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setStyleSheet(
            f"background: transparent; border: none; color: {fg}; "
            "font-size: 13px; line-height: 1.55;"
        )
        # Render markdown for assistant; plain for user
        if role == "assistant":
            body.setHtml(_INSIGHT_CSS + md.markdown(text, extensions=["tables", "fenced_code"]))
        else:
            body.setPlainText(text)
        body.setMinimumHeight(40)
        body.document().setTextWidth(420)
        h_ = int(body.document().size().height()) + 12
        body.setFixedHeight(min(max(h_, 36), 320))
        lay.addWidget(body)

        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        if align == Qt.AlignRight:
            wrapper.addStretch(1)
            wrapper.addWidget(bubble, 0)
        elif align == Qt.AlignCenter:
            wrapper.addStretch(1)
            wrapper.addWidget(bubble, 0)
            wrapper.addStretch(1)
        else:
            wrapper.addWidget(bubble, 0)
            wrapper.addStretch(1)
        wrapper_w = QWidget()
        wrapper_w.setLayout(wrapper)
        # Insert before the trailing stretch
        self._chat_messages_lay.insertWidget(self._chat_messages_lay.count() - 1, wrapper_w)
        reveal_height(wrapper_w, duration_ms=200)
        # Auto-scroll to bottom
        from PySide6.QtCore import QTimer

        QTimer.singleShot(40, lambda: self._chat_scroll.verticalScrollBar().setValue(
            self._chat_scroll.verticalScrollBar().maximum()
        ))
        return wrapper_w

    def _append_status_line(self, text: str, kind: str = "info") -> None:
        assert self._chat_messages_lay is not None
        lbl = QLabel(text)
        lbl.setProperty("badge", kind if kind in ("info", "success", "warning", "danger") else "muted")
        lbl.setAlignment(Qt.AlignCenter)
        wrapper = QHBoxLayout()
        wrapper.addStretch(1)
        wrapper.addWidget(lbl)
        wrapper.addStretch(1)
        wrapper_w = QWidget()
        wrapper_w.setLayout(wrapper)
        self._chat_messages_lay.insertWidget(self._chat_messages_lay.count() - 1, wrapper_w)

    @qasync.asyncSlot()
    async def _on_send(self) -> None:
        if self._session is None:
            h.toast(self.window(), "未配置 LLM，无法对话", "danger")
            return
        text = self._chat_input.toPlainText().strip()
        if not text:
            return
        self._chat_input.clear()
        self._append_chat_bubble("user", text)
        self._send_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._cancel_event = asyncio.Event()

        thinking_label = QLabel("正在思考…")
        thinking_label.setStyleSheet("color:#9CA3AF; font-size:12px;")
        wrap = QHBoxLayout()
        wrap.addWidget(thinking_label)
        wrap.addStretch(1)
        wrap_w = QWidget()
        wrap_w.setLayout(wrap)
        self._chat_messages_lay.insertWidget(self._chat_messages_lay.count() - 1, wrap_w)

        def on_event(e: TraceEvent) -> None:
            if e.kind == "tool_call":
                thinking_label.setText(f"调用工具：{e.text}")
            elif e.kind == "compaction":
                thinking_label.setText("压缩上下文…")
            elif e.kind == "thought" and e.text:
                thinking_label.setText(f"思考：{e.text[:60]}…")

        try:
            try:
                turn = await asyncio.wait_for(
                    self._session.turn(text, on_event=on_event,
                                       cancel_event=self._cancel_event),
                    timeout=max(60, self._session.preset.timeout_seconds * 4),
                )
            except asyncio.TimeoutError:
                self._append_status_line("⚠ 处理超时，已中止", "warning")
                return
            wrap_w.deleteLater()

            self._append_chat_bubble("assistant", turn.assistant_summary or "(空回复)")

            # Render new charts and insights
            for spec in turn.new_charts:
                widget = self._build_chart_from_spec(spec)
                if widget is None:
                    continue
                card = self._wrap_chart_card(spec.title, spec.rationale, widget)
                self._add_chart_card_to_grid(card)
            for ins in turn.new_insights:
                self._append_dynamic_insight(ins)

            if turn.new_charts or turn.new_insights:
                self._append_status_line(
                    f"已新增 {len(turn.new_charts)} 个图表 / {len(turn.new_insights)} 条洞察",
                    "success",
                )
            if turn.aborted_reason:
                self._append_status_line(f"中止原因：{turn.aborted_reason}", "warning")
        except Exception as e:  # noqa: BLE001
            wrap_w.deleteLater()
            self._append_status_line(f"出错：{e}", "danger")
        finally:
            self._send_btn.setEnabled(True)
            self._cancel_btn.setEnabled(False)

    def _on_cancel(self) -> None:
        if hasattr(self, "_cancel_event") and self._cancel_event:
            self._cancel_event.set()

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

    # ---- export -----------------------------------------------------------
    def _build_export_payload(self) -> ExportPayload:
        result = self._current_result
        preset = self.state.selected_preset()
        model = self.state.selected_model()

        kpis: list[tuple[str, str]] = []
        if result:
            total = len(result.rows)
            tokens = result.prompt_tokens_total + result.completion_tokens_total
            has_per_row = any(o is not None for o in result.row_outputs)
            if has_per_row:
                success = sum(1 for o, e in zip(result.row_outputs, result.row_errors) if o and not e)
                fail = total - success
                avg_ms = result.duration_ms_total // max(total, 1)
                rate = (success / total * 100) if total else 0
                kpis = [
                    ("总行数", str(total)),
                    ("成功", str(success)),
                    ("失败", str(fail)),
                    ("成功率", f"{rate:.1f}%"),
                    ("平均耗时", f"{avg_ms} ms"),
                    ("Token 用量", f"{tokens:,}"),
                ]
            else:
                kpis = [
                    ("数据行数", str(total)),
                    ("数据列数", str(len(result.columns))),
                    ("耗时", f"{result.duration_ms_total // 1000} s"),
                    ("Prompt tokens", f"{result.prompt_tokens_total:,}"),
                    ("Completion tokens", f"{result.completion_tokens_total:,}"),
                    ("分析模式", result.mode),
                ]

        # Capture every chart widget to PNG
        charts: list[ChartImage] = []
        for title, rationale, body in self._chart_records:
            try:
                png = capture_widget_png(body, scale=2.0)
            except Exception:
                png = b""
            charts.append(ChartImage(title=title, rationale=rationale, png_bytes=png))

        # Insights from session
        insights: list[tuple[str, str, str]] = []
        if self._session:
            for ins in self._session.all_insights:
                insights.append((ins.title, ins.body, ins.severity))

        sample_cols = list(result.columns) if result else []
        sample_rows = list(result.rows[:8]) if result else []

        return ExportPayload(
            title="Krystal Data Vision 分析报告",
            subtitle=(model.name if model else "无模型 · 直接整表汇总"),
            preset_name=(preset.name if preset else ""),
            model_id=(preset.model if preset else ""),
            analysis_model_name=(model.name if model else "—"),
            mode=(result.mode if result else ""),
            kpis=kpis,
            summary_markdown=(result.summary_markdown if result else "") or "",
            charts=charts,
            sample_columns=sample_cols,
            sample_rows=sample_rows,
            insights=insights,
        )

    def _on_export_word(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from datetime import datetime as _dt

        suggested = f"分析报告_{_dt.now().strftime('%Y%m%d_%H%M%S')}.docx"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Word", suggested, "Word 文档 (*.docx)"
        )
        if not path:
            return
        try:
            payload = self._build_export_payload()
            export_docx(payload, path)
            h.toast(self.window(), f"已导出 Word：{path}", "success")
        except Exception as e:  # noqa: BLE001
            import logging

            logging.exception("docx export failed")
            h.toast(self.window(), f"Word 导出失败：{e}", "danger")

    def _on_export_ppt(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from datetime import datetime as _dt

        suggested = f"分析报告_{_dt.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 PPT", suggested, "PowerPoint 文档 (*.pptx)"
        )
        if not path:
            return
        try:
            payload = self._build_export_payload()
            export_pptx(payload, path)
            h.toast(self.window(), f"已导出 PPT：{path}", "success")
        except Exception as e:  # noqa: BLE001
            import logging

            logging.exception("pptx export failed")
            h.toast(self.window(), f"PPT 导出失败：{e}", "danger")


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

_INSIGHT_CSS = """
<style>
body { font-family: 'PingFang SC','Helvetica Neue','Microsoft YaHei','Segoe UI',sans-serif;
       color:#1F2937; line-height:1.65; }
h1,h2,h3 { color:#111827; margin-top: 12px; }
h1 { font-size: 17px; }
h2 { font-size: 15px; }
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


def _build_chart_widget(
    kind: str,
    cols: list[str],
    params: dict[str, Any],
    all_columns: list[str],
    all_rows: list[dict[str, Any]],
    stats,
) -> QWidget | None:
    """Produce a chart widget for a given (kind, columns, params) spec."""

    def col_vals(name: str) -> list[Any]:
        return [r.get(name) for r in all_rows]

    if kind == "kpi":
        return ch.make_stat_summary({k: v for k, v in stats.items() if v.kind != "empty"})
    if kind == "stat_summary":
        return ch.make_stat_summary(stats)
    if kind == "donut":
        return ch.make_pie(col_vals(cols[0]), donut=True)
    if kind == "pie":
        return ch.make_pie(col_vals(cols[0]))
    if kind == "bar":
        if len(cols) == 2:
            agg = (params or {}).get("agg", "mean")
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
        from collections import defaultdict

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
        p = params or {}
        return ch.make_pivot(
            all_rows,
            row_field=p.get("row", cols[0]),
            value_field=p.get("value", cols[1] if len(cols) > 1 else cols[0]),
            agg=p.get("agg", "mean"),
        )
    if kind == "reconcile_bar":
        p = params or {}
        return ch.make_reconcile_bar(
            internal=p.get("internal", []),
            external=p.get("external", []),
        )
    return None
