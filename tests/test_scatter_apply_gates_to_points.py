"""The per-cell scatter's opt-in gate filter.

The per-cell scatter is the one view that does not filter by the scatter gates
drawn on it: it shows the full cloud with the boxes over it, so you can see and
resize what you selected. Every other view composes ``Gate_Pass`` through
``df_included_mask``. That asymmetry is deliberate, but it was invisible and
unchangeable — a user checking whether "Use scatter-plot gates for cell
inclusion" had done anything would look at the scatter, see points outside the
box, and conclude the gating was broken.

``collect_scatter_data`` now takes ``include_gate`` (default ``False`` — today's
behaviour for every other caller), and the tab's **Apply gates** toggle opts in
via ``scatter_points_gated``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from well_viewer.features.scatter.collect import (
    collect_scatter_data,
    gate_filter_title_suffix,
    scatter_points_gated,
)


def _df(*, gate_pass=None):
    """Three cells, one well. ``gate_pass=None`` omits the column entirely —
    the intensity-mode shape, where the layer does not exist."""
    n = 3
    cols = {
        "Included": [1] * n,
        "area_px": [200.0] * n,
        "timepoint_hours": [24.0] * n,
        "timepoint": ["24h"] * n,
        "filename": [f"img_{i}.tif" for i in range(n)],
        "nucleus_id": [str(i) for i in range(n)],
        "gfp_mean_intensity": [10.0, 20.0, 30.0],
        "mcherry_mean_intensity": [11.0, 21.0, 31.0],
    }
    if gate_pass is not None:
        cols["Gate_Pass"] = gate_pass
    return pd.DataFrame(cols)


def _ctx(label_to_df, *, apply_gates=False, use_scatter_gates=False,
         gate_draw_mode=False, gate_edit=None):
    from well_viewer.core.app_runtime import AppRuntime
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths=dict.fromkeys(label_to_df),
                                well_labels={}, cache=dict(label_to_df)),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=list(label_to_df), selections=[]),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
        scatter=SimpleNamespace(apply_gates_to_points=apply_gates),
        cell_gating=SimpleNamespace(use_scatter_gates=use_scatter_gates),
    )
    ctx = SimpleNamespace(
        app=app, state=app._app_state, runtime=AppRuntime(app),
        widgets=SimpleNamespace(get_or_none=lambda n: getattr(app, n, None)),
    )
    ctx.runtime.scatter.gate_draw_mode = gate_draw_mode
    ctx.runtime.scatter.gate_edit = gate_edit
    return ctx


def _n_points(scatter_data):
    return sum(len(d["x"]) for d in scatter_data.values())


# ── the collect kwarg ───────────────────────────────────────────────────────

def test_default_keeps_the_full_cloud():
    """The default must not change for any existing caller — batch export, the
    CSV export service, and the plot itself all relied on the old hardcoded
    ``include_gate=False``."""
    ctx = _ctx({"B02": _df(gate_pass=[1, 0, 0])})

    data = collect_scatter_data(
        ctx, "gfp_mean_intensity", "mcherry_mean_intensity", [24.0],
        well_colors=["#ff0000"],
    )

    assert _n_points(data) == 3


def test_include_gate_filters_to_the_gated_population():
    ctx = _ctx({"B02": _df(gate_pass=[1, 0, 1])})

    data = collect_scatter_data(
        ctx, "gfp_mean_intensity", "mcherry_mean_intensity", [24.0],
        well_colors=["#ff0000"], include_gate=True,
    )

    assert _n_points(data) == 2
    xs = sorted(x for d in data.values() for x in d["x"])
    assert xs == [10.0, 30.0]


def test_include_gate_is_a_no_op_without_the_column():
    """Intensity mode never writes ``Gate_Pass``; the layer is absent and
    ``df_included_mask`` treats an absent layer as all-pass."""
    ctx = _ctx({"B02": _df()})

    data = collect_scatter_data(
        ctx, "gfp_mean_intensity", "mcherry_mean_intensity", [24.0],
        well_colors=["#ff0000"], include_gate=True,
    )

    assert _n_points(data) == 3


# ── when the toggle actually filters ────────────────────────────────────────

def test_gated_only_when_the_toggle_and_the_gating_mode_agree():
    assert scatter_points_gated(_ctx({}, apply_gates=True,
                                     use_scatter_gates=True)) is True
    # Toggle off — no filtering whatever the Cell Gating tab says.
    assert scatter_points_gated(_ctx({}, apply_gates=False,
                                     use_scatter_gates=True)) is False


def test_not_gated_while_cell_gating_uses_intensity_thresholds():
    """With scatter-gate inclusion off, ``Gate_Pass`` is all-1 and filtering
    would be a silent no-op. Reporting False keeps the title tag honest."""
    assert scatter_points_gated(_ctx({}, apply_gates=True,
                                     use_scatter_gates=False)) is False


def test_not_gated_while_a_gate_is_being_drawn():
    """Drawing a gate fans out to a recompute and a redraw. If the filter were
    live, every point outside the box would vanish the instant it was drawn —
    nothing left to judge the gate against, and the RectangleSelector rebuilding
    under the cursor."""
    assert scatter_points_gated(_ctx({}, apply_gates=True, use_scatter_gates=True,
                                     gate_draw_mode=True)) is False


def test_not_gated_while_a_gate_edge_is_being_dragged():
    assert scatter_points_gated(_ctx({}, apply_gates=True, use_scatter_gates=True,
                                     gate_edit={"idx": 0, "handle": "e"})) is False


# ── the title tag ───────────────────────────────────────────────────────────

def test_title_suffix_tracks_the_filter():
    """A saved or copied figure has to carry the fact that it is filtered — the
    on-screen toggle does not travel into an SVG."""
    on = _ctx({}, apply_gates=True, use_scatter_gates=True)
    off = _ctx({}, apply_gates=False, use_scatter_gates=True)

    assert gate_filter_title_suffix(on) == " · gate-filtered"
    assert gate_filter_title_suffix(off) == ""
