"""Popover — an anchor-relative floating panel.

A frameless `Qt.Popup` top-level that positions itself next to an *anchor*
widget (below / above / left / right, auto-flipping if it would be clipped by
the screen), dismisses on outside-click or `Esc` (free with `Qt.Popup`), and
hosts an arbitrary content widget inside a token-styled card with a soft drop
shadow.

Use it for: the figure-header "Stats · SEM" chip popover, the `LutSelector`
list, the titlebar theme-switcher, kebab menus, etc. (Distinct from `Drawer`,
which is edge-docked, not anchor-relative.)

API
---
* ``Popover(parent=None)`` — usually pass the host window so it composites above
  it; the window flags make it a top-level regardless.
* ``setContentWidget(widget)`` / ``contentWidget()``
* ``popup(anchor, side="bottom", align="start", gap=None)`` — show, positioned
  relative to *anchor* (a ``QWidget``). ``side`` ∈ {"bottom","top","left","right"};
  ``align`` ∈ {"start","center","end"} along the perpendicular axis. The chosen
  ``side`` is auto-flipped to the opposite if the popover would overflow the
  anchor's screen; the final position is clamped onto the screen.
* ``close()`` / ``isOpen()``
* ``opened`` / ``closed`` signals.

Sizing/paddings come from ``theme`` tokens (DPI-friendly); the visible card is a
``QFrame#PopoverFrame`` inset from the popover edges by the shadow blur.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame, QGraphicsDropShadowEffect, QVBoxLayout, QWidget,
)

import theme  # noqa: E402


class Popover(QWidget):
    opened = Signal()
    closed = Signal()

    _SIDES = ("bottom", "top", "left", "right")
    _OPPOSITE = {"bottom": "top", "top": "bottom", "left": "right", "right": "left"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setObjectName("Popover")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._shadow_pad = max(8, theme.Spacing.md)  # room for the drop shadow

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._shadow_pad, self._shadow_pad,
                                 self._shadow_pad, self._shadow_pad)
        outer.setSpacing(0)

        self._frame = QFrame(self)
        self._frame.setObjectName("PopoverFrame")
        self._frame.setAttribute(Qt.WA_StyledBackground, True)
        outer.addWidget(self._frame)

        self._body = QVBoxLayout(self._frame)
        m = theme.Spacing.md
        self._body.setContentsMargins(m, m, m, m)
        self._body.setSpacing(theme.Spacing.sm)

        shadow = QGraphicsDropShadowEffect(self._frame)
        shadow.setBlurRadius(self._shadow_pad)
        shadow.setOffset(0, 4)
        # Shadow opacity matches theme.Colors.drop_shadow_md (0.35α).
        _shadow_color = QColor(theme.Colors.ink_dark)
        _shadow_color.setAlpha(round(0.35 * 255))
        shadow.setColor(_shadow_color)
        self._frame.setGraphicsEffect(shadow)

        self._content: QWidget | None = None
        self.setStyleSheet(self._build_qss())
        self.hide()

    # ── content ──────────────────────────────────────────────────────────
    def setContentWidget(self, widget: QWidget | None) -> None:
        if self._content is not None:
            self._body.removeWidget(self._content)
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = widget
        if widget is not None:
            self._body.addWidget(widget)
        if self.isVisible():
            self.adjustSize()

    def contentWidget(self) -> QWidget | None:
        return self._content

    def bodyLayout(self) -> QVBoxLayout:
        """The card's content layout — for callers that want to add several
        widgets rather than one ``setContentWidget`` payload."""
        return self._body

    # ── show / hide ──────────────────────────────────────────────────────
    def isOpen(self) -> bool:
        return self.isVisible()

    def popup(self, anchor: QWidget, side: str = "bottom",
              align: str = "start", gap: int | None = None) -> None:
        if anchor is None:
            return
        if side not in self._SIDES:
            side = "bottom"
        if align not in ("start", "center", "end"):
            align = "start"
        if gap is None:
            gap = theme.Spacing.xs

        self.adjustSize()
        w, h = self.width(), self.height()

        ar = anchor.rect()
        a_tl = anchor.mapToGlobal(ar.topLeft())
        ax, ay, aw, ah = a_tl.x(), a_tl.y(), ar.width(), ar.height()

        scr_obj = anchor.screen() if hasattr(anchor, "screen") else None
        scr = (scr_obj or QGuiApplication.primaryScreen()).availableGeometry()

        # The popover's own edges include the shadow padding; ``gap`` is the
        # visible gap, so subtract the padding when placing.
        pad = self._shadow_pad

        # The popover rect has ``pad`` of transparent shadow before the visible
        # card on every side. "start" aligns the card's leading edge with the
        # anchor's; "end" aligns the trailing edges; "center" centres them.
        def place(s: str) -> tuple[int, int]:
            if s in ("bottom", "top"):
                if align == "center":
                    x = ax + aw // 2 - w // 2
                elif align == "end":
                    x = ax + aw - w + pad
                else:  # start
                    x = ax - pad
                y = (ay + ah + gap - pad) if s == "bottom" else (ay - h + pad - gap)
            else:  # left / right
                if align == "center":
                    y = ay + ah // 2 - h // 2
                elif align == "end":
                    y = ay + ah - h + pad
                else:  # start
                    y = ay - pad
                x = (ax + aw + gap - pad) if s == "right" else (ax - w + pad - gap)
            return x, y

        # Choose side; flip to the opposite if the primary side overflows.
        x, y = place(side)
        if not _rect_fits(x, y, w, h, scr):
            fx, fy = place(self._OPPOSITE[side])
            if _rect_fits(fx, fy, w, h, scr):
                x, y = fx, fy
        # Clamp onto the screen as a last resort.
        x = max(scr.left() - pad, min(x, scr.right() - w + pad + 1))
        y = max(scr.top() - pad, min(y, scr.bottom() - h + pad + 1))

        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self.activateWindow()
        if self._content is not None:
            self._content.setFocus(Qt.PopupFocusReason)

    # ``Qt.Popup`` already closes on outside-click and Esc; mirror Esc for
    # robustness and emit the signals.
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.opened.emit()

    def hideEvent(self, event) -> None:  # noqa: N802
        # Both ``Qt.Popup`` dismissal (outside-click / Esc) and an explicit
        # ``close()`` route through ``hide()`` → here. Emit ``closed`` once.
        super().hideEvent(event)
        self.closed.emit()

    # ── style ────────────────────────────────────────────────────────────
    def _build_qss(self) -> str:
        c, r = theme.Colors, theme.Radii
        return f"""
        #Popover {{ background: transparent; }}
        #PopoverFrame {{
            background-color: {c.panel};
            border: 1px solid {c.border};
            border-radius: {r.lg}px;
        }}
        """


def _rect_fits(x: int, y: int, w: int, h: int, scr) -> bool:
    return (x >= scr.left() and y >= scr.top()
            and x + w <= scr.right() + 1 and y + h <= scr.bottom() + 1)


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QGridLayout, QLabel, QPushButton, QVBoxLayout,
        QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("Popover — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)
    title = QLabel("Popover — click a button; click outside or Esc to dismiss")
    title.setObjectName("Title")
    title.setWordWrap(True)
    lay.addWidget(title)

    def make_content(text: str) -> _QW:
        w = _QW()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.Spacing.sm)
        h = QLabel(text)
        h.setObjectName("Heading")
        v.addWidget(h)
        v.addWidget(QLabel("Some popover content."))
        v.addWidget(QPushButton("An action"))
        return w

    grid = QGridLayout()
    grid.setHorizontalSpacing(theme.Spacing.xl)
    grid.setVerticalSpacing(theme.Spacing.xl)
    sides = [("bottom", "start"), ("bottom", "center"), ("bottom", "end"),
             ("top", "center"), ("left", "center"), ("right", "center")]
    for i, (side, align) in enumerate(sides):
        b = QPushButton(f"{side} / {align}")
        b.setCursor(Qt.PointingHandCursor)
        pop = Popover(root)
        pop.setContentWidget(make_content(f"side={side}\nalign={align}"))
        b.clicked.connect(lambda _=False, _b=b, _p=pop, _s=side, _a=align: _p.popup(_b, side=_s, align=_a))
        grid.addWidget(b, i // 3, i % 3, Qt.AlignCenter)
    grid_host = _QW()
    grid_host.setLayout(grid)
    lay.addWidget(grid_host, 1)

    echo = QLabel("(open one)")
    echo.setObjectName("Secondary")
    lay.addWidget(echo)
    # one shared popover wired to opened/closed for the echo line
    side_b = QPushButton("opened/closed signal demo")
    sig_pop = Popover(root)
    sig_pop.setContentWidget(make_content("watch the line below"))
    sig_pop.opened.connect(lambda: echo.setText("popover opened"))
    sig_pop.closed.connect(lambda: echo.setText("popover closed"))
    side_b.clicked.connect(lambda: sig_pop.popup(side_b, side="top"))
    lay.addWidget(side_b, 0, Qt.AlignLeft)

    root.resize(560, 360)
    root.show()
    _sys.exit(app.exec())
