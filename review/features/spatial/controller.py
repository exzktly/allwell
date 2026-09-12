"""Spatial-analysis controller — behaviour + state mutation.

Every setter and lifecycle hook takes a ``ViewerContext``. Widget handles
resolve via ``ctx.widgets.get_or_none``, state via ``ctx.state.feature_slice``,
and per-well DataFrames read from ``ctx.state.dataset.cache`` — the Spatial
tab operates on wells that a plotting / image tab has already loaded, so
no god-object reach-through is needed.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from review.core.ui_helpers import EmptyReason

import logging

from core.safe import swallowed
from review.domain import spatial_compute as spc
from review.features.spatial import state as sp_state
from review.features.spatial.collect import (
    available_timepoints, collect_spatial_frame, collect_summary_matrix,
    summary_matrix_to_csv_rows,
)
from review.features.spatial.renderer import render_spatial

_logger = logging.getLogger("review")

_MIN_CELLS_FOR_STATS = 3

# Per-frame levels facet one panel per selected well. Past this many wells the
# grid squishes to unreadable slivers, so we render the first N (plate-order)
# and note the remainder. Summary level is unbounded — it collapses each well
# to a single matrix row, so it has no per-panel budget.
_MAX_FRAME_PANELS = 12


def _state(ctx):
    return ctx.state.feature_slice("spatial")


def _sync_empty_state(ctx) -> None:
    """Re-evaluate this tab's empty-state reason after its own state changed.

    ``spatial_empty_reason`` depends on ``st.level``, which nothing outside this
    module touches — so none of the app-wide refresh triggers (dataset load, lazy
    build, selection change) fire when the user switches level. Without this the
    placeholder was decided once and then stood over a page that had something to
    draw: at Summary level the reason correctly returns ``None`` while the stack
    stayed on page 0.
    """
    from review.core.ui_helpers import refresh_empty_state_pages
    try:
        refresh_empty_state_pages(ctx.window)
    except Exception:
        swallowed()


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
    _sync_empty_state(ctx)


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

def _rep_active_wells(ctx) -> list:
    """Active wells in replicate (group) mode: every non-hidden group's members
    plus active solo wells, plate-order sorted.

    In rep-mode the plate is passive and the per-well ``selected_wells`` set is
    not maintained — the active set lives in the group ``hidden`` flags. This is
    the same source the plotting tabs use (``rep_sets_active``) and the plate
    highlights, so the Spatial tab tracks group selection in lock-step."""
    sel = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths
    wells = {
        w for s in (sel.selections or []) if not s.get("hidden")
        for w in (s.get("wells") or []) if w in well_paths
    }
    wells |= {w for w in (sel.active_solo_wells or set()) if w in well_paths}
    return sorted(wells)


def spatial_empty_reason(ctx) -> EmptyReason | None:
    """The Spatial tab's ``wrap_with_empty_state`` reason — level-dependent.

    Spatial has two fallbacks for an empty selection and they are not the same
    kind of thing. At Summary level ``_summary_wells`` falls back to *every*
    loaded well, which is a defensible default for a matrix that is about the
    plate as a whole; it keeps rendering, and its caption names the fallback so
    the reader knows the selection did not scope it.

    At the three per-frame levels ``_frame_wells`` falls back to the first key of
    ``well_paths`` — one arbitrary well, in dictionary order, with nothing on
    screen saying it was picked for the user. That is a silent substitution, so
    those levels show the placeholder instead.
    """
    from review.core.selection_controller import has_plottable_selection
    from review.core.ui_helpers import EmptyReason

    st = _state(ctx)
    if st is None or st.level == sp_state.LEVEL_SUMMARY:
        return None
    if has_plottable_selection(ctx):
        return None
    return EmptyReason(
        "No wells selected.",
        "Click wells on the plate at the left.",
    )


def _frame_wells(ctx, st) -> list:
    """Wells to facet at a per-frame level. In replicate (group) mode this is the
    active set (non-hidden groups + solo wells), matching the plate highlight and
    the plotting tabs; otherwise the current per-well plate selection, falling
    back to the first loaded well. Bounded by the caller's panel cap."""
    if ctx.state.selection.selections:
        return _rep_active_wells(ctx)
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
        # Not "load a dataset": the per-frame levels are placeholder-gated on the
        # selection now, so this branch is reached with wells chosen whose frames
        # would not resolve — and saying load-a-dataset over a loaded one is the
        # contradiction this whole branch set out to remove.
        st.last_error = "No spatial data for the chosen wells and timepoint."
        render_spatial(ctx)
        _sync_empty_state(ctx)
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
    _sync_empty_state(ctx)


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
    """Wells to include in the Summary walk. In replicate (group) mode this is
    the active set (non-hidden groups + solo wells); otherwise the current plate
    selection, falling back to every loaded well when nothing is selected."""
    if ctx.state.selection.selections:
        return _rep_active_wells(ctx)
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
        st.last_error = "Press Recompute to build the summary."
        render_spatial(ctx)
        _sync_empty_state(ctx)
        return
    if not tps:
        st.last_error = "No timepoints available for this dataset."
        render_spatial(ctx)
        _sync_empty_state(ctx)
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
        _sync_empty_state(ctx)
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
    _sync_empty_state(ctx)


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
        ctx.notify.set_status("Switch to Level = Summary before exporting.")
        return
    rows = summary_matrix_to_csv_rows(payload)
    if not rows:
        ctx.notify.set_status("Summary matrix is empty — nothing to export.")
        return
    _write_summary_csv(ctx, rows)


def _write_summary_csv(ctx, rows) -> None:
    """Open a Save-As dialog and write ``rows`` (list of dicts) to CSV."""
    import csv
    from review.core.csv_export import ask_save_csv

    # The dialog is ``core.csv_export``'s: it roots itself at the open dataset,
    # parents itself to the window (an unparented QFileDialog on macOS opens as
    # a detached window rather than an app sheet, and can fall behind the main
    # window with no way back), and offers one filter. Only the *writer* stays
    # here, because these rows are dicts and the schema is their field names.
    path = ask_save_csv(ctx, "Export Spatial Summary CSV",
                        "spatial_summary.csv")
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
        ctx.notify.set_status(f"CSV write failed: {exc}")
        return
    ctx.notify.set_status(f"Wrote {len(rows)} rows → {path}")
