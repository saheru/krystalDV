"""Config page — manage LLM presets (base url, key, model, params)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import qasync
from PySide6.QtCore import Qt, Signal  # noqa: F401
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
from kdv.ui.state import AppState


# QListWidget::item is fully transparent (see style.py) — the widget inside
# paints its own card. This height is therefore the widget height + a small
# spacing gap.
LIST_ITEM_CARD_HEIGHT = 64
LIST_ITEM_GAP = 8


class ConfigPage(QWidget):
    presets_changed = Signal()

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self._current: LLMPreset | None = None
        self._form_built: bool = False
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
        self._form_built = True

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
        self.maxtok_input.setRange(256, 131072)
        self.maxtok_input.setSingleStep(512)
        self.maxtok_input.setToolTip(
            "LLM 单次回复的最大 token 数（输出上限）。\n"
            "建议值：\n"
            "  · 逐行结构化分析（字段不多）：1024–2048\n"
            "  · 字段多/描述长：2048–4096\n"
            "  · 整表汇总长篇 Markdown：4096–8192（程序自动至少 4096）\n"
            "  · 大型多维报告：8192–16384\n"
            "调大不会浪费成本——它只是上限，实际计费按 LLM 真实输出。"
        )

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(5, 600)
        self.timeout_input.setSuffix(" 秒")

        self.concurrency_input = QSpinBox()
        self.concurrency_input.setRange(1, 50)

        self.retries_input = QSpinBox()
        self.retries_input.setRange(0, 10)

        self.struct_mode = QComboBox()
        self.struct_mode.addItems(["auto（推荐）", "function_calling（强制）", "prompt（兼容）"])

        self.batch_size_input = QSpinBox()
        self.batch_size_input.setRange(1, 30)
        self.batch_size_input.setSingleStep(1)
        self.batch_size_input.setSuffix(" 行/次")

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
            self.batch_size_input,
        ):
            w.setMinimumHeight(34)

        def _hint(text: str) -> QWidget:
            from PySide6.QtWidgets import QLabel as _QL

            lbl = _QL(text)
            lbl.setStyleSheet("color:#9CA3AF; font-size:11px; padding:0 0 4px 2px;")
            lbl.setWordWrap(True)
            return lbl

        form.addRow("名称", self.name_input)
        form.addRow("", _hint("给配置起个易识别的名字，例如『DeepSeek 主账号』『智谱 GLM 测试』。"))

        form.addRow("Base URL", self.base_url_input)
        form.addRow("", _hint(
            "OpenAI 兼容服务的 API 端点，到 /v1 为止：\n"
            "  · OpenAI       https://api.openai.com/v1\n"
            "  · DeepSeek     https://api.deepseek.com/v1\n"
            "  · 智谱 GLM     https://open.bigmodel.cn/api/paas/v4\n"
            "  · Moonshot     https://api.moonshot.cn/v1\n"
            "  · 火山方舟     https://ark.cn-beijing.volces.com/api/v3\n"
            "  · Azure OpenAI https://<your>.openai.azure.com/openai/deployments/<id>"
        ))

        form.addRow("API Key", key_wrap)
        form.addRow("", _hint("以 sk- 或服务商指定前缀开头。会加密保存到系统钥匙串。"))

        form.addRow("模型 ID", self.model_input)
        form.addRow("", _hint(
            "服务商接受的模型标识符。常见：gpt-4o-mini / gpt-4o / "
            "deepseek-chat / glm-4 / moonshot-v1-128k / claude-3-5-sonnet-20241022。"
        ))

        form.addRow("温度", self.temp_input)
        form.addRow("", _hint(
            "0–2 之间。结构化数据分析建议 0.0–0.3（更确定、可重现）；"
            "需要发散洞察可设 0.5–0.8。"
        ))

        form.addRow("最大 tokens", self.maxtok_input)
        form.addRow("", _hint(
            "单次回复输出上限。建议：逐行 1024–2048；字段多 2048–4096；"
            "整表汇总 4096–8192；大型多维报告 8192–16384。设大不浪费——按真实输出计费。"
        ))

        form.addRow("超时", self.timeout_input)
        form.addRow("", _hint(
            "单次 HTTP 请求的等待秒数。简短问答 30；常规分析 60；超长 prompt（>8k tokens）建议 120–180。"
        ))

        form.addRow("并发", self.concurrency_input)
        form.addRow("", _hint(
            "逐行模式同时发出的请求数。免费/低速率服务 2–3；DeepSeek/OpenAI 标准 5–10；"
            "企业级 / 高速率配额 10–20。设太大会触发 429 限流。"
        ))

        form.addRow("最大重试", self.retries_input)
        form.addRow("", _hint(
            "遇到 429 / 5xx / 网络异常时自动指数退避重试的次数。常规 3 即可；网络不稳定可设 5。"
        ))

        form.addRow("结构化模式", self.struct_mode)
        form.addRow("", _hint(
            "  · auto（推荐）：优先用 function calling 强约束输出，失败则回退到提示词\n"
            "  · function_calling：强制走工具调用，部分国产代理可能不支持\n"
            "  · prompt：完全靠提示词要求 JSON 输出，兼容性最好但偶尔失败"
        ))

        form.addRow("批量大小", self.batch_size_input)
        form.addRow("", _hint(
            "逐行分析时把多少行打包成一次 LLM 调用——共享一份 schema/system prompt：\n"
            "  · 1：经典模式，每行单独调用（互不影响，最稳）\n"
            "  · 5–10：节省 40–60% 输入 token，速度快 3–5×（推荐）\n"
            "  · 15–30：极致省钱，但单批解析失败会让 30 行都标记为失败\n"
            "建议先用 1 跑通，再调到 10 优化成本/速度。"
        ))

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
        # Form widgets we cached as attrs are now dangling; force a rebuild
        # next time _ensure_form_for is called.
        self._form_built = False
        es = h.empty_state(
            "还没有 LLM 配置",
            "新建一个 OpenAI 兼容的服务配置，例如 DeepSeek、智谱 GLM、Moonshot 或 OpenAI 本身。",
            "+ 新建预设",
            self._on_new,
        )
        self._detail_holder_lay.addWidget(es)
        self._detail_holder_lay.addStretch(1)

    # ---- list -------------------------------------------------------------
    def _reload_list(self) -> None:
        # Critical: block list signals around clear() / setCurrentRow() so
        # itemSelectionChanged doesn't fire and recursively re-enter
        # _on_select while we're rebuilding. Without this, _on_save → reload
        # → setCurrentRow → _on_select → setItemWidget(s) → another
        # itemSelectionChanged could clobber `_current` mid-test, which is
        # how clicking 测试连接 on one preset visibly jumped back to a
        # different (previously-selected) one.
        self.list.blockSignals(True)
        try:
            self.list.clear()
            items = self.state.presets.list()
            if not items:
                self._current = None
                self._show_empty()
                return
            # Prefer the current detail panel's preset over whatever is
            # persisted in settings — otherwise side effects from other
            # pages (run_page.refresh_pickers writes last_preset_id too)
            # can yank focus to a different preset behind the user's back.
            target_id = (
                (self._current.id if self._current else None)
                or self.state.settings.settings.last_preset_id
                or items[0].id
            )
            target_idx = 0
            for i, p in enumerate(items):
                it = QListWidgetItem()
                it.setSizeHint(self._list_item_size())
                it.setData(Qt.UserRole, p.id)
                self.list.addItem(it)
                w = self._render_list_item(p, selected=p.id == target_id)
                self.list.setItemWidget(it, w)
                if p.id == target_id:
                    target_idx = i
            self.list.setCurrentRow(target_idx)
        finally:
            self.list.blockSignals(False)
        # Sync the form to the new selection without going through the
        # `itemSelectionChanged` path. _ensure_form_for() builds the form
        # only when needed and populates from the target preset.
        target = self.state.presets.get(target_id) if items else None
        if target is not None:
            self._current = target
            self._ensure_form_for(target)

    def _list_item_size(self):
        from PySide6.QtCore import QSize

        return QSize(0, LIST_ITEM_CARD_HEIGHT + LIST_ITEM_GAP)

    def _render_list_item(self, p: LLMPreset, *, selected: bool) -> QWidget:
        from PySide6.QtGui import QFontMetrics
        from PySide6.QtWidgets import QFrame

        # The inner card paints background + border + selection.
        card = QFrame()
        card.setObjectName("presetCard")
        card.setFixedHeight(LIST_ITEM_CARD_HEIGHT)
        from kdv.ui import style as _st

        border = _st.PRIMARY if selected else _st.BORDER
        bg = _st.ACCENT_SOFT if selected else _st.BG_CARD
        card.setStyleSheet(
            f"#presetCard {{"
            f"  background: {bg};"
            f"  border: {2 if selected else 1}px solid {border};"
            f"  border-radius: 10px;"
            f"}}"
        )

        outer = QVBoxLayout(card)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.setContentsMargins(0, 0, 0, 0)
        title = QLabel(p.name or "(未命名)")
        title.setStyleSheet("font-weight: 600; font-size: 13px; background: transparent; border: none;")
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        title.setTextFormat(Qt.PlainText)
        head.addWidget(title, 1)

        kind = {"ok": "success", "fail": "danger", "unknown": "muted"}[p.last_test_status]
        badge_lbl = h.badge(
            {"ok": "连通", "fail": "失败", "unknown": "未测"}[p.last_test_status],
            kind,
        )
        badge_lbl.setStyleSheet(badge_lbl.styleSheet() + " border: none;")
        head.addWidget(badge_lbl)
        outer.addLayout(head)

        sub = QLabel()
        sub.setStyleSheet(
            "color: #6B7280; font-size: 11px; background: transparent; border: none;"
        )
        sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sub.setTextFormat(Qt.PlainText)
        full = f"{p.model} · {p.base_url}"
        fm = QFontMetrics(sub.font())
        sub.setText(fm.elidedText(full, Qt.ElideRight, 240))
        outer.addWidget(sub)
        return card

    def _on_select(self) -> None:
        # Fired only by genuine user-driven selection changes — _reload_list
        # blocks the signal during programmatic rebuilds.
        item = self.list.currentItem()
        if not item:
            return
        pid = item.data(Qt.UserRole)
        p = self.state.presets.get(pid)
        if not p:
            return
        if self._current is not None and self._current.id == pid:
            # Already on this preset — no need to re-render anything.
            return
        self._current = p
        self.state.settings.update(last_preset_id=pid)
        # Re-render every list-item so only the chosen card shows the highlight.
        for i in range(self.list.count()):
            li = self.list.item(i)
            other_id = li.data(Qt.UserRole)
            other = self.state.presets.get(other_id)
            if other:
                self.list.setItemWidget(
                    li, self._render_list_item(other, selected=other_id == pid)
                )
        self._ensure_form_for(p)

    def _ensure_form_for(self, p: LLMPreset) -> None:
        """Build the form on first use, then just populate values.

        The previous version tore down + rebuilt every form widget on every
        selection change, which interacted badly with `_on_test`'s in-flight
        await — old widget refs would deleteLater() during the test, and
        signal recursion through _reload_list could end up overwriting the
        selection. Building once and only populating on switch removes that
        whole class of races.
        """
        if not self._form_built:
            self._build_form()
        self._populate_form(p)

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
        self.batch_size_input.setValue(getattr(p, "batch_size", 1))
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
        p.batch_size = int(self.batch_size_input.value())
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
        self._on_save()
        p = self._current
        # Hard wall-clock timeout — even if the proxy hangs forever, the UI
        # button will recover. preset.timeout × 2 + 5s slack covers retries.
        wall_clock = max(15, p.timeout_seconds * 2 + 5)

        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中…")
        ok = False
        msg = ""
        try:
            fc_support = "unknown"
            try:
                async with LLMClient(p, self.state.get_api_key(p)) as client:
                    ok, msg, fc_support = await asyncio.wait_for(
                        client.test_connection(), timeout=wall_clock
                    )
            except asyncio.TimeoutError:
                ok, msg = False, f"测试超时（{wall_clock}s 内未返回，建议检查 base URL / 网络 / 模型 ID 是否正确）"
            except Exception as e:  # noqa: BLE001
                logging.exception("test_connection failed")
                ok, msg = False, f"测试失败：{e}"

            p.last_test_status = "ok" if ok else "fail"
            p.last_test_message = msg
            p.last_test_at = datetime.utcnow().isoformat()
            if ok:
                p.fc_support = fc_support  # type: ignore[assignment]
            try:
                self.state.presets.upsert(p)
            except Exception:
                logging.exception("failed to persist preset after test")
            self._populate_form(p)
            self._reload_list()
            h.toast(self.window(), msg, "success" if ok else "danger")
        finally:
            # ALWAYS restore the button so the user isn't stuck.
            self.test_btn.setEnabled(True)
            self.test_btn.setText("测试连接")
