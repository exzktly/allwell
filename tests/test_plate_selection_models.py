"""The shared plate's three selection models, and its ring decoration.

``WellPlateSelector`` is the app's only painted well grid, so the surfaces that
used to carry their own copies of it now drive it in the model that matches what
they are editing:

* ``"set"``   — the default, an unordered selection (Review's rails, Subset's
  picker, every dashboard's status map).
* ``"path"``  — an ordered acquisition route (Convert OME-TIFF), where a click
  appends an off-path well and removes an on-path one, and the visit numbers
  are positions in that list rather than a marker map a host has to keep in
  sync.
* ``"paint"`` — the grid owns nothing and reports the gesture (Batch Export),
  because export-group membership is not a set of clicked wells.

The ring is a third decoration channel alongside colour and selection, for a
host that must mark a subset of already-coloured wells without disturbing what
the colour is saying.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.well_plate_selector import WellPlateSelector, _parse_well_id  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _plate(model: str = "set") -> WellPlateSelector:
    p = WellPlateSelector()
    p.setSelectionModel(model)
    p.resize(480, 320)
    return p


def _event(grid, well_id: str, kind, buttons=Qt.LeftButton) -> QMouseEvent:
    row, col = _parse_well_id(well_id)
    centre = grid._cell_rect(row, col).center()
    return QMouseEvent(kind, QPointF(centre), Qt.LeftButton, buttons, Qt.NoModifier)


def _press(plate, well_id: str) -> None:
    plate._grid.mousePressEvent(_event(plate._grid, well_id,
                                       QMouseEvent.Type.MouseButtonPress))


def _drag_to(plate, well_id: str) -> None:
    plate._grid.mouseMoveEvent(_event(plate._grid, well_id,
                                      QMouseEvent.Type.MouseMove))


def _release(plate, well_id: str) -> None:
    plate._grid.mouseReleaseEvent(_event(plate._grid, well_id,
                                         QMouseEvent.Type.MouseButtonRelease,
                                         buttons=Qt.NoButton))


# ── the default is unchanged ─────────────────────────────────────────────────
def test_the_default_model_is_the_unordered_set(qapp):
    """Every existing host must be untouched by the two new models."""
    p = _plate()
    assert p._grid.selection_model() == "set"
    _press(p, "C03")
    assert p.selectedWellIds() == ["C03"]
    assert p.pathWellIds() == []


def test_an_unknown_model_is_ignored_rather_than_raising(qapp):
    p = _plate()
    p.setSelectionModel("nonsense")
    assert p._grid.selection_model() == "set"


# ── "path" ───────────────────────────────────────────────────────────────────
def test_a_path_click_appends_and_a_second_removes(qapp):
    p = _plate("path")
    seen: list[list[str]] = []
    p.pathChanged.connect(seen.append)

    _press(p, "A01")
    _press(p, "C05")
    _press(p, "B03")
    assert p.pathWellIds() == ["A01", "C05", "B03"]
    assert seen[-1] == ["A01", "C05", "B03"]

    _press(p, "C05")
    assert p.pathWellIds() == ["A01", "B03"]     # the rest keep their order


def test_a_path_click_does_not_touch_the_selection_set(qapp):
    """The two models are separate stores on purpose: the selection dict's
    insertion order is destroyed by the rank-colour recompute, so it cannot
    carry an order."""
    p = _plate("path")
    _press(p, "A01")
    _press(p, "B02")
    assert p.selectedWellIds() == []


def test_setting_a_path_canonicalises_dedupes_and_drops_junk(qapp):
    p = _plate("path")
    assert p.setPathWellIds(["a1", "B02", "a1", "not-a-well", "h12"]) is True
    assert p.pathWellIds() == ["A01", "B02", "H12"]
    # An unchanged assignment reports no change, so a host can skip a repaint.
    assert p.setPathWellIds(["A01", "B02", "H12"]) is False


def test_setting_a_path_emits_only_when_asked(qapp):
    p = _plate("path")
    seen: list[list[str]] = []
    p.pathChanged.connect(seen.append)
    p.setPathWellIds(["A01"])
    assert seen == []
    p.setPathWellIds(["A01", "A02"], emit=True)
    assert seen == [["A01", "A02"]]


def test_a_disabled_well_cannot_join_the_path(qapp):
    p = _plate("path")
    p.setEnabledWells(["A01", "A02"])
    _press(p, "H12")
    assert p.pathWellIds() == []


def test_the_corner_toggle_is_absent_outside_the_set_model(qapp):
    """It toggles the selection set, and neither other model has one."""
    assert _plate("set")._grid._corner_active() is True
    assert _plate("path")._grid._corner_active() is False
    assert _plate("paint")._grid._corner_active() is False


# ── "paint" ──────────────────────────────────────────────────────────────────
def test_paint_reports_the_gesture_and_owns_nothing(qapp):
    p = _plate("paint")
    pressed: list[str] = []
    dragged: list[str] = []
    finished: list[bool] = []
    p.wellPaintPressed.connect(pressed.append)
    p.wellPaintDragged.connect(dragged.append)
    p.paintDragFinished.connect(lambda: finished.append(True))

    _press(p, "B02")
    _drag_to(p, "B03")
    _drag_to(p, "B04")
    _drag_to(p, "B04")          # same well again — reported once
    _release(p, "B04")

    assert pressed == ["B02"]
    assert dragged == ["B03", "B04"]
    assert finished == [True]
    # Nothing became "selected": the host owns the membership.
    assert p.selectedWellIds() == []


def test_paint_does_not_claim_the_selection_settled(qapp):
    """``selectionDragFinished`` means "the selection stopped changing", and a
    paint gesture never changed one — anything listening for it (a redraw of
    the plots that follow the selection) must not fire."""
    p = _plate("paint")
    settled: list[bool] = []
    p.selectionDragFinished.connect(lambda: settled.append(True))
    _press(p, "B02")
    _release(p, "B02")
    assert settled == []


def test_a_disabled_well_starts_no_paint_gesture(qapp):
    p = _plate("paint")
    p.setEnabledWells(["A01"])
    pressed: list[str] = []
    p.wellPaintPressed.connect(pressed.append)
    _press(p, "H12")
    assert pressed == []


# ── rings ────────────────────────────────────────────────────────────────────
def test_rings_are_their_own_channel(qapp):
    p = _plate()
    p.setWellColor("A01", "#ff0000")
    p.setWellRing("A01")
    grid = p._grid
    key = _parse_well_id("A01")
    assert grid._rings == {key}
    assert key in grid._colors           # the colour is untouched
    p.setWellRing("A01", False)
    assert grid._rings == set()


def test_setting_the_whole_ring_set_replaces_it(qapp):
    p = _plate()
    p.setWellRings(["A01", "B02", "not-a-well"])
    assert p._grid._rings == {_parse_well_id("A01"), _parse_well_id("B02")}
    p.setWellRings([])
    assert p._grid._rings == set()
    p.setWellRings(["C03"])
    p.clearWellRings()
    assert p._grid._rings == set()


def test_painting_runs_with_every_decoration_at_once(qapp):
    """Rings, dials, a path and its arrows all draw in one pass."""
    from PySide6.QtGui import QImage

    p = _plate("path")
    p.setPathWellIds(["A01", "C05", "B03", "H12"])
    p.setWellRings(["A01", "H12"])
    p.setWellFraction("C05", 0.4)
    p.setWellColor("B03", "#3388ff")
    p.setEnabledWells([f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)][:60])
    img = QImage(480, 320, QImage.Format_ARGB32)
    p.render(img)
    assert not img.isNull()
