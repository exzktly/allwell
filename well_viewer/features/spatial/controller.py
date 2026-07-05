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
from well_viewer.features.spatial.collect import (
    available_timepoints, collect_spatial_frame, collect_summary_matrix,
    summary_matrix_to_csv_rows,
)
from well_viewer.features.spatial.renderer import render_spatial

_logger = logging.getLogger("well_viewer")

_MIN_CELLS_FOR_STATS = 3

# Per-frame levels facet one panel per selected well. Past this many wells the
# grid squishes to unreadable slivers, so we render the first N (plate-order)
# and note the remainder. Summary level is unbounded — it collapses each well
# to a single matrix row, so it has no per-panel budget.
_MAX_FRAME_PANELS = 12


def _state(ctx):
    return ctx.state.feature_slice("spatial")


def _autocompute(ctx) -> None:
    """Recompute *unless* we're at Summary level, where the walk is expensive
    (one DBSCAN per (well, timepoint)) and the user drives the compute via the
    Recompute button. Per-frame levels stay live so the tab still feels
    interactive on cheap parameter changes."""
    st = _state(ctx)
    if st is None:
        return
    if st.level == sp_state.LEVEL_SUMMARY:
        return
    sp_recompute(ctx)


def _rerender_only(ctx) -> None:
    """Re-render without recomputing. Used by summary-metric / display-mode
    setters at Summary level — the matrix is already cached; changing the
    projection field or heatmap-vs-line only needs a fresh draw."""
    st = _state(ctx)
    if st is None or not st.last_result:
        return
    # Keep the render-time projection fields in sync with the state slice.
    if st.last_result.get("kind") == "summary_matrix":
        st.last_result["summary_metric_key"] = st.summary_metric_key
        st.last_result["summary_display"] = st.summary_display
    render_spatial(ctx)


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
        _autocompute(ctx)


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
    _run_registered(ctx, "_sp_sync_summary_visibility")
    sp_recompute(ctx)


def sp_set_viz(ctx, viz: str) -> None:
    st = _state(ctx)
    if st is None or not viz:
        return
    st.viz = viz
    _run_registered(ctx, "_sp_sync_summary_visibility")
    sp_recompute(ctx)


def sp_set_metric(ctx, col: str) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.metric_col = col or ""
    _autocompute(ctx)


def sp_set_timepoint(ctx, tp) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.timepoint = None if tp is None else float(tp)
    _autocompute(ctx)


def sp_set_colony_method(ctx, method: str) -> None:
    st = _state(ctx)
    if st is None or method not in (sp_state.METHOD_DBSCAN,
                                     sp_state.METHOD_SINGLE_LINKAGE):
        return
    st.colony_method = method
    _autocompute(ctx)


def sp_set_colony_eps_mult(ctx, value: float) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.colony_eps_mult = float(value)
    _autocompute(ctx)


def sp_set_colony_min_samples(ctx, value: int) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.colony_min_samples = int(value)
    _autocompute(ctx)


def sp_set_boundary_pct(ctx, value: float) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.boundary_pct = float(value)
    _autocompute(ctx)


def sp_set_show_hulls(ctx, on: bool) -> None:
    st = _state(ctx)
    if st is None:
        return
    st.show_hulls = bool(on)
    _autocompute(ctx)


def sp_set_summary_metric(ctx, key: str) -> None:
    """At Summary level, changing which FrameSummary field to project is a
    pure re-render (matrix already cached); nothing to recompute."""
    st = _state(ctx)
    if st is None or not key:
        return
    st.summary_metric_key = key
    _rerender_only(ctx)


def sp_set_summary_display(ctx, mode: str) -> None:
    """Heatmap ↔ Line: pure re-render, no recompute."""
    st = _state(ctx)
    if st is None or mode not in sp_state.SUMMARY_DISPLAYS:
        return
    st.summary_display = mode
    _rerender_only(ctx)


# ── recompute pipeline ───────────────────────────────────────────────────────

def _frame_wells(ctx, st) -> list:
    """Wells to facet at a per-frame level — the current plate selection in
    plate order, falling back to the first loaded well when nothing is
    selected. Unlike the Summary walk this stays bounded by the caller's cap
    (one figure panel per well)."""
    selected = list(ctx.state.selection.selected_wells or [])
    if selected:
        return sorted(selected)
    for lbl in ctx.state.dataset.well_paths:
        return [lbl]
    return []


def sp_recompute(ctx) -> None:
    """Collect + compute for the current state, then render.

    Per-frame levels (Cell / Colony / Intra-colony) compute one payload per
    selected well and stash them on ``st.frames`` so the renderer can facet a
    panel per well. Summary level stays a single collapsed matrix.
    """
    st = _state(ctx)
    if st is None or ctx.widgets.get_or_none("_sp_canvas") is None:
        return
    st.reset_result()
    if st.level == sp_state.LEVEL_SUMMARY:
        _recompute_summary(ctx, st)
        return
    wells = _frame_wells(ctx, st)
    if not wells:
        st.last_error = "Load a dataset and pick a well to analyse."
        render_spatial(ctx)
        return

    shown = wells[:_MAX_FRAME_PANELS]
    st.frames_truncated = len(wells) - len(shown)
    st.active_well = shown[0]
    frames = []
    total_cells = 0
    for well in shown:
        payload = _compute_frame_payload(ctx, st, well)
        total_cells += int(payload.get("n_cells", 0) or 0)
        frames.append(payload)
    st.frames = frames
    st.n_cells = total_cells
    render_spatial(ctx)


