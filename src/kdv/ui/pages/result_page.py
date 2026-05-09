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
from kdv.export.payload import ChartImage, ExportPayload, capture_widget_png  # noqa: F401
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
        self._floating_chat: QWidget | None = None
        self._build_empty()

    # ---- empty placeholder ----------------------------------------------
    def _build_empty(self) -> None:
        if self.layout():
            self._clear_layout()
        else:
            self._main = QVBoxLayout(self)
            self._main.setContentsMargins(28, 24, 28, 24)
            self._main.setSpacing(18)
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
    def render_result(self, result: RunResult | None) -> None:
        # Hard reset state so prior runs don't bleed into this one.
        self._clear_layout()
        self._dynamic_charts = []
        self._dynamic_insights = []
        self._auto_chart_count = 0
        self._chart_records = []
        self._current_result = result
        self._session = None
        # Tear down any visible floating chat — it was bound to the prior dataset.
        if hasattr(self, "_floating_chat") and self._floating_chat is not None:
            try:
                self._floating_chat.deleteLater()
            except Exception:
                pass
            self._floating_chat = None
        if result is None:
            # Run cleared (a new run is starting) — show neutral placeholder.
            self._build_empty()
            return
        # Prevent stale agent artefacts from previous renders polluting this view.
        agent_charts = self.state.extra.pop("agent_charts", None) or []
        agent_insights = self.state.extra.pop("agent_insights", None) or []
        # Retry-summary material — pulled into instance attrs so the retry
        # button can use them, and popped from state.extra so a non-agent
        # project opened next won't inherit them.
        self._agent_fallbacks = self.state.extra.pop("agent_fallbacks", None) or []
        self._agent_task_summaries = self.state.extra.pop("agent_task_summaries", None) or []
        self._agent_retry_preset = self.state.extra.pop("agent_retry_preset", None)
        self._agent_retry_api_key = self.state.extra.pop("agent_retry_api_key", "") or ""
        if hasattr(self, "_floating_chat") and self._floating_chat is not None:
            try:
                self._floating_chat.deleteLater()
            except Exception:
                pass
            self._floating_chat = None

        # ---- whole page in one scroll area ------------------------------
        from PySide6.QtWidgets import QScrollArea as _SA

        scroll = _SA()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_SA.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        page = QWidget()
        page.setObjectName("page")
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(28, 24, 28, 32)
        page_lay.setSpacing(20)
        scroll.setWidget(page)
        self._main.addWidget(scroll, 1)
        self._page_lay = page_lay  # for floating-chat reparenting reference

        head = QHBoxLayout()
        head.addWidget(h.heading("分析结果", level=1))
        head.addStretch(1)
        export_btn = h.primary_button("📤 导出报告")
        export_btn.clicked.connect(self._on_open_export_dialog)
        chat_toggle_btn = h.ghost_button("💬 对话分析")
        chat_toggle_btn.setToolTip("打开/关闭浮动对话框")
        chat_toggle_btn.clicked.connect(self._toggle_floating_chat)
        head.addWidget(chat_toggle_btn)
        head.addWidget(export_btn)
        head.addWidget(h.muted(f"运行 ID: {result.run_id}"))
        page_lay.addLayout(head)

        page_lay.addWidget(self._build_kpis(result))

        # ---- 整表汇总洞察 (full width card) -----------------------------
        insights_card = self._build_insights_panel(result)
        page_lay.addWidget(insights_card)

        # ---- 图表网格 (full width, vertical stack of cards) ------------
        charts_section = h.make_card(padding=16)
        charts_section.layout().addWidget(h.heading("分析图表", level=2))
        self._chart_grid = QGridLayout()
        self._chart_grid.setContentsMargins(0, 0, 0, 0)
        self._chart_grid.setHorizontalSpacing(16)
        self._chart_grid.setVerticalSpacing(16)
        charts_section.layout().addLayout(self._chart_grid)
        page_lay.addWidget(charts_section)

        # ---- bottom: raw data + errors (still tabs, full width) -------
        bot_tabs = QTabWidget()
        bot_tabs.setMinimumHeight(280)
        bot_tabs.addTab(self._build_data_table(result), "原始数据 + 输出")
        bot_tabs.addTab(
            self._build_error_table(result),
            f"错误（{sum(1 for e in result.row_errors if e)}）",
        )
        page_lay.addWidget(bot_tabs)

        # Populate auto-recommended charts
        self._populate_auto_charts(result)

        # Inject agent-completed artefacts (popped above so don't pollute next run)
        for spec in agent_charts:
            widget = self._build_chart_from_spec(spec)
            if widget is None:
                continue
            card = self._wrap_chart_card(spec.title, spec.rationale, widget)
            self._add_chart_card_to_grid(card)
        for ins in agent_insights:
            self._append_dynamic_insight(ins)

        # Spin up agent session for the floating chat
        preset = self.state.selected_preset()
        if preset and self.state.get_api_key(preset):
            self._session = AgentSession(
                preset=preset,
                api_key=self.state.get_api_key(preset),
                columns=result.columns,
                rows=result.rows,
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
            return _empty_chart_label(f"图表 {s.kind} 渲染失败")

    def _build_chart_from_spec(self, spec: ChartSpec) -> QWidget | None:
        try:
            return _build_chart_widget(
                spec.kind, spec.columns, spec.params or {},
                self._merged_columns, self._merged_rows, self._stats,
            )
        except Exception:
            return _empty_chart_label(f"图表 {spec.kind} 渲染失败")

    # ---- insights -------------------------------------------------------
    def _build_insights_panel(self, result: RunResult) -> QWidget:
        card = h.make_card()
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(h.heading("整表汇总洞察", level=2))
        head.addStretch(1)
        # Retry button is hidden by default; we show it only when the
        # current run has at least one fallback summary stashed in state.
        # Click → call LLM with stored prompts → replace summary in-place.
        self._retry_summary_btn = h.ghost_button("🔄 重试 LLM 总结")
        self._retry_summary_btn.setVisible(False)
        self._retry_summary_btn.setToolTip(
            "本次运行有任务的总结因 LLM 调用持续失败而走了确定性兜底，"
            "点这里重新调用 LLM 升级总结。"
        )
        self._retry_summary_btn.clicked.connect(self._on_retry_fallback_summaries)
        head.addWidget(self._retry_summary_btn)
        card.layout().addLayout(head)
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
        # Show the button if there are pending fallbacks for this run.
        fallbacks = getattr(self, "_agent_fallbacks", None) or []
        if fallbacks:
            self._retry_summary_btn.setVisible(True)
            self._retry_summary_btn.setText(
                f"🔄 重试 LLM 总结（{len(fallbacks)} 个任务用了兜底）"
            )
        return card

    @qasync.asyncSlot()
    async def _on_retry_fallback_summaries(self) -> None:
        """Re-issue the LLM summary call for every task that fell back.

        The agent runner stashes the exact prompts it WOULD HAVE sent into
        `state.extra["agent_fallbacks"]` whenever the in-loop summary retries
        all timed out. This handler walks that list, hits the LLM once per
        entry, and replaces the corresponding task_summaries[i].summary
        in-place. Successful entries are dropped from the fallback list so
        they don't re-trigger.
        """
        from kdv.llm.client import LLMClient

        fallbacks = getattr(self, "_agent_fallbacks", None) or []
        task_summaries = getattr(self, "_agent_task_summaries", None) or []
        preset = getattr(self, "_agent_retry_preset", None)
        api_key = getattr(self, "_agent_retry_api_key", "") or ""
        if not fallbacks:
            h.toast(self.window(), "没有需要重试的总结", "info")
            return
        if not preset or not api_key:
            h.toast(
                self.window(),
                "缺少 LLM 配置或 API key（仅当前会话内的运行可重试）",
                "warning",
            )
            return

        self._retry_summary_btn.setEnabled(False)
        original_text = self._retry_summary_btn.text()
        self._retry_summary_btn.setText("重试中…")

        upgraded = 0
        still_failing: list = []
        async with LLMClient(preset, api_key) as client:
            for fb in fallbacks:
                try:
                    resp = await client.chat(
                        system_prompt=fb.system_prompt,
                        user_prompt=fb.user_prompt,
                        schema_fields=None,
                        max_tokens=800,
                    )
                    text = (resp.text or "").strip()
                    if not text:
                        still_failing.append(fb)
                        continue
                    if 0 <= fb.task_index < len(task_summaries):
                        task_summaries[fb.task_index]["summary"] = text
                        task_summaries[fb.task_index].pop("is_fallback", None)
                    upgraded += 1
                except Exception as e:  # noqa: BLE001
                    fb.last_error = str(e)[:160]
                    still_failing.append(fb)

        if upgraded > 0:
            # Rebuild summary_markdown from the (now-upgraded) task_summaries
            # so the insights browser, future exports, etc. all see the new
            # prose. Mirrors main_window._on_agent_result_open's join.
            from kdv.agent.runner import sanitize_task_summary

            new_md = "\n\n".join(
                f"## 任务 {i + 1}：{ts['task']}\n\n"
                f"{sanitize_task_summary(ts['task'], ts['summary'])}"
                for i, ts in enumerate(task_summaries)
            )
            if self.state.last_run is not None:
                self.state.last_run.summary_markdown = new_md  # type: ignore[attr-defined]
            html = md.markdown(new_md, extensions=["tables", "fenced_code"])
            self._insights_browser.setHtml(_INSIGHT_CSS + html)

        # Keep the still-failing list on the page so subsequent clicks only
        # retry those.
        self._agent_fallbacks = still_failing

        self._retry_summary_btn.setEnabled(True)
        if not still_failing:
            self._retry_summary_btn.setVisible(False)
            h.toast(
                self.window(),
                f"已升级 {upgraded} 个任务总结（LLM 调用全部成功）",
                "success",
            )
        else:
            self._retry_summary_btn.setText(
                f"🔄 重试 LLM 总结（剩 {len(still_failing)} 个待升级）"
            )
            if upgraded > 0:
                h.toast(
                    self.window(),
                    f"升级 {upgraded} 个；剩 {len(still_failing)} 个仍失败，可继续点重试",
                    "warning",
                )
            else:
                last = still_failing[0].last_error if still_failing else ""
                h.toast(
                    self.window(),
                    f"全部仍失败：{last[:80]}",
                    "danger",
                )
            # Restore button label only if we didn't already update it above.
            if upgraded == 0:
                self._retry_summary_btn.setText(original_text)

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

    # ---- floating chat --------------------------------------------------
    def _toggle_floating_chat(self) -> None:
        if getattr(self, "_floating_chat", None) is None:
            self._floating_chat = self._build_floating_chat()
        if self._floating_chat.isVisible():
            self._floating_chat.hide()
        else:
            self._position_floating_chat()
            self._floating_chat.show()
            self._floating_chat.raise_()

    def _position_floating_chat(self) -> None:
        """Place the floating chat at the bottom-right of the result page."""
        if self._floating_chat is None:
            return
        parent = self.window() or self
        margin = 20
        w = 420
        hgt = 520
        try:
            geo = parent.geometry()
            x = geo.x() + geo.width() - w - margin
            y = geo.y() + geo.height() - hgt - margin
            self._floating_chat.setGeometry(x, y, w, hgt)
        except Exception:
            self._floating_chat.resize(w, hgt)

    def _build_floating_chat(self) -> QWidget:
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QFrame as _QF

        win = _QF(self.window())
        win.setObjectName("floatingChat")
        win.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        win.setAttribute(Qt.WA_TranslucentBackground, False)
        win.setStyleSheet(
            "#floatingChat {"
            f"  background: {style.BG_CARD};"
            f"  border: 1px solid {style.BORDER_STRONG};"
            "  border-radius: 14px;"
            "}"
        )

        outer = QVBoxLayout(win)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)

        # Drag handle row
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("💬 对话分析")
        title.setStyleSheet(
            "font-size: 14px; font-weight: 700; background: transparent; border: none;"
        )
        head.addWidget(title)
        head.addStretch(1)
        close_btn = QLabel("✕")
        close_btn.setStyleSheet(
            f"color: {style.TEXT_MUTED}; font-size: 14px; padding: 2px 6px;"
            f"background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.PointingHandCursor)

        def _close(_event):
            win.hide()
        close_btn.mousePressEvent = _close  # type: ignore[assignment]
        head.addWidget(close_btn)
        outer.addLayout(head)

        # Sub-text
        sub = h.muted(
            "和数据对话：新增图表 / 对账 / 筛选 / 计算……图表洞察会实时贴到主页面。"
        )
        sub.setWordWrap(True)
        outer.addWidget(sub)

        # Message list scroll
        scroll = QScrollArea(win)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        self._chat_messages_lay = QVBoxLayout(inner)
        self._chat_messages_lay.setContentsMargins(0, 0, 0, 0)
        self._chat_messages_lay.setSpacing(8)
        self._chat_messages_lay.addStretch(1)
        scroll.setWidget(inner)
        self._chat_scroll = scroll
        outer.addWidget(scroll, 1)

        # Input row
        input_row = QHBoxLayout()
        self._chat_input = QPlainTextEdit()
        self._chat_input.setPlaceholderText("提问或要求…  (Cmd/Ctrl+Enter 发送)")
        self._chat_input.setMinimumHeight(60)
        self._chat_input.setMaximumHeight(96)
        self._chat_input.installEventFilter(self)
        input_row.addWidget(self._chat_input, 1)

        send_col = QVBoxLayout()
        send_col.setSpacing(4)
        self._send_btn = h.primary_button("发送")
        self._send_btn.setMinimumHeight(28)
        self._send_btn.clicked.connect(self._on_send)
        self._cancel_btn = h.danger_button("取消")
        self._cancel_btn.setMinimumHeight(28)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        send_col.addWidget(self._send_btn)
        send_col.addWidget(self._cancel_btn)
        input_row.addLayout(send_col)
        outer.addLayout(input_row)

        # Make the title bar draggable
        self._chat_drag_offset = QPoint()

        def _press(event):
            if event.button() == Qt.LeftButton:
                self._chat_drag_offset = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
                event.accept()
        def _move(event):
            if event.buttons() & Qt.LeftButton:
                win.move(event.globalPosition().toPoint() - self._chat_drag_offset)
                event.accept()
        title.mousePressEvent = _press  # type: ignore[assignment]
        title.mouseMoveEvent = _move    # type: ignore[assignment]
        title.setCursor(Qt.SizeAllCursor)

        # Greeting
        if self._session is not None:
            self._append_chat_bubble(
                "assistant",
                "您好！这份数据已加载。可以让我新增图表、做对账、筛选再分析——"
                "图表和洞察会**实时贴到主页面**：\n\n"
                "- *把销售额按地区做柱状图*\n"
                "- *渠道 A 12000 / B 8500，做差异对比*\n"
                "- *找出金额最高的前 10 行*",
            )
        else:
            self._append_chat_bubble(
                "assistant",
                "_未检测到可用的 LLM 配置，对话分析已禁用。请到『LLM 配置』页填写 API key。_",
            )
        return win

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

    # ---- export ---------------------------------------------------------
    def _on_open_export_dialog(self) -> None:
        if not self._chart_records:
            h.toast(self.window(), "当前页面没有可导出的图表", "warning")
            return
        from kdv.ui.export_dialog import ExportReportDialog

        result = self._current_result
        preset = self.state.selected_preset()
        api_key = self.state.get_api_key(preset) if preset else ""
        kpis = self._collect_kpis_for_export(result)
        meta = {
            "subtitle": (self.state.selected_model().name if self.state.selected_model() else "无模型 · 整表汇总"),
            "preset_name": preset.name if preset else "",
            "model_id": preset.model if preset else "",
            "analysis_model_name": self.state.selected_model().name if self.state.selected_model() else "—",
            "mode": result.mode if result else "",
        }
        dlg = ExportReportDialog(
            parent=self.window(),
            chart_records=list(self._chart_records),
            result_columns=list(result.columns) if result else [],
            result_rows=list(result.rows[:30]) if result else [],
            result_summary=(result.summary_markdown if result else "") or "",
            kpis=kpis,
            meta=meta,
            preset=preset,
            api_key=api_key,
        )
        dlg.exec()

    def _collect_kpis_for_export(self, result: RunResult | None) -> list[tuple[str, str]]:
        if result is None:
            return []
        total = len(result.rows)
        has_per_row = any(o is not None for o in result.row_outputs)
        tokens = result.prompt_tokens_total + result.completion_tokens_total
        if has_per_row:
            success = sum(1 for o, e in zip(result.row_outputs, result.row_errors) if o and not e)
            fail = total - success
            avg_ms = result.duration_ms_total // max(total, 1)
            rate = (success / total * 100) if total else 0
            return [
                ("总行数", str(total)),
                ("成功", str(success)),
                ("失败", str(fail)),
                ("成功率", f"{rate:.1f}%"),
                ("平均耗时", f"{avg_ms} ms"),
                ("Token 用量", f"{tokens:,}"),
            ]
        return [
            ("数据行数", str(total)),
            ("数据列数", str(len(result.columns))),
            ("耗时", f"{result.duration_ms_total // 1000} s"),
            ("Prompt tokens", f"{result.prompt_tokens_total:,}"),
            ("Completion tokens", f"{result.completion_tokens_total:,}"),
            ("分析模式", result.mode),
        ]


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


def _empty_chart_label(text: str) -> QWidget:
    """Fallback widget shown when a chart fails to render."""
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet("color:#9CA3AF; font-size:13px; padding:32px;")
    return lbl


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
