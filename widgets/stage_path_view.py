"""StagePathView — the microscope's actual travel, drawn at plate scale.

A plate map can say which *well* each image belongs to and the order the wells
were visited. It cannot say anything about what happened inside a well, because
every field of view in one well is the same cell. This view draws the other half:
one dot per imaged position, joined in acquisition order, over the same 8x12
cell geometry as the plate above it — so the fields inside a well appear as a
small cluster in the right cell, with the stage's route through them visible.

It borrows its geometry from a reference plate rather than deriving it from its
own width (``setPlate``): the plate reserves room for its row and column headers
before sizing cells, so two widgets of equal width do not otherwise agree on
where a well is. Points arrive already in cell units from
``datasets.ome_import.stage_layout.path_in_cells``; this widget does no
coordinate maths of its own beyond scaling cells to pixels.

Carries no All-Well knowledge — a list of points and a plate to align to — so it
stays inside the ``widgets/`` no-app-coupling contract.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

import theme

_N_ROWS, _N_COLS = 8, 12

# Dot diameter and line width as fractions of a cell, so the drawing scales with
# the plate instead of pinning pixel sizes that only look right at one size.
# These are ceilings: see ``_marks`` for why the drawing usually goes smaller.
_DOT_FRACTION = 0.14
_START_DOT_FRACTION = 0.26
_LINE_FRACTION = 0.035
_GRID_LINE_FRACTION = 0.012

# A dot is drawn at most this share of the distance to its nearest neighbour, so
# the fields of view inside one well stay separate marks. A whole cell is a well,
# and the fields in it are a fifteenth of that apart, so a dot sized off the cell
# alone is wider than the gap it has to sit in — the four dots merge into one and
# the drawing says exactly what the plate map above it already said.
_DOT_OF_SPACING = 0.45

# Below this many pixels a dot is not a dot, so the cluster is drawn as a mark
# the eye can still resolve rather than sub-pixel mush.
_MIN_DOT_PX = 2.2

# Above this many positions the dots are dropped and only the route is drawn: a
# plate scan can hold thousands, and at that density the dots merge into a blob
# that hides the very route they were meant to decorate.
_DOT_BUDGET = 600


class StagePathView(QWidget):
    """Dots at each imaged position, joined in order, at a plate's own scale."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StagePathView")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # The plate's card, painted here rather than inherited. A
        # ``WellPlateSelector`` builds that rule into a stylesheet it sets on
        # *itself*, and this view is its sibling, not its child — taking the
        # same object name got the name and none of the appearance, which is
        # exactly the silent miss ``tests/test_theme_selectors.py`` exists to
        # catch. Same tokens, so the two still read as one drawing.
        self.setStyleSheet(f"""
        #StagePathView {{
            background-color: {theme.Colors.panel};
            border: 1px solid {theme.Colors.border_subtle};
            border-radius: {theme.Radii.md}px;
        }}
        """)
        self._plate: QWidget | None = None
        self._points: list[tuple[float, float]] = []

    def setPlate(self, plate: QWidget | None) -> None:
        """Take cell geometry from *plate* (anything with ``cellRect``)."""
        self._plate = plate
        self.update()

    def setPath(self, points) -> None:
        """Set the path, as ``(x, y)`` in cell units — see the module docstring."""
        self._points = [(float(x), float(y)) for x, y in points or []]
        self.update()

    def clear(self) -> None:
        self.setPath([])

    def hasPath(self) -> bool:
        return bool(self._points)

    # ── painting ─────────────────────────────────────────────────────────────
    def _geometry(self) -> tuple[float, float, float] | None:
        """``(cell, ox, oy)`` in this widget's coordinates, from the plate."""
        plate = self._plate
        if plate is None or not hasattr(plate, "cellRect"):
            return None
        rect = plate.cellRect(0, 0)
        cell = float(rect.width())
        if cell <= 0:
            return None
        # The plate's own vertical centring is its business; this view centres
        # the 8x12 block in its own height so the two read as one drawing.
        oy = max(0.0, (self.height() - cell * _N_ROWS) / 2.0)
        return cell, float(rect.x()), oy

    def paintEvent(self, event) -> None:  # noqa: D401 — Qt entry point
        geometry = self._geometry()
        if geometry is None:
            return
        cell, ox, oy = geometry
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_cells(painter, cell, ox, oy)
        if self._points:
            self._paint_path(painter, cell, ox, oy)
        painter.end()

    def _paint_cells(self, painter: QPainter, cell: float,
                     ox: float, oy: float) -> None:
        """The 8x12 lattice, faint — it is a reference frame, not the subject."""
        pen = QPen(QColor(theme.Colors.border))
        pen.setWidthF(max(1.0, cell * _GRID_LINE_FRACTION))
        painter.setPen(pen)
        for row in range(_N_ROWS + 1):
            y = oy + row * cell
            painter.drawLine(QPointF(ox, y), QPointF(ox + _N_COLS * cell, y))
        for col in range(_N_COLS + 1):
            x = ox + col * cell
            painter.drawLine(QPointF(x, oy), QPointF(x, oy + _N_ROWS * cell))

    @staticmethod
    def _marks(pixels: list[QPointF], cell: float) -> tuple[float, float]:
        """``(dot diameter, line width)`` in pixels for this set of points.

        Sized off the **closest pair**, not off the cell. A cell is a whole well
        and the fields of view inside it are a fifteenth of that apart, so a dot
        scaled to the cell is wider than the gap it has to sit in: the fields
        merge into a single mark and the drawing tells the user nothing the plate
        map above it had not already said. The cell fraction stays as a ceiling,
        for a scan with one field per well where there is nothing to crowd.
        """
        closest = min(
            (
                max(abs(a.x() - b.x()), abs(a.y() - b.y()))
                for a, b in zip(pixels[:-1], pixels[1:])
                if a != b
            ),
            default=cell,
        )
        dot = min(cell * _DOT_FRACTION, max(_MIN_DOT_PX, closest * _DOT_OF_SPACING))
        return dot, min(cell * _LINE_FRACTION, max(1.3, dot / 2.5))

    def _paint_path(self, painter: QPainter, cell: float,
                    ox: float, oy: float) -> None:
        pixels = [
            QPointF(ox + (x + 0.5) * cell, oy + (y + 0.5) * cell)
            for x, y in self._points
        ]
        dot, line = self._marks(pixels, cell)

        route = QPainterPath(pixels[0])
        for point in pixels[1:]:
            route.lineTo(point)
        pen = QPen(QColor(theme.Colors.accent))
        pen.setWidthF(line)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(route)

        painter.setPen(Qt.NoPen)
        if len(pixels) <= _DOT_BUDGET:
            painter.setBrush(QColor(theme.Colors.accent))
            for point in pixels:
                painter.drawEllipse(point, dot / 2.0, dot / 2.0)
        # The first position always keeps a dot, and a larger one: where the scan
        # began is the one thing on this drawing the user chose rather than read.
        # Sized off the same neighbour spacing, so it marks the start without
        # swallowing the cluster it sits in.
        painter.setBrush(QColor(theme.Colors.success))
        start = max(dot * 1.8, min(cell * _START_DOT_FRACTION, dot * 3.0)) / 2.0
        painter.drawEllipse(pixels[0], start, start)