def _compute_frame_payload(ctx, st, well: str) -> dict:
    """Collect + compute a single well's per-frame payload for the current
    level / viz. A ``"error"`` key marks a panel that can't be drawn (no cells,
    or a compute failure) so the renderer can message just that facet without
    blanking the whole grid."""
    frame = collect_spatial_frame(
        ctx, well,
        timepoint=st.timepoint,
        metric_col=st.metric_col,
        include_gate=True,
    )
    pts = frame["points"]
    n_cells = int(pts.shape[0])
    payload = {
        "well": well,
        "timepoint": frame.get("timepoint"),
        "points": pts,
        "metric": frame.get("metric"),
        "metric_col": st.metric_col,
        "n_cells": n_cells,
    }
    if n_cells == 0:
        payload["error"] = "No cells at the selected timepoint."
        return payload

    metrics = st.enabled_metrics
    try:
        if metrics.get("nnd", True):
            payload["nnd"] = spc.nearest_neighbour_distance(pts)
        if st.viz == sp_state.VIZ_RIPLEY and n_cells >= _MIN_CELLS_FOR_STATS:
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
        _logger.exception("Spatial compute failed for %s", well)
        payload["error"] = f"Analysis failed: {exc}"

    return payload


# ── LEVEL_SUMMARY (per-(well,timepoint) matrix) ──────────────────────────────

def _summary_wells(ctx) -> list:
    """Wells to include in the Summary walk — the current plate selection
    (plate-order sorted), falling back to every loaded well if the selection
    is empty."""
    selected = list(ctx.state.selection.selected_wells or [])
    if selected:
        return sorted(selected)
    return sorted(ctx.state.dataset.well_paths)


def _recompute_summary(ctx, st) -> None:
    """Walk every (well ∈ selection) × (timepoint ∈ loaded) frame and stash a
    :class:`FrameSummary` matrix on the state slice for the renderer + CSV
    export."""
    wells = _summary_wells(ctx)
    tps = available_timepoints(ctx)
    if not wells:
        st.last_error = "Select one or more wells to summarise."
        render_spatial(ctx)
        return
    if not tps:
        st.last_error = "No timepoints available — load a dataset first."
        render_spatial(ctx)
        return
    try:
        matrix = collect_summary_matrix(
            ctx, wells, tps,
            metric_col=st.metric_col,
            colony_method=st.colony_method,
            eps_mult=st.colony_eps_mult,
            min_samples=st.colony_min_samples,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _logger.exception("Summary compute failed")
        st.last_error = f"Summary compute failed: {exc}"
        render_spatial(ctx)
        return
    st.last_result = {
        "kind": "summary_matrix",
        **matrix,
        "summary_metric_key": st.summary_metric_key,
        "summary_display": st.summary_display,
    }
    st.n_cells = sum(
        int(s.n_cells) for s in matrix["summaries"].flatten() if s is not None
    )
    render_spatial(ctx)


def sp_export_summary_csv(ctx) -> None:
    """Write the currently-computed Summary matrix to a user-chosen CSV path.

    No-ops (with a status-bar hint) when Summary isn't the active level or
    the matrix hasn't been computed yet.
    """
    st = _state(ctx)
    if st is None:
        return
    payload = st.last_result or {}
    if payload.get("kind") != "summary_matrix":
        ctx.set_status("Switch to Level = Summary before exporting.")
        return
    rows = summary_matrix_to_csv_rows(payload)
    if not rows:
        ctx.set_status("Summary matrix is empty — nothing to export.")
        return
    _write_summary_csv(ctx, rows)


def _write_summary_csv(ctx, rows) -> None:
    """Open a Save-As dialog and write ``rows`` (list of dicts) to CSV."""
    import csv
    from PySide6.QtWidgets import QFileDialog
    dataset_dir = getattr(ctx.state.dataset, "dir", None) or ""
    default = "spatial_summary.csv"
    parent = None
    path, _ = QFileDialog.getSaveFileName(
        parent, "Export Spatial Summary CSV",
        f"{dataset_dir}/{default}" if dataset_dir else default,
        "CSV files (*.csv)",
    )
    if not path:
        return
    fieldnames = list(rows[0].keys())
    try:
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
    except Exception as exc:  # pragma: no cover - defensive
        _logger.exception("Summary CSV write failed")
        ctx.set_status(f"CSV write failed: {exc}")
        return
    ctx.set_status(f"Wrote {len(rows)} rows → {path}")
