"""Scatter plot rendering for multi-well fluorescence analysis.

The pure data-gathering half (``collect_scatter_data`` / ``collect_scatter_agg_data``
/ ``get_all_timepoints``) lives in :mod:`well_viewer.features.scatter.collect`
and is re-exported here for backwards compatibility; this module owns the
matplotlib rendering, click handling, and combo wiring.
"""

from __future__ import annotations

import logging
from typing import List

_logger = logging.getLogger(__name__)

from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values
from well_viewer.core.channel_state_controller import populate_metric_combo
from well_viewer.domain.ratio_models import is_ratio_key
from pipeline.shape_features import is_shape_column
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import apply_export_style_to_current, style_legend
from well_viewer.features.scatter.collect import (  # noqa: F401  (re-export)
    collect_scatter_agg_data,
    collect_scatter_data,
    get_all_timepoints,
)
from well_viewer.features.scatter.gating import (
    paint_gates,
    refresh_gate_selector,
    render_scatter_points,
)

from well_viewer.features.scatter._layout import _render_scatter_faceted, _setup_scatter_axes, _style_scatter_ax, log1p_axis_suffix  # noqa: E501

NO_SELECTION_MSG = "No wells or well groups selected.\nSelect wells on the left panel or define groups to plot."


def redraw_scatter(
    app,
    col_x: str,
    col_y: str,
    timepoints_h,
    *,
    well_colors: List[str],
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

    # Collapse any prior "Separate axes" panels back to the single overlay axis.
    if len(getattr(app._scatter_fig, "axes", []) or []) > 1:
        _setup_scatter_axes(app, 1)

    active_rsets = app._rep_sets_active()
    selected_wells = [lbl for lbl in app._app_state.selection.selected_wells if lbl in app._app_state.dataset.well_paths]

    # Clear existing plot
    app._ax_scatter.clear()
    # Honour Screen / Publication mode — the scatter axes used to default
    # to matplotlib's white background, so dark-mode users saw a glaring
    # white block. Shared with the faceted + aggregate paths (audit A16).
    _bg, _title_fg, _muted_fg, _grid, _spine = _style_scatter_ax(app, app._ax_scatter)

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
        fluor_gate_x=fluor_gate_x,
        fluor_gate_y=fluor_gate_y,
    )

    # Stack one group per panel (>1 group) when Layout is "Separate axes", or
    # whenever "Color by time" is on — overlaid time-coloured clouds are noise,
    # so each well gets its own time-coloured panel via _render_scatter_faceted.
    color_by_tp = bool(app._app_state.scatter.color_by_tp)
    _lay = getattr(app, "_scatter_layout_var", None)
    _separate = _lay is not None and str(_lay.currentText()).startswith("Separate")
    if len(scatter_data) > 1 and (color_by_tp or _separate):
        _render_scatter_faceted(app, scatter_data, col_x, col_y, tps_list)
        return

    # ``render_scatter_points`` owns the solid / density / color-by-time branch,
    # returning the click-interaction points and the legend handles. Density mode
    # + log state come from the shared export-style prefs, re-read each redraw.
    prefs = getattr(app, "_export_style_prefs", None) or {}
    density_on = bool(prefs.get("scatter_density", False))
    interaction_points, legend_handles = render_scatter_points(
        app, app._ax_scatter, scatter_data,
        density_on=density_on,
        x_log=bool(prefs.get("scatter_x_log", True)),
        y_log=bool(prefs.get("scatter_y_log", True)),
        marker_size=float(prefs.get("marker_size", 5.0) or 5.0),
        color_by_tp=color_by_tp,
    )

    # Format axes — derive readable label from column name
    def _col_label(col: str) -> str:
        from well_viewer.data.metric_labels import split_metric_col
        if is_shape_column(col):
            from pipeline.shape_features import label_for as _shape_label_for
            return _shape_label_for(col) or col
        parts = split_metric_col(col)
        if parts is None:
            return col
        _ch, metric_key, label = parts
        if metric_key == "smfish_count":
            ch = label.split(" ")[0]
            return f"{ch} smFISH Count"
        return label

    app._ax_scatter.set_xlabel(_col_label(col_x) + log1p_axis_suffix(bool(prefs.get("scatter_x_log", True))))
    app._ax_scatter.set_ylabel(_col_label(col_y) + log1p_axis_suffix(bool(prefs.get("scatter_y_log", True))))
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
        # Transparent frame keeps the legend off the plot background. Density and
        # color-by-time supply explicit handles; plain solid mode uses labels.
        if legend_handles:
            _leg = app._ax_scatter.legend(
                handles=legend_handles, loc='best', fontsize=8,
                framealpha=0.0, facecolor="none",
            )
        else:
            _leg = app._ax_scatter.legend(loc='best', fontsize=8, framealpha=0.0, facecolor="none")
        style_legend(_leg, app._ax_scatter)

    # Draw stored rectangular gates + their percentage labels on top of the
    # points. Gate coords are data-space, so they track log scale / limits.
    paint_gates(app, [(app._ax_scatter, scatter_data, tps_list, _muted_fg, True)], cols=(col_x, col_y))

    # Redraw canvas
    app._scatter_interaction_cache = {
        "points": interaction_points,
        # Overlay is a single panel: pair the points with the one axis so the
        # click/hover handlers share one panel-resolution path with the
        # "Separate axes" layout (see _layout._render_scatter_faceted).
        "axes_points": [(app._ax_scatter, interaction_points)],
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
        apply_export_style_to_current(app, app._scatter_fig,
                                      getattr(app, "_scatter_canvas", None))
    except Exception:  # pragma: no cover
        swallowed()  # was: pass (silent except)
    # ax.clear() at the top of this redraw voids any live RectangleSelector;
    # rebuild it against the fresh axes when gate-draw mode is armed.
    refresh_gate_selector(app)
    app._scatter_canvas.draw()


def _finite_positive(v) -> bool:
    """True iff *v* is a real number strictly greater than 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return 0.0 < f < float("inf")  # finite & positive (NaN/inf compare False)


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
    # Shared axis theming (audit A16); the aggregate figure tracks the theme bg.
    _bg, _title_fg, _muted_fg, _grid, _spine = _style_scatter_ax(
        app, app._ax_scatter_agg, fig=app._scatter_agg_fig)

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
                swallowed()  # was: pass (silent except)

        # Format axes
        app._ax_scatter_agg.set_xlabel(stat_x)
        app._ax_scatter_agg.set_ylabel(stat_y)
        tp_range = f"t={min(timepoints_h)}h to {max(timepoints_h)}h" if timepoints_h else ""
        app._ax_scatter_agg.set_title(
            f"Per-well aggregate ({tp_range})" if tp_range else "Per-well aggregate",
            color=_title_fg,
        )
        app._ax_scatter_agg.grid(True, alpha=0.3, color=_grid)
        _leg_agg = app._ax_scatter_agg.legend(loc='best', fontsize=8, framealpha=0.0, facecolor="none")
        style_legend(_leg_agg, app._ax_scatter_agg)

    # Redraw canvas
    try:
        apply_export_style_to_current(app, app._scatter_agg_fig,
                                      getattr(app, "_scatter_agg_canvas", None))
    except Exception:  # pragma: no cover
        swallowed()  # was: pass (silent except)
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

    fluor_gate_x = app._get_fluor_gate(ch_x_base)
    fluor_gate_y = app._get_fluor_gate(ch_y_base)

    metric_x = app._scatter_axis_metric_key("x")
    metric_y = app._scatter_axis_metric_key("y")
    col_x = app._col_for_scatter_axis(ch_x_entry, metric_x)
    col_y = app._col_for_scatter_axis(ch_y_entry, metric_y)

    from well_viewer.domain.plate_layout import WELL_COLORS
    redraw_scatter(
        app,
        col_x,
        col_y,
        tps_h,
        well_colors=WELL_COLORS,
        fluor_gate_x=fluor_gate_x,
        fluor_gate_y=fluor_gate_y,
    )
    apply_export_style_to_current(app, app._scatter_fig, getattr(app, "_scatter_canvas", None))


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

    from well_viewer.domain.plate_layout import WELL_COLORS
    redraw_scatter_agg(
        app,
        stat_x,
        stat_y,
        selected_timepoints,
        well_colors=WELL_COLORS,
    )
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
    scatter_ch_options = [str(c).upper() for c in channels] + ratio_labels

    # The per-cell scatter additionally offers the shape-feature virtual
    # channels: each point is a cell, so per-cell morphology (eccentricity,
    # area, …) is a natural axis. They are channel-independent and shown under
    # their own section, mirroring the line/bar channel dropdown. The aggregate
    # scatter keeps fluor+ratio only — its stat-string aggregation has no
    # shape-feature path. ``_label_to_channel_key`` (built by
    # ``_update_channel_selector``) maps each label back to its ``shape_*``
    # column so ``_col_for_scatter_axis`` resolves it like a ratio.
    from pipeline.shape_features import label_for as _shape_label_for
    from well_viewer.core.ui_helpers import set_combo_values_sectioned as _set_combo_values_sectioned
    shape_cols = list(app._app_state.channel.shape_features or [])
    shape_labels = [_shape_label_for(c) or c for c in shape_cols]
    cells_ch_options = scatter_ch_options + shape_labels
    cells_sections = [("", list(scatter_ch_options))]
    if shape_labels:
        cells_sections.append(("Shape Features", shape_labels))

    _set_combo_values_sectioned(app._scatter_ch_x_cb, cells_sections)
    _set_combo_values_sectioned(app._scatter_ch_y_cb, cells_sections)

    if cells_ch_options:
        if app._scatter_ch_x_var.currentText() not in cells_ch_options:
            app._scatter_ch_x_var.setCurrentText(cells_ch_options[0])
        if app._scatter_ch_y_var.currentText() not in cells_ch_options:
            app._scatter_ch_y_var.setCurrentText(cells_ch_options[0 if len(cells_ch_options) == 1 else 1])

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

    # Repopulate the multi-tp picker. PRESERVE the user's per-timepoint checks
    # across refreshes: the available timepoints are dataset-wide and don't
    # change when wells are added/removed, so a selection refresh (e.g. adding a
    # well to the plot) must not reset the timepoint filter. New timepoints
    # default to checked; ones no longer present are dropped; a fresh dataset
    # (no prior selections) lands in the "everything visible" state.
    prior_tp = getattr(app, "_scatter_tp_selections", None) or {}
    app._scatter_tp_selections = {
        tp_str: bool(prior_tp.get(tp_str, True)) for tp_str in tp_strs
    }
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

    # Update timepoint selections for aggregate scatter — preserve the user's
    # checks across refreshes (same rationale as the per-cell picker above): a
    # well add/remove must not reset the timepoint filter. New tps default
    # checked; missing ones dropped; a fresh dataset checks all.
    prior_agg_tp = getattr(app, "_scatter_agg_tp_selections", None) or {}
    app._scatter_agg_tp_selections = {
        tp_str: bool(prior_agg_tp.get(tp_str, True)) for tp_str in tp_strs
    }

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
    mapped_key = (getattr(app, "_label_to_channel_key", None) or {}).get(entry)
    if mapped_key and is_ratio_key(mapped_key):
        return mapped_key
    if mapped_key and is_shape_column(mapped_key):
        return mapped_key
    if entry.endswith(" (spots)"):
        ch = entry[:-8]  # Remove " (spots)"
        return f"{ch.lower()}_smfish_count"
    else:
        metric = app._app_state.channel.active_metric or "mean_intensity"
        if metric == "smfish_count":
            # The scatter dropdown uses the " (spots)" suffix for smfish;
            # fall back to mean intensity for the plain channel entry.
            metric = "mean_intensity"
        return f"{entry.lower()}_{metric}"



def col_for_scatter_axis(app, channel_entry: str, metric_key: str) -> str:
    """Map a scatter (channel_entry, metric_key) pair to a CSV column or ratio key.

    Ratio channels collapse to the ratio key regardless of metric (ratios
    don't carry a metric suffix). Otherwise builds ``<channel>_<metric>``.
    ``metric_key`` may be ``fraction_above_threshold`` for the agg scatter
    — that's a synthetic key that downstream aggregation code treats as a
    Fraction-On aggregation against the channel's mean-intensity column.
    """
    mapped_key = (getattr(app, "_label_to_channel_key", None) or {}).get(channel_entry)
    if mapped_key and is_ratio_key(mapped_key):
        return mapped_key
    # Shape features are a single per-cell value (the ``shape_*`` column itself);
    # they carry no metric suffix, like ratios.
    if mapped_key and is_shape_column(mapped_key):
        return mapped_key
    if metric_key in ("fraction_above_threshold", "smfish_count"):
        # Fraction-on uses MFI as the underlying value column; smFISH
        # uses the dedicated count column. (.lower(): CSV cols are lower-case.)
        if metric_key == "smfish_count":
            return f"{channel_entry.lower()}_smfish_count"
        return f"{channel_entry.lower()}_mean_intensity"
    return f"{channel_entry.lower()}_{metric_key}"



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
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
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

