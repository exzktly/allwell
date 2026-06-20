"""Bar/violin/beeswarm matplotlib drawing.

Extracted from ``WellViewerApp`` so the GUI class no longer owns plot
rendering. Each function takes ``app`` (WellViewerApp) for state access plus
the matplotlib axes to draw into.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

_logger = logging.getLogger(__name__)

from ui.theme import (
    BORDER,
    CLR_ERR_BAR,
    CLR_MUTED_DISABLED,
    CLR_PLACEHOLDER,
    WARN,
)

from well_viewer.features.barplot.controller import (
    apply_bar_ylims as _apply_bar_ylims,
    cluster_centers as _cluster_centers,
    cluster_x_for_group_keys,
    clustered_x_positions as _clustered_x_positions,
    render_bar_items as _render_bar_items,
)


def _annotate_cluster_footers(items, axes) -> None:
    """Per-cluster well/rset label centred under each axis's xtick row.

    Group-by-well multi-tp shows ``t=0`` / ``t=1`` / ... as the per-bar
    ticks (mirrored on every panel via ``set_xticklabels``), so the
    cluster's identity gets a footer on EACH axis below its tick row.
    Text uses the same muted_fg the ticks themselves are drawn with.
    """
    if not items or not axes:
        return
    from well_viewer.core.plot_style import tokens_for
    xs = _clustered_x_positions(items)
    for ax in axes:
        _muted_fg = tokens_for(ax)[2]
        for gk, cx in _cluster_centers(items, xs):
            ax.annotate(gk, xy=(cx, 0), xycoords=("data", "axes fraction"),
                        xytext=(0, -32), textcoords="offset points",
                        ha="center", va="top", fontsize=8, fontweight="bold",
                        color=_muted_fg, annotation_clip=False)
import numpy as np
import pandas as pd


def _set_frac_ylim(app, ax_frac) -> None:
    """Clamp the OnFraction panel to [0, 1] unless fold-change is on.

    Raw fractions are bounded so the clamp is a sensible default — without
    it matplotlib's autoscaler wastes the panel on a tiny range. Under
    fold-change the frac becomes a ratio (can exceed 1) or subtract result
    (can go negative); the clamp would silently clip the bars and hide the
    FC effect, so we let matplotlib auto-scale instead. User-entered y-lim
    overrides still apply in either case via ``apply_bar_ylims``.
    """
    from well_viewer.domain import fold_change as _fc
    fc_on, _, fc_t0 = _fc.fold_change_state(app)
    if fc_on or fc_t0:
        return
    ax_frac.set_ylim(-0.05, 1.05)

from well_viewer.data.data_loading import (
    _beeswarm_jitter,
    df_included_mask,
    parse_timepoint_hours,
    resolve_value_series,
)
from well_viewer.core.plot_style import (
    apply_ax_style,
    apply_export_style_to_current,
    tokens_for,
)
from well_viewer.domain.stats_compute import aggregate_well
from well_viewer.domain.metric_registry import metric_key_gating_exempt
from well_viewer.core.safe import swallowed


NO_SELECTION_MSG = (
    "No wells or well groups selected.\n"
    "Select wells on the left panel or define groups to plot."
)


def _pooled_rows(app, lbl: str, pool_groups: Optional[Dict[str, List[str]]]):
    """Return cells for ``lbl``. When ``pool_groups[lbl]`` exists, concat
    the per-well DataFrames of every well in the group; otherwise just
    return ``app._get_rows(lbl)`` unchanged."""
    if pool_groups is not None and lbl in pool_groups:
        frames = []
        for w in pool_groups[lbl]:
            sub = app._get_rows(w)
            if sub is not None and not sub.empty:
                frames.append(sub)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)
    return app._get_rows(lbl)


def _format_tp_title(target_ts: List[float], tp_str: str) -> str:
    """Build the axis-title timepoint suffix for one or many tps."""
    if not target_ts:
        return ""
    if len(target_ts) == 1:
        return f"t = {tp_str} h"
    return f"t = [{tp_str}] h"


def draw_violin(
    app,
    ax_mean,
    ax_frac,
    wells: List[str],
    colors: List[str],
    xlabels: List[str],
    target_t,
    tp_str: str,
    threshold: float,
    *,
    xs: Optional[List[float]] = None,
    group_keys: Optional[List[str]] = None,
    cell_scale: Optional[Dict[str, float]] = None,
    bw: Optional[float] = None,
    pool_groups: Optional[Dict[str, List[str]]] = None,
) -> None:
    """KDE-smoothed distribution per well/group.

    *cell_scale*, when supplied, maps each well to a denominator the
    pre-threshold cell values are divided by. This is how the per-cell
    view participates in fold-change normalization — each well's
    distribution shifts to live on the fold-change axis. NaN denominator
    means "this well had no resolvable baseline" and the well is left
    blank with no violin shape.

    *bw* — explicit KDE bandwidth (matches the on-tab slider value /
    100). When None, falls back to the on-tab ``_violin_slider`` widget;
    the batch export passes its own bw so the figure does not depend on
    the live tab's slider state.
    """
    try:
        from scipy.stats import gaussian_kde
    except ImportError:
        ax_mean.text(0.5, 0.5, "scipy required for violin plot",
                     transform=ax_mean.transAxes, ha="center", va="center",
                     color=tokens_for(ax_mean)[2], fontsize=9)
        return

    n = len(wells)
    if bw is None:
        slider = getattr(app, "_violin_slider", None)
        bw_raw = float(slider.value()) / 100.0 if slider is not None else 1.0
        bw = max(0.05, bw_raw)
    else:
        bw = max(0.05, float(bw))
    bar_w = min(0.4, 3.0 / max(n, 1))

    if xs is None:
        xs = list(range(n))
    if isinstance(target_t, (list, tuple)):
        target_ts_per_col = [float(t) for t in target_t]
    else:
        target_ts_per_col = [float(target_t)] * n
    frac_vals: List[float] = []
    ratios = app._app_state.channel.ratio_index

    for i, (lbl, color) in enumerate(zip(wells, colors)):
        col_tp = target_ts_per_col[i] if i < len(target_ts_per_col) else target_ts_per_col[0]
        df = _pooled_rows(app, lbl, pool_groups)
        vals = np.empty(0, dtype=float)
        n_total = n_above = 0
        if df is not None and not df.empty and "timepoint_hours" in df.columns:
            mask = df_included_mask(df).to_numpy(copy=True)
            tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
            with np.errstate(invalid="ignore"):
                mask &= np.isfinite(tp) & (np.abs(tp - col_tp) <= 1e-6)
            v = resolve_value_series(df, app._app_state.channel.active_val_col, ratios).to_numpy()
            mask &= np.isfinite(v)
            v_sel = v[mask]
            n_total = int(v_sel.size)
            vals = v_sel[v_sel > threshold]
            n_above = int(vals.size)

        frac_vals.append(n_above / n_total if n_total else float("nan"))

        # Apply fold-change scaling to the per-cell values (above-threshold
        # set only). The threshold filter ran on raw values above, so we
        # scale post-filter — that keeps the "above-threshold" semantics
        # consistent across the bar / line / violin renderers.
        if cell_scale is not None:
            factor = cell_scale.get(lbl, 1.0)
            if not (isinstance(factor, (int, float)) and math.isfinite(factor)
                    and factor != 0):
                # NaN factor means we lost the denominator for this well
                # — render a blank slot instead of misleading raw values.
                continue
            if factor != 1.0:
                vals = vals / factor

        xi = xs[i] if i < len(xs) else i
        if vals.size < 3:
            if vals.size:
                ax_mean.scatter([xi], [vals[0]], c=color, s=20, zorder=4)
            continue

        arr = vals
        kde = gaussian_kde(arr, bw_method=bw)
        y_min, y_max = float(arr.min()), float(arr.max())
        y_pad = (y_max - y_min) * 0.05
        ys_grid = np.linspace(y_min - y_pad, y_max + y_pad, 200)
        density = kde(ys_grid)
        max_d = density.max()
        if max_d > 0:
            density = density / max_d * bar_w

        ax_mean.fill_betweenx(ys_grid, xi - density, xi + density,
                              color=color, alpha=0.55, zorder=2)
        ax_mean.plot(xi - density, ys_grid, color=color, lw=0.6, alpha=0.7, zorder=3)
        ax_mean.plot(xi + density, ys_grid, color=color, lw=0.6, alpha=0.7, zorder=3)

        median = float(np.median(arr))
        ax_mean.hlines(median, xi - bar_w * 0.6, xi + bar_w * 0.6,
                       colors="white", lw=2.0, zorder=5)
        ax_mean.hlines(median, xi - bar_w * 0.6, xi + bar_w * 0.6,
                       colors=color, lw=1.2, zorder=6)

    for i, (fv, color) in enumerate(zip(frac_vals, colors)):
        xi = xs[i] if i < len(xs) else i
        if not math.isnan(fv):
            ax_frac.scatter([xi], [fv], c=color, s=30, zorder=3, linewidths=0)
        else:
            ax_frac.scatter([xi], [0], c=CLR_PLACEHOLDER, s=16,
                            marker="x", zorder=3, linewidths=1)

    ax_frac.axhline(0.5, color=BORDER, lw=0.8, ls="--", alpha=0.5, zorder=1)
    xlim_lo = (min(xs) - 0.6) if xs else -0.6
    xlim_hi = (max(xs) + 0.6) if xs else 0.4
    for ax in (ax_mean, ax_frac):
        ax.set_xticks(xs)
        ax.set_xticklabels(xlabels,
                           rotation=45 if n > 8 else 0,
                           ha="right" if n > 8 else "center",
                           fontsize=7)
        ax.set_xlim(xlim_lo, xlim_hi)
    if group_keys:
        # Headers above the mean panel showing the cluster identity.
        cluster_x: dict = {}
        cluster_order: List[str] = []
        for gk, x in zip(group_keys, xs):
            gk = gk or ""
            if not gk:
                continue
            if gk not in cluster_x:
                cluster_x[gk] = []
                cluster_order.append(gk)
            cluster_x[gk].append(x)
        for gk in cluster_order:
            positions = cluster_x[gk]
            if not positions:
                continue
            cx = sum(positions) / len(positions)
            ax_mean.text(cx, 1.02, gk, transform=ax_mean.get_xaxis_transform(),
                         ha="center", va="bottom", fontsize=8, fontweight="bold")
    _set_frac_ylim(app, ax_frac)
    ax_frac.set_ylabel("Fraction", fontsize=8, labelpad=5)
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL as _MLB, fraction_title
    from well_viewer.domain import fold_change as _fc
    _metric_label = _MLB.get(app._app_state.channel.active_metric, "Mean Intensity")
    _fc_v, _fc_lbl, _fc_t0 = _fc.fold_change_state(app)
    _fc_suffix = _fc.fold_change_suffix(_fc_v, _fc_t0, _fc_lbl) if cell_scale else ""
    _tp_suffix = _format_tp_title(target_ts_per_col, tp_str)
    ax_mean.set_title(
        f"{app._app_state.channel.active_channel.upper()} {_metric_label} distribution (violin, bw={bw:.2f}){_fc_suffix}  —  {_tp_suffix}",
        color=tokens_for(ax_mean)[1], fontsize=9, fontweight="bold", pad=6)
    ax_mean.set_ylabel(f"{app._app_state.channel.active_channel.upper()} {_metric_label}{_fc_suffix}", fontsize=8, labelpad=5)
    ax_frac.set_title(
        f"{fraction_title(app._app_state.channel.active_metric)}  —  {_tp_suffix}",
        color=tokens_for(ax_frac)[1], fontsize=9, fontweight="bold", pad=6)


def draw_beeswarm(
    app,
    ax_mean,
    ax_frac,
    wells: List[str],
    colors: List[str],
    xlabels: List[str],
    target_t,
    tp_str: str,
    threshold: float,
    *,
    xs: Optional[List[float]] = None,
    group_keys: Optional[List[str]] = None,
    cell_scale: Optional[Dict[str, float]] = None,
    pool_groups: Optional[Dict[str, List[str]]] = None,
) -> None:
    """One column per (well, timepoint), each cell a jittered point.

    *cell_scale* — see :func:`draw_violin`. When supplied, each well's
    above-threshold cells are divided by ``cell_scale[well]`` before
    plotting. NaN factor means the well's baseline couldn't be resolved
    and the column is left blank.

    *target_t* — single ``float`` or a list of per-column floats.
    """
    n = len(wells)
    if xs is None:
        xs = list(range(n))
    if isinstance(target_t, (list, tuple)):
        target_ts_per_col = [float(t) for t in target_t]
    else:
        target_ts_per_col = [float(target_t)] * n
    bar_w = min(0.35, 3.0 / max(n, 1))
    ratios = app._app_state.channel.ratio_index

    for i, (lbl, color) in enumerate(zip(wells, colors)):
        col_tp = target_ts_per_col[i] if i < len(target_ts_per_col) else target_ts_per_col[0]
        xi = xs[i] if i < len(xs) else i
        df = _pooled_rows(app, lbl, pool_groups)
        cell_vals = np.empty(0, dtype=float)
        frac_val: Optional[float] = None
        if df is not None and not df.empty:
            mask = df_included_mask(df).to_numpy(copy=True)
            if "timepoint_hours" in df.columns:
                tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
            else:
                tp = np.full(len(df), np.nan)
            need = ~np.isfinite(tp)
            if need.any() and "timepoint" in df.columns:
                fallback = (df.loc[need, "timepoint"].fillna("").astype(str)
                            .map(parse_timepoint_hours))
                tp[need] = fallback.fillna(np.nan).to_numpy(dtype=float)
            with np.errstate(invalid="ignore"):
                mask &= np.isfinite(tp) & (np.abs(tp - col_tp) <= 1e-6)
            val = resolve_value_series(df, app._app_state.channel.active_val_col, ratios).to_numpy()
            mask &= np.isfinite(val)
            v_sel = val[mask]
            n_total = int(v_sel.size)
            cell_vals = v_sel[v_sel > threshold]
            n_above = int(cell_vals.size)
            if n_total > 0:
                frac_val = n_above / n_total

        # Fold-change per-cell scaling — same contract as draw_violin.
        skipped_scale = False
        if cell_scale is not None:
            factor = cell_scale.get(lbl, 1.0)
            if not (isinstance(factor, (int, float)) and math.isfinite(factor)
                    and factor != 0):
                skipped_scale = True
            elif factor != 1.0 and cell_vals.size:
                cell_vals = cell_vals / factor

        if skipped_scale:
            ax_mean.scatter([xi], [0], c=CLR_PLACEHOLDER, s=16,
                            marker="x", zorder=3, linewidths=1)
        elif cell_vals.size:
            jx, jy = _beeswarm_jitter(cell_vals.tolist(), x_center=float(xi),
                                       max_spread=bar_w)
            sc = ax_mean.scatter(jx, jy, c=color, s=6, alpha=0.55,
                                 zorder=3, linewidths=0)
            # v5.2.31: tag so the export-style override picks up the
            # collection and applies marker-size / open-vs-filled /
            # edge-width prefs. Pre-v5.2.31 only ``ax.lines`` artists
            # were styled, so beeswarm dots silently ignored the
            # user's Marker pref changes.
            sc._aw_marker_collection = True
            m = float(cell_vals.mean())
            ax_mean.plot([xi - bar_w * 0.6, xi + bar_w * 0.6],
                         [m, m], color=color, lw=1.5, zorder=4)
        else:
            ax_mean.scatter([xi], [0], c=CLR_PLACEHOLDER, s=16,
                            marker="x", zorder=3, linewidths=1)

        if frac_val is not None:
            ax_frac.scatter([xi], [frac_val], c=color, s=30,
                            zorder=3, linewidths=0)
        else:
            ax_frac.scatter([xi], [0], c=CLR_PLACEHOLDER, s=16,
                            marker="x", zorder=3, linewidths=1)

    ax_frac.axhline(0.5, color=BORDER, lw=0.8, ls="--",
                    alpha=0.5, zorder=1)
    xlim_lo = (min(xs) - 0.6) if xs else -0.6
    xlim_hi = (max(xs) + 0.6) if xs else 0.4
    for ax in (ax_mean, ax_frac):
        ax.set_xticks(xs)
        ax.set_xticklabels(xlabels,
                           rotation=45 if n > 8 else 0,
                           ha="right" if n > 8 else "center",
                           fontsize=7)
        ax.set_xlim(xlim_lo, xlim_hi)
    if group_keys:
        cluster_x: dict = {}
        cluster_order: List[str] = []
        for gk, x in zip(group_keys, xs):
            gk = gk or ""
            if not gk:
                continue
            if gk not in cluster_x:
                cluster_x[gk] = []
                cluster_order.append(gk)
            cluster_x[gk].append(x)
        for gk in cluster_order:
            positions = cluster_x[gk]
            if not positions:
                continue
            cx = sum(positions) / len(positions)
            ax_mean.text(cx, 1.02, gk, transform=ax_mean.get_xaxis_transform(),
                         ha="center", va="bottom", fontsize=8, fontweight="bold")
    _set_frac_ylim(app, ax_frac)
    ax_frac.set_ylabel("Fraction", fontsize=8, labelpad=5)
    from well_viewer.data.metric_labels import (
        METRIC_KEY_TO_LABEL as _MLB,
        threshold_qualifier, fraction_title,
    )
    from well_viewer.domain import fold_change as _fc
    _metric_label = _MLB.get(app._app_state.channel.active_metric, "Mean Intensity")
    _thr_q = threshold_qualifier(app._app_state.channel.active_metric)
    _fc_v, _fc_lbl, _fc_t0 = _fc.fold_change_state(app)
    _fc_suffix = _fc.fold_change_suffix(_fc_v, _fc_t0, _fc_lbl) if cell_scale else ""
    _tp_suffix = _format_tp_title(target_ts_per_col, tp_str)
    ax_mean.set_title(
        f"{app._app_state.channel.active_channel.upper()} {_metric_label} per cell{_thr_q}{_fc_suffix}  —  {_tp_suffix}",
        color=tokens_for(ax_mean)[1], fontsize=9, fontweight="bold", pad=6)
    ax_mean.set_ylabel(f"{app._app_state.channel.active_channel.upper()} {_metric_label}{_fc_suffix}", fontsize=8, labelpad=5)
    ax_frac.set_title(
        f"{fraction_title(app._app_state.channel.active_metric)}  —  {_tp_suffix}",
        color=tokens_for(ax_frac)[1], fontsize=9, fontweight="bold", pad=6)


def draw_grouped_bar_mode(
    app,
    *,
    ax_mean,
    ax_frac,
    ax_n,
    active_rsets,
    target_ts: List[float],
    tp_str: str,
    threshold: float,
    band_lbl: str,
    use_sem: bool,
) -> None:
    """Render the canonical grouped-bar (or per-well) view.

    The items returned by :func:`collect_bar_items` are already in
    drag-order, already fold-change-normalized, and already carry their
    display labels and colours — this function is just the renderer
    wrapper that paints axes-decorations around the shared ``BarItem``
    draw loop. A miss sink tracks any timepoint where the vs-control
    denominator couldn't be resolved so the user gets one status
    warning instead of silently empty bars.
    """
    fc_misses: set = set()
    use_groups, items, _ = app._collect_bar_items(target_ts, miss_sink=fc_misses)
    _render_bar_items(
        ax_mean=ax_mean,
        ax_frac=ax_frac,
        ax_n=ax_n,
        use_groups=use_groups,
        items=items,
        threshold=threshold,
        warn_color=WARN,
        border_color=BORDER,
        placeholder_color=CLR_PLACEHOLDER,
        disabled_well_color=CLR_MUTED_DISABLED,
        err_bar_color=CLR_ERR_BAR,
        fc_active=bool(app._app_state.plot.fold_change_active),
    )
    if str(getattr(app._app_state.plot, "bar_group_mode", "well") or "well") == "well":
        _xtick_axes = [ax_mean, ax_frac] + ([ax_n] if ax_n is not None else [])
        _annotate_cluster_footers(items, _xtick_axes)
    ax_frac.set_ylabel("Fraction", fontsize=8, labelpad=5)
    _ch = app._app_state.channel.active_channel.upper()
    from well_viewer.data.metric_labels import (
        METRIC_KEY_TO_LABEL as _MLB,
        threshold_qualifier, fraction_title,
        events_per_fov_title, events_total_title,
    )
    from well_viewer.domain import fold_change as _fc
    _metric_label = _MLB.get(app._app_state.channel.active_metric, "Mean Intensity")
    _metric_key = app._app_state.channel.active_metric
    _thr_q = threshold_qualifier(_metric_key)
    _fc_v, _fc_lbl, _fc_t0 = _fc.fold_change_state(app)
    _fc_suffix = _fc.fold_change_suffix(_fc_v, _fc_t0, _fc_lbl)
    _tp_suffix = _format_tp_title(target_ts, tp_str)
    ax_mean.set_title(
        f"{_ch} {_metric_label}{_thr_q} ± {band_lbl}{_fc_suffix}  —  {_tp_suffix}",
        color=tokens_for(ax_mean)[1], fontsize=9, fontweight="bold", pad=6,
    )
    ax_mean.set_ylabel(f"{_ch} {_metric_label}{_fc_suffix}", fontsize=8, labelpad=5)
    ax_frac.set_title(
        f"{fraction_title(_metric_key)}  —  {_tp_suffix}",
        color=tokens_for(ax_frac)[1], fontsize=9, fontweight="bold", pad=6,
    )
    if ax_n is not None:
        fov_active = app._use_fov_spread_active()
        if fov_active:
            n_title = f"{events_per_fov_title(_metric_key, band_lbl)}  —  {_tp_suffix}"
            n_ylabel = "N(spots)/FOV" if _metric_key == "smfish_count" else "N(above)/FOV"
        else:
            n_title = f"{events_total_title(_metric_key)}  —  {_tp_suffix}"
            n_ylabel = "N(spots)" if _metric_key == "smfish_count" else "N(above)"
        ax_n.set_title(
            n_title, color=tokens_for(ax_n)[1], fontsize=9, fontweight="bold", pad=6,
        )
        ax_n.set_ylabel(n_ylabel, fontsize=8, labelpad=5)
    _apply_bar_ylims(app, ax_mean, ax_frac, ax_n=ax_n)
    if fc_misses:
        _tps = ", ".join(f"{t:g}" for t in sorted(fc_misses))
        app._set_status(
            f"Fold change: control missing at t={_tps} — bars dropped."
        )
    app._bar_canvas.draw_idle()


def redraw_bars(app) -> None:
    """Top-level bar plot redraw (drives the Bar Plots tab)."""
    if not hasattr(app, "_ax_bar_mean"):
        return
    ax_mean = app._ax_bar_mean
    ax_frac = app._ax_bar_frac
    ax_n = getattr(app, "_ax_bar_n", None)
    ax_mean.cla()
    ax_frac.cla()
    if ax_n is not None:
        ax_n.cla()

    use_sem = app._app_state.plot.use_sem
    band_lbl = "SEM" if use_sem else "SD"
    # smfish_count: include zero-spot cells in the mean / beeswarm / violin
    # population (see barplot/controller.py for the full rationale).
    if metric_key_gating_exempt(app._app_state.channel.active_metric) or app._app_state.channel.is_shape_active():
        threshold = -1.0
    else:
        threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)

    active_rsets = app._rep_sets_active()
    bar_selected = selected_bar_wells(app, active_rsets)

    _ch = app._app_state.channel.active_channel.upper()
    from well_viewer.data.metric_labels import (
        METRIC_KEY_TO_LABEL as _MLB,
        threshold_qualifier, fraction_title,
        events_per_fov_title, events_total_title,
    )
    from well_viewer.domain import fold_change as _fc
    _metric_label = _MLB.get(app._app_state.channel.active_metric, "Mean Intensity")
    _metric_key = app._app_state.channel.active_metric
    _thr_q = threshold_qualifier(_metric_key)
    _fc_v, _fc_lbl, _fc_t0 = _fc.fold_change_state(app)
    _fc_suffix = _fc.fold_change_suffix(_fc_v, _fc_t0, _fc_lbl)
    apply_ax_style(ax_mean,
                   f"{_ch} {_metric_label}{_thr_q} ± {band_lbl}{_fc_suffix}",
                   f"{_ch} {_metric_label}{_fc_suffix}")
    apply_ax_style(ax_frac, fraction_title(_metric_key), "Fraction")
    if ax_n is not None:
        fov_active = app._use_fov_spread_active()
        if fov_active:
            apply_ax_style(
                ax_n,
                events_per_fov_title(_metric_key, band_lbl),
                "N(spots)/FOV" if _metric_key == "smfish_count" else "N(above)/FOV",
            )
        else:
            apply_ax_style(
                ax_n, events_total_title(_metric_key),
                "N(spots)" if _metric_key == "smfish_count" else "N(above)",
            )
    _set_frac_ylim(app, ax_frac)

    if not app._app_state.dataset.well_paths:
        draw_bar_empty_state(
            app, ax_mean, ax_frac,
            "Load a directory.\n"
            "Use the Open button at the top-right (⌘O) to pick a folder.",
            ax_n=ax_n,
        )
        return
    if not bar_selected and not active_rsets:
        draw_bar_empty_state(app, ax_mean, ax_frac, NO_SELECTION_MSG, ax_n=ax_n)
        return

    tp_list = resolve_bar_timepoints(app)
    if not tp_list:
        draw_bar_empty_state(app, ax_mean, ax_frac, "Select a timepoint above", ax_n=ax_n)
        return
    target_ts = [t for t, _ in tp_list]
    tp_strs = [s for _, s in tp_list]
    tp_str_combined = ", ".join(tp_strs)

    if draw_per_cell_bar_mode(
        app,
        ax_mean=ax_mean,
        ax_frac=ax_frac,
        ax_n=ax_n,
        active_rsets=active_rsets,
        target_ts=target_ts,
        tp_str=tp_str_combined,
        threshold=threshold,
    ):
        apply_export_style_to_current(app, app._bar_fig, getattr(app, "_bar_canvas", None))
        return
    draw_grouped_bar_mode(
        app,
        ax_mean=ax_mean,
        ax_frac=ax_frac,
        ax_n=ax_n,
        active_rsets=active_rsets,
        target_ts=target_ts,
        tp_str=tp_str_combined,
        threshold=threshold,
        band_lbl=band_lbl,
        use_sem=use_sem,
    )
    apply_export_style_to_current(app, app._bar_fig, getattr(app, "_bar_canvas", None))


def draw_bar_empty_state(app, ax_mean, ax_frac, message: str, *, ax_n=None) -> None:
    # Honour Screen vs Publication mode so the placeholder doesn't render
    # as a glaring white block when the rest of the app is dark.
    _bg, _title_fg, _muted_fg, _grid, _spine = tokens_for(ax_mean)
    try:
        ax_mean.figure.set_facecolor(_bg)
    except Exception:
        swallowed()  # was: pass (silent except)
    axes = [ax_mean, ax_frac]
    if ax_n is not None:
        axes.append(ax_n)
    for ax in axes:
        ax.set_facecolor(_bg)
        ax.text(
            0.5, 0.5, message,
            transform=ax.transAxes,
            ha="center", va="center",
            color=_muted_fg, fontsize=10,
        )
        ax.set_axis_off()
    app._bar_canvas.draw_idle()


def resolve_bar_timepoints(app) -> List[tuple]:
    """Return the list of ``(float, str)`` timepoints currently selected.

    ``BarPlotState.tp_selections`` is authoritative once populated: an
    empty selection there means "show nothing", regardless of what the
    hidden ``_bar_tp_cb`` combo still carries. We only fall back to the
    combo before the popup has been populated at all (e.g. very early
    after construction, before ``update_bar_tp_menu`` ran), so the
    legacy single-tp shape still works on a fresh app.
    """
    sels = app._app_state.bar.tp_selections if hasattr(app, "_app_state") else None
    if isinstance(sels, dict) and sels:
        out: List[tuple] = []
        for tp_str, on in sels.items():
            if not on:
                continue
            try:
                out.append((float(tp_str), tp_str))
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda p: p[0])
        return out
    # Fallback: derive from the hidden combo (popup not yet populated).
    tp_str = app._bar_tp_var.currentText() if hasattr(app, "_bar_tp_var") else ""
    if tp_str in ("—", ""):
        return []
    try:
        return [(float(tp_str), tp_str)]
    except ValueError:
        return []


def selected_bar_wells(app, active_rsets) -> List[str]:
    if active_rsets:
        return []
    return sorted(
        (lbl for lbl in app._app_state.selection.selected_wells
         if lbl in app._app_state.dataset.well_paths),
        key=lambda lbl: app._parse_rc(lbl),
    )


def draw_per_cell_bar_mode(
    app,
    *,
    ax_mean,
    ax_frac,
    ax_n=None,
    active_rsets,
    target_ts: List[float],
    tp_str: str,
    threshold: float,
) -> bool:
    from well_viewer.core import debug_flags as _debug_flags
    from well_viewer.domain import fold_change as _fc

    use_per_cell = app._bar_violin or app._bar_swarm
    if not use_per_cell:
        return False
    ordered_keys = app._bar_current_keys()
    plot = app._app_state.plot
    group_mode = getattr(plot, "bar_group_mode", "well")
    pool_reps = bool(getattr(plot, "bar_pool_replicates", True))
    pool_rset_cells = bool(getattr(plot, "bar_pool_rset_cells", False)) and bool(active_rsets)

    # Build a base (well, color, well_label, cluster_label) sequence for
    # one timepoint. When rsets are active and pool_replicates is False,
    # expand wells inside each rset (keeping the rset rank colour). The
    # cluster_label is used as the cluster header (well-grouped layout)
    # so it carries the full disambiguating identity (e.g. "A01 [CTRL]"),
    # while the per-bar tick label stays short.
    base_wells: List[str] = []
    base_colors: List[str] = []
    base_short: List[str] = []      # short well/rset label used per-bar
    base_full: List[str] = []       # disambiguating label used in cluster headers
    pool_groups: Optional[Dict[str, List[str]]] = None
    if pool_rset_cells:
        # One column per rset; cells pooled across the rset's wells. The
        # rset *name* becomes the "well" key passed downstream — draw_violin /
        # draw_beeswarm route the row-fetch through ``pool_groups``, and
        # ``build_cell_scaling`` accepts rset names because ``member_*``
        # resolvers branch on ``_label_to_replicate_set``.
        rset_by_name = {r.name: r for r in active_rsets}
        pool_groups = {}
        for key in ordered_keys:
            rset = rset_by_name.get(key)
            if rset is None:
                continue
            valid = [w for w in rset.wells if w in app._app_state.dataset.well_paths]
            if not valid:
                continue
            base_wells.append(rset.name)
            base_colors.append(app._rank_color_rset(rset))
            base_short.append(rset.name)
            base_full.append(rset.name)
            pool_groups[rset.name] = valid
    elif active_rsets and pool_reps:
        rset_by_name = {r.name: r for r in active_rsets}
        for key in ordered_keys:
            rset = rset_by_name.get(key)
            if rset is None:
                continue
            color = app._rank_color_rset(rset)
            valid = [w for w in rset.wells if w in app._app_state.dataset.well_paths]
            for w in valid:
                base_wells.append(w)
                base_colors.append(color)
                short = app._well_display_label(w)
                base_short.append(short)
                base_full.append(f"{short} [{rset.name}]")
    elif active_rsets:
        seen: set = set()
        for rset in active_rsets:
            color = app._rank_color_rset(rset)
            for w in rset.wells:
                if w in seen or w not in app._app_state.dataset.well_paths:
                    continue
                seen.add(w)
                base_wells.append(w)
                base_colors.append(color)
                short = app._well_display_label(w)
                base_short.append(short)
                base_full.append(f"{short} [{rset.name}]")
    else:
        base_wells = [k for k in ordered_keys if k in app._app_state.dataset.well_paths]
        base_colors = [app._rank_color_well(w) for w in base_wells]
        base_short = [app._well_display_label(w) for w in base_wells]
        base_full = list(base_short)

    if not base_wells:
        app._bar_canvas.draw_idle()
        return True

    # Materialise (well × tp) columns in the order the active layout asks
    # for. Labels stay single-line: cluster headers carry the primary
    # identity so per-bar labels only need to carry the secondary axis
    # (the variable that changes inside the cluster).
    plot_wells: List[str] = []
    plot_colors: List[str] = []
    plot_labels: List[str] = []
    plot_tps: List[float] = []
    group_keys: List[str] = []
    multi_tp = len(target_ts) > 1
    if group_mode == "timepoint":
        for t in target_ts:
            tp_lbl = f"{t:.4g}"
            for w, c, short, full in zip(base_wells, base_colors, base_short, base_full):
                plot_wells.append(w)
                plot_colors.append(c)
                # cluster header = tp; per-bar tick = well identity
                plot_labels.append(full if multi_tp else full)
                plot_tps.append(float(t))
                group_keys.append(tp_lbl if multi_tp else "")
    else:
        for w, c, short, full in zip(base_wells, base_colors, base_short, base_full):
            for t in target_ts:
                tp_lbl = f"{t:.4g}"
                plot_wells.append(w)
                plot_colors.append(c)
                # cluster header = well/rset identity; per-bar tick = tp
                plot_labels.append(f"t={tp_lbl}" if multi_tp else full)
                plot_tps.append(float(t))
                group_keys.append(full if multi_tp else "")

    if _debug_flags.review_bar_debug_enabled():
        mode = "violin" if app._bar_violin else "beeswarm"
        _logger.debug(
            "per-cell mode=%s wells=%r labels=%r tps=%r",
            mode, plot_wells, plot_labels, plot_tps,
        )
    # Build the per-well scale dict ONCE — same denominator each well uses for
    # its cell values. Pass through to violin / beeswarm so fold-change
    # participation no longer ends at the bar renderer. Fold-change resolves
    # against the FIRST selected tp when multiple are chosen (legacy
    # single-tp behaviour preserved for the common case).
    _fc_state = _fc.fold_change_state(app)
    if _fc_state[0] or _fc_state[2]:
        cell_scale = _fc.build_cell_scaling(
            app, base_wells, fc_state=_fc_state, target_t=target_ts[0],
            threshold=threshold, val_col=app._app_state.channel.active_val_col,
            use_sem=app._app_state.plot.use_sem,
            per_fov_spread=app._use_fov_spread_active(),
            fluor_gates=app._get_all_fluor_gates(),
        )
    else:
        cell_scale = None

    xs = cluster_x_for_group_keys(group_keys)
    if app._bar_violin:
        draw_violin(
            app, ax_mean, ax_frac, plot_wells, plot_colors, plot_labels,
            plot_tps, tp_str, threshold,
            xs=xs, group_keys=group_keys,
            cell_scale=cell_scale,
            pool_groups=pool_groups,
        )
    else:
        draw_beeswarm(
            app, ax_mean, ax_frac,
            plot_wells, plot_colors, plot_labels,
            plot_tps, tp_str, threshold,
            xs=xs, group_keys=group_keys,
            cell_scale=cell_scale,
            pool_groups=pool_groups,
        )
    _apply_bar_ylims(app, ax_mean, ax_frac, ax_n=ax_n)
    # Even in violin/beeswarm mode the user wants to see the events
    # above threshold count alongside the distribution.
    if ax_n is not None:
        draw_per_well_n_bars(
            app, ax_n, plot_wells, plot_colors, plot_labels,
            plot_tps, threshold, xs=xs, group_keys=group_keys,
            pool_groups=pool_groups,
        )
    app._bar_canvas.draw_idle()
    return True


def draw_per_well_n_bars(
    app,
    ax_n,
    wells: List[str],
    colors: List[str],
    xlabels: List[str],
    target_t,
    threshold: float,
    *,
    xs: Optional[List[float]] = None,
    group_keys: Optional[List[str]] = None,
    pool_groups: Optional[Dict[str, List[str]]] = None,
) -> None:
    """Render the events-above-threshold panel for per-cell view modes.

    Honours the Aggregate-FOVs toggle: when active, each well's bar is the
    mean of its per-FOV above-threshold counts and an error bar spans ±
    the per-FOV SD/SEM. When the toggle is off, the bar is the well's
    total above-threshold count with no error bar.
    """
    from matplotlib.ticker import MaxNLocator
    from well_viewer.domain.stats_compute import aggregate_group
    fluor_gates = app._get_all_fluor_gates()
    per_fov_spread = app._use_fov_spread_active()
    use_sem = bool(app._app_state.plot.use_sem)
    n = len(wells)
    if xs is None:
        xs = list(range(n))
    if isinstance(target_t, (list, tuple)):
        target_ts_per_col = [float(t) for t in target_t]
    else:
        target_ts_per_col = [float(target_t)] * n
    bar_w = min(0.6, 5.0 / max(n, 1))
    for i, lbl in enumerate(wells):
        col_tp = target_ts_per_col[i] if i < len(target_ts_per_col) else target_ts_per_col[0]
        xi = xs[i] if i < len(xs) else i
        if pool_groups is not None and lbl in pool_groups:
            pts = aggregate_group(app,
                pool_groups[lbl], threshold=threshold, use_sem=use_sem,
                val_col=app._app_state.channel.active_val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
        else:
            pts = aggregate_well(app,
                lbl, threshold=threshold, use_sem=use_sem,
                val_col=app._app_state.channel.active_val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
        matched = [pt for pt in pts if abs(pt[0] - col_tp) < 1e-6]
        color = colors[i % len(colors)] if colors else app._rank_color_well(lbl)
        if matched:
            pt = matched[0]
            if per_fov_spread:
                # AggPoint index 7 = mean per-FOV n_above; index 8 = SD/SEM.
                bar_val = float(pt[7]) if len(pt) >= 8 else 0.0
                err_val = float(pt[8]) if len(pt) >= 9 else 0.0
            else:
                # AggPoint index 4 = total n_above for the well.
                bar_val = float(pt[4])
                err_val = 0.0
        else:
            bar_val = 0.0
            err_val = 0.0
        if bar_val > 0:
            ax_n.bar(xi, bar_val, width=bar_w, color=color, alpha=0.85, zorder=3, linewidth=0)
            if err_val > 0:
                ax_n.errorbar(xi, bar_val, yerr=err_val, fmt="none", ecolor=CLR_ERR_BAR, elinewidth=1.4, capsize=4, zorder=4)
        else:
            ax_n.bar(xi, 0, width=bar_w, color=CLR_PLACEHOLDER, linewidth=1, edgecolor=CLR_MUTED_DISABLED, linestyle="--", zorder=3)
    ax_n.set_xticks(xs)
    ax_n.set_xticklabels(xlabels, rotation=45 if n > 8 else 0, ha="right" if n > 8 else "center", fontsize=7)
    xlim_lo = (min(xs) - 0.6) if xs else -0.6
    xlim_hi = (max(xs) + 0.6) if xs else 0.4
    ax_n.set_xlim(xlim_lo, xlim_hi)
    # Use float ticks when the bar shows a per-FOV mean (Aggregate FOVs
    # toggle on); otherwise the events-above value is an integer count.
    cur_lo, cur_hi = ax_n.get_ylim()
    ax_n.set_ylim(0, max(cur_hi, 1))
    ax_n.yaxis.set_major_locator(MaxNLocator(integer=not per_fov_spread))
    setattr(ax_n, "_categorical_xaxis", True)
