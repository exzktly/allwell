"""Batch Export's plate map is the app's plate control, not a button grid.

It used to be 96 flat 28x22px ``QPushButton``s laid out in a ``QGridLayout``,
each one's ``mousePressEvent`` / ``mouseMoveEvent`` / ``mouseReleaseEvent``
reassigned to the panel's own methods — enabled buttons swallow mouse events, so
the container's handlers never fired and per-button forwarding was the only way
to get a drag across the plate. Rectangles on a grid do not read as a plate, and
it was the one well grid in the app sharing nothing with the others.

It is now a ``WellPlateSelector`` in the ``"paint"`` selection model: the plate
reports the gesture and the panel applies it to the active group. These cover
the three things that swap had to preserve — the add/remove polarity latched on
the press, the group colours and disabled wells reaching the plate, and the
active group's own wells staying distinguishable from the rest.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _panel(app):
    from well_viewer.features.batch_export import BarBatchExportPanel
    return BarBatchExportPanel(app._ctx, None, use_sidebar_groups=False)


def _grid(panel):
    return panel._map_plate._grid


def test_the_plate_map_is_a_well_plate_selector(loaded_app):
    from widgets.well_plate_selector import WellPlateSelector

    panel = _panel(loaded_app)
    assert isinstance(panel._map_plate, WellPlateSelector)
    # It owns no selection of its own — group membership is the model, and it
    # is not a set of clicked wells (one well can pull in a replicate set).
    assert _grid(panel).selection_model() == "paint"
    # Headers stay inert: the panel's only row/column action replaces every
    # group, so a stray click on the plate's edge must not reach it.
    assert not _grid(panel)._rc_selectable


def test_a_press_adds_and_a_press_on_an_owned_well_removes(loaded_app):
    """The gesture's direction is latched by the well it started on, so one
    drag adds or removes throughout rather than toggling under the cursor."""
    from well_viewer.features.batch_export._common import BarGroup

    panel = _panel(loaded_app)
    well = next(iter(loaded_app._app_state.dataset.well_paths))
    panel._groups.append(BarGroup("G1"))
    panel._active_grp = 0

    panel._plate_press(well)
    assert well in panel._groups[0].wells
    assert panel._drag_adding is True

    panel._plate_press(well)
    assert well not in panel._groups[0].wells
    assert panel._drag_adding is False


def test_a_well_outside_the_dataset_is_ignored(loaded_app):
    """The plate shows all 96 positions; only the ones the dataset actually
    has may join a group."""
    from well_viewer.features.batch_export._common import BarGroup

    panel = _panel(loaded_app)
    panel._groups.append(BarGroup("G1"))
    panel._active_grp = 0
    absent = next(f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)
                  if f"{r}{c:02d}" not in loaded_app._app_state.dataset.well_paths)
    panel._plate_press(absent)
    assert panel._groups[0].wells == []


def test_group_colours_and_the_active_ring_reach_the_plate(loaded_app):
    """Which group a well is in is its colour; which group you are *editing*
    is a ring on top, because those are two different questions and colour is
    already answering the first."""
    from PySide6.QtGui import QColor
    from widgets.well_plate_selector import _parse_well_id
    from well_viewer.features.batch_export._common import BarGroup

    panel = _panel(loaded_app)
    wells = list(loaded_app._app_state.dataset.well_paths)[:2]
    panel._groups.append(BarGroup("G1", solo_wells=[wells[0]]))
    panel._groups.append(BarGroup("G2", solo_wells=[wells[1]]))
    panel._active_grp = 1
    panel._refresh_map()

    grid = _grid(panel)
    assert grid._colors[_parse_well_id(wells[0])] == QColor(panel._group_color(0))
    assert grid._colors[_parse_well_id(wells[1])] == QColor(panel._group_color(1))
    # Only the active group is ringed.
    assert grid._rings == {_parse_well_id(wells[1])}


def test_wells_the_dataset_lacks_are_disabled_not_hand_tinted(loaded_app):
    """The plate already draws an absent well as an empty socket, and a
    disabled well ignores the paint gesture for free."""
    from widgets.well_plate_selector import _parse_well_id

    panel = _panel(loaded_app)
    panel._refresh_map()
    grid = _grid(panel)
    have = set(loaded_app._app_state.dataset.well_paths)
    assert grid._enabled == {_parse_well_id(w) for w in have}


def test_the_plate_is_capped_so_it_cannot_eat_the_group_list(loaded_app):
    """``role="rail"`` is exempt from the size caps because Review's left-rail
    picker is the app's primary interaction surface and scales to whatever
    width the rail gives it. This plate sits in a panel above the group list;
    uncapped it reserves a card as tall as ``heightForWidth`` allows (389px
    against the old button grid's 212) and takes that room from the list."""
    from widgets.plate_geometry import PLATE_MAX_HEIGHT, PLATE_MAX_WIDTH

    panel = _panel(loaded_app)
    assert panel._map_plate.maximumWidth() == PLATE_MAX_WIDTH
    assert panel._map_plate.maximumHeight() == PLATE_MAX_HEIGHT


def test_a_dataset_with_no_wells_leaves_every_well_disabled(loaded_app):
    """``setEnabledWells(None)`` means "all 96 enabled", so passing
    ``sorted(avail) or None`` turned an empty dataset into a full plate of
    live-looking wells that silently reject every click."""
    panel = _panel(loaded_app)
    panel._ctx.state.dataset.well_paths.clear()
    panel._refresh_map()
    assert _grid(panel)._enabled == set()


def test_the_ring_follows_a_well_out_of_the_active_group_mid_drag(loaded_app):
    """The ring says "in the group being edited", so it has to move with the
    colour. Left to the end-of-gesture refresh, a well dragged out of the
    active group sits ringed but uncoloured for the rest of the drag."""
    from widgets.well_plate_selector import _parse_well_id
    from well_viewer.features.batch_export._common import BarGroup

    panel = _panel(loaded_app)
    well = next(iter(loaded_app._app_state.dataset.well_paths))
    panel._groups.append(BarGroup("G1", solo_wells=[well]))
    panel._active_grp = 0
    panel._refresh_map()
    assert _parse_well_id(well) in _grid(panel)._rings

    panel._plate_press(well)        # starts a remove gesture, mid-drag repaint
    assert _parse_well_id(well) not in _grid(panel)._rings
    assert _parse_well_id(well) not in _grid(panel)._colors


def test_a_real_drag_gesture_reaches_the_group(loaded_app):
    """End to end through the plate's own mouse handling, not the panel's
    slots: press, drag across two more wells, release."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from widgets.well_plate_selector import _parse_well_id
    from well_viewer.features.batch_export._common import BarGroup

    panel = _panel(loaded_app)
    panel._map_plate.resize(480, 320)
    wells = [w for w in loaded_app._app_state.dataset.well_paths][:3]
    if len(wells) < 3:
        import pytest
        pytest.skip("fixture dataset has fewer than three wells")
    panel._groups.append(BarGroup("G1"))
    panel._active_grp = 0
    panel._refresh_map()

    grid = _grid(panel)

    def _ev(well, kind, buttons=Qt.LeftButton):
        row, col = _parse_well_id(well)
        return QMouseEvent(kind, QPointF(grid._cell_rect(row, col).center()),
                           Qt.LeftButton, buttons, Qt.NoModifier)

    grid.mousePressEvent(_ev(wells[0], QMouseEvent.Type.MouseButtonPress))
    grid.mouseMoveEvent(_ev(wells[1], QMouseEvent.Type.MouseMove))
    grid.mouseMoveEvent(_ev(wells[2], QMouseEvent.Type.MouseMove))
    grid.mouseReleaseEvent(_ev(wells[2], QMouseEvent.Type.MouseButtonRelease,
                               buttons=Qt.NoButton))

    assert set(panel._groups[0].wells) >= set(wells)
