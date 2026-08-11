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
