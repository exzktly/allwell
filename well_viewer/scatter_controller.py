"""Scatter plot data collection and rendering for multi-well fluorescence analysis."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
import numpy as np
import pandas as pd

from . import debug_flags
from .data_loading import df_included_mask, resolve_value_series
from well_viewer.stats_compute import well_aggregate_stats
from well_viewer.ui_helpers import set_combo_values as _set_combo_values
from well_viewer.channel_state_controller import populate_metric_combo
from well_viewer.ratio_models import is_ratio_key

NO_SELECTION_MSG = "No wells or well groups selected.\nSelect wells on the left panel or define groups to plot."


def get_all_timepoints(app) -> List[float]:
    """Extract all unique included timepoints across loaded wells."""
    timepoints: set = set()
    for label in app._app_state.dataset.well_paths:
        df = app._get_rows(label)
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        mask = df_included_mask(df)
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
    cell_area_threshold: float = 0.0,
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
        cell_area_threshold: Minimum cell area in pixels; cells below are excluded
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

    def _collect_well(well_label: str) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, list]]:
        df = app._get_rows(well_label)
        if df is None or df.empty:
            return None
        if "timepoint_hours" not in df.columns or "area_px" not in df.columns:
            return None
        mask = df_included_mask(df).to_numpy(copy=True)
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
            mask &= np.isfinite(area) & (area > cell_area_threshold)
        x = resolve_value_series(df, col_x, ratios).to_numpy()
        y = resolve_value_series(df, col_y, ratios).to_numpy()
        mask &= np.isfinite(x) & np.isfinite(y) & (x > fluor_gate_x) & (y > fluor_gate_y)
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
            print(f"DEBUG scatter_controller: Row keys: {list(sel.columns)}")
            print(f"DEBUG scatter_controller: filename={filenames[0]!r}, nuclear_id={nuclear_ids[0]!r}")
        metadata = [(well_label, fn, nid, int(ri))
                    for fn, nid, ri in zip(filenames, nuclear_ids, row_idx)]
        return x[mask], y[mask], tp[mask], metadata

    # Use active replicate sets if defined, otherwise fall back to selected wells
    active_rsets = app._rep_sets_active()

    if active_rsets:
        from well_viewer.lineplot_controller import _apply_order as _apply_rs_order
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
        from well_viewer.lineplot_controller import _apply_order as _apply_well_order
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


def redraw_scatter(
    app,
    col_x: str,
    col_y: str,
    timepoints_h,
    *,
    well_colors: List[str],
    cell_area_threshold: float = 0.0,
    fluor_gate_x: float = 0.0,
    fluor_gate_y: float = 0.0,
) -> None:
    """Redraw scatter plot with new data.

    ``timepoints_h`` is either a single float (legacy single-tp call) or a
    list — every cell at any of the supplied timepoints is plotted.

    Args:
        app: WellViewerApp instance
        col_x: X-axis column name (e.g., "gfp_mean_intensity" or "gfp_smfish_count")
        col_y: Y-axis column name (e.g., "mcherry_mean_intensity" or "mcherry_smfish_count")
        timepoints_h: One timepoint or a list of timepoints in hours
        well_colors: List of colors for groups/wells
        cell_area_threshold: Minimum cell area in pixels; cells below are excluded
        fluor_gate_x: FluorGating threshold for X channel; cells below are excluded
        fluor_gate_y: FluorGating threshold for Y channel; cells below are excluded
    """
    # Lazy-build guard. The Scatter tab is built on demand; if a redraw
    # is fanned out before the tab body exists, no-op rather than
    # AttributeError-ing.
    if not all(
        hasattr(app, attr)
        for attr in ("_ax_scatter", "_scatter_fig", "_scatter_canvas")
    ):
        return

    active_rsets = app._rep_sets_active()
    selected_wells = [lbl for lbl in app._app_state.selection.selected_wells if lbl in app._app_state.dataset.well_paths]

    # Clear existing plot
    app._ax_scatter.clear()
    # Honour Screen / Publication mode — the scatter axes used to default
    # to matplotlib's white background, so dark-mode users saw a glaring
    # white block. Pull the theme bg + ink tokens via plot_style.tokens_for.
    from well_viewer.plot_style import tokens_for as _tokens_for_ax
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for_ax(app._ax_scatter)
    try:
        app._scatter_fig.set_facecolor(_bg)
    except Exception:
        pass
    app._ax_scatter.set_facecolor(_bg)
    for sp in app._ax_scatter.spines.values():
        sp.set_color(_spine)
        sp.set_linewidth(0.8)
    app._ax_scatter.tick_params(colors=_muted_fg, labelsize=8)
    app._ax_scatter.xaxis.label.set_color(_muted_fg)
    app._ax_scatter.yaxis.label.set_color(_muted_fg)
    app._ax_scatter.title.set_color(_title_fg)

    if not selected_wells and not active_rsets:
        app._scatter_interaction_cache = {"points": []}
        app._ax_scatter.text(
            0.5,
            0.5,
            NO_SELECTION_MSG,
            ha='center',
            va='center',
            transform=app._ax_scatter.transAxes,
            fontsize=10,
            color=_muted_fg,
        )
        app._ax_scatter.set_axis_off()
        app._scatter_canvas.draw()
        return

    # Normalise the timepoint argument so single-float legacy callers
    # keep working alongside the new multi-tp picker.
    if isinstance(timepoints_h, (int, float)):
        tps_list = [float(timepoints_h)]
    else:
        tps_list = [float(t) for t in (timepoints_h or [])]

    # Collect scatter data
    scatter_data = collect_scatter_data(
        app,
        col_x,
        col_y,
        tps_list,
        well_colors=well_colors,
        cell_area_threshold=cell_area_threshold,
        fluor_gate_x=fluor_gate_x,
        fluor_gate_y=fluor_gate_y,
    )

    # Plot one Line2D per well (even inside a rep-set) so the SVG / PDF
    # exporter emits one <g> group per well — re-colouring an individual
    # well in Illustrator is then a single-click. ``plot`` over
    # ``scatter`` so the artists sit in ax.lines and pick up the
    # Properties sidebar's marker / size / line-style overrides.
    interaction_points: List[Tuple[float, float, Tuple[str, str, str, int]]] = []
    for label, data in scatter_data.items():
        wells_breakdown = data.get('wells') or {label: {
            'x': data['x'], 'y': data['y'],
            'metadata': data.get('metadata', []),
        }}
        first_well_in_group = True
        for well_label, well_data in wells_breakdown.items():
            # Suppress the legend for every well after the first one in
            # the group so a rep-set still shows as a single legend entry
            # but each well is independently styleable in the SVG output.
            (line,) = app._ax_scatter.plot(
                well_data['x'],
                well_data['y'],
                marker='o',
                linestyle='none',
                label=label if first_well_in_group else "_nolegend_",
                color=data['color'],
                alpha=0.6,
                markersize=6,
                markeredgecolor='none',
            )
            # Tag as scatter-only so the export-style hook never overrides
            # the markers-only linestyle with a connecting line.
            line._scatter_only_artist = True
            # matplotlib forwards ``gid`` to the SVG ``id`` attribute on
            # the artist's enclosing <g>. Tag every well's <g> with a
            # stable, human-readable id so Illustrator's layer / selection
            # tools can target it directly. The rep-set name is folded in
            # too when applicable, since two rep-sets can both contain a
            # well that the user pruned to one well per set in editing.
            gid = (
                f"well_{well_label}__{label}"
                if label != well_label else f"well_{well_label}"
            )
            try:
                line.set_gid(gid)
            except Exception:
                pass
            first_well_in_group = False

        # Store metadata for click tracking
        if not hasattr(app, '_scatter_metadata'):
            app._scatter_metadata = {}
        app._scatter_metadata[label] = data['metadata']
        interaction_points.extend(
            (x, y, meta) for x, y, meta in zip(data['x'], data['y'], data['metadata'])
        )

    # Format axes — derive readable label from column name
    def _col_label(col: str) -> str:
        from well_viewer.metric_labels import split_metric_col
        parts = split_metric_col(col)
        if parts is None:
            return col
        _ch, metric_key, label = parts
        if metric_key == "smfish_count":
            ch = label.split(" ")[0]
            return f"{ch} smFISH Count"
        return label

    app._ax_scatter.set_xlabel(_col_label(col_x))
    app._ax_scatter.set_ylabel(_col_label(col_y))
    if not tps_list:
        tp_tag = ""
    elif len(tps_list) == 1:
        tp_tag = f" (t={tps_list[0]:g}h)"
    else:
        tp_tag = f" (t={min(tps_list):g}h–{max(tps_list):g}h, {len(tps_list)} tps)"
    app._ax_scatter.set_title(
        f"Scatter: {_col_label(col_x)} vs {_col_label(col_y)}{tp_tag}",
        color=_title_fg,
    )
    app._ax_scatter.grid(True, alpha=0.3, color=_grid)
    if scatter_data:
        # Transparent frame keeps the legend from masking the plot
        # background with the wrong theme color.
        app._ax_scatter.legend(loc='best', fontsize=8, framealpha=0.0, facecolor="none")

    # Redraw canvas
    app._scatter_interaction_cache = {
        "points": interaction_points,
        # Single-tp callers used ``timepoint_h``; preserve that key when
        # exactly one tp was passed so downstream consumers (export CSV,
        # click metadata) don't need to know about the new list shape.
        "timepoint_h": tps_list[0] if len(tps_list) == 1 else None,
        "timepoints_h": tps_list,
        "col_x": col_x,
        "col_y": col_y,
    }
    # Re-apply Export Style sidebar prefs so axis limits / log scale /
    # font sizes survive a redraw — matches bar/heatmap/distribution.
    try:
        from well_viewer.figure_export_editor import apply_export_style_to_current
        apply_export_style_to_current(app, app._scatter_fig,
                                      getattr(app, "_scatter_canvas", None))
    except Exception:  # pragma: no cover
        pass
    app._scatter_canvas.draw()


def _finite_positive(v) -> bool:
    """True iff *v* is a real number strictly greater than 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0.0


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
        from well_viewer.metric_labels import METRIC_LABEL_TO_KEY
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
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()  # Apply all channel gates
    ratios = app._app_state.channel.ratio_index
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

    # Create color gradient for timepoints using a colormap
    if timepoints_h:
        cmap = matplotlib.colormaps['viridis']
        normalized_tps = np.linspace(0, 1, len(timepoints_h))
        tp_to_color = {tp: cmap(norm_val) for tp, norm_val in zip(sorted(timepoints_h), normalized_tps)}
    else:
        tp_to_color = {}

    # Derive column names based on metric type. The per-axis Property combo
    # supplies ``intensity_x`` / ``intensity_y`` (which CSV column) and
    # ``metric_x`` / ``metric_y`` (how to aggregate: mean / fraction-on /
    # smfish / ratio).
    if metric_x == "smfish":
        val_col_x = f"{ch_x}_smfish_count"
        threshold_x = 0  # No threshold for smfish counts (all spots)
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
        threshold_y = 0  # No threshold for smfish counts (all spots)
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
            cell_area_threshold=cell_area_threshold,
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


