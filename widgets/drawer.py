"""Drawer — an edge slide-in overlay panel (right edge or bottom edge).

A child widget of some host (usually the main window) that animates in from an
edge, over a dim backdrop, and dismisses on outside-click or ``Esc``. Hosts an
arbitrary content widget.

``edge="right"`` (default) slides a vertical panel in from the right and is sized
by ``width_hint`` / ``width_fraction``. ``edge="bottom"`` slides a full-width
horizontal panel up from the bottom and is sized by ``height_hint`` /
``height_fraction`` — use it for wide, log-style content.

API
---
* ``Drawer(host, parent=None, *, edge="right", width_hint=None,
  width_fraction=0.34, height_hint=None, height_fraction=0.4)``
* ``setContentWidget(widget)`` / ``contentWidget()``
* ``open()`` / ``close()`` / ``toggle()`` / ``isOpen()``
* ``opened`` / ``closed`` signals.

Sizes are logical-pixel / fraction based (DPI-aware via the host's logical
geometry); colours from ``theme`` tokens.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPropertyAnimation, QRect, Qt, Signal,
)
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui import theme
from widgets._support import with_alpha


class _Backdrop(QWidget):
    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("DrawerBackdrop")
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setVisible(False)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        # 0.35 alpha matches theme.Colors.drop_shadow_md ("rgba(0,0,0,0.35)")
        # — keep the literal here because Drawer paints a solid backdrop
        # rather than reading a CSS rgba string. The numeric value comes
        # from the same design-token decision.
        p.fillRect(self.rect(), with_alpha(theme.Colors.ink_dark, 0.35))

    def mousePressEvent(self, _event) -> None:
        self.clicked.emit()


class Drawer(QWidget):
    opened = Signal()
    closed = Signal()

    def __init__(self, host: QWidget, parent: QWidget | None = None, *,
                 edge: str = "right",
                 width_hint: int | None = None, width_fraction: float = 0.34,
                 height_hint: int | None = None, height_fraction: float = 0.4) -> None:
        super().__init__(parent or host)
        self.setObjectName("Drawer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._host = host
        self._edge = edge if edge in ("right", "bottom") else "right"
        self._width_hint = int(width_hint) if width_hint else None
        self._width_fraction = float(width_fraction)
        self._height_hint = int(height_hint) if height_hint else None
        self._height_fraction = float(height_fraction)
        self._open = False

        self._backdrop = _Backdrop(host)
        self._backdrop.clicked.connect(self.close)

        self._layout = QVBoxLayout(self)
        m = theme.Spacing.lg
        self._layout.setContentsMargins(m, m, m, m)
        self._layout.setSpacing(theme.Spacing.md)
        self._content: QWidget | None = None

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)

        host.installEventFilter(self)
        self.setStyleSheet(self._build_qss())
        self.hide()

    # ── API ──────────────────────────────────────────────────────────────
    def setContentWidget(self, widget: QWidget) -> None:
        if self._content is not None:
            self._layout.removeWidget(self._content)
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = widget
        if widget is not None:
            self._layout.addWidget(widget)

    def contentWidget(self) -> QWidget | None:
        return self._content

    def isOpen(self) -> bool:
        return self._open

    def open(self) -> None:  # mirrors common drawer API
        if self._open:
            return
        self._open = True
        self._sync_backdrop_geom()
        self._backdrop.setVisible(True)
        self._backdrop.raise_()
        start, end = self._closed_rect(), self._open_rect()
        self.setGeometry(start)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()
        self.opened.emit()

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(self._closed_rect())
        self._anim.start()
        self.closed.emit()

    def toggle(self) -> None:
        self.close() if self._open else self.open()

    # ── internals ────────────────────────────────────────────────────────
    def _drawer_width(self) -> int:
        if self._width_hint:
            return min(self._width_hint, self._host.width())
        return max(280, round(self._host.width() * self._width_fraction))

    def _drawer_height(self) -> int:
        if self._height_hint:
            return min(self._height_hint, self._host.height())
        return max(200, round(self._host.height() * self._height_fraction))

    def _open_rect(self) -> QRect:
        """On-screen geometry when fully open, per edge."""
        if self._edge == "bottom":
            h = self._drawer_height()
            return QRect(0, self._host.height() - h, self._host.width(), h)
        w = self._drawer_width()
        return QRect(self._host.width() - w, 0, w, self._host.height())

    def _closed_rect(self) -> QRect:
        """Off-screen (dismissed) geometry the panel animates to/from, per edge."""
        if self._edge == "bottom":
            h = self._drawer_height()
            return QRect(0, self._host.height(), self._host.width(), h)
        w = self._drawer_width()
        return QRect(self._host.width(), 0, w, self._host.height())

    def _sync_backdrop_geom(self) -> None:
        self._backdrop.setGeometry(0, 0, self._host.width(), self._host.height())

    def _on_anim_finished(self) -> None:
        if not self._open:
            self.hide()
            self._backdrop.setVisible(False)

    def eventFilter(self, obj, event) -> bool:
        # PySide6 occasionally dispatches a non-QEvent through eventFilter
        # (observed: a QStandardItem from a QCompleter popup routed in place of
        # the QEvent). Bail before any ``event.type()`` access — and before
        # ``super().eventFilter`` rejects the non-QObject — so the whole window
        # survives those edge cases instead of TypeError-ing.
        if not isinstance(event, QEvent):
            return False
        if obj is self._host:
            if event.type() == QEvent.Resize and self.isVisible():
                self.setGeometry(self._open_rect() if self._open else self._closed_rect())
                self._sync_backdrop_geom()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _build_qss(self) -> str:
        c, r = theme.Colors, theme.Radii
        if self._edge == "bottom":
            frame = (f"border-top: 1px solid {c.border_subtle};"
                     f" border-top-left-radius: {r.lg}px;"
                     f" border-top-right-radius: {r.lg}px;")
        else:
            frame = (f"border-left: 1px solid {c.border_subtle};"
                     f" border-top-left-radius: {r.lg}px;"
                     f" border-bottom-left-radius: {r.lg}px;")
        return f"""
        #Drawer {{
            background-color: {c.rail};
            {frame}
        }}
        #DrawerBackdrop {{ background: transparent; }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout,
        QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    host = _QW()
    host.setWindowTitle("Drawer — demo")
    hv = QVBoxLayout(host)
    pad = theme.Spacing.lg
    hv.setContentsMargins(pad, pad, pad, pad)
    title = QLabel("Drawer — click 'Open Analyze' (Esc / click-outside to dismiss)")
    title.setObjectName("Title")
    title.setWordWrap(True)
    hv.addWidget(title)
    open_btn = QPushButton("Open Analyze")
    open_btn.setObjectName("Primary")
    hv.addWidget(open_btn, 0, Qt.AlignLeft)
    hv.addStretch(1)

    drawer = Drawer(host, width_fraction=0.4)
    content = _QW()
    cv = QVBoxLayout(content)
    cv.setContentsMargins(0, 0, 0, 0)
    cv.setSpacing(theme.Spacing.md)
    h2 = QLabel("Analyze")
    h2.setObjectName("Title")
    cv.addWidget(h2)
    cv.addWidget(QLabel("Pipeline input directory:"))
    cv.addWidget(QLineEdit())
    cv.addWidget(QPushButton("Run pipeline"))
    cv.addStretch(1)
    drawer.setContentWidget(content)
    open_btn.clicked.connect(drawer.open)

    host.resize(640, 420)
    host.show()
    _sys.exit(app.exec())
