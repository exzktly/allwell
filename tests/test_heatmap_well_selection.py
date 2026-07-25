"""Well selection must keep working on the Heat Map tab.

Two independent defects made selecting and deselecting wells impossible there:

1. The Heat Map tab turns the sidebar plate into a WELL_MIME drag source so the
   user can drag wells into the layout grid. The drag source consumed the whole
   press, so a click on the plate neither selected nor deselected a well — and
   "Auto-populate from selection" had no way to be given a selection.
2. The heat-map canvas read the click's keyboard modifiers with
   ``int(gui_event.modifiers())``. PySide6 exposes them as a ``KeyboardModifier``
   flag object that ``int()`` rejects, so the ``except`` branch fired on every
   click and Shift / Ctrl were always seen as unheld: Ctrl-click could never
   toggle a well out of the selection.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from well_viewer.core.dnd import WELL_MIME
from well_viewer.features.heatmap.controller import _event_modifiers
from widgets.well_plate_selector import WellPlateSelector


# ── the sidebar plate stays a well picker while it is a drag source ──────────

def _plate(qtbot) -> WellPlateSelector:
    w = WellPlateSelector()
    qtbot.addWidget(w)
    w.resize(600, 420)
    return w


def _mouse_event(kind, pos: QPointF) -> QMouseEvent:
    return QMouseEvent(kind, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def _click_well(plate: WellPlateSelector, row: int, col: int) -> None:
    """Press and release on the centre of well ``(row, col)`` without moving."""
    grid = plate._grid
    pos = grid._cell_rect(row, col).center()
    grid.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, pos))
    grid.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, pos))


def test_click_selects_while_plate_is_a_drag_source(qtbot):
    plate = _plate(qtbot)
    plate.setDragMime(WELL_MIME)

    _click_well(plate, 0, 0)

    assert plate.selectedWellIds() == ["A01"]


def test_click_deselects_while_plate_is_a_drag_source(qtbot):
    plate = _plate(qtbot)
    plate.setSelectedWellIds(["A01", "B02"])
    plate.setDragMime(WELL_MIME)

    _click_well(plate, 0, 0)

    assert plate.selectedWellIds() == ["B02"]


def test_drag_source_click_still_commits_the_selection(qtbot):
    """The click must emit ``selectionDragFinished`` like a press-driven one.

    That signal is what the app turns into ``on_plate_sel_change`` — without it
    the plate would repaint but the plots would never see the new selection.
    """
    plate = _plate(qtbot)
    plate.setDragMime(WELL_MIME)

    with qtbot.waitSignal(plate.selectionDragFinished, timeout=1000):
        _click_well(plate, 2, 3)

    assert plate.selectedWellIds() == ["C04"]


def test_passive_plate_click_activates_while_a_drag_source(qtbot):
    """In group mode the click emits ``wellActivated`` instead of toggling."""
    plate = _plate(qtbot)
    plate.setSelectionMode("passive")
    plate.setDragMime(WELL_MIME)

    with qtbot.waitSignal(plate.wellActivated, timeout=1000) as blocker:
        _click_well(plate, 1, 1)

    assert blocker.args == ["B02"]
    assert plate.selectedWellIds() == []


def test_press_that_becomes_a_drag_does_not_select(qtbot):
    """Once the QDrag starts, the release must not fall back to a click."""
    plate = _plate(qtbot)
    plate.setDragMime(WELL_MIME)
    grid = plate._grid
    pos = grid._cell_rect(0, 0).center()

    grid.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, pos))
    # Stand in for the QDrag hand-off mouseMoveEvent performs; a real drag
    # blocks in ``QDrag.exec`` and clears the armed cell exactly like this.
    grid._drag_press_pos = None
    grid._drag_press_cell = None
    grid.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, pos))

    assert plate.selectedWellIds() == []


def test_click_still_selects_when_not_a_drag_source(qtbot):
    """The non-drag path (every other tab) is unchanged."""
    plate = _plate(qtbot)

    _click_well(plate, 0, 0)

    assert plate.selectedWellIds() == ["A01"]


def test_row_header_click_unaffected_by_drag_mime(qtbot):
    plate = _plate(qtbot)
    plate.setDragMime(WELL_MIME)
    grid = plate._grid
    _lab, cell, ox, oy = grid._metrics()
    pos = QPointF(ox - cell / 2.0, oy + cell / 2.0)

    grid.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, pos))
    grid.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, pos))

    assert len(plate.selectedWellIds()) == 12  # the whole of row A


# ── heat-map canvas modifiers ────────────────────────────────────────────────

class _FakeGuiEvent:
    def __init__(self, flags):
        self._flags = flags

    def modifiers(self):
        return self._flags


class _FakeMplEvent:
    def __init__(self, *, gui_event=None, key=None):
        self.guiEvent = gui_event
        self.key = key


def test_modifiers_read_from_qt_flag_object():
    """The PySide6 flag object is what a real click carries."""
    assert _event_modifiers(
        _FakeMplEvent(gui_event=_FakeGuiEvent(Qt.ShiftModifier))
    ) == (True, False)
    assert _event_modifiers(
        _FakeMplEvent(gui_event=_FakeGuiEvent(Qt.ControlModifier))
    ) == (False, True)
    assert _event_modifiers(
        _FakeMplEvent(gui_event=_FakeGuiEvent(Qt.ShiftModifier | Qt.ControlModifier))
    ) == (True, True)
    assert _event_modifiers(
        _FakeMplEvent(gui_event=_FakeGuiEvent(Qt.NoModifier))
    ) == (False, False)


def test_int_on_qt_modifiers_still_raises():
    """Pin the reason the old ``int(...)`` read silently reported "no modifier"."""
    with pytest.raises(TypeError):
        int(Qt.ShiftModifier | Qt.ControlModifier)


def test_modifiers_fall_back_to_the_matplotlib_key_state():
    assert _event_modifiers(_FakeMplEvent(key="shift")) == (True, False)
    assert _event_modifiers(_FakeMplEvent(key="control")) == (False, True)
    assert _event_modifiers(_FakeMplEvent(key="ctrl+shift")) == (True, True)
    assert _event_modifiers(_FakeMplEvent(key="cmd")) == (False, True)
    assert _event_modifiers(_FakeMplEvent(key=None)) == (False, False)
    assert _event_modifiers(_FakeMplEvent()) == (False, False)
