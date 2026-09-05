"""Line-graph rendering helpers extracted from well_viewer3."""

from __future__ import annotations

from well_viewer.data.metric_labels import spread_label

import math
from dataclasses import dataclass, field

import numpy as np

from well_viewer.domain import fold_change as _fc
from well_viewer.domain.numeric import is_number, is_numeric
from well_viewer.domain.stats_compute import compute_rep_stats
from well_viewer.domain.stats_compute import compute_rep_per_fov_stats
from well_viewer.domain.stats_compute import aggregate_well_cached, cdf_values_cached
from well_viewer.domain.stats_compute import compute_rep_n_above_stats
from well_viewer.domain.stats_compute import compute_rep_n_above_total
from well_viewer.domain.stats_compute import replicate_unit
from well_viewer.domain.plot_members import plot_replicate_sets
from well_viewer.domain.selections_model import (
    color_for_replicate_set, color_for_well,
    well_display_label,
)
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.domain.line_colors import (
    LineTraceStyle,
    line_color_mode,
    trace_style_for_mode,
)
from well_viewer.domain.plot_order import apply_order as _apply_order
from well_viewer.domain.fold_change_series_cache import (
    ControlSeriesCache as _ControlSeriesCache,
)
from well_viewer.domain.fold_change_pairing import (
    control_denominators,
    normalize_member_pts,
    shortfall_from_items,
)
from well_viewer.domain import plot_members as _members
from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates, on_cutoff_for_column,
)
from well_viewer.data.data_loading import get_rows
from well_viewer.core.empty_state_copy import NO_DATASET_MESSAGE
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_annotations import (
    LABEL_FRAC_CAP,
    LABEL_SPAN_HEADROOM,
    annotate_series,
    value_label_settings,
)
from well_viewer.core.plot_style import apply_export_style_to_current
from well_viewer.core.plot_style import apply_time_rescale
from well_viewer.domain.time_rescale import axis_label as time_axis_label
from well_viewer.domain.time_rescale import rescale_for as time_rescale_for