def redraw_scatter_agg(
    app,
    stat_x: str,
    stat_y: str,
    timepoints_h: List[float],
    *,
    well_colors: List[str],
) -> None:
    """Redraw aggregate scatter plot with statistics for multiple timepoints.

    Args:
        app: WellViewerApp instance
        stat_x: X-axis statistic (e.g., "Mean Fluorescence GFP")
        stat_y: Y-axis statistic (e.g., "Fraction On GFP")
        timepoints_h: List of timepoints in hours
        well_colors: List of colors for replicates/wells
    """
    # Lazy-build guard — Scatter Aggregate is a sub-tab of the Scatter
    # tab, built on first activation.
    if not all(
        hasattr(app, attr)
        for attr in ("_ax_scatter_agg", "_scatter_agg_fig", "_scatter_agg_canvas")
    ):
        return

    active_rsets = app._rep_sets_active()
    selected_wells = [lbl for lbl in app._app_state.selection.selected_wells if lbl in app._app_state.dataset.well_paths]

    # Clear existing plot
    app._ax_scatter_agg.clear()
    from well_viewer.plot_style import tokens_for as _tokens_for_ax
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for_ax(app._ax_scatter_agg)
    try:
        app._scatter_agg_fig.set_facecolor(_bg)
    except Exception:
        pass
    app._ax_scatter_agg.set_facecolor(_bg)
    for sp in app._ax_scatter_agg.spines.values():
        sp.set_color(_spine)
        sp.set_linewidth(0.8)
    app._ax_scatter_agg.tick_params(colors=_muted_fg, labelsize=8)
    app._ax_scatter_agg.xaxis.label.set_color(_muted_fg)
    app._ax_scatter_agg.yaxis.label.set_color(_muted_fg)

    if not selected_wells and not active_rsets:
        app._ax_scatter_agg.text(
            0.5,
            0.5,
            NO_SELECTION_MSG,
            ha='center',
            va='center',
            transform=app._ax_scatter_agg.transAxes,
            fontsize=10,
            color=_muted_fg,
        )
        app._ax_scatter_agg.set_axis_off()
        app._scatter_agg_canvas.draw()
        return

    # Collect aggregate scatter data
    scatter_data = collect_scatter_agg_data(
        app,
        stat_x,
        stat_y,
        timepoints_h,
        well_colors=well_colors,
    )

    if not scatter_data:
        app._ax_scatter_agg.text(
            0.5, 0.5,
            "No data available.\nPlease select wells/groups and timepoints.",
            ha='center', va='center',
            transform=app._ax_scatter_agg.transAxes,
            fontsize=10,
            color=_muted_fg,
        )
    else:
        # Plot each replicate/well-timepoint as separate error bar series
        for label, data in scatter_data.items():
            # Skip xerr / yerr entirely when there's nothing to show.
            # ``capsize=5`` draws a "_" / "|" cap marker at every point
            # even when the bar length is zero, producing a cross at
            # the centre of every marker that reads as a phantom error
            # bar. Passing None hides both the bar and its caps.
            x_err_seq = data.get('x_err') or []
            y_err_seq = data.get('y_err') or []
            xerr = x_err_seq if any(_finite_positive(e) for e in x_err_seq) else None
            yerr = y_err_seq if any(_finite_positive(e) for e in y_err_seq) else None
            container = app._ax_scatter_agg.errorbar(
                data['x'],
                data['y'],
                xerr=xerr,
                yerr=yerr,
                label=data['label'],
                color=data['color'],
                marker=data.get('marker', 'o'),
                markersize=8,
                linestyle='none',
                capsize=5,
                capthick=1.5,
                alpha=0.7,
            )
            # Tag the data line so the export-style hook can't add a
            # connecting line between aggregate points.
            try:
                container.lines[0]._scatter_only_artist = True
            except Exception:
                pass

        # Format axes
        app._ax_scatter_agg.set_xlabel(stat_x)
        app._ax_scatter_agg.set_ylabel(stat_y)
        tp_range = f"t={min(timepoints_h)}h to {max(timepoints_h)}h" if timepoints_h else ""
        app._ax_scatter_agg.set_title(
            f"Aggregate Scatter: {stat_x} vs {stat_y} ({tp_range})",
            color=_title_fg,
        )
        app._ax_scatter_agg.grid(True, alpha=0.3, color=_grid)
        app._ax_scatter_agg.legend(loc='best', fontsize=8, framealpha=0.0, facecolor="none")

    # Redraw canvas
    try:
        from well_viewer.figure_export_editor import apply_export_style_to_current
        apply_export_style_to_current(app, app._scatter_agg_fig,
                                      getattr(app, "_scatter_agg_canvas", None))
    except Exception:  # pragma: no cover
        pass
    app._scatter_agg_canvas.draw()


