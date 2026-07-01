"""Toast — a small, auto-dismissing floating notification.

A frameless top-level popup (status dot + message) that fades in near the
bottom-right of a parent window, lingers, then fades out. Token-styled with a
soft drop shadow.

Usage
-----
::

    Toast.show_message(parent_window, "Saved", kind="success")
    Toast.show_message(parent_window, "Export failed", kind="danger", msec=6000)

Lower-level: construct a ``Toast(parent, text, kind)`` and call ``popup()``.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import (  # noqa: E402
    QEasingCurve, QPointF, QPropertyAnimation, QTimer, Qt,
)
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QWidget,
)

import theme  # noqa: E402
from widgets.status_dot import StatusDot  # noqa: E402


class Toast(QWidget):
    def __init__(self, parent: QWidget | None, text: str, *,
                 kind: str = "success", msec: int = 4000) -> None:
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._host = parent
        self._msec = max(800, int(msec))

        card = QWidget(self)
        card.setObjectName("ToastCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        m = theme.Spacing.md
        outer = QHBoxLayout(self)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)  # room for the shadow
        outer.addWidget(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(m, theme.Spacing.sm, m + theme.Spacing.xs, theme.Spacing.sm)
        row.setSpacing(theme.Spacing.sm)
        self._dot = StatusDot(kind, diameter=max(8, round(self.fontMetrics().height() * 0.5)))
        self._label = QLabel(text, card)
        self._label.setObjectName("ToastLabel")
        row.addWidget(self._dot)
        row.addWidget(self._label)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(theme.Spacing.md)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, round(0.35 * 255)))
        card.setGraphicsEffect(shadow)

        self.setStyleSheet(self._build_qss())
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.InOutCubic)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._fade_out)

    # ── public ───────────────────────────────────────────────────────────
    def popup(self) -> None:
        self.adjustSize()
        self._place()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setDuration(140)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._dismiss_timer.start(self._msec)

    @classmethod
    def show_message(cls, parent: QWidget | None, text: str, *,
                     kind: str = "success", msec: int = 4000) -> "Toast":
        app_qss = ""
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            app_qss = app.styleSheet() if app else ""
        except Exception:
            pass
        t = cls(parent, text, kind=kind, msec=msec)
        if app_qss and not parent:
            # Top-level with no styled parent: make sure tokens still apply.
            t.setStyleSheet(app_qss + t.styleSheet())
        t.popup()
        return t

    # ── internals ────────────────────────────────────────────────────────
    def _place(self) -> None:
        margin = theme.Spacing.lg
        if self._host is not None and self._host.isVisible():
            geo = self._host.geometry()
            bottom_right = self._host.mapToGlobal(
                QPointF(geo.width(), geo.height()).toPoint()
            )
            x = bottom_right.x() - self.width() - margin
            y = bottom_right.y() - self.height() - margin
        else:
            from PySide6.QtGui import QGuiApplication
            scr = QGuiApplication.primaryScreen().availableGeometry()
            x = scr.right() - self.width() - margin
            y = scr.bottom() - self.height() - margin
        self.move(int(x), int(y))

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setDuration(220)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        # Connect via UniqueConnection so re-invocations don't accumulate
        # duplicate close-on-finished slots, and no disconnect is needed
        # (which would emit a non-fatal RuntimeWarning on first call when
        # nothing is connected yet).
        try:
            self._fade.finished.connect(self.close, Qt.UniqueConnection)
        except (RuntimeError, TypeError):
            # Already connected — Qt raises on duplicate UniqueConnection.
            pass
        self._fade.start()

    def mousePressEvent(self, _event) -> None:  # noqa: N802
        self._dismiss_timer.stop()
        self._fade_out()

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        #Toast {{ background: transparent; }}
        #ToastCard {{
            background-color: {c.panel};
            border: 1px solid {c.border};
            border-radius: {r.lg}px;
        }}
        QLabel#ToastLabel {{
            color: {c.text_primary};
            font-size: {t.body_size}px;
            background: transparent;
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    host = _QW()
    host.setWindowTitle("Toast — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(host)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)
    title = QLabel("Toast")
    title.setObjectName("Title")
    lay.addWidget(title)
    row = QHBoxLayout()
    b_ok = QPushButton("Success")
    b_ok.setObjectName("Primary")
    b_warn = QPushButton("Warning")
    b_err = QPushButton("Error")
    b_err.setObjectName("Danger")
    row.addWidget(b_ok)
    row.addWidget(b_warn)
    row.addWidget(b_err)
    row.addStretch(1)
    lay.addLayout(row)
    lay.addStretch(1)

    b_ok.clicked.connect(lambda: Toast.show_message(host, "Saved layout.awd", kind="success"))
    b_warn.clicked.connect(lambda: Toast.show_message(host, "Some wells had no data", kind="warn", msec=5000))
    b_err.clicked.connect(lambda: Toast.show_message(host, "Export failed: disk full", kind="danger", msec=6000))

    host.resize(480, 280)
    host.show()
    _sys.exit(app.exec())
