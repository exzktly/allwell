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
