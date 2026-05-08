"""Agent 中心 — 多 agent 任务并发管理 + tab 视图。

每个运行中的 AgentJob 一个 tab，显示：任务列表 / 进度 / trace 流 / 取消按钮。
顶部 "+ 新建 Agent 任务" 按钮打开对话框启动新 agent。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from kdv.agent.manager import AgentJob
from kdv.agent.trace import TraceEvent
from kdv.excel.reader import ExcelTable, read_excel
from kdv.ui import helpers as h
from kdv.ui import style
from kdv.ui.state import AppState


class AgentsPage(QWidget):
    """Multi-tab agent management."""

    open_in_results = Signal(object)  # AgentJob — request to load result into result page

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self._tabs_by_job: dict[str, "AgentJobTab"] = {}
        self._build()
        # Wire to AgentManager — react when new jobs spawn or finish
        self.state.agents.job_added.connect(self._on_job_added)
        self.state.agents.job_removed.connect(self._on_job_removed)

    def _build(self) -> None:
        root, _page = h.make_scroll_page(self)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(h.heading("🤖 Agent 中心", level=1))
        head.addStretch(1)
        new_btn = h.primary_button("+ 新建 Agent 任务")
        new_btn.clicked.connect(self._on_new_job)
        head.addWidget(new_btn)
        root.addLayout(head)
        root.addWidget(
            h.muted(
                "Agent 会自主调用工具（读取列、聚合、相关性、对账、生成图表/洞察）来完成你给的任务。"
                "可同时运行多个 Agent，每个一个 tab。"
            )
        )

        # ---- usage card -------------------------------------------------
        usage = h.make_card()
        usage.layout().addWidget(h.heading("Agent 模式怎么用？", level=3))
        usage_text = QLabel(
            "<b>1. 上传你的数据 Excel</b>（任意结构都行，第一行是列名）<br>"
            "<b>2. 写下你想知道的问题</b>，每行一个：<br>"
            "&nbsp;&nbsp;&nbsp;• <i>找出销售额最高的前 10 个客户，做柱状图</i><br>"
            "&nbsp;&nbsp;&nbsp;• <i>哪个供应商月度差额最大？给个柱状图</i><br>"
            "&nbsp;&nbsp;&nbsp;• <i>金额和折扣是不是相关？</i><br>"
            "&nbsp;&nbsp;&nbsp;• <i>渠道商A本月对账12000、B 8500、C 5000，做差额分析</i><br>"
            "<b>3. Agent 自动用工具收集证据，把图表和洞察显示在这里</b>"
        )
        usage_text.setStyleSheet(
            "color: #374151; font-size: 13px; line-height: 1.7; "
            "background: transparent; border: none;"
        )
        usage_text.setWordWrap(True)
        usage_text.setTextFormat(Qt.RichText)
        usage.layout().addWidget(usage_text)
        root.addWidget(usage)

        # ---- tabs container ---------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._on_close_tab)
        self.tabs.setMinimumHeight(420)

        # placeholder when empty
        self._empty_tab = self._make_empty_tab()
        self.tabs.addTab(self._empty_tab, "暂无 Agent 任务")
        # Disable close button on placeholder
        self.tabs.tabBar().setTabButton(0, QTabBar.RightSide, None)

        root.addWidget(self.tabs)

    def _make_empty_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        es = h.empty_state(
            "还没有 Agent 任务",
            "点右上角『+ 新建 Agent 任务』开始。",
            "+ 新建 Agent 任务",
            self._on_new_job,
        )
        lay.addWidget(es)
        return w

    # ---- new job dialog ------------------------------------------------
    def _on_new_job(self) -> None:
        if not self.state.presets.list():
            h.toast(self.window(), "请先到『LLM 配置』页配置 API key", "warning")
            return
        dlg = NewAgentJobDialog(self.state, self)
        if dlg.exec() == QDialog.Accepted:
            payload = dlg.payload()
            if payload:
                self.state.agents.spawn(**payload)

    # ---- manager signal handlers ---------------------------------------
    def _on_job_added(self, job: AgentJob) -> None:
        # Remove placeholder tab if present
        if self.tabs.count() == 1 and self.tabs.widget(0) is self._empty_tab:
            self.tabs.removeTab(0)

        tab = AgentJobTab(job, self.state, self)
        tab.open_results_requested.connect(lambda j=job: self.open_in_results.emit(j))
        idx = self.tabs.addTab(tab, self._tab_title(job))
        self._tabs_by_job[job.id] = tab
        self.tabs.setCurrentIndex(idx)
        # Update tab title when status changes
        job.state_changed.connect(lambda _s, j=job: self._refresh_tab_title(j))

    def _on_job_removed(self, job_id: str) -> None:
        tab = self._tabs_by_job.pop(job_id, None)
        if tab is None:
            return
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            self.tabs.removeTab(idx)
        if self.tabs.count() == 0:
            self.tabs.addTab(self._empty_tab, "暂无 Agent 任务")
            self.tabs.tabBar().setTabButton(0, QTabBar.RightSide, None)

    def _on_close_tab(self, index: int) -> None:
        w = self.tabs.widget(index)
        if w is self._empty_tab:
            return
        if isinstance(w, AgentJobTab):
            self.state.agents.remove(w.job.id)

    def _tab_title(self, job: AgentJob) -> str:
        emoji = {
            "pending": "⏳",
            "running": "⚙️",
            "done": "✅",
            "cancelled": "⛔",
            "error": "❗",
        }.get(job.status, "•")
        return f"{emoji} {job.name[:24]}"

    def _refresh_tab_title(self, job: AgentJob) -> None:
        tab = self._tabs_by_job.get(job.id)
        if tab is None:
            return
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            self.tabs.setTabText(idx, self._tab_title(job))


# ======================================================================
# Per-job tab content
# ======================================================================
class AgentJobTab(QWidget):
    """Detail view for one AgentJob."""

    open_results_requested = Signal()

    def __init__(self, job: AgentJob, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job = job
        self.state = state
        self._build()
        # subscribe to job signals
        self.job.event.connect(self._on_event)
        self.job.state_changed.connect(self._on_state)
        self.job.finished.connect(self._on_finished)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(12)

        # ---- header card ------------------------------------------------
        head_card = h.make_card()
        title_row = QHBoxLayout()
        self.title_lbl = QLabel(self.job.name)
        self.title_lbl.setStyleSheet(
            "font-size: 16px; font-weight: 700; background: transparent; border: none;"
        )
        title_row.addWidget(self.title_lbl, 1)
        self.status_badge = h.badge(self._status_label(), self._status_kind())
        title_row.addWidget(self.status_badge)
        head_card.layout().addLayout(title_row)

        meta = QLabel(
            f"{self.job.preset.name} · {self.job.preset.model} · "
            f"{len(self.job.rows)} 行 × {len(self.job.columns)} 列  ·  "
            f"开始于 {self.job.started_at or '—'}"
        )
        meta.setStyleSheet(
            "color: #6B7280; font-size: 12px; background: transparent; border: none;"
        )
        meta.setWordWrap(True)
        head_card.layout().addWidget(meta)

        # Action row
        actions = QHBoxLayout()
        self.cancel_btn = h.danger_button("取消")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.open_btn = h.primary_button("📊 查看图表 / 洞察")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_results_requested.emit)
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        actions.addWidget(self.open_btn)
        head_card.layout().addLayout(actions)
        outer.addWidget(head_card)

        # ---- splitter: tasks (left) | trace (right) --------------------
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)

        tasks_card = h.make_card()
        tasks_card.layout().addWidget(h.heading("任务列表", level=3))
        tasks_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(self.job.tasks))
        tasks_view = QPlainTextEdit(tasks_text)
        tasks_view.setReadOnly(True)
        tasks_view.setStyleSheet(
            "QPlainTextEdit { font-size: 13px; line-height: 1.5; }"
        )
        tasks_card.layout().addWidget(tasks_view, 1)
        split.addWidget(tasks_card)

        trace_card = h.make_card()
        trace_card.layout().addWidget(h.heading("Agent 追踪", level=3))
        self.trace_view = QPlainTextEdit()
        self.trace_view.setReadOnly(True)
        self.trace_view.setMaximumBlockCount(2000)
        self.trace_view.setStyleSheet(
            "QPlainTextEdit { font-family: Menlo, monospace; font-size: 12px; "
            "background: #FAFBFC; }"
        )
        trace_card.layout().addWidget(self.trace_view, 1)
        split.addWidget(trace_card)
        split.setSizes([320, 720])

        outer.addWidget(split, 1)

        # Reflect any events that happened before the UI subscribed
        for e in self.job.events:
            self._on_event(e)

    # ---- helpers --------------------------------------------------------
    def _status_label(self) -> str:
        return {
            "pending": "等待启动",
            "running": "运行中",
            "done": "已完成",
            "cancelled": "已取消",
            "error": "出错",
        }.get(self.job.status, self.job.status)

    def _status_kind(self) -> str:
        return {
            "pending": "muted",
            "running": "info",
            "done": "success",
            "cancelled": "muted",
            "error": "danger",
        }.get(self.job.status, "muted")

    # ---- slots ----------------------------------------------------------
    def _on_event(self, e: TraceEvent) -> None:
        prefix = {
            "task_start": "▶ 任务开始",
            "task_end": "■ 任务结束",
            "thought": "💭",
            "tool_call": "🔧 调用",
            "tool_result": "↳ 结果",
            "compaction": "📦 上下文压缩",
            "error": "❌ 错误",
            "final": "✓",
        }.get(e.kind, e.kind)
        text = (e.text or "").splitlines()[0][:240]
        self.trace_view.appendPlainText(f"[{e.ts.split('T')[-1] if 'T' in e.ts else e.ts}] {prefix}  {text}")

    def _on_state(self, _new: str) -> None:
        self.status_badge.setText(self._status_label())
        self.status_badge.setProperty("badge", self._status_kind())
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
        if self.job.status in ("done", "cancelled", "error"):
            self.cancel_btn.setEnabled(False)
            self.open_btn.setEnabled(self.job.status == "done")

    def _on_finished(self, _result: Any) -> None:
        self._on_state(self.job.status)

    def _on_cancel(self) -> None:
        self.job.cancel()
        self.cancel_btn.setEnabled(False)


# ======================================================================
# New-job dialog
# ======================================================================
class NewAgentJobDialog(QDialog):
    """Modal: pick preset + Excel + write tasks."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._data: ExcelTable | None = None
        self.setWindowTitle("新建 Agent 任务")
        self.setMinimumSize(640, 540)
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        lay.addWidget(h.heading("新建 Agent 任务", level=2))
        lay.addWidget(h.muted("配置 LLM、上传数据，写下你想问的问题（每行一个任务）。"))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setVerticalSpacing(12)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：本月供应商对账分析")
        self.name_input.setMinimumHeight(36)

        self.preset_picker = QComboBox()
        self.preset_picker.setMinimumHeight(36)
        for p in self.state.presets.list():
            badge = ""
            if p.fc_support == "no":
                badge = "  ⚠ 不支持工具调用"
            elif p.fc_support == "unknown":
                badge = "  ⚪ 工具调用未测试"
            self.preset_picker.addItem(f"{p.name} · {p.model}{badge}", p.id)
        cur_p = self.state.selected_preset()
        if cur_p:
            i = self.preset_picker.findData(cur_p.id)
            if i >= 0:
                self.preset_picker.setCurrentIndex(i)
        self.preset_picker.currentIndexChanged.connect(self._refresh_fc_warning)

        # Data upload row
        data_row = QHBoxLayout()
        self.upload_btn = h.primary_button("📂 上传数据 Excel")
        self.upload_btn.clicked.connect(self._on_upload)
        self.data_label = h.muted("尚未上传数据")
        self.data_label.setWordWrap(True)
        data_row.addWidget(self.upload_btn)
        data_row.addWidget(self.data_label, 1)
        data_wrap = QWidget()
        data_wrap.setLayout(data_row)

        self.tasks_input = QPlainTextEdit()
        self.tasks_input.setPlaceholderText(
            "每行一个任务，例如：\n"
            "  找出金额最高的前 10 行，做柱状图\n"
            "  把销售按月份做时序图\n"
            "  渠道A本月对账12000，B 8500，做差额对比"
        )
        self.tasks_input.setMinimumHeight(180)

        form.addRow("任务名称", self.name_input)
        form.addRow("LLM 配置", self.preset_picker)
        form.addRow("数据 Excel", data_wrap)
        form.addRow("任务列表", self.tasks_input)
        lay.addLayout(form)

        # FC-support warning banner — shown only when the chosen preset
        # is known not to support OpenAI function calling.
        self._fc_warning = QLabel()
        self._fc_warning.setWordWrap(True)
        self._fc_warning.setVisible(False)
        self._fc_warning.setStyleSheet(
            "background: #FEF2F2; color: #991B1B; "
            "border: 1px solid #FCA5A5; border-radius: 8px; "
            "padding: 10px 14px; font-size: 12px; line-height: 1.6;"
        )
        lay.addWidget(self._fc_warning)
        self._refresh_fc_warning()

        btns = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.start_btn = h.primary_button("启动 Agent")
        self.start_btn.clicked.connect(self.accept)
        btns.addButton(self.start_btn, QDialogButtonBox.AcceptRole)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _refresh_fc_warning(self) -> None:
        if not hasattr(self, "_fc_warning"):
            return
        pid = self.preset_picker.currentData()
        p = self.state.presets.get(pid) if pid else None
        if p is None:
            self._fc_warning.setVisible(False)
            return
        if p.fc_support == "no":
            self._fc_warning.setText(
                "⚠ <b>该 LLM 配置不支持 OpenAI function calling</b>。"
                "Agent 模式必须依赖工具调用——这次任务大概率会失败。<br>"
                "建议：改用 OpenAI / DeepSeek / 智谱 GLM 等支持工具调用的端点；"
                "或用『运行分析』里的逐行/汇总模式（Agent 之外的模式不需要 function calling）。"
            )
            self._fc_warning.setTextFormat(Qt.RichText)
            self._fc_warning.setVisible(True)
        elif p.fc_support == "unknown":
            self._fc_warning.setText(
                "⚪ 该 LLM 配置尚未测试是否支持工具调用。Agent 模式依赖此能力——"
                "建议先到『LLM 配置』页点『测试连接』验证再来运行。"
            )
            self._fc_warning.setStyleSheet(
                "background: #FFFBEB; color: #92400E; "
                "border: 1px solid #FCD34D; border-radius: 8px; "
                "padding: 10px 14px; font-size: 12px; line-height: 1.6;"
            )
            self._fc_warning.setVisible(True)
        else:
            self._fc_warning.setVisible(False)

    def _on_upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据 Excel", "", "Excel 文件 (*.xlsx *.xlsm)"
        )
        if not path:
            return
        try:
            tbl = read_excel(path)
        except Exception as e:  # noqa: BLE001
            h.toast(self, f"读取失败：{e}", "danger")
            return
        self._data = tbl
        self.data_label.setText(
            f"{Path(path).name} · {len(tbl.rows)} 行 × {len(tbl.columns)} 列"
        )

    def payload(self) -> dict | None:
        pid = self.preset_picker.currentData()
        if not pid:
            h.toast(self.parent() or self, "请选择 LLM 配置", "warning")
            return None
        preset = self.state.presets.get(pid)
        if not preset:
            return None
        api_key = self.state.get_api_key(preset)
        if not api_key:
            h.toast(self.parent() or self, "选中预设的 API key 为空", "danger")
            return None
        if not self._data or not self._data.rows:
            h.toast(self.parent() or self, "请上传数据 Excel", "warning")
            return None
        tasks = [t.strip() for t in self.tasks_input.toPlainText().splitlines() if t.strip()]
        if not tasks:
            h.toast(self.parent() or self, "请至少写一个任务", "warning")
            return None
        name = self.name_input.text().strip() or (
            f"{Path(self._data.source_path).stem} · "
            f"{datetime.now().strftime('%H:%M:%S')}"
        )
        return {
            "name": name,
            "tasks": tasks,
            "preset": preset,
            "api_key": api_key,
            "columns": list(self._data.columns),
            "rows": list(self._data.rows),
        }
