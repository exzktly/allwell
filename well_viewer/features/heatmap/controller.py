"""Heatmap tab rendering.

Renders an arbitrary R×C grid colored by a chosen metric (mean above
threshold, fraction above threshold, cell count, or ratio mean) at a
single timepoint. Supports click-to-select wells, modifier-aware
selection, and a hover-value status update.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import theme
from matplotlib import patches as mpatches
from matplotlib import colormaps as mpl_colormaps


from well_viewer.data.data_loading import (
    _all_fluor_values_filtered,
    get_rows,
)
from well_viewer.features.heatmap.models import (
    HeatmapLayout,
    make_plate_layout,
)
from well_viewer.domain.ratio_models import is_ratio_key
from well_viewer.domain.stats_compute import aggregate_group
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import apply_export_style_to_current
from well_viewer.core.channel_state_controller import (
    active_channel_label,
    get_all_fluor_gates,
    get_thresh_frac_on,
)
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

# Selection-rectangle outline. Deliberately its own colour, not
# ``theme.Colors.danger`` — ``danger`` already marks the drag-reorder
# drop-target label in this same axes (see the label-drag block below), and
# reusing it here would make a selected well indistinguishable from a drop
# target while dragging over it.
_SELECTION_OUTLINE_COLOR = "#FF6B35"


def _contrast_ink(rgba: tuple[float, float, float, float]) -> str:
    """Pick readable ink for a cell's rendered colour (via ``im.to_rgba``).

    Standard relative-luminance threshold. Takes the already-composited RGBA
    a specific value maps to — including colormap ``set_bad`` masking — so it
    agrees with what's actually drawn instead of recomputing the norm by hand.
    Distinct from ``image_table/image_compose.py``'s "Auto (contrast)" toggle,
    which measures mean raw-pixel RGB on the source image rather than a
    colormap+norm-mapped scalar; the two operate on different inputs and
    aren't interchangeable.
    """
    r, g, b = rgba[0], rgba[1], rgba[2]
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return theme.Colors.ink_dark if luminance > 0.6 else theme.Colors.ink_light


def active_layout(ctx) -> HeatmapLayout:
    """Return the active layout, synthesizing the plate default when needed."""
    layouts = list(ctx.runtime.heatmap.layouts or [])
    name = ctx.state.heatmap.active_layout_name
    if name:
        for lay in layouts:
            if lay.name == name:
                return lay
    if layouts:
        return layouts[0]
    return make_plate_layout((ctx.state.dataset.well_paths or {}).keys())


def _selected_metric(ctx) -> str:
    cb = ctx.widgets.get_or_none("_heatmap_metric_cb")
    if cb is None:
        return METRIC_MEAN
    return str(cb.currentText() or METRIC_MEAN)


def _selected_cmap(ctx) -> str:
    cb = ctx.widgets.get_or_none("_heatmap_cmap_cb")
    if cb is None:
        return "viridis"
    # v2 LutSelector exposes lut() + isReversed() rather than currentText().
    if hasattr(cb, "lut"):
        name = cb.lut() or "viridis"
        if hasattr(cb, "isReversed") and cb.isReversed():
            name = f"{name}_r"
        return name
    return str(cb.currentText() or "viridis")


def _selected_tp(ctx) -> float | None:
    slider = ctx.widgets.get_or_none("_heatmap_tp_slider")
    tps: list[float] = list(ctx.state.heatmap.tp_values or [])
    if slider is None or not tps:
        return None
    idx = max(0, min(slider.value(), len(tps) - 1))
    return tps[idx]


def sync_heatmap_slider_to_focus(ctx) -> None:
    """Move the slider + label to the shared focus timepoint (N5) when one is set
    and present in this tab's timepoints — so a timepoint chosen on another
    plotting tab (Distribution) is reflected here. Signals are blocked so this
    silent sync never re-fires ``_on_tp_changed``. No-op when no focus is set."""
    focus = current_focus_tp(ctx)
    if focus is None:
        return
    tps: list[float] = list(ctx.state.heatmap.tp_values or [])
    i = nearest_index(tps, focus)
    if i is None:
        return
    slider = ctx.widgets.get_or_none("_heatmap_tp_slider")
    if slider is not None and slider.value() != i:
        blocked = slider.blockSignals(True)
        try:
            slider.setValue(i)
        finally:
            slider.blockSignals(blocked)
    label = ctx.widgets.get_or_none("_heatmap_tp_label")
    if label is not None:
        label.setText(f"t = {tps[i]:g} h")


def _repsets_loaded(ctx) -> list:
    """Every loaded replicate set (incl. hidden) over ``ctx.state`` — the
    pool the heatmap's rep-set-average expansion walks. Derived from the
    domain resolver so no god-object / feature import is needed."""
    from well_viewer.domain.selections_model import replicate_sets_loaded
    return replicate_sets_loaded(ctx.state.selection.selections,
                                 ctx.state.dataset.well_paths)


def _expand_repset_wells(ctx, wells: Iterable[str]) -> list[str]:
    """If the rep-set average toggle is on, expand each input well to every
    well in the rep-set it belongs to. Otherwise return wells as-is.
    """
    src = list(wells)
    if not bool(ctx.state.heatmap.repset_avg):
        return src
    rep_sets = _repsets_loaded(ctx)
    if not rep_sets:
        return src
    out: list[str] = []
    seen: set = set()
    for w in src:
        chosen: list[str] | None = None
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


def _repset_name_for_wells(ctx, wells: Iterable[str]) -> str | None:
    """Return the name of the first rep-set that contains any of *wells*, or None."""
    if not bool(ctx.state.heatmap.repset_avg):
        return None
    rep_sets = _repsets_loaded(ctx)
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
    ctx,
    wells,
    *,
    val_col: str,
    fluor_gates: dict[str, float],
    ratios,
    target_t: float,
) -> float:
    """Return the mean of ``_all_fluor_values_filtered`` across *wells*.

    Filters each well's DataFrame separately and concatenates the
    resulting 1-D float arrays — much cheaper than ``pd.concat`` on
    the full per-well DataFrames inside the heatmap's O(T·R·C) inner
    loop.
    """
    parts: list[np.ndarray] = []
    for w in wells:
        df = get_rows(ctx, w)
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
    ctx,
    wells: Iterable[str],
    target_t: float,
    metric: str,
    val_col: str,
    threshold: float,
    fluor_gates: dict[str, float],
    ratios: dict | None = None,
) -> float:
    """Compute the heatmap cell value by pooling rows across *wells*."""
    expanded = _expand_repset_wells(ctx, wells)
    valid_wells = [w for w in expanded if w in ctx.state.dataset.well_paths]
    if not valid_wells:
        return float("nan")

    if metric == METRIC_COUNT:
        # Use aggregate to apply gate logic consistently, then read n_total.
        pts = aggregate_group(ctx,
            valid_wells, threshold=threshold, use_sem=False,
            val_col=val_col,
            fluor_gates=fluor_gates,
        )
        matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
        return float(matched[0][5]) if matched else 0.0

    if metric == METRIC_FRACTION:
        pts = aggregate_group(ctx,
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
            ctx, valid_wells,
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
            ctx, valid_wells,
            val_col=val_col,
            fluor_gates=fluor_gates,
            ratios=ratios,
            target_t=target_t,
        )

    # METRIC_MEAN (and fallback)
    pts = aggregate_group(ctx,
        valid_wells, threshold=threshold, use_sem=False,
        val_col=val_col,
        fluor_gates=fluor_gates,
    )
    matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
    if not matched:
        return float("nan")
    return float(matched[0][1])


def _resolve_color_scale(arr: np.ndarray, ctx) -> tuple[float | None, float | None]:
    """Pick (vmin, vmax) for the heatmap.

    Mode is read from ``ctx.state.heatmap.scale_mode`` (Auto/Fixed). For Fixed we
    read the persisted ``ctx.state.heatmap.vmin`` / ``.vmax``. For Auto we use the
    global range pre-computed across every timepoint (kept on
    ``ctx.runtime.heatmap.global_vmin`` / ``.global_vmax``) so the colormap
    stays stable while scrubbing the timepoint slider.
    """
    mode = str(ctx.state.heatmap.scale_mode or "Auto")
    if mode == "Fixed":
        vmin = float((ctx.state.heatmap.vmin if ctx.state.heatmap.vmin is not None
                      else float("nan")) or float("nan"))
        vmax = float((ctx.state.heatmap.vmax if ctx.state.heatmap.vmax is not None
                      else float("nan")) or float("nan"))
        if math.isfinite(vmin) and math.isfinite(vmax) and vmax > vmin:
            return vmin, vmax
    # Auto: prefer the global range across all timepoints.
    g_vmin = ctx.runtime.heatmap.global_vmin
    g_vmax = ctx.runtime.heatmap.global_vmax
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
    ctx,
    layout,
    metric: str,
    val_col: str,
    threshold: float,
    fluor_gates: dict[str, float],
    ratios,
) -> tuple[float | None, float | None]:
    """Pool ``_cell_value`` across every (timepoint, cell) and return (vmin, vmax).

    Result is cached on ``ctx.runtime.heatmap`` keyed on the inputs that
    influence it so timepoint-slider scrubbing does not retrigger the full sweep.
    """
    tps = list(ctx.state.heatmap.tp_values or [])
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
        bool(ctx.state.heatmap.repset_avg),
        bool(ctx.state.heatmap.log_scale),
    )
    cached = ctx.runtime.heatmap.global_range_cache
    if cached is not None and cached.get("key") == cache_key:
        return cached["vmin"], cached["vmax"]

    vmin: float | None = None
    vmax: float | None = None
    for tp in tps:
        for (r, c), wells in layout.cells.items():
            if not (0 <= r < layout.rows and 0 <= c < layout.cols):
                continue
            try:
                v = _cell_value(
                    ctx, wells, tp, metric, val_col,
                    threshold, fluor_gates, ratios,
                )
            except Exception:
                v = float("nan")
            if math.isfinite(v):
                vmin = v if vmin is None else min(vmin, v)
                vmax = v if vmax is None else max(vmax, v)

    ctx.runtime.heatmap.global_range_cache = {"key": cache_key, "vmin": vmin, "vmax": vmax}
    return vmin, vmax


def redraw_heatmap(ctx) -> None:
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    fig = ctx.widgets.get_or_none("_heatmap_fig")
    if ax is None or canvas is None or fig is None:
        return

    layout = active_layout(ctx)
    sync_heatmap_slider_to_focus(ctx)
    tp = _selected_tp(ctx)
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

    metric = _selected_metric(ctx)
    val_col = ctx.state.channel.active_val_col
    # Shapes keep every cell (no ON cutoff); smFISH counts honour the
    # channel's ThreshFracOn like intensities (count > cutoff is "on").
    if ctx.state.channel.is_shape_active():
        threshold = -1.0
    else:
        threshold = get_thresh_frac_on(ctx, ctx.state.channel.active_channel)
    fluor_gates = get_all_fluor_gates(ctx)
    ratios = ctx.state.channel.ratio_index
    cmap_name = _selected_cmap(ctx)

    arr = np.full((layout.rows, layout.cols), np.nan, dtype=float)
    cell_well_index: dict[tuple[int, int], list[str]] = {}

    for (r, c), wells in layout.cells.items():
        if not (0 <= r < layout.rows and 0 <= c < layout.cols):
            continue
        cell_well_index[(r, c)] = list(wells)
        try:
            arr[r, c] = _cell_value(
                ctx, wells, tp, metric, val_col,
                threshold, fluor_gates, ratios,
            )
        except Exception:
            arr[r, c] = float("nan")

    # Cache for click/hover handlers (stamped onto ``app._heatmap_*`` by the
    # runtime namespace so the app-native mouse handlers keep resolving).
    ctx.runtime.heatmap.cell_well_index = cell_well_index
    ctx.runtime.heatmap.array = arr
    ctx.runtime.heatmap.layout_active = layout

    # Pool min/max across every timepoint so the colormap stays consistent
    # while the user scrubs the timepoint slider. Cached so this only
    # recomputes when an input changes.
    g_vmin, g_vmax = _compute_global_range(
        ctx, layout, metric, val_col,
        threshold, fluor_gates, ratios,
    )
    ctx.runtime.heatmap.global_vmin = g_vmin
    ctx.runtime.heatmap.global_vmax = g_vmax

    # Render. Resolve the active plot-theme tokens up front so the empty-cell
    # fill, figure background, and axes background all track the theme — the
    # heatmap deliberately skips PlotCard.setPlotTheme on redraw, so without
    # this the figure rendered on matplotlib's default white inside the dark UI.
    from well_viewer.core.plot_style import tokens_for as _tokens_for
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for(ax)
    fig.patch.set_facecolor(_bg)

    vmin, vmax = _resolve_color_scale(arr, ctx)
    cmap = mpl_colormaps.get_cmap(cmap_name).copy()
    cmap.set_bad(color=_bg)  # empty cells blend into the themed background

    masked = np.ma.masked_invalid(arr)

    # Log-scale toggle: route values through a LogNorm so the colorbar
    # spans orders of magnitude. Values ≤ 0 become invalid (masked).
    norm = None
    log_scale = bool(ctx.state.heatmap.log_scale)
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
        else:
            # No usable positive range — a blank grid, one distinct value, or a
            # Fixed vmin at or below zero. Say so: rendering linear in silence
            # looks identical to the toggle having no effect at all.
            ctx.notify.set_status(
                "Heat Map: log scale needs two or more distinct positive values — "
                "showing the linear colour scale."
            )

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
    drag = ctx.runtime.heatmap.label_drag
    if drag is not None:
        _kind = drag["kind"]
        _src = drag["src"]
        _over = drag.get("over")
        _drag_labels = ax.get_xticklabels() if _kind == "col" else ax.get_yticklabels()
        for _i, _lbl in enumerate(_drag_labels):
            if _i == _src:
                _lbl.set_color(theme.Colors.accent_pressed)
                _lbl.set_fontweight("bold")
            elif _over is not None and _i == _over and _over != _src:
                _lbl.set_color(theme.Colors.danger)
                _lbl.set_fontweight("bold")

    # Cell text annotations. Resolve the user's number format once, not per cell.
    value_fmt = _cell_value_format(ctx, fig)
    for (r, c), wells in cell_well_index.items():
        v = arr[r, c]
        if not math.isfinite(v):
            continue
        rs_name = _repset_name_for_wells(ctx, wells)
        if rs_name:
            text = rs_name
        elif len(wells) == 1:
            text = wells[0]
        else:
            text = f"{wells[0]}+{len(wells) - 1}"
        ink = _contrast_ink(im.to_rgba(v))
        ax.text(
            c, r + 0.32, text,
            ha="center", va="center",
            fontsize=6, color=ink, alpha=0.8,
        )
        ax.text(
            c, r - 0.05, _format_cell_value(v, metric, value_fmt),
            ha="center", va="center",
            fontsize=8, color=ink,
        )

    # Selection rectangles.
    selected = set(ctx.state.selection.selected_wells or set())
    for (r, c), wells in cell_well_index.items():
        if any(w in selected for w in wells):
            rect = mpatches.Rectangle(
                (c - 0.5, r - 0.5), 1.0, 1.0,
                fill=False, edgecolor=_SELECTION_OUTLINE_COLOR, linewidth=2.0,
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
    cax = ctx.widgets.get_or_none("_heatmap_cax")
    if cax is not None:
        try:
            cax.cla()
            ctx.runtime.heatmap.colorbar = fig.colorbar(im, cax=cax)
            # Opt the bar out of the export-style pass's axis handling: its scale
            # and ticks come from ``im``'s norm, so under Log scale that pass
            # would otherwise reset the log ticks it just installed. ``cla()``
            # drops the attribute, so re-stamp it on every redraw.
            setattr(cax, "_aw_colorbar", True)
            ctx.runtime.heatmap.colorbar.set_label(_metric_axis_label(metric, ctx),
                                                   fontsize=8, color=_muted_fg)
            cax.tick_params(colors=_muted_fg, labelsize=7)
            for _spi in cax.spines.values():
                _spi.set_color(_spine)
        except Exception:
            ctx.runtime.heatmap.colorbar = None
    else:
        if ctx.runtime.heatmap.colorbar is not None:
            try:
                ctx.runtime.heatmap.colorbar.remove()
            except Exception:
                swallowed()  # was: pass (silent except)
        try:
            ctx.runtime.heatmap.colorbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.03)
            setattr(ctx.runtime.heatmap.colorbar.ax, "_aw_colorbar", True)
            ctx.runtime.heatmap.colorbar.set_label(_metric_axis_label(metric, ctx),
                                                   fontsize=8, color=_muted_fg)
            ctx.runtime.heatmap.colorbar.ax.tick_params(colors=_muted_fg, labelsize=7)
            for _spi in ctx.runtime.heatmap.colorbar.ax.spines.values():
                _spi.set_color(_spine)
        except Exception:
            ctx.runtime.heatmap.colorbar = None

    title = (
        f"{active_channel_label(ctx)}"
        f" — {metric} — t = {tp:g} h"
    )
    # ``pad`` lifts the title away from the heatmap rows so the descenders
    # never overlap the top row of cells; the figure's top=0.88 margin
    # reserves enough room for the lifted title.
    ax.set_title(title, fontsize=9, color=_title_fg, pad=8)

    # Apply the Export Style sidebar prefs (font sizes, grid, log scale, …)
    # so toggling those in the configurator survives a redraw.
    try:
        apply_export_style_to_current(ctx, fig, canvas)
    except Exception:
        swallowed()  # was: pass (silent except)

    canvas.draw_idle()


def _format_cell_value(v: float, metric: str, fmt: str = "") -> str:
    """The number drawn in (and reported under the cursor for) one grid cell.

    *fmt* is the user's printf-style format from the Properties panel — ``"%.2f"``,
    ``"%.1e"``, ``"%d"``. An empty *fmt* keeps the automatic choice, which reads the
    metric and the magnitude: counts are whole numbers, fractions get two places,
    and intensities scale their precision to their size. A format that can't render
    the value (a bad conversion, or ``%d`` against something non-finite) falls back
    to that automatic choice rather than dropping the number.
    """
    if not math.isfinite(v):
        return ""
    if fmt:
        try:
            return fmt % v
        except (TypeError, ValueError):
            pass  # unusable format → the automatic choice below
    if metric == METRIC_COUNT:
        return f"{int(round(v))}"
    if metric == METRIC_FRACTION:
        return f"{v:.2f}"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.3g}"


def _cell_value_format(ctx, fig) -> str:
    """The Properties panel's printf format for this heat map's cell numbers."""
    try:
        from well_viewer.core.plot_style import figure_prefs
        return str(figure_prefs(ctx, fig).get("heatmap_value_format", "") or "")
    except Exception:
        swallowed()  # a missing pref must never stop the grid drawing
        return ""


def _metric_axis_label(metric: str, ctx) -> str:
    label = active_channel_label(ctx)
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL as _MLB
    prop_label = _MLB.get(ctx.state.channel.active_metric, "Mean Intensity")
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

def _cell_at_event(ctx, event) -> tuple[int, int] | None:
    if event.inaxes is None or event.xdata is None or event.ydata is None:
        return None
    layout = ctx.runtime.heatmap.layout_active
    if layout is None:
        return None
    c = int(round(event.xdata))
    r = int(round(event.ydata))
    if not (0 <= r < layout.rows and 0 <= c < layout.cols):
        return None
    return r, c


def _event_modifiers(event) -> tuple[bool, bool]:
    """Return ``(shift, ctrl)`` for a matplotlib mouse *event*.

    Qt's ``QMouseEvent`` carries the authoritative modifier state, but PySide6
    exposes it as a ``KeyboardModifier`` flag object that ``int()`` refuses to
    convert — the value has to be read through ``.value``. Matplotlib's own
    ``event.key`` is the fallback for events with no GUI event attached (it only
    tracks modifiers while the canvas holds keyboard focus, so it is not enough
    on its own). ``cmd`` counts as ctrl because that is what macOS reports for
    the Command key; Qt already folds Command into ``ControlModifier`` there.
    """
    gui_event = getattr(event, "guiEvent", None)
    modifiers = getattr(gui_event, "modifiers", None)
    if callable(modifiers):
        try:
            flags = modifiers()
            value = int(getattr(flags, "value", flags))
            from PySide6.QtCore import Qt as _Qt
            shift_bit = int(getattr(_Qt.ShiftModifier, "value", _Qt.ShiftModifier))
            ctrl_bit = int(getattr(_Qt.ControlModifier, "value", _Qt.ControlModifier))
            return bool(value & shift_bit), bool(value & ctrl_bit)
        except Exception:
            swallowed()  # fall through to the matplotlib key state
    keys = set((getattr(event, "key", None) or "").lower().split("+"))
    return "shift" in keys, bool(keys & {"control", "ctrl", "cmd"})


def on_heatmap_click(ctx, event) -> None:
    rc = _cell_at_event(ctx, event)
    if rc is None:
        return
    cell_index = ctx.runtime.heatmap.cell_well_index or {}
    wells_here = list(cell_index.get(rc, []))
    if not wells_here:
        return

    selected = set(ctx.state.selection.selected_wells or set())
    shift, ctrl = _event_modifiers(event)

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

    # ``set_selected_wells`` is the single mutation point (prev-sel tracking +
    # redraw fan-out); it takes ctx, so thread the context.
    from well_viewer.core.selection_controller import set_selected_wells
    set_selected_wells(ctx, selected)


def on_heatmap_motion(ctx, event) -> None:
    rc = _cell_at_event(ctx, event)
    status_lbl = ctx.widgets.get_or_none("_heatmap_status_lbl")
    if rc is None:
        if status_lbl is not None:
            status_lbl.setText("")
        return
    cell_index = ctx.runtime.heatmap.cell_well_index or {}
    arr = ctx.runtime.heatmap.array
    wells = cell_index.get(rc, [])
    if status_lbl is not None:
        if not wells:
            status_lbl.setText(f"({rc[0]},{rc[1]}): empty")
        else:
            v = arr[rc[0], rc[1]] if arr is not None else float("nan")
            sample = ",".join(wells[:3]) + ("…" if len(wells) > 3 else "")
            metric = _selected_metric(ctx)
            # Same format as the drawn cell, so the readout matches what's under
            # the cursor rather than showing a differently-rounded number.
            fmt = _cell_value_format(ctx, ctx.widgets.get_or_none("_heatmap_fig"))
            status_lbl.setText(f"{sample}: {_format_cell_value(v, metric, fmt)}")


# ── Row/col label drag-to-reorder ────────────────────────────────────────────

def _label_at_event(ctx, event) -> tuple[str, int] | None:
    """Return ("row", i) or ("col", i) if *event* is near a tick label, else None.

    Used only for the initial press that starts a drag, so the threshold is
    generous (12 px) but still requires the cursor to be close to a label.
    """
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
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


def _nearest_label_in_drag(ctx, event, kind: str) -> int | None:
    """Flexibly find the target row/col index during a drag.

    Prefers the data coordinate (when the cursor is inside the axes) so the
    user can release anywhere along a column strip or row strip.  Falls back
    to nearest-label-by-pixel when the cursor is outside the axes (e.g. over
    the label gutter itself).
    """
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if ax is None or canvas is None or event.x is None or event.y is None:
        return None

    layout = ctx.runtime.heatmap.layout_active
    if layout is None:
        layout = active_layout(ctx)

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
    best: int | None = None
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


def _update_drag_label_colors(ctx) -> None:
    """Apply drag highlight colours to existing tick label artists and redraw."""
    ax = ctx.widgets.get_or_none("_heatmap_ax")
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if ax is None or canvas is None:
        return
    drag = ctx.runtime.heatmap.label_drag
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
                lbl.set_color(theme.Colors.accent_pressed)
                lbl.set_fontweight("bold")
            elif over is not None and i == over and over != src:
                lbl.set_color(theme.Colors.danger)
                lbl.set_fontweight("bold")
    canvas.draw_idle()


def _set_canvas_cursor(ctx, cursor_shape) -> None:
    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    if canvas is None:
        return
    try:
        if cursor_shape is None:
            canvas.unsetCursor()
        else:
            canvas.setCursor(cursor_shape)
    except Exception:
        swallowed()  # was: pass (silent except)


def on_heatmap_label_drag_press(ctx, event) -> bool:
    """Start a label drag on left-press over a tick label. Returns True if consumed."""
    if getattr(event, "button", None) != 1:
        return False
    if getattr(event, "dblclick", False):
        return False
    target = _label_at_event(ctx, event)
    if target is None:
        return False
    kind, idx = target
    ctx.runtime.heatmap.label_drag = {"kind": kind, "src": idx, "over": idx}
    _update_drag_label_colors(ctx)
    try:
        from PySide6.QtCore import Qt as _Qt
        _set_canvas_cursor(ctx, _Qt.ClosedHandCursor)
    except Exception:
        swallowed()  # was: pass (silent except)
    return True


def on_heatmap_label_drag_motion(ctx, event) -> None:
    """Update drag highlight while the mouse moves."""
    drag = ctx.runtime.heatmap.label_drag
    if drag is None:
        # Show open-hand cursor when hovering over any label.
        target = _label_at_event(ctx, event)
        try:
            from PySide6.QtCore import Qt as _Qt
            if target is not None:
                _set_canvas_cursor(ctx, _Qt.OpenHandCursor)
            else:
                _set_canvas_cursor(ctx, None)
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    # Use the flexible finder so the highlight tracks the cursor everywhere.
    drag["over"] = _nearest_label_in_drag(ctx, event, drag["kind"])
    _update_drag_label_colors(ctx)


def on_heatmap_label_drag_release(ctx, event) -> None:
    """Finish drag: apply reorder if source ≠ target, then clean up."""
    drag = ctx.runtime.heatmap.label_drag
    if drag is None:
        return
    src = drag["src"]
    kind = drag["kind"]
    # Recompute target at the release position (motion may not have fired last).
    over = _nearest_label_in_drag(ctx, event, kind)
    if over is None:
        over = drag.get("over")
    ctx.runtime.heatmap.label_drag = None
    _set_canvas_cursor(ctx, None)
    if over is not None and over != src:
        _apply_label_reorder(ctx, kind, src, over)
    else:
        _update_drag_label_colors(ctx)  # just clear highlights


def _apply_label_reorder(ctx, kind: str, src: int, dst: int) -> None:
    from well_viewer.features.heatmap.sidebar_view import (
        _ensure_sidebar_layout,
        _persist_and_redraw,
        refresh_heatmap_layout_sidebar,
    )
    layout = _ensure_sidebar_layout(ctx)
    if kind == "row":
        layout.reorder_rows(src, dst)
    else:
        layout.reorder_cols(src, dst)
    _persist_and_redraw(ctx)
    refresh_heatmap_layout_sidebar(ctx)
