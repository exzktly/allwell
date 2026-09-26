"""Bar-plot data collection (no rendering).

The pure data-gathering half of the bar-plot feature: walk the loaded wells /
replicate sets, apply fold-change scaling + the cell-gating thresholds, and
return :class:`BarItem` rows. Mirrors ``scatter/collect.py`` — split out of
``barplot/controller.py`` (T4) so the controller keeps state/interaction wiring
while this module owns collection. ``barplot/controller`` and the CSV exporters
re-export these names.
"""

from __future__ import annotations

from review.data.metric_labels import spread_label

from review.domain.plot_members import plot_replicate_sets

from review.domain.selections_model import (
    LABEL_FROM_CUSTOM,
    color_for_replicate_set,
    color_for_well,
    replicate_display_label,
    well_display_label,
    well_display_label_and_source,
)
from review.core.channel_state_controller import get_all_fluor_gates, on_cutoff_for_column
from review.data.data_loading import extract_well_token as _extract_well_token
from review.data.data_loading import get_rows
from domain.text_columns import fov_text
from review.domain.viewer_state import parse_rc

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass

# Module scope, not function-local: ``_decorate`` imports nothing from here,
# so there is no cycle to work around — and a deferred feature import that is
# not breaking a cycle is one the budget guard counts for nothing.
from review.features.barplot._decorate import decorate_bar_items
from review.domain.batch_models import BarGroup, ReplicateSet
from review.domain import fold_change as _fc
from review.domain.fold_change_series_cache import (
    ControlSeriesCache as _ControlSeriesCache,
)
from review.domain.fold_change_pairing import (
    control_denominators,
    panel_nan_flags,
)
from review.domain import plot_members as _members
from review.domain.time_rescale import format_scaled as _rescale_tp
from review.domain.time_rescale import rescale_for as _time_rescale_for
from review.domain.stats_compute import (
    aggregate_group,
    aggregate_well_cached,
    compute_rep_n_above_stats,
    compute_rep_n_above_total,
    compute_rep_per_fov_stats,
    compute_rep_stats,
    replicate_unit,
)
from core import debug_flags as debug_flags
from domain.tp_match import tp_close
from review.domain.gating_state import active_fluor_gates

_logger = logging.getLogger(__name__)


@dataclass
class BarItem:
    """One bar in the bar-plot view, post-normalization.

    A single shape consumed by every downstream renderer (on-screen,
    off-screen export, CSV emitter). Replaces the loose-tuple-with-
    backwards-compat-positional-unpacking pattern that grew during the
    fold-change PR.

    Fields:
      * ``key``       — drag-order identity. Replicate-set name for
        grouped mode, well token ("A01") for per-well mode.
      * ``display``   — x-tick label shown on the chart.
      * ``color``     — bar fill colour, already resolved per item
        (rank colour for rep-set, well-rank colour for per-well).
      * ``mean`` / ``spread`` — post-normalization values. NaN /
        ``has_mean=False`` means render as the dashed placeholder.
      * ``frac`` / ``frac_spread`` / ``has_frac`` — fraction-above-
        threshold panel. Fold-changed independently of ``mean`` when
        fold-change is on (FC denominator is the control / baseline's
        frac — frac and mean are independent quantities).
      * ``n_above`` / ``n_above_spread`` — events-above-threshold panel,
        the value actually drawn. For a replicate set it is the mean of
        the per-well counts with the cross-well spread; per-FOV mode
        substitutes the per-FOV mean and spread. For a single well it is
        that well's own count.
      * ``n_above_total`` — the cross-well **total**, for the CSV's
        ``events_total`` column, which has always been an integer
        count. ``None`` for a row that is not a replicate aggregation,
        never ``0`` — nothing computed is not the same as counted zero.
      * ``fc_control`` — the control reference **this bar** was divided
        by, or ``None``. Under a single global control every bar carries
        the same one; under per-sample controls they differ, and the
        x-tick's second line, the CSV's ``fold_change_control`` column
        and the figure's annotations all read this rather than
        re-deriving it.
      * ``fc_outcome`` — a ``ControlOutcome`` value saying *why* this bar
        is what it is, or ``""`` when the control axis is off. It is the
        only thing separating "nobody assigned a control" from "the
        control is gone" from "the control has no data at this
        timepoint", all three of which otherwise produce the same empty
        bar and the same empty CSV cell.
    """
    key: str
    display: str
    color: str
    mean: float
    spread: float
    has_mean: bool
    frac: float
    frac_spread: float
    has_frac: bool
    n_above: float
    n_above_spread: float
    n_above_total: int | None = None
    fc_control: object | None = None
    fc_outcome: str = ""
    # Multi-timepoint plotting. ``tp`` is the timepoint this item was
    # computed at; ``group_key`` is the cluster identity the renderer uses
    # to lay out adjacent x positions (well/rset name in "Group by well"
    # mode, formatted tp string in "Group by timepoint" mode, or "" when
    # this item is itself a pooled column).
    tp: float = 0.0
    group_key: str = ""
    # The second band level, inside ``group_key``: set only by the *Then by*
    # decoration (``_decorate.decorate_bar_items``), "" otherwise. A sub-band
    # is identified by the ``(group_key, subgroup_key)`` pair, never by
    # ``subgroup_key`` alone — the same dose sits inside every cell line.
    subgroup_key: str = ""
    # The timepoint group this bar belongs to: with more than one timepoint
    # selected, the collector's own cluster identity (the sample under "Group
    # by well", the timepoint under "Group by timepoint"), "" otherwise. Set
    # once by :func:`mark_timepoint_runs` and never touched by the decorator,
    # so it survives *Split by* replacing ``group_key``. Adjacent bars sharing
    # it are drawn :attr:`BarGeometry.group_spacing` apart rather than a full
    # slot, so the group reads as one.
    run_key: str = ""


#: The gap between the bars of one timepoint group, as a fraction of the bar
#: width, when the Properties → Bars setting has not been touched.
DEFAULT_GROUP_SPACING = 0.2


@dataclass(frozen=True)
class BarGeometry:
    """The Properties → Bars settings, resolved for one render.

    *width* is a bar's width as a fraction of one x slot, or ``0.0`` for the
    automatic width (:func:`bar_width_for`). *group_spacing* is the gap between
    the bars of one timepoint group as a fraction of the bar width. A value
    object rather than two loose arguments because the tab renderer and Batch
    Export must lay out the same figure the same way, and Batch Export renders
    with ``ctx=None`` — so it resolves these once, from the same state, and
    hands them over, exactly as it does the colour mode.
    """
    width: float = 0.0
    group_spacing: float = DEFAULT_GROUP_SPACING


def bar_geometry_settings(ctx) -> BarGeometry:
    """The Bars settings from *ctx*'s plot state, or the defaults without one."""
    plot = getattr(getattr(ctx, "state", None), "plot", None)
    if plot is None:
        return BarGeometry()
    return BarGeometry(
        width=float(getattr(plot, "bar_width", 0.0) or 0.0),
        group_spacing=float(getattr(plot, "bar_group_spacing",
                                    DEFAULT_GROUP_SPACING)),
    )


def bar_width_for(n: int, use_groups: bool, geometry: BarGeometry) -> float:
    """The drawn bar width: the user's, or the automatic one for *n* bars.

    Automatic narrows as bars multiply (five slots' worth shared out) so a
    crowded figure does not read as a solid block, capped a little wider for
    pooled groups than for single wells. A user width is clamped to one slot:
    wider and neighbouring bars would overlap, which draws values on top of
    each other rather than beside each other.
    """
    if geometry.width > 0:
        return min(1.0, max(0.05, float(geometry.width)))
    return min(0.65 if use_groups else 0.6, 5.0 / max(n, 1))


def mark_timepoint_runs(items, multi_tp: bool) -> None:
    """Record each bar's timepoint group, in place, before decoration.

    One pass over the finished list for the reason ``_decorate`` is one pass:
    the collectors build items in several branches, and a rule applied in some
    of them draws a figure where some groups are tight and some are not.
    """
    for item in items:
        item.run_key = (item.group_key or "") if multi_tp else ""


def bar_layout(items, use_groups: bool, geometry: BarGeometry):
    """``(bar width, x positions)`` for a finished, decorated item list."""
    bar_w = bar_width_for(len(items), use_groups, geometry)
    return bar_w, clustered_x_positions(
        items, bar_w=bar_w, group_spacing=geometry.group_spacing)


def _bar_debug(msg: str) -> None:
    """Emit opt-in bar-plot diagnostics for draw-time label debugging."""
    if debug_flags.review_bar_debug_enabled():
        _logger.debug("%s", msg)


def _fc_scale(value, err, *, eff_ctrl, eff_ctrl_spread, t0, t0_spread,
              fc_vs_ctrl, fc_vs_t0, fc_op_ctrl, fc_op_t0, fc_order):
    """One axis' fold-change ``scale_bar_value`` call (audit A12).

    The mean / fraction / N triplet applied this exact call three times in each
    of two code paths (6× paste); the only per-axis difference is which
    ``(value, err)`` and resolved ``eff_ctrl`` / ``t0`` baselines feed in, so
    they become arguments. Returns the scaled ``(value, err)``.
    """
    return _fc.scale_bar_value(
        value, err,
        control_mean=eff_ctrl if fc_vs_ctrl else None,
        control_spread=eff_ctrl_spread if fc_vs_ctrl else None,
        t0_mean=t0 if fc_vs_t0 else None,
        t0_spread=t0_spread if fc_vs_t0 else None,
        op_ctrl=fc_op_ctrl, op_t0=fc_op_t0,
        order=fc_order,
    )


