"""Heatmap tab rendering.

Renders an arbitrary R×C grid colored by a chosen metric (mean above
threshold, fraction above threshold, cell count, or ratio mean) at a
single timepoint. Supports click-to-select wells, modifier-aware
selection, and a hover-value status update.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from matplotlib import patches as mpatches
from matplotlib import colormaps as mpl_colormaps


from well_viewer.data.data_loading import (
    _all_fluor_values_filtered,
)
from well_viewer.features.heatmap.models import (
    HeatmapLayout,
    make_plate_layout,
)
from well_viewer.domain.ratio_models import is_ratio_key
from well_viewer.domain.stats_compute import aggregate_group
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import apply_export_style_to_current
from well_viewer.core.timepoint_sync import current_focus_tp, nearest_index


METRIC_MEAN = "Mean above threshold"
METRIC_MEAN_ALL = "Mean (all cells)"
METRIC_FRACTION = "Fraction above threshold"
METRIC_COUNT = "Cell count"
METRIC_RATIO = "Ratio value"  # legacy; not exposed in METRIC_OPTIONS — ratios are picked via the channel dropdown.

# "Mean above threshold" applies the per-channel ThreshFracOn cut (only
# meaningful for MFI-based columns); "Mean (all cells)" pools every cell
# that passes cell-area + FluorGating without the per-channel threshold
# filter, which is the right default when the active Property isn't MFI.
METRIC_OPTIONS = [METRIC_MEAN, METRIC_MEAN_ALL, METRIC_FRACTION, METRIC_COUNT]


def active_layout(app) -> HeatmapLayout:
    """Return the active layout, synthesizing the plate default when needed."""
    layouts = list(getattr(app, "_heatmap_layouts", []) or [])
    name = getattr(app, "_active_heatmap_layout_name", None)
    if name:
        for lay in layouts:
            if lay.name == name:
                return lay
    if layouts:
        return layouts[0]
    return make_plate_layout((app._app_state.dataset.well_paths or {}).keys())


def _selected_metric(app) -> str:
    cb = getattr(app, "_heatmap_metric_cb", None)
    if cb is None:
        return METRIC_MEAN
    return str(cb.currentText() or METRIC_MEAN)


def _selected_cmap(app) -> str:
    cb = getattr(app, "_heatmap_cmap_cb", None)
    if cb is None:
        return "viridis"
    # v2 LutSelector exposes lut() + isReversed() rather than currentText().
    if hasattr(cb, "lut"):
        name = cb.lut() or "viridis"
        if hasattr(cb, "isReversed") and cb.isReversed():
            name = f"{name}_r"
        return name
    return str(cb.currentText() or "viridis")


def _selected_tp(app) -> Optional[float]:
    slider = getattr(app, "_heatmap_tp_slider", None)
    tps: List[float] = list(getattr(app, "_heatmap_tp_values", []) or [])
    if slider is None or not tps:
        return None
    idx = max(0, min(slider.value(), len(tps) - 1))
    return tps[idx]


def sync_heatmap_slider_to_focus(app) -> None:
    """Move the slider + label to the shared focus timepoint (N5) when one is set
    and present in this tab's timepoints — so a timepoint chosen on another
    plotting tab (Distribution) is reflected here. Signals are blocked so this
    silent sync never re-fires ``_on_tp_changed``. No-op when no focus is set."""
    focus = current_focus_tp(app)
    if focus is None:
        return
    tps: List[float] = list(getattr(app, "_heatmap_tp_values", []) or [])
    i = nearest_index(tps, focus)
    if i is None:
        return
    slider = getattr(app, "_heatmap_tp_slider", None)
    if slider is not None and slider.value() != i:
        blocked = slider.blockSignals(True)
        try:
            slider.setValue(i)
        finally:
            slider.blockSignals(blocked)
    label = getattr(app, "_heatmap_tp_label", None)
    if label is not None:
        label.setText(f"t = {tps[i]:g} h")


def _expand_repset_wells(app, wells: Iterable[str]) -> List[str]:
    """If the rep-set average toggle is on, expand each input well to every
    well in the rep-set it belongs to. Otherwise return wells as-is.
    """
    src = list(wells)
    if not bool(getattr(app, "_heatmap_repset_avg", False)):
        return src
    get_loaded = getattr(app, "_rep_sets_loaded", None)
    rep_sets = list(get_loaded() if callable(get_loaded) else [])
    if not rep_sets:
        return src
    out: List[str] = []
    seen: set = set()
    for w in src:
        chosen: Optional[List[str]] = None
        for rs in rep_sets:
            members = list(getattr(rs, "wells", []) or [])
            if w in members:
                chosen = members
                break
        if chosen is None:
            if w not in seen:
                seen.add(w)
                out.append(w)
        else:
            for m in chosen:
                if m not in seen:
                    seen.add(m)
                    out.append(m)
    return out


def _repset_name_for_wells(app, wells: Iterable[str]) -> Optional[str]:
    """Return the name of the first rep-set that contains any of *wells*, or None."""
    if not bool(getattr(app, "_heatmap_repset_avg", False)):
        return None
    get_loaded = getattr(app, "_rep_sets_loaded", None)
    rep_sets = list(get_loaded() if callable(get_loaded) else [])
    if not rep_sets:
        return None
    src = list(wells)
    for w in src:
        for rs in rep_sets:
            members = list(getattr(rs, "wells", []) or [])
            if w in members:
                return str(getattr(rs, "name", "") or "")
    return None


def _pool_filtered_mean(
    app,
    wells,
    *,
    val_col: str,
    fluor_gates: Dict[str, float],
    ratios,
    target_t: float,
) -> float:
    """Return the mean of ``_all_fluor_values_filtered`` across *wells*.

    Filters each well's DataFrame separately and concatenates the
    resulting 1-D float arrays — much cheaper than ``pd.concat`` on
    the full per-well DataFrames inside the heatmap's O(T·R·C) inner
    loop.
    """
    parts: List[np.ndarray] = []
    for w in wells:
        df = app._get_rows(w)
        if df is None or df.empty:
            continue
        vals = _all_fluor_values_filtered(
            df, val_col=val_col,
            fluor_gates=fluor_gates,
            ratios=ratios,
            tp_filter=target_t,
        )
        if vals.size:
            parts.append(vals)
    if not parts:
        return float("nan")
    pooled = parts[0] if len(parts) == 1 else np.concatenate(parts)
    return float(pooled.mean())


def _cell_value(
    app,
    wells: Iterable[str],
    target_t: float,
    metric: str,
    val_col: str,
    threshold: float,
    fluor_gates: Dict[str, float],
    ratios: Optional[Dict] = None,
) -> float:
    """Compute the heatmap cell value by pooling rows across *wells*."""
    expanded = _expand_repset_wells(app, wells)
    valid_wells = [w for w in expanded if w in app._app_state.dataset.well_paths]
    if not valid_wells:
        return float("nan")

    if metric == METRIC_COUNT:
        # Use aggregate to apply gate logic consistently, then read n_total.
        pts = aggregate_group(app,
            valid_wells, threshold=threshold, use_sem=False,
            val_col=val_col,
            fluor_gates=fluor_gates,
        )
        matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
        return float(matched[0][5]) if matched else 0.0

    if metric == METRIC_FRACTION:
        pts = aggregate_group(app,
            valid_wells, threshold=threshold, use_sem=False,
            val_col=val_col,
            fluor_gates=fluor_gates,
        )
        matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
        if not matched:
            return float("nan")
        return float(matched[0][3])

    if metric == METRIC_RATIO:
        if not is_ratio_key(val_col):
            return float("nan")
        # Run the filter per well and concat the resulting 1-D float
        # arrays instead of pd.concat'ing the per-well DataFrames.
        # `_compute_global_range` calls this O(T·R·C) times per
        # heatmap redraw — full-frame concat in the hot loop was the
        # bottleneck on slider scrubs.
        return _pool_filtered_mean(
            app, valid_wells,
            val_col=val_col,
            fluor_gates=fluor_gates,
            ratios=ratios,
            target_t=target_t,
        )

    if metric == METRIC_MEAN_ALL:
        # Mean across every included cell at the target timepoint — no
        # per-channel ThreshFracOn filter applied. Useful for non-MFI
        # properties (Total / Max / Min / Std) where the MFI threshold is
        # semantically irrelevant.
        return _pool_filtered_mean(
            app, valid_wells,
            val_col=val_col,
            fluor_gates=fluor_gates,
            ratios=ratios,
            target_t=target_t,
        )

    # METRIC_MEAN (and fallback)
    pts = aggregate_group(app,
        valid_wells, threshold=threshold, use_sem=False,
        val_col=val_col,
        fluor_gates=fluor_gates,
    )
    matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
    if not matched:
        return float("nan")
    return float(matched[0][1])


def _resolve_color_scale(arr: np.ndarray, app) -> Tuple[Optional[float], Optional[float]]:
    """Pick (vmin, vmax) for the heatmap.

    Mode is read from ``app._heatmap_scale_mode`` (Auto/Fixed). For Fixed we
    read ``app._heatmap_vmin`` / ``app._heatmap_vmax``. For Auto we use the
    global range pre-computed across every timepoint (kept on
    ``app._heatmap_global_vmin`` / ``app._heatmap_global_vmax``) so the
    colormap stays stable while scrubbing the timepoint slider.
    """
    mode = str(getattr(app, "_heatmap_scale_mode", "Auto") or "Auto")
    if mode == "Fixed":
        vmin = float(getattr(app, "_heatmap_vmin", float("nan")) or float("nan"))
        vmax = float(getattr(app, "_heatmap_vmax", float("nan")) or float("nan"))
        if math.isfinite(vmin) and math.isfinite(vmax) and vmax > vmin:
            return vmin, vmax
    # Auto: prefer the global range across all timepoints.
    g_vmin = getattr(app, "_heatmap_global_vmin", None)
    g_vmax = getattr(app, "_heatmap_global_vmax", None)
    if (
        g_vmin is not None and g_vmax is not None
        and math.isfinite(g_vmin) and math.isfinite(g_vmax) and g_vmax > g_vmin
    ):
        return float(g_vmin), float(g_vmax)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None, None
    return float(finite.min()), float(finite.max())


def _compute_global_range(
    app,
    layout,
    metric: str,
    val_col: str,
    threshold: float,
    fluor_gates: Dict[str, float],
    ratios,
) -> Tuple[Optional[float], Optional[float]]:
    """Pool ``_cell_value`` across every (timepoint, cell) and return (vmin, vmax).

    Result is cached on ``app`` keyed on the inputs that influence it so
    timepoint-slider scrubbing does not retrigger the full sweep.
    """
    tps = list(getattr(app, "_heatmap_tp_values", []) or [])
    if not tps:
        return None, None

    # Content-based cache key. ``id(layout)`` / ``id(ratios)`` changed
    # every time the runtime app rebuilt either object, missing the
    # cache on slider scrubs where the underlying data was identical.
    # Use the layout's structural shape and the ratios dict's sorted
    # key tuple — both are cheap to compute relative to the O(T·R·C)
    # sweep below.
    layout_fingerprint = (
        getattr(layout, "name", ""),
        int(getattr(layout, "rows", 0)),
        int(getattr(layout, "cols", 0)),
        tuple(
            sorted(
                ((int(r), int(c)), tuple(sorted(w or [])))
                for (r, c), w in (getattr(layout, "cells", {}) or {}).items()
            )
        ),
    )
    ratios_fingerprint = tuple(sorted((ratios or {}).keys()))
    cache_key = (
        layout_fingerprint,
        metric,
        val_col,
        float(threshold),
        tuple(sorted((fluor_gates or {}).items())),
        ratios_fingerprint,
        tuple(tps),
        bool(getattr(app, "_heatmap_repset_avg", False)),
        bool(getattr(app, "_heatmap_log_scale", False)),
    )
    cached = getattr(app, "_heatmap_global_range_cache", None)
    if cached is not None and cached.get("key") == cache_key:
        return cached["vmin"], cached["vmax"]

    vmin: Optional[float] = None
    vmax: Optional[float] = None
    for tp in tps:
        for (r, c), wells in layout.cells.items():
            if not (0 <= r < layout.rows and 0 <= c < layout.cols):
                continue
            try:
                v = _cell_value(
                    app, wells, tp, metric, val_col,
                    threshold, fluor_gates, ratios,
                )
            except Exception:
                v = float("nan")
            if math.isfinite(v):
                vmin = v if vmin is None else min(vmin, v)
                vmax = v if vmax is None else max(vmax, v)

    app._heatmap_global_range_cache = {"key": cache_key, "vmin": vmin, "vmax": vmax}
    return vmin, vmax


def redraw_heatmap(app) -> None:
    ax = getattr(app, "_heatmap_ax", None)
    canvas = getattr(app, "_heatmap_canvas", None)
    fig = getattr(app, "_heatmap_fig", None)
    if ax is None or canvas is None or fig is None:
        return

    layout = active_layout(app)
    sync_heatmap_slider_to_focus(app)
    tp = _selected_tp(app)
    if tp is None:
        # Try to derive from the data; if still none, leave the canvas blank.
        ax.clear()
        ax.text(
            0.5, 0.5, "No timepoints loaded.",
            ha="center", va="center", transform=ax.transAxes,
            fontsize=10, color="gray",
        )
        ax.set_axis_off()
        canvas.draw_idle()
        return

    metric = _selected_metric(app)
    val_col = app._app_state.channel.active_val_col
    # Shapes keep every cell (no ON cutoff); smFISH counts honour the
    # channel's ThreshFracOn like intensities (count > cutoff is "on").
    if app._app_state.channel.is_shape_active():
        threshold = -1.0
    else:
        threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
    fluor_gates = app._get_all_fluor_gates()
    ratios = app._app_state.channel.ratio_index
    cmap_name = _selected_cmap(app)

    arr = np.full((layout.rows, layout.cols), np.nan, dtype=float)
    cell_well_index: Dict[Tuple[int, int], List[str]] = {}

    for (r, c), wells in layout.cells.items():
        if not (0 <= r < layout.rows and 0 <= c < layout.cols):
            continue
        cell_well_index[(r, c)] = list(wells)
        try:
            arr[r, c] = _cell_value(
                app, wells, tp, metric, val_col,
                threshold, fluor_gates, ratios,
            )
        except Exception:
            arr[r, c] = float("nan")

    # Cache for click/hover handlers.
    app._heatmap_cell_well_index = cell_well_index
    app._heatmap_array = arr
    app._heatmap_layout_active = layout

    # Pool min/max across every timepoint so the colormap stays consistent
    # while the user scrubs the timepoint slider. Cached on app so this
    # only recomputes when an input changes.
    g_vmin, g_vmax = _compute_global_range(
        app, layout, metric, val_col,
        threshold, fluor_gates, ratios,
    )
    app._heatmap_global_vmin = g_vmin
    app._heatmap_global_vmax = g_vmax

    # Render. Resolve the active plot-theme tokens up front so the empty-cell
    # fill, figure background, and axes background all track the theme — the
    # heatmap deliberately skips PlotCard.setPlotTheme on redraw, so without
    # this the figure rendered on matplotlib's default white inside the dark UI.
    from well_viewer.core.plot_style import tokens_for as _tokens_for
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for(ax)
    fig.patch.set_facecolor(_bg)

    vmin, vmax = _resolve_color_scale(arr, app)
    cmap = mpl_colormaps.get_cmap(cmap_name).copy()
    cmap.set_bad(color=_bg)  # empty cells blend into the themed background

    masked = np.ma.masked_invalid(arr)

    # Log-scale toggle: route values through a LogNorm so the colorbar
    # spans orders of magnitude. Values ≤ 0 become invalid (masked).
    norm = None
    log_scale = bool(getattr(app, "_heatmap_log_scale", False))
    if log_scale:
        from matplotlib.colors import LogNorm
        positive = masked.filled(np.nan)
        positive = positive[np.isfinite(positive) & (positive > 0)]
        log_vmin = float(np.min(positive)) if positive.size else None
        log_vmax = float(np.max(positive)) if positive.size else None
        # User-fixed vmin/vmax win when they're positive.
        if (vmin is not None and vmin > 0) and (vmax is not None and vmax > vmin):
            log_vmin, log_vmax = float(vmin), float(vmax)
        if log_vmin is not None and log_vmax is not None and log_vmax > log_vmin > 0:
            norm = LogNorm(vmin=log_vmin, vmax=log_vmax)
            # Mask non-positive entries so LogNorm doesn't choke.
            masked = np.ma.masked_where(~np.isfinite(masked.filled(np.nan)) | (masked.filled(np.nan) <= 0), masked)

    ax.clear()
    ax.set_facecolor(_bg)  # ax.clear() resets to white; re-apply the theme bg
    if norm is not None:
        im = ax.imshow(masked, aspect="equal", cmap=cmap, norm=norm,
                       origin="upper", interpolation="nearest")
    else:
        im = ax.imshow(masked, aspect="equal", cmap=cmap, vmin=vmin, vmax=vmax,
                       origin="upper", interpolation="nearest")

    # Always materialise label lists of the right length. Persisted layouts
    # can carry shorter ``row_labels`` / ``col_labels`` (e.g. one entry left
    # over from a resize) — ``layout.col_labels or [...]`` would short-
    # circuit to the truthy short list and produce a labels/ticks-count
    # mismatch ValueError when matplotlib's FixedLocator validates them.
    raw_row_labels = list(layout.row_labels or [])
    raw_col_labels = list(layout.col_labels or [])
    row_labels = [
        str(raw_row_labels[i]) if i < len(raw_row_labels) and raw_row_labels[i] else str(i + 1)
        for i in range(layout.rows)
    ]
    col_labels = [
        str(raw_col_labels[i]) if i < len(raw_col_labels) and raw_col_labels[i] else str(i + 1)
        for i in range(layout.cols)
    ]
    ax.set_xticks(range(layout.cols))
    ax.set_xticklabels(col_labels, fontsize=8)
    ax.xaxis.tick_top()
    ax.set_yticks(range(layout.rows))
    ax.set_yticklabels(row_labels, fontsize=8)
    setattr(ax, "_categorical_xaxis", True)
    setattr(ax, "_categorical_yaxis", True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)

    # Row/col drag-reorder visual: colour source blue, drop-target red.
    drag = getattr(app, "_heatmap_label_drag", None)
    if drag is not None:
        _kind = drag["kind"]
        _src = drag["src"]
        _over = drag.get("over")
        _drag_labels = ax.get_xticklabels() if _kind == "col" else ax.get_yticklabels()
        for _i, _lbl in enumerate(_drag_labels):
            if _i == _src:
                _lbl.set_color("#2563EB")
                _lbl.set_fontweight("bold")
            elif _over is not None and _i == _over and _over != _src:
                _lbl.set_color("#DC2626")
                _lbl.set_fontweight("bold")

    # Cell text annotations.
    for (r, c), wells in cell_well_index.items():
        v = arr[r, c]
        if not math.isfinite(v):
            continue
        rs_name = _repset_name_for_wells(app, wells)
        if rs_name:
            text = rs_name
        elif len(wells) == 1:
            text = wells[0]
        else:
            text = f"{wells[0]}+{len(wells) - 1}"
        ax.text(
            c, r + 0.32, text,
            ha="center", va="center",
            fontsize=6, color="black", alpha=0.8,
        )
        ax.text(
            c, r - 0.05, _format_cell_value(v, metric),
            ha="center", va="center",
            fontsize=8, color="black",
        )

    # Selection rectangles.
    selected = set(app._app_state.selection.selected_wells or set())
    for (r, c), wells in cell_well_index.items():
        if any(w in selected for w in wells):
            rect = mpatches.Rectangle(
                (c - 0.5, r - 0.5), 1.0, 1.0,
                fill=False, edgecolor="#FF6B35", linewidth=2.0,
            )
            ax.add_patch(rect)

    # Color bar — refill the persistent ``cax`` reserved at build time so
    # the main axes geometry stays put across redraws. Calling
    # ``fig.colorbar(im, ax=ax, ...)`` would silently shrink ``ax`` on
    # each redraw, which is what made the plot creep left and shrink.
    # Theme-aware text colors so the colorbar/title invert with the PlotCard's
    # publication↔screen toggle instead of staying mpl-default black.
    # Theme tokens (_bg/_title_fg/_muted_fg/_spine) were resolved up front in
    # the render section above and the figure/axes backgrounds already painted.
    cax = getattr(app, "_heatmap_cax", None)
    if cax is not None:
        try:
            cax.cla()
            app._heatmap_colorbar = fig.colorbar(im, cax=cax)
            app._heatmap_colorbar.set_label(_metric_axis_label(metric, app),
                                            fontsize=8, color=_muted_fg)
            cax.tick_params(colors=_muted_fg, labelsize=7)
            for _spi in cax.spines.values():
                _spi.set_color(_spine)
        except Exception:
            app._heatmap_colorbar = None
    else:
        if hasattr(app, "_heatmap_colorbar") and app._heatmap_colorbar is not None:
            try:
                app._heatmap_colorbar.remove()
            except Exception:
                swallowed()  # was: pass (silent except)
        try:
            app._heatmap_colorbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.03)
            app._heatmap_colorbar.set_label(_metric_axis_label(metric, app),
                                            fontsize=8, color=_muted_fg)
            app._heatmap_colorbar.ax.tick_params(colors=_muted_fg, labelsize=7)
            for _spi in app._heatmap_colorbar.ax.spines.values():
                _spi.set_color(_spine)
        except Exception:
            app._heatmap_colorbar = None

    title = (
        f"{app._active_channel_label() if hasattr(app, '_active_channel_label') else app._app_state.channel.active_channel.upper()}"
        f" — {metric} — t = {tp:g} h"
    )
    # ``pad`` lifts the title away from the heatmap rows so the descenders
    # never overlap the top row of cells; the figure's top=0.88 margin
    # reserves enough room for the lifted title.
    ax.set_title(title, fontsize=9, color=_title_fg, pad=8)

    # Apply the Export Style sidebar prefs (font sizes, grid, log scale, …)
    # so toggling those in the configurator survives a redraw.
    try:
        apply_export_style_to_current(app, fig, canvas)
    except Exception:
        swallowed()  # was: pass (silent except)

    canvas.draw_idle()


def _format_cell_value(v: float, metric: str) -> str:
    if not math.isfinite(v):
        return ""
    if metric == METRIC_COUNT:
        return f"{int(round(v))}"
    if metric == METRIC_FRACTION:
        return f"{v:.2f}"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.3g}"


def _metric_axis_label(metric: str, app) -> str:
    label = app._active_channel_label() if hasattr(app, "_active_channel_label") else ""
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL as _MLB
    prop_label = _MLB.get(app._app_state.channel.active_metric, "Mean Intensity")
    if metric == METRIC_COUNT:
        return "Cell count"
    if metric == METRIC_FRACTION:
        # smFISH counts now honour ThreshFracOn like intensities, so the
        # generic "above threshold" wording is accurate for every metric.
        return f"{label} fraction above threshold"
    if metric == METRIC_RATIO:
        return f"{label} (mean ratio)"
    if metric == METRIC_MEAN_ALL:
        return f"{label} {prop_label} (mean of all cells)"
    return f"{label} {prop_label} (mean above threshold)"


# ── Click + hover handlers ───────────────────────────────────────────────────

def _cell_at_event(app, event) -> Optional[Tuple[int, int]]:
    if event.inaxes is None or event.xdata is None or event.ydata is None:
        return None
    layout = getattr(app, "_heatmap_layout_active", None)
    if layout is None:
        return None
    c = int(round(event.xdata))
    r = int(round(event.ydata))
    if not (0 <= r < layout.rows and 0 <= c < layout.cols):
        return None
    return r, c


def on_heatmap_click(app, event) -> None:
    rc = _cell_at_event(app, event)
    if rc is None:
        return
    cell_index = getattr(app, "_heatmap_cell_well_index", {}) or {}
    wells_here = list(cell_index.get(rc, []))
    if not wells_here:
        return

    selected = set(app._app_state.selection.selected_wells or set())
    modifiers = 0
    try:
        # Matplotlib events expose the GUI event for modifier inspection.
        gui_event = getattr(event, "guiEvent", None)
        if gui_event is not None and hasattr(gui_event, "modifiers"):
            modifiers = int(gui_event.modifiers())
    except Exception:
        modifiers = 0

    from PySide6.QtCore import Qt as _Qt
    shift = bool(modifiers & int(_Qt.ShiftModifier))
    ctrl = bool(modifiers & int(_Qt.ControlModifier))

    if shift:
        selected.update(wells_here)
    elif ctrl:
        for w in wells_here:
            if w in selected:
                selected.discard(w)
            else:
                selected.add(w)
    else:
        selected = set(wells_here)

    if hasattr(app, "_set_selected_wells"):
        app._set_selected_wells(selected)
    else:
        app._app_state.selection.selected_wells = selected
        if hasattr(app, "_redraw"):
            app._redraw()


def on_heatmap_motion(app, event) -> None:
    rc = _cell_at_event(app, event)
    status_lbl = getattr(app, "_heatmap_status_lbl", None)
    if rc is None:
        if status_lbl is not None:
            status_lbl.setText("")
        return
    cell_index = getattr(app, "_heatmap_cell_well_index", {}) or {}
    arr = getattr(app, "_heatmap_array", None)
    wells = cell_index.get(rc, [])
    if status_lbl is not None:
        if not wells:
            status_lbl.setText(f"({rc[0]},{rc[1]}): empty")
        else:
            v = arr[rc[0], rc[1]] if arr is not None else float("nan")
            sample = ",".join(wells[:3]) + ("…" if len(wells) > 3 else "")
            metric = _selected_metric(app)
            status_lbl.setText(f"{sample}: {_format_cell_value(v, metric)}")


# ── Row/col label drag-to-reorder ────────────────────────────────────────────

def _label_at_event(app, event) -> Optional[Tuple[str, int]]:
    """Return ("row", i) or ("col", i) if *event* is near a tick label, else None.

    Used only for the initial press that starts a drag, so the threshold is
    generous (12 px) but still requires the cursor to be close to a label.
    """
    ax = getattr(app, "_heatmap_ax", None)
    canvas = getattr(app, "_heatmap_canvas", None)
    if ax is None or canvas is None or event.x is None or event.y is None:
        return None
    try:
        renderer = canvas.get_renderer()
    except Exception:
        return None
    pad = 12
    for i, lbl in enumerate(ax.get_xticklabels()):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
            if (bb.x0 - pad <= event.x <= bb.x1 + pad and
                    bb.y0 - pad <= event.y <= bb.y1 + pad):
                return ("col", i)
        except Exception:
            swallowed()  # silent except
            continue
    for i, lbl in enumerate(ax.get_yticklabels()):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
            if (bb.x0 - pad <= event.x <= bb.x1 + pad and
                    bb.y0 - pad <= event.y <= bb.y1 + pad):
                return ("row", i)
        except Exception:
            swallowed()  # silent except
            continue
    return None


def _nearest_label_in_drag(app, event, kind: str) -> Optional[int]:
    """Flexibly find the target row/col index during a drag.

    Prefers the data coordinate (when the cursor is inside the axes) so the
    user can release anywhere along a column strip or row strip.  Falls back
    to nearest-label-by-pixel when the cursor is outside the axes (e.g. over
    the label gutter itself).
    """
    ax = getattr(app, "_heatmap_ax", None)
    canvas = getattr(app, "_heatmap_canvas", None)
    if ax is None or canvas is None or event.x is None or event.y is None:
        return None

    layout = getattr(app, "_heatmap_layout_active", None)
    if layout is None:
        layout = active_layout(app)

    # Inside the axes: data coords give us the exact row/col directly.
    if event.inaxes is ax:
        if kind == "col" and event.xdata is not None:
            col = int(round(event.xdata))
            if 0 <= col < layout.cols:
                return col
        if kind == "row" and event.ydata is not None:
            row = int(round(event.ydata))
            if 0 <= row < layout.rows:
                return row

    # Outside axes (e.g. label gutter): snap to nearest label by pixel centre.
    try:
        renderer = canvas.get_renderer()
    except Exception:
        return None
    labels = ax.get_xticklabels() if kind == "col" else ax.get_yticklabels()
    best: Optional[int] = None
    best_dist = float("inf")
    for i, lbl in enumerate(labels):
        try:
            bb = lbl.get_window_extent(renderer=renderer)
            cx = (bb.x0 + bb.x1) / 2
            cy = (bb.y0 + bb.y1) / 2
            dist = abs(event.x - cx) if kind == "col" else abs(event.y - cy)
            if dist < best_dist:
                best_dist = dist
                best = i
        except Exception:
            swallowed()  # silent except
            continue
    return best


def _update_drag_label_colors(app) -> None:
    """Apply drag highlight colours to existing tick label artists and redraw."""
    ax = getattr(app, "_heatmap_ax", None)
    canvas = getattr(app, "_heatmap_canvas", None)
    if ax is None or canvas is None:
        return
    drag = getattr(app, "_heatmap_label_drag", None)
    # Reset all labels to default.
    for lbl in ax.get_xticklabels():
        lbl.set_color("black")
        lbl.set_fontweight("normal")
    for lbl in ax.get_yticklabels():
        lbl.set_color("black")
        lbl.set_fontweight("normal")
    if drag is not None:
        kind = drag["kind"]
        src = drag["src"]
        over = drag.get("over")
        labels = ax.get_xticklabels() if kind == "col" else ax.get_yticklabels()
        for i, lbl in enumerate(labels):
            if i == src:
                lbl.set_color("#2563EB")
                lbl.set_fontweight("bold")
            elif over is not None and i == over and over != src:
                lbl.set_color("#DC2626")
                lbl.set_fontweight("bold")
    canvas.draw_idle()


def _set_canvas_cursor(app, cursor_shape) -> None:
    canvas = getattr(app, "_heatmap_canvas", None)
    if canvas is None:
        return
    try:
        if cursor_shape is None:
            canvas.unsetCursor()
        else:
            canvas.setCursor(cursor_shape)
    except Exception:
        swallowed()  # was: pass (silent except)


def on_heatmap_label_drag_press(app, event) -> bool:
    """Start a label drag on left-press over a tick label. Returns True if consumed."""
    if getattr(event, "button", None) != 1:
        return False
    if getattr(event, "dblclick", False):
        return False
    target = _label_at_event(app, event)
    if target is None:
        return False
    kind, idx = target
    app._heatmap_label_drag = {"kind": kind, "src": idx, "over": idx}
    _update_drag_label_colors(app)
    try:
        from PySide6.QtCore import Qt as _Qt
        _set_canvas_cursor(app, _Qt.ClosedHandCursor)
    except Exception:
        swallowed()  # was: pass (silent except)
    return True


def on_heatmap_label_drag_motion(app, event) -> None:
    """Update drag highlight while the mouse moves."""
    drag = getattr(app, "_heatmap_label_drag", None)
    if drag is None:
        # Show open-hand cursor when hovering over any label.
        target = _label_at_event(app, event)
        try:
            from PySide6.QtCore import Qt as _Qt
            if target is not None:
                _set_canvas_cursor(app, _Qt.OpenHandCursor)
            else:
                _set_canvas_cursor(app, None)
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    # Use the flexible finder so the highlight tracks the cursor everywhere.
    drag["over"] = _nearest_label_in_drag(app, event, drag["kind"])
    _update_drag_label_colors(app)


def on_heatmap_label_drag_release(app, event) -> None:
    """Finish drag: apply reorder if source ≠ target, then clean up."""
    drag = getattr(app, "_heatmap_label_drag", None)
    if drag is None:
        return
    src = drag["src"]
    kind = drag["kind"]
    # Recompute target at the release position (motion may not have fired last).
    over = _nearest_label_in_drag(app, event, kind)
    if over is None:
        over = drag.get("over")
    app._heatmap_label_drag = None
    _set_canvas_cursor(app, None)
    if over is not None and over != src:
        _apply_label_reorder(app, kind, src, over)
    else:
        _update_drag_label_colors(app)  # just clear highlights


def _apply_label_reorder(app, kind: str, src: int, dst: int) -> None:
    from well_viewer.features.heatmap.sidebar_view import (
        _ensure_sidebar_layout,
        _persist_and_redraw,
        refresh_heatmap_layout_sidebar,
    )
    layout = _ensure_sidebar_layout(app)
    if kind == "row":
        layout.reorder_rows(src, dst)
    else:
        layout.reorder_cols(src, dst)
    _persist_and_redraw(app)
    refresh_heatmap_layout_sidebar(app)
