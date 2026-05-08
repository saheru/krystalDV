"""Projects page — list and reopen previously analysed datasets."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from kdv.analysis.projects import ProjectMeta, ProjectSnapshot
from kdv.analysis.runner import RunResult
from kdv.ui import helpers as h
from kdv.ui import style
from kdv.ui.state import AppState


class ProjectsPage(QWidget):
    project_opened = Signal(object)  # emits a RunResult reconstructed from the snapshot

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.setObjectName("page")
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(h.heading("分析项目", level=1))
        head.addStretch(1)
        refresh_btn = h.ghost_button("⟳ 刷新")
        refresh_btn.clicked.connect(self.refresh)
        head.addWidget(refresh_btn)
        root.addLayout(head)
        root.addWidget(h.muted("每次分析完成后会自动保存为项目。点击重新打开查看图表/对话/数据。"))

        self.list = QListWidget()
        self.list.setSpacing(0)
        self.list.itemDoubleClicked.connect(self._on_open)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        root.addWidget(self.list, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.open_btn = h.primary_button("📂 打开选中项目")
        self.open_btn.clicked.connect(self._on_open)
        self.rename_btn = h.ghost_button("✎ 重命名")
        self.rename_btn.clicked.connect(self._on_rename)
        self.delete_btn = h.danger_button("🗑 删除")
        self.delete_btn.clicked.connect(self._on_delete)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.delete_btn)
        actions.addWidget(self.open_btn)
        root.addLayout(actions)

    # ---- list -----------------------------------------------------------
    def refresh(self) -> None:
        self.list.clear()
        items = self.state.projects.list()
        if not items:
            es = h.empty_state(
                "暂无项目",
                "在『运行分析』页跑一次分析，结果会自动保存为项目并在这里列出。",
            )
            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 240))
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
            self.list.setItemWidget(it, es)
            return
        for meta in items:
            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 96))
            it.setData(Qt.UserRole, meta.project_id)
            self.list.addItem(it)
            self.list.setItemWidget(it, self._render_card(meta))

    def _render_card(self, meta: ProjectMeta) -> QWidget:
        card = QFrame()
        card.setObjectName("projCard")
        card.setStyleSheet(
            f"#projCard {{"
            f"  background: {style.BG_CARD};"
            f"  border: 1px solid {style.BORDER};"
            f"  border-radius: 10px;"
            f"  margin: 4px 0;"
            f"}}"
            f"#projCard:hover {{ border: 1px solid {style.PRIMARY}; }}"
        )
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel(meta.name)
        title.setStyleSheet(
            "font-weight: 600; font-size: 14px; background: transparent; border: none;"
        )
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        head.addWidget(title, 1)
        kind = "info" if meta.mode == "summary" else "success"
        head.addWidget(h.badge(meta.mode or "—", kind))
        outer.addLayout(head)

        sub = QLabel(
            f"{meta.preset_name or '—'} · {meta.model_id or '—'} · "
            f"{meta.n_rows} 行 × {meta.n_cols} 列"
        )
        sub.setStyleSheet(
            "color: #6B7280; font-size: 12px; background: transparent; border: none;"
        )
        outer.addWidget(sub)

        ts = QLabel(f"更新于 {meta.updated_at}")
        ts.setStyleSheet(
            "color: #9CA3AF; font-size: 11px; background: transparent; border: none;"
        )
        outer.addWidget(ts)
        return card

    def _selected_id(self) -> str | None:
        it = self.list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    # ---- actions --------------------------------------------------------
    def _on_open(self, *_args) -> None:
        pid = self._selected_id()
        if not pid:
            h.toast(self.window(), "请先选择一个项目", "warning")
            return
        snap = self.state.projects.load(pid)
        if not snap:
            h.toast(self.window(), "项目读取失败（已损坏或被删除）", "danger")
            return
        result = RunResult(
            run_id=snap.project_id,
            mode=snap.mode or "summary",  # type: ignore[arg-type]
            columns=list(snap.columns),
            rows=list(snap.rows),
            row_outputs=list(snap.row_outputs),
            row_errors=list(snap.row_errors),
            summary_markdown=snap.summary_markdown,
            summary_structured=None,
            prompt_tokens_total=snap.prompt_tokens_total,
            completion_tokens_total=snap.completion_tokens_total,
            duration_ms_total=snap.duration_ms_total,
        )
        self.state.last_run = result
        self.project_opened.emit(result)
        h.toast(self.window(), f"已打开：{snap.name}", "success")

    def _on_rename(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        meta = next((m for m in self.state.projects.list() if m.project_id == pid), None)
        if not meta:
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名项目", "新名称：", text=meta.name
        )
        if not ok or not new_name.strip():
            return
        self.state.projects.rename(pid, new_name.strip())
        self.refresh()

    def _on_delete(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        ans = QMessageBox.question(
            self, "确认删除", "确定要删除该项目吗？此操作不可撤销。"
        )
        if ans != QMessageBox.Yes:
            return
        self.state.projects.delete(pid)
        self.refresh()
        h.toast(self.window(), "已删除", "info")
