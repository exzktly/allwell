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
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
import numpy as np
import pandas as pd

from well_viewer.core import debug_flags as debug_flags
from well_viewer.data.data_loading import df_included_mask, resolve_value_series
from well_viewer.domain.stats_compute import well_aggregate_stats
from well_viewer.domain.metric_registry import column_gating_exempt

_logger = logging.getLogger(__name__)


def timepoint_color_map(timepoints) -> Dict[float, Any]:
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


def get_all_timepoints(app) -> List[float]:
    """Extract all unique included timepoints across loaded wells."""
    timepoints: set = set()
    for label in app._app_state.dataset.well_paths:
        df = app._get_rows(label)
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        # include_gate=False: the per-cell scatter is where gates are drawn, so it
        # must keep showing the full cloud (+ the gates) instead of filtering
        # itself by Gate_Pass. Every other view applies the gate via the default.
        mask = df_included_mask(df, include_gate=False)
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").where(mask)
        timepoints.update(float(t) for t in tp.dropna().unique())
    return sorted(timepoints)


def collect_scatter_data(
    app,
    col_x: str,
    col_y: str,
    timepoints_h,
    *,
    well_colors: List[str],
    fluor_gate_x: float = 0.0,
    fluor_gate_y: float = 0.0,
    ratios: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Collect scatter plot data for the given column names and timepoint(s).

    ``timepoints_h`` accepts a single ``float`` (legacy) or a list of floats.
    Cells whose ``timepoint_hours`` matches any of the supplied values
    (within 1e-6) are included.

    Groups data by well group (if defined) or by individual well. Each group/well
    gets a distinct color and contains x/y values plus metadata for click tracking.

    Args:
        app: WellViewerApp instance with data and state
        col_x: X-axis column name (e.g., "gfp_mean_intensity" or "gfp_smfish_count")
        col_y: Y-axis column name (e.g., "mcherry_mean_intensity" or "mcherry_smfish_count")
        timepoints_h: One target timepoint or a list of them
        well_colors: List of color strings for coloring groups/wells
        fluor_gate_x: FluorGating threshold for X channel; cells below are excluded
        fluor_gate_y: FluorGating threshold for Y channel; cells below are excluded

    Returns:
        Dict mapping group/well name → {
            'x': [values],
            'y': [values],
            'color': color_str,
            'metadata': [(well_label, fov, row_idx), ...]
        }
    """

    scatter_data: Dict[str, Dict[str, Any]] = {}

    if ratios is None:
        ratios = app._app_state.channel.ratio_index or {}

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

    def _collect_well(well_label: str) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, list]]:
        df = app._get_rows(well_label)
        if df is None or df.empty:
            return None
        if "timepoint_hours" not in df.columns or "area_px" not in df.columns:
            return None
        # Full cloud + gates on the per-cell scatter: don't filter by the gates
        # drawn here (Gate_Pass). All other views honour it via df_included_mask.
        mask = df_included_mask(df, include_gate=False).to_numpy(copy=True)
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
    active_rsets = app._rep_sets_active()

    if active_rsets:
        from well_viewer.features.lineplot.controller import _apply_order as _apply_rs_order
        active_rsets = _apply_rs_order(
            active_rsets,
            list(app._app_state.plot.line_order_rsets or []),
            key=lambda r: getattr(r, "name", ""),
        )
        for group_idx, rset in enumerate(active_rsets):
            group_name = rset.name
            color = app._rank_color_rset(rset)  # decision #1: colour by well-position rank
            xs: List[np.ndarray] = []
            ys: List[np.ndarray] = []
            tps: List[np.ndarray] = []
            metadata: List[Tuple[str, str, str, int]] = []
            # Per-well breakdown so the SVG renderer can emit one
            # Line2D per well (each becomes its own ``<g>`` group). The
            # whole-group ``x``/``y``/``metadata`` arrays stay too — the
            # CSV exporter and click resolver still consume them.
            wells_breakdown: dict[str, dict] = {}
            for well_label in rset.wells:
                if well_label not in app._app_state.dataset.well_paths:
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
                }
    else:
        from well_viewer.features.lineplot.controller import _apply_order as _apply_well_order
        selected_wells = sorted(
            (lbl for lbl in app._app_state.selection.selected_wells if lbl in app._app_state.dataset.well_paths),
            key=lambda lbl: app._parse_rc(lbl),
        )
        selected_wells = _apply_well_order(
            selected_wells,
            list(app._app_state.plot.line_order_wells or []),
            key=lambda x: x,
        )
        for well_idx, well_label in enumerate(selected_wells):
            color = app._rank_color_well(well_label)  # decision #1: colour by well-position rank
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
            }

    return scatter_data


def collect_scatter_agg_data(
    app,
    stat_x: str,  # e.g., "Mean Fluorescence GFP"
    stat_y: str,  # e.g., "Fraction On mCherry"
    timepoints_h: List[float],
    *,
    well_colors: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Collect aggregate scatter plot data by computing statistics for each replicate/well.

    Args:
        app: WellViewerApp instance with data and state
        stat_x: X-axis statistic (e.g., "Mean Fluorescence GFP" or "Fraction On GFP")
        stat_y: Y-axis statistic (e.g., "Mean Fluorescence mCherry" or "Fraction On mCherry")
        timepoints_h: List of target timepoints in hours
        well_colors: List of color strings for coloring replicates/wells

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
    scatter_data: Dict[str, Dict[str, Any]] = {}

    # Extract channel and metric type from statistic names. The legacy
    # "Mean Fluorescence GFP" stat string now optionally carries a
    # parenthesised property suffix ("Mean Fluorescence GFP (Total
    # Intensity)") so the agg renderer can pick a non-MFI column.
    def parse_statistic(stat_str: str) -> Tuple[str, str, str]:
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

    ch_x, metric_x, intensity_x = parse_statistic(stat_x)
    ch_y, metric_y, intensity_y = parse_statistic(stat_y)
    use_sem = app._app_state.plot.use_sem
    per_fov = bool(
        getattr(app, "_use_fov_spread_active", lambda: False)()
    )
    # Per-channel cell-gating thresholds don't apply to ratios — use 0 so
    # every cell with a defined ratio contributes to the well-level mean.
    threshold_x = 0.0 if metric_x == "ratio" else app._get_thresh_frac_on(ch_x)
    threshold_y = 0.0 if metric_y == "ratio" else app._get_thresh_frac_on(ch_y)
    fluor_gates = app._get_all_fluor_gates()  # Apply all channel gates
    label_to_key = getattr(app, "_label_to_channel_key", None) or {}

    # Get active replicates or selected wells
    active_rsets = app._rep_sets_active()

    if active_rsets:
        # Mode: replicate sets
        labels_to_process = [(r.name, r.wells) for r in active_rsets]
    else:
        # Mode: individual wells
        selected_wells = sorted(
            (lbl for lbl in app._app_state.selection.selected_wells if lbl in app._app_state.dataset.well_paths),
            key=lambda lbl: app._parse_rc(lbl),
        )
        labels_to_process = [(lbl, [lbl]) for lbl in selected_wells]

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
        # -1.0 (not 0) so zero-spot cells are counted. Matches the
        # bar/line/heatmap convention in data_loading.aggregate_with_threshold_df.
        threshold_x = -1.0
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
        # See threshold_x comment above.
        threshold_y = -1.0
    elif metric_y == "ratio":
        val_col_y = label_to_key.get(ch_y, "")
    elif metric_y == "frac":
        val_col_y = f"{ch_y}_mean_intensity"
    else:
        val_col_y = f"{ch_y}_{intensity_y}"

    def _agg_wells(wells, tp, val_col, threshold, metric):
        """Mean ± SD/SEM across well-level values for the scatter-aggregate plot.

        Delegates to ``WellViewerApp._well_aggregate_stats`` so this
        path agrees numerically with the line/bar plots'
        ``_compute_rep_stats`` and the Stats tab's pairwise tests.
        Returns ``(value, err)`` picking ``frac`` when ``metric ==
        "frac"`` and ``mean`` otherwise. When ``per_fov`` is on (driven
        by the app's "Across FOV" toggle), the spread is computed
        across per-FOV means rather than across wells.
        """
        if not val_col:
            return float("nan"), 0.0
        gm, gerr, gf, ferr = well_aggregate_stats(app,
            wells, tp,
            threshold=threshold,
            val_col=val_col,
            fluor_gates=fluor_gates,
            use_sem=use_sem,
            per_fov_spread=per_fov,
        )
        if metric == "frac":
            return gf, ferr
        return gm, gerr

    for label_idx, (label, wells) in enumerate(labels_to_process):
        marker = markers[label_idx % len(markers)]

        for tp in sorted(timepoints_h):
            mean_x, err_x = _agg_wells(wells, tp, val_col_x, threshold_x, metric_x)
            mean_y, err_y = _agg_wells(wells, tp, val_col_y, threshold_y, metric_y)

            if math.isnan(mean_x) or math.isnan(mean_y):
                continue

            data_key = f"{label}_tp{tp}"
            tp_color = tp_to_color.get(tp, (0, 0, 0, 1))
            scatter_data[data_key] = {
                'x': [mean_x],
                'y': [mean_y],
                'x_err': [err_x],
                'y_err': [err_y],
                'color': tp_color,
                'marker': marker,
                'timepoint': tp,
                'label': f"{label} (t={tp}h)",
            }

    return scatter_data
