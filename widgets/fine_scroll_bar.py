"""FineScrollBar — a scroll bar whose handle cannot outrun the content.

A plain ``QScrollBar`` maps the whole document onto the groove, so dragging the
handle one pixel moves the content by ``range / (groove - handle)``. On a long
document in a short view that is many lines per pixel: the Application Log
drawer holds up to 500 entries, a traceback each being several lines, and its
bar was reported as scrolling too fast to read anything while dragging.

This bar caps that rate. While the handle is dragged, the value moves by at
most ``max_rate()`` units per pixel of pointer movement — for a text view, one
line per pixel — and by the plain proportional amount when that is slower, so
a short document drags exactly as before. Clicks on the groove (a page at a
time), the wheel and the arrow keys are Qt's own.

The price of a capped rate is that the handle lags the pointer on a long drag:
the handle still shows where the view is in the document, and the pointer
only says how far it has moved. Releasing and grabbing again carries on from
where the view is.

* ``FineScrollBar(orientation, parent=None, *, max_rate)`` — *max_rate* is a
  callable returning the cap in value units per pixel, asked again at the
  start of every drag (a font or UI-scale change alters a line's height).
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QScrollBar, QStyle, QStyleOptionSlider, QWidget


class FineScrollBar(QScrollBar):
    """A ``QScrollBar`` whose handle drag moves at most ``max_rate()`` value
    units per pixel. Everything other than a handle drag is the base class's."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None, *,
                 max_rate: Callable[[], float]) -> None:
        super().__init__(orientation, parent)
        self._max_rate = max_rate
        self._drag: tuple[int, int, float] | None = None   # (start pos, start value, rate)

    # ── geometry ────────────────────────────────────────────────────────────
    def _option(self) -> QStyleOptionSlider:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return opt

    def _along(self, p: QPoint) -> int:
        return p.y() if self.orientation() == Qt.Vertical else p.x()

    def proportional_rate(self) -> float:
        """Value units per pixel a plain scroll bar's handle moves at: the
        range spread over the distance the handle can travel."""
        opt = self._option()
        style = self.style()
        groove = style.subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarGroove, self)
        handle = style.subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarSlider, self)
        if self.orientation() == Qt.Vertical:
            travel = groove.height() - handle.height()
        else:
            travel = groove.width() - handle.width()
        span = self.maximum() - self.minimum()
        return span / travel if travel > 0 else 0.0

    def drag_rate(self) -> float:
        """The rate a handle drag moves at now: the proportional rate, capped
        at ``max_rate()``."""
        natural = self.proportional_rate()
        cap = float(self._max_rate())
        return min(natural, cap) if cap > 0 else natural

    def _on_handle(self, pos: QPoint) -> bool:
        hit = self.style().hitTestComplexControl(QStyle.CC_ScrollBar, self._option(), pos, self)
        return hit == QStyle.SC_ScrollBarSlider

    # ── a handle drag at the capped rate ────────────────────────────────────
    def mousePressEvent(self, event) -> None:  # Qt naming
        pos = event.position().toPoint()
        if event.button() == Qt.LeftButton and self._on_handle(pos):
            self._drag = (self._along(pos), self.value(), self.drag_rate())
            self.setSliderDown(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # Qt naming
        if self._drag is None:
            super().mouseMoveEvent(event)
            return
        start, value, rate = self._drag
        moved = self._along(event.position().toPoint()) - start
        self.setValue(round(value + moved * rate))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # Qt naming
        if self._drag is None:
            super().mouseReleaseEvent(event)
            return
        self._drag = None
        self.setSliderDown(False)
        event.accept()
