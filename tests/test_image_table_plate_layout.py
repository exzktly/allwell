"""Image Table plate (96-well) layout: well_row × well_col virtual dims.

Covers the "show each well's image in true plate positions (rows A–H, columns
1–12), correctly labelled" feature. The plate axes are two *virtual* dims that
never get their own render-model cell key — the layout composes them into the
real ``well`` token, and the facet labeller derives them back from that well for
the A–H / 1–12 axis headers. These tests pin:

* the per-dim value lists (``well_row`` → letters, ``well_col`` → numbers),
* well composition (resolve + blank-on-miss),
* the facet label derivation + the corner suppression,
* the builder-spec ``plate`` round-trip.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402
from well_viewer.features.image_browser.image_table.build_layout import (  # noqa: E402
    image_table_apply_spec,
)
from well_viewer.features.image_browser.image_table import facet_view as F  # noqa: E402
from well_viewer.features.image_browser.image_table import grid_layout as G  # noqa: E402
from well_viewer.features.image_browser.image_table.state import (  # noqa: E402
    Axis, ChannelSpec, ImageTableState, TableSpec,
    spec_from_state, spec_to_state_fields,
)


def _fake_app(*, wells, channels=("gfp",), active_wells=None):
    info = {"available_timepoints": [], "available_fovs": [], "nuclear_token": ""}
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(active_wells=set(active_wells or [])),
    )
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state, runtime=AppRuntime(app),
                               notify=SimpleNamespace(set_status=lambda *a, **k: None))
    return app


# ── value lists ──────────────────────────────────────────────────────────────

def test_plate_row_and_col_options_span_the_selection():
    app = _fake_app(wells=["A01", "A12", "B01", "H06"])
    assert G._axis_values(app._ctx, "well_row") == ["A", "B", "H"]
    assert G._axis_values(app._ctx, "well_col") == ["1", "6", "12"]


def test_plate_options_respect_the_plate_selection():
    # active_wells narrows both plate axes (a partial plate stays a partial plate)
    app = _fake_app(wells=["A01", "A02", "B01", "B02", "C03"],
                    active_wells=["A01", "B02"])
    assert G._axis_values(app._ctx, "well_row") == ["A", "B"]
    assert G._axis_values(app._ctx, "well_col") == ["1", "2"]


# ── well composition ─────────────────────────────────────────────────────────

def test_compose_plate_well_resolves_row_col_to_the_real_token():
    app = _fake_app(wells=["A01", "B05", "H12"])
    idx = G.plate_well_index(app._ctx)
    vals = {"well_row": "B", "well_col": "5", "channel": "GFP"}
    G.compose_plate_well(vals, idx)
    assert vals["well"] == "B05"


def test_compose_plate_well_blanks_a_missing_position():
    app = _fake_app(wells=["A01", "B05"])  # no C07 in the dataset
    idx = G.plate_well_index(app._ctx)
    vals = {"well_row": "C", "well_col": "7"}
    G.compose_plate_well(vals, idx)
    assert vals["well"] == ""


def test_fixed_off_axis_does_not_pin_a_well_in_plate_mode():
    app = _fake_app(wells=["A01", "B02"], active_wells=["A01", "B02"])
    fixed = G._fixed_off_axis(app._ctx, {"well_row", "well_col"})
    # well is composed per cell, so no fallback well is pinned
    assert fixed["well"] == ""


# ── end-to-end layout: a plate grid of the right shape + wells ───────────────

def test_build_plate_layout_fills_a_plate_shaped_grid():
    wells = [f"{r}{c:02d}" for r in "AB" for c in (1, 2, 3)]  # A01..B03, 2×3
    app = _fake_app(wells=wells, active_wells=wells)
    st = app._app_state.image_table
    st.row_dims = ["well_row"]
    st.col_dims = ["well_col"]
    st.active_channels = {"GFP"}
    st.fixed_vals = {"channel": "GFP"}
    C.image_table_rebuild_grid(app._ctx)  # placeholder grid (defaults 2×3)
    C._build_nested_layout(app._ctx, ["well_row"], ["well_col"])
    cells = app._ctx.runtime.image_table.cells
    assert len(cells) == 2 and len(cells[0]) == 3
    got = [[cell["well"] for cell in row] for row in cells]
    assert got == [["A01", "A02", "A03"], ["B01", "B02", "B03"]]
    # every cell carries the fixed channel
    assert all(cell["chan"] == "GFP" for row in cells for cell in row)


def test_apply_plate_spec_builds_the_plate_grid():
    # the path the "Plate" toggle drives: apply a plate TableSpec end to end
    wells = [f"{r}{c:02d}" for r in "AB" for c in (1, 2)]  # A01,A02,B01,B02
    app = _fake_app(wells=wells, active_wells=wells)
    C.image_table_rebuild_grid(app._ctx)
    spec = TableSpec(
        rows=Axis(root=None, channels=[ChannelSpec("gfp", "corrected", colormap="Greens")]),
        cols=Axis(root="timepoint"),
        plate=True,
    )
    image_table_apply_spec(app._ctx, spec, render=False)
    st = app._app_state.image_table
    assert st.row_dims == ["well_row"] and st.col_dims == ["well_col"]
    assert st.group_size == 0
    cells = app._ctx.runtime.image_table.cells
    got = [[cell["well"] for cell in row] for row in cells]
    assert got == [["A01", "A02"], ["B01", "B02"]]
    assert all(cell["chan"] == "GFP" for row in cells for cell in row)


# ── facet labels ─────────────────────────────────────────────────────────────

def test_facet_cv_derives_plate_row_col_from_the_well():
    cell = {"well": "C09", "chan": "GFP", "tp": "", "fov": ""}
    assert F._cv(cell, "well_row") == "C"
    assert F._cv(cell, "well_col") == "9"


def test_plan_facets_labels_plate_axes_and_omits_the_well_corner():
    app = _fake_app(wells=["A01", "A02", "B01", "B02"])
    st = app._app_state.image_table
    st.row_dims = ["well_row"]
    st.col_dims = ["well_col"]
    cells = [
        [{"well": "A01", "chan": "GFP", "tp": "", "fov": ""},
         {"well": "A02", "chan": "GFP", "tp": "", "fov": ""}],
        [{"well": "B01", "chan": "GFP", "tp": "", "fov": ""},
         {"well": "B02", "chan": "GFP", "tp": "", "fov": ""}],
    ]
    plan = F.plan_facets(app._ctx, cells)
    assert plan is not None
    top = [t[0] for t in plan.top]     # column headers
    left = [t[0] for t in plan.left]   # row headers
    assert top == ["1", "2"]
    assert left == ["A", "B"]
    # the composed well varies per cell, so it must NOT show as a fixed corner
    assert "A01" not in plan.corner


# ── builder-spec round-trip ──────────────────────────────────────────────────

def test_plate_spec_flattens_to_well_row_col_axes_and_fixed_channel():
    spec = TableSpec(
        rows=Axis(root=None, channels=[ChannelSpec("gfp", "corrected", colormap="Greens")]),
        cols=Axis(root="timepoint"),
        plate=True,
    )
    fields = spec_to_state_fields(spec)
    assert fields["row_dims"] == ["well_row"]
    assert fields["col_dims"] == ["well_col"]
    assert fields["group_size"] == 0
    assert fields["fixed_vals"]["channel"] in fields["active_channels"]


def test_plate_spec_round_trips_through_state():
    st = ImageTableState(
        row_dims=["well_row"], col_dims=["well_col"],
        active_channels={"gfp"},
        channel_opts={"gfp": ChannelSpec("gfp", "corrected", colormap="Greens").to_dict()},
        fixed_vals={"channel": "gfp"},
    )
    spec = spec_from_state(st)
    assert spec.plate is True
    assert [c.channel for c in spec.rows.channels] == ["gfp"]
    # and flattening it back reproduces the plate axes
    assert spec_to_state_fields(spec)["row_dims"] == ["well_row"]


# ── plate mode must yield to a manual row/col drag ───────────────────────────

def test_dropping_on_an_axis_exits_plate_mode():
    """Regression: with Plate mode on, dragging a root/channel onto Rows or
    Columns was silently discarded — ``spec_to_state_fields`` kept forcing
    well_row/well_col — so the user's row/column spec was ignored. A drop onto an
    axis is a manual layout and must turn Plate off so it takes effect."""
    from well_viewer.features.image_browser.image_table import builder_view as B

    app = _fake_app(wells=["A01", "A02", "B01", "B02"],
                    channels=("gfp", "mcherry"),
                    active_wells=["A01", "A02", "B01", "B02"])
    ctx = app._ctx

    # Enter plate mode; the axes flatten to the virtual plate dims.
    spec = B.builder_spec(ctx)
    B._set_plate(ctx, True)
    image_table_apply_spec(ctx, B.builder_spec(ctx), render=False)
    assert B.builder_spec(ctx).plate is True
    assert ctx.state.image_table.row_dims == ["well_row"]
    assert ctx.state.image_table.col_dims == ["well_col"]

    # Now specify a manual layout: channels down rows, wells across columns.
    B._on_zone_drop(ctx, "cols", "well")
    B._on_zone_drop(ctx, "rows", "newchan:gfp")
    image_table_apply_spec(ctx, B.builder_spec(ctx), render=False)

    # Plate mode is off and the manual axes are honored.
    assert B.builder_spec(ctx).plate is False
    assert ctx.state.image_table.row_dims == ["channel"]
    assert ctx.state.image_table.col_dims == ["well"]


def test_dropping_a_root_on_fixed_leaves_plate_mode_untouched():
    """Only an axis (rows/cols) drop cancels plate mode — pinning a root under
    Fixed does not (plate already auto-derives the fixed dims)."""
    from well_viewer.features.image_browser.image_table import builder_view as B

    app = _fake_app(wells=["A01", "B02"], channels=("gfp",), active_wells=["A01", "B02"])
    ctx = app._ctx
    B.builder_spec(ctx)
    B._set_plate(ctx, True)
    B._on_zone_drop(ctx, "fixed", "timepoint")
    assert B.builder_spec(ctx).plate is True
