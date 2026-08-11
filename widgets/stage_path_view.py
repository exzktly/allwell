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

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

import theme
from widgets.plate_geometry import PLATE_MAX_HEIGHT, PLATE_MAX_WIDTH

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

# A position's number is drawn once there is room for it: the gap to its nearest
# neighbour has to exceed this many pixels, or the labels overlap each other and
# the marks they belong to. It is a *room* test rather than a count, so zooming
# in reveals the numbers exactly where the eye has already gone.
_LABEL_ROOM_PX = 16.0

# Font size as a share of the room available, floored at a size that is still
# a readable glyph rather than a smudge. Below about 8pt the digits stop being
# distinguishable at all, so a label that cannot reach it is not drawn.
_LABEL_OF_ROOM = 0.42
_LABEL_MIN_PT = 8.0
_LABEL_MAX_PT = 13.0

# Clearance kept around a label before the next one may be drawn. Labels that
# touch are worse than none: the numbers stop being readable *and* stop being
# attributable to a mark.
_LABEL_PAD_PX = 2.0

# Zoom bounds, and how much one wheel notch moves it.
_ZOOM_MIN, _ZOOM_MAX, _ZOOM_STEP = 1.0, 40.0, 1.25


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
        # A bare QWidget has no size hint, so a layout gives it nothing and it
        # collapses to 0x0 — visible by every flag, and never a pixel on screen.
        # The plate above it gets its size from `plate_geometry`; this asks for
        # the same box, so the two line up and neither has to be told twice.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._plate: QWidget | None = None
        self._points: list[tuple[float, float]] = []
        self._fitted = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._drag_from: QPointF | None = None
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(PLATE_MAX_WIDTH, PLATE_MAX_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def setPlate(self, plate: QWidget | None) -> None:
        """Take cell geometry from *plate* (anything with ``cellRect``)."""
        self._plate = plate
        self.update()

    def setPath(self, points) -> None:
        """Set the path, as ``(x, y)`` in cell units — see the module docstring."""
        self._points = [(float(x), float(y)) for x, y in points or []]
        # Decided here, not while painting: the host writes its caption from
        # ``isFitted`` as soon as it sets the path, and a value that only became
        # true on the next paint left the caption describing the previous one.
        # The test needs the points alone, so there is nothing to wait for.
        xs = [x for x, _ in self._points]
        ys = [y for _, y in self._points]
        self._fitted = bool(self._points) and not (
            min(xs) >= -0.5 and max(xs) <= _N_COLS - 0.5
            and min(ys) >= -0.5 and max(ys) <= _N_ROWS - 0.5)
        self.update()

    def clear(self) -> None:
        self.setPath([])

    def hasPath(self) -> bool:
        return bool(self._points)

    zoomChanged = Signal(float)

    def zoom(self) -> float:
        return self._zoom

    def setZoom(self, factor: float, about: QPointF | None = None) -> None:
        """Zoom to *factor*, keeping the point under *about* where it is.

        Zooming about the cursor rather than the centre is what makes a scan of
        hundreds of positions navigable: the well being looked at stays put
        instead of sliding off while the view grows around the middle.
        """
        new = max(_ZOOM_MIN, min(_ZOOM_MAX, float(factor)))
        if new == self._zoom:
            return
        anchor = about if about is not None else QPointF(self.rect().center())
        # Keep the anchor over the same content: solve for the pan that leaves
        # (anchor - pan) / zoom unchanged across the two zoom levels.
        ratio = new / self._zoom
        self._pan = QPointF(
            anchor.x() - (anchor.x() - self._pan.x()) * ratio,
            anchor.y() - (anchor.y() - self._pan.y()) * ratio,
        )
        self._zoom = new
        if new == _ZOOM_MIN:
            self._pan = QPointF(0.0, 0.0)     # fully out is always the whole map
        self.update()
        self.zoomChanged.emit(new)

    def resetView(self) -> None:
        """Back to the whole map, unzoomed and unpanned."""
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()
        self.zoomChanged.emit(self._zoom)

    # ── navigation ───────────────────────────────────────────────────────────
    def wheelEvent(self, event) -> None:  # noqa: D401 — Qt entry point
        notches = event.angleDelta().y() / 120.0
        if not notches:
            return
        self.setZoom(self._zoom * (_ZOOM_STEP ** notches),
                     QPointF(event.position()))
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: D401 — Qt entry point
        if event.button() == Qt.LeftButton and self._zoom > _ZOOM_MIN:
            self._drag_from = QPointF(event.position())
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: D401 — Qt entry point
        if self._drag_from is None:
            return
        here = QPointF(event.position())
        self._pan += here - self._drag_from
        self._drag_from = here
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: D401 — Qt entry point
        self._drag_from = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: D401 — Qt entry point
        self.resetView()

    def isFitted(self) -> bool:
        """True when the drawing had to be shrunk to show the whole path.

        The path is drawn at the plate's own scale — one cell, one well — which
        only works while it *fits* on a plate. A scan whose inferred well pitch
        came out too small spans far more than twelve columns, and at plate
        scale all but the first few wells fall outside the widget: the user sees
        a fragment and no indication that there is more. Then the drawing is
        scaled to its own extent instead, and this says so, because a picture
        that is no longer one-cell-per-well must not claim to be.
        """
        return self._fitted

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
        # One transform for the whole drawing. The lattice is the frame the path
        # is read against, so it has to move and scale with it — left at plate
        # scale while the path zoomed, it stopped being a reference and became
        # a decoration the path had flown off.
        scale, px, py = self._transform(cell, ox, oy)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(self.rect())
        self._paint_cells(painter, scale, px, py)
        if self._points:
            self._paint_path(painter, scale, px, py)
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

    def _transform(self, cell: float, ox: float, oy: float):
        """``(scale, x offset, y offset)`` for drawing the path in this widget.

        Plate scale whenever the path fits the 8x12 grid, which is the point of
        the view. When it does not — a pitch read too small, a block placed off
        the edge — the whole drawing is scaled to its own bounding box and
        centred, so the shape of the run is visible even though a cell is no
        longer a well. :meth:`isFitted` reports which of the two happened.
        """
        if not self._fitted:
            return (cell * self._zoom,
                    ox * self._zoom + self._pan.x(),
                    oy * self._zoom + self._pan.y())
        xs = [x for x, _ in self._points]
        ys = [y for _, y in self._points]
        span_x = max(max(xs) - min(xs), 1e-9)
        span_y = max(max(ys) - min(ys), 1e-9)
        scale = min(_N_COLS * cell / (span_x + 1), _N_ROWS * cell / (span_y + 1))
        scale *= self._zoom
        return (scale,
                ox + (_N_COLS * cell - span_x * scale) / 2.0
                - min(xs) * scale + self._pan.x(),
                oy + (_N_ROWS * cell - span_y * scale) / 2.0
                - min(ys) * scale + self._pan.y())

    def _paint_path(self, painter: QPainter, scale: float,
                    px: float, py: float) -> None:
        pixels = [
            QPointF(px + (x + 0.5) * scale, py + (y + 0.5) * scale)
            for x, y in self._points
        ]
        cell = scale
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
        self._paint_numbers(painter, pixels, dot)

    def _paint_numbers(self, painter: QPainter, pixels: list[QPointF],
                       dot: float) -> None:
        """Number each position, once the drawing has room to show them.

        Room, not a count: at full extent a plate scan's positions are a few
        pixels apart and any label overlaps its neighbours and the marks they
        name, while zoomed in there is space and the numbers are the reason to
        have zoomed. So they appear exactly where the eye has already gone,
        rather than being all-or-nothing for the whole run.
        """
        room = min(
            (
                max(abs(a.x() - b.x()), abs(a.y() - b.y()))
                for a, b in zip(pixels[:-1], pixels[1:])
                if a != b
            ),
            default=float(min(self.width(), self.height())),
        )
        if room < _LABEL_ROOM_PX:
            return
        font = QFont(painter.font())
        font.setPointSizeF(min(_LABEL_MAX_PT, max(_LABEL_MIN_PT,
                                                  room * _LABEL_OF_ROOM)))
        painter.setFont(font)
        # Text needs its own hint. Without it the glyphs are drawn unsmoothed
        # and at this size that is not small text, it is noise — which is what
        # "white scribbling" is.
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setPen(QPen(QColor(theme.Colors.ink_light)))
        painter.setBrush(Qt.NoBrush)
        metrics = QFontMetricsF(font)
        offset = dot / 2.0 + 2.0
        # A label is drawn only where it does not touch one already drawn. The
        # spacing test above is between *consecutive* marks, which says nothing
        # about a mark from another row landing in the same place, and a number
        # is far wider than the gap that test clears — so on a dense run every
        # label overlapped its neighbours and the result read as scribble.
        taken: list = []
        for index, point in enumerate(pixels, start=1):
            label = str(index)
            rect = metrics.boundingRect(label)
            rect.moveTo(point.x() + offset, point.y() - offset - rect.height())
            rect = rect.adjusted(-_LABEL_PAD_PX, -_LABEL_PAD_PX,
                                 _LABEL_PAD_PX, _LABEL_PAD_PX)
            if any(rect.intersects(other) for other in taken):
                continue
            taken.append(rect)
            painter.drawText(QPointF(point.x() + offset, point.y() - offset),
                             label)