def redraw_scatter_orchestrate(app) -> None:
    """Redraw the scatter plot with current selections.

    Mirrors redraw_scatter_agg_orchestrate's empty-state handling: no
    timepoints available -> "No timepoints available." placeholder;
    none selected -> "Please select timepoints to plot."
    """
    # Scatter Plot: Cells tab body is built lazily -- bail out if the
    # user triggers a redraw before that builder has run.
    if not hasattr(app, "_scatter_ch_x_var"):
        return
    try:
        ch_x_entry = app._scatter_ch_x_var.currentText()
        ch_y_entry = app._scatter_ch_y_var.currentText()
    except ValueError:
        return

    if not hasattr(app, "_scatter_tp_selections") or not app._scatter_tp_selections:
        if hasattr(app, "_ax_scatter"):
            app._ax_scatter.clear()
            app._ax_scatter.text(
                0.5, 0.5, "No timepoints available.",
                ha='center', va='center',
                transform=app._ax_scatter.transAxes,
                fontsize=10, color='gray',
            )
            if hasattr(app, "_scatter_canvas"):
                app._scatter_canvas.draw()
        return

    tps_h: list = []
    for tp_str, on in app._scatter_tp_selections.items():
        if not on:
            continue
        try:
            tps_h.append(float(tp_str))
        except (TypeError, ValueError):
            continue
    if not tps_h:
        if hasattr(app, "_ax_scatter"):
            app._ax_scatter.clear()
            app._ax_scatter.text(
                0.5, 0.5, "Please select timepoints to plot.",
                ha='center', va='center',
                transform=app._ax_scatter.transAxes,
                fontsize=10, color='gray',
            )
            if hasattr(app, "_scatter_canvas"):
                app._scatter_canvas.draw()
        return

    # Mirror the first selected tp into the legacy combo so the
    # save-figure / export-CSV paths that still read it see a value.
    if getattr(app, "_scatter_tp_cb", None) is not None:
        first_str = f"{tps_h[0]:.1f}"
        if app._scatter_tp_cb.findText(first_str) >= 0:
            blocked = app._scatter_tp_cb.blockSignals(True)
            try:
                app._scatter_tp_cb.setCurrentText(first_str)
            finally:
                app._scatter_tp_cb.blockSignals(blocked)

    ch_x_base = ch_x_entry.split(" ")[0]
    ch_y_base = ch_y_entry.split(" ")[0]

    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gate_x = app._get_fluor_gate(ch_x_base)
    fluor_gate_y = app._get_fluor_gate(ch_y_base)

    metric_x = app._scatter_axis_metric_key("x")
    metric_y = app._scatter_axis_metric_key("y")
    col_x = app._col_for_scatter_axis(ch_x_entry, metric_x)
    col_y = app._col_for_scatter_axis(ch_y_entry, metric_y)

    from well_viewer.plate_layout import WELL_COLORS
    redraw_scatter(
        app,
        col_x,
        col_y,
        tps_h,
        well_colors=WELL_COLORS,
        cell_area_threshold=cell_area_threshold,
        fluor_gate_x=fluor_gate_x,
        fluor_gate_y=fluor_gate_y,
    )
    from well_viewer.figure_export_editor import apply_export_style_to_current
    apply_export_style_to_current(app, app._scatter_fig, getattr(app, "_scatter_canvas", None))


