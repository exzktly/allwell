"""Saved Plots restore fidelity — the bits that were captured-but-not-restored
(or not captured at all) so a restored plot could not recreate the saved figure:

- Gating (fluor gates + ThreshFracOn cuts + scatter-gate config): captured on
  ``SavedPlot.thresholds`` but never re-applied; now restored, and stale gates the
  user added after saving are cleared.
- Axis limits: round-tripped into state but never pushed onto the figure; now a
  post-restore redraw applies them.
- Heat Map display settings (colormap / scale / vmin·vmax / toggles / layout):
  never captured; now via the feature's ``view_state_io`` hook.
- Scatter ``color_by_tp`` / ``gates_hidden`` and the cross-tab PlotState fields
  (legend visibility, line draw order, focus timepoint): never captured.
"""
from __future__ import annotations

import pytest

from review.features.saved_plots.collect import capture_params
from review.features.saved_plots.controller import (
    _restore_thresholds,
    restore_saved_plot,
)
from review.features.saved_plots.state import SavedPlot, get_store
from review.persistence import view_state

pytestmark = pytest.mark.qt


# ── Gating (thresholds + scatter-gate config) ────────────────────────────────

def _set_gating(app, *, gfp_gate=100.0, gfp_frac=12.5, with_scatter_gate=False):
    cg = app._app_state.cell_gating
    cg.fluor_gates = {"gfp": gfp_gate}
    cg.thresh_frac_on = {"gfp": gfp_frac}
    if with_scatter_gate:
        cg.scatter_gates = [{
            "x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0,
            "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity",
            "name": "G1",
        }]
        cg.use_scatter_gates = True


def test_capture_gating_block_records_full_gating(loaded_app):
    """capture_params stores the full gating block (gate values + scatter config)
    on ``thresholds`` — the same block the pipeline-info sidecar persists."""
    app = loaded_app
    _set_gating(app, with_scatter_gate=True)
    _params, thresholds = capture_params(app._ctx, "Bar Plots")
    assert thresholds.get("fluor_gates") == {"gfp": 100.0}
    assert thresholds.get("thresh_frac_on") == {"gfp": 12.5}
    assert thresholds.get("use_scatter_gates") is True
    assert thresholds.get("scatter_gates") and \
        thresholds["scatter_gates"][0]["name"] == "G1"


def test_restore_reapplies_gating_block(loaded_app):
    """The captured block is re-applied to the live cell_gating slice on restore
    (previously it was saved for the summary blurb only, never re-applied)."""
    app = loaded_app
    _set_gating(app, gfp_gate=100.0, gfp_frac=12.5, with_scatter_gate=True)
    _params, thresholds = capture_params(app._ctx, "Bar Plots")

    # Diverge the live state, then restore.
    cg = app._app_state.cell_gating
    cg.fluor_gates = {}
    cg.thresh_frac_on = {}
    cg.scatter_gates = []
    cg.use_scatter_gates = False

    _restore_thresholds(app._ctx, thresholds)
    assert cg.fluor_gates == {"gfp": 100.0}
    assert cg.thresh_frac_on == {"gfp": 12.5}
    assert cg.use_scatter_gates is True
    assert cg.scatter_gates and cg.scatter_gates[0]["name"] == "G1"


def test_restore_clears_gate_added_after_save(loaded_app):
    """A plot saved with all-default gating restores to default — a gate the user
    added after saving is cleared (recreate-exactly)."""
    app = loaded_app
    cg = app._app_state.cell_gating
    cg.fluor_gates = {}
    cg.thresh_frac_on = {}
    _params, thresholds = capture_params(app._ctx, "Bar Plots")
    assert thresholds == {}  # nothing non-default to save

    # User adds a gate after saving.
    cg.fluor_gates = {"gfp": 250.0}
    cg.thresh_frac_on = {"gfp": 33.0}

    _restore_thresholds(app._ctx, thresholds)
    assert cg.fluor_gates == {}
    assert cg.thresh_frac_on == {}


def test_restore_thresholds_backward_compatible(loaded_app):
    """Older records may carry ``thresholds={}`` or a legacy fluor/thresh-only
    dict — restore must not raise and must reset to the saved (default) state."""
    app = loaded_app
    app._app_state.cell_gating.fluor_gates = {"gfp": 10.0}
    _restore_thresholds(app._ctx, {})                       # empty
    _restore_thresholds(app._ctx, None)                     # missing
    _restore_thresholds(app._ctx, {"thresh_frac_on": {"gfp": 20.0}})  # legacy
    assert app._app_state.cell_gating.thresh_frac_on == {"gfp": 20.0}


# ── Axis limits ──────────────────────────────────────────────────────────────

def test_axis_limits_round_trip_through_full_restore(loaded_app):
    """A manual x-limit set on the Line figure survives save→restore and is
    actually pushed onto the axes (the redraw-ordering fix)."""
    app = loaded_app
    ctx = app._ctx
    # Set a manual x-limit in the per-figure axis prefs (what the Properties
    # panel writes), keyed by the stable Line figure key.
    app._app_state.export_prefs.axis_prefs["line"] = {
        "x_lim_min": "3", "x_lim_max": "9",
    }
    params, thresholds = capture_params(ctx, "Line Graphs")
    assert params["export_axis_prefs"]["line"]["x_lim_min"] == "3"

    # Persist a record and wipe the live axis prefs, then restore.
    store = get_store(ctx)
    rec = SavedPlot(
        id="axis-1", created_at="2026-01-01T00:00:00", tab="Line Graphs",
        title="Line", png_filename="axis-1.png", params=params,
        thresholds=thresholds,
    )
    store.records.append(rec)
    app._app_state.export_prefs.axis_prefs = {}

    restore_saved_plot(ctx, "axis-1")
    # Data round-trips back into state...
    assert app._app_state.export_prefs.axis_prefs["line"]["x_lim_min"] == "3"
    # ...and reaches the live axes via the post-restore redraw.
    fig = ctx.widgets.get_or_none("_line_fig")
    assert fig is not None and fig.axes
    lo, hi = fig.axes[0].get_xlim()
    assert lo == pytest.approx(3.0) and hi == pytest.approx(9.0)