#: Written onto an otherwise-empty figure so an *exported* copy states its
#: own shortfall; the on-screen placeholder covers this canvas.
EMPTY_SELECTION_EXPORT_NOTE = "No wells selected — nothing to plot."
NO_DATA_MSG = NO_DATASET_MESSAGE


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
    #: Cross-well total events, for the CSV's integer ``n_above_threshold``
    #: column. ``n_above`` is the plotted value and is a per-well mean for a
    #: replicate set, so the two differ and the CSV needs its own. ``None``
    #: for a non-aggregated row, never ``0``.
    n_above_total: int | None = None
    n_above_spread: float = 0.0
    has_mean: bool = True
    has_frac: bool = True

    def as_aggpoint(self) -> tuple:
        """Convert back to the AggPoint tuple shape ``line_metric_row`` expects.

        Index 4 carries the **drawn** value, which for a replicate set is a
        per-well mean and therefore fractional. It used to be rounded down with
        ``int()`` here, which is how a curve at 7.5 reached the batch line CSV
        as 7; the CSV's integer count now comes from index 10 instead.
        """
        n_above = (float(self.n_above)
                   if is_number(self.n_above)
                   else float("nan"))
        return (
            self.t, self.mean, self.spread, self.frac,
            n_above, int(self.n_total), self.frac_spread,
            self.n_above_pf_mean, self.n_above_pf_spread,
            self.n_above_spread,
            self.n_above_total,
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
        always raw). A **numpy array**, and it matters at plate scale:
        ``_all_fluor_values_filtered`` already returns one, and boxing it
        into a Python list costs 32 bytes a value against 8 — a plate of
        537,393 cells is 4.3 MB as an array and 17 MB as a list, before
        the sorted copy and the y-values the CDF panel then builds from
        it. Readers coerce with ``np.asarray`` rather than assuming, so a
        caller handing in a plain list still works.
      * ``fc_control`` / ``fc_outcome`` — which control **this trace** was
        divided by and why it is what it is, mirroring the same pair on
        ``BarItem``. They live on the series rather than the point
        because a member's control does not change with time; what
        changes with time is whether the control had a sample there,
        which the blanked points already say.
    """
    key: str
    display: str
    color: str
    kind: str
    wells: list[str]
    points: list[LinePoint] = field(default_factory=list)
    cdf_vals: np.ndarray = field(
        default_factory=lambda: np.empty(0, dtype=float))
    fc_control: object | None = None
    fc_outcome: str = ""


@dataclass
class LineAxes:
    """The four stacked Line-Graphs panels, passed explicitly to the renderer.

    Bundling the axes is what lets :func:`render_line_series` draw the same
    four-panel figure both on-tab (axes from ``ctx.widgets``) and off-screen
    (batch export owns its own :class:`~matplotlib.figure.Figure`). Order and
    identity match the tab: mean / fraction / N(above) / CDF.
    """
    mean: object
    frac: object
    n: object
    cdf: object


def _aggpoint_to_linepoint(pt) -> LinePoint:
    """Lift a raw AggPoint tuple into a :class:`LinePoint`."""
    t = float(pt[0])
    mean = float(pt[1]) if is_numeric(pt[1]) else float("nan")
    spread = float(pt[2]) if is_numeric(pt[2]) else 0.0
    frac = float(pt[3]) if (len(pt) > 3 and is_numeric(pt[3])) else float("nan")
    # n_above may be a fold-change ratio (float, or NaN at a missing-control
    # timepoint) once FC normalizes the N panel, so keep it float / NaN-safe
    # rather than int(): the renderer reads ``float(p.n_above)`` and skips
    # points without a mean anyway.
    _n_raw = pt[4] if len(pt) > 4 else 0
    n_above = (float(_n_raw)
               if is_number(_n_raw)
               else float("nan"))
    n_total = int(pt[5]) if len(pt) > 5 else 0
    frac_spread = float(pt[6]) if len(pt) > 6 else 0.0
    n_above_pf_mean = float(pt[7]) if len(pt) > 7 else 0.0
    n_above_pf_spread = float(pt[8]) if len(pt) > 8 else 0.0
    n_above_spread = float(pt[9]) if len(pt) > 9 else 0.0
    n_above_total = int(pt[10]) if (len(pt) > 10 and pt[10] is not None) else None
    return LinePoint(
        t=t, mean=mean, spread=spread, frac=frac,
        n_above=n_above, n_total=n_total, frac_spread=frac_spread,
        n_above_pf_mean=n_above_pf_mean, n_above_pf_spread=n_above_pf_spread,
        n_above_spread=n_above_spread, n_above_total=n_above_total,
        has_mean=not (isinstance(mean, float) and math.isnan(mean)),
        has_frac=not (isinstance(frac, float) and math.isnan(frac)),
    )


def collect_line_series(
    ctx,
    *,
    threshold: float,
    use_sem: bool,
    val_col: str | None = None,
    fc_state=None,
    fc_baseline_tp: float | None = None,
    miss_sink: set[float] | None = None,
    include_cdf: bool = True,
    all_fluor_values_filtered=None,
) -> tuple[list[LineSeries], str]:
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
    band_lbl = spread_label(use_sem)

    if fc_state is None:
        fc_state = _fc.fold_change_state(ctx)
    fc = _fc.coerce_fc_state(fc_state)
    if fc_baseline_tp is None:
        fc_baseline_tp = _fc.parse_baseline_tp(ctx)
    fc_op_ctrl = _fc.parse_fc_op_ctrl(ctx)
    fc_op_t0 = _fc.parse_fc_op_t0(ctx)
    if val_col is None:
        val_col = ctx.state.channel.active_val_col

    fluor_gates = get_all_fluor_gates(ctx)
    active_rsets = plot_replicate_sets(ctx)
    rep_per_fov = ctx.state.plot.use_fov_replicates if active_rsets else False
    per_fov_spread = ctx.state.plot.use_fov_replicates if not active_rsets else False

    # Control series — mean-of-per-well-means / per-FOV / per-well agg
    # matching each member's own stat. ``normalize_pts`` is handed
    # {t: (mean, spread)} so error propagation has the right denominator
    # uncertainty. The N panel plots the per-FOV-mean count when FOV-spread is
    # on, else the per-well statistic — the control denominator and the scaled
    # field must match.
    fc_n_per_fov = bool(rep_per_fov or per_fov_spread)
    ctrl_cache = _ControlSeriesCache(
        ctx, threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=fc_n_per_fov, fluor_gates=fluor_gates,
    )
    denoms = control_denominators(
        ctx, ctrl_cache, fc,
        _members.line_member_keys(ctx) if fc.per_member else (),
        scope_wells=_members.member_wells(ctx, "line") if fc.per_member else None,
        member_rsets=_members.member_replicate_sets(ctx) if fc.per_member else None,
        miss_sink=miss_sink,
    )
    hidden_members = denoms.hidden_members
    _norm_kw = dict(fc=fc, n_per_fov=fc_n_per_fov, baseline_tp=fc_baseline_tp,
                    op_ctrl=fc_op_ctrl, op_t0=fc_op_t0, miss_sink=miss_sink)

    series_out: list[LineSeries] = []

    if active_rsets:
        ordered_rsets = _apply_order(
            active_rsets,
            list(ctx.state.plot.line_order_rsets or []),
            key=lambda r: getattr(r, "name", ""),
        )
        for rset in ordered_rsets:
            if rset.name in hidden_members:
                continue
            color = color_for_replicate_set(rset, ctx.state.selection.selections)
            valid_wells = [w for w in rset.wells if w in ctx.state.dataset.well_paths]
            # Unified replicate rule: per-FOV (FOVs pooled across the set's wells)
            # when FOV mode is on, else per-well — matching the stats n.
            rset_per_fov = replicate_unit(len(valid_wells), rep_per_fov) == "fov"
            all_tps: set = set()
            cdf_parts: list = []
            # Cells the set holds at each timepoint, pooled across its wells.
            # The per-well aggregation is already being walked for the
            # timepoint list and is cached, so the sum is free — and without it
            # the CSV's ``n_total`` had nothing to report for a set at all: the
            # point built below carried a placeholder there, which the exporter
            # (rightly) refused to print, leaving the column empty on every row
            # of a replicate-set file.
            cells_at: dict = {}
            for lbl in valid_wells:
                for _pt in aggregate_well_cached(ctx,
                    lbl, threshold=threshold, use_sem=False,
                    val_col=val_col,
                    fluor_gates=fluor_gates,
                ):
                    all_tps.add(_pt[0])
                    cells_at[_pt[0]] = cells_at.get(_pt[0], 0) + int(_pt[5])
                if include_cdf and all_fluor_values_filtered is not None:
                    # Collect the arrays and concatenate once. ``.extend`` here
                    # boxed every value into a Python float, four times the
                    # memory of the array it was handed and the reason a large
                    # plate ran out of it in the CDF panel.
                    cdf_parts.append(cdf_values_cached(
                        ctx, lbl, val_col=val_col, fluor_gates=fluor_gates,
                        compute=all_fluor_values_filtered,
                    ))
            _raw_pts: list = []
            for t in sorted(all_tps):
                # Index 10 is the cross-well total the CSV's integer column
                # keeps; index 4 stays the drawn value.
                _n_above_total = compute_rep_n_above_total(ctx, rset, t, threshold)
                _n_above_spread = 0.0
                if rset_per_fov:
                    gm, gerr, gf, gfe, gnpf_m, gnpf_s = compute_rep_per_fov_stats(ctx,
                        rset, t, threshold, use_sem,
                    )
                else:
                    gm, gerr, gf, gfe = compute_rep_stats(ctx,
                        rset, t, threshold, use_sem,
                    )
                    # Mean of the set's per-well counts — the same statistic
                    # the Mean and Fraction panels beside it use, and the one
                    # the Bar tab draws. This read ``compute_rep_n_above``, a
                    # total across the set's wells, so the two tabs plotted
                    # different numbers for the same replicate set: 5 per well
                    # on a bar against 10 on a curve.
                    _n_above_val, _n_above_spread = compute_rep_n_above_stats(
                        ctx, rset, t, use_sem, threshold)
                    # ``gfe`` is ``compute_rep_stats``'s fourth return value —
                    # the spread of the set's per-well fractions. It was bound
                    # to ``_`` and then overwritten with 0.0 here, which is why
                    # the Fraction panel had no error band in well-replicate
                    # mode while the Bar tab, reading the same producer, did.
                    # The per-FOV fields stay 0.0: they are genuinely not
                    # computed on this branch.
                    gnpf_m, gnpf_s = 0.0, 0.0
                if not (isinstance(gm, float) and math.isnan(gm)):
                    # AggPoint idx 6 = frac_spread (per-FOV fraction error), 7/8 =
                    # per-FOV n-above mean/spread. These were previously dropped,
                    # so the "fraction above threshold" subplot had no FOV error
                    # to draw and fell back to shading the area under the line.
                    _raw_pts.append((
                        t, gm, gerr, gf,
                        _n_above_val if not rset_per_fov else 0,
                        cells_at.get(t, 0),
                        gfe, gnpf_m, gnpf_s,
                        _n_above_spread if not rset_per_fov else 0.0,
                        _n_above_total,
                    ))
            _raw_pts = normalize_member_pts(
                _raw_pts, denoms, rset.name, **_norm_kw)
            points = [_aggpoint_to_linepoint(pt) for pt in _raw_pts]
            n_wells = len(valid_wells)
            display = (
                f"{rset.name} (n={n_wells})" if n_wells != 1 else rset.name
            )
            _ctrl_ref, _outcome = denoms.control_for(rset.name)
            series_out.append(LineSeries(
                key=rset.name, display=display, color=color, kind="repset",
                wells=valid_wells, points=points,
                cdf_vals=(np.concatenate(cdf_parts) if cdf_parts
                          else np.empty(0, dtype=float)),
                fc_control=_ctrl_ref, fc_outcome=str(_outcome or ""),
            ))
        return series_out, band_lbl

    if ctx.state.selection.selections:
        # Group mode (replicate sets / groups defined) with every group hidden
        # → no series. The per-well branch below would fall back to
        # ``selected_wells``, but in group mode the plate is passive and clicks
        # toggle group visibility, so that set is stale: falling back
        # resurrected every well after "Deselect all". ``ordered_bar_keys``
        # refuses the same fallback for the same reason, and the two panes
        # sitting side by side must not disagree about what is selected.
        return series_out, band_lbl

    series_out.extend(_per_well_series(
        ctx, denoms, hidden_members,
        threshold=threshold, use_sem=use_sem, val_col=val_col,
        fluor_gates=fluor_gates, per_fov_spread=per_fov_spread,
        include_cdf=include_cdf,
        all_fluor_values_filtered=all_fluor_values_filtered,
        norm_kw=_norm_kw,
    ))
    return series_out, band_lbl


def _per_well_series(ctx, denoms, hidden_members, *, threshold, use_sem, val_col,
                     fluor_gates, per_fov_spread, include_cdf,
                     all_fluor_values_filtered, norm_kw) -> list:
    """One :class:`LineSeries` per selected well, in the saved line order.

    Split out of :func:`collect_line_series` so that function stays under the
    complexity ratchet — the group-mode guard above it is one more branch, and
    this loop was already the larger half of the body.
    """
    out: list[LineSeries] = []
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
        if label in hidden_members:
            continue
        color = color_for_well(label)
        pts = aggregate_well_cached(ctx,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        pts = normalize_member_pts(list(pts or []), denoms, label, **norm_kw)
        points = [_aggpoint_to_linepoint(pt) for pt in (pts or [])]
        cdf_vals = np.empty(0, dtype=float)
        if include_cdf and all_fluor_values_filtered is not None:
            # Straight through as the array it already is; ``list()`` here was
            # a boxed copy of every cell in the well.
            cdf_vals = cdf_values_cached(
                ctx, label, val_col=val_col, fluor_gates=fluor_gates,
                compute=all_fluor_values_filtered,
            )
        display = well_display_label(ctx, label)
        _ctrl_ref, _outcome = denoms.control_for(label)
        out.append(LineSeries(
            key=label, display=display, color=color, kind="well",
            wells=[label], points=points, cdf_vals=cdf_vals,
            fc_control=_ctrl_ref, fc_outcome=str(_outcome or ""),
        ))
    return out


#: Vertices a CDF curve is worth drawing on screen. A sorted CDF is monotone,
#: so between two adjacent pixels it can only rise; drawing a thousand vertices
#: into a panel a few hundred pixels wide paints the same line more slowly.
#: ``None`` means draw every point, which is what the export paths pass — a
#: figure saved at 300 dpi is read at a resolution where the extra vertices can
#: matter, and a silently thinned export is a degraded artifact.
SCREEN_CDF_POINTS = 512


def _decimated(values, budget: int | None):
    """*values* thinned to at most *budget* points, endpoints kept.

    Returns the indices as well, because the CDF's y-axis is a function of each
    point's ORIGINAL rank among all the cells — thin the x values and compute y
    from the thinned length and the curve stops being that well's CDF.
    """
    n = values.size
    if budget is None or n <= budget:
        return values, np.arange(1, n + 1, dtype=float)
    idx = np.linspace(0, n - 1, budget).astype(int)
    return values[idx], (idx + 1).astype(float)


def _paint_series(axes: LineAxes, series, *, fov_active: bool,
                  cdf_point_budget: int | None = None,
                  value_labels: bool = False,
                  value_label_format: str = "",
                  style: LineTraceStyle | None = None) -> tuple[bool, bool, bool]:
    """Draw one collected line series onto the mean / fraction / N / CDF axes
    (audit A5). Returns ``(painted_ts, painted_cdf, painted_n)`` — the caller
    ORs these across series to decide legend/empty handling. Pure side effect on
    the axes; the drawing sequence mirrors the original inline loop exactly.

    Takes an explicit :class:`LineAxes` bundle (not ``ctx.widgets``) so the same
    painter serves the on-tab redraw and off-screen batch export.

    *value_labels* prints each aggregate point's ``mean ± spread`` beside it
    (Properties → Overlays), in the trace's own colour so a panel of several
    series stays readable. The CDF panel takes none: its points are one per
    cell, not a mean of anything, and there is nothing there to state a spread
    for.

    *style* is the trace's resolved colour and marker (Properties → Colours,
    via ``domain/line_colors.trace_style``). ``None`` means the plate mode,
    where the colour is the series' own and each panel keeps the marker that
    identifies *it* — a circle, a square, a triangle. In grey mode the marker
    identifies the **trace** instead and is therefore the same shape on all
    four panels, so a reader matching a curve from the Value panel to the
    Fraction panel is matching the marker."""
    ax_mean = axes.mean
    ax_frac = axes.frac
    ax_n = axes.n
    ax_cdf = axes.cdf
    any_ts = any_cdf = any_n = False
    if style is None:
        style = LineTraceStyle(color=series.color)
    color = style.color
    # ``_mark`` fills in the per-trace marker when the colour mode set one and
    # otherwise leaves the panel's own. Passing the face and edge explicitly in
    # both modes keeps a filled marker filled once the export-style pass has
    # run over it — that pass pins both to the line colour, so a marker whose
    # openness is data has to say so (``_aw_marker_face_is_data``) as well as
    # one whose shape is.
    def _mark(panel_marker: str) -> dict:
        if style.marker is None:
            return {"marker": panel_marker}
        kw = {
            "marker": style.marker,
            "markerfacecolor": style.face,
            "markeredgecolor": style.color,
        }
        if style.linestyle is not None:
            kw["linestyle"] = style.linestyle
        return kw

    def _tag(artist_list):
        """Mark a trace whose marker encodes which trace it is.

        Three flags, and all three are needed, because the export-style pass
        overrides three separate things from the Properties prefs: the marker
        *shape* unless ``_aw_marker_is_data`` says otherwise, the marker *face*
        unless ``_aw_marker_face_is_data`` does, and the *dash pattern* unless
        ``_aw_linestyle_is_data`` does. Each was measured going wrong before its
        flag existed — without the face one every open marker came back filled
        the moment the pass ran, which is every redraw, and half the cycle
        stopped distinguishing anything.
        """
        if style.marker is None:
            return
        for artist in artist_list:
            artist._aw_marker_is_data = True
            artist._aw_marker_face_is_data = True
            if style.linestyle is not None:
                artist._aw_linestyle_is_data = True
    agg_times = [p.t for p in series.points if p.has_mean]
    agg_means = [p.mean for p in series.points if p.has_mean]
    agg_errs = [p.spread for p in series.points if p.has_mean]
    if agg_times:
        _tag(ax_mean.plot(
            agg_times, agg_means, color=color, lw=2,
            markersize=4, label=series.display, zorder=3, **_mark("o"),
        ))
        ax_mean.fill_between(
            agg_times,
            [m - e for m, e in zip(agg_means, agg_errs)],
            [m + e for m, e in zip(agg_means, agg_errs)],
            color=color, alpha=0.15, zorder=2,
        )
        if value_labels:
            annotate_series(ax_mean, agg_times, agg_means, agg_errs,
                            fmt=value_label_format, color=color)
        vf = [(p.t, p.frac, p.frac_spread) for p in series.points if p.has_frac]
        if vf:
            vt2, vf2, vfe = zip(*vf)
            _tag(ax_frac.plot(
                vt2, vf2, color=color, lw=2,
                markersize=3, label=series.display, zorder=3, **_mark("s"),
            ))
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
            if value_labels:
                annotate_series(ax_frac, vt2, vf2, vfe,
                                fmt=value_label_format, color=color)
        any_ts = True
        # N events panel
        if fov_active:
            vn = [(p.t, p.n_above_pf_mean, p.n_above_pf_spread)
                  for p in series.points if p.has_mean]
        else:
            # ``n_above_spread`` is the cross-well spread of the per-well
            # counts, computed by ``compute_rep_n_above_stats`` and carried on
            # the point since it was built (index 9 of the AggPoint tuple).
            # Passing a literal 0.0 here threw it away at the last step, so the
            # Line tab's Events panel drew no replicate error band while the
            # Bar tab, reading the same statistic, drew one.
            vn = [(p.t, float(p.n_above), float(p.n_above_spread))
                  for p in series.points if p.has_mean]
        if vn:
            vnt, vnv, vne = zip(*vn)
            _tag(ax_n.plot(
                vnt, vnv, color=color, lw=2,
                markersize=3, label=series.display, zorder=3, **_mark("^"),
            ))
            if any(e > 0 for e in vne):
                lo = [max(0.0, v - e) for v, e in zip(vnv, vne)]
                hi = [v + e for v, e in zip(vnv, vne)]
                ax_n.fill_between(vnt, lo, hi, color=color, alpha=0.15, zorder=2)
            if value_labels:
                annotate_series(ax_n, vnt, vnv, vne,
                                fmt=value_label_format, color=color)
            any_n = True
    # ``np.asarray`` rather than assuming: the field is an array, but a caller
    # handing in a plain list must not silently draw nothing — and an array's
    # truthiness raises, so the emptiness test is on ``.size``.
    cdf_vals = np.asarray(series.cdf_vals, dtype=float)
    if cdf_vals.size:
        fluor_s = np.sort(cdf_vals)
        # ``n`` is the well's cell count and comes from the FULL array, before
        # any thinning. Reading it off the drawn curve instead would put
        # "n=512" in the legend of a well holding 21,600 cells — a wrong number
        # in the one place a reader goes to find the right one.
        n = fluor_s.size
        cdf_label = f"{series.display} (n={n:,})"
        x, ranks = _decimated(fluor_s, cdf_point_budget)
        # The CDF is the one panel with no error band and thousands of points,
        # so in grey mode it is also the one panel a shade alone cannot
        # identify: ten wells would be ten grey curves. It takes the trace's
        # marker too, spaced along the curve rather than at every vertex —
        # ``markevery`` as a fraction places them at even *screen* intervals,
        # so a curve that is nearly vertical over most of its range still gets
        # its marks spread out. In plate mode it stays a bare line, exactly as
        # before, because there the colour already names it.
        _cdf_marks = {} if style.marker is None else {
            **_mark(style.marker), "markersize": 4, "markevery": 0.15,
        }
        # The CDF is a step-like curve read for where it crosses, so it keeps a
        # solid line: a dashed one there reads as a gap in the distribution
        # rather than as an identity, and the marker already carries the
        # identity on this panel.
        _cdf_marks.pop("linestyle", None)
        _tag(ax_cdf.plot(
            x, ranks / n,
            color=color, lw=1.8, label=cdf_label, zorder=3, **_cdf_marks,
        ))
        any_cdf = True
    return any_ts, any_cdf, any_n


def _has_labeled(ax) -> bool:
    """True if *ax* has at least one real (non-underscore) legend label."""
    handles, labels = ax.get_legend_handles_labels()
    return any(lbl and not str(lbl).startswith("_") for lbl in labels)


_DEFAULT_LEGEND_VISIBLE = {"mean": True, "frac": True, "n": True, "cdf": True}

#: How many traces a legend names before it stops naming them individually.
#: A plate-wide selection puts one entry per well in every panel, and at 96
#: entries the legend is both illegible — it overruns the axes it is supposed to
#: annotate — and the single most expensive thing in the redraw: matplotlib
#: measures every label through FreeType while packing the legend box, several
#: times per label, which cost more than drawing all the data.
#:
#: ``None`` means name them all, which is what the export paths pass: a figure
#: saved at 300 dpi has room for a legend the screen does not.
SCREEN_LEGEND_ENTRIES = 12


def _capped_legend(ax, legend_kw: dict, legend_max: int | None):
    """``ax.legend`` naming at most *legend_max* traces, and saying so.

    The overflow is announced in the legend itself rather than left implicit.
    A legend that silently stops after twelve of ninety-six entries is a
    degraded artifact that looks exactly like a complete one — the reader has
    no way to tell that the trace they are looking for is drawn but unnamed.
    The final entry says how many are not listed.
    """
    handles, labels = ax.get_legend_handles_labels()
    if legend_max is None or len(handles) <= legend_max:
        return ax.legend(**legend_kw)
    from matplotlib.lines import Line2D

    hidden = len(handles) - legend_max
    handles = list(handles[:legend_max])
    labels = list(labels[:legend_max])
    handles.append(Line2D([], [], linestyle="none"))
    labels.append(f"+{hidden:,} more (not listed)")
    return ax.legend(handles, labels, **legend_kw)


def _finalize_line_axes(
    axes: LineAxes, *, any_ts: bool, any_cdf: bool, any_n: bool,
    fov_active: bool, fc_active: bool, legend_kw: dict,
    threshold: float, warn: str, muted_fg,
    legend_visible: dict | None = None,
    show_threshold_line: bool = True,
    cdf_xrange: tuple[float, float] | None = None,
    empty_cdf_msg: str | None = None,
    draggable_legends: bool = False,
    legend_max: int | None = None,
    value_labels: bool = False,
) -> None:
    """Post-paint axis finishing (audit A5): N-panel integer ticks, per-panel
    legends (honouring saved visibility), and the CDF threshold line / x-range /
    empty message.

    All tab-only inputs are parameters so the finalizer works off-screen:
    ``legend_visible`` (defaults to all-visible), ``show_threshold_line``,
    ``cdf_xrange`` (``None`` → caller-derived; the on-tab wrapper passes the
    ``_cdf_xmin/xmax_edit`` values), ``empty_cdf_msg`` (``None`` → draw nothing
    when the CDF is empty), and ``draggable_legends`` (tab only)."""
    ax_mean = axes.mean
    ax_frac = axes.frac
    ax_n = axes.n
    ax_cdf = axes.cdf
    if legend_visible is None:
        legend_visible = _DEFAULT_LEGEND_VISIBLE

    def _place_legend(ax, key: str):
        leg = _capped_legend(ax, legend_kw, legend_max)
        if draggable_legends:
            leg.set_draggable(True)
        leg.set_visible(legend_visible.get(key, True))
        return leg

    if value_labels:
        # The labels are drawn beyond the outermost point of each trace, in ink
        # rather than in data, so matplotlib's own 5% margin does not know
        # about them and crops the extreme one. Widen the time panels before
        # the N panel's clamp below reads them.
        #
        # The Fraction panel is in the list **only under fold change**, which is
        # exactly when its clamp does not run: with fold change off,
        # ``redraw_line_plots`` has already pinned it to the widened
        # ``LABEL_FRAC_CAP`` and widening again would stack one allowance on
        # the other. With it on, the field is a ratio, no clamp applies, and
        # nothing else was giving this panel any room at all — the Bar tab's
        # Fraction panel gets it from ``autofit``, so leaving it out here made
        # two surfaces answer one question two ways.
        _labelled = [ax_mean, ax_n] + ([ax_frac] if fc_active else [])
        for _ax_lbl in _labelled:
            _lo, _hi = _ax_lbl.get_ylim()
            if _hi <= _lo:
                continue
            _pad = (_hi - _lo) * (LABEL_SPAN_HEADROOM - 1.0)
            # Padded at both ends when the panel holds negatives: a label on a
            # point below zero is drawn below it, so the room it needs is at
            # the floor, where the top padding does nothing for it.
            _ax_lbl.set_ylim(_lo - (_pad if _lo < 0 else 0.0), _hi + _pad)
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
            _place_legend(ax_mean, "mean")
        if _has_labeled(ax_frac):
            _place_legend(ax_frac, "frac")
        if any_n and _has_labeled(ax_n):
            _place_legend(ax_n, "n")
    if any_cdf:
        if show_threshold_line:
            ax_cdf.axvline(threshold, color=warn, lw=1.2, ls="--", label=f"threshold={threshold:.2f}", zorder=4, picker=8)
        if cdf_xrange is not None:
            cdf_lo, cdf_hi = cdf_xrange
        else:
            cdf_lo, cdf_hi = 0.0, 300.0
        if cdf_hi <= cdf_lo:
            cdf_hi = cdf_lo + 1.0
        if show_threshold_line:
            ax_cdf.axvspan(threshold, cdf_hi, alpha=0.05, color=warn, zorder=1)
        if _has_labeled(ax_cdf):
            _place_legend(ax_cdf, "cdf")
        ax_cdf.set_xlim(cdf_lo, cdf_hi)
    elif empty_cdf_msg is not None:
        ax_cdf.text(0.5, 0.5, empty_cdf_msg, transform=ax_cdf.transAxes, ha="center", va="center", color=muted_fg, fontsize=10)


def render_line_series(
    axes: LineAxes, series_list, *,
    fov_active: bool, fc_active: bool, threshold: float,
    legend_kw: dict, warn_color, muted_fg,
    legend_visible: dict | None = None,
    show_threshold_line: bool = True,
    cdf_xrange: tuple[float, float] | None = None,
    empty_cdf_msg: str | None = None,
    draggable_legends: bool = False,
    cdf_point_budget: int | None = None,
    legend_max: int | None = None,
    value_labels: bool = False,
    value_label_format: str = "",
    color_mode: str = "plate",
) -> tuple[bool, bool, bool]:
    """Paint every :class:`LineSeries` onto *axes* then finalize the panels.

    Single source of truth for line-plot *drawing*, mirroring how
    ``collect_line_series`` is the single source of truth for the *data*. Both
    the on-tab redraw (``redraw_line_plots``) and the batch-export figure call
    this with their own :class:`LineAxes`, so the four-panel layout
    (mean / fraction / N(above) / CDF) can never drift between them.

    When ``cdf_xrange`` is ``None`` the CDF x-limits are derived from the plotted
    values (batch behaviour); the tab passes its own ``_cdf_xmin/xmax_edit``
    range. Returns ``(any_ts, any_cdf, any_n)``.

    *color_mode* is Properties → Colours (``"plate"`` or ``"grey"``), resolved
    by the caller and passed rather than read off a ``ctx`` because this
    function has none — the batch line figure calls it with its own state, and
    a mode the tab honoured and the export dropped is the divergence this
    single renderer exists to prevent. The **position** in *series_list* is what
    picks a trace's shade, dash and marker, so the drag-reorder the user applied
    to the legend is the order those cues follow.
    """
    any_ts = any_cdf = any_n = False
    for index, series in enumerate(series_list):
        ts, cdf, n = _paint_series(axes, series, fov_active=fov_active,
                                   cdf_point_budget=cdf_point_budget,
                                   value_labels=value_labels,
                                   value_label_format=value_label_format,
                                   style=trace_style_for_mode(
                                       color_mode, index, series.color))
        any_ts = any_ts or ts
        any_cdf = any_cdf or cdf
        any_n = any_n or n
    if cdf_xrange is None and any_cdf:
        # Per series, never flattened. Building one list of every value across
        # every series held the whole plate a second time, as boxed floats, to
        # compute two numbers. The values are finite by construction
        # (``_all_fluor_values_filtered`` masks on ``np.isfinite``), so
        # ``min``/``max`` over the flattened list and these agree exactly.
        lo = hi = None
        for s in series_list:
            v = np.asarray(s.cdf_vals, dtype=float)
            if not v.size:
                continue
            v_lo, v_hi = float(v.min()), float(v.max())
            lo = v_lo if lo is None else min(lo, v_lo)
            hi = v_hi if hi is None else max(hi, v_hi)
        if lo is not None:
            cdf_xrange = (lo, hi)
    _finalize_line_axes(
        axes, any_ts=any_ts, any_cdf=any_cdf, any_n=any_n,
        fov_active=fov_active, fc_active=fc_active, legend_kw=legend_kw,
        threshold=threshold, warn=warn_color, muted_fg=muted_fg,
        legend_visible=legend_visible, show_threshold_line=show_threshold_line,
        cdf_xrange=cdf_xrange, empty_cdf_msg=empty_cdf_msg,
        draggable_legends=draggable_legends, legend_max=legend_max,
        value_labels=value_labels,
    )
    return any_ts, any_cdf, any_n


def line_status_text(ctx, series_list, fc_misses, active_rsets, selected) -> str:
    """What the status bar says after a line redraw.

    Pure and separate from the redraw so the sentence can be asserted without
    building a window — which matters because the failure it exists to prevent
    is a status line that reports business as usual while traces are missing
    from the plot.

    Fold-change shortfalls come first, read off each trace's own outcome rather
    than off the timepoint miss set. A trace whose control was never assigned,
    or names a group that is not loaded, is blanked without any timepoint being
    recorded as missed, so a status keyed on ``fc_misses`` alone said nothing
    while curves vanished under a legend that still named them. The bar
    renderer reads the same records for the same reason.

    ``fc_misses`` still carries the one shortfall no outcome records — a
    control that resolved but has no sample at some timepoints, which
    ``normalize_pts`` drops point by point — so it is reported when the
    per-trace outcomes are all clean.
    """
    shortfall = shortfall_from_items(series_list, timepoints=fc_misses)
    if shortfall:
        return shortfall
    if fc_misses:
        tps = ", ".join(f"{t:g}" for t in sorted(fc_misses))
        return f"Fold change: control missing at t={tps} — those points dropped."
    if active_rsets:
        well_paths = ctx.state.dataset.well_paths
        n_wells = sum(sum(1 for w in r.wells if w in well_paths)
                      for r in active_rsets)
        # threshold + band now live in the always-visible status chips (F1).
        return f"{len(active_rsets)} replicate set(s)  ·  {n_wells} well(s)"
    n_total = sum(len(get_rows(ctx, lbl)) for lbl in selected)
    return f"{len(selected)} well(s)  |  {n_total:,} nuclei"


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
    # Each of the three time panels (mean / fraction / N) shows its own timepoint
    # tick labels — cla() restores them and matplotlib does not auto-hide labels
    # on manually-added sharex subplots, so no re-suppression here. The matching
    # "Time (hours)" x-labels are set below alongside ax_n's.

    use_sem = ctx.state.plot.use_sem
    band_lbl = spread_label(use_sem)
    # The ON cutoff follows the value column: shapes keep every cell, spot
    # counts read ThreshCountOn, intensities read ThreshFracOn.
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)
    # Filtered against the loaded wells, as the three sibling renderers do
    # (``barplot.controller``, ``heatmap.controller``, ``scatter.collect``).
    # ``selected_wells`` is app-wide state that outlives a dataset swap, so a
    # token left over from the previous plate reaches ``get_rows`` and raises
    # ``KeyError`` — which aborts the whole redraw before it reaches
    # ``apply_export_style_to_current``, losing the Properties-panel settings
    # along with the plot.
    selected = sorted(
        (lbl for lbl in ctx.state.selection.selected_wells
         if lbl in ctx.state.dataset.well_paths),
        key=parse_rc,
    )

    # Fold-change state read once for the axis title suffix. The actual
    # control-series resolution + per-trace normalization happens inside
    # ``collect_line_series`` (single source of truth, mirrors the
    # bar plot's ``collect_bar_items``).
    _live_fc = _fc.fold_change_state(ctx)
    fc_vs_ctrl = _live_fc.vs_control_on
    fc_vs_t0 = _live_fc.vs_t0_on
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
    _fc_suffix = _fc.fold_change_suffix_for(ctx, "line")
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
    active_rsets = plot_replicate_sets(ctx)
    cdf_lbl = (f"{_ch} {metric_label} CDF (all wells per replicate set)" if active_rsets else f"{_ch} {metric_label} CDF (all selected wells)")
    apply_ax_style(ax_cdf, cdf_lbl, "Cumulative fraction")
    # Every time panel carries its own timepoints + x-label (the panels are
    # well-separated by hspace, so each reads as its own graph).
    # The three time panels share the Properties sidebar's "Interval per
    # timepoint" factor: their ticks print the filename's hours multiplied by
    # it, and the label carries the factor so a rescaled axis can't be mistaken
    # for one drawn on the hours the run actually recorded (the timepoint menus
    # and every export still show those).
    _time_rescale = time_rescale_for(ctx.state.plot)
    _time_lbl = time_axis_label("Time (hours)", _time_rescale)
    for _ax_time in (ax_mean, ax_frac, ax_n):
        _ax_time.set_xlabel(_time_lbl, fontsize=8, labelpad=5)
        apply_time_rescale(_ax_time, _time_rescale)
    # Same rule as the Bar tab's ``renderer._set_frac_ylim``, and for the same
    # reason: a raw fraction is bounded, so the clamp is a good default, but
    # under fold change the field is a ratio (which can exceed 1) or a
    # difference (which can go negative). Clamping then clips the very effect
    # the normalisation exists to show, silently — the curve simply leaves the
    # panel. The two tabs draw the same field and disagreed about this.
    _value_labels, _value_label_fmt = value_label_settings(ctx)
    if not _fc.fold_change_state(ctx).active:
        # The clamp opens up when the value labels are on, for the same reason
        # the bar Fraction panel's cap does (both use ``LABEL_FRAC_CAP``):
        # a trace sitting at 1.0 otherwise has its label drawn over the title.
        ax_frac.set_ylim(-0.05, LABEL_FRAC_CAP if _value_labels else 1.05)
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
    from well_viewer.core.selection_controller import has_plottable_selection
    # One answer to "is there anything to draw?", in core, next to the state
    # it reads: the four renderers each spelled the condition out and could
    # therefore disagree with the placeholder drawn over them.
    if not has_plottable_selection(ctx):
        # The words are the tab's shared empty-state placeholder, which covers
        # this canvas. What is left to do here is make sure the figure *behind*
        # it is genuinely empty: Export ▾ / Save figure / per-panel Copy read the
        # Figure object, not the screen, so axes still holding the previous
        # selection's plot would export as though it were current.
        try:
            fig.set_facecolor(_bg)
        except Exception:
            swallowed()  # was: pass (silent except)
        for ax in (ax_mean, ax_frac, ax_n, ax_cdf):
            ax.cla()
            ax.set_facecolor(_bg)
            ax.set_axis_off()
        # One line, on the first panel only. Invisible on screen (the tab's
        # shared placeholder is in front of this canvas) but present in anything
        # exported from the figure, which carries no placeholder with it.
        ax_mean.text(0.5, 0.5, EMPTY_SELECTION_EXPORT_NOTE,
                     transform=ax_mean.transAxes, ha="center", va="center",
                     color=_muted_fg, fontsize=10)
        canvas.draw_idle()
        return

    # Single source of truth: ask ``collect_line_series`` for the
    # fully-normalized series in legend order, then paint via the shared
    # renderer (also used by the batch export, so the panel set never drifts).
    series_list, _ = collect_line_series(
        ctx,
        threshold=threshold, use_sem=use_sem,
        miss_sink=fc_misses, include_cdf=True,
        all_fluor_values_filtered=all_fluor_values_filtered,
    )
    # The tab's CDF x-range comes from the _cdf_xmin/xmax_edit widgets (default
    # 0–300), not the data — resolve it here and hand it to the shared renderer.
    def _edit_float(name: str, default: float) -> float:
        try:
            return float(ctx.widgets.get_or_none(name).text())
        except (ValueError, AttributeError):
            return default
    _cdf_xrange = (_edit_float("_cdf_xmin_edit", 0.0),
                   _edit_float("_cdf_xmax_edit", 300.0))
    axes = LineAxes(ax_mean, ax_frac, ax_n, ax_cdf)
    _full_fidelity = bool(getattr(ctx.state.plot, "export_full_fidelity", False))
    render_line_series(
        axes, series_list,
        fov_active=_fov_active, fc_active=bool(fc_vs_ctrl or fc_vs_t0),
        threshold=threshold, legend_kw=legend_kw,
        warn_color=warn, muted_fg=_muted_fg,
        legend_visible=ctx.state.plot.legend_visible,
        show_threshold_line=getattr(ctx.state.plot, "show_threshold_line", False),
        cdf_xrange=_cdf_xrange,
        empty_cdf_msg=f"No {ctx.state.channel.active_channel.upper()} data found.",
        draggable_legends=True,
        # Screen budgets — dropped while a capture is in flight. The batch
        # exporter renders onto its own axes and never passes them; the tab's
        # own Save menu saves *this* figure object, so it raises
        # ``export_full_fidelity`` and repaints before capturing. A figure read
        # at 300 dpi has room for every CDF vertex and every trace name, and a
        # silently thinned export is a degraded artifact that looks exactly like
        # a complete one.
        cdf_point_budget=None if _full_fidelity else SCREEN_CDF_POINTS,
        legend_max=None if _full_fidelity else SCREEN_LEGEND_ENTRIES,
        # Properties → Overlays. Resolved above so the Fraction panel's clamp
        # and the labels themselves cannot disagree about whether they are on.
        value_labels=_value_labels,
        value_label_format=_value_label_fmt,
        # Properties → Colours. Resolved from the same state the Batch Export
        # line figure resolves it from, so the two cannot disagree.
        color_mode=line_color_mode(ctx.state.plot),
    )

    ctx.notify.set_status(line_status_text(
        ctx, series_list, fc_misses, active_rsets, selected))

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