def on_scatter_click(app, event) -> None:
    """Handle click events on scatter plot datapoints."""
    if event.inaxes is not app._ax_scatter or event.xdata is None or event.ydata is None:
        return

    if event.button != 1:
        return

    try:
        interaction_cache = getattr(app, "_scatter_interaction_cache", None) or {}
        points = interaction_cache.get("points", [])
        if not points:
            return

        min_dist = float('inf')
        nearest_well = None
        nearest_filename = None
        nearest_nuclear_id = None
        nearest_row_idx = None

        for x, y, meta in points:
            dx = x - event.xdata
            dy = y - event.ydata
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < min_dist:
                min_dist = dist
                nearest_well, nearest_filename, nearest_nuclear_id, nearest_row_idx = meta

        if nearest_well is not None:
            app._open_scatter_cell_viewer(nearest_well, nearest_filename, nearest_nuclear_id, nearest_row_idx)

    except Exception as e:
        # The user clicked expecting a popup -- surface the failure instead of
        # swallowing it into a fleeting status line.
        import logging
        import traceback
        from PySide6.QtWidgets import QMessageBox
        logging.getLogger(__name__).exception("Error handling scatter-plot cell click")
        tb = traceback.format_exc()
        app._set_status(f"Could not open cell viewer: {e}")
        try:
            box = QMessageBox(app)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Cell viewer error")
            box.setText("Could not open the cell-image viewer for the clicked point.")
            box.setInformativeText(str(e))
            box.setDetailedText(tb)
            box.exec()
        except Exception:
            pass


