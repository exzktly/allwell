"""Image Table: grouped / tiled well layout.

A group size wraps the Columns axis into vertically-stacked bands, repeating the
Rows axis per band; the per-row LUT/colormap collapses to one shared row per
row-axis value (channel) across all bands. The band math is tested directly; the
build + shared-LUT wiring is exercised through the controller.
"""
from __future__ import annotations

import math

import pytest

from well_viewer.features.image_browser.image_table.grid_layout import _layout_cell_values


_FIXED = {"well": "", "channel": "", "timepoint": "0h", "fov": "1"}


def test_flat_layout_unchanged_without_group():
    # No group kwargs → straight row_vals[r] / col_vals[c] (today's behavior).
    rv, cv = ["GFP", "RFP"], ["A01", "A02", "A03"]
    v = _layout_cell_values("channel", "well", rv, cv, _FIXED, 1, 2)
    assert v["channel"] == "RFP" and v["well"] == "A03"
    assert v["timepoint"] == "0h" and v["fov"] == "1"


def test_grouped_bands_wrap_columns_and_repeat_rows():
    # 2 channels × 12 wells, groups of 6 → 4×6 banded grid.
    rv = ["GFP", "RFP"]
    cv = [f"A{i:02d}" for i in range(1, 13)]
    G, n = 6, 2

    def cell(r, c):
        return _layout_cell_values("channel", "well", rv, cv, _FIXED, r, c,
                                   n_row_vals=n, group_size=G)

    # Band 1 (rows 0-1) → wells A01..A06.
    assert cell(0, 0)["channel"] == "GFP" and cell(0, 0)["well"] == "A01"
    assert cell(1, 0)["channel"] == "RFP" and cell(1, 0)["well"] == "A01"
    assert cell(1, 5)["well"] == "A06"
    # Band 2 (rows 2-3) → wells A07..A12.
    assert cell(2, 0)["channel"] == "GFP" and cell(2, 0)["well"] == "A07"
    assert cell(3, 5)["channel"] == "RFP" and cell(3, 5)["well"] == "A12"


def test_ragged_final_band_blanks_missing_cells():
    # 14 wells, groups of 6 → 3 bands; the last band only fills A13, A14.
    rv = ["GFP", "RFP"]
    cv = [f"A{i:02d}" for i in range(1, 15)]
    G, n = 6, 2
    # Row 4 = band 2 (0-indexed), col 0 → well index 12 → A13.
    assert _layout_cell_values("channel", "well", rv, cv, _FIXED, 4, 0,
                               n_row_vals=n, group_size=G)["well"] == "A13"
    # Col 2 in that band → well index 14 → out of range → blank.
    assert _layout_cell_values("channel", "well", rv, cv, _FIXED, 4, 2,
                               n_row_vals=n, group_size=G)["well"] == ""


# ── Qt integration ───────────────────────────────────────────────────────────

pytestmark_qt = pytest.mark.qt


@pytest.mark.qt
def test_build_grouped_layout_and_shared_lut(image_table_app):
    from well_viewer.features.image_browser.image_table import controller as C
    from well_viewer.features.image_browser.image_table.grid_layout import _axis_values
    from well_viewer.features.image_browser.image_table.image_compose import _row_cmap_name
    from well_viewer.features.image_browser.image_table.export import _build_export_figure

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)

    # Rows = channels, Columns = wells.
    for which, axis in (("row", "channel"), ("col", "well")):
        cb = getattr(ctx.runtime.image_table, f"{which}_axis_cb", None)
        if cb is not None:
            cb.setCurrentIndex(cb.findData(axis))
        setattr(ctx.runtime.image_table, f"{which}_axis", axis)

    chans = _axis_values(ctx, "channel")[:12]
    wells = _axis_values(ctx, "well")
    n_ch = len(chans)
    if n_ch == 0 or len(wells) < 2:
        pytest.skip("dataset lacks channels/wells to exercise banding")

    # Group size 1 forces one well per band → as many bands as wells, proving the
    # banding + per-channel LUT sharing regardless of the dataset's well count.
    G = 1
    ctx.state.image_table.group_size = G
    C.image_table_build_layout(ctx)

    cells = ctx.runtime.image_table.cells
    expected_bands = min(math.ceil(len(wells) / G), max(1, 60 // n_ch))
    assert len(cells) == n_ch * expected_bands
    assert len(cells[0]) == G

    # One LUT row per channel (collapsed), shared across bands via lut_row_map.
    assert ctx.state.image_table.group_row_span == n_ch
    assert ctx.runtime.image_table.lut_row_map == [r % n_ch for r in range(len(cells))]
    assert len(ctx.runtime.image_table.lut) == n_ch

    # Set the first channel's colormap once; every band showing it resolves to it.
    ctx.runtime.image_table.row_lut_color_cbs[0].setLut("Reds")
    assert _row_cmap_name(ctx, 0) == "Reds"
    if len(cells) > n_ch:  # a second band exists → same channel, same colormap
        assert _row_cmap_name(ctx, n_ch) == "Reds"

    # Generate + export must not raise and the export mirrors the grid shape.
    C.image_table_generate(ctx)
    fig, _ = _build_export_figure(ctx)
    assert len(fig.axes) == len(cells) * len(cells[0])


@pytest.mark.qt
def test_group_zero_is_flat_layout(image_table_app):
    from well_viewer.features.image_browser.image_table import controller as C

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)
    ctx.state.image_table.group_size = 0
    C.image_table_apply_preset(ctx, "channel", "well")

    # No grouping → identity LUT map, group state cleared.
    cells = ctx.runtime.image_table.cells
    assert ctx.state.image_table.group_row_span == 0
    assert ctx.runtime.image_table.lut_row_map == list(range(len(cells)))