# ── Heat Map display state (view_state_io hook) ──────────────────────────────

def test_heatmap_display_settings_round_trip(loaded_app):
    """The Heat Map view_state_io hook captures + re-applies colormap / scale /
    vmin·vmax / toggles onto the typed heatmap slice."""
    from review.features.heatmap.view_state_io import (
        apply_heatmap, snapshot_heatmap,
    )
    app = loaded_app
    hm = app._app_state.feature_slice("heatmap")
    assert hm is not None
    hm.cmap_name = "magma"
    hm.scale_mode = "Fixed"
    hm.vmin = 5.0
    hm.vmax = 42.0
    hm.repset_avg = True
    hm.log_scale = True

    blk = snapshot_heatmap(app._ctx)
    assert blk["cmap"] == "magma" and blk["scale_mode"] == "Fixed"
    assert blk["vmin"] == 5.0 and blk["vmax"] == 42.0

    # Diverge, then re-apply.
    hm.cmap_name = "viridis"
    hm.scale_mode = "Auto"
    hm.vmin = None
    hm.vmax = None
    hm.repset_avg = False
    hm.log_scale = False

    apply_heatmap(app._ctx, blk)
    assert hm.scale_mode == "Fixed"
    assert hm.vmin == 5.0 and hm.vmax == 42.0
    assert hm.repset_avg is True and hm.log_scale is True


# ── Scatter toggles + PlotState fields (view_state snapshot/restore) ─────────

def test_scatter_toggles_round_trip(loaded_app):
    """``color_by_tp`` / ``gates_hidden`` survive a view_state snapshot→restore."""
    app = loaded_app
    sc = app._app_state.feature_slice("scatter")
    sc.color_by_tp = True
    sc.gates_hidden = True
    snap = view_state.snapshot(app._ctx)
    assert snap["tabs"]["Scatter Plot"]["color_by_tp"] is True
    assert snap["tabs"]["Scatter Plot"]["gates_hidden"] is True

    sc.color_by_tp = False
    sc.gates_hidden = False
    view_state._apply_scatter(app._ctx, snap["tabs"]["Scatter Plot"])
    assert sc.color_by_tp is True and sc.gates_hidden is True


def test_plotstate_fields_round_trip(loaded_app):
    """Legend visibility, Line draw order, and focus timepoint round-trip."""
    app = loaded_app
    plot = app._app_state.plot
    plot.legend_visible = {"mean": False, "frac": True, "cdf": False, "n": True}
    plot.line_order_rsets = ["B", "A"]
    plot.line_order_wells = ["A02", "A01"]
    plot.focus_timepoint = 24.0

    snap = view_state.snapshot(app._ctx)
    assert snap["legend_visible"]["mean"] is False
    assert snap["line_order_rsets"] == ["B", "A"]
    assert snap["focus_timepoint"] == 24.0

    plot.legend_visible = {"mean": True, "frac": True, "cdf": True, "n": True}
    plot.line_order_rsets = []
    plot.line_order_wells = []
    plot.focus_timepoint = None

    view_state.restore(app._ctx, dict(snap))
    assert plot.legend_visible["mean"] is False and plot.legend_visible["cdf"] is False
    assert plot.line_order_rsets == ["B", "A"]
    assert plot.line_order_wells == ["A02", "A01"]
    assert plot.focus_timepoint == pytest.approx(24.0)


# ── Where the restore lands ──────────────────────────────────────────────────

def test_restore_lands_on_the_saved_tab_in_the_real_app(loaded_app):
    """End to end in the built app: Restore leaves the user looking at the tab
    the plot came from, with the saved settings applied.

    ``view_state.restore`` is shared with the dataset-load path, which lands on
    Home deliberately. Saved Plots must not inherit that: navigating to Home
    applies every restored setting to a tab nobody is looking at, which is
    indistinguishable from a restore that did nothing.
    """
    from review.core.tab_controller import current_centre_tab
    from review.features.saved_plots.controller import save_plot_from_card

    app = loaded_app
    ctx = app._ctx
    view_state._activate_tab(ctx, "Bar Plots")
    ctx.state.plot.use_sem = True
    ctx.state.plot.time_scale = 4.0
    assert save_plot_from_card(ctx, "Bar Plots", b"\x89PNG", "Panel A").startswith("Saved")

    # Walk away and undo both settings, as a user would before restoring.
    view_state._activate_tab(ctx, "Home")
    ctx.state.plot.use_sem = False
    ctx.state.plot.time_scale = 1.0

    rec = get_store(ctx).records[-1]
    restore_saved_plot(ctx, rec.id)

    assert current_centre_tab(ctx) == "Bar Plots"
    assert ctx.state.plot.use_sem is True
    assert ctx.state.plot.time_scale == pytest.approx(4.0)
