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

import theme  # noqa: E402  (after the sys.path fix above)
from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

__all__ = [
    "theme", "lerp_color", "with_alpha", "run_demo",
    "install_qss_refresh",
]


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

    class _Filter(QObject):
        """Event filter must subclass QObject — Qt rejects plain Python
        classes passed to ``installEventFilter`` with a TypeError.

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

        def eventFilter(self, obj, event):  # noqa: N802 — Qt naming
            if not isinstance(event, QEvent):
                return False
            if event.type() == QEvent.StyleChange:
                try:
                    new = qss_factory()
                    current = obj.styleSheet()
                    if new != current:
                        obj.setStyleSheet(new)
                except Exception:
                    pass
            return False

    # Parent the filter to the widget so it lives exactly as long as
    # the widget does (and Qt cleans it up automatically). Also assign
    # back to an attribute so the idempotency check has something to
    # find on a second call.
    filt = _Filter(widget)
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
