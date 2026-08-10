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
    # Taking ownership replaces the preview rather than extending it — the
    # preview's wells were never chosen, and keeping them makes them positions
    # 1..n of the order the user is defining.
    assert w.order() == ["H12"]
    w.set_preview(["A01", "A02", "A03"])   # ignored now
    assert w.order() == ["H12"]


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


def test_set_order_and_set_preview_emit_nothing(qapp):
    """The page calls both on every keystroke in the grid fields; either one
    emitting would re-enter ``refresh_readout`` through ``orderChanged``.

    This is the contract the rewrite onto ``WellPlateSelector`` most risked —
    the shared widget has its own ``pathChanged``, and routing it straight to
    ``orderChanged`` would have made every programmatic assignment a signal."""
    w = PlateOrderEditor()
    emitted: list[list[str]] = []
    w.orderChanged.connect(emitted.append)
    w.set_order(["A01", "B02"])
    w.set_preview(["C03"])          # ignored (dirty), but must be silent either way
    assert emitted == []


def test_clear_on_an_already_clear_widget_emits_nothing(qapp):
    w = PlateOrderEditor()
    emitted: list[list[str]] = []
    w.orderChanged.connect(emitted.append)
    w.clear()
    assert emitted == []
    w.set_order(["A01"])
    w.clear()
    assert emitted == [[]]          # exactly one, for the clear that did something


def test_editing_the_order_drops_any_progress_dials(qapp):
    """A dial describes a conversion of the order that was on the plate when
    it ran; a different order makes it a claim about other wells."""
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_order(["A01", "A02"])
    w.setWellFraction("A01", 1.0)
    assert w._grid._fractions != {}

    _click_well(w, "H12")           # user edits the order
    assert w._grid._fractions == {}


# ── a preview is not a path ──────────────────────────────────────────────────


def test_the_first_click_replaces_a_preview_rather_than_extending_it(qapp):
    """The host draws its grid guess into the same path the user clicks into.

    Appending to it would make the guess's wells positions 1..n of an order the
    user believes they are defining from scratch, pushing every clicked well one
    place later — a conversion where each well receives the previous stage
    position's images, with no count on screen that looks wrong.
    """
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_preview(["B02"])
    assert w.order() == ["B02"]
    assert not w.is_dirty()

    _click_well(w, "C03")
    assert w.order() == ["C03"]
    assert w.is_dirty()

    # Ownership is taken once: from here the plate appends as it always has.
    _click_well(w, "C04")
    assert w.order() == ["C03", "C04"]


def test_taking_over_a_multi_well_preview_keeps_only_what_was_clicked(qapp):
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_preview(["A01", "A02", "A03"])

    _click_well(w, "H12")
    assert w.order() == ["H12"]


def test_clicking_a_previewed_well_starts_from_nothing(qapp):
    """The plate's click gesture removes an on-path well; on a preview that
    leaves an empty owned path, which is what taking over means."""
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_preview(["A01", "A02"])

    _click_well(w, "A01")
    assert w.order() == []
    assert w.is_dirty()


def test_an_order_the_host_set_is_appended_to_not_replaced(qapp):
    """`set_order` is the host handing over a real order (a restored job, or
    "Generate path from grid"), not a guess — clicks extend it."""
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_order(["A01", "A02"])

    _click_well(w, "H12")
    assert w.order() == ["A01", "A02", "H12"]


def test_clear_forgets_the_preview_so_the_next_takeover_is_clean(qapp):
    w = PlateOrderEditor()
    w.resize(480, 320)
    w.set_preview(["A01"])
    w.clear()
    w.set_preview(["B02"])

    _click_well(w, "C03")
    assert w.order() == ["C03"]