def _scale_group_bar(mean_pair, frac_pair, n_pair, *, pts, ctrl, outcome,
                     fc_vs_ctrl, fc_vs_t0, baseline_tp, per_fov_spread,
                     fc_op_ctrl, fc_op_t0, fc_order, baseline_ctrl):
    """Fold-change one batch-group bar's three panels.

    The batch counterpart of what ``_items_for_tp`` does on the tab, kept out
    of line because it is the only part of that loop with any decisions in it.
    *ctrl* is the ``(mean, frac, n)`` denominator triple ``ControlDenominators``
    returned for this member; each element is a ``(value, spread)`` pair or
    ``None``.

    Every baseline comes from the member's **own** points. By name it could
    not: the bar path does not enter ``_app_group_scope``, so a group's
    replicate set is not in the app's selections while this runs.

    *fc_op_ctrl* / *fc_op_t0* / *fc_order* are the user's operator and stacking
    choices, and *baseline_ctrl* is a ``tp -> (mean, frac, n)`` lookup of the
    control at a baseline timepoint — the extra input :func:`_stack_axes`
    needs. All four exist because without them this figure silently took the
    ``ratio`` / ``ctrl_first`` defaults: a plate exported under **subtract**,
    or with both axes on, carried numbers that did not match the tab it was
    exported from, on every bar.

    Returns ``(mean, spread, frac, frac_spread, n_above, n_above_spread)``.
    """
    ctrl_stats, ctrl_frac, ctrl_n = ctrl
    eff_mean, eff_mean_s = ctrl_stats or (None, None)
    eff_frac, eff_frac_s = ctrl_frac or (None, None)
    eff_n, eff_n_s = ctrl_n or (None, None)
    nan_mean, nan_frac, nan_n = panel_nan_flags(
        fc_vs_ctrl, outcome, eff_mean, eff_frac, eff_n)

    if nan_mean:
        # The tab's collector NaNs a bar whose control did not resolve; this
        # one used to hand ``scale_bar_value`` a None denominator, which
        # returns the value untouched — a raw number on a fold-change axis, in
        # an exported figure nobody is watching being drawn.
        return (float("nan"), 0.0, float("nan"), 0.0, float("nan"), 0.0)
    if not (fc_vs_ctrl or fc_vs_t0):
        return (*mean_pair, *frac_pair, *n_pair)

    t0_mean = t0_spread = t0_frac = t0_frac_spread = t0_n = t0_n_spread = None
    if fc_vs_t0:
        _stack_kw = dict(fc_order=fc_order, fc_op_ctrl=fc_op_ctrl,
                         fc_op_t0=fc_op_t0)
        # The operation decides what a usable baseline is: subtracting zero is
        # well defined, dividing by it is not. Passing it means the three
        # panels resolve the *same* baseline timepoint in subtract mode instead
        # of the mean panel using t0 and the fraction and events panels
        # silently falling through to a later one.
        rtp_m, t0_mean, t0_spread = _fc.baseline_from_points_resolved(
            pts, baseline_tp=baseline_tp, op=fc_op_t0)
        rtp_f, t0_frac, t0_frac_spread = _fc.baseline_frac_from_points_resolved(
            pts, baseline_tp=baseline_tp, op=fc_op_t0)
        rtp_n, t0_n, t0_n_spread = _fc.baseline_n_from_points_resolved(
            pts, per_fov_spread=per_fov_spread, baseline_tp=baseline_tp,
            op=fc_op_t0)
        # Cross-axis stacking, one lookup of the control per resolved baseline
        # timepoint. Each panel resolves its own baseline tp, exactly as the
        # tab's three ``_member_baseline`` calls do.
        if fc_vs_ctrl:
            eff_mean, eff_mean_s, t0_mean, t0_spread = _stack_axes(
                baseline_ctrl(rtp_m)[0], eff=eff_mean, eff_spread=eff_mean_s,
                t0=t0_mean, t0_spread=t0_spread, **_stack_kw)
            eff_frac, eff_frac_s, t0_frac, t0_frac_spread = _stack_axes(
                baseline_ctrl(rtp_f)[1], eff=eff_frac, eff_spread=eff_frac_s,
                t0=t0_frac, t0_spread=t0_frac_spread, **_stack_kw)
            eff_n, eff_n_s, t0_n, t0_n_spread = _stack_axes(
                baseline_ctrl(rtp_n)[2], eff=eff_n, eff_spread=eff_n_s,
                t0=t0_n, t0_spread=t0_n_spread, **_stack_kw)

    _fc_kw = dict(fc_vs_ctrl=fc_vs_ctrl, fc_vs_t0=fc_vs_t0,
                  fc_op_ctrl=fc_op_ctrl, fc_op_t0=fc_op_t0, fc_order=fc_order)
    m, s = _fc_scale(*mean_pair, eff_ctrl=eff_mean, eff_ctrl_spread=eff_mean_s,
                     t0=t0_mean, t0_spread=t0_spread, **_fc_kw)
    f, fs = _fc_scale(*frac_pair, eff_ctrl=eff_frac, eff_ctrl_spread=eff_frac_s,
                      t0=t0_frac, t0_spread=t0_frac_spread, **_fc_kw)
    n, ns = _fc_scale(*n_pair, eff_ctrl=eff_n, eff_ctrl_spread=eff_n_s,
                      t0=t0_n, t0_spread=t0_n_spread, **_fc_kw)
    # Each panel drops on its own denominator, as on the tab.
    if nan_frac:
        f, fs = float("nan"), 0.0
    if nan_n:
        n, ns = float("nan"), 0.0
    return (m, s, f, fs, n, ns)


def _stack_axes(ctrl_at_baseline, *, eff, eff_spread, t0, t0_spread,
                fc_order, fc_op_ctrl, fc_op_t0):
    """Compose the two fold-change axes onto one another's denominator.

    With both axes active one of them has to be applied to the other's
    denominator, and which one is what ``fc_order`` selects: ``ctrl_first``
    turns the baseline into a post-control baseline, ``t0_first`` turns the
    control into a post-t0 control. *ctrl_at_baseline* is the control's
    ``(mean, spread)`` at the **baseline** timepoint, which is the only extra
    input either direction needs.

    This shape appeared six times — mean, fraction and events, in each of the
    grouped and per-well branches — with the three copies in one branch
    carrying a ``None`` guard the others lacked. Returns
    ``(eff, eff_spread, t0, t0_spread)`` with exactly one of the two pairs
    changed.
    """
    if ctrl_at_baseline is None:
        return eff, eff_spread, t0, t0_spread
    ctrl_mean, ctrl_spread = ctrl_at_baseline
    if fc_order == "t0_first":
        if eff is None:
            return eff, eff_spread, t0, t0_spread
        eff, eff_spread = _fc._apply_axis(
            eff, eff_spread or 0.0, ctrl_mean, ctrl_spread, fc_op_t0)
        return eff, eff_spread, t0, t0_spread
    if t0 is None:
        return eff, eff_spread, t0, t0_spread
    t0, t0_spread = _fc._apply_axis(
        t0, t0_spread, ctrl_mean, ctrl_spread, fc_op_ctrl)
    return eff, eff_spread, t0, t0_spread


# Re-exported from the domain layer so the many callers that import it from
# here (barplot.controller, export.service, batch_export.bar_panel) keep their
# import path. A frozen ``FoldChangeState``, read by name only —
# ``tests/test_fc_state_is_not_unpacked.py`` fails on any site that unpacks it,
# because a call site that drops a newly added field by unpacking three values
# draws raw numbers under a fold-change axis.
FC_STATE_OFF = _fc.FC_STATE_OFF