def on_scatter_motion(app, event) -> None:
    """Handle hover events on scatter plot to show tooltips."""
    if event.inaxes is not app._ax_scatter or event.xdata is None or event.ydata is None:
        return

    try:
        interaction_cache = getattr(app, "_scatter_interaction_cache", None) or {}
        points = interaction_cache.get("points", [])
        if not points:
            return

        min_dist = float('inf')
        nearest_info = None
        threshold = 0.5

        for x, y, meta in points:
            dx = x - event.xdata
            dy = y - event.ydata
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < min_dist:
                min_dist = dist
                nearest_info = meta

        if min_dist < threshold and nearest_info:
            well_label, filename, nuclear_id, row_idx = nearest_info
            tooltip_text = f"File: {filename} | Nuclear ID: {nuclear_id}"
            app._set_status(tooltip_text)
        else:
            app._set_status("")

    except Exception:
        pass


def redraw_scatter_agg_orchestrate(app) -> None:
    """Redraw the aggregate scatter plot with current selections."""
    if not hasattr(app, "_scatter_agg_stat_x_var"):
        return
    # Sync the hidden stat-string combos with the new per-axis
    # (channel, metric) state so downstream code (export_service,
    # save-figure helpers) sees a coherent view.
    app._sync_scatter_agg_stat_combos()
    try:
        stat_x = app._scatter_agg_stat_x_var.currentText()
        stat_y = app._scatter_agg_stat_y_var.currentText()

        if not hasattr(app, '_scatter_agg_tp_selections') or not app._scatter_agg_tp_selections:
            app._ax_scatter_agg.clear()
            app._ax_scatter_agg.text(
                0.5, 0.5,
                "No timepoints available.",
                ha='center', va='center',
                transform=app._ax_scatter_agg.transAxes,
                fontsize=10,
                color='gray'
            )
            app._scatter_agg_canvas.draw()
            return

        selected_timepoints = []
        for tp_str, selected in app._scatter_agg_tp_selections.items():
            if selected:
                try:
                    selected_timepoints.append(float(tp_str))
                except ValueError:
                    pass

        if not selected_timepoints:
            app._ax_scatter_agg.clear()
            app._ax_scatter_agg.text(
                0.5, 0.5,
                "Please select timepoints to plot.",
                ha='center', va='center',
                transform=app._ax_scatter_agg.transAxes,
                fontsize=10,
                color='gray'
            )
            app._scatter_agg_canvas.draw()
            return

    except (ValueError, AttributeError):
        return

    from well_viewer.plate_layout import WELL_COLORS
    redraw_scatter_agg(
        app,
        stat_x,
        stat_y,
        selected_timepoints,
        well_colors=WELL_COLORS,
    )
    from well_viewer.figure_export_editor import apply_export_style_to_current
    apply_export_style_to_current(app, app._scatter_agg_fig, getattr(app, "_scatter_agg_canvas", None))


