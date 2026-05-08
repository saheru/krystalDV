"""Main window — top bar + sidebar + stacked pages with cross-fade transitions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from kdv import __app_name__
from kdv.ui import helpers as h
from kdv.ui import style
from kdv.ui.pages.config_page import ConfigPage
from kdv.ui.pages.model_page import ModelPage
from kdv.ui.pages.projects_page import ProjectsPage
from kdv.ui.pages.result_page import ResultPage
from kdv.ui.pages.run_page import RunPage
from kdv.ui.state import AppState


NAV_ITEMS = [
    ("config", "⚙  LLM 配置"),
    ("model", "🧬  分析模型"),
    ("run", "▶  运行分析"),
    ("result", "📊  结果可视化"),
    ("projects", "📁  项目历史"),
]


class MainWindow(QMainWindow):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.setWindowTitle(__app_name__)
        self.resize(state.settings.settings.window_width, state.settings.settings.window_height)
        self.setMinimumSize(1024, 640)
        self._build()
        self._wire_signals()
        self._setup_shortcuts()

    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("page")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- top bar ---------------------------------------------------
        topbar = QWidget()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(56)
        tlay = QHBoxLayout(topbar)
        tlay.setContentsMargins(20, 0, 20, 0)
        tlay.setSpacing(12)

        logo_dot = QLabel("◆")
        logo_dot.setStyleSheet(f"color: {style.PRIMARY}; font-size: 22px;")
        title = QLabel("Krystal Data Vision")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        sub = QLabel("LLM 驱动的 Excel 数据分析")
        sub.setStyleSheet("color: #6B7280; font-size: 12px;")

        tlay.addWidget(logo_dot)
        tlay.addWidget(title)
        tlay.addWidget(sub)
        tlay.addStretch(1)

        help_btn = h.ghost_button("帮助")
        help_btn.clicked.connect(self._show_help)
        about_btn = h.ghost_button("关于")
        about_btn.clicked.connect(self._show_about)
        tlay.addWidget(help_btn)
        tlay.addWidget(about_btn)

        root.addWidget(topbar)

        # ---- body: sidebar + stack ------------------------------------
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        slay = QVBoxLayout(sidebar)
        slay.setContentsMargins(12, 16, 12, 16)
        slay.setSpacing(4)

        self._nav_buttons: dict[str, QPushButton] = {}
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for key, label in NAV_ITEMS:
            btn = QPushButton(label)
            btn.setProperty("navItem", True)
            btn.setCheckable(True)
            btn.setAutoExclusive(False)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._switch_to(k))
            self._nav_buttons[key] = btn
            self._nav_group.addButton(btn)
            slay.addWidget(btn)
        slay.addStretch(1)

        from kdv import __version__

        credit = QLabel(
            f"v{__version__}\n"
            "Leah Yao 作品\n"
            "鸣谢 Chris Chen"
        )
        credit.setStyleSheet(
            "color: #9CA3AF; font-size: 11px; padding: 8px 6px; line-height: 1.6;"
        )
        credit.setAlignment(Qt.AlignCenter)
        credit.setWordWrap(True)
        slay.addWidget(credit)

        body.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("page")
        self.config_page = ConfigPage(self.state)
        self.model_page = ModelPage(self.state)
        self.run_page = RunPage(self.state)
        self.result_page = ResultPage(self.state)
        self.projects_page = ProjectsPage(self.state)
        self._page_index = {
            "config": self.stack.addWidget(self.config_page),
            "model": self.stack.addWidget(self.model_page),
            "run": self.stack.addWidget(self.run_page),
            "result": self.stack.addWidget(self.result_page),
            "projects": self.stack.addWidget(self.projects_page),
        }

        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        self.setCentralWidget(central)
        self._switch_to("config")

    def _wire_signals(self) -> None:
        self.config_page.presets_changed.connect(self.run_page.refresh_pickers)
        self.model_page.models_changed.connect(self.run_page.refresh_pickers)
        self.run_page.run_completed.connect(self._on_run_completed)
        self.run_page.run_completed.connect(lambda _r: self.projects_page.refresh())
        self.projects_page.project_opened.connect(self._on_run_completed)

    def _setup_shortcuts(self) -> None:
        for i, (key, _) in enumerate(NAV_ITEMS, start=1):
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.activated.connect(lambda k=key: self._switch_to(k))

    def _on_run_completed(self, result) -> None:
        self.result_page.render_result(result)
        self._switch_to("result")

    # ---- nav --------------------------------------------------------------
    def _switch_to(self, key: str) -> None:
        for k, btn in self._nav_buttons.items():
            btn.setChecked(k == key)
        idx = self._page_index[key]
        if self.stack.currentIndex() == idx:
            return
        self.stack.setCurrentIndex(idx)
        # No page-level fade animation — Qt's QGraphicsOpacityEffect doesn't
        # compose with the shadow effects on inner cards and visibly clips
        # content in macOS. Subtle bubble/toast animations remain elsewhere.

    def _show_help(self) -> None:
        h.toast(
            self,
            "Ctrl+1/2/3/4 切换页面 · 在『LLM 配置』填入 Base URL 与 API Key 后开始",
            "info",
        )

    def _show_about(self) -> None:
        from kdv import __version__
        from PySide6.QtWidgets import (
            QDialog,
            QHBoxLayout as _HBox,
            QLabel as _Label,
            QVBoxLayout as _VBox,
        )
        from kdv.ui import style as _st

        dlg = QDialog(self)
        dlg.setWindowTitle("关于 Krystal Data Vision")
        dlg.setFixedSize(440, 360)
        layout = _VBox(dlg)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(14)

        logo_row = _HBox()
        logo = _Label("◆")
        logo.setStyleSheet(f"color: {_st.PRIMARY}; font-size: 36px;")
        logo_row.addWidget(logo)
        title = _Label("Krystal Data Vision")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #111827;")
        logo_row.addWidget(title)
        logo_row.addStretch(1)
        layout.addLayout(logo_row)

        sub = _Label("LLM 驱动的 Excel 数据分析工具")
        sub.setStyleSheet("color: #6B7280; font-size: 13px;")
        layout.addWidget(sub)

        line = _Label()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {_st.BORDER};")
        layout.addWidget(line)

        info_lines = [
            ("版本", f"v{__version__}"),
            ("作品", "Leah Yao 作品"),
            ("鸣谢", "Chris Chen"),
            ("仓库", "github.com/saheru/krystalDV"),
        ]
        for k, v in info_lines:
            row = _HBox()
            row.setSpacing(12)
            kl = _Label(k)
            kl.setStyleSheet("color: #9CA3AF; font-size: 12px; min-width: 60px;")
            kl.setFixedWidth(60)
            vl = _Label(v)
            vl.setStyleSheet("color: #1F2937; font-size: 13px; font-weight: 600;")
            row.addWidget(kl)
            row.addWidget(vl, 1)
            layout.addLayout(row)

        layout.addStretch(1)
        close = h.primary_button("关闭")
        close.clicked.connect(dlg.accept)
        bottom = _HBox()
        bottom.addStretch(1)
        bottom.addWidget(close)
        layout.addLayout(bottom)

        dlg.exec()

    # ---- persist window size ---------------------------------------------
    def closeEvent(self, e) -> None:  # noqa: N802
        self.state.settings.update(
            window_width=self.width(), window_height=self.height()
        )
        super().closeEvent(e)
