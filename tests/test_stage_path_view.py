"""The stage-path view, and the geometry it borrows from a plate.

The whole point of this widget is that its cells land under the plate's own, so
a dot in the third cell along means the third column. Everything here is about
that agreement; the path maths itself lives in ``test_ome_stage_layout.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.stage_path_view import StagePathView  # noqa: E402
from widgets.well_plate_selector import WellPlateSelector  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_cell_rect_is_reported_in_the_plate_s_own_coordinates(qapp):
    """Deriving the cell size from the widget's width fails — the grid reserves
    room for its row and column headers first — so the plate has to say."""
    plate = WellPlateSelector()
    plate.resize(330, 210)
    plate.show()
    qapp.processEvents()

    first = plate.cellRect(0, 0)
    second = plate.cellRect(0, 1)
    assert first.width() > 0
    assert round(second.x() - first.x(), 6) == round(first.width(), 6)
    # Inside the widget, not the inner grid child: a sibling view of the same
    # width can use these x values directly.
    assert first.x() > 0
    assert first.right() < plate.width()
    plate.hide()


def test_the_view_takes_its_scale_from_the_plate(qapp):
    plate = WellPlateSelector()
    plate.resize(330, 210)
    plate.show()
    view = StagePathView()
    view.resize(330, 210)
    view.setPlate(plate)
    qapp.processEvents()

    cell, ox, _oy = view._geometry()
    assert cell == plate.cellRect(0, 0).width()
    assert ox == plate.cellRect(0, 0).x()
    plate.hide()


def test_the_view_has_no_geometry_without_a_plate(qapp):
    view = StagePathView()
    view.resize(330, 210)
    assert view._geometry() is None
    view.paintEvent(None)          # must not raise with nothing to align to


def test_path_round_trips_and_clears(qapp):
    view = StagePathView()
    assert not view.hasPath()
    view.setPath([(1.0, 1.0), (1.07, 1.0)])
    assert view.hasPath()
    assert view._points == [(1.0, 1.0), (1.07, 1.0)]
    view.clear()
    assert not view.hasPath()


def test_painting_a_path_does_not_raise(qapp):
    """Offscreen render of the real paint path — the dots, the route and the
    start marker — including the dense case that drops the per-point dots."""
    from PySide6.QtGui import QPixmap

    plate = WellPlateSelector()
    plate.resize(330, 210)
    plate.show()
    view = StagePathView()
    view.resize(330, 210)
    view.setPlate(plate)
    qapp.processEvents()

    for count in (1, 24, 2000):
        view.setPath([(i % 12, (i // 12) % 8) for i in range(count)])
        pixmap = QPixmap(view.size())
        view.render(pixmap)
        assert not pixmap.isNull(), count
    plate.hide()


def test_the_view_asks_a_layout_for_a_real_box(qapp):
    """A bare QWidget has no size hint, so a QVBoxLayout gives it nothing and it
    lays out at 0x0 — `isVisible()` True, `setPath` full, and not one pixel on
    screen. Asserting the points it holds cannot catch that; only its geometry
    can, and this is the assertion that was missing."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget as _W

    from widgets.plate_geometry import PLATE_MAX_HEIGHT, PLATE_MAX_WIDTH

    host = _W()
    layout = QVBoxLayout(host)
    view = StagePathView(host)
    layout.addWidget(view)
    host.resize(900, 900)
    host.show()
    qapp.processEvents()

    assert view.sizeHint().height() == PLATE_MAX_HEIGHT
    assert view.sizeHint().width() == PLATE_MAX_WIDTH
    assert view.height() > 0
    assert view.width() > 0
    host.hide()


def test_a_path_that_does_not_fit_a_plate_is_scaled_to_fit(qapp):
    """At plate scale a path wider than twelve cells runs off the widget, and
    the user sees the first few wells and no sign there is more. A wrong well
    pitch read out of the coordinates does exactly that."""
    plate = WellPlateSelector()
    plate.resize(330, 210)
    plate.show()
    view = StagePathView()
    view.resize(330, 210)
    view.setPlate(plate)
    qapp.processEvents()

    view.setPath([(c, r) for r in range(3) for c in range(4)])
    view.grab()
    assert not view.isFitted()                    # fits a plate: true scale

    view.setPath([(c * 3.0, r * 2.5) for r in range(8) for c in range(12)])
    view.grab()
    assert view.isFitted()

    view.setPath([(50.0 + c, 2.0) for c in range(6)])   # off the right edge
    view.grab()
    assert view.isFitted()
    plate.hide()