def update_scatter_menus(app) -> None:
    """Populate scatter plot dropdowns with available channels and timepoints."""
    # Channel options for both scatter tabs: real fluor channels +
    # ratio labels. Drops the legacy "(spots)" channel variants — the
    # per-axis Property combo now expresses smFISH count via the
    # ``smFISH Count`` metric entry, so we no longer overload the
    # channel column with aggregation flavour.
    channels = list(app._app_state.channel.fluor_channels) if app._app_state.channel.fluor_channels else ["gfp"]
    ratio_labels = app._ratio_dropdown_labels()
    scatter_ch_options = list(channels) + ratio_labels

    _set_combo_values(app._scatter_ch_x_cb, scatter_ch_options)
    _set_combo_values(app._scatter_ch_y_cb, scatter_ch_options)

    if scatter_ch_options:
        if app._scatter_ch_x_var.currentText() not in scatter_ch_options:
            app._scatter_ch_x_var.setCurrentText(scatter_ch_options[0])
        if app._scatter_ch_y_var.currentText() not in scatter_ch_options:
            app._scatter_ch_y_var.setCurrentText(scatter_ch_options[0 if len(scatter_ch_options) == 1 else 1])

    # Refresh per-axis metric combo enable state to match the
    # currently-selected channels.
    for axis in ("x", "y"):
        app._refresh_scatter_metric_for_axis(axis)

    # Update timepoint dropdown for cells scatter
    timepoints = list(app._app_state.dataset.all_timepoints_cache) or get_all_timepoints(app)
    tp_strs = [f"{tp:.1f}" for tp in timepoints] if timepoints else ["0"]
    _set_combo_values(app._scatter_tp_cb, tp_strs)

    if tp_strs and app._scatter_tp_var.currentText() not in tp_strs:
        app._scatter_tp_var.setCurrentText(tp_strs[0])

    # Repopulate the multi-tp picker. Mirrors the aggregate scatter's
    # behaviour: every refresh resets every tp to checked, so a fresh
    # dataset load lands in the same "everything visible" state.
    if hasattr(app, "_scatter_tp_selections"):
        app._scatter_tp_selections.clear()
    else:
        app._scatter_tp_selections = {}
    for tp_str in tp_strs:
        app._scatter_tp_selections[tp_str] = True
    app._update_scatter_tp_selection_display()

    # Aggregate scatter — propagate the same channel list to its own
    # per-axis Channel combo.
    for cb_attr in ("_scatter_agg_ch_x_cb", "_scatter_agg_ch_y_cb"):
        cb = getattr(app, cb_attr, None)
        if cb is None:
            continue
        current = cb.currentText()
        _set_combo_values(cb, scatter_ch_options)
        if current and current in scatter_ch_options:
            cb.setCurrentText(current)
        else:
            default_idx = 0 if cb_attr.endswith("_x_cb") else (1 if len(scatter_ch_options) > 1 else 0)
            cb.setCurrentText(scatter_ch_options[default_idx])
    for axis in ("x", "y"):
        app._refresh_scatter_agg_metric_for_axis(axis)

    # Legacy stat-string combos — kept hidden but in sync so old
    # exporters and the save-figure helpers continue to work.
    statistics: List[str] = []
    for ch in channels:
        statistics.append(f"Mean Fluorescence {ch.upper()}")
        statistics.append(f"Fraction On {ch.upper()}")
        if ch in app._app_state.channel.smfish_channels:
            statistics.append(f"smFISH Count {ch.upper()}")
    for ratio_label in ratio_labels:
        statistics.append(f"Mean Ratio {ratio_label}")
    for cb_attr in ("_scatter_agg_stat_x_cb", "_scatter_agg_stat_y_cb"):
        cb = getattr(app, cb_attr, None)
        if cb is None:
            continue
        _set_combo_values(cb, statistics or ["Mean Fluorescence GFP"])
    # Sync the legacy stat combos to whatever the new per-axis combos
    # express right now.
    app._sync_scatter_agg_stat_combos()

    # Update timepoint selections for aggregate scatter; all default checked.
    if hasattr(app, '_scatter_agg_tp_selections'):
        prev_selected = {tp_str for tp_str, v in app._scatter_agg_tp_selections.items() if v}
        app._scatter_agg_tp_selections.clear()
    else:
        prev_selected = set()
        app._scatter_agg_tp_selections = {}

    for tp_str in tp_strs:
        app._scatter_agg_tp_selections[tp_str] = True

    app._update_tp_selection_display()



