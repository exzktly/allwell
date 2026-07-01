"""WindowResizeGrips — edge/corner resize affordances for a frameless window.

A helper object (not a widget you place in a layout) that installs 8 invisible
~6–8 px grip widgets — 4 edges + 4 corners — over a top-level frameless window,
sets the right resize cursors, and on press resizes the window. Two modes:

* ``"system"`` — calls ``window.windowHandle().startSystemResize(edge)`` (OS-driven;
  gets snapping/animation). Reliable on Windows/Wayland; on macOS Cocoa it has a
  history of being a no-op for frameless windows.
* ``"manual"`` — the grips compute the new ``window.geometry()`` from the drag
  delta themselves (always works; no OS snap).
* ``"auto"`` (default) — ``"manual"`` on macOS, ``"system"`` elsewhere.

Usage::

    self._grips = WindowResizeGrips(self, mode="auto", margin=8)   # self = QMainWindow/QWidget top-level
    # ... later, if needed:  self._grips.detach()

The grips reposition themselves whenever the window resizes, and stay on top of
the window's other children (so the very edges resize even though a custom
titlebar sits just inside).
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

import theme  # noqa: E402,F401  (wires the repo-root import path)

# (edge-flag, cursor) for the 4 edges + 4 corners.
_EDGES: list[tuple[Qt.Edges, Qt.CursorShape]] = [
    (Qt.TopEdge,                    Qt.SizeVerCursor),
    (Qt.BottomEdge,                 Qt.SizeVerCursor),
    (Qt.LeftEdge,                   Qt.SizeHorCursor),
    (Qt.RightEdge,                  Qt.SizeHorCursor),
    (Qt.TopEdge | Qt.LeftEdge,      Qt.SizeFDiagCursor),
    (Qt.BottomEdge | Qt.RightEdge,  Qt.SizeFDiagCursor),
    (Qt.TopEdge | Qt.RightEdge,     Qt.SizeBDiagCursor),
    (Qt.BottomEdge | Qt.LeftEdge,   Qt.SizeBDiagCursor),
]


class _Grip(QWidget):
    def __init__(self, window: QWidget, edges: Qt.Edges, cursor: Qt.CursorShape,
                 mode: str) -> None:
        super().__init__(window)
        self._window = window
        self._edges = edges
        self._mode = mode  # "system" | "manual"
        self.setCursor(cursor)
        self.setMouseTracking(True)
        # Transparent: no paint; just an event-catching strip.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._press_global: QPoint | None = None
        self._orig_geo: QRect | None = None

    # ── manual-resize math ───────────────────────────────────────────────
    def _resize_to(self, delta: QPoint) -> None:
        if self._orig_geo is None:
            return
        win = self._window
        g = QRect(self._orig_geo)
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        mn = win.minimumSize()
        mw = max(mn.width(), 1)
        mh = max(mn.height(), 1)
        if self._edges & Qt.LeftEdge:
            new_w = max(mw, w - delta.x())
            x += (w - new_w)
            w = new_w
        if self._edges & Qt.RightEdge:
            w = max(mw, w + delta.x())
        if self._edges & Qt.TopEdge:
            new_h = max(mh, h - delta.y())
            y += (h - new_h)
            h = new_h
        if self._edges & Qt.BottomEdge:
            h = max(mh, h + delta.y())
        win.setGeometry(x, y, w, h)

    # ── events ───────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        if self._mode == "system":
            handle = self._window.windowHandle()
            if handle is not None and hasattr(handle, "startSystemResize"):
                handle.startSystemResize(self._edges)
                event.accept()
                return
            # fall through to manual if no handle / no API
        self._press_global = event.globalPosition().toPoint()
        self._orig_geo = QRect(self._window.geometry())
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is not None and (event.buttons() & Qt.LeftButton):
            self._resize_to(event.globalPosition().toPoint() - self._press_global)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_global = None
        self._orig_geo = None
        super().mouseReleaseEvent(event)


def _resolve_mode(mode: str) -> str:
    if mode in ("system", "manual"):
        return mode
    # "auto"
    return "manual" if _sys.platform == "darwin" else "system"


class WindowResizeGrips(QObject):
    def __init__(self, window: QWidget | None = None, *, mode: str = "auto",
                 margin: int = 8) -> None:
        super().__init__(window)
        self._mode = _resolve_mode(mode)
        self._margin = max(2, int(margin))
        self._grips: list[_Grip] = []
        self._window: QWidget | None = None
        if window is not None:
            self.attach(window)

    # ── API ──────────────────────────────────────────────────────────────
    def attach(self, window: QWidget) -> None:
        self.detach()
        self._window = window
        for edges, cursor in _EDGES:
            self._grips.append(_Grip(window, edges, cursor, self._mode))
        window.installEventFilter(self)
        self._reposition()
        for g in self._grips:
            g.show()
            g.raise_()

    def detach(self) -> None:
        if self._window is not None:
            self._window.removeEventFilter(self)
        for g in self._grips:
            g.setParent(None)
            g.deleteLater()
        self._grips = []
        self._window = None

    def setMode(self, mode: str) -> None:
        self._mode = _resolve_mode(mode)
        for g in self._grips:
            g._mode = self._mode

    def mode(self) -> str:
        return self._mode

    # ── internals ────────────────────────────────────────────────────────
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._window and event.type() in (
            QEvent.Resize, QEvent.Show, QEvent.WindowStateChange,
        ):
            self._reposition()
        return super().eventFilter(obj, event)

    def _reposition(self) -> None:
        win = self._window
        if win is None or not self._grips:
            return
        m = self._margin
        w, h = win.width(), win.height()
        # Disable the grips while maximized/fullscreen (resizing then is moot).
        maximized = bool(win.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
        # Geometry per the order in _EDGES: top, bottom, left, right, TL, BR, TR, BL.
        rects = [
            QRect(m, 0, max(0, w - 2 * m), m),                # top
            QRect(m, h - m, max(0, w - 2 * m), m),            # bottom
            QRect(0, m, m, max(0, h - 2 * m)),                # left
            QRect(w - m, m, m, max(0, h - 2 * m)),            # right
            QRect(0, 0, m, m),                                # top-left
            QRect(w - m, h - m, m, m),                        # bottom-right
            QRect(w - m, 0, m, m),                            # top-right
            QRect(0, h - m, m, m),                            # bottom-left
        ]
        # Edges first, then corners on top (last raised = topmost).
        order = [0, 1, 2, 3, 4, 5, 6, 7]
        for i in order:
            g = self._grips[i]
            g.setGeometry(rects[i])
            g.setVisible(not maximized)
            g.raise_()


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout,
        QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    win = QMainWindow()
    win.setWindowTitle("WindowResizeGrips — demo")
    win.setWindowFlag(Qt.FramelessWindowHint, True)
    win.setMinimumSize(280, 180)

    central = _QW()
    v = QVBoxLayout(central)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)

    # fake titlebar (drag to move)
    bar = _QW()
    bar.setObjectName("Sidebar")
    bar.setAttribute(Qt.WA_StyledBackground, True)
    bl = QHBoxLayout(bar)
    bl.setContentsMargins(theme.Spacing.md, theme.Spacing.sm, theme.Spacing.sm, theme.Spacing.sm)
    bl.addWidget(QLabel("Frameless test window — drag the title bar to move; drag the edges to resize"))
    bl.addStretch(1)
    close_b = QPushButton("✕")
    close_b.clicked.connect(win.close)
    bl.addWidget(close_b)

    _drag = {"o": None}
    def _press(e):
        if e.button() == Qt.LeftButton:
            h = win.windowHandle()
            if h is not None:
                h.startSystemMove()
            else:
                _drag["o"] = e.globalPosition().toPoint() - win.frameGeometry().topLeft()
    def _move(e):
        if _drag["o"] is not None and (e.buttons() & Qt.LeftButton):
            win.move(e.globalPosition().toPoint() - _drag["o"])
    def _rel(e):
        _drag["o"] = None
    bar.mousePressEvent = _press
    bar.mouseMoveEvent = _move
    bar.mouseReleaseEvent = _rel
    v.addWidget(bar)

    body = QVBoxLayout()
    pad = theme.Spacing.lg
    body_w = _QW()
    body_w.setLayout(body)
    body.setContentsMargins(pad, pad, pad, pad)
    body.setSpacing(theme.Spacing.md)
    info = QLabel("Hover near the 4 edges / 4 corners — the cursor changes; drag to resize.")
    info.setWordWrap(True)
    body.addWidget(info)
    mode_b = QPushButton("mode: " + ("manual" if _sys.platform == "darwin" else "system") + " — click to toggle")
    body.addWidget(mode_b)
    body.addStretch(1)
    v.addWidget(body_w, 1)

    win.setCentralWidget(central)
    grips = WindowResizeGrips(win, mode="auto", margin=8)

    def _toggle_mode():
        new = "system" if grips.mode() == "manual" else "manual"
        grips.setMode(new)
        mode_b.setText(f"mode: {new} — click to toggle")
    mode_b.clicked.connect(_toggle_mode)

    win.resize(440, 280)
    win.show()
    _sys.exit(app.exec())
