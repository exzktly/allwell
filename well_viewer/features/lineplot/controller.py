"""Line-graph rendering helpers extracted from well_viewer3."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from well_viewer.domain import fold_change as _fc
from well_viewer.domain.stats_compute import compute_rep_stats
from well_viewer.domain.stats_compute import compute_rep_per_fov_stats
from well_viewer.domain.stats_compute import aggregate_well
from well_viewer.domain.stats_compute import compute_rep_n_above
from well_viewer.domain.stats_compute import replicate_unit
from well_viewer.domain.selections_model import (
    active_replicate_sets, color_for_replicate_set, color_for_well,
    well_display_label,
)
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates, get_thresh_frac_on,
)
from well_viewer.data.data_loading import get_rows
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import apply_export_style_to_current


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
    n_above: float = 0.0
    n_total: int = 0
    frac_spread: float = 0.0
    n_above_pf_mean: float = 0.0
    n_above_pf_spread: float = 0.0
    has_mean: bool = True
    has_frac: bool = True

    def as_aggpoint(self) -> tuple:
        """Convert back to the AggPoint tuple shape ``line_metric_row`` expects."""
        n_above = (int(self.n_above)
                   if isinstance(self.n_above, (int, float)) and math.isfinite(self.n_above)
                   else float("nan"))
        return (
            self.t, self.mean, self.spread, self.frac,
            n_above, int(self.n_total), self.frac_spread,
            self.n_above_pf_mean, self.n_above_pf_spread,
        )


@dataclass
class LineSeries:
    r"""One member's curve on the line plot (rep-set or well).

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
    # n_above may be a fold-change ratio (float, or NaN at a missing-control
    # timepoint) once FC normalizes the N panel, so keep it float / NaN-safe
    # rather than int(): the renderer reads ``float(p.n_above)`` and skips
    # points without a mean anyway.
    _n_raw = pt[4] if len(pt) > 4 else 0
    n_above = (float(_n_raw)
               if isinstance(_n_raw, (int, float)) and math.isfinite(_n_raw)
               else float("nan"))
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
    ctx,
    *,
    threshold: float,
    use_sem: bool,
    val_col: Optional[str] = None,
    fc_state: Optional[Tuple[bool, str, bool]] = None,
    fc_baseline_tp: Optional[float] = None,
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
        fc_state = _fc.fold_change_state(ctx)
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
    if fc_baseline_tp is None:
        fc_baseline_tp = _fc.parse_baseline_tp(ctx)
    fc_op_ctrl = _fc.parse_fc_op_ctrl(ctx)
    fc_op_t0 = _fc.parse_fc_op_t0(ctx)
    if val_col is None:
        val_col = ctx.state.channel.active_val_col

    fluor_gates = get_all_fluor_gates(ctx)
    active_rsets = active_replicate_sets(ctx.state.selection.selections, ctx.state.dataset.well_paths)
    rep_per_fov = ctx.state.plot.use_fov_replicates if active_rsets else False
    per_fov_spread = ctx.state.plot.use_fov_replicates if not active_rsets else False

    # Control series — mean-of-per-well-means / per-FOV / per-well agg
    # matching each member's own stat (post-PR-3). Pass {t: (mean, spread)}
    # so error propagation through normalize_pts has the right
    # denominator uncertainty.
    fc_control_stats: dict = {}
    fc_control_frac_stats: dict = {}
    fc_control_n_stats: dict = {}
    # The N panel plots the per-FOV-mean count when FOV-spread is on, else the
    # total count — the control denominator and the scaled field must match.
    fc_n_per_fov = bool(rep_per_fov or per_fov_spread)
    if fc_vs_ctrl and fc_ctrl_lbl:
        fc_control_stats = _fc.member_stats_series(
            ctx, fc_ctrl_lbl,
            threshold=threshold, val_col=val_col,
            use_sem=use_sem, per_fov_spread=fc_n_per_fov,
            fluor_gates=fluor_gates,
        )
        fc_control_frac_stats = _fc.member_frac_stats_series(
            ctx, fc_ctrl_lbl,
            threshold=threshold, val_col=val_col,
            use_sem=use_sem, per_fov_spread=fc_n_per_fov,
            fluor_gates=fluor_gates,
        )
        fc_control_n_stats = _fc.member_n_stats_series(
            ctx, fc_ctrl_lbl,
            threshold=threshold, val_col=val_col,
            per_fov_spread=fc_n_per_fov,
            fluor_gates=fluor_gates,
        )

    series_out: List[LineSeries] = []

    if active_rsets:
        ordered_rsets = _apply_order(
            active_rsets,
            list(ctx.state.plot.line_order_rsets or []),
            key=lambda r: getattr(r, "name", ""),
        )
        for rset in ordered_rsets:
            color = color_for_replicate_set(rset, ctx.state.selection.selections)
            valid_wells = [w for w in rset.wells if w in ctx.state.dataset.well_paths]
            # Unified replicate rule: per-FOV only for a single-well set with FOV
            # mode on; a replicate set (>=2 wells) is per-well, matching the stats n.
            rset_per_fov = replicate_unit(len(valid_wells), rep_per_fov) == "fov"
            all_tps: set = set()
            cdf_vals: list = []
            for lbl in valid_wells:
                for t, *_ in aggregate_well(ctx,
                    lbl, threshold=threshold, use_sem=False,
                    val_col=val_col,
                    fluor_gates=fluor_gates,
                ):
                    all_tps.add(t)
                if include_cdf and all_fluor_values_filtered is not None:
                    rows = get_rows(ctx, lbl)
                    cdf_vals.extend(all_fluor_values_filtered(
                        rows, val_col=val_col,
                        fluor_gates=fluor_gates,
                        ratios=ctx.state.channel.ratio_index,
                    ))
            _raw_pts: list = []
            for t in sorted(all_tps):
                if rset_per_fov:
                    gm, gerr, gf, gfe, gnpf_m, gnpf_s = compute_rep_per_fov_stats(ctx,
                        rset, t, threshold, use_sem,
                    )
                else:
                    gm, gerr, gf, _ = compute_rep_stats(ctx,
                        rset, t, threshold, use_sem,
                    )
                    # compute_rep_n_above reads threshold from app state internally
                    _n_above_val = compute_rep_n_above(ctx, rset, t)
                    gfe, gnpf_m, gnpf_s = 0.0, 0.0, 0.0
                if not (isinstance(gm, float) and math.isnan(gm)):
                    # AggPoint idx 6 = frac_spread (per-FOV fraction error), 7/8 =
                    # per-FOV n-above mean/spread. These were previously dropped,
                    # so the "fraction above threshold" subplot had no FOV error
                    # to draw and fell back to shading the area under the line.
                    _raw_pts.append((t, gm, gerr, gf, _n_above_val if not rset_per_fov else 0, 0, gfe, gnpf_m, gnpf_s))
            if _raw_pts and (fc_control_stats or fc_vs_t0):
                _raw_pts = _fc.normalize_pts(
                    _raw_pts,
                    control_stats=fc_control_stats or None,
                    control_frac_stats=fc_control_frac_stats or None,
                    control_n_stats=fc_control_n_stats or None,
                    normalize_frac_n=True,
                    n_per_fov=fc_n_per_fov,
                    use_t0=fc_vs_t0,
                    baseline_tp=fc_baseline_tp,
                    op_ctrl=fc_op_ctrl,
                    op_t0=fc_op_t0,
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
        (lbl for lbl in ctx.state.selection.selected_wells if lbl in ctx.state.dataset.well_paths),
        key=lambda lbl: parse_rc(lbl),
    )
    ordered_selected = _apply_order(
        selected,
        list(ctx.state.plot.line_order_wells or []),
        key=lambda x: x,
    )
    for label in ordered_selected:
        color = color_for_well(label)
        pts = aggregate_well(ctx,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        if pts and (fc_control_stats or fc_vs_t0):
            pts = _fc.normalize_pts(
                pts,
                control_stats=fc_control_stats or None,
                control_frac_stats=fc_control_frac_stats or None,
                control_n_stats=fc_control_n_stats or None,
                normalize_frac_n=True,
                n_per_fov=fc_n_per_fov,
                use_t0=fc_vs_t0,
                baseline_tp=fc_baseline_tp,
                op_ctrl=fc_op_ctrl,
                op_t0=fc_op_t0,
                miss_sink=miss_sink,
            )
        points = [_aggpoint_to_linepoint(pt) for pt in (pts or [])]
        cdf_vals: list = []
        if include_cdf and all_fluor_values_filtered is not None:
            rows = get_rows(ctx, label)
            cdf_vals = list(all_fluor_values_filtered(
                rows, val_col=val_col,
                fluor_gates=fluor_gates,
                ratios=ctx.state.channel.ratio_index,
            ))
        display = well_display_label(ctx, label)
        series_out.append(LineSeries(
            key=label, display=display, color=color, kind="well",
            wells=[label], points=points, cdf_vals=cdf_vals,
        ))
    return series_out, band_lbl


def _paint_series(ctx, series, *, fov_active: bool) -> "tuple[bool, bool, bool]":
    """Draw one collected line series onto the mean / fraction / N / CDF axes
    (audit A5). Returns ``(painted_ts, painted_cdf, painted_n)`` — the caller
    ORs these across series to decide legend/empty handling. Pure side effect on
    the axes; the drawing sequence mirrors the original inline loop exactly."""
    ax_mean = ctx.widgets.get_or_none("_line_ax_mean")
    ax_frac = ctx.widgets.get_or_none("_line_ax_frac")
    ax_n = ctx.widgets.get_or_none("_line_ax_n")
    ax_cdf = ctx.widgets.get_or_none("_line_ax_cdf")
    any_ts = any_cdf = any_n = False
    color = series.color
    agg_times = [p.t for p in series.points if p.has_mean]
    agg_means = [p.mean for p in series.points if p.has_mean]
    agg_errs = [p.spread for p in series.points if p.has_mean]
    if agg_times:
        ax_mean.plot(
            agg_times, agg_means, color=color, lw=2,
            marker="o", markersize=4, label=series.display, zorder=3,
        )
        ax_mean.fill_between(
            agg_times,
            [m - e for m, e in zip(agg_means, agg_errs)],
            [m + e for m, e in zip(agg_means, agg_errs)],
            color=color, alpha=0.15, zorder=2,
        )
        vf = [(p.t, p.frac, p.frac_spread) for p in series.points if p.has_frac]
        if vf:
            vt2, vf2, vfe = zip(*vf)
            ax_frac.plot(
                vt2, vf2, color=color, lw=2,
                marker="s", markersize=3, label=series.display, zorder=3,
            )
            if any(e > 0 for e in vfe):
                # FOV / replicate spread present (e.g. "FOV errors" mode):
                # draw a ±error band around the fraction line, matching the
                # mean subplot — NOT a fill down to the x-axis (which read as
                # "colouring the area underneath" and carried no error info).
                lo = [max(0.0, f - e) for f, e in zip(vf2, vfe)]
                hi = [f + e for f, e in zip(vf2, vfe)]
                ax_frac.fill_between(
                    vt2, lo, hi, color=color, alpha=0.15, zorder=2,
                )
            else:
                # No spread (single FOV / no error mode): keep the light
                # area shading under the curve as before.
                ax_frac.fill_between(
                    vt2, 0, vf2, color=color, alpha=0.10, zorder=2,
                )
        any_ts = True
        # N events panel
        if fov_active:
            vn = [(p.t, p.n_above_pf_mean, p.n_above_pf_spread)
                  for p in series.points if p.has_mean]
        else:
            vn = [(p.t, float(p.n_above), 0.0)
                  for p in series.points if p.has_mean]
        if vn:
            vnt, vnv, vne = zip(*vn)
            ax_n.plot(
                vnt, vnv, color=color, lw=2,
                marker="^", markersize=3, label=series.display, zorder=3,
            )
            if any(e > 0 for e in vne):
                lo = [max(0.0, v - e) for v, e in zip(vnv, vne)]
                hi = [v + e for v, e in zip(vnv, vne)]
                ax_n.fill_between(vnt, lo, hi, color=color, alpha=0.15, zorder=2)
            any_n = True
    if series.cdf_vals:
        fluor_s = sorted(series.cdf_vals)
        n = len(fluor_s)
        cdf_label = (
            f"{series.display} (n={n:,})"
            if series.kind == "repset"
            else f"{series.display} (n={n:,})"
        )
        ax_cdf.plot(
            fluor_s, [(k + 1) / n for k in range(n)],
            color=color, lw=1.8, label=cdf_label, zorder=3,
        )
        any_cdf = True
    return any_ts, any_cdf, any_n


def _has_labeled(ax) -> bool:
    """True if *ax* has at least one real (non-underscore) legend label."""
    handles, labels = ax.get_legend_handles_labels()
    return any(lbl and not str(lbl).startswith("_") for lbl in labels)


def _finalize_line_axes(
    ctx, *, any_ts: bool, any_cdf: bool, any_n: bool,
    fov_active: bool, fc_active: bool, legend_kw: dict,
    threshold: float, warn: str, muted_fg,
) -> None:
    """Post-paint axis finishing (audit A5): N-panel integer ticks, per-panel
    legends (honouring saved visibility), and the CDF threshold line / x-range /
    empty message. Extracted verbatim from ``redraw_line_plots``."""
    ax_mean = ctx.widgets.get_or_none("_line_ax_mean")
    ax_frac = ctx.widgets.get_or_none("_line_ax_frac")
    ax_n = ctx.widgets.get_or_none("_line_ax_n")
    ax_cdf = ctx.widgets.get_or_none("_line_ax_cdf")
    legend_visible = ctx.state.plot.legend_visible
    if any_n:
        cur_lo, cur_hi = ax_n.get_ylim()
        ax_n.set_ylim(0, max(cur_hi, 1))
        if not fov_active and not fc_active:
            # Raw counts → integer ticks. Under fold change the N panel shows a
            # ratio, so let matplotlib pick fractional ticks.
            from matplotlib.ticker import MaxNLocator
            ax_n.yaxis.set_major_locator(MaxNLocator(integer=True))
    if any_ts:
        if _has_labeled(ax_mean):
            leg_mean = ax_mean.legend(**legend_kw)
            leg_mean.set_draggable(True)
            leg_mean.set_visible(legend_visible["mean"])
        if _has_labeled(ax_frac):
            leg_frac = ax_frac.legend(**legend_kw)
            leg_frac.set_draggable(True)
            leg_frac.set_visible(legend_visible["frac"])
        if any_n and _has_labeled(ax_n):
            leg_n = ax_n.legend(**legend_kw)
            leg_n.set_draggable(True)
            leg_n.set_visible(legend_visible.get("n", True))
    if any_cdf:
        _show_thr = getattr(ctx.state.plot, "show_threshold_line", False)
        if _show_thr:
            ax_cdf.axvline(threshold, color=warn, lw=1.2, ls="--", label=f"threshold={threshold:.2f}", zorder=4, picker=8)
        _xmin_edit = ctx.widgets.get_or_none("_cdf_xmin_edit")
        try:
            cdf_lo = float(_xmin_edit.text())
        except (ValueError, AttributeError):
            cdf_lo = 0.0
        _xmax_edit = ctx.widgets.get_or_none("_cdf_xmax_edit")
        try:
            cdf_hi = float(_xmax_edit.text())
        except (ValueError, AttributeError):
            cdf_hi = 300.0
        if cdf_hi <= cdf_lo:
            cdf_hi = cdf_lo + 1.0
        if _show_thr:
            ax_cdf.axvspan(threshold, cdf_hi, alpha=0.05, color=warn, zorder=1)
        if _has_labeled(ax_cdf):
            leg_cdf = ax_cdf.legend(**legend_kw)
            leg_cdf.set_draggable(True)
            leg_cdf.set_visible(legend_visible["cdf"])
        ax_cdf.set_xlim(cdf_lo, cdf_hi)
    else:
        ax_cdf.text(0.5, 0.5, f"No {ctx.state.channel.active_channel.upper()} data found.", transform=ax_cdf.transAxes, ha="center", va="center", color=muted_fg, fontsize=10)


def redraw_line_plots(
    ctx,
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
        ctx.widgets.get_or_none(attr) is not None
        for attr in ("_line_ax_mean", "_line_ax_frac", "_line_ax_cdf",
                     "_line_ax_n", "_line_fig", "_line_canvas")
    ):
        return
    ax_mean = ctx.widgets.get_or_none("_line_ax_mean")
    ax_frac = ctx.widgets.get_or_none("_line_ax_frac")
    ax_n = ctx.widgets.get_or_none("_line_ax_n")
    ax_cdf = ctx.widgets.get_or_none("_line_ax_cdf")
    fig = ctx.widgets.get_or_none("_line_fig")
    canvas = ctx.widgets.get_or_none("_line_canvas")
    for ax in (ax_mean, ax_frac, ax_n, ax_cdf):
        ax.cla()
    # Re-suppress shared-x tick labels reset by cla()
    ax_mean.tick_params(labelbottom=False)
    ax_frac.tick_params(labelbottom=False)

    use_sem = ctx.state.plot.use_sem
    band_lbl = "SEM" if use_sem else "SD"
    # Shapes keep every cell (no ON cutoff); smFISH counts honour the
    # channel's ThreshFracOn like intensities (count > cutoff is "on").
    if ctx.state.channel.is_shape_active():
        threshold = -1.0
    else:
        threshold = get_thresh_frac_on(ctx, ctx.state.channel.active_channel)
    selected = sorted(ctx.state.selection.selected_wells, key=parse_rc)

    # Fold-change state read once for the axis title suffix. The actual
    # control-series resolution + per-trace normalization happens inside
    # ``collect_line_series`` (single source of truth, mirrors the
    # bar plot's ``collect_bar_items``).
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = _fc.fold_change_state(ctx)
    fc_misses: set = set()
    # Theme-aware chrome colors (track the active PlotCard's Publication/Screen
    # state) — the renderer's *trace* colours stay rank-based.
    from well_viewer.core.plot_style import tokens_for as _tokens_for_ax
    _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for_ax(ax_mean)
    # framealpha=0 keeps the legend frame transparent so whatever sits behind
    # the box (gridlines, curves) shows through correctly — fixes the wrong
    # legend background in non-default themes.
    legend_kw = dict(fontsize=7, framealpha=0.0, facecolor="none", edgecolor=_spine, labelcolor=_title_fg)

    _ch = ctx.state.channel.active_channel.upper()
    _fc_suffix = _fc.fold_change_suffix_for(ctx)
    from well_viewer.data.metric_labels import threshold_qualifier, fraction_title, events_per_fov_title, events_total_title
    _thr_q = threshold_qualifier(ctx.state.channel.active_metric)
    apply_ax_style(ax_mean, f"{_ch} {metric_label}{_thr_q} ± {band_lbl}{_fc_suffix}", metric_label + _fc_suffix)
    apply_ax_style(ax_frac, f"{fraction_title(ctx.state.channel.active_metric)}{_fc_suffix}", "Fraction" + _fc_suffix)
    _fov_active = ctx.state.plot.use_fov_replicates
    _metric_key = ctx.state.channel.active_metric
    if _fov_active:
        _n_title = events_per_fov_title(_metric_key, band_lbl)
        _n_ylabel = "N(above)/FOV"
    else:
        _n_title = events_total_title(_metric_key)
        _n_ylabel = "N(above)"
    apply_ax_style(ax_n, f"{_n_title}{_fc_suffix}", _n_ylabel + _fc_suffix)
    active_rsets = active_replicate_sets(ctx.state.selection.selections, ctx.state.dataset.well_paths)
    cdf_lbl = (f"{_ch} {metric_label} CDF (all wells per replicate set)" if active_rsets else f"{_ch} {metric_label} CDF (all selected wells)")
    apply_ax_style(ax_cdf, cdf_lbl, "Cumulative fraction")
    ax_n.set_xlabel("Time (hours)", fontsize=8, labelpad=5)
    ax_frac.set_ylim(-0.05, 1.05)
    ax_cdf.set_xlabel(f"{_ch} {metric_label}", fontsize=8, labelpad=5)
    ax_cdf.set_ylim(-0.02, 1.05)

    has_data = bool(ctx.state.dataset.well_paths)
    # Empty-state warning needs to honour Screen mode so the text isn't
    # painted against a default white matplotlib figure when the rest of
    # the app is dark. Force the figure + axes facecolor to the theme bg
    # before drawing the message.
    if not has_data:
        try:
            fig.set_facecolor(_bg)
        except Exception:
            swallowed()  # was: pass (silent except)
        for ax in (ax_mean, ax_frac, ax_n, ax_cdf):
            ax.set_title("")
            ax.cla()
            ax.set_facecolor(_bg)
            ax.text(0.5, 0.5, NO_DATA_MSG, transform=ax.transAxes,
                    ha="center", va="center", color=_muted_fg, fontsize=10)
            ax.set_axis_off()
        canvas.draw_idle()
        ctx.notify.set_status("No data loaded.")
        return
    if not selected and not active_rsets:
        try:
            fig.set_facecolor(_bg)
        except Exception:
            swallowed()  # was: pass (silent except)
        for ax in (ax_mean, ax_frac, ax_n, ax_cdf):
            ax.set_title("")
            ax.set_facecolor(_bg)
            ax.text(0.5, 0.5, NO_SELECTION_MSG, transform=ax.transAxes, ha="center", va="center", color=_muted_fg, fontsize=10)
            ax.set_axis_off()
        canvas.draw_idle()
        ctx.notify.set_status("No wells selected.")
        return

    any_ts = any_cdf = any_n = False
    # Single source of truth: ask ``collect_line_series`` for the
    # fully-normalized series in legend order, then iterate to paint.
    series_list, _ = collect_line_series(
        ctx,
        threshold=threshold, use_sem=use_sem,
        miss_sink=fc_misses, include_cdf=True,
        all_fluor_values_filtered=all_fluor_values_filtered,
    )
    for series in series_list:
        ts, cdf, n = _paint_series(ctx, series, fov_active=_fov_active)
        any_ts = any_ts or ts
        any_cdf = any_cdf or cdf
        any_n = any_n or n

    _finalize_line_axes(
        ctx, any_ts=any_ts, any_cdf=any_cdf, any_n=any_n,
        fov_active=_fov_active, fc_active=bool(fc_vs_ctrl or fc_vs_t0),
        legend_kw=legend_kw, threshold=threshold, warn=warn, muted_fg=_muted_fg,
    )

    if fc_misses:
        # Tell the user which timepoints lost their bars/points to a
        # missing control sample, so an unexpectedly empty plot isn't a
        # silent surprise.
        _tps = ", ".join(f"{t:g}" for t in sorted(fc_misses))
        ctx.notify.set_status(
            f"Fold change: control missing at t={_tps} — those points dropped."
        )
    elif active_rsets:
        n_wells = sum(sum(1 for w in r.wells if w in ctx.state.dataset.well_paths) for r in active_rsets)
        # threshold + band now live in the always-visible status chips (F1).
        ctx.notify.set_status(f"{len(active_rsets)} replicate set(s)  ·  {n_wells} well(s)")
    else:
        n_total = sum(len(get_rows(ctx, l)) for l in selected)
        ctx.notify.set_status(f"{len(selected)} well(s)  |  {n_total:,} nuclei")

    # Re-apply the Export Style sidebar's prefs so font sizes, axis
    # limits, log scale, etc. survive a redraw (matches what the bar,
    # heatmap, and distribution renderers do).
    try:
        apply_export_style_to_current(ctx, fig, canvas)
    except Exception:  # pragma: no cover - never let style restore break a draw
        swallowed()  # was: pass (silent except)
    canvas.draw_idle()


def on_fig_click(ctx, event) -> None:
    """Right-click on a Line-Graphs axes -> toggle that axes' legend."""
    if event.inaxes is None or event.button != 3:
        return
    canvas = ctx.widgets.get_or_none("_line_canvas")
    ax_map = {
        id(ctx.widgets.get_or_none("_line_ax_mean")): "mean",
        id(ctx.widgets.get_or_none("_line_ax_frac")): "frac",
        id(ctx.widgets.get_or_none("_line_ax_n")):    "n",
        id(ctx.widgets.get_or_none("_line_ax_cdf")):  "cdf",
    }
    clicked_ax = event.inaxes
    key = ax_map.get(id(clicked_ax))
    if key is None:
        return
    legend_visible = ctx.state.plot.legend_visible
    legend_visible[key] = not legend_visible[key]
    leg = clicked_ax.get_legend()
    if leg is not None:
        leg.set_visible(legend_visible[key])
        canvas.draw_idle()
    state = "shown" if legend_visible[key] else "hidden"
    ctx.notify.set_status(f"Legend for '{key}' plot {state}  (right-click to toggle)")

# ``toggle_sem`` moved to ``well_viewer.core.plot_toggles`` (A8 enabler E3) so
# non-plot features (batch-export's inline Error-Band row) can drive the SEM/SD
# toggle through ctx without a cross-feature import.