def sync_scatter_agg_stat_combos(app) -> None:
    """Mirror the new per-axis (channel, metric) state into the legacy
    ``_scatter_agg_stat_x/y_cb`` string combos so any code path that still
    reads them (export_service, save-figure) sees a valid stat string.
    """
    for axis in ("x", "y"):
        stat_cb = getattr(app, f"_scatter_agg_stat_{axis}_cb", None)
        if stat_cb is None:
            continue
        ch_cb = getattr(app, f"_scatter_agg_ch_{axis}_cb", None)
        m_cb = getattr(app, f"_scatter_agg_metric_{axis}_cb", None)
        channel = str(ch_cb.currentText() if ch_cb else "")
        metric_label = str(m_cb.currentText() if m_cb else "Mean Intensity")
        stat_str = app._compose_scatter_agg_stat(channel, metric_label)
        blocked = stat_cb.blockSignals(True)
        try:
            if stat_cb.findText(stat_str) < 0:
                stat_cb.addItem(stat_str)
            stat_cb.setCurrentText(stat_str)
        finally:
            stat_cb.blockSignals(blocked)



def col_for_scatter_entry(app, entry: str) -> str:
    """Legacy single-string entry → column resolver (kept for stats/export back-compat).

    "gfp" -> "gfp_mean_intensity"
    "gfp (spots)" -> "gfp_smfish_count"
    "GFP/MCHERRY" (a ratio dropdown label) -> "ratio:GFP/MCHERRY"
    """
    ratio_key = (getattr(app, "_label_to_channel_key", None) or {}).get(entry)
    if ratio_key and is_ratio_key(ratio_key):
        return ratio_key
    if entry.endswith(" (spots)"):
        ch = entry[:-8]  # Remove " (spots)"
        return f"{ch}_smfish_count"
    else:
        metric = app._app_state.channel.active_metric or "mean_intensity"
        if metric == "smfish_count":
            # The scatter dropdown uses the " (spots)" suffix for smfish;
            # fall back to mean intensity for the plain channel entry.
            metric = "mean_intensity"
        return f"{entry}_{metric}"



def col_for_scatter_axis(app, channel_entry: str, metric_key: str) -> str:
    """Map a scatter (channel_entry, metric_key) pair to a CSV column or ratio key.

    Ratio channels collapse to the ratio key regardless of metric (ratios
    don't carry a metric suffix). Otherwise builds ``<channel>_<metric>``.
    ``metric_key`` may be ``fraction_above_threshold`` for the agg scatter
    — that's a synthetic key that downstream aggregation code treats as a
    Fraction-On aggregation against the channel's mean-intensity column.
    """
    ratio_key = (getattr(app, "_label_to_channel_key", None) or {}).get(channel_entry)
    if ratio_key and is_ratio_key(ratio_key):
        return ratio_key
    if metric_key in ("fraction_above_threshold", "smfish_count"):
        # Fraction-on uses MFI as the underlying value column; smFISH
        # uses the dedicated count column.
        if metric_key == "smfish_count":
            return f"{channel_entry}_smfish_count"
        return f"{channel_entry}_mean_intensity"
    return f"{channel_entry}_{metric_key}"



