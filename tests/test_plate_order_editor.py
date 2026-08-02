"""Headless checks for the acquisition-ordering plate (``widgets.plate_order_editor``).

The widget owns an ordered list of wells (the acquisition path), not an unordered
selection set, so these exercise the ordered round-trip, the click append /
remove-and-renumber behaviour, the dirty flag that stops a preview overwriting a
hand-drawn order, and that ``paintEvent`` (numbers + arrow) runs without crashing.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.plate_order_editor import PlateOrderEditor  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _click_well(w: PlateOrderEditor, well_id: str) -> None:
    """Synthesise a left-click on the centre of *well_id*'s cell.

    The editor is a ``WellPlateSelector``, so the painted grid that handles
    mouse events is the ``_grid`` child, not the card itself.
    """
    from widgets.well_plate_selector import _parse_well_id

    row, col = _parse_well_id(well_id)
    grid = w._grid
    center = grid._cell_rect(row, col).center()
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(center), Qt.LeftButton,
        Qt.LeftButton, Qt.NoModifier,
    )
    grid.mousePressEvent(ev)


def test_set_order_round_trips(qapp):
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_order(["a1", "B02", "h12"])
    assert w.order() == ["A01", "B02", "H12"]     # canonicalised, order preserved
    assert w.is_dirty()


def test_set_order_dedupes(qapp):
    w = PlateOrderEditor()
    w.set_order(["A01", "A01", "B02"])
    assert w.order() == ["A01", "B02"]


def test_click_appends_then_removes_and_renumbers(qapp):
    w = PlateOrderEditor()
    w.resize(480, 320)
    seen: list[list[str]] = []
    w.orderChanged.connect(seen.append)

    _click_well(w, "A01")
    _click_well(w, "C05")
    _click_well(w, "B03")
    assert w.order() == ["A01", "C05", "B03"]
    assert seen[-1] == ["A01", "C05", "B03"]
    assert w.is_dirty()

    # Clicking an on-path well removes it; the rest keep their relative order.
    _click_well(w, "C05")
    assert w.order() == ["A01", "B03"]
    assert seen[-1] == ["A01", "B03"]


def test_preview_ignored_once_dirty(qapp):
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_preview(["A01", "A02"])       # non-dirty preview shows through
    assert w.order() == ["A01", "A02"]
    assert not w.is_dirty()

    _click_well(w, "H12")               # user takes ownership
    assert w.is_dirty()
    w.set_preview(["A01", "A02", "A03"])   # ignored now
    assert w.order() == ["A01", "A02", "H12"]


def test_clear_resets_and_emits(qapp):
    w = PlateOrderEditor()
    w.set_order(["A01", "B02"])
    emitted: list[list[str]] = []
    w.orderChanged.connect(emitted.append)
    w.clear()
    assert w.order() == []
    assert not w.is_dirty()
    assert emitted[-1] == []


def test_paint_runs_with_path(qapp):
    from PySide6.QtGui import QImage

    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_order(["A01", "C05", "B03", "H12"])   # >=2 wells → arrow path drawn
    img = QImage(480, 320, QImage.Format_ARGB32)
    w.render(img)   # must not crash exercising the number + arrow paint path
    assert not img.isNull()
