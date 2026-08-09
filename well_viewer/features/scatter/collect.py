"""Scatter-plot data collection (no rendering).

The pure data-gathering half of the scatter feature: walk the loaded wells /
replicate sets, apply the inclusion + fluor / count gates, and return plain
dicts of x/y values (and aggregate means ± spread) keyed by group. These have
no matplotlib-axes / Qt dependencies beyond a colormap lookup, so both the
scatter *renderer* (``scatter.controller``) and the CSV exporters
(``export.service``) — plus the distribution tab's timepoint enumeration — can
import them *downward* without forming the scatter↔export / distribution→scatter
import cycles that previously forced function-local imports (T2).

``scatter.controller`` re-exports these names for backwards compatibility.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

from well_viewer.core import debug_flags as debug_flags
from well_viewer.core.lazy import lazy as _lazy
from well_viewer.core.safe import swallow
from well_viewer.data.data_loading import df_included_mask, resolve_value_series, get_rows
from well_viewer.domain.stats_compute import group_replicate_stats, _reduce_reps
from well_viewer.domain.metric_registry import column_gating_exempt
from well_viewer.domain.selections_model import (
    active_replicate_sets, color_for_replicate_set, color_for_well,
    replicate_display_label, well_display_label,
)
from well_viewer.domain.viewer_state import parse_rc

_logger = logging.getLogger(__name__)


def agg_empty_message(ctx, cut_sink: dict | None) -> str:
    """What to draw when the aggregate collector returned nothing.

    An empty plot used to read "No data available. Please select wells/groups
    and timepoints." whatever the cause, which was a lie in the case that
    actually brought a user here: wells selected, timepoints ticked, counts
    present in the CSVs, and every single point silently dropped because the
    channel's ON cutoff sat above the whole range a spot tally can occupy. The
    message sent them back to a selection that was never wrong.

    So when the collector can say the cells were *cut*, say that instead — name
    the axis, the channel, the cutoff, and how many cells it kept, and point at
    the control that changes it.
    """
    axis = next((a for a in ("X", "Y") if a in (cut_sink or {})), None)
    if axis is None:
        return ("No data at the selected timepoints.\n"
                "Pick different timepoints, or select more wells/groups.")
    info = cut_sink[axis]
    channel = str(info.get("channel") or "").upper()
    is_count = str(info.get("col", "")).endswith("_smfish_count")
    quantity = "spot count" if is_count else "intensity"
    knob = "ThreshCountOn" if is_count else "ThreshFracOn"
    n_total = int(info.get("n_total", 0))
    detail = (f"Every cell on the {axis} axis was cut by the ON cutoff: "
              f"{channel} {quantity} > {float(info.get('cutoff', 0.0)):g} "
              f"kept 0 of {n_total:,} cells.")
    # Mirror the reason to the status line too — an empty figure is easy to
    # scroll past, and this is the sentence that says what to change.
    with swallow("scatter: status line unavailable for the cut-cells notice"):
        ctx.notify.set_status(detail)
    return (f"No points to plot.\n{detail}\n"
            f"Lower {knob} for {channel} in Sample Definitions → Cell Gating.")


def timepoint_color_map(timepoints) -> dict[float, Any]:
    """Map sorted timepoints → viridis RGBA — the per-cell + aggregate convention.

    Shared by ``collect_scatter_agg_data`` (one marker per well/timepoint) and
    the per-cell "Color by time" render path so the two scatter views assign the
    same colour to the same timepoint. Returns ``{}`` for an empty list.
    """
    tps = sorted({float(t) for t in (timepoints or [])})
    if not tps:
        return {}
    cmap = matplotlib.colormaps['viridis']
    return {tp: cmap(v) for tp, v in zip(tps, np.linspace(0, 1, len(tps)))}


# ``get_all_timepoints`` is a dataset-level helper shared by scatter /
# distribution / heat map, so it lives in the data layer now; re-exported here
# for the existing ``scatter.collect`` / ``scatter.controller`` import paths.
from well_viewer.data.data_loading import get_all_timepoints  # noqa: F401

# Lazy: ``gating`` imports ``timepoint_color_map`` from this module, so a
# module-scope import back would cycle. Same binding style the Cell Gating
# compute layer uses for this very function — both sides run the one shared
# membership test, so "inside a gate" means the same thing everywhere.
compute_gate_inclusion = _lazy("well_viewer.features.scatter.gating",
                               "compute_gate_inclusion")


def scatter_points_gated(ctx) -> bool:
    """True when the per-cell scatter should plot only cells inside the gates.

    Two conditions:

    * the tab's **Apply gates** toggle is on;
    * no gate is being drawn or edge-dragged right now.

    That second clause is not a nicety. Releasing a freshly drawn gate fans out
    through ``_notify_gates_changed`` → the gating worker → a scatter redraw, so
    with the filter live every point outside the new box would vanish the
    instant it was drawn — leaving nothing to judge the gate against, nothing to
    drag its edge back out over, and the ``RectangleSelector`` rebuilding under
    the cursor. While drawing or editing, the full cloud stays.

    Deliberately *not* conditioned on the Cell Gating tab's ``use_scatter_gates``.
    This filter is computed here, from the boxes this plot is drawing (see
    :func:`gates_for_axes`), so it neither needs nor consults that tab's state —
    and tying it to a second switch was one more way for the plot to disagree
    with itself. Filtering here changes nothing about what any other view counts.
    """
    if not bool(ctx.state.scatter.apply_gates_to_points):
        return False
    if bool(ctx.runtime.scatter.gate_draw_mode):
        return False
    return ctx.runtime.scatter.gate_edit is None


def gates_for_axes(ctx, col_x: str, col_y: str) -> list:
    """The stored gates that can be evaluated against the axes on screen.

    A gate's box is in the data space of its own ``x_col``/``y_col`` and means
    nothing on another pair, so only a matching pair is returned — the same rule
    ``gating._gate_on_current_axes`` applies when deciding what to *draw*, which
    is what keeps the filter and the boxes talking about the same gates.

    A gate whose columns were never stamped is **excluded**, where the drawing
    rule keeps it ("can't tell, don't hide"). The two differ on purpose:
    ``compute_gate_inclusion`` skips an unstamped gate, so treating it as
    matching here would contribute an all-false mask and blank the plot. A box
    drawn but not filtering is a far better failure than an empty cloud.
    """
    return [g for g in (ctx.state.cell_gating.scatter_gates or [])
            if g.get("x_col") == col_x and g.get("y_col") == col_y]


def gate_filter_title_suffix(ctx) -> str:
    """Axes-title tag marking a gate-filtered cloud, else ``""``.

    The toggle changes *which cells are plotted*, so a figure copied or saved
    out of this tab has to carry that fact with it — the on-screen toggle does
    not travel into an SVG.
    """
    return " · gate-filtered" if scatter_points_gated(ctx) else ""


def collect_scatter_data(
    ctx,
    col_x: str,
    col_y: str,
    timepoints_h,
    *,
    well_colors: list[str],
    fluor_gate_x: float = 0.0,
    fluor_gate_y: float = 0.0,
    ratios: dict[str, Any] | None = None,
    gates: list | None = None,
) -> dict[str, dict[str, Any]]:
    """Collect scatter plot data for the given column names and timepoint(s).

    ``timepoints_h`` accepts a single ``float`` (legacy) or a list of floats.
    Cells whose ``timepoint_hours`` matches any of the supplied values
    (within 1e-6) are included.

    Groups data by well group (if defined) or by individual well. Each group/well
    gets a distinct color and contains x/y values plus metadata for click tracking.

    Args:
        ctx: ViewerContext with typed state and services
        col_x: X-axis column name (e.g., "gfp_mean_intensity" or "gfp_smfish_count")
        col_y: Y-axis column name (e.g., "mcherry_mean_intensity" or "mcherry_smfish_count")
        timepoints_h: One target timepoint or a list of them
        well_colors: List of color strings for coloring groups/wells
        fluor_gate_x: FluorGating threshold for X channel; cells below are excluded
        fluor_gate_y: FluorGating threshold for Y channel; cells below are excluded
        gates: keep only cells inside these gate boxes (union). ``None`` /
            empty — the default, and what every caller other than the live tab
            passes — draws the full cloud with the boxes over it, which is the
            point of the plot the gates are drawn on. Membership is computed
            here from the geometry rather than read off the ``Gate_Pass``
            column: that column is written by the Cell Gating worker,
            asynchronously and against *its own* gate selection, so it can
            describe a different set of boxes than the ones on screen. Pass the
            gates this plot is drawing (``gates_for_axes``) and the two cannot
            drift apart.

    Returns:
        Dict mapping group/well name → {
            'x': [values],
            'y': [values],
            'color': color_str,
            'metadata': [(well_label, fov, row_idx), ...]
        }
    """

    scatter_data: dict[str, dict[str, Any]] = {}

    if ratios is None:
        ratios = ctx.state.channel.ratio_index or {}

    if isinstance(timepoints_h, (int, float)):
        target_tps = [float(timepoints_h)]
    else:
        target_tps = [float(t) for t in (timepoints_h or [])]
    target_arr = np.asarray(target_tps, dtype=float)

    # smFISH counts are integer spot tallies and shape features are morphology
    # values — neither is a fluorescence intensity, so the per-channel
    # fluor-gate (an intensity floor like 50–1500) would gate out every such
    # cell. The metric registry owns the "bypass the gate" rule.
    is_count_x = column_gating_exempt(col_x)
    is_count_y = column_gating_exempt(col_y)

    def _collect_well(well_label: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, list] | None:
        df = get_rows(ctx, well_label)
        if df is None or df.empty:
            return None
        if "timepoint_hours" not in df.columns or "area_px" not in df.columns:
            return None
        # The Gate_Pass layer stays off here: the per-cell scatter is where the
        # gates are drawn, so it shows the full cloud with the boxes over it
        # while every other view filters via df_included_mask. The tab's
        # "Apply gates" toggle filters through ``gates`` below instead — against
        # the boxes on screen, not against a column another tab's worker wrote.
        mask = df_included_mask(df, include_gate=False).to_numpy(copy=True)
        if gates:
            mask &= compute_gate_inclusion(df, gates, ratios)
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            finite = np.isfinite(tp)
            if target_arr.size == 0:
                mask &= False
            else:
                # tp ≈ any selected target within tolerance.
                tp_match = np.zeros_like(tp, dtype=bool)
                for t in target_arr:
                    tp_match |= np.abs(tp - t) <= 1e-6
                mask &= finite & tp_match
        area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(area)
        x = resolve_value_series(df, col_x, ratios).to_numpy()
        y = resolve_value_series(df, col_y, ratios).to_numpy()
        gate_x_mask = np.ones_like(x, dtype=bool) if is_count_x else (x > fluor_gate_x)
        gate_y_mask = np.ones_like(y, dtype=bool) if is_count_y else (y > fluor_gate_y)
        mask &= np.isfinite(x) & np.isfinite(y) & gate_x_mask & gate_y_mask
        if not mask.any():
            return None
        sel = df.loc[mask]
        if sel.empty:
            return None
        filenames = (sel["filename"].astype(str).to_numpy()
                     if "filename" in sel.columns else np.array([""] * len(sel)))
        nuclear_ids = (sel["nucleus_id"].astype(str).to_numpy()
                       if "nucleus_id" in sel.columns else np.array([""] * len(sel)))
        row_idx = sel.index.to_numpy()
        if debug_flags.review_scatter_debug_enabled() and len(sel) > 0:
            _logger.debug("scatter row keys: %s", list(sel.columns))
            _logger.debug("scatter filename=%r nuclear_id=%r", filenames[0], nuclear_ids[0])
        metadata = [(well_label, fn, nid, int(ri))
                    for fn, nid, ri in zip(filenames, nuclear_ids, row_idx)]
        return x[mask], y[mask], tp[mask], metadata

    # Use active replicate sets if defined, otherwise fall back to selected wells
    active_rsets = active_replicate_sets(
        ctx.state.selection.selections, ctx.state.dataset.well_paths)

    if active_rsets:
        from well_viewer.features.lineplot.controller import _apply_order as _apply_rs_order
        active_rsets = _apply_rs_order(
            active_rsets,
            list(ctx.state.plot.line_order_rsets or []),
            key=lambda r: getattr(r, "name", ""),
        )
        for group_idx, rset in enumerate(active_rsets):
            group_name = rset.name
            color = color_for_replicate_set(rset, ctx.state.selection.selections)  # decision #1: colour by well-position rank
            xs: list[np.ndarray] = []
            ys: list[np.ndarray] = []
            tps: list[np.ndarray] = []
            metadata: list[tuple[str, str, str, int]] = []
            # Per-well breakdown so the SVG renderer can emit one
            # Line2D per well (each becomes its own ``<g>`` group). The
            # whole-group ``x``/``y``/``metadata`` arrays stay too — the
            # CSV exporter and click resolver still consume them.
            wells_breakdown: dict[str, dict] = {}
            for well_label in rset.wells:
                if well_label not in ctx.state.dataset.well_paths:
                    continue
                got = _collect_well(well_label)
                if got is None:
                    continue
                wx, wy, wtp, wmeta = got
                xs.append(wx); ys.append(wy); tps.append(wtp); metadata.extend(wmeta)
                wells_breakdown[well_label] = {
                    'x': wx.tolist(),
                    'y': wy.tolist(),
                    'tp': wtp.tolist(),
                    'metadata': wmeta,
                }
            if xs:
                scatter_data[group_name] = {
                    'x': np.concatenate(xs).tolist(),
                    'y': np.concatenate(ys).tolist(),
                    'tp': np.concatenate(tps).tolist(),
                    'color': color,
                    'metadata': metadata,
                    'wells': wells_breakdown,
                    # Legend display name — user-defined sample-definition label
                    # for the group (falls back to the group name). The dict key
                    # stays the raw identity used by the click resolver / export.
                    'label': replicate_display_label(ctx, rset),
                }
    else:
        from well_viewer.features.lineplot.controller import _apply_order as _apply_well_order
        selected_wells = sorted(
            (lbl for lbl in ctx.state.selection.selected_wells if lbl in ctx.state.dataset.well_paths),
            key=lambda lbl: parse_rc(lbl),
        )
        selected_wells = _apply_well_order(
            selected_wells,
            list(ctx.state.plot.line_order_wells or []),
            key=lambda x: x,
        )
        for well_idx, well_label in enumerate(selected_wells):
            color = color_for_well(well_label)  # decision #1: colour by well-position rank
            got = _collect_well(well_label)
            if got is None:
                continue
            wx, wy, wtp, wmeta = got
            wx_list = wx.tolist()
            wy_list = wy.tolist()
            wtp_list = wtp.tolist()
            scatter_data[well_label] = {
                'x': wx_list,
                'y': wy_list,
                'tp': wtp_list,
                'color': color,
                'metadata': wmeta,
                # Single-well groups still expose a 'wells' breakdown so
                # the SVG renderer can treat both modes uniformly.
                'wells': {
                    well_label: {
                        'x': wx_list,
                        'y': wy_list,
                        'tp': wtp_list,
                        'metadata': wmeta,
                    },
                },
                # Legend display name — user-defined well label (falls back to
                # the well token). Key stays the raw token used as identity.
                'label': well_display_label(ctx, well_label),
            }

    return scatter_data


def collect_scatter_agg_data(
    ctx,
    stat_x: str,  # e.g., "Mean Fluorescence GFP"
    stat_y: str,  # e.g., "Fraction On mCherry"
    timepoints_h: list[float],
    *,
    well_colors: list[str],
    cut_sink: dict | None = None,
) -> dict[str, dict[str, Any]]:
    """Collect aggregate scatter plot data by computing statistics for each replicate/well.

    Args:
        ctx: ViewerContext with typed state and services
        stat_x: X-axis statistic (e.g., "Mean Fluorescence GFP" or "Fraction On GFP")
        stat_y: Y-axis statistic (e.g., "Mean Fluorescence mCherry" or "Fraction On mCherry")
        timepoints_h: List of target timepoints in hours
        well_colors: List of color strings for coloring replicates/wells
        cut_sink: optional dict the caller passes in; an axis whose cells were
            *all* cut by its ON cutoff records ``{"X"|"Y": {channel, col,
            cutoff, n_total, n_above}}`` here. An empty result with an empty
            sink means there were no cells to begin with — a different problem
            with a different remedy, and the caller says so.

    Returns:
        Dict mapping "label_tp{timepoint}" → {
            'x': [mean_x],
            'y': [mean_y],
            'x_err': [error_x],
            'y_err': [error_y],
            'color': color_str,
            'timepoint': timepoint_h,
            'label': 'label (t=Xh)'
        }
    """
    scatter_data: dict[str, dict[str, Any]] = {}

    # Extract channel and metric type from statistic names. The legacy
    # "Mean Fluorescence GFP" stat string now optionally carries a
    # parenthesised property suffix ("Mean Fluorescence GFP (Total
    # Intensity)") so the agg renderer can pick a non-MFI column.
    def parse_statistic(stat_str: str) -> tuple[str, str, str]:
        """Parse statistic strings.

        Returns ``(channel_or_label, metric, intensity_metric)`` where
        ``metric`` is one of ``"mean"`` | ``"frac"`` | ``"smfish"`` |
        ``"ratio"`` and ``intensity_metric`` is one of the
        ``metric_labels.INTENSITY_METRIC_KEYS`` values (defaults to
        ``"mean_intensity"`` when the stat string doesn't carry a
        property suffix). ``intensity_metric`` is ignored for the
        ``smfish`` / ``ratio`` paths.
        """
        from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
        suffix_metric = "mean_intensity"
        body = stat_str
        if body.endswith(")") and " (" in body:
            head, paren = body.rsplit(" (", 1)
            paren = paren[:-1]
            key = METRIC_LABEL_TO_KEY.get(paren)
            if key in (
                "mean_intensity", "total_intensity", "max_intensity",
                "min_intensity", "std_intensity",
            ):
                body = head
                suffix_metric = key
        if body.startswith("Mean Ratio "):
            label = body[len("Mean Ratio "):]
            return label, "ratio", suffix_metric
        if body.startswith("Mean Fluorescence"):
            channel = body.replace("Mean Fluorescence ", "").lower()
            return channel, "mean", suffix_metric
        elif body.startswith("Fraction On"):
            channel = body.replace("Fraction On ", "").lower()
            return channel, "frac", suffix_metric
        elif body.startswith("smFISH Count"):
            channel = body.replace("smFISH Count ", "").lower()
            return channel, "smfish", suffix_metric
        else:
            return "gfp", "mean", suffix_metric

    from well_viewer.core.channel_state_controller import (
        get_all_fluor_gates, on_cutoff_for_column,
    )

    ch_x, metric_x, intensity_x = parse_statistic(stat_x)
    ch_y, metric_y, intensity_y = parse_statistic(stat_y)
    use_sem = ctx.state.plot.use_sem
    per_fov = bool(ctx.state.plot.use_fov_replicates)
    fluor_gates = get_all_fluor_gates(ctx)  # Apply all channel gates
    # ``_label_to_channel_key`` (ratio/shape label→column map) is app-native
    # state not yet migrated — residual bridge.
    label_to_key = ctx.state.channel.label_to_channel_key

    # Get active replicates or selected wells
    active_rsets = active_replicate_sets(
        ctx.state.selection.selections, ctx.state.dataset.well_paths)

    # Each entry is ``(identity, display_label, wells)``. ``identity`` is the raw
    # rep-set name / well token used as the click/CSV/export key; ``display_label``
    # is the user-defined Sample-Definition name shown in the legend (mirrors the
    # per-cell scatter's ``collect_scatter_data``).
    if active_rsets:
        # Mode: replicate sets
        labels_to_process = [
            (r.name, replicate_display_label(ctx, r), r.wells) for r in active_rsets
        ]
    else:
        # Mode: individual wells
        selected_wells = sorted(
            (lbl for lbl in ctx.state.selection.selected_wells if lbl in ctx.state.dataset.well_paths),
            key=lambda lbl: parse_rc(lbl),
        )
        labels_to_process = [
            (lbl, well_display_label(ctx, lbl), [lbl]) for lbl in selected_wells
        ]

    # Define markers for each well/group
    markers = ['o', 's', '^', 'D', 'v', 'p', '*', 'h', '+', 'x']  # circle, square, triangle, diamond, etc.

    # Create color gradient for timepoints using a colormap (shared with the
    # per-cell "Color by time" path via timepoint_color_map).
    tp_to_color = timepoint_color_map(timepoints_h)

    # Derive column names based on metric type. The per-axis Property combo
    # supplies ``intensity_x`` / ``intensity_y`` (which CSV column) and
    # ``metric_x`` / ``metric_y`` (how to aggregate: mean / fraction-on /
    # smfish / ratio).
    if metric_x == "smfish":
        val_col_x = f"{ch_x}_smfish_count"
    elif metric_x == "ratio":
        val_col_x = label_to_key.get(ch_x, "")
    elif metric_x == "frac":
        # Fraction-on always uses MFI as the underlying gate column;
        # the aggregation reports the fraction passing the channel's
        # ThreshFracOn cut.
        val_col_x = f"{ch_x}_mean_intensity"
    else:
        val_col_x = f"{ch_x}_{intensity_x}"

    if metric_y == "smfish":
        val_col_y = f"{ch_y}_smfish_count"
    elif metric_y == "ratio":
        val_col_y = label_to_key.get(ch_y, "")
    elif metric_y == "frac":
        val_col_y = f"{ch_y}_mean_intensity"
    else:
        val_col_y = f"{ch_y}_{intensity_y}"

    # The ON cutoff has to be picked from the column, so it is resolved here
    # rather than beside the channels above. Ratios are handled in front of the
    # resolver: they are a computed value with no per-channel cutoff of their
    # own, and 0.0 lets every cell with a defined ratio reach the well mean.
    threshold_x = 0.0 if metric_x == "ratio" else on_cutoff_for_column(
        ctx, val_col_x, channel=ch_x)
    threshold_y = 0.0 if metric_y == "ratio" else on_cutoff_for_column(
        ctx, val_col_y, channel=ch_y)

    def _record_cut(axis, val_col, channel, threshold, reps):
        """Note why an axis produced no value, when the reason is knowable.

        ``ReplicateStat`` already carries ``n_above`` and ``n_total``, so the
        two cases separate for free: no cells at this timepoint at all, versus
        cells that were every one of them cut by the ON cutoff. Conflating them
        is what let the tab tell the user to select wells and timepoints they
        had already selected.
        """
        if cut_sink is None or not reps:
            return
        n_total = sum(int(r.n_total) for r in reps)
        n_above = sum(int(r.n_above) for r in reps)
        if n_total > 0 and n_above == 0:
            cut_sink.setdefault(axis, {
                "channel": channel, "col": val_col, "cutoff": threshold,
                "n_total": n_total, "n_above": n_above,
            })

    def _agg_wells(wells, tp, val_col, threshold, metric, axis="", channel=""):
        """Mean ± SD/SEM for the scatter-aggregate plot, via the ONE replicate
        model the bar / line plots and the Stats tab use.

        Reduces ``group_replicate_stats`` — the single canonical producer of
        per-replicate summaries — so this path agrees numerically with
        ``compute_rep_stats`` / ``compute_rep_per_fov_stats`` (and thus the
        Stats tab's tests) in **both** modes. Each well is one replicate in
        well-mode; every FOV across all of the group's wells is one replicate
        when ``per_fov`` (the "Across FOV" toggle) is on — so a rep-set's FOV
        spread pools FOVs across wells rather than averaging per-well spreads.
        Returns ``(value, err)`` picking ``frac`` when ``metric == "frac"`` and
        ``mean`` otherwise.
        """
        if not val_col:
            return float("nan"), 0.0
        reps = group_replicate_stats(
            ctx, wells, tp,
            use_fov=per_fov,
            val_col=val_col,
            threshold=threshold,
            fluor_gates=fluor_gates,
        )
        field = "frac" if metric == "frac" else "mean"
        value, err = _reduce_reps(reps, field, use_sem)
        if math.isnan(value):
            _record_cut(axis, val_col, channel, threshold, reps)
        return value, err

    for label_idx, (identity, disp_label, wells) in enumerate(labels_to_process):
        marker = markers[label_idx % len(markers)]

        for tp in sorted(timepoints_h):
            mean_x, err_x = _agg_wells(wells, tp, val_col_x, threshold_x,
                                       metric_x, axis="X", channel=ch_x)
            mean_y, err_y = _agg_wells(wells, tp, val_col_y, threshold_y,
                                       metric_y, axis="Y", channel=ch_y)

            if math.isnan(mean_x) or math.isnan(mean_y):
                continue

            data_key = f"{identity}_tp{tp}"
            tp_color = tp_to_color.get(tp, (0, 0, 0, 1))
            scatter_data[data_key] = {
                'x': [mean_x],
                'y': [mean_y],
                'x_err': [err_x],
                'y_err': [err_y],
                'color': tp_color,
                'marker': marker,
                'timepoint': tp,
                # Per-point legend text (kept for CSV / click / batch-export
                # consumers reading ``data["label"]``); the tab renderer builds a
                # tidy two-part legend from ``group_label`` + ``timepoint``.
                'label': f"{disp_label} (t={tp}h)",
                'group_label': disp_label,
            }

    return scatter_data
