"""PlateOrderEditor — a click-to-define acquisition-ordering plate mockup.

An 8×12 microplate that shows, and lets the user edit, the **order** in which a
scan visited its wells. Unlike :class:`widgets.well_plate_selector.WellPlateSelector`
(whose model is an unordered *set*), this widget's model is an ordered *list* of
wells — the acquisition path — so it can represent an arbitrary, possibly
non-rectangular ordering that the OME-TIFF converter maps onto stage positions.

Two things are drawn:

* **Numbered wells** — each well on the path is lit and carries its 1-based
  sequence number, so you can read the visit order straight off the plate.
* **The ordering arrow** — a subtle, alpha-blended line runs from each visited
  well to the next with a small arrowhead, tracing the position→well naming
  sequence across the plate as a quiet guide rather than a bold overlay.

Interaction: clicking an off-path well **appends** it (it becomes the next
number); clicking an on-path well **removes** it and the rest renumber. Every
click marks the widget *dirty* (user-owned), which the embedding page uses to
stop overwriting a hand-drawn order with the rectangular grid preview.

All metrics derive from the widget font / available size (no hardcoded device
pixels), so the plate is DPI / font-scaling aware; every colour comes from
``theme`` tokens. Geometry helpers mirror ``well_plate_selector`` deliberately —
the two plates should read as the same control.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

import theme
from widgets._support import with_alpha

_ROW_LETTERS = "ABCDEFGH"
_N_ROWS = 8
_N_COLS = 12


def _well_id(row: int, col: int) -> str:
    return f"{_ROW_LETTERS[row]}{col + 1:02d}"


def _parse_well_id(well_id) -> tuple[int, int] | None:
    wid = str(well_id).strip().upper()
    if len(wid) < 2 or wid[0] not in _ROW_LETTERS:
        return None
    try:
        col = int(wid[1:]) - 1
    except ValueError:
        return None
    row = _ROW_LETTERS.index(wid[0])
    if 0 <= row < _N_ROWS and 0 <= col < _N_COLS:
        return row, col
    return None


class PlateOrderEditor(QWidget):
    """8×12 plate whose model is an ordered acquisition path (click to edit)."""

    orderChanged = Signal(list)   # new order as a list of well ids, e.g. ["A01", …]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlateOrderEditor")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

        # The acquisition path: an ordered list of (row, col). Element i is the
        # well visited at stage position i (before FOV/timepoint interleaving).
        self._order: list[tuple[int, int]] = []
        self._dirty = False
        self._hover_cell: tuple[int, int] | None = None
        self.setStyleSheet(self._build_qss())

    # ── geometry (font / size relative; mirrors well_plate_selector) ─────────
    def _label_extent(self) -> float:
        return self.fontMetrics().height() * 1.7

    def _metrics(self) -> tuple[float, float, float, float]:
        lab = self._label_extent()
        avail_w = max(1.0, self.width() - lab - 2.0)
        avail_h = max(1.0, self.height() - lab - 2.0)
        cell = max(1.0, min(avail_w / _N_COLS, avail_h / _N_ROWS))
        total_w = lab + cell * _N_COLS
        total_h = lab + cell * _N_ROWS
        ox = (self.width() - total_w) / 2.0 + lab
        oy = (self.height() - total_h) / 2.0 + lab
        return lab, cell, ox, oy

    def _cell_rect(self, row: int, col: int) -> QRectF:
        _lab, cell, ox, oy = self._metrics()
        return QRectF(ox + col * cell, oy + row * cell, cell, cell)

    def _well_rect(self, row: int, col: int) -> QRectF:
        cr = self._cell_rect(row, col)
        d = cr.width() * 0.80
        return QRectF(cr.center().x() - d / 2.0, cr.center().y() - d / 2.0, d, d)

    def _hit(self, x: float, y: float) -> tuple[int, int] | None:
        _lab, cell, ox, oy = self._metrics()
        if x < ox or y < oy:
            return None
        col = int((x - ox) // cell)
        row = int((y - oy) // cell)
        if 0 <= row < _N_ROWS and 0 <= col < _N_COLS:
            return row, col
        return None

    def sizeHint(self) -> QSize:
        u = max(20, round(self.fontMetrics().height() * 1.7))
        lab = round(self._label_extent())
        return QSize(lab + u * _N_COLS, lab + u * _N_ROWS)

    def minimumSizeHint(self) -> QSize:
        u = 12
        lab = round(self.fontMetrics().height() * 1.2)
        return QSize(lab + u * _N_COLS, lab + u * _N_ROWS)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        lab = self._label_extent()
        cell = max(1.0, (max(1.0, float(w)) - lab) / _N_COLS)
        return int(round(lab + cell * _N_ROWS))

    # ── model API ────────────────────────────────────────────────────────────
    def order(self) -> list[str]:
        """The acquisition path as canonical well ids, in visit order."""
        return [_well_id(r, c) for (r, c) in self._order]

    def _set_cells(self, labels, *, dirty: bool, emit: bool) -> None:
        new: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for w in labels or ():
            parsed = _parse_well_id(w)
            if parsed is not None and parsed not in seen:
                seen.add(parsed)
                new.append(parsed)
        if new == self._order and dirty == self._dirty:
            return
        self._order = new
        self._dirty = dirty
        self.update()
        if emit:
            self.orderChanged.emit(self.order())

    def set_order(self, labels) -> None:
        """Set the path from *labels* as a user-owned (dirty) order, no signal."""
        self._set_cells(labels, dirty=True, emit=False)

    def set_preview(self, labels) -> None:
        """Display *labels* as a non-dirty preview (e.g. the grid's serpentine).

        Ignored once the widget is dirty — a hand-drawn order is never
        overwritten by a preview. Emits nothing and does not mark the widget
        dirty, so the page can track the grid without stealing ownership.
        """
        if self._dirty:
            return
        self._set_cells(labels, dirty=False, emit=False)

    def is_dirty(self) -> bool:
        return self._dirty

    def clear(self) -> None:
        """Empty the path and drop dirty state, emitting the (empty) order."""
        if not self._order and not self._dirty:
            return
        self._order = []
        self._dirty = False
        self.update()
        self.orderChanged.emit([])

    # ── interaction ──────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        hit = self._hit(event.position().x(), event.position().y())
        if hit is None:
            return
        if hit in self._order:
            self._order.remove(hit)     # removing renumbers the rest for free
        else:
            self._order.append(hit)
        self._dirty = True
        self.update()
        self.orderChanged.emit(self.order())

    def mouseMoveEvent(self, event) -> None:
        hit = self._hit(event.position().x(), event.position().y())
        if hit != self._hover_cell:
            self._hover_cell = hit
            self.update()

    def leaveEvent(self, _event) -> None:
        if self._hover_cell is not None:
            self._hover_cell = None
            self.update()

    # ── painting ─────────────────────────────────────────────────────────────
    def _paint_lit(self, p: QPainter, wr: QRectF, base: QColor, hovered: bool) -> None:
        c = theme.Colors
        base = QColor(base)
        grad = QRadialGradient(
            wr.center().x() - wr.width() * 0.15,
            wr.center().y() - wr.height() * 0.20,
            wr.width() * 0.75,
        )
        grad.setColorAt(0.0, base.lighter(150))
        grad.setColorAt(0.55, base)
        grad.setColorAt(1.0, base.darker(135))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(c.text_primary) if hovered else base.darker(170),
                      max(1.0, wr.width() * 0.04)))
        p.drawEllipse(wr)

    def _paint_depressed(self, p: QPainter, wr: QRectF, hovered: bool) -> None:
        c = theme.Colors
        base = QColor(c.rail)
        grad = QLinearGradient(wr.topLeft(), wr.bottomLeft())
        grad.setColorAt(0.0, base.darker(165))
        grad.setColorAt(0.55, base.darker(115))
        grad.setColorAt(1.0, base.lighter(112))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(c.border_strong if hovered else c.border_subtle),
                      max(1.0, wr.width() * 0.05)))
        p.drawEllipse(wr)

    def _paint_path(self, p: QPainter, cell: float) -> None:
        """Draw the subtle arrow tracing the visit order over the wells."""
        if len(self._order) < 2:
            return
        accent = with_alpha(QColor(theme.Colors.accent), 0.55)
        width = max(1.2, cell * 0.05)
        p.setPen(QPen(accent, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QBrush(accent))
        inset = cell * 0.40   # stop just outside each well circle
        for (r0, c0), (r1, c1) in zip(self._order, self._order[1:]):
            a = self._well_rect(r0, c0).center()
            b = self._well_rect(r1, c1).center()
            seg = QPointF(b.x() - a.x(), b.y() - a.y())
            length = (seg.x() ** 2 + seg.y() ** 2) ** 0.5
            if length <= 1e-6:
                continue
            ux, uy = seg.x() / length, seg.y() / length
            start = QPointF(a.x() + ux * inset, a.y() + uy * inset)
            end = QPointF(b.x() - ux * inset, b.y() - uy * inset)
            p.setPen(QPen(accent, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(start, end)
            self._draw_arrowhead(p, end, ux, uy, cell)

    def _draw_arrowhead(self, p: QPainter, tip: QPointF, ux: float, uy: float,
                        cell: float) -> None:
        head = cell * 0.16
        # Base corners: back off along the segment, then step out perpendicular.
        bx, by = tip.x() - ux * head, tip.y() - uy * head
        px, py = -uy, ux
        left = QPointF(bx + px * head * 0.6, by + py * head * 0.6)
        right = QPointF(bx - px * head * 0.6, by - py * head * 0.6)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([tip, left, right]))

    def paintEvent(self, _event) -> None:
        c = theme.Colors
        lab, cell, ox, oy = self._metrics()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setFont(self.font())

        # Column numbers (1–12) along the top.
        for ci in range(_N_COLS):
            rect = QRectF(ox + ci * cell, oy - lab, cell, lab)
            p.setPen(QColor(c.text_muted))
            p.drawText(rect, int(Qt.AlignCenter), str(ci + 1))
        # Row letters (A–H) down the left.
        for ri in range(_N_ROWS):
            rect = QRectF(ox - lab, oy + ri * cell, lab, cell)
            p.setPen(QColor(c.text_muted))
            p.drawText(rect, int(Qt.AlignCenter), _ROW_LETTERS[ri])

        # Sequence index of each visited cell, for the well numbers.
        seq = {cell_rc: i + 1 for i, cell_rc in enumerate(self._order)}

        # Wells.
        for ri in range(_N_ROWS):
            for ci in range(_N_COLS):
                key = (ri, ci)
                wr = self._well_rect(ri, ci)
                hovered = self._hover_cell == key
                if key in seq:
                    self._paint_lit(p, wr, QColor(c.accent), hovered)
                    self._draw_number(p, wr, str(seq[key]))
                else:
                    self._paint_depressed(p, wr, hovered)

        # The ordering arrow, over the wells.
        self._paint_path(p, cell)
        p.end()

    def _draw_number(self, p: QPainter, wr: QRectF, text: str) -> None:
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(max(7, int(wr.height() * 0.52)))
        p.setFont(f)
        p.setPen(QColor(theme.Colors.ink_dark))
        p.drawText(wr, int(Qt.AlignCenter), text)
        p.setFont(self.font())

    def _build_qss(self) -> str:
        c, r = theme.Colors, theme.Radii
        return f"""
        #PlateOrderEditor {{
            background-color: {c.panel};
            border: 1px solid {c.border_subtle};
            border-radius: {r.md}px;
        }}
        """
