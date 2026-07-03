"""Spatial-analysis controller — behaviour + state mutation.

Every setter and lifecycle hook takes a ``ViewerContext``. Widget handles
resolve via ``ctx.widgets.get_or_none``, state via ``ctx.state.feature_slice``,
and per-well DataFrames read from ``ctx.state.dataset.cache`` — the Spatial
tab operates on wells that a plotting / image tab has already loaded, so
no god-object reach-through is needed.
"""

from __future__ import annotations

import logging

from well_viewer.core.safe import swallowed
from well_viewer.domain import spatial_compute as spc
from well_viewer.features.spatial import state as sp_state
from well_viewer.features.spatial.collect import collect_spatial_frame
from well_viewer.features.spatial.renderer import render_spatial

_logger = logging.getLogger("well_viewer")

_MIN_CELLS_FOR_STATS = 3


def _state(ctx):
    return ctx.state.feature_slice("spatial")


def _run_registered(ctx, name: str) -> None:
    """Call the closure registered under *name* (metric picker / timepoint /
    viz-menu refresh), swallowing failures — some may not exist yet on first
    activation, or if the tab body hasn't been built."""
    fn = ctx.widgets.get_or_none(name)
    if callable(fn):
        try:
            fn()
        except Exception:
            swallowed()


# ── lifecycle hooks ──────────────────────────────────────────────────────────

def sp_on_activate(ctx) -> None:
    """Tab shown: refresh input pickers, then recompute."""
    st = _state(ctx)
    if st is None:
        return
    _run_registered(ctx, "_sp_refresh_metric_picker")
    _run_registered(ctx, "_sp_refresh_timepoints")
    _run_registered(ctx, "_sp_refresh_viz_menu")
    if ctx.widgets.get_or_none("_sp_canvas") is not None:
        sp_recompute(ctx)


def sp_on_selection_changed(ctx) -> None:
    if ctx.widgets.get_or_none("_sp_canvas") is not None:
        sp_recompute(ctx)


def sp_on_channel_changed(ctx) -> None:
    _run_registered(ctx, "_sp_refresh_metric_picker")
    if ctx.widgets.get_or_none("_sp_canvas") is not None:
        sp_recompute(ctx)


# ── setters (each ends in a recompute) ───────────────────────────────────────

def sp_set_level(ctx, level: str) -> None:
    st = _state(ctx)
    if st is None or level not in sp_state.LEVELS:
        return
    st.level = level
    viable = sp_state.VIZ_BY_LEVEL.get(level, ())
    if viable and st.viz not in viable:
        st.viz = viable[0]
    _run_registered(ctx, "_sp_refresh_viz_menu")
    sp_recompute(ctx)


def sp_set_viz(ctx, viz: str) -> None:
    st = _state(ctx)
    if st is None or not viz:
        return
    st.viz = viz
    sp_recompute(ctx)


def sp_set_metric(ctx, col: str) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.metric_col = col or ""
    sp_recompute(ctx)


def sp_set_timepoint(ctx, tp) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.timepoint = None if tp is None else float(tp)
    sp_recompute(ctx)


def sp_set_colony_method(ctx, method: str) -> None:
    st = _state(ctx)
    if st is None or method not in (sp_state.METHOD_DBSCAN,
                                     sp_state.METHOD_SINGLE_LINKAGE):
        return
    st.colony_method = method
    sp_recompute(ctx)


def sp_set_colony_eps_mult(ctx, value: float) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.colony_eps_mult = float(value)
    sp_recompute(ctx)


def sp_set_colony_min_samples(ctx, value: int) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.colony_min_samples = int(value)
    sp_recompute(ctx)


def sp_set_boundary_pct(ctx, value: float) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.boundary_pct = float(value)
    sp_recompute(ctx)


def sp_set_show_hulls(ctx, on: bool) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.show_hulls = bool(on)
    sp_recompute(ctx)


# ── recompute pipeline ───────────────────────────────────────────────────────

def _active_well(ctx, st) -> str:
    selected = list(ctx.state.selection.selected_wells or [])
    if st.active_well and st.active_well in selected:
        return st.active_well
    if selected:
        return sorted(selected)[0]
    for lbl in ctx.state.dataset.well_paths:
        return lbl
    return ""


def sp_recompute(ctx) -> None:
    """Collect + compute for the current state, then render."""
    st = _state(ctx)
    if st is None or ctx.widgets.get_or_none("_sp_canvas") is None:
        return
    st.reset_result()
    well = _active_well(ctx, st)
    st.active_well = well
    if not well:
        st.last_error = "Load a dataset and pick a well to analyse."
        render_spatial(ctx)
        return

    frame = collect_spatial_frame(
        ctx, well,
        timepoint=st.timepoint,
        metric_col=st.metric_col,
        include_gate=True,
    )
    pts = frame["points"]
    st.n_cells = int(pts.shape[0])
    if st.n_cells == 0:
        st.last_error = f"No cells in {well} at the selected timepoint."
        render_spatial(ctx)
        return

    payload = {
        "well": well,
        "timepoint": frame.get("timepoint"),
        "points": pts,
        "metric": frame.get("metric"),
        "metric_col": st.metric_col,
    }

    metrics = st.enabled_metrics
    try:
        if metrics.get("nnd", True):
            payload["nnd"] = spc.nearest_neighbour_distance(pts)
        if st.viz == sp_state.VIZ_RIPLEY and st.n_cells >= _MIN_CELLS_FOR_STATS:
            payload["ripley"] = spc.ripley_l(pts)

        need_colonies = (st.level in (sp_state.LEVEL_COLONY, sp_state.LEVEL_INTRA)
                         or st.viz in (sp_state.VIZ_COLONY_SIZE,
                                       sp_state.VIZ_COLONY_METRIC_DIST,
                                       sp_state.VIZ_RADIAL_PROFILE,
                                       sp_state.VIZ_BOUNDARY_INTERIOR))
        if need_colonies:
            colonies = spc.detect_colonies(
                pts, method=st.colony_method,
                eps_mult=st.colony_eps_mult,
                min_samples=st.colony_min_samples,
            )
            payload["colony_labels"] = colonies.labels
            payload["colony_eps"] = colonies.eps
            payload["colony_n"] = colonies.n_colonies
            if colonies.n_colonies:
                payload["colony_stats"] = spc.colony_stats(pts, colonies.labels)
                geom = spc.intra_colony_geometry(
                    pts, colonies.labels, boundary_pct=st.boundary_pct)
                payload["intra_geom"] = geom
                if st.viz == sp_state.VIZ_RADIAL_PROFILE and frame.get("metric") is not None:
                    payload["radial_profile"] = spc.radial_profile(
                        geom.r_over_R, frame["metric"])
                if (st.viz == sp_state.VIZ_COLONY_METRIC_DIST
                        and frame.get("metric") is not None):
                    payload["variance_decomp"] = spc.variance_decomposition(
                        frame["metric"], colonies.labels)
                if st.level == sp_state.LEVEL_INTRA:
                    payload["intra_colour"] = geom.r_over_R
                    payload["intra_colour_label"] = "r / R"

    except Exception as exc:  # pragma: no cover - defensive
        _logger.exception("Spatial compute failed")
        st.last_error = f"Analysis failed: {exc}"

    st.last_result = payload
    render_spatial(ctx)
