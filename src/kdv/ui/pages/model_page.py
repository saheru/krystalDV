"""Model page — define an analysis model from sample/template Excel."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kdv.analysis.model import AnalysisModel
from kdv.excel.template import export_template_skeleton, load_output_template
from kdv.llm.schema import FieldSpec
from kdv.ui import helpers as h
from kdv.ui.state import AppState


SYSTEM_TEMPLATE_LABELS = [
    ("general", "通用数据分析"),
    ("sentiment", "情感/客户体验"),
    ("ticket_classify", "工单分类"),
    ("survey_open", "调研开放题"),
    ("general_eng", "General (English)"),
]

FIELD_TYPES = ["string", "number", "integer", "boolean", "enum", "array", "date"]


class ModelPage(QWidget):
    models_changed = Signal()

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self._current: AnalysisModel | None = None
        self.setObjectName("page")
        self._build()
        self._reload_list()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(h.heading("分析模型", level=1))
        head.addStretch(1)
        new_btn = h.primary_button("+ 新建模型")
        new_btn.clicked.connect(self._on_new)
        head.addWidget(new_btn)
        root.addLayout(head)
        root.addWidget(
            h.muted("用 Excel 定义分析输出结构：可上传含示例数据的 Excel，或带『字段定义』sheet 的输出模板。")
        )

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)

        left = h.make_card(padding=12)
        left.layout().addWidget(h.heading("我的模型", level=3))
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_select)
        left.layout().addWidget(self.list)
        split.addWidget(left)

        self._right_holder = QWidget()
        self._right_holder_lay = QVBoxLayout(self._right_holder)
        self._right_holder_lay.setContentsMargins(0, 0, 0, 0)
        self._right_holder_lay.setSpacing(16)
        split.addWidget(self._right_holder)
        split.setSizes([300, 820])
        root.addWidget(split, 1)

    # ---- list -------------------------------------------------------------
    def _reload_list(self) -> None:
        from PySide6.QtCore import QSize

        self.list.clear()
        items = self.state.models.list()
        if not items:
            self._show_empty()
            return
        target_id = self.state.settings.settings.last_model_id or items[0].id
        for m in items:
            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 64 + 8))
            it.setData(Qt.UserRole, m.id)
            self.list.addItem(it)
            w = self._render_list_item(m, selected=m.id == target_id)
            self.list.setItemWidget(it, w)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == target_id:
                self.list.setCurrentRow(i)
                return
        self.list.setCurrentRow(0)

    def _render_list_item(self, m: AnalysisModel, *, selected: bool = False) -> QWidget:
        from PySide6.QtWidgets import QFrame
        from kdv.ui import style as _st

        card = QFrame()
        card.setObjectName("modelCard")
        card.setFixedHeight(64)
        border = _st.PRIMARY if selected else _st.BORDER
        bg = _st.ACCENT_SOFT if selected else _st.BG_CARD
        card.setStyleSheet(
            f"#modelCard {{"
            f"  background: {bg};"
            f"  border: {2 if selected else 1}px solid {border};"
            f"  border-radius: 10px;"
            f"}}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        title = QLabel(m.name or "(未命名)")
        title.setStyleSheet(
            "font-weight: 600; font-size: 13px; background: transparent; border: none;"
        )
        title.setTextFormat(Qt.PlainText)
        lay.addWidget(title)
        sub = QLabel(f"{len(m.output_fields)} 个输出字段")
        sub.setStyleSheet(
            "color: #6B7280; font-size: 11px; background: transparent; border: none;"
        )
        lay.addWidget(sub)
        return card

    def _on_new(self) -> None:
        m = AnalysisModel()
        self.state.models.upsert(m)
        self._reload_list()
        self.models_changed.emit()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == m.id:
                self.list.setCurrentRow(i)
                break

    def _on_select(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        mid = item.data(Qt.UserRole)
        m = self.state.models.get(mid)
        if not m:
            return
        self._current = m
        self.state.settings.update(last_model_id=mid)
        for i in range(self.list.count()):
            li = self.list.item(i)
            other_id = li.data(Qt.UserRole)
            other = self.state.models.get(other_id)
            if other:
                self.list.setItemWidget(
                    li, self._render_list_item(other, selected=other_id == mid)
                )
        self._build_editor()
        self._populate_editor(m)

    # ---- editor -----------------------------------------------------------
    def _show_empty(self) -> None:
        self._clear_holder()
        es = h.empty_state(
            "还没有分析模型",
            "上传一份 Excel 模板（含示例数据，或包含『字段定义』sheet）即可生成新模型。",
            "+ 新建模型",
            self._on_new,
        )
        self._right_holder_lay.addWidget(es)

    def _clear_holder(self) -> None:
        while self._right_holder_lay.count():
            it = self._right_holder_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _build_editor(self) -> None:
        self._clear_holder()

        # ---- meta card --------------------------------------------------
        meta_card = h.make_card()
        meta_card.layout().addWidget(h.heading("基础信息", level=3))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(20)
        self.name_input = QLineEdit()
        self.desc_input = QLineEdit()
        self.template_input = QComboBox()
        for k, label in SYSTEM_TEMPLATE_LABELS:
            self.template_input.addItem(label, k)
        self.system_extra_input = QPlainTextEdit()
        self.system_extra_input.setPlaceholderText("可选：附加的系统提示词，会拼到内置模板之后。")
        self.system_extra_input.setFixedHeight(72)
        self.goal_input = QPlainTextEdit()
        self.goal_input.setPlaceholderText("例：识别每条工单的根因类别与紧急程度，并给出处理建议。")
        self.goal_input.setFixedHeight(72)
        form.addRow("名称", self.name_input)
        form.addRow("描述", self.desc_input)
        form.addRow("内置模板", self.template_input)
        form.addRow("附加系统提示词", self.system_extra_input)
        form.addRow("分析目标", self.goal_input)
        meta_card.layout().addLayout(form)
        self._right_holder_lay.addWidget(meta_card)

        # ---- import card -------------------------------------------------
        import_card = h.make_card()
        import_card.layout().addWidget(h.heading("从 Excel 导入输出结构", level=3))
        import_card.layout().addWidget(
            h.muted("支持两种 Excel：① 仅含示例数据（自动推断类型）；② 含『字段定义』sheet（精确定义字段）。")
        )
        row = QHBoxLayout()
        self.import_btn = h.primary_button("📂 上传输出模板 Excel")
        self.import_btn.clicked.connect(self._on_import)
        self.export_btn = h.ghost_button("⬇ 下载示例模板")
        self.export_btn.clicked.connect(self._on_export_template)
        self.source_lbl = h.muted("")
        row.addWidget(self.import_btn)
        row.addWidget(self.export_btn)
        row.addStretch(1)
        row.addWidget(self.source_lbl)
        import_card.layout().addLayout(row)
        self._right_holder_lay.addWidget(import_card)

        # ---- fields table -----------------------------------------------
        fields_card = h.make_card()
        fc_head = QHBoxLayout()
        fc_head.addWidget(h.heading("输出字段", level=3))
        fc_head.addStretch(1)
        add_field_btn = h.ghost_button("+ 新增字段")
        add_field_btn.clicked.connect(self._on_add_field)
        del_field_btn = h.ghost_button("− 删除选中")
        del_field_btn.clicked.connect(self._on_del_field)
        fc_head.addWidget(add_field_btn)
        fc_head.addWidget(del_field_btn)
        fields_card.layout().addLayout(fc_head)

        self.fields_table = QTableWidget(0, 6)
        self.fields_table.setHorizontalHeaderLabels(
            ["字段名", "类型", "描述/提示词", "示例", "枚举值（逗号）", "必填"]
        )
        self.fields_table.verticalHeader().setVisible(False)
        self.fields_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fields_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.fields_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.fields_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.fields_table.setAlternatingRowColors(True)
        self.fields_table.setMinimumHeight(200)
        fields_card.layout().addWidget(self.fields_table)
        self._right_holder_lay.addWidget(fields_card, 1)

        # ---- save row ----------------------------------------------------
        save_row = QHBoxLayout()
        self.save_btn = h.primary_button("保存模型")
        self.save_btn.clicked.connect(self._on_save)
        self.delete_btn = h.danger_button("删除模型")
        self.delete_btn.clicked.connect(self._on_delete)
        save_row.addWidget(self.save_btn)
        save_row.addStretch(1)
        save_row.addWidget(self.delete_btn)
        self._right_holder_lay.addLayout(save_row)

    def _populate_editor(self, m: AnalysisModel) -> None:
        self.name_input.setText(m.name)
        self.desc_input.setText(m.description)
        idx = next(
            (i for i, (k, _) in enumerate(SYSTEM_TEMPLATE_LABELS) if k == m.system_template), 0
        )
        self.template_input.setCurrentIndex(idx)
        self.system_extra_input.setPlainText(m.custom_system_prompt)
        self.goal_input.setPlainText(m.analysis_goal)
        self.source_lbl.setText(
            f"已从：{Path(m.sample_path).name}" if m.sample_path else "尚未导入模板。"
        )
        self._fill_fields_table(m.output_fields)

    def _fill_fields_table(self, fields: list[FieldSpec]) -> None:
        self.fields_table.setRowCount(len(fields))
        for i, f in enumerate(fields):
            self._set_field_row(i, f)

    def _set_field_row(self, i: int, f: FieldSpec) -> None:
        self.fields_table.setItem(i, 0, QTableWidgetItem(f.name))
        type_cb = QComboBox()
        type_cb.addItems(FIELD_TYPES)
        type_cb.setCurrentText(f.type)
        self.fields_table.setCellWidget(i, 1, type_cb)
        self.fields_table.setItem(i, 2, QTableWidgetItem(f.description))
        self.fields_table.setItem(i, 3, QTableWidgetItem(f.example))
        self.fields_table.setItem(i, 4, QTableWidgetItem("、".join(f.enum_values)))
        cb = QCheckBox()
        cb.setChecked(f.required)
        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(8, 0, 0, 0)
        wl.addWidget(cb)
        wl.addStretch(1)
        self.fields_table.setCellWidget(i, 5, wrapper)

    def _collect_fields(self) -> list[FieldSpec]:
        out: list[FieldSpec] = []
        for i in range(self.fields_table.rowCount()):
            name_it = self.fields_table.item(i, 0)
            name = name_it.text().strip() if name_it else ""
            if not name:
                continue
            type_cb: QComboBox = self.fields_table.cellWidget(i, 1)  # type: ignore[assignment]
            type_v = type_cb.currentText() if type_cb else "string"
            desc_it = self.fields_table.item(i, 2)
            desc = desc_it.text().strip() if desc_it else ""
            example_it = self.fields_table.item(i, 3)
            example = example_it.text().strip() if example_it else ""
            enum_it = self.fields_table.item(i, 4)
            enum_text = enum_it.text().strip() if enum_it else ""
            enum_vals = [s.strip() for s in enum_text.replace(",", "、").split("、") if s.strip()]
            required_w: QWidget = self.fields_table.cellWidget(i, 5)  # type: ignore[assignment]
            cb = required_w.findChild(QCheckBox) if required_w else None
            required = cb.isChecked() if cb else True
            out.append(
                FieldSpec(
                    name=name,
                    type=type_v,  # type: ignore[arg-type]
                    description=desc,
                    required=required,
                    enum_values=enum_vals,
                    example=example,
                )
            )
        return out

    # ---- actions ----------------------------------------------------------
    def _on_add_field(self) -> None:
        i = self.fields_table.rowCount()
        self.fields_table.insertRow(i)
        self._set_field_row(i, FieldSpec(name=f"字段{i+1}"))

    def _on_del_field(self) -> None:
        rows = sorted({it.row() for it in self.fields_table.selectedItems()}, reverse=True)
        for r in rows:
            self.fields_table.removeRow(r)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择输出模板 Excel", "", "Excel 文件 (*.xlsx *.xlsm)"
        )
        if not path:
            return
        try:
            result = load_output_template(path)
        except Exception as e:  # noqa: BLE001
            h.toast(self.window(), f"读取失败：{e}", "danger")
            return
        if not result.fields:
            h.toast(self.window(), "未识别到字段，请检查 Excel 内容。", "warning")
            return
        self._fill_fields_table(result.fields)
        if self._current:
            self._current.sample_path = path
        kind_label = "字段定义 sheet" if result.source == "explicit" else "示例数据自动推断"
        self.source_lbl.setText(f"来源：{Path(path).name}（{kind_label}, {len(result.fields)} 字段）")
        h.toast(
            self.window(),
            f"已识别 {len(result.fields)} 个字段（{kind_label}）",
            "success",
        )

    def _on_export_template(self) -> None:
        fields = self._collect_fields() or [
            FieldSpec(name="示例字段1", type="string", description="字段含义说明", example="示例值"),
            FieldSpec(name="示例字段2", type="enum", enum_values=["A", "B", "C"], description="枚举示例"),
        ]
        path, _ = QFileDialog.getSaveFileName(
            self, "保存示例模板", "kdv_template.xlsx", "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        try:
            export_template_skeleton(path, fields)
            h.toast(self.window(), f"已保存：{Path(path).name}", "success")
        except Exception as e:  # noqa: BLE001
            h.toast(self.window(), f"保存失败：{e}", "danger")

    def _on_save(self) -> None:
        if not self._current:
            return
        m = self._current
        m.name = self.name_input.text().strip() or "新建分析模型"
        m.description = self.desc_input.text().strip()
        m.system_template = self.template_input.currentData()
        m.custom_system_prompt = self.system_extra_input.toPlainText()
        m.analysis_goal = self.goal_input.toPlainText()
        m.output_fields = self._collect_fields()
        self.state.models.upsert(m)
        self._reload_list()
        h.toast(self.window(), "已保存模型", "success")
        self.models_changed.emit()

    def _on_delete(self) -> None:
        if not self._current:
            return
        self.state.models.delete(self._current.id)
        self._current = None
        self.state.settings.update(last_model_id="")
        self._reload_list()
        h.toast(self.window(), "已删除", "info")
        self.models_changed.emit()
