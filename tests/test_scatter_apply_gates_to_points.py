"""The per-cell scatter's opt-in gate filter.

The per-cell scatter is the one view that does not filter by the scatter gates
drawn on it: it shows the full cloud with the boxes over it, so you can see and
resize what you selected. The **Apply gates** toggle opts in.

The filter computes membership *here*, from the gate geometry this plot is
drawing, rather than reading the ``Gate_Pass`` column. That column is written by
the Cell Gating tab's worker — asynchronously, and against its own gate
selection — so it can describe a different set of boxes than the ones on screen.
It did, and the visible result was a cloud in one corner and an empty box in
another: the toggle never recomputes the column, so a gate drawn or resized
since the last worker pass left it describing the old geometry; the column also
unions in gates drawn on *other* metric pairs, whose boxes this plot
deliberately hides.

Computing it here makes "the points you see are the points inside the boxes you
see" true by construction.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from review.features.scatter.collect import (
    collect_scatter_data,
    gate_filter_title_suffix,
    gates_for_axes,
    scatter_points_gated,
)

COL_X = "gfp_mean_intensity"
COL_Y = "mcherry_mean_intensity"


def _gate(x0=5.0, y0=5.0, x1=25.0, y1=25.0, *, x_col=COL_X, y_col=COL_Y,
          name="Gate 1"):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "x_col": x_col, "y_col": y_col, "name": name}


def _df(*, gate_pass=None):
    """Three cells at x/y = 10, 20, 30 — the first two inside ``_gate()``.

    ``gate_pass`` writes the Gate_Pass column; ``None`` omits it, the shape a
    dataset has before any gating pass has run.
    """
    n = 3
    cols = {
        "Included": [1] * n,
        "area_px": [200.0] * n,
        "timepoint_hours": [24.0] * n,
        "timepoint": ["24h"] * n,
        "filename": [f"img_{i}.tif" for i in range(n)],
        "nucleus_id": [str(i) for i in range(n)],
        COL_X: [10.0, 20.0, 30.0],
        COL_Y: [10.0, 20.0, 30.0],
    }
    if gate_pass is not None:
        cols["Gate_Pass"] = gate_pass
    return pd.DataFrame(cols)


def _ctx(label_to_df, *, apply_gates=False, scatter_gates=(),
         gate_draw_mode=False, gate_edit=None):
    from review.core.app_runtime import AppRuntime
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths=dict.fromkeys(label_to_df),
                                well_labels={}, cache=dict(label_to_df)),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=list(label_to_df), selections=[]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
        scatter=SimpleNamespace(apply_gates_to_points=apply_gates),
        cell_gating=SimpleNamespace(scatter_gates=list(scatter_gates)),
    )
    ctx = SimpleNamespace(
        app=app, state=app._app_state, runtime=AppRuntime(app),
        widgets=SimpleNamespace(get_or_none=lambda n: getattr(app, n, None)),
    )
    ctx.runtime.scatter.gate_draw_mode = gate_draw_mode
    ctx.runtime.scatter.gate_edit = gate_edit
    return ctx


def _xs(scatter_data):
    return sorted(x for d in scatter_data.values() for x in d["x"])


def _collect(ctx, **kw):
    return collect_scatter_data(ctx, COL_X, COL_Y, [24.0],
                                well_colors=["#ff0000"], **kw)


# ── the filter itself ───────────────────────────────────────────────────────

def test_no_gates_keeps_the_full_cloud():
    """The default for every caller other than the live tab — batch export and
    the CSV export service both rely on it."""
    ctx = _ctx({"B02": _df()})

    assert _xs(_collect(ctx)) == [10.0, 20.0, 30.0]
    assert _xs(_collect(ctx, gates=None)) == [10.0, 20.0, 30.0]
    assert _xs(_collect(ctx, gates=[])) == [10.0, 20.0, 30.0]


def test_gates_filter_to_the_cells_inside_the_boxes():
    ctx = _ctx({"B02": _df()})

    assert _xs(_collect(ctx, gates=[_gate()])) == [10.0, 20.0]


def test_a_stale_gate_pass_column_cannot_mislead_the_plot():
    """The regression. Gate_Pass here says the exact opposite of the geometry —
    the state you get when a gate has been drawn or moved since the last gating
    pass. The survivors must follow the box, not the column."""
    ctx = _ctx({"B02": _df(gate_pass=[0, 0, 1])})

    assert _xs(_collect(ctx, gates=[_gate()])) == [10.0, 20.0]


def test_several_gates_union():
    ctx = _ctx({"B02": _df()})
    low = _gate(0.0, 0.0, 15.0, 15.0, name="Gate 1")
    high = _gate(25.0, 25.0, 40.0, 40.0, name="Gate 2")

    assert _xs(_collect(ctx, gates=[low, high])) == [10.0, 30.0]


# ── which gates belong to these axes ────────────────────────────────────────

def test_gates_for_axes_keeps_only_this_metric_pair():
    """A gate's box is in the data space of its own columns; on another pair the
    coordinates are meaningless. Filtering has to use the same rule the drawing
    does, or the plot shows cells kept by a box it is not drawing."""
    mine = _gate(name="Gate 1")
    elsewhere = _gate(x_col="cy5_smfish_count", y_col="dapi_mean_intensity",
                      name="Gate 2")
    ctx = _ctx({}, scatter_gates=[mine, elsewhere])

    assert gates_for_axes(ctx, COL_X, COL_Y) == [mine]


def test_gates_for_axes_drops_an_unstamped_gate():
    """``compute_gate_inclusion`` skips a gate with no recorded columns, so
    including it would contribute an all-false mask and blank the plot. It is
    excluded here even though the drawing rule keeps it — a box drawn but not
    filtering beats an empty cloud."""
    unstamped = _gate(x_col=None, y_col=None)
    ctx = _ctx({"B02": _df()}, scatter_gates=[unstamped])

    assert gates_for_axes(ctx, COL_X, COL_Y) == []
    # …and the plot therefore keeps every point rather than losing them all.
    assert _xs(_collect(ctx, gates=gates_for_axes(ctx, COL_X, COL_Y))) == [
        10.0, 20.0, 30.0]


def test_gates_for_axes_is_empty_without_gates():
    assert gates_for_axes(_ctx({}), COL_X, COL_Y) == []


# ── when the toggle actually filters ────────────────────────────────────────

def test_gated_when_the_toggle_is_on():
    assert scatter_points_gated(_ctx({}, apply_gates=True)) is True
    assert scatter_points_gated(_ctx({}, apply_gates=False)) is False


def test_not_gated_while_a_gate_is_being_drawn():
    """Drawing a gate fans out to a recompute and a redraw. If the filter were
    live, every point outside the box would vanish the instant it was drawn —
    nothing left to judge the gate against, and the RectangleSelector rebuilding
    under the cursor."""
    assert scatter_points_gated(_ctx({}, apply_gates=True,
                                     gate_draw_mode=True)) is False


def test_not_gated_while_a_gate_edge_is_being_dragged():
    assert scatter_points_gated(_ctx({}, apply_gates=True,
                                     gate_edit={"idx": 0, "handle": "e"})) is False


def test_does_not_depend_on_the_cell_gating_tab():
    """The filter is computed from this plot's own boxes, so it does not consult
    ``use_scatter_gates`` — tying it to a second switch was one more way for the
    plot to disagree with itself."""
    ctx = _ctx({}, apply_gates=True)
    ctx.state.cell_gating.use_scatter_gates = False

    assert scatter_points_gated(ctx) is True


# ── the title tag ───────────────────────────────────────────────────────────

def test_title_suffix_tracks_the_filter():
    """A saved or copied figure has to carry the fact that it is filtered — the
    on-screen toggle does not travel into an SVG."""
    assert gate_filter_title_suffix(_ctx({}, apply_gates=True)) == " · gate-filtered"
    assert gate_filter_title_suffix(_ctx({}, apply_gates=False)) == ""
