"""Shared helpers for the ``widgets`` package.

Importing this module also makes the repo root importable so ``import theme``
resolves whether a widget module is imported normally or run directly as
``__main__``.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from ui import theme  # after the sys.path fix above
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor
from util.aw_safe import swallowed

__all__ = [
    "theme", "lerp_color", "with_alpha", "run_demo",
    "install_qss_refresh", "set_variant",
]


def set_variant(widget, variant: str) -> None:
    """Change *widget*'s ``variant`` dynamic property at runtime and force a
    QSS repolish, so a property-selector rule (``QPushButton[variant=...]``)
    picks up the new value immediately.

    Changing ``setProperty("variant", ...)`` alone does nothing visible —
    Qt's stylesheet cache only re-evaluates property selectors on an
    explicit repolish. ``review.core.plot_toggles.refresh_fov_btn_state``
    is the precedent this generalises (a QPushButton's checked-state variant
    flips live the same way); kept here rather than duplicated per call site.
    """
    widget.setProperty("variant", variant)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def lerp_color(a, b, t: float) -> QColor:
    """Linear-interpolate between two colors (any QColor-constructible input)."""
    a, b = QColor(a), QColor(b)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return QColor(
        round(a.red()   + (b.red()   - a.red())   * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue()  + (b.blue()  - a.blue())  * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def with_alpha(color, alpha: float) -> QColor:
    """Return *color* with its alpha set to *alpha* (0.0–1.0)."""
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


class _QssRefreshFilter(QObject):
    """Re-applies a widget's QSS when Qt reports a style change.

    An event filter must subclass QObject — Qt rejects plain Python classes
    passed to ``installEventFilter`` with a TypeError. Declared at module scope
    with the QSS factory held on the instance, rather than inside
    :func:`install_qss_refresh` closing over it: see
    ``tests/test_qt_subclasses_live_at_module_scope.py``.

    Two safety guards on top of the basic filter:

    1. Skip non-QEvent dispatches (PySide6 occasionally routes a
       QStandardItem from a QCompleter through eventFilter).
    2. Skip when ``setStyleSheet`` would set the same string the
       widget already carries. Without this guard, the filter's
       own ``setStyleSheet`` call fires *another* StyleChange,
       which re-enters this filter, which re-applies the QSS, etc.
       Qt rebuilds the entire QSS-driven style on every cycle —
       load-time stalls of ~20s observed before the guard.
    """

    def __init__(self, qss_factory, parent) -> None:
        super().__init__(parent)
        self._qss_factory = qss_factory

    def eventFilter(self, obj, event):  # Qt naming
        if not isinstance(event, QEvent):
            return False
        if event.type() == QEvent.StyleChange:
            try:
                new = self._qss_factory()
                current = obj.styleSheet()
                if new != current:
                    obj.setStyleSheet(new)
            except Exception:
                swallowed()
        return False


def install_qss_refresh(widget, qss_factory) -> None:
    """Wire *widget* to refresh its per-instance stylesheet when the
    application theme changes.

    Widgets in ``widgets/`` set their own QSS in ``__init__`` via
    ``self.setStyleSheet(self._qss())`` — that's load-bearing for
    per-widget style scoping, but it freezes the colour tokens at
    construction time. When a runtime theme switcher rebuilds
    ``QApplication.setStyleSheet(...)``, Qt fires ``QEvent.StyleChange``
    on each widget; this helper installs an event filter that catches
    that event and calls ``widget.setStyleSheet(qss_factory())`` so the
    cached QSS picks up the new tokens.

    Idempotent: calling it twice on the same widget installs only one
    filter (the second is a no-op).
    """
    if getattr(widget, "_qss_refresh_filter_installed", False):
        return

    # Parent the filter to the widget so it lives exactly as long as
    # the widget does (and Qt cleans it up automatically). Also assign
    # back to an attribute so the idempotency check has something to
    # find on a second call.
    filt = _QssRefreshFilter(qss_factory, widget)
    widget.installEventFilter(filt)
    widget._qss_refresh_filter = filt
    widget._qss_refresh_filter_installed = True


def run_demo(factory, title: str, *, size=(380, 320)) -> None:
    """Open a small standalone window hosting ``factory()`` with the v2 theme."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())
    w = factory()
    w.setWindowTitle(title)
    if size:
        w.resize(*size)
    w.show()
    app.exec()
