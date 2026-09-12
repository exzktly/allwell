"""Image Table: grouped / tiled well layout.

A group size wraps the Columns axis into vertically-stacked bands, repeating the
Rows axis per band; the per-row LUT/colormap collapses to one shared row per
row-axis value (channel) across all bands. The band math is tested directly; the
build + shared-LUT wiring is exercised through the controller.
"""
from __future__ import annotations

import math

import pytest

from review.features.image_browser.image_table.grid_layout import (
    _layout_cell_values,
    banded_indices,
)


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


def test_banded_indices_is_the_shared_band_math():
    # Both layout paths (flat and nested) resolve a cell through this one helper,
    # so they band identically: 2 rows per band, 6 columns per band.
    assert banded_indices(0, 0, 2, 6) == (0, 0)
    assert banded_indices(1, 5, 2, 6) == (1, 5)
    assert banded_indices(2, 0, 2, 6) == (0, 6)     # band 1 starts at column 6
    assert banded_indices(3, 5, 2, 6) == (1, 11)
    assert banded_indices(4, 2, 2, 6) == (0, 14)    # band 2, past a 14-value axis


# ── Nested (compound) axes band too ──────────────────────────────────────────
#
# The reported defect: with FOV on the rows (channels nested under it) and Wells
# across the columns, Wrap cols was disabled and — had it been reachable — the
# nested layout dropped the wrap on the floor. The column *product* bands exactly
# like a single dim's values, so these drive the real builder path.


def _nested_ctx(*, wells, channels, timepoints=None, fovs=None):
    """A lightweight ctx for the layout builders: dataset dims + an empty grid.

    No dataset and no tab build — the Qt grid rebuild is stubbed with plain dict
    cells, which is all ``_build_nested_layout`` writes into."""
    from types import SimpleNamespace
    from review.core.app_runtime import AppRuntime
    from review.features.image_browser.image_table.state import ImageTableState

    info = {"available_timepoints": timepoints or [], "available_fovs": fovs or [],
            "nuclear_token": ""}
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(active_wells=set(wells)),
        image_view=None,
    )
    app._parse_rc = lambda w: (w[0], int(w[1:]))
    ctx = SimpleNamespace(app=app, state=app._app_state, runtime=AppRuntime(app),
                          notify=SimpleNamespace(set_status=lambda _s: None))
    app._ctx = ctx
    return ctx


class _GridStub:
    """Stands in for the controller's Qt grid rebuild + render."""

    @staticmethod
    def image_table_rebuild_grid(ctx):
        st = ctx.state.image_table
        ctx.runtime.image_table.cells = [[{} for _ in range(st.cols)]
                                         for _ in range(st.rows)]

    @staticmethod
    def image_table_generate(ctx):
        pass


@pytest.fixture
def _stub_grid(monkeypatch):
    from review.features.image_browser.image_table import build_layout as BL
    monkeypatch.setattr(BL, "_ctrl", lambda: _GridStub)
    return BL


def _reported_spec(wrap):
    """The layout from the report: rows = FOV › Channel, cols = Well."""
    from review.features.image_browser.image_table.state import (
        Axis, ChannelSpec, TableSpec,
    )
    return TableSpec(
        rows=Axis(root="fov", channels=[ChannelSpec("GFP"), ChannelSpec("RFP")]),
        cols=Axis(root="well"),
        wrap=wrap,
    )


def test_wrap_bands_a_nested_axis_layout(_stub_grid):
    # 8 wells across the columns, 2 FOVs × 2 channels down the rows, wrap 3.
    wells = [f"A{i:02d}" for i in range(1, 9)]
    ctx = _nested_ctx(wells=wells, channels=["gfp", "rfp"], timepoints=["T01"],
                      fovs=["1", "2"])
    _stub_grid.image_table_apply_spec(ctx, _reported_spec(3), render=False)

    st = ctx.state.image_table
    cells = ctx.runtime.image_table.cells
    # 3 bands of 3 columns; the 4-row FOV×channel product repeats per band.
    assert (st.rows, st.cols) == (12, 3)
    assert st.group_size == 3 and st.group_row_span == 4
    assert (cells[0][0]["fov"], cells[0][0]["chan"], cells[0][0]["well"]) == ("1", "GFP", "A01")
    assert cells[3][2]["well"] == "A03"      # end of band 0
    assert cells[4][0]["well"] == "A04"      # band 1 resumes the well axis
    assert cells[4][0]["chan"] == "GFP" and cells[4][0]["fov"] == "1"
    assert cells[8][1]["well"] == "A08"      # band 2 holds the last two wells
    # The ragged tail is blank, not a repeat and not the held off-axis well.
    assert cells[8][2].get("well", "") == ""


def test_nested_wrap_zero_stays_flat(_stub_grid):
    wells = [f"A{i:02d}" for i in range(1, 9)]
    ctx = _nested_ctx(wells=wells, channels=["gfp", "rfp"], timepoints=["T01"],
                      fovs=["1", "2"])
    _stub_grid.image_table_apply_spec(ctx, _reported_spec(0), render=False)

    st = ctx.state.image_table
    assert (st.rows, st.cols) == (4, 8)
    assert st.group_size == 0 and st.group_row_span == 0


# ── Qt integration ───────────────────────────────────────────────────────────

pytestmark_qt = pytest.mark.qt


@pytest.mark.qt
def test_build_grouped_layout_and_shared_lut(image_table_app):
    from review.features.image_browser.image_table import controller as C
    from review.features.image_browser.image_table.grid_layout import _axis_values
    from review.features.image_browser.image_table.image_compose import _row_cmap_name
    from review.features.image_browser.image_table.export import _build_export_figure

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

    # The row axis (channel) repeats every band → group_row_span records it.
    assert ctx.state.image_table.group_row_span == n_ch

    # Per-channel display is keyed by channel token now (channel_opts), so every
    # band showing a channel resolves to the same colormap regardless of grid row.
    chan0 = cells[0][0]["chan"]
    ctx.state.image_table.channel_opts = {chan0: {"colormap": "Reds"}}
    assert _row_cmap_name(ctx, 0, chan0) == "Reds"
    if len(cells) > n_ch:  # a second band exists → same channel, same colormap
        assert cells[n_ch][0]["chan"] == chan0
        assert _row_cmap_name(ctx, n_ch, chan0) == "Reds"

    # Generate + export must not raise and the export mirrors the grid shape.
    # The figure also carries the facet label strips, so count only image cells.
    from review.features.image_browser.image_table.renderer import FACET_GID
    C.image_table_generate(ctx)
    fig, _ = _build_export_figure(ctx)
    cell_axes = [a for a in fig.axes if a.get_gid() != FACET_GID]
    assert len(cell_axes) == len(cells) * len(cells[0])


@pytest.mark.qt
def test_group_zero_is_flat_layout(image_table_app):
    from review.features.image_browser.image_table import controller as C

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)
    ctx.state.image_table.group_size = 0
    C.image_table_apply_preset(ctx, "channel", "well")

    # No grouping → group state cleared (flat one-value-per-row layout).
    assert ctx.state.image_table.group_row_span == 0
    assert ctx.state.image_table.group_size == 0