def collect_bar_items(
    ctx, target_t, *,
    fc_state=None,
    fc_baseline_tp: float | None = None,
    miss_sink: set | None = None,
) -> tuple[bool, list[BarItem], str]:
    """Compute the bar-plot ``BarItem``s for the active mode (rep-set or per-well).

    Single source of truth — every consumer of bar data (on-screen renderer,
    off-screen figure exporter, CSV emitter) receives the same ``BarItem``
    list in the same order. Fold-change normalization is applied exactly
    once, here.

    *target_t* — either a single ``float`` (legacy single-timepoint call) or
    a ``list[float]`` of timepoints. Multi-tp callers (the on-screen
    renderer driven by ``BarPlotState.tp_selections``) get one ``BarItem``
    per (key, tp) pair; the emission order respects ``bar_group_mode``
    ("well": well-major; "timepoint": tp-major). The fold-change resolver
    runs once per tp.

    When *fc_state* is None (default) the ctx's current state is used.
    Callers that want raw values can pass :data:`FC_STATE_OFF` — the
    aggregation helpers are cached so a second call is cheap, which is how
    the on-tab CSV emits the additive (raw + fold-change) schema.

    *miss_sink* — optional set the caller passes in; the function adds the
    affected timepoint to it when fold-change-vs-control is requested but
    the control has no sample there.

    Returns ``(use_groups, items, band_lbl)``. ``use_groups`` is True when
    replicate sets are active *and* the pool-replicates toggle is on (drives
    a slightly wider bar style in the renderer); the items themselves carry
    display labels and colours so callers don't need to re-resolve them.
    """
    use_sem = ctx.state.plot.use_sem
    band_lbl = spread_label(use_sem)
    if isinstance(target_t, (list, tuple)):
        target_ts = [float(t) for t in target_t]
    else:
        target_ts = [float(target_t)]
    if not target_ts:
        return False, [], band_lbl
    # Which ON cutoff applies is a property of the value column, not of this
    # tab — shape/morphology keeps every cell, spot counts read ThreshCountOn,
    # intensities read ThreshFracOn.
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)
    active_rsets = plot_replicate_sets(ctx)
    pool_reps = bool(getattr(ctx.state.plot, "bar_pool_replicates", True))
    # ``use_groups`` here drives the renderer's bar-width preset (wider for
    # grouped mode); it stays True only when wells are actually being
    # collapsed into rset columns.
    use_groups = bool(active_rsets) and pool_reps
    group_mode = getattr(ctx.state.plot, "bar_group_mode", "well")

    if fc_state is None:
        fc_state = _fc.fold_change_state(ctx)
    fc = _fc.coerce_fc_state(fc_state)
    fc_vs_ctrl = fc.vs_control_on
    fc_vs_t0 = fc.vs_t0_on
    if fc_baseline_tp is None:
        fc_baseline_tp = _fc.parse_baseline_tp(ctx)
    fc_op_ctrl = _fc.parse_fc_op_ctrl(ctx)
    fc_op_t0 = _fc.parse_fc_op_t0(ctx)
    fc_order = _fc.parse_fc_order(ctx)
    _stack_kw = dict(fc_order=fc_order, fc_op_ctrl=fc_op_ctrl, fc_op_t0=fc_op_t0)
    fluor_gates = get_all_fluor_gates(ctx)
    per_fov_spread = ctx.state.plot.use_fov_replicates

    # One memo for every control denominator this redraw asks for. The vs-t0
    # stacking blocks below resolve a control inside the member loop, so
    # without it a 24-member plate rebuilt the control's whole series 882
    # times; with it, three times per distinct control.
    ctrl_cache = _ControlSeriesCache(
        ctx, threshold=threshold, val_col=ctx.state.channel.active_val_col,
        use_sem=use_sem, per_fov_spread=per_fov_spread, fluor_gates=fluor_gates,
    )

    # A member's own baseline does not depend on the timepoint being drawn —
    # ``member_first_tp_*_resolved`` takes no target tp — but it is asked for
    # inside the per-tp loop, so a rep-set's whole series was rebuilt once per
    # member per timepoint per axis. Memoised on the arguments that do vary.
    _baseline_memo: dict = {}

    def _member_baseline(fn, label: str, per_fov: bool):
        """One ``member_first_tp_*_resolved`` call, memoised for this redraw."""
        key = (fn.__name__, label, bool(per_fov))
        if key not in _baseline_memo:
            _baseline_memo[key] = fn(
                ctx, label,
                threshold=threshold, val_col=ctx.state.channel.active_val_col,
                use_sem=use_sem, per_fov_spread=per_fov,
                fluor_gates=fluor_gates, baseline_tp=fc_baseline_tp,
            )
        return _baseline_memo[key]

    # Per-member pairing, resolved once for the whole redraw. The member keys
    # come from ``domain/plot_members.py`` rather than being re-derived here,
    # which is what keeps the editor's rows and this loop's normalization from
    # describing two different plots; ``tests/test_plot_members.py`` pins the
    # two key lists equal.
    denoms = control_denominators(
        ctx, ctrl_cache, fc,
        _members.bar_member_keys(ctx) if fc.per_member else (),
        scope_wells=_members.member_wells(ctx, "bar") if fc.per_member else None,
        member_rsets=_members.member_replicate_sets(ctx) if fc.per_member else None,
        miss_sink=miss_sink,
    )
    hidden_members = denoms.hidden_members

    ordered_keys = ctx.runtime.barplot.current_keys()
    rset_by_name = {r.name: r for r in active_rsets}

    def _format_tp(t: float) -> str:
        # Label only. ``BarItem.key`` keeps the unscaled ``t{t:.4g}`` form,
        # because that is the bar's identity — drag order, the x-position map
        # and the significance-bracket matching all key on it, and none of them
        # should move when the user changes how the axis is *printed*.
        return _rescale_tp(t, _time_rescale_for(ctx.state.plot))

    def _items_for_tp(target_t: float) -> list[BarItem]:
        """Build one ``BarItem`` per bar key at this timepoint.

        Fold-change resolution runs once here per tp so the control / t0
        denominators are not re-aggregated for every bar.
        """
        out: list[BarItem] = []
        tp_str = _format_tp(target_t)

        if use_groups:
            # Grouped mode: one BarItem per rset, wells inside each rset
            # collapsed via compute_rep_stats / compute_rep_per_fov_stats.
            multi_tp = len(target_ts) > 1
            for key in ordered_keys:
                rset = rset_by_name.get(key)
                if rset is None:
                    continue
                if key in hidden_members:
                    continue
                (fc_ctrl_ref, fc_outcome, ctrl_stats_t, ctrl_frac_t,
                 ctrl_n_t) = denoms.at(key, target_t)
                color = color_for_replicate_set(rset, ctx.state.selection.selections)
                rset_disp = replicate_display_label(ctx, rset)
                # In multi-tp group-by-well mode the cluster header is the
                # rset name; per-bar label collapses to the timepoint so
                # the tick row stays single-line.
                if multi_tp and group_mode == "well":
                    display = f"t={tp_str}"
                else:
                    display = rset_disp
                # Unified replicate rule (shared with the Statistics tab): with FOV
                # mode on, FOVs are the replicates (pooled across the set's wells);
                # otherwise each well is a replicate. Either way the spread matches
                # the stats test's n.
                n_loaded = sum(1 for w in rset.wells
                               if w in ctx.state.dataset.well_paths)
                rset_per_fov = replicate_unit(n_loaded, per_fov_spread) == "fov"
                if rset_per_fov:
                    gm, g_err_m, gf, g_err_f, n_above_pf_mean, n_above_pf_spread = (
                        compute_rep_per_fov_stats(ctx, rset, target_t, threshold, use_sem)
                    )
                    n_above_val = float(n_above_pf_mean)
                    n_above_err = float(n_above_pf_spread)
                else:
                    gm, g_err_m, gf, g_err_f = compute_rep_stats(ctx, rset, target_t, threshold, use_sem)
                    # Events panel: per-well mean ± cross-well spread (replicate
                    # error bars), consistent with the Mean/Fraction panels —
                    # rather than a spread-less total.
                    n_above_val, n_above_err = compute_rep_n_above_stats(
                        ctx, rset, target_t, use_sem, threshold)
                # The CSV's integer column keeps meaning the cross-well total,
                # which is a different number from the mean drawn above.
                n_above_total = compute_rep_n_above_total(ctx, rset, target_t, threshold)
                t0_mean = None
                t0_spread = None
                t0_frac = None
                t0_frac_spread = None
                t0_n = None
                t0_n_spread = None
                # Per-rset effective control denominator. ``ctrl_*_t`` are this
                # member's raw ctrl@t — in t0_first mode we replace them
                # locally with the post-t0 control without leaking into the
                # next rset.
                eff_ctrl_mean, eff_ctrl_spread = ctrl_stats_t or (None, None)
                eff_ctrl_frac, eff_ctrl_frac_spread = ctrl_frac_t or (None, None)
                eff_ctrl_n, eff_ctrl_n_spread = ctrl_n_t or (None, None)
                fc_force_nan, fc_nan_frac, fc_nan_n = panel_nan_flags(
                    fc_vs_ctrl, fc_outcome,
                    eff_ctrl_mean, eff_ctrl_frac, eff_ctrl_n)
                # Guarded on the control having resolved, mirroring the
                # per-well branch below. Without it the stacking runs first and
                # divides a None control by the baseline.
                if fc_vs_t0 and not fc_force_nan:
                    stats = _member_baseline(
                        _fc.member_first_tp_stats_resolved, rset.name, rset_per_fov)
                    if stats is not None:
                        resolved_tp, t0_mean, t0_spread = stats
                        # Cross-axis stacking when both axes are active.
                        # ctrl_first: t0 denom becomes post-control baseline.
                        # t0_first:   control denom becomes post-t0 control.
                        if fc_vs_ctrl and fc_ctrl_ref is not None:
                            (eff_ctrl_mean, eff_ctrl_spread,
                             t0_mean, t0_spread) = _stack_axes(
                                ctrl_cache.stats_at(fc_ctrl_ref, resolved_tp),
                                eff=eff_ctrl_mean, eff_spread=eff_ctrl_spread,
                                t0=t0_mean, t0_spread=t0_spread, **_stack_kw)
                    fstats = _member_baseline(
                        _fc.member_first_tp_frac_resolved, rset.name, per_fov_spread)
                    if fstats is not None:
                        resolved_tp_f, t0_frac, t0_frac_spread = fstats
                        if fc_vs_ctrl and fc_ctrl_ref is not None:
                            (eff_ctrl_frac, eff_ctrl_frac_spread,
                             t0_frac, t0_frac_spread) = _stack_axes(
                                ctrl_cache.frac_at(fc_ctrl_ref, resolved_tp_f),
                                eff=eff_ctrl_frac, eff_spread=eff_ctrl_frac_spread,
                                t0=t0_frac, t0_spread=t0_frac_spread, **_stack_kw)
                    nstats = _member_baseline(
                        _fc.member_first_tp_n_resolved, rset.name, per_fov_spread)
                    if nstats is not None:
                        resolved_tp_n, t0_n, t0_n_spread = nstats
                        if fc_vs_ctrl and fc_ctrl_ref is not None:
                            (eff_ctrl_n, eff_ctrl_n_spread,
                             t0_n, t0_n_spread) = _stack_axes(
                                ctrl_cache.n_at(fc_ctrl_ref, resolved_tp_n),
                                eff=eff_ctrl_n, eff_spread=eff_ctrl_n_spread,
                                t0=t0_n, t0_spread=t0_n_spread, **_stack_kw)
                if fc_force_nan:
                    gm = float("nan")
                    g_err_m = 0.0
                    gf = float("nan")
                    g_err_f = 0.0
                    n_above_val = float("nan")
                    n_above_err = 0.0
                elif fc_vs_ctrl or fc_vs_t0:
                    _fc_kw = dict(fc_vs_ctrl=fc_vs_ctrl, fc_vs_t0=fc_vs_t0,
                                  fc_op_ctrl=fc_op_ctrl, fc_op_t0=fc_op_t0,
                                  fc_order=fc_order)
                    gm, g_err_m = _fc_scale(
                        gm, g_err_m, eff_ctrl=eff_ctrl_mean,
                        eff_ctrl_spread=eff_ctrl_spread, t0=t0_mean,
                        t0_spread=t0_spread, **_fc_kw)
                    gf, g_err_f = _fc_scale(
                        gf, g_err_f, eff_ctrl=eff_ctrl_frac,
                        eff_ctrl_spread=eff_ctrl_frac_spread, t0=t0_frac,
                        t0_spread=t0_frac_spread, **_fc_kw)
                    n_above_val, n_above_err = _fc_scale(
                        n_above_val, n_above_err, eff_ctrl=eff_ctrl_n,
                        eff_ctrl_spread=eff_ctrl_n_spread, t0=t0_n,
                        t0_spread=t0_n_spread, **_fc_kw)
                    if fc_nan_frac:
                        gf, g_err_f = float("nan"), 0.0
                    if fc_nan_n:
                        n_above_val, n_above_err = float("nan"), 0.0
                has_mean = not math.isnan(gm)
                # Not coupled to ``has_mean``. The two are answers to different
                # questions: the mean is the average intensity *of the cells
                # above threshold*, undefined when there are none, while the
                # fraction is ``n_above / n_total``, and a set where nothing
                # passed the cut has a fraction of exactly 0 — a measurement,
                # and usually the point of the experiment. Coupling them drew
                # the "could not be computed" placeholder over that zero in
                # pooled mode while the per-well branch below (which does not
                # couple them) drew the honest 0, so the same set read two ways
                # depending on a layout toggle.
                has_frac = not math.isnan(gf)
                gk = rset_disp if group_mode == "well" else tp_str
                out.append(BarItem(
                    key=rset.name, display=display, color=color,
                    mean=gm, spread=g_err_m, has_mean=has_mean,
                    frac=gf, frac_spread=g_err_f, has_frac=has_frac,
                    n_above=n_above_val, n_above_spread=n_above_err,
                    n_above_total=n_above_total,
                    fc_control=fc_ctrl_ref, fc_outcome=str(fc_outcome or ""),
                    tp=float(target_t), group_key=gk,
                ))
            return out

        # Per-well branch: either no rsets are active, or
        # ``bar_pool_replicates`` is False and rsets exist (wells are
        # iterated explicitly so the rset colour is preserved).
        multi_tp = len(target_ts) > 1
        if active_rsets and not pool_reps:
            iter_specs = []  # (well_label, color, display, group_key)
            seen: set = set()
            for rset in active_rsets:
                rcolor = color_for_replicate_set(rset, ctx.state.selection.selections)
                for w in rset.wells:
                    if w in seen:
                        continue
                    if w not in ctx.state.dataset.well_paths:
                        continue
                    seen.add(w)
                    well_disp = ctx.runtime.barplot.well_display_label(w)
                    if multi_tp and group_mode == "well":
                        # cluster header shows well + rset; per-bar label
                        # shows just the timepoint.
                        disp = f"t={tp_str}"
                    elif multi_tp and group_mode == "timepoint":
                        # cluster header shows timepoint; per-bar label
                        # shows well + rset name (single line).
                        disp = f"{well_disp} [{rset.name}]"
                    else:
                        disp = f"{well_disp} [{rset.name}]"
                    gk_well = f"{well_disp} [{rset.name}]"
                    gk = gk_well if group_mode == "well" else tp_str
                    iter_specs.append((w, rcolor, disp, gk))
            label_iter = iter_specs
        else:
            label_iter = []
            for label in ordered_keys:
                if label not in ctx.state.dataset.well_paths:
                    continue
                color = color_for_well(label)
                well_disp = ctx.runtime.barplot.well_display_label(label)
                if multi_tp and group_mode == "well":
                    display = f"t={tp_str}"
                else:
                    display = well_disp
                gk = well_disp if group_mode == "well" else tp_str
                label_iter.append((label, color, display, gk))

        for label, color, display, gk in label_iter:
            if label in hidden_members:
                continue
            (fc_ctrl_ref, fc_outcome, ctrl_stats_t, ctrl_frac_t,
             ctrl_n_t) = denoms.at(label, target_t)
            eff_ctrl_mean_t, eff_ctrl_spread_t = ctrl_stats_t or (None, None)
            eff_ctrl_frac_t, eff_ctrl_frac_spread_t = ctrl_frac_t or (None, None)
            eff_ctrl_n_t, eff_ctrl_n_spread_t = ctrl_n_t or (None, None)
            fc_force_nan, fc_nan_frac, fc_nan_n = panel_nan_flags(
                fc_vs_ctrl, fc_outcome,
                eff_ctrl_mean_t, eff_ctrl_frac_t, eff_ctrl_n_t)
            pts = aggregate_well_cached(ctx,
                label, threshold=threshold, use_sem=use_sem,
                val_col=ctx.state.channel.active_val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            matched = [pt for pt in pts if tp_close(pt[0], target_t)]
            if matched:
                pt = matched[0]
                m, s, f = pt[1], pt[2], pt[3]
                # ``n_above_total`` stays ``None`` on this branch: it means "the
                # cross-well total behind a replicate aggregation", and one well
                # is not an aggregation of anything. Setting it here filled the
                # CSV's replicate columns for a solo well — a unit of ``well``
                # beside a spread of ``0.000000``, which reads as "the
                # replicates agreed" for a row that had no replicates — and made
                # the integer total win over the per-FOV mean in
                # ``events_total``. The count itself is still what this
                # bar draws with Aggregate-FOVs off.
                n_above_total = None
                own_count = float(pt[4])
                fs = float(pt[6]) if len(pt) >= 7 else 0.0
                n_above_pf_mean = float(pt[7]) if len(pt) >= 8 else 0.0
                n_above_pf_spread = float(pt[8]) if len(pt) >= 9 else 0.0
                if per_fov_spread:
                    n_above_val = n_above_pf_mean
                    n_above_err = n_above_pf_spread
                else:
                    n_above_val = own_count
                    n_above_err = 0.0
                if fc_force_nan:
                    m = float("nan")
                    s = 0.0
                    f = float("nan")
                    fs = 0.0
                    n_above_val = float("nan")
                    n_above_err = 0.0
                elif fc_vs_ctrl or fc_vs_t0:
                    t0_mean = None
                    t0_spread = None
                    t0_frac = None
                    t0_frac_spread = None
                    t0_n = None
                    t0_n_spread = None
                    eff_ctrl_mean = eff_ctrl_mean_t
                    eff_ctrl_spread = eff_ctrl_spread_t
                    eff_ctrl_frac = eff_ctrl_frac_t
                    eff_ctrl_frac_spread = eff_ctrl_frac_spread_t
                    eff_ctrl_n = eff_ctrl_n_t
                    eff_ctrl_n_spread = eff_ctrl_n_spread_t
                    if fc_vs_t0:
                        resolved_tp, t0_mean, t0_spread = _fc.baseline_from_points_resolved(
                            pts, baseline_tp=fc_baseline_tp, op=fc_op_t0,
                        )
                        resolved_tp_f, t0_frac, t0_frac_spread = _fc.baseline_frac_from_points_resolved(
                            pts, baseline_tp=fc_baseline_tp, op=fc_op_t0,
                        )
                        resolved_tp_n, t0_n, t0_n_spread = _fc.baseline_n_from_points_resolved(
                            pts, per_fov_spread=per_fov_spread, baseline_tp=fc_baseline_tp,
                            op=fc_op_t0,
                        )
                        # Cross-axis stacking: ctrl_first → adjust t0 denom; t0_first → adjust control denom.
                        if fc_vs_ctrl and fc_ctrl_ref is not None and resolved_tp is not None and t0_mean is not None:
                            (eff_ctrl_mean, eff_ctrl_spread,
                             t0_mean, t0_spread) = _stack_axes(
                                ctrl_cache.stats_at(fc_ctrl_ref, resolved_tp),
                                eff=eff_ctrl_mean, eff_spread=eff_ctrl_spread,
                                t0=t0_mean, t0_spread=t0_spread, **_stack_kw)
                        if fc_vs_ctrl and fc_ctrl_ref is not None and resolved_tp_f is not None and t0_frac is not None:
                            (eff_ctrl_frac, eff_ctrl_frac_spread,
                             t0_frac, t0_frac_spread) = _stack_axes(
                                ctrl_cache.frac_at(fc_ctrl_ref, resolved_tp_f),
                                eff=eff_ctrl_frac, eff_spread=eff_ctrl_frac_spread,
                                t0=t0_frac, t0_spread=t0_frac_spread, **_stack_kw)
                        if fc_vs_ctrl and fc_ctrl_ref is not None and resolved_tp_n is not None and t0_n is not None:
                            (eff_ctrl_n, eff_ctrl_n_spread,
                             t0_n, t0_n_spread) = _stack_axes(
                                ctrl_cache.n_at(fc_ctrl_ref, resolved_tp_n),
                                eff=eff_ctrl_n, eff_spread=eff_ctrl_n_spread,
                                t0=t0_n, t0_spread=t0_n_spread, **_stack_kw)
                    _fc_kw = dict(fc_vs_ctrl=fc_vs_ctrl, fc_vs_t0=fc_vs_t0,
                                  fc_op_ctrl=fc_op_ctrl, fc_op_t0=fc_op_t0,
                                  fc_order=fc_order)
                    m, s = _fc_scale(
                        m, s, eff_ctrl=eff_ctrl_mean,
                        eff_ctrl_spread=eff_ctrl_spread, t0=t0_mean,
                        t0_spread=t0_spread, **_fc_kw)
                    f, fs = _fc_scale(
                        f, fs, eff_ctrl=eff_ctrl_frac,
                        eff_ctrl_spread=eff_ctrl_frac_spread, t0=t0_frac,
                        t0_spread=t0_frac_spread, **_fc_kw)
                    n_above_val, n_above_err = _fc_scale(
                        n_above_val, n_above_err, eff_ctrl=eff_ctrl_n,
                        eff_ctrl_spread=eff_ctrl_n_spread, t0=t0_n,
                        t0_spread=t0_n_spread, **_fc_kw)
                    if fc_nan_frac:
                        f, fs = float("nan"), 0.0
                    if fc_nan_n:
                        n_above_val, n_above_err = float("nan"), 0.0
                has_mean = not math.isnan(m)
                has_frac = not math.isnan(f)
                out.append(BarItem(
                    key=label, display=display, color=color,
                    mean=m, spread=s, has_mean=has_mean,
                    frac=f, frac_spread=fs, has_frac=has_frac,
                    n_above=n_above_val, n_above_spread=n_above_err,
                    n_above_total=n_above_total,
                    fc_control=fc_ctrl_ref, fc_outcome=str(fc_outcome or ""),
                    tp=float(target_t), group_key=gk,
                ))
            else:
                out.append(BarItem(
                    key=label, display=display, color=color,
                    mean=float("nan"), spread=0.0, has_mean=False,
                    frac=float("nan"), frac_spread=0.0, has_frac=False,
                    n_above=0.0, n_above_spread=0.0,
                    fc_control=fc_ctrl_ref, fc_outcome=str(fc_outcome or ""),
                    tp=float(target_t), group_key=gk,
                ))
        return out

    items: list[BarItem] = []
    if group_mode == "timepoint":
        # tp-major: emit all bars at t0, then all bars at t1, ...
        for tp in target_ts:
            items.extend(_items_for_tp(tp))
    else:
        # well-major: for each key, emit its bars at all selected tps in
        # order so a single well's cluster ends up contiguous.
        per_tp = [(_items_for_tp(tp), tp) for tp in target_ts]
        # The per-tp lists share the same key order (driven by
        # ``ordered_keys`` + the rset-expansion order). Interleave by key.
        n_keys = len(per_tp[0][0]) if per_tp else 0
        for ki in range(n_keys):
            for items_for_tp, _tp in per_tp:
                if ki < len(items_for_tp):
                    items.append(items_for_tp[ki])
    # Colour by / Split by, once over the finished list rather than in each of
    # the four construction branches above. A sweep that edited the branches
    # would be a sweep with a branch left out, and a figure where some bars
    # honour the control and some do not is worse than one where none does.
    mark_timepoint_runs(items, len(target_ts) > 1)
    items, _notice = decorate_bar_items(ctx, items, group_mode=group_mode)
    return use_groups, items, band_lbl


def batch_members(ctx, group, *, pool_replicates: bool):
    """``(key, display, wells, rset_name_or_None, colour)`` for a batch group.

    Extracted because two callers need the same answer: this collector, and
    ``batch_export/bar_panel``, which labels the Colour-by legend on the figure
    it builds itself. A batch member is a ``BarGroup``'s replicate set or solo
    well — in expanded mode a well of one — so its key space is not the tab's,
    and a legend built against the tab's keys names the members whose names
    happen to coincide and omits the rest. One definition, so the ink and the
    key that explains it cannot be computed two different ways.
    """
    # Build (key, display, wells, rset_name_or_None, color) member list.
    from review.domain.plate_layout import WELL_COLORS as _WELL_COLORS
    members: list[tuple[str, str, list[str], str | None, str]] = []
    if pool_replicates:
        for i, rset in enumerate(group.members):
            valid = [w for w in rset.wells if w in ctx.state.dataset.well_paths]
            if not valid:
                continue
            members.append((rset.name, replicate_display_label(ctx, rset), valid, rset.name,
                            _WELL_COLORS[len(members) % len(_WELL_COLORS)]))
        for w in group.solo_wells:
            if w not in ctx.state.dataset.well_paths:
                continue
            members.append((w, well_display_label(ctx, w), [w], None,
                            _WELL_COLORS[len(members) % len(_WELL_COLORS)]))
    else:
        # Expand rset members into per-well entries; each well coloured by
        # the rset's palette colour so the membership stays visible.
        seen: set = set()
        for ri, rset in enumerate(group.members):
            rcolor = _WELL_COLORS[ri % len(_WELL_COLORS)]
            for w in rset.wells:
                if w in seen or w not in ctx.state.dataset.well_paths:
                    continue
                seen.add(w)
                members.append((w, f"{well_display_label(ctx, w)} [{rset.name}]", [w], rset.name, rcolor))
        for w in group.solo_wells:
            if w in seen or w not in ctx.state.dataset.well_paths:
                continue
            seen.add(w)
            members.append((w, well_display_label(ctx, w), [w], None,
                            _WELL_COLORS[len(members) % len(_WELL_COLORS)]))
    return members


def collect_bar_items_for_group(
    ctx,
    group: BarGroup,
    target_t,
    *,
    val_col: str,
    threshold: float,
    use_sem: bool,
    per_fov_spread: bool,
    fc_state,
    fc_baseline_tp: float | None = None,
    fluor_gates: dict | None = None,
    pool_replicates: bool = True,
    group_mode: str = "well",
) -> list[BarItem]:
    """Build ``BarItem``s for a batch-export ``BarGroup`` (members + solo wells).

    Batch-side analogue of :func:`collect_bar_items` with the same
    multi-tp / pool-replicates / group-mode semantics. ``target_t`` may be
    a single ``float`` (legacy single-tp call) or a ``list[float]``; the
    function emits one ``BarItem`` per (member, tp) pair, ordered by
    ``group_mode`` (well-major / tp-major).

    *pool_replicates* — when False, each rset member's wells are expanded
    into their own ``BarItem``s (one per well, coloured by the rset's
    palette colour). Default True preserves the legacy collapse.

    Pass :data:`FC_STATE_OFF` to skip fold-change scaling and get raw
    aggregated values (used by the additive CSV emitter).
    """
    if fluor_gates is None:
        fluor_gates = get_all_fluor_gates(ctx)

    if isinstance(target_t, (list, tuple)):
        target_ts = [float(t) for t in target_t]
    else:
        target_ts = [float(target_t)]
    if not target_ts:
        return []

    fc = _fc.coerce_fc_state(fc_state)
    fc_vs_ctrl = fc.vs_control_on
    fc_vs_t0 = fc.vs_t0_on
    # Read from the same place the tab reads them. Taking the defaults here
    # instead is what made this figure disagree with its on-tab twin under
    # ``subtract`` and whenever both axes were on.
    fc_op_ctrl = _fc.parse_fc_op_ctrl(ctx)
    fc_op_t0 = _fc.parse_fc_op_t0(ctx)
    fc_order = _fc.parse_fc_order(ctx)

    members = batch_members(ctx, group, pool_replicates=pool_replicates)

    def _member_pts(wells: list[str], rset_name: str | None) -> list:
        """AggPoint list for one member, matching the plotting tab's statistic.

        A pooled multi-well replicate set uses ``compute_rep_stats`` /
        ``compute_rep_per_fov_stats`` (mean-of-per-well-means) — NOT the old
        pool-of-cells ``aggregate_group`` — so batch bars equal the tab. A solo
        well (or a non-pooled single-well entry) stays on ``aggregate_group``
        (== ``aggregate_well`` for one well), preserving its within-well spread.
        The caller sets ``active_val_col`` so the rep-set stat reads the batch
        panel's column."""
        if rset_name is not None and len(wells) > 1:
            rset = ReplicateSet(rset_name, wells)
            rset_per_fov = replicate_unit(len(wells), per_fov_spread) == "fov"
            all_tps: set = set()
            for w in wells:
                for t, *_ in aggregate_well_cached(ctx, w, threshold=threshold,
                                            use_sem=False, val_col=val_col,
                                            fluor_gates=fluor_gates):
                    all_tps.add(t)
            out_pts: list = []
            for t in sorted(all_tps):
                # Index 4 is the value the Events panel draws; index 9 is its
                # replicate spread and index 10 the cross-well total the CSV's
                # integer column keeps. The spread used to be dropped on the
                # floor here and the total conflated with index 4, so the batch
                # bar truncated a 7.5 mean to 7 and drew no error bar.
                ntotal = compute_rep_n_above_total(ctx, rset, t, threshold)
                if rset_per_fov:
                    gm, gerr, gf, gfe, gnpf_m, gnpf_s = compute_rep_per_fov_stats(
                        ctx, rset, t, threshold, use_sem)
                    nval, nspread = 0, 0.0
                else:
                    gm, gerr, gf, gfe = compute_rep_stats(ctx, rset, t, threshold, use_sem)
                    # Per-well mean, matching the Bar tab's own numerator.
                    nval, nspread = compute_rep_n_above_stats(ctx, rset, t, use_sem, threshold)
                    # ``gfe`` is the fraction spread the producer returned; it
                    # was discarded and replaced with 0.0, so a batch export
                    # reported ``err_frac`` as zero where the tab drew a real
                    # whisker. The per-FOV fields are genuinely not computed
                    # here and stay 0.0.
                    gnpf_m, gnpf_s = 0.0, 0.0
                out_pts.append(
                    (t, gm, gerr, gf, nval, 0, gfe, gnpf_m, gnpf_s, nspread, ntotal))
            return out_pts
        return aggregate_group(ctx,
            wells, threshold=threshold, use_sem=use_sem,
            val_col=val_col, fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )

    member_pts_by_key = {
        key: _member_pts(wells, rset_name)
        for (key, _bd, wells, rset_name, _c) in members
    }
    # Control denominators, memoised for this call. The frac series was already
    # hoisted here by hand; the cache does the same for the mean side and will
    # hold every distinct control once per-sample mapping lands.
    ctrl_cache = _ControlSeriesCache(
        ctx, threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread, fluor_gates=fluor_gates,
    )
    # The batch panel carries its own mapping, so the same per-member resolver
    # serves it — the member keys here are the group's own, not the tab's.
    denoms = control_denominators(
        ctx, ctrl_cache, fc, [key for (key, _d, _w, _r, _c) in members],
        scope_wells={key: wells for (key, _d, wells, _r, _c) in members},
        member_rsets={w: rset_name
                      for (_k, _d, wells, rset_name, _c) in members
                      if rset_name for w in wells},
    )
    hidden_members = denoms.hidden_members

    def _format_tp(t: float) -> str:
        # Label only. ``BarItem.key`` keeps the unscaled ``t{t:.4g}`` form,
        # because that is the bar's identity — drag order, the x-position map
        # and the significance-bracket matching all key on it, and none of them
        # should move when the user changes how the axis is *printed*.
        return _rescale_tp(t, _time_rescale_for(ctx.state.plot))

    def _items_for_tp(target_t: float) -> list[BarItem]:
        out: list[BarItem] = []
        tp_str = _format_tp(target_t)
        multi_tp = len(target_ts) > 1
        for (key, base_display, wells, rset_name, color) in members:
            if key in hidden_members:
                continue
            (fc_ctrl_ref, fc_outcome, ctrl_stats_t, ctrl_frac_t,
             ctrl_n_t) = denoms.at(key, target_t)
            fc_control_mean_at_t, fc_control_spread_at_t = ctrl_stats_t or (None, None)
            fc_control_frac_at_t, fc_control_frac_spread_at_t = ctrl_frac_t or (None, None)
            pts = member_pts_by_key[key]
            matched = [pt for pt in pts if tp_close(pt[0], target_t)]
            if matched:
                pt = matched[0]
                m, s, f = pt[1], pt[2], pt[3]
                # A replicate set's point carries the drawn mean at index 4 and
                # the cross-well total at index 10. A solo well has no index 10
                # and no total to report — it is not an aggregation of
                # anything — so it stays ``None`` and its CSV replicate columns
                # stay empty, the way the tab's per-well branch does it.
                n_above_total = (int(pt[10]) if len(pt) >= 11
                                 and pt[10] is not None else None)
                frac_spread = float(pt[6]) if len(pt) >= 7 else 0.0
                n_above_pf_mean = float(pt[7]) if len(pt) >= 8 else 0.0
                n_above_pf_spread = float(pt[8]) if len(pt) >= 9 else 0.0
                n_above_rep_spread = float(pt[9]) if len(pt) >= 10 else 0.0
                if per_fov_spread:
                    n_above_val = n_above_pf_mean
                    n_above_err = n_above_pf_spread
                else:
                    n_above_val = float(pt[4]) if len(pt) >= 5 else 0.0
                    n_above_err = n_above_rep_spread
                (m, s, f, frac_spread, n_above_val, n_above_err) = _scale_group_bar(
                    (m, s), (f, frac_spread), (n_above_val, n_above_err),
                    pts=pts, ctrl=(ctrl_stats_t, ctrl_frac_t, ctrl_n_t),
                    outcome=fc_outcome, fc_vs_ctrl=fc_vs_ctrl,
                    fc_vs_t0=fc_vs_t0, baseline_tp=fc_baseline_tp,
                    per_fov_spread=per_fov_spread,
                    fc_op_ctrl=fc_op_ctrl, fc_op_t0=fc_op_t0, fc_order=fc_order,
                    baseline_ctrl=lambda tp, _k=key: denoms.baseline_at(_k, tp))
                has_mean = not math.isnan(m)
                # See the note in the rep-set branch above: a fraction of zero
                # is a measurement even when the above-threshold mean is not.
                has_frac = not math.isnan(f)
            else:
                # Nothing measured here. ``n_above_total`` is bound in this
                # branch too: it was set only inside the ``if matched:`` arm, so
                # a member with no point at this timepoint either inherited the
                # previous member's count into its own CSV cell — a fabricated
                # integer on a row whose mean is NaN — or, when it came first,
                # raised ``UnboundLocalError`` and killed the whole group.
                # ``None`` rather than ``0``: nothing computed is not a count
                # of zero, and the CSV writer already renders it as empty.
                m = float("nan"); s = 0.0
                f = float("nan"); frac_spread = 0.0
                n_above_val = float("nan"); n_above_err = 0.0
                n_above_total = None
                has_mean = False; has_frac = False
            # Labels stay single-line: cluster headers show the primary
            # identity; per-bar labels show the secondary.
            if multi_tp and group_mode == "well":
                display = f"t={tp_str}"
                gk = base_display
            elif multi_tp and group_mode == "timepoint":
                display = base_display
                gk = tp_str
            else:
                display = base_display
                gk = ""
            out.append(BarItem(
                key=key, display=display, color=color,
                mean=m, spread=s, has_mean=has_mean,
                frac=f, frac_spread=frac_spread, has_frac=has_frac,
                n_above=n_above_val, n_above_spread=n_above_err,
                n_above_total=n_above_total,
                fc_control=fc_ctrl_ref, fc_outcome=str(fc_outcome or ""),
                tp=float(target_t), group_key=gk,
            ))
        return out

    items: list[BarItem] = []
    if group_mode == "timepoint":
        for t in target_ts:
            items.extend(_items_for_tp(t))
    else:
        per_tp = [(_items_for_tp(tp), tp) for tp in target_ts]
        n_keys = len(per_tp[0][0]) if per_tp else 0
        for ki in range(n_keys):
            for items_for_tp, _tp in per_tp:
                if ki < len(items_for_tp):
                    items.append(items_for_tp[ki])
    # Colour by / Split by, over **this group's** key space rather than the
    # tab's. The two differ — a batch member is a `BarGroup`'s replicate set or
    # solo well, and in expanded mode a well of one — which is why the map is
    # passed rather than looked up, exactly as the fold-change resolver above
    # already does with ``scope_wells``. Matching against the tab's keys instead
    # would colour the members whose names happened to coincide and leave the
    # rest, which is the half-decorated figure that is worse than none.
    mark_timepoint_runs(items, len(target_ts) > 1)
    items, _notice = decorate_bar_items(
        ctx, items, wells_by_key=batch_wells_by_key(members),
        group_mode=group_mode)
    return items


def batch_wells_by_key(members) -> dict:
    """``member key -> wells`` for a batch group's own key space.

    Shared with ``batch_export/bar_panel``, which needs the same map to label
    the Colour-by legend on the figure it builds. A batch member is a
    ``BarGroup``'s replicate set or solo well — and in expanded mode a well of
    one — so its keys are not the tab's, and a legend built against the tab's
    keys would name the members whose names happened to coincide and omit the
    rest.
    """
    return {key: wells for (key, _d, wells, _r, _c) in members}


def _pooled_wells_for_fov_mode(ctx) -> list[str]:
    """Wells contributing to the FOV-pooled per-tp distribution.

    When replicate sets are active, every well inside every visible rset is
    pooled together (the user-set pool-replicates toggle is moot here — the
    "Aggregate FOV" mode already collapses well identity). When no rsets are
    active, the user's plate selection is pooled.
    """
    active_rsets = plot_replicate_sets(ctx)
    out: list[str] = []
    seen: set = set()
    if active_rsets:
        for rset in active_rsets:
            for w in rset.wells:
                if w in seen:
                    continue
                if w not in ctx.state.dataset.well_paths:
                    continue
                seen.add(w)
                out.append(w)
        return out
    if ctx.state.selection.selections:
        # Group mode but all groups hidden → no wells (don't resurrect the stale
        # plate selection; see ordered_bar_keys).
        return out
    for label in ctx.state.selection.selected_wells:
        if label in seen or label not in ctx.state.dataset.well_paths:
            continue
        seen.add(label)
        out.append(label)
    out.sort(key=parse_rc)
    return out


def collect_bar_items_fov_pooled(
    ctx, target_ts: list[float], *,
    threshold: float | None = None,
) -> tuple[list[BarItem], str]:
    """Build one ``BarItem`` per timepoint by pooling every well's cells.

    Used when the *Aggregate FOV* toggle is on AND the user has selected
    multiple timepoints (or even a single one). All cells from every well
    contributing to the plot are pooled per timepoint and aggregated with
    ``aggregate_with_threshold_df(..., per_fov_spread=True)`` so SD/SEM is
    computed across per-FOV means (FOV ids prefixed by well to avoid
    cross-well collisions).

    Returns ``(items, band_lbl)`` — one ``BarItem`` per tp, in ascending tp
    order; the items use ``group_key=""`` because they form a single
    timepoint-axis sequence with no clustering.
    """
    import numpy as _np
    import pandas as _pd
    from review.data.data_loading import (
        _aggregate_arrays as _agg_arrays,
        df_included_mask as _incl_mask,
        resolve_value_series as _resolve_vals,
    )

    use_sem = ctx.state.plot.use_sem
    band_lbl = spread_label(use_sem)
    if not target_ts:
        return [], band_lbl

    val_col = ctx.state.channel.active_val_col
    if threshold is None:
        threshold = on_cutoff_for_column(
            ctx, val_col, channel=ctx.state.channel.active_channel)

    fluor_gates = get_all_fluor_gates(ctx) or {}
    ratios = ctx.state.channel.ratio_index

    wells = _pooled_wells_for_fov_mode(ctx)

    # Build one (tp, val, fov) triple stream across all pooled wells. FOV ids
    # are well-prefixed so the same numeric FOV in two wells stays distinct.
    tp_chunks: list[_np.ndarray] = []
    val_chunks: list[_np.ndarray] = []
    fov_chunks: list[_np.ndarray] = []
    for w in wells:
        df = get_rows(ctx, w)
        if df is None or len(df) == 0:
            continue
        mask = _incl_mask(df).to_numpy(copy=True)
        if "area_px" in df.columns:
            area = _pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
            with _np.errstate(invalid="ignore"):
                mask &= _np.isfinite(area)
        for channel, gate in active_fluor_gates(fluor_gates).items():
            col = f"{channel}_mean_intensity"
            if col not in df.columns:
                continue
            v = _pd.to_numeric(df[col], errors="coerce").to_numpy()
            mask &= _np.isfinite(v) & (v > gate)
        if "timepoint_hours" in df.columns:
            tp = _pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
        else:
            tp = _np.full(len(df), _np.nan)
        val = _resolve_vals(df, val_col, ratios).to_numpy()
        fov_arr = fov_text(df).to_numpy()
        final = mask & _np.isfinite(tp) & _np.isfinite(val)
        if not final.any():
            continue
        # Prefix FOV by well so cross-well FOV ids stay disjoint.
        fov_pref = _np.array([f"{w}|{f}" for f in fov_arr[final]], dtype=object)
        tp_chunks.append(tp[final].astype(float))
        val_chunks.append(val[final].astype(float))
        fov_chunks.append(fov_pref)

    # Same split as the per-well branch: the key is identity and stays in the
    # hours the pipeline parsed, the display is a label and follows the
    # Properties sidebar's rescale factor.
    _tp_rescale = _time_rescale_for(ctx.state.plot)

    items: list[BarItem] = []
    if not tp_chunks:
        for t in target_ts:
            items.append(BarItem(
                key=f"t{t:.4g}",
                display=f"t={_rescale_tp(t, _tp_rescale)} h",
                color=color_for_well("A01"),
                mean=float("nan"), spread=0.0, has_mean=False,
                frac=float("nan"), frac_spread=0.0, has_frac=False,
                n_above=0.0, n_above_spread=0.0,
                tp=float(t), group_key="",
            ))
        return items, band_lbl

    tp_all = _np.concatenate(tp_chunks)
    val_all = _np.concatenate(val_chunks)
    fov_all = _np.concatenate(fov_chunks)

    # Aggregate per tp; only return the tps the user actually selected.
    from review.domain.plate_layout import WELL_COLORS as _WELL_COLORS
    target_set = set()
    for t in target_ts:
        target_set.add(float(t))
    # _aggregate_arrays groups by exact tp values present in the stream; match
    # against the shared relative tolerance (``tp_match.tp_close``) rather than
    # an absolute one, which a 4-significant-figure combo round-trip defeats.
    pts = _agg_arrays(tp_all, val_all, fov_all, threshold, use_sem, True)
    pts_by_t = {float(p[0]): p for p in pts}

    for i, t in enumerate(target_ts):
        # Same relative tolerance as the per-well branch, for the same reason.
        matched = None
        for pt_t, pt in pts_by_t.items():
            if tp_close(pt_t, float(t)):
                matched = pt
                break
        color = _WELL_COLORS[i % len(_WELL_COLORS)]
        display = f"t={_rescale_tp(t, _tp_rescale)}"
        if matched is None:
            items.append(BarItem(
                key=f"t{float(t):.4g}", display=display, color=color,
                mean=float("nan"), spread=0.0, has_mean=False,
                frac=float("nan"), frac_spread=0.0, has_frac=False,
                n_above=0.0, n_above_spread=0.0,
                tp=float(t), group_key="",
            ))
            continue
        m, s, f = matched[1], matched[2], matched[3]
        frac_spread = float(matched[6]) if len(matched) >= 7 else 0.0
        n_above_pf_mean = float(matched[7]) if len(matched) >= 8 else 0.0
        n_above_pf_spread = float(matched[8]) if len(matched) >= 9 else 0.0
        items.append(BarItem(
            key=f"t{float(t):.4g}", display=display, color=color,
            mean=float(m), spread=float(s), has_mean=not math.isnan(m),
            frac=float(f), frac_spread=frac_spread, has_frac=not math.isnan(f),
            n_above=n_above_pf_mean, n_above_spread=n_above_pf_spread,
            tp=float(t), group_key="",
        ))
    return items, band_lbl


def collect_bar_items_for_app(ctx, target_t: float, *,
                       fc_state=None, miss_sink=None) -> tuple:
    """Return ``(use_groups, list[BarItem], band_lbl)`` for *target_t*.

    Thin wrapper around :func:`barplot_controller.collect_bar_items`.
    ``fc_state`` lets the on-tab CSV exporter request raw items (pass
    ``FC_STATE_OFF``) alongside the normalized items the renderer
    consumes. ``miss_sink`` collects timepoints where a vs-control
    denominator couldn't be resolved so the caller can surface a
    single status warning at the end of the redraw.
    """
    return collect_bar_items(
        ctx, target_t, fc_state=fc_state, miss_sink=miss_sink,
    )


# ── Bar layout + drag-reorder hit-test (Qt-free; re-exported by controller) ──


def cluster_x_for_group_keys(group_keys: list[str]) -> list[float]:
    """The one-level cluster-position arithmetic of :func:`clustered_x_positions`,
    from a parallel list of group_key strings rather than ``BarItem``s.

    One level only: it has no sub-band keys, so it matches
    :func:`clustered_x_positions` exactly when no item carries a
    ``subgroup_key``. That holds for every caller, because the per-cell paths
    build their own keys and are never decorated.

    Used by the per-cell renderers (violin / beeswarm) and the batch
    export to compute matching x positions for non-``BarItem`` callers.
    """
    xs: list[float] = []
    cur_x = 0.0
    last_gk: str | None = None
    for i, gk in enumerate(group_keys):
        gk = gk or ""
        if i == 0:
            xs.append(cur_x)
            last_gk = gk
            continue
        if gk and gk == last_gk:
            cur_x += 1.0
        else:
            cur_x += 2.0 if gk and last_gk and gk != last_gk else 1.0
        xs.append(cur_x)
        last_gk = gk
    return xs


def cluster_centers(items, xs: list[float]):
    """Yield ``(group_key, centre_x)`` for each non-empty cluster, in encounter
    order.

    Pure layout arithmetic over the items and the positions
    ``clustered_x_positions`` gave them, which is why it sits here rather than
    beside the two annotation passes that consume it (the headers above the
    Mean panel and the footers under the tick rows). Those live in
    ``barplot/_annotations.py`` and ``barplot/renderer.py``; keeping the maths
    in the Qt-free half is what lets the private annotations module import it
    without reaching back up into ``controller``.
    """
    cluster_x: dict = {}
    cluster_order: list[str] = []
    for it, x in zip(items, xs):
        gk = it.group_key or ""
        if not gk:
            continue
        if gk not in cluster_x:
            cluster_x[gk] = []
            cluster_order.append(gk)
        cluster_x[gk].append(x)
    for gk in cluster_order:
        positions = cluster_x[gk]
        if positions:
            yield gk, sum(positions) / len(positions)


def drop_redundant_cluster_keys(items: list[BarItem]) -> None:
    """Blank ``group_key`` for items whose cluster header would only duplicate
    the bar's own tick label, in place.

    A ``group_key`` that occurs on exactly one item AND already equals that
    item's ``display`` carries no extra information (e.g. one pooled replicate
    group per a single timepoint). Left in, ``_annotate_cluster_headers`` draws a
    redundant bold label at the top of the Mean panel — overlapping the figure
    title — and ``clustered_x_positions`` inserts a spurious inter-bar gap.

    Genuine clusters are preserved: a header shared by ≥2 bars (e.g. a
    single-timepoint *unpooled* replicate set with several wells), or a header
    whose bars' ``display`` is something else (multi-timepoint, where ``display``
    is the timepoint, not the group name), is left untouched.
    """
    counts = Counter(it.group_key for it in items if it.group_key)
    for it in items:
        if it.group_key and counts[it.group_key] == 1 and it.display == it.group_key:
            it.group_key = ""


#: The extra x-distance between two adjacent sub-bands of one band — and the
#: amount the gap between bands grows by when they hold sub-bands, so that gap
#: (1.5 units) stays three times this one. The hierarchy then reads from
#: spacing alone: a wide gap separates cell lines, a narrow one separates doses
#: within a line.
SUBGROUP_GAP = 0.5


def clustered_x_positions(items: list[BarItem], *, bar_w: float | None = None,
                          group_spacing: float = DEFAULT_GROUP_SPACING
                          ) -> list[float]:
    """Compute clustered x-positions from ``BarItem.group_key``.

    Items sharing the same ``group_key`` get contiguous adjacent positions; a
    one-unit gap is inserted between clusters. When every item has an empty
    or unique ``group_key``, this reduces to ``list(range(len(items)))`` —
    matching the legacy single-tp single-cluster layout exactly.

    Inside a band, a change of ``subgroup_key`` inserts the narrower
    :data:`SUBGROUP_GAP`, and the gap between two sub-banded bands grows by the
    same amount so it stays the wider of the two. Positions are therefore not
    always integers. Every consumer already takes these positions as given
    (``bar_idx_at_x`` / ``bar_guide_x`` exist because gaps made them
    non-consecutive), so nothing downstream rounds them.

    Adjacent bars of one timepoint group — the same ``run_key`` inside the
    same band and sub-band — are drawn ``bar_w × (1 + group_spacing)`` apart
    rather than a full slot, capped at one slot, so the group reads as one
    block with a sliver between its bars. *bar_w* defaults to the automatic
    width for these items; the renderer passes the width it will actually
    draw, through :func:`bar_layout`, so the sliver is measured against the
    real bars.
    """
    if bar_w is None:
        bar_w = bar_width_for(len(items), False, BarGeometry())
    tight = min(1.0, bar_w * (1.0 + max(0.0, float(group_spacing))))
    xs: list[float] = []
    cur_x = 0.0
    prev = None
    for item in items:
        if prev is None:
            xs.append(cur_x)
            prev = item
            continue
        gk, last_gk = item.group_key or "", prev.group_key or ""
        sub, last_sub = item.subgroup_key or "", prev.subgroup_key or ""
        run = getattr(item, "run_key", "") or ""
        if gk and gk == last_gk and sub == last_sub and run and \
                run == (getattr(prev, "run_key", "") or ""):
            cur_x += tight
        elif gk and gk == last_gk:
            cur_x += 1.0 + (SUBGROUP_GAP if sub != last_sub else 0.0)
        elif gk and last_gk:
            # Between bands. Widened by the sub-band gap when either side is
            # sub-banded, so the band gap stays visibly the larger of the two
            # rather than half a unit away from it.
            cur_x += 2.0 + (SUBGROUP_GAP if (sub or last_sub) else 0.0)
        else:
            cur_x += 1.0
        xs.append(cur_x)
        prev = item
    return xs


def subcluster_centers(items, xs: list[float]):
    """Yield ``(subgroup_key, centre_x)`` for each contiguous sub-band.

    Keyed on the ``(group_key, subgroup_key)`` pair and on contiguity rather
    than on the label: ``1 µM`` inside HeLa and ``1 µM`` inside U2OS are two
    sub-bands with one name, and :func:`cluster_centers`' label-keyed pooling
    would draw a single header between them, over the wrong bars.
    """
    run_key = None
    run: list[float] = []
    for it, x in zip(items, xs):
        sub = getattr(it, "subgroup_key", "") or ""
        key = (it.group_key or "", sub)
        if key != run_key:
            if run_key is not None and run_key[1] and run:
                yield run_key[1], sum(run) / len(run)
            run_key, run = key, []
        run.append(x)
    if run_key is not None and run_key[1] and run:
        yield run_key[1], sum(run) / len(run)


def bar_idx_at_x(xdata: float, n: int, xs: list[float] | None = None) -> int:
    """Return the bar index nearest to *xdata*, clamped to [0, n-1].

    When ``xs`` (the actual on-screen x-positions from
    :func:`clustered_x_positions`) is supplied and matches ``n``, the nearest
    real position wins — clusters insert gaps so bars are NOT at integer x and
    ``int(round(xdata))`` picks the wrong column. Falls back to integer
    rounding only when ``xs`` is unavailable / length-mismatched.
    """
    if xs and len(xs) == n:
        return min(range(n), key=lambda i: abs(xdata - xs[i]))
    return max(0, min(n - 1, int(round(xdata))))


def bar_drag_xs(ctx, keys: list[str]) -> list[float] | None:
    """The on-screen x-positions for the current bars, aligned to ``keys``.

    Stashed by the renderer (:func:`render_bar_items` and the per-cell path).
    Returns None when absent or out of sync with the current key order, so the
    hit-test safely falls back to integer positions rather than misaligning.
    """
    xs = getattr(ctx.runtime.barplot, "xs", None)
    xs_keys = getattr(ctx.runtime.barplot, "xs_keys", None)
    if xs and xs_keys == keys and len(xs) == len(keys):
        return list(xs)
    return None


def bar_guide_x(xs: list[float] | None, tgt: int, src: int, n: int) -> float:
    """X for the drop-indicator guide line: the midpoint of the gap the bar
    would land in (after ``tgt`` when moving right, before it when moving
    left), using real cluster positions when available."""
    if xs and len(xs) == n:
        if tgt > src:
            return (xs[tgt] + xs[tgt + 1]) / 2.0 if tgt + 1 < n else xs[tgt] + 0.5
        return (xs[tgt - 1] + xs[tgt]) / 2.0 if tgt - 1 >= 0 else xs[tgt] - 0.5
    # Fallback: legacy integer layout.
    if tgt > src:
        return min(tgt + 0.5, n - 0.5)
    return max(tgt - 0.5, -0.5)


def bar_err_settings(ctx) -> tuple:
    """The Bar tab's name for :func:`core.plot_state.error_bar_settings`.

    The rule moved to ``core`` when the Heat Map's per-cell ``±`` started
    asking it too: the Properties → Statistics panel is app-wide, and a second
    reading of it would let one figure draw a 95% CI while its neighbour drew
    the SEM it was built from. This stays as the name five call sites already
    use, and holds no logic of its own.
    """
    from review.core.plot_state import error_bar_settings
    return error_bar_settings(ctx)


def bar_well_display_label(ctx, lbl: str) -> str:
    """Bar-plot-safe well label that avoids numeric-only tick text.

    **Only a hand-typed label is second-guessed.** The guard is for a Well
    Labels entry of ``3``, which on a bar axis reads as a count or an index and
    says nothing about the well. A name composed from the sample variables can
    be numeric for the opposite reason — a `Dose` of ``10`` declared without a
    unit is the condition, and it is the whole of what the user asked the axis
    to say — so substituting the well token there would undo the naming on
    exactly the plates this labelling exists for. §7.1: a domain predicate
    applied outside its domain.
    """
    disp, source = well_display_label_and_source(ctx, lbl)
    disp = str(disp)
    if source == LABEL_FROM_CUSTOM and re.match(r"^\s*\d+\s*$", disp):
        return _extract_well_token(lbl) or disp
    return disp
