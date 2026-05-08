"""Reusable QPropertyAnimation helpers.

IMPORTANT: QGraphicsOpacityEffect attached to a widget can interfere with
keyboard input / IME / focus on its child input widgets. So every fade
animation here REMOVES the effect after completion.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QTimer,
    Qt,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


def _clear_effect_later(widget: QWidget) -> None:
    """Detach any QGraphicsEffect on the next event-loop tick."""
    QTimer.singleShot(0, lambda: widget and widget.setGraphicsEffect(None))


def fade_in(widget: QWidget, *, duration_ms: int = 220) -> QPropertyAnimation:
    """Fade in via a temporary QGraphicsOpacityEffect that is detached on finish."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(lambda w=widget: _clear_effect_later(w))
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    return anim


def slide_in(
    widget: QWidget, *, direction: str = "right", offset_px: int = 24, duration_ms: int = 260
) -> QParallelAnimationGroup:
    """Slide + fade. Effect removed after finish so child inputs stay responsive."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)

    start_geo = widget.geometry()
    dx = dy = 0
    if direction == "right":
        dx = offset_px
    elif direction == "left":
        dx = -offset_px
    elif direction == "down":
        dy = offset_px
    elif direction == "up":
        dy = -offset_px
    widget.setGeometry(start_geo.x() + dx, start_geo.y() + dy, start_geo.width(), start_geo.height())

    geo_anim = QPropertyAnimation(widget, b"geometry", widget)
    geo_anim.setDuration(duration_ms)
    geo_anim.setStartValue(widget.geometry())
    geo_anim.setEndValue(start_geo)
    geo_anim.setEasingCurve(QEasingCurve.OutCubic)

    op_anim = QPropertyAnimation(effect, b"opacity", widget)
    op_anim.setDuration(duration_ms)
    op_anim.setStartValue(0.0)
    op_anim.setEndValue(1.0)
    op_anim.setEasingCurve(QEasingCurve.OutCubic)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(geo_anim)
    group.addAnimation(op_anim)
    group.finished.connect(lambda w=widget: _clear_effect_later(w))
    group.start(QParallelAnimationGroup.DeleteWhenStopped)
    return group


def pulse(widget: QWidget, *, duration_ms: int = 220) -> QPropertyAnimation:
    """Brief opacity pulse — used for quick feedback after click."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(1.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms)
    anim.setKeyValueAt(0, 1.0)
    anim.setKeyValueAt(0.5, 0.55)
    anim.setKeyValueAt(1, 1.0)
    anim.setEasingCurve(QEasingCurve.InOutSine)
    anim.finished.connect(lambda w=widget: _clear_effect_later(w))
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    return anim


# Backward-compat shim (unused now).
def schedule_clear_effect(widget: QWidget, delay_ms: int = 400) -> None:
    QTimer.singleShot(delay_ms, lambda: widget.setGraphicsEffect(None))
