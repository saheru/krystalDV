"""Global QSS — single hardcoded modern light palette.

Loaded once on app startup. No runtime theme switching.
"""
from __future__ import annotations


PRIMARY = "#5B6CFF"
PRIMARY_HOVER = "#4F5DE8"
PRIMARY_PRESS = "#3F4DCC"
ACCENT_SOFT = "#EEF1FF"

BG = "#F7F8FA"
BG_CARD = "#FFFFFF"
BORDER = "#E5E7EB"
BORDER_STRONG = "#D1D5DB"
TEXT = "#1F2937"
TEXT_MUTED = "#6B7280"
TEXT_SUBTLE = "#9CA3AF"

SUCCESS = "#10B981"
WARNING = "#F59E0B"
DANGER = "#EF4444"
INFO = "#3B82F6"


GLOBAL_QSS = f"""
/* ====== Base ====== */
* {{
    font-family: "PingFang SC", "Helvetica Neue", "Microsoft YaHei", "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QWidget#topbar {{
    background: {BG_CARD};
    border-bottom: 1px solid {BORDER};
}}
QWidget#sidebar {{
    background: {BG_CARD};
    border-right: 1px solid {BORDER};
}}
QWidget#page {{
    background: {BG};
}}
QWidget[card="true"] {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QWidget[card="true"]:hover {{
    border: 1px solid #C7CCFF;
}}

/* ====== Sidebar nav buttons ====== */
QPushButton[navItem="true"] {{
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: {TEXT_MUTED};
    font-size: 14px;
    min-height: 22px;
}}
QPushButton[navItem="true"]:hover:!checked {{
    background: {ACCENT_SOFT};
    color: {PRIMARY};
}}
QPushButton[navItem="true"]:checked {{
    background: {PRIMARY};
    color: white;
    font-weight: 600;
}}
QPushButton[navItem="true"]:checked:hover {{
    background: {PRIMARY_HOVER};
    color: white;
}}

/* ====== Buttons ====== */
QPushButton {{
    background: {BG_CARD};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 9px 18px;
    font-size: 13px;
    min-height: 22px;
}}
QPushButton:hover {{
    border-color: {PRIMARY};
    color: {PRIMARY};
    background: {ACCENT_SOFT};
}}
QPushButton:pressed {{
    background: #E0E7FF;
    padding-top: 10px;
    padding-bottom: 8px;
}}
QPushButton:disabled {{
    color: {TEXT_SUBTLE};
    background: #F3F4F6;
    border-color: {BORDER};
}}
QPushButton[primary="true"] {{
    background: {PRIMARY};
    color: white;
    border: none;
    font-weight: 600;
    padding: 10px 24px;
    min-height: 22px;
}}
QPushButton[primary="true"]:hover {{
    background: {PRIMARY_HOVER};
}}
QPushButton[primary="true"]:pressed {{
    background: {PRIMARY_PRESS};
    padding-top: 11px;
    padding-bottom: 9px;
}}
QPushButton[primary="true"]:disabled {{
    background: #C7CCFF;
}}
QPushButton[ghost="true"] {{
    background: transparent;
    border: none;
    color: {PRIMARY};
    padding: 7px 10px;
    min-height: 18px;
}}
QPushButton[ghost="true"]:hover {{
    color: {PRIMARY_HOVER};
    background: {ACCENT_SOFT};
    border-radius: 8px;
}}
QPushButton[ghost="true"]:pressed {{
    background: #DCE3FF;
}}
QPushButton[danger="true"] {{
    background: white;
    color: {DANGER};
    border: 1px solid #FCA5A5;
    padding: 9px 18px;
    min-height: 22px;
}}
QPushButton[danger="true"]:hover {{
    background: #FEF2F2;
    border-color: {DANGER};
}}
QPushButton[danger="true"]:pressed {{
    background: #FEE2E2;
}}

/* ====== Inputs ====== */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {PRIMARY};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {PRIMARY};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_MUTED};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {PRIMARY};
    padding: 4px;
}}

/* ====== Labels ====== */
QLabel[h1="true"] {{ font-size: 24px; font-weight: 600; color: {TEXT}; }}
QLabel[h2="true"] {{ font-size: 18px; font-weight: 600; color: {TEXT}; }}
QLabel[h3="true"] {{ font-size: 15px; font-weight: 600; color: {TEXT}; }}
QLabel[muted="true"] {{ color: {TEXT_MUTED}; }}
QLabel[subtle="true"] {{ color: {TEXT_SUBTLE}; }}
QLabel[badge="success"] {{ color: {SUCCESS}; background: #ECFDF5; padding: 2px 10px; border-radius: 9999px; font-size: 12px; }}
QLabel[badge="warning"] {{ color: {WARNING}; background: #FFFBEB; padding: 2px 10px; border-radius: 9999px; font-size: 12px; }}
QLabel[badge="danger"]  {{ color: {DANGER};  background: #FEF2F2; padding: 2px 10px; border-radius: 9999px; font-size: 12px; }}
QLabel[badge="info"]    {{ color: {INFO};    background: #EFF6FF; padding: 2px 10px; border-radius: 9999px; font-size: 12px; }}
QLabel[badge="muted"]   {{ color: {TEXT_MUTED}; background: #F3F4F6; padding: 2px 10px; border-radius: 9999px; font-size: 12px; }}

/* ====== Tables ====== */
QTableWidget, QTableView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: transparent;
    alternate-background-color: #FAFBFC;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {PRIMARY};
}}
QHeaderView::section {{
    background: #F9FAFB;
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 10px 12px;
    font-weight: 600;
}}
QTableWidget::item, QTableView::item {{
    padding: 6px 12px;
    border: none;
}}

/* ====== Lists ======
 * Items are transparent / borderless. The widget INSIDE each item paints
 * its own background + border + selection — that gives us pixel-perfect
 * control without QSS padding clipping the content.
 */
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
}}
QListWidget::item:hover, QListWidget::item:selected {{
    background: transparent;
    border: none;
}}

/* ====== Scrollbars ====== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: #D1D5DB;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #9CA3AF; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 4px;
}}
QScrollBar::handle:horizontal {{
    background: #D1D5DB;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ====== Progress ====== */
QProgressBar {{
    background: #F3F4F6;
    border: none;
    border-radius: 8px;
    height: 10px;
    text-align: center;
    color: {TEXT_MUTED};
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {PRIMARY}, stop:1 #8B97FF);
    border-radius: 8px;
}}

/* ====== Tabs ====== */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background: {BG_CARD};
    top: -1px;
}}
QTabBar::tab {{
    padding: 8px 16px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    color: {TEXT_MUTED};
    background: transparent;
}}
QTabBar::tab:selected {{
    background: {BG_CARD};
    color: {PRIMARY};
    border: 1px solid {BORDER};
    border-bottom-color: {BG_CARD};
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {PRIMARY}; }}

/* ====== Sliders ====== */
QSlider::groove:horizontal {{
    height: 4px;
    background: #E5E7EB;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {PRIMARY};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::sub-page:horizontal {{
    background: {PRIMARY};
    border-radius: 2px;
}}

/* ====== ToolTip ====== */
QToolTip {{
    background: {TEXT};
    color: white;
    border: none;
    padding: 6px 10px;
    border-radius: 6px;
}}
"""


def apply_global_style(app) -> None:
    app.setStyleSheet(GLOBAL_QSS)
