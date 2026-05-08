"""Reusable QPropertyAnimation helpers.

IMPORTANT: QGraphicsOpacityEffect attached to a widget can interfere with
keyboard input / IME / focus on its child input widgets, AND it can only
be attached to one widget at a time — adding it to a parent silently
removes it from a child (or replaces a drop-shadow). So every fade
helper here REMOVES the effect after completion, and we prefer
geometry / size animations (no graphics effect at all) for new code.
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


# Hard-coded sentinel that "no max" means in Qt
_HUGE = 16_777_215


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


# =====================================================================
# Effect-free animations (preferred for inline widgets)
# =====================================================================

def reveal_height(widget: QWidget, *, duration_ms: int = 280) -> QPropertyAnimation:
    """Animate widget from height 0 → its natural height. No graphics effect.

    Captures the widget's layout-natural height, sets maxHeight to 0, then
    animates back to that height. After finish, restores maxHeight to "no max"
    so resizing later still works.

    Use this in place of `fade_in` for content that is being inserted into
    a layout. It works on inputs, comboboxes, charts — anything.
    """
    widget.show()
    target = widget.sizeHint().height()
    if target <= 0:
        target = max(widget.height(), 60)
    widget.setMaximumHeight(0)
    anim = QPropertyAnimation(widget, b"maximumHeight", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(0)
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _release() -> None:
        if widget:
            widget.setMaximumHeight(_HUGE)

    anim.finished.connect(_release)
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    return anim


def stagger_reveal(widgets: list[QWidget], *, step_ms: int = 60, duration_ms: int = 240) -> None:
    """Reveal a list of widgets one after another, each delayed by `step_ms`."""
    for i, w in enumerate(widgets):
        QTimer.singleShot(i * step_ms, lambda w=w: reveal_height(w, duration_ms=duration_ms))


def slide_window(widget: QWidget, *, dx: int = 0, dy: int = -16, duration_ms: int = 240) -> QPropertyAnimation:
    """Animate a widget's window position (only useful for top-level / popup widgets)."""
    g = widget.geometry()
    end_geo = g
    start_geo = g.translated(dx, dy)
    widget.setGeometry(start_geo)
    anim = QPropertyAnimation(widget, b"geometry", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(start_geo)
    anim.setEndValue(end_geo)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    return anim


def animate_int_value(widget: QWidget, prop: bytes, start: int, end: int, *, duration_ms: int = 500) -> QPropertyAnimation:
    """Animate an integer Qt property (e.g. progressBar.value) smoothly."""
    anim = QPropertyAnimation(widget, prop, widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(int(start))
    anim.setEndValue(int(end))
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    return anim


def count_up_label(label, *, start: float, end: float, duration_ms: int = 700, fmt: str = "{:.0f}") -> None:
    """Animate a QLabel's text from `start` to `end` over `duration_ms`.

    Uses QVariantAnimation so we don't depend on a Qt property on the label.
    """
    from PySide6.QtCore import QVariantAnimation

    anim = QVariantAnimation(label)
    anim.setDuration(duration_ms)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.valueChanged.connect(lambda v: label.setText(fmt.format(float(v))))
    anim.start(QVariantAnimation.DeleteWhenStopped)


def shake(widget: QWidget, *, amplitude: int = 6, duration_ms: int = 320) -> None:
    """Quick horizontal shake — used for invalid-input feedback."""
    g = widget.geometry()
    anim = QPropertyAnimation(widget, b"pos", widget)
    anim.setDuration(duration_ms)
    p = g.topLeft()
    a = amplitude
    anim.setKeyValueAt(0.0, p)
    anim.setKeyValueAt(0.2, p + _Pt(-a, 0))
    anim.setKeyValueAt(0.4, p + _Pt(a, 0))
    anim.setKeyValueAt(0.6, p + _Pt(-a // 2, 0))
    anim.setKeyValueAt(0.8, p + _Pt(a // 2, 0))
    anim.setKeyValueAt(1.0, p)
    anim.setEasingCurve(QEasingCurve.InOutSine)
    anim.start(QPropertyAnimation.DeleteWhenStopped)


def _Pt(x: int, y: int):
    from PySide6.QtCore import QPoint

    return QPoint(x, y)
