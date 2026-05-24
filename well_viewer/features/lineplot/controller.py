"""Line-graph rendering helpers extracted from well_viewer3."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from well_viewer.domain import fold_change as _fc
from well_viewer.features.barplot.controller import FC_STATE_OFF
from well_viewer.domain.stats_compute import compute_rep_stats
from well_viewer.domain.stats_compute import compute_rep_per_fov_stats
from well_viewer.domain.stats_compute import aggregate_well


NO_SELECTION_MSG = "No wells or well groups selected.\nSelect wells on the left panel or define groups to plot."
NO_DATA_MSG = "Load a directory.\nUse the Open button at the top-right (⌘O) to pick a folder."


# ── LineSeries data model ───────────────────────────────────────────────────
#
# Single source of truth for the line-plot pipeline — mirrors the role
# ``BarItem`` plays for the bar plot. ``LinePoint`` carries the full
# AggPoint shape so the renderer (uses t/mean/spread/frac) and the CSV
# writers (also need n_above / n_total / frac_spread) consume the same
# value without converting back and forth.


@dataclass
class LinePoint:
    """One timepoint of a :class:`LineSeries`, post-normalization.

    Holds the full AggPoint shape returned by ``_aggregate_well`` /
    ``_aggregate_group``: the renderer reads (t, mean, spread, frac),
    the CSV writers read those plus n_above / n_total / frac_spread.
    ``has_mean`` / ``has_frac`` mirror the bar plot's NaN-vs-real
    distinction so renderers don't second-guess what to draw.
    """
    t: float
    mean: float
    spread: float
    frac: float
    n_above: int = 0
    n_total: int = 0
    frac_spread: float = 0.0
    n_above_pf_mean: float = 0.0
    n_above_pf_spread: float = 0.0
    has_mean: bool = True
    has_frac: bool = True

    def as_aggpoint(self) -> tuple:
        """Convert back to the AggPoint tuple shape ``line_metric_row`` expects."""
        return (
            self.t, self.mean, self.spread, self.frac,
            int(self.n_above), int(self.n_total), self.frac_spread,
            self.n_above_pf_mean, self.n_above_pf_spread,
        )


@dataclass
class LineSeries:
    """One member's curve on the line plot (rep-set or well).

    Fields:
      * ``key`` — drag-order identity. Rep-set name for grouped mode,
        well token ("A01") for per-well mode.
      * ``display`` — legend label.
      * ``color`` — trace colour, already resolved.
      * ``kind`` — ``"repset"`` or ``"well"``. CSV writers branch on
        this for the wells / well_names / n_wells columns.
      * ``wells`` — contributing well tokens (1 for ``kind="well"``).
      * ``points`` — post-normalization :class:`LinePoint`\ s in
        chronological order. Empty when no usable data.
      * ``cdf_vals`` — raw per-cell values for the CDF panel (the
        distribution panel doesn't fold-change-normalize naturally;
        always raw).
    """
    key: str
    display: str
    color: str
    kind: str
    wells: List[str]
    points: List[LinePoint] = field(default_factory=list)
    cdf_vals: List[float] = field(default_factory=list)


def _apply_order(items, saved_order, key):
    """Reorder ``items`` so entries matching ``saved_order`` come first.

    Items whose ``key(item)`` appears in ``saved_order`` are emitted in the
    saved order; the remainder follow in their natural (input) order. An empty
    or missing saved_order is a no-op (returns items unchanged).
    """
    if not saved_order:
        return list(items)
    items = list(items)
    by_key: dict[str, list[int]] = {}  # str-key -> indices into items
    for idx, it in enumerate(items):
        try:
            k = str(key(it))
        except Exception:
            k = ""
        by_key.setdefault(k, []).append(idx)
    # Track which item *indices* have already been emitted. id()-based
    # dedup was unsafe for small interned strings or any items the
    # iterator might compare equal — distinct list positions could
    # share an id, producing spurious dedup.
    seen_indices: set[int] = set()
    out: list = []
    for k in saved_order:
        bucket = by_key.get(str(k))
        if not bucket:
            continue
        for idx in bucket:
            out.append(items[idx])
            seen_indices.add(idx)
    for idx, it in enumerate(items):
        if idx not in seen_indices:
            out.append(it)
    return out


def _aggpoint_to_linepoint(pt) -> LinePoint:
    """Lift a raw AggPoint tuple into a :class:`LinePoint`."""
    t = float(pt[0])
    mean = float(pt[1]) if isinstance(pt[1], (int, float)) else float("nan")
    spread = float(pt[2]) if isinstance(pt[2], (int, float)) else 0.0
    frac = float(pt[3]) if (len(pt) > 3 and isinstance(pt[3], (int, float))) else float("nan")
    n_above = int(pt[4]) if len(pt) > 4 else 0
    n_total = int(pt[5]) if len(pt) > 5 else 0
    frac_spread = float(pt[6]) if len(pt) > 6 else 0.0
    n_above_pf_mean = float(pt[7]) if len(pt) > 7 else 0.0
    n_above_pf_spread = float(pt[8]) if len(pt) > 8 else 0.0
    return LinePoint(
        t=t, mean=mean, spread=spread, frac=frac,
        n_above=n_above, n_total=n_total, frac_spread=frac_spread,
        n_above_pf_mean=n_above_pf_mean, n_above_pf_spread=n_above_pf_spread,
        has_mean=not (isinstance(mean, float) and math.isnan(mean)),
        has_frac=not (isinstance(frac, float) and math.isnan(frac)),
    )


def collect_line_series(
    app,
    *,
    threshold: float,
    use_sem: bool,
    val_col: Optional[str] = None,
    fc_state: Optional[Tuple[bool, str, bool]] = None,
    miss_sink: Optional[Set[float]] = None,
    include_cdf: bool = True,
    all_fluor_values_filtered=None,
) -> Tuple[List[LineSeries], str]:
    """Build :class:`LineSeries` for the currently-active line-plot mode.

    Single source of truth for the line-plot pipeline (mirrors
    :func:`barplot_controller.collect_bar_items` for the bar tab). The
    on-screen renderer, the on-tab CSV exporter, and the batch CSV /
    figure all consume the same shape — fold-change normalization
    applied exactly once, here.

    *fc_state* — pass :data:`barplot_controller.FC_STATE_OFF` to skip
    normalization (used by the additive CSV path to emit raw rows
    alongside fold-change rows). ``None`` defaults to the app's
    current state.

    *include_cdf* — set False to skip per-cell CDF aggregation when
    the caller doesn't need it (CSV writers, off-screen exports).

    *all_fluor_values_filtered* — callable used by the renderer to
    pre-compute the CDF panel's per-cell value list; optional for
    callers that only want the time-series.

    Returns ``(series, band_lbl)``. ``band_lbl`` is "SEM" or "SD"
    matching ``use_sem``.
    """
    band_lbl = "SEM" if use_sem else "SD"

    if fc_state is None:
        fc_state = _fc.fold_change_state(app)
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
    if val_col is None:
        val_col = app._app_state.channel.active_val_col

    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    active_rsets = app._rep_sets_active()
    rep_per_fov = app._use_fov_spread_active() if active_rsets else False
    per_fov_spread = app._use_fov_spread_active() if not active_rsets else False

    # Control series — mean-of-per-well-means / per-FOV / per-well agg
    # matching each member's own stat (post-PR-3). Pass {t: (mean, spread)}
    # so error propagation through normalize_pts has the right
    # denominator uncertainty.
    fc_control_stats: dict = {}
    if fc_vs_ctrl and fc_ctrl_lbl:
        fc_control_stats = _fc.member_stats_series(
            app, fc_ctrl_lbl,
            threshold=threshold, val_col=val_col,
            use_sem=use_sem, per_fov_spread=rep_per_fov or per_fov_spread,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
        )

    series_out: List[LineSeries] = []

    if active_rsets:
        ordered_rsets = _apply_order(
            active_rsets,
            list(app._app_state.plot.line_order_rsets or []),
            key=lambda r: getattr(r, "name", ""),
        )
        for rset in ordered_rsets:
            color = app._rank_color_rset(rset)
            valid_wells = [w for w in rset.wells if w in app._app_state.dataset.well_paths]
            all_tps: set = set()
            cdf_vals: list = []
            for lbl in valid_wells:
                for t, *_ in aggregate_well(app, 
                    lbl, threshold=threshold, use_sem=False,
                    val_col=val_col,
                    cell_area_threshold=cell_area_threshold,
                    fluor_gates=fluor_gates,
                ):
                    all_tps.add(t)
                if include_cdf and all_fluor_values_filtered is not None:
                    rows = app._get_rows(lbl)
                    cdf_vals.extend(all_fluor_values_filtered(
                        rows, val_col=val_col,
                        cell_area_threshold=cell_area_threshold,
                        fluor_gates=fluor_gates,
                        ratios=app._app_state.channel.ratio_index,
                    ))
            _raw_pts: list = []
            for t in sorted(all_tps):
                if rep_per_fov:
                    gm, gerr, gf, _, _, _ = compute_rep_per_fov_stats(app, 
                        rset, t, threshold, use_sem,
                    )
                else:
                    gm, gerr, gf, _ = compute_rep_stats(app, 
                        rset, t, threshold, use_sem,
                    )
                if not (isinstance(gm, float) and math.isnan(gm)):
                    _raw_pts.append((t, gm, gerr, gf, 0, 0, 0.0, 0.0, 0.0))
            if _raw_pts and (fc_control_stats or fc_vs_t0):
                _raw_pts = _fc.normalize_pts(
                    _raw_pts,
                    control_stats=fc_control_stats or None,
                    use_t0=fc_vs_t0,
                    miss_sink=miss_sink,
                )
            points = [_aggpoint_to_linepoint(pt) for pt in _raw_pts]
            n_wells = len(valid_wells)
            display = (
                f"{rset.name} (n={n_wells})" if n_wells != 1 else rset.name
            )
            series_out.append(LineSeries(
                key=rset.name, display=display, color=color, kind="repset",
                wells=valid_wells, points=points, cdf_vals=cdf_vals,
            ))
        return series_out, band_lbl

    # Per-well branch.
    selected = sorted(
        (lbl for lbl in app._app_state.selection.selected_wells if lbl in app._app_state.dataset.well_paths),
        key=lambda lbl: app._parse_rc(lbl),
    )
    ordered_selected = _apply_order(
        selected,
        list(app._app_state.plot.line_order_wells or []),
        key=lambda x: x,
    )
    for label in ordered_selected:
        color = app._rank_color_well(label)
        pts = aggregate_well(app, 
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        if pts and (fc_control_stats or fc_vs_t0):
            pts = _fc.normalize_pts(
                pts,
                control_stats=fc_control_stats or None,
                use_t0=fc_vs_t0,
                miss_sink=miss_sink,
            )
        points = [_aggpoint_to_linepoint(pt) for pt in (pts or [])]
        cdf_vals: list = []
        if include_cdf and all_fluor_values_filtered is not None:
            rows = app._get_rows(label)
            cdf_vals = list(all_fluor_values_filtered(
                rows, val_col=val_col,
                cell_area_threshold=cell_area_threshold,
                fluor_gates=fluor_gates,
                ratios=app._app_state.channel.ratio_index,
            ))
        display = app._well_display_label(label)
        series_out.append(LineSeries(
            key=label, display=display, color=color, kind="well",
            wells=[label], points=points, cdf_vals=cdf_vals,
        ))
    return series_out, band_lbl


def redraw_line_plots(
    app,
    *,
    apply_ax_style,
    all_fluor_values,
    all_fluor_values_filtered,
    warn: str,
    metric_label: str = "Mean Intensity",
) -> None:
    """Redraw the line/fraction/CDF panel set for the active app state."""
    # Lazy-build guard: this controller is reachable from the redraw fan-out
    # whether or not the Line Graphs tab has been built. Today it's
    # eager-built; this guard is the safe shape for when it isn't.
    if not all(
        hasattr(app, attr)
        for attr in ("_line_ax_mean", "_line_ax_frac", "_line_ax_cdf",
                     "_line_fig", "_line_canvas")
    ):
        return
    for ax in (app._line_ax_mean, app._line_ax_frac, app._line_ax_cdf):
        ax.cla()

    use_sem = app._app_state.plot.use_sem
    band_lbl = "SEM" if use_sem else "SD"
    # smFISH counts are integer spot counts; the channel's mean-intensity
    # threshold gates them all out. Treat 'above threshold' as 'has at
    # least one spot' for smfish_count (matches scatter's convention).
    if app._app_state.channel.active_metric == "smfish_count":
        threshold = 0.0
    else:
        threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
    selected = app._selected_labels()

    # Fold-change state read once for the axis title suffix. The actual
    # control-series resolution + per-trace normalization happens inside
    # ``collect_line_series`` (single source of truth, mirrors the
    # bar plot's ``collect_bar_items``).
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = _fc.fold_change_state(app)
    fc_misses: set = set()
    # Theme-aware chrome colors (track the active PlotCard's Publication/Screen
    # state) — the renderer's *trace* colours stay rank-based.
    from well_viewer.core.plot_style import tokens_for as _tokens_for_ax
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for_ax(app._line_ax_mean)
    # framealpha=0 keeps the legend frame transparent so whatever sits behind
    # the box (gridlines, curves) shows through correctly — fixes the wrong
    # legend background in non-default themes.
    legend_kw = dict(fontsize=7, framealpha=0.0, facecolor="none", edgecolor=_spine, labelcolor=_title_fg)

    _ch = app._app_state.channel.active_channel.upper()
    _fc_suffix = _fc.fold_change_suffix(fc_vs_ctrl, fc_vs_t0, fc_ctrl_lbl)
    from well_viewer.data.metric_labels import threshold_qualifier, fraction_title
    _thr_q = threshold_qualifier(app._app_state.channel.active_metric)
    apply_ax_style(app._line_ax_mean, f"{_ch} {metric_label}{_thr_q} ± {band_lbl}{_fc_suffix}", metric_label + _fc_suffix)
    apply_ax_style(app._line_ax_frac, fraction_title(app._app_state.channel.active_metric), "Fraction")
    cdf_lbl = (f"{_ch} {metric_label} CDF (all wells per replicate set)" if app._rep_sets_active() else f"{_ch} {metric_label} CDF (all selected wells)")
    apply_ax_style(app._line_ax_cdf, cdf_lbl, "Cumulative fraction")
    app._line_ax_frac.set_xlabel("Time (hours)", fontsize=8, labelpad=5)
    app._line_ax_frac.set_ylim(-0.05, 1.05)
    app._line_ax_cdf.set_xlabel(f"{_ch} {metric_label}", fontsize=8, labelpad=5)
    app._line_ax_cdf.set_ylim(-0.02, 1.05)

    active_rsets = app._rep_sets_active()
    has_data = bool(app._app_state.dataset.well_paths)
    # Empty-state warning needs to honour Screen mode so the text isn't
    # painted against a default white matplotlib figure when the rest of
    # the app is dark. Force the figure + axes facecolor to the theme bg
    # before drawing the message.
    if not has_data:
        try:
            app._line_fig.set_facecolor(_bg)
        except Exception:
            pass
        for ax in (app._line_ax_mean, app._line_ax_frac, app._line_ax_cdf):
            ax.set_title("")
            ax.cla()
            ax.set_facecolor(_bg)
            ax.text(0.5, 0.5, NO_DATA_MSG, transform=ax.transAxes,
                    ha="center", va="center", color=_muted_fg, fontsize=10)
            ax.set_axis_off()
        app._line_canvas.draw_idle()
        app._set_status("No data loaded.")
        return
    if not selected and not active_rsets:
        try:
            app._line_fig.set_facecolor(_bg)
        except Exception:
            pass
        for ax in (app._line_ax_mean, app._line_ax_frac, app._line_ax_cdf):
            ax.set_title("")
            ax.set_facecolor(_bg)
            ax.text(0.5, 0.5, NO_SELECTION_MSG, transform=ax.transAxes, ha="center", va="center", color=_muted_fg, fontsize=10)
            ax.set_axis_off()
        app._line_canvas.draw_idle()
        app._set_status("No wells selected.")
        return

    any_ts = any_cdf = False
    # Single source of truth: ask ``collect_line_series`` for the
    # fully-normalized series in legend order, then iterate to paint.
    series_list, _ = collect_line_series(
        app,
        threshold=threshold, use_sem=use_sem,
        miss_sink=fc_misses, include_cdf=True,
        all_fluor_values_filtered=all_fluor_values_filtered,
    )
    for series in series_list:
        color = series.color
        agg_times = [p.t for p in series.points if p.has_mean]
        agg_means = [p.mean for p in series.points if p.has_mean]
        agg_errs = [p.spread for p in series.points if p.has_mean]
        if agg_times:
            app._line_ax_mean.plot(
                agg_times, agg_means, color=color, lw=2,
                marker="o", markersize=4, label=series.display, zorder=3,
            )
            app._line_ax_mean.fill_between(
                agg_times,
                [m - e for m, e in zip(agg_means, agg_errs)],
                [m + e for m, e in zip(agg_means, agg_errs)],
                color=color, alpha=0.15, zorder=2,
            )
            vf = [(p.t, p.frac) for p in series.points if p.has_frac]
            if vf:
                vt2, vf2 = zip(*vf)
                app._line_ax_frac.plot(
                    vt2, vf2, color=color, lw=2,
                    marker="s", markersize=3, label=series.display, zorder=3,
                )
                app._line_ax_frac.fill_between(
                    vt2, 0, vf2, color=color, alpha=0.10, zorder=2,
                )
            any_ts = True
        if series.cdf_vals:
            fluor_s = sorted(series.cdf_vals)
            n = len(fluor_s)
            cdf_label = (
                f"{series.display} (n={n:,})"
                if series.kind == "repset"
                else f"{series.display} (n={n:,})"
            )
            app._line_ax_cdf.plot(
                fluor_s, [(k + 1) / n for k in range(n)],
                color=color, lw=1.8, label=cdf_label, zorder=3,
            )
            any_cdf = True

    def _has_labeled(ax) -> bool:
        handles, labels = ax.get_legend_handles_labels()
        return any(lbl and not str(lbl).startswith("_") for lbl in labels)

    if any_ts:
        if _has_labeled(app._line_ax_mean):
            leg_mean = app._line_ax_mean.legend(**legend_kw)
            leg_mean.set_draggable(True)
            leg_mean.set_visible(app._app_state.plot.legend_visible["mean"])
        if _has_labeled(app._line_ax_frac):
            leg_frac = app._line_ax_frac.legend(**legend_kw)
            leg_frac.set_draggable(True)
            leg_frac.set_visible(app._app_state.plot.legend_visible["frac"])
    if any_cdf:
        app._line_ax_cdf.axvline(threshold, color=warn, lw=1.2, ls="--", label=f"threshold={threshold:.2f}", zorder=4, picker=8)
        try:
            cdf_lo = float(app._cdf_xmin_edit.text())
        except (ValueError, AttributeError):
            cdf_lo = 0.0
        try:
            cdf_hi = float(app._cdf_xmax_edit.text())
        except (ValueError, AttributeError):
            cdf_hi = 300.0
        if cdf_hi <= cdf_lo:
            cdf_hi = cdf_lo + 1.0
        app._line_ax_cdf.axvspan(threshold, cdf_hi, alpha=0.05, color=warn, zorder=1)
        if _has_labeled(app._line_ax_cdf):
            leg_cdf = app._line_ax_cdf.legend(**legend_kw)
            leg_cdf.set_draggable(True)
            leg_cdf.set_visible(app._app_state.plot.legend_visible["cdf"])
        app._line_ax_cdf.set_xlim(cdf_lo, cdf_hi)
    else:
        app._line_ax_cdf.text(0.5, 0.5, f"No {app._app_state.channel.active_channel.upper()} data found.", transform=app._line_ax_cdf.transAxes, ha="center", va="center", color=_muted_fg, fontsize=10)

    if fc_misses:
        # Tell the user which timepoints lost their bars/points to a
        # missing control sample, so an unexpectedly empty plot isn't a
        # silent surprise.
        _tps = ", ".join(f"{t:g}" for t in sorted(fc_misses))
        app._set_status(
            f"Fold change: control missing at t={_tps} — those points dropped."
        )
    elif active_rsets:
        n_wells = sum(sum(1 for w in r.wells if w in app._app_state.dataset.well_paths) for r in active_rsets)
        app._set_status(f"{len(active_rsets)} replicate set(s)  ·  {n_wells} well(s)  |  threshold={threshold:.2f}  |  band={band_lbl}")
    else:
        n_total = sum(len(app._get_rows(l)) for l in selected)
        app._set_status(f"{len(selected)} well(s)  |  {n_total:,} nuclei  |  threshold={threshold:.2f}  |  band={band_lbl}")

    # Re-apply the Export Style sidebar's prefs so font sizes, axis
    # limits, log scale, etc. survive a redraw (matches what the bar,
    # heatmap, and distribution renderers do).
    try:
        from well_viewer.features.export.style import apply_export_style_to_current
        apply_export_style_to_current(app, app._line_fig,
                                      getattr(app, "_line_canvas", None))
    except Exception:  # pragma: no cover - never let style restore break a draw
        pass
    app._line_canvas.draw_idle()


def on_fig_click(app, event) -> None:
    """Right-click on a Line-Graphs axes -> toggle that axes' legend."""
    if event.inaxes is None or event.button != 3:
        return
    ax_map = {
        id(app._line_ax_mean): "mean",
        id(app._line_ax_frac): "frac",
        id(app._line_ax_cdf):  "cdf",
    }
    clicked_ax = event.inaxes
    key = ax_map.get(id(clicked_ax))
    if key is None:
        return
    app._app_state.plot.legend_visible[key] = not app._app_state.plot.legend_visible[key]
    leg = clicked_ax.get_legend()
    if leg is not None:
        leg.set_visible(app._app_state.plot.legend_visible[key])
        app._line_canvas.draw_idle()
    state = "shown" if app._app_state.plot.legend_visible[key] else "hidden"
    app._set_status(f"Legend for '{key}' plot {state}  (right-click to toggle)")


def toggle_sem(app) -> None:
    app._invalidate_stats_cache()
    is_sem = app._app_state.plot.toggle_sem()
    text = "SEM" if is_sem else "SD"
    variant = "sem" if is_sem else "sem_warn"
    for btn in list(getattr(app, "_sem_btns", []) or []):
        btn.setText(text)
        btn.setProperty("variant", variant)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
    for obs in list(getattr(app, "_sem_observers", []) or []):
        try:
            obs(is_sem)
        except Exception:
            pass
    app._redraw()
    if app._current_centre_tab() == "Bar Plots":
        from well_viewer.features.barplot.renderer import redraw_bars
        redraw_bars(app)

