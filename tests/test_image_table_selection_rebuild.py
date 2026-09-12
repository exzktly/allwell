"""Image Table: changing the sidebar well selection must rebuild the grid.

Regression for the "3 wells selected but only a single column" bug: selecting
wells (or Select All / Clear) updated ``active_wells`` and the plate display but
never re-ran the layout, so a Wells row/column axis stayed frozen at the
selection it had when the shelf was last edited.
"""
import pytest

pytest.importorskip("PySide6")

from review.features.image_browser.image_table import builder_view as B
from review.features.image_browser.image_table import controller as C
from review.features.image_browser.image_table.build_layout import (
    image_table_apply_spec,
)


def _cols(ctx):
    cells = ctx.runtime.image_table.cells
    return len(cells[0]) if cells else 0


def _flush(ctx):
    # Land the debounced rebuild synchronously (no Qt event loop in the test).
    B.builder_flush_pending(ctx)


def _build_channels_x_wells(ctx):
    """Author 'channels down rows, wells across columns' via the real shelf."""
    ctx.runtime.image_table.builder_spec = None
    B.builder_spec(ctx)
    B._on_zone_drop(ctx, "cols", "well")          # drag the Well root onto columns
    image_table_apply_spec(ctx, B.builder_spec(ctx), render=False)


def test_selection_change_rebuilds_wells_axis(image_table_app):
    ctx = image_table_app._ctx
    st = ctx.state.image_table
    plate = ctx.runtime.image_table.sidebar_plate
    allw = sorted(ctx.state.dataset.well_paths.keys())
    assert len(allw) >= 3

    # Build the table with a single well selected → one well column.
    plate.setSelectedWellIds([allw[0]])
    _build_channels_x_wells(ctx)
    assert _cols(ctx) == 1

    # Now select three wells in the sidebar. The grid must follow — not stay at 1.
    plate.setSelectedWellIds(allw[:3])
    assert st.active_wells == set(allw[:3])
    _flush(ctx)
    assert _cols(ctx) == 3, "Wells axis did not track the sidebar selection"


def test_select_all_and_clear_rebuild(image_table_app):
    ctx = image_table_app._ctx
    plate = ctx.runtime.image_table.sidebar_plate
    allw = sorted(ctx.state.dataset.well_paths.keys())

    plate.setSelectedWellIds([allw[0]])
    _build_channels_x_wells(ctx)
    assert _cols(ctx) == 1

    C.image_table_select_all(ctx)
    _flush(ctx)
    assert _cols(ctx) == len(allw)

    C.image_table_clear_active(ctx)     # empty selection ⇒ all wells enumerate
    _flush(ctx)
    assert _cols(ctx) == len(allw)


def test_rebuild_is_noop_before_shelf_spec_exists(image_table_app):
    ctx = image_table_app._ctx
    ctx.runtime.image_table.builder_spec = None
    # Must not raise or build anything when no spec has been authored yet.
    B.schedule_selection_rebuild(ctx)
    _flush(ctx)