def test_a_fitted_path_is_drawn_inside_the_widget(qapp):
    """Scaling to fit is only worth anything if the result lands on screen."""
    from PySide6.QtGui import QPixmap

    plate = WellPlateSelector()
    plate.resize(330, 210)
    plate.show()
    view = StagePathView()
    view.resize(330, 210)
    view.setPlate(plate)
    view.setPath([(c * 4.0, r * 3.0) for r in range(8) for c in range(12)])
    qapp.processEvents()

    def _render():
        pixmap = QPixmap(view.size())
        view.render(pixmap)
        return pixmap.toImage()

    with_path = _render()
    view.setPath([])
    qapp.processEvents()
    empty = _render()
    # Compared against the same widget holding nothing, rather than hunting an
    # exact colour: the marks are antialiased, so their pixels are blends.
    differing = sum(with_path.pixelColor(x, y) != empty.pixelColor(x, y)
                    for x in range(0, with_path.width(), 2)
                    for y in range(0, with_path.height(), 2))
    assert differing > 50, "the fitted path drew nothing inside the widget"
    plate.hide()


def _view_with_plate(qapp, size=(660, 420)):
    plate = WellPlateSelector()
    plate.setFixedSize(*size)
    plate.show()
    view = StagePathView()
    view.setFixedSize(*size)
    view.setPlate(plate)
    view.show()
    qapp.processEvents()
    return view, plate


def _render(view):
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(view.size())
    view.render(pixmap)
    return pixmap.toImage()


def test_zoom_changes_the_drawing_and_reset_undoes_it(qapp):
    from PySide6.QtCore import QPointF

    view, plate = _view_with_plate(qapp)
    view.setPath([(c + (f % 2) * 0.07, r + (f // 2) * 0.07)
                  for r in range(2) for c in range(3) for f in range(4)])
    out = _render(view)

    view.setZoom(8.0, QPointF(120, 110))
    assert view.zoom() == 8.0
    assert _render(view) != out

    view.resetView()
    assert view.zoom() == 1.0
    assert _render(view) == out          # exactly where it started
    plate.hide()


def test_zoom_is_bounded_and_keeps_its_anchor_put(qapp):
    from PySide6.QtCore import QPointF

    view, plate = _view_with_plate(qapp)
    view.setPath([(float(c), 0.0) for c in range(6)])

    view.setZoom(1000.0)
    assert view.zoom() <= 40.0            # bounded, not unbounded
    view.setZoom(0.01)
    assert view.zoom() == 1.0             # and never smaller than the whole map
    # Fully zoomed out is always the whole map, never a panned fragment of it.
    view.setZoom(6.0, QPointF(200, 100))
    view.setZoom(1.0)
    assert view._pan == QPointF(0.0, 0.0)
    plate.hide()


def test_the_lattice_moves_with_the_path(qapp):
    """The grid is the frame the path is read against. Left at plate scale
    while the path zooms it stops being a reference: the path flies off it and
    what is left is an empty lattice."""
    from PySide6.QtCore import QPointF

    view, plate = _view_with_plate(qapp)
    view.clear()                                  # the lattice on its own
    flat = _render(view)
    view.setZoom(8.0, QPointF(100, 100))
    assert _render(view) != flat, "the lattice did not move with the zoom"

    # And the path is still drawn on it at that zoom, over the same cells.
    view.resetView()
    view.setPath([(float(c) * 0.1, 0.1) for c in range(4)])
    view.setZoom(8.0, QPointF(60, 60))
    with_path = _render(view)
    view.clear()
    assert _render(view) != with_path, "the path is not drawn at this zoom"
    plate.hide()


def test_positions_are_numbered_once_there_is_room(qapp):
    """Room, not a count: at full extent the marks are pixels apart and labels
    would overlap each other, while zoomed in the numbers are the reason to
    have zoomed."""
    from PySide6.QtCore import QPointF

    view, plate = _view_with_plate(qapp)
    # Four fields inside each of two wells: a fraction of a cell apart, so at
    # 1x there is no room for a label and at 8x there is.
    view.setPath([(c + (f % 2) * 0.07, (f // 2) * 0.07)
                  for c in range(2) for f in range(4)])
    flat = _render(view)
    view.setZoom(8.0, QPointF(80, 80))
    close = _render(view)

    def _ink(image):
        return sum(image.pixelColor(x, y).lightness()
                   for x in range(0, image.width(), 3)
                   for y in range(0, image.height(), 3))

    assert _ink(close) != _ink(flat)
    plate.hide()
