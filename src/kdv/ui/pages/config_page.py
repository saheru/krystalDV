"""Config page — manage LLM presets (base url, key, model, params)."""
from __future__ import annotations

from datetime import datetime

import qasync
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from kdv.config.models import LLMPreset
from kdv.config.store import SecretStore
from kdv.llm.client import LLMClient
from kdv.ui import helpers as h
from kdv.ui.animations import fade_in
from kdv.ui.state import AppState


# Fixed list-item height so two-line content always fits without overlap.
LIST_ITEM_HEIGHT = 64


class ConfigPage(QWidget):
    presets_changed = Signal()

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self._current: LLMPreset | None = None
        self.setObjectName("page")
        self._build()
        self._reload_list()

    # ---- layout -----------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(h.heading("LLM 配置预设", level=1))
        head.addStretch(1)
        new_btn = h.primary_button("+ 新建预设")
        new_btn.clicked.connect(self._on_new)
        head.addWidget(new_btn)
        root.addLayout(head)
        root.addWidget(h.muted("管理多个 OpenAI 兼容服务（DeepSeek / 智谱 / Moonshot / OpenAI / Azure 等）。"))

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)

        left = h.make_card(padding=12)
        left_lay = left.layout()
        left_lay.addWidget(h.heading("我的预设", level=3))
        self.list = QListWidget()
        self.list.setSpacing(6)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.itemSelectionChanged.connect(self._on_select)
        left_lay.addWidget(self.list)
        split.addWidget(left)

        # Right side wrapped in a scroll area so the action buttons are always reachable.
        right_card = h.make_card(padding=20)
        right_card_lay = right_card.layout()
        right_card_lay.addWidget(h.heading("详情", level=2))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        right_card_lay.addWidget(self._scroll, 1)

        # Holder placed inside scroll area
        self._detail_holder = QWidget()
        self._detail_holder.setObjectName("page")
        self._detail_holder_lay = QVBoxLayout(self._detail_holder)
        self._detail_holder_lay.setContentsMargins(0, 0, 0, 0)
        self._detail_holder_lay.setSpacing(14)
        self._scroll.setWidget(self._detail_holder)

        split.addWidget(right_card)
        split.setSizes([320, 720])
        root.addWidget(split, 1)

    def _build_form(self) -> None:
        # clear holder
        while self._detail_holder_lay.count():
            it = self._detail_holder_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            else:
                lay = it.layout()
                if lay is not None:
                    self._delete_layout(lay)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：DeepSeek 主账号")
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("https://api.openai.com/v1")
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("sk-...")
        self.show_key_btn = h.ghost_button("显示")
        self.show_key_btn.clicked.connect(self._toggle_show_key)
        key_wrap = QWidget()
        key_row = QHBoxLayout(key_wrap)
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(6)
        key_row.addWidget(self.api_key_input, 1)
        key_row.addWidget(self.show_key_btn)

        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("gpt-4o-mini / deepseek-chat / glm-4 ...")

        self.temp_input = QDoubleSpinBox()
        self.temp_input.setRange(0.0, 2.0)
        self.temp_input.setSingleStep(0.1)
        self.temp_input.setDecimals(2)

        self.maxtok_input = QSpinBox()
        self.maxtok_input.setRange(64, 32768)
        self.maxtok_input.setSingleStep(128)

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(5, 600)
        self.timeout_input.setSuffix(" 秒")

        self.concurrency_input = QSpinBox()
        self.concurrency_input.setRange(1, 50)

        self.retries_input = QSpinBox()
        self.retries_input.setRange(0, 10)

        self.struct_mode = QComboBox()
        self.struct_mode.addItems(["auto（推荐）", "function_calling（强制）", "prompt（兼容）"])

        for w in (
            self.name_input,
            self.base_url_input,
            self.api_key_input,
            self.model_input,
        ):
            w.setMinimumHeight(34)
        for w in (
            self.temp_input,
            self.maxtok_input,
            self.timeout_input,
            self.concurrency_input,
            self.retries_input,
            self.struct_mode,
        ):
            w.setMinimumHeight(34)

        form.addRow("名称", self.name_input)
        form.addRow("Base URL", self.base_url_input)
        form.addRow("API Key", key_wrap)
        form.addRow("模型 ID", self.model_input)
        form.addRow("温度", self.temp_input)
        form.addRow("最大 tokens", self.maxtok_input)
        form.addRow("超时", self.timeout_input)
        form.addRow("并发", self.concurrency_input)
        form.addRow("最大重试", self.retries_input)
        form.addRow("结构化模式", self.struct_mode)

        self._detail_holder_lay.addLayout(form)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(QLabel("连接状态："))
        self.status_badge = h.badge("未测试", "muted")
        status_row.addWidget(self.status_badge)
        self.status_msg = h.muted("")
        self.status_msg.setWordWrap(True)
        status_row.addWidget(self.status_msg, 1)
        self._detail_holder_lay.addLayout(status_row)

        self._detail_holder_lay.addWidget(h.hline())

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.test_btn = h.primary_button("测试连接")
        self.test_btn.clicked.connect(self._on_test)
        self.save_btn = h.primary_button("保存")
        self.save_btn.clicked.connect(self._on_save)
        self.delete_btn = h.danger_button("删除")
        self.delete_btn.clicked.connect(self._on_delete)
        actions.addWidget(self.test_btn)
        actions.addWidget(self.save_btn)
        actions.addStretch(1)
        actions.addWidget(self.delete_btn)
        self._detail_holder_lay.addLayout(actions)

        self._detail_holder_lay.addStretch(1)

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

    def _show_empty(self) -> None:
        while self._detail_holder_lay.count():
            it = self._detail_holder_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            else:
                lay = it.layout()
                if lay is not None:
                    self._delete_layout(lay)
        es = h.empty_state(
            "还没有 LLM 配置",
            "新建一个 OpenAI 兼容的服务配置，例如 DeepSeek、智谱 GLM、Moonshot 或 OpenAI 本身。",
            "+ 新建预设",
            self._on_new,
        )
        self._detail_holder_lay.addWidget(es)
        self._detail_holder_lay.addStretch(1)
        fade_in(es)

    # ---- list -------------------------------------------------------------
    def _reload_list(self) -> None:
        self.list.clear()
        items = self.state.presets.list()
        for p in items:
            it = QListWidgetItem()
            it.setSizeHint(self._list_item_size())
            it.setData(Qt.UserRole, p.id)
            self.list.addItem(it)
            w = self._render_list_item(p)
            self.list.setItemWidget(it, w)

        if not items:
            self._current = None
            self._show_empty()
            return

        target_id = self.state.settings.settings.last_preset_id or items[0].id
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == target_id:
                self.list.setCurrentRow(i)
                return
        self.list.setCurrentRow(0)

    def _list_item_size(self):
        from PySide6.QtCore import QSize

        # Wider than the column so eliding kicks in instead of clipping behind scrollbar
        return QSize(0, LIST_ITEM_HEIGHT)

    def _render_list_item(self, p: LLMPreset) -> QWidget:
        w = QWidget()
        w.setFixedHeight(LIST_ITEM_HEIGHT - 4)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel(p.name or "(未命名)")
        title.setStyleSheet("font-weight: 600; font-size: 13px; background: transparent;")
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        title.setMaximumWidth(180)
        title.setTextFormat(Qt.PlainText)
        head.addWidget(title, 1)
        kind = {"ok": "success", "fail": "danger", "unknown": "muted"}[p.last_test_status]
        head.addWidget(
            h.badge(
                {"ok": "连通", "fail": "失败", "unknown": "未测"}[p.last_test_status],
                kind,
            )
        )
        lay.addLayout(head)

        sub = QLabel(f"{p.model} · {p.base_url}")
        sub.setStyleSheet(
            "color: #6B7280; font-size: 11px; background: transparent;"
        )
        sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sub.setTextFormat(Qt.PlainText)
        # Manual elide to avoid the label overflowing past the list item bounds
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(sub.font())
        sub.setText(fm.elidedText(sub.text(), Qt.ElideRight, 240))
        lay.addWidget(sub)
        return w

    def _on_select(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        pid = item.data(Qt.UserRole)
        p = self.state.presets.get(pid)
        if not p:
            return
        self._current = p
        self.state.settings.update(last_preset_id=pid)
        self._build_form()
        self._populate_form(p)
        fade_in(self._detail_holder)

    def _populate_form(self, p: LLMPreset) -> None:
        self.name_input.setText(p.name)
        self.base_url_input.setText(p.base_url)
        self.api_key_input.setText(self.state.get_api_key(p))
        self.model_input.setText(p.model)
        self.temp_input.setValue(p.temperature)
        self.maxtok_input.setValue(p.max_tokens)
        self.timeout_input.setValue(p.timeout_seconds)
        self.concurrency_input.setValue(p.max_concurrency)
        self.retries_input.setValue(p.max_retries)
        idx = {"auto": 0, "function_calling": 1, "prompt": 2}.get(p.structured_mode, 0)
        self.struct_mode.setCurrentIndex(idx)
        kind = {"ok": "success", "fail": "danger", "unknown": "muted"}[p.last_test_status]
        self.status_badge.setProperty("badge", kind)
        self.status_badge.setText(
            {"ok": "连通", "fail": "失败", "unknown": "未测试"}[p.last_test_status]
        )
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
        self.status_msg.setText(p.last_test_message or "")

    # ---- actions ----------------------------------------------------------
    def _on_new(self) -> None:
        p = LLMPreset()
        self.state.presets.upsert(p)
        self.state.settings.update(last_preset_id=p.id)
        self._reload_list()
        self.presets_changed.emit()

    def _toggle_show_key(self) -> None:
        if self.api_key_input.echoMode() == QLineEdit.Password:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
            self.show_key_btn.setText("隐藏")
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.show_key_btn.setText("显示")

    def _collect_form(self) -> LLMPreset:
        p = self._current or LLMPreset()
        p.name = self.name_input.text().strip() or "新建预设"
        p.base_url = self.base_url_input.text().strip() or "https://api.openai.com/v1"
        p.model = self.model_input.text().strip() or "gpt-4o-mini"
        p.temperature = float(self.temp_input.value())
        p.max_tokens = int(self.maxtok_input.value())
        p.timeout_seconds = int(self.timeout_input.value())
        p.max_concurrency = int(self.concurrency_input.value())
        p.max_retries = int(self.retries_input.value())
        modes = ["auto", "function_calling", "prompt"]
        p.structured_mode = modes[self.struct_mode.currentIndex()]  # type: ignore[assignment]
        return p

    def _on_save(self) -> None:
        if not self._current:
            return
        p = self._collect_form()
        self.state.presets.upsert(p)
        self.state.set_api_key(p, self.api_key_input.text())
        self._current = p
        self._reload_list()
        h.toast(self.window(), "已保存", "success")
        self.presets_changed.emit()

    def _on_delete(self) -> None:
        if not self._current:
            return
        self.state.secrets.delete(SecretStore.llm_key(self._current.id))
        self.state.presets.delete(self._current.id)
        self._current = None
        self.state.settings.update(last_preset_id="")
        self._reload_list()
        h.toast(self.window(), "已删除", "info")
        self.presets_changed.emit()

    @qasync.asyncSlot()
    async def _on_test(self) -> None:
        if not self._current:
            return
        # save first so the test reflects the form
        self._on_save()
        p = self._current
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中…")
        try:
            async with LLMClient(p, self.state.get_api_key(p)) as client:
                ok, msg = await client.test_connection()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, str(e)
        p.last_test_status = "ok" if ok else "fail"
        p.last_test_message = msg
        p.last_test_at = datetime.utcnow().isoformat()
        self.state.presets.upsert(p)
        self._populate_form(p)
        self._reload_list()
        h.toast(self.window(), msg, "success" if ok else "danger")
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测试连接")