def compose_scatter_agg_stat(app, channel_entry: str, metric_label: str) -> str:
    """Build the legacy stat-string from a (channel, metric) pair.

    Mirrors ``parse_statistic`` in scatter_controller: keeps the same
    prefix vocabulary so older callers and the savefile naming still
    recognise the result.
    """
    ratio_key = (getattr(app, "_label_to_channel_key", None) or {}).get(channel_entry)
    if ratio_key and is_ratio_key(ratio_key):
        return f"Mean Ratio {channel_entry}"
    ch_upper = (channel_entry or "").upper()
    if metric_label == "Fraction above threshold":
        return f"Fraction On {ch_upper}"
    if metric_label == "smFISH Count":
        return f"smFISH Count {ch_upper}"
    # All intensity flavours map to the per-cell mean of values above
    # threshold via _aggregate_group; surface a metric-aware suffix so
    # axes/titles reflect the selected property.
    return f"Mean Fluorescence {ch_upper} ({metric_label})"



def refresh_scatter_agg_metric_for_axis(app, axis: str) -> None:
    """Reshape the agg-scatter Property combo for the selected channel.

    Real channels get the full intensity set plus ``Fraction above
    threshold``; ratio channels collapse to ``Calculated Val``.
    """
    ch_cb = getattr(app, f"_scatter_agg_ch_{axis}_cb", None)
    m_cb = getattr(app, f"_scatter_agg_metric_{axis}_cb", None)
    if ch_cb is None or m_cb is None:
        return
    channel = str(ch_cb.currentText() or "")
    populate_metric_combo(app, 
        m_cb, channel_entry=channel, smfish_channel=channel,
        include_fraction=True,
    )



def scatter_axis_metric_key(app, axis: str, *, agg: bool = False) -> str:
    """Return the metric key for axis ``x``/``y`` on the active scatter tab."""
    from well_viewer.metric_labels import METRIC_LABEL_TO_KEY
    attr = (
        f"_scatter_agg_metric_{axis}_cb" if agg
        else f"_scatter_metric_{axis}_cb"
    )
    cb = getattr(app, attr, None)
    if cb is None:
        return "mean_intensity"
    label = str(cb.currentText() or "")
    if agg and label in app._SCATTER_AGG_METRIC_EXTRA:
        return app._SCATTER_AGG_METRIC_EXTRA[label]
    return METRIC_LABEL_TO_KEY.get(label, "mean_intensity")



def update_scatter_tp_selection_display(app) -> None:
    """Update the per-cell scatter label showing selected timepoints.

    Mirrors _update_tp_selection_display so the two pickers behave
    identically (label format included).
    """
    count = sum(1 for v in app._scatter_tp_selections.values() if v)
    total = len(app._scatter_tp_selections)
    label_text = f"(All {count} selected)" if count == total else f"({count}/{total} selected)"
    if hasattr(app, "_scatter_tp_label") and app._scatter_tp_label is not None:
        app._scatter_tp_label.setText(label_text)



def refresh_scatter_metric_for_axis(app, axis: str) -> None:
    """Reshape the cells-scatter Property combo for the selected channel."""
    ch_cb = getattr(app, f"_scatter_ch_{axis}_cb", None)
    m_cb = getattr(app, f"_scatter_metric_{axis}_cb", None)
    if ch_cb is None or m_cb is None:
        return
    channel = str(ch_cb.currentText() or "")
    populate_metric_combo(app, 
        m_cb, channel_entry=channel, smfish_channel=channel,
    )



def update_tp_selection_display(app) -> None:
    """Update the aggregate scatter label showing selected timepoints."""
    count = sum(1 for v in app._scatter_agg_tp_selections.values() if v)
    total = len(app._scatter_agg_tp_selections)
    label_text = f"(All {count} selected)" if count == total else f"({count}/{total} selected)"
    if hasattr(app, "_scatter_agg_tp_label") and app._scatter_agg_tp_label is not None:
        app._scatter_agg_tp_label.setText(label_text)

