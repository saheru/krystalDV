"""Small UI helpers — used inline by pages, no separate widget library."""
from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from kdv.ui import style


def make_card(parent: QWidget | None = None, *, padding: int = 18, shadow: bool = False) -> QFrame:
    """A rounded white container with hover-lift via QSS only.

    Drop-shadow is intentionally NOT added — Qt allows only one graphics
    effect per widget, and a future fade animation on a parent would silently
    clobber it. The QSS rule on `QWidget[card="true"]` already provides a
    clean border + hover state.
    `shadow=` is kept for backward compatibility but is a no-op.
    """
    f = QFrame(parent)
    f.setProperty("card", True)
    f.setAttribute(Qt.WA_StyledBackground, True)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(padding, padding, padding, padding)
    lay.setSpacing(12)
    return f


def heading(text: str, *, level: int = 1) -> QLabel:
    lbl = QLabel(text)
    if level == 1:
        lbl.setProperty("h1", True)
        lbl.setMinimumHeight(36)
    elif level == 2:
        lbl.setProperty("h2", True)
        lbl.setMinimumHeight(28)
    else:
        lbl.setProperty("h3", True)
        lbl.setMinimumHeight(22)
    lbl.setContentsMargins(0, 2, 0, 2)
    return lbl


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("muted", True)
    return lbl


def badge(text: str, kind: Literal["success", "warning", "danger", "info", "muted"] = "muted") -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("badge", kind)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return lbl


def primary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("primary", True)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(36)
    return b


def ghost_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("ghost", True)
    b.setCursor(Qt.PointingHandCursor)
    return b


def danger_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("danger", True)
    b.setCursor(Qt.PointingHandCursor)
    return b


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {style.BORDER}; background: {style.BORDER}; border: none; max-height: 1px;")
    return f


def empty_state(title: str, subtitle: str = "", action_text: str = "", on_action=None) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setAlignment(Qt.AlignCenter)
    lay.setSpacing(14)

    icon = QLabel("◇")
    icon.setStyleSheet(f"color: {style.PRIMARY}; font-size: 56px;")
    icon.setAlignment(Qt.AlignCenter)
    lay.addWidget(icon)

    h = heading(title, level=2)
    h.setAlignment(Qt.AlignCenter)
    lay.addWidget(h)

    if subtitle:
        s = muted(subtitle)
        s.setAlignment(Qt.AlignCenter)
        s.setWordWrap(True)
        lay.addWidget(s)

    if action_text and on_action:
        btn = primary_button(action_text)
        btn.clicked.connect(on_action)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)

    return w


# -------- Toast ---------------------------------------------------------
class Toast(QWidget):
    """Self-dismissing top-right notification."""

    def __init__(
        self,
        parent: QWidget,
        text: str,
        kind: Literal["success", "warning", "danger", "info"] = "info",
        *,
        duration_ms: int = 2400,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowFlags(Qt.SubWindow | Qt.FramelessWindowHint)

        bg, fg = {
            "success": ("#ECFDF5", style.SUCCESS),
            "warning": ("#FFFBEB", style.WARNING),
            "danger": ("#FEF2F2", style.DANGER),
            "info": ("#EFF6FF", style.INFO),
        }[kind]
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {fg}; "
            "border-radius: 10px; padding: 10px 14px; font-weight: 500;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lbl = QLabel(text, self)
        self._lbl.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self._lbl)
        self.adjustSize()

        # position top-right of parent
        margin = 24
        self.move(parent.width() - self.width() - margin, margin)
        self.show()

        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        self._fi = QPropertyAnimation(eff, b"opacity", self)
        self._fi.setDuration(180)
        self._fi.setStartValue(0.0)
        self._fi.setEndValue(1.0)
        self._fi.setEasingCurve(QEasingCurve.OutCubic)
        self._fi.start()

        QTimer.singleShot(duration_ms, self._dismiss)

    def _dismiss(self) -> None:
        eff = self.graphicsEffect()
        if not eff:
            self.deleteLater()
            return
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.deleteLater)
        anim.start()


def toast(parent: QWidget, text: str, kind: Literal["success", "warning", "danger", "info"] = "info") -> None:
    Toast(parent, text, kind)
