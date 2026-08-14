"""Bar-plot data collection (no rendering).

The pure data-gathering half of the bar-plot feature: walk the loaded wells /
replicate sets, apply fold-change scaling + the cell-gating thresholds, and
return :class:`BarItem` rows. Mirrors ``scatter/collect.py`` — split out of
``barplot/controller.py`` (T4) so the controller keeps state/interaction wiring
while this module owns collection. ``barplot/controller`` and the CSV exporters
re-export these names.
"""

from __future__ import annotations

from well_viewer.domain.selections_model import (color_for_replicate_set, color_for_well, replicate_display_label, well_display_label, active_replicate_sets)
from well_viewer.core.channel_state_controller import get_all_fluor_gates, on_cutoff_for_column
from well_viewer.data.data_loading import get_rows
from well_viewer.domain.viewer_state import parse_rc

import logging
import math
from collections import Counter
from dataclasses import dataclass

from well_viewer.domain.batch_models import BarGroup, ReplicateSet
from well_viewer.domain import fold_change as _fc
from well_viewer.domain.fold_change_series_cache import (
    ControlSeriesCache as _ControlSeriesCache,
)
from well_viewer.domain.time_rescale import format_scaled as _rescale_tp
from well_viewer.domain.stats_compute import (
    aggregate_group,
    aggregate_well,
    compute_rep_n_above_stats,
    compute_rep_per_fov_stats,
    compute_rep_stats,
    replicate_unit,
)
from well_viewer.core import debug_flags as debug_flags

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
      * ``n_above`` / ``n_above_spread`` — events-above-threshold panel.
        ``n_above_spread > 0`` indicates per-FOV spread mode; in that
        case ``n_above`` is the per-FOV mean (float), otherwise it's
        the total count (kept as float for storage uniformity, cast on
        display).
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
    # Multi-timepoint plotting. ``tp`` is the timepoint this item was
    # computed at; ``group_key`` is the cluster identity the renderer uses
    # to lay out adjacent x positions (well/rset name in "Group by well"
    # mode, formatted tp string in "Group by timepoint" mode, or "" when
    # this item is itself a pooled column).
    tp: float = 0.0
    group_key: str = ""


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


# Re-exported from the domain layer so the many callers that import it from
# here (barplot.controller, export.service, batch_export.bar_panel) keep their
# import path. It is a frozen ``FoldChangeState`` that still compares equal to
# the ``(False, "", False)`` tuple it used to be.
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
    band_lbl = "SEM" if use_sem else "SD"
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
    active_rsets = active_replicate_sets(ctx.state.selection.selections, ctx.state.dataset.well_paths)
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
    fc_ctrl_lbl = fc.control_label
    fc_vs_t0 = fc.vs_t0_on
    if fc_baseline_tp is None:
        fc_baseline_tp = _fc.parse_baseline_tp(ctx)
    fc_op_ctrl = _fc.parse_fc_op_ctrl(ctx)
    fc_op_t0 = _fc.parse_fc_op_t0(ctx)
    fc_order = _fc.parse_fc_order(ctx)
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

    ordered_keys = ctx.runtime.barplot.current_keys()
    rset_by_name = {r.name: r for r in active_rsets}

    def _format_tp(t: float) -> str:
        # Label only. ``BarItem.key`` keeps the unscaled ``t{t:.4g}`` form,
        # because that is the bar's identity — drag order, the x-position map
        # and the significance-bracket matching all key on it, and none of them
        # should move when the user changes how the axis is *printed*.
        return _rescale_tp(t, ctx.state.plot.time_scale)

    def _items_for_tp(target_t: float) -> list[BarItem]:
        """Build one ``BarItem`` per bar key at this timepoint.

        Fold-change resolution runs once here per tp so the control / t0
        denominators are not re-aggregated for every bar.
        """
        fc_control_mean_at_t = None
        fc_control_spread_at_t = None
        fc_control_frac_at_t = None
        fc_control_frac_spread_at_t = None
        fc_control_n_at_t = None
        fc_control_n_spread_at_t = None
        if fc_vs_ctrl and fc_ctrl_lbl:
            ctrl_stats = ctrl_cache.stats_at(fc_ctrl_lbl, target_t)
            if ctrl_stats is None:
                if miss_sink is not None:
                    miss_sink.add(float(target_t))
            else:
                fc_control_mean_at_t, fc_control_spread_at_t = ctrl_stats
            ctrl_frac = ctrl_cache.frac_at(fc_ctrl_lbl, target_t)
            if ctrl_frac is not None:
                fc_control_frac_at_t, fc_control_frac_spread_at_t = ctrl_frac
            ctrl_n = ctrl_cache.n_at(fc_ctrl_lbl, target_t)
            if ctrl_n is not None:
                fc_control_n_at_t, fc_control_n_spread_at_t = ctrl_n
        fc_force_nan = fc_vs_ctrl and fc_ctrl_lbl and fc_control_mean_at_t is None

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
                        ctx, rset, target_t, use_sem)
                t0_mean = None
                t0_spread = None
                t0_frac = None
                t0_frac_spread = None
                t0_n = None
                t0_n_spread = None
                # Per-rset effective control denominator. Outer-scope
                # fc_control_mean_at_t / _spread_at_t are the raw ctrl@t —
                # in t0_first mode we replace them locally with the post-t0
                # control without leaking into the next rset.
                eff_ctrl_mean = fc_control_mean_at_t
                eff_ctrl_spread = fc_control_spread_at_t
                eff_ctrl_frac = fc_control_frac_at_t
                eff_ctrl_frac_spread = fc_control_frac_spread_at_t
                eff_ctrl_n = fc_control_n_at_t
                eff_ctrl_n_spread = fc_control_n_spread_at_t
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
                        if fc_vs_ctrl and fc_ctrl_lbl:
                            ctrl_b = ctrl_cache.stats_at(fc_ctrl_lbl, resolved_tp)
                            if ctrl_b is not None:
                                if fc_order == "t0_first":
                                    eff_ctrl_mean, eff_ctrl_spread = _fc._apply_axis(
                                        eff_ctrl_mean, eff_ctrl_spread or 0.0,
                                        ctrl_b[0], ctrl_b[1], fc_op_t0,
                                    )
                                else:
                                    t0_mean, t0_spread = _fc._apply_axis(
                                        t0_mean, t0_spread,
                                        ctrl_b[0], ctrl_b[1], fc_op_ctrl,
                                    )
                    fstats = _member_baseline(
                        _fc.member_first_tp_frac_resolved, rset.name, per_fov_spread)
                    if fstats is not None:
                        resolved_tp_f, t0_frac, t0_frac_spread = fstats
                        if fc_vs_ctrl and fc_ctrl_lbl:
                            ctrl_bf = ctrl_cache.frac_at(fc_ctrl_lbl, resolved_tp_f)
                            if ctrl_bf is not None:
                                if fc_order == "t0_first":
                                    if eff_ctrl_frac is not None:
                                        eff_ctrl_frac, eff_ctrl_frac_spread = _fc._apply_axis(
                                            eff_ctrl_frac, eff_ctrl_frac_spread or 0.0,
                                            ctrl_bf[0], ctrl_bf[1], fc_op_t0,
                                        )
                                else:
                                    t0_frac, t0_frac_spread = _fc._apply_axis(
                                        t0_frac, t0_frac_spread,
                                        ctrl_bf[0], ctrl_bf[1], fc_op_ctrl,
                                    )
                    nstats = _member_baseline(
                        _fc.member_first_tp_n_resolved, rset.name, per_fov_spread)
                    if nstats is not None:
                        resolved_tp_n, t0_n, t0_n_spread = nstats
                        if fc_vs_ctrl and fc_ctrl_lbl:
                            ctrl_bn = ctrl_cache.n_at(fc_ctrl_lbl, resolved_tp_n)
                            if ctrl_bn is not None:
                                if fc_order == "t0_first":
                                    if eff_ctrl_n is not None:
                                        eff_ctrl_n, eff_ctrl_n_spread = _fc._apply_axis(
                                            eff_ctrl_n, eff_ctrl_n_spread or 0.0,
                                            ctrl_bn[0], ctrl_bn[1], fc_op_t0,
                                        )
                                else:
                                    t0_n, t0_n_spread = _fc._apply_axis(
                                        t0_n, t0_n_spread,
                                        ctrl_bn[0], ctrl_bn[1], fc_op_ctrl,
                                    )
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
                has_mean = not math.isnan(gm)
                has_frac = has_mean and not math.isnan(gf)
                gk = rset_disp if group_mode == "well" else tp_str
                out.append(BarItem(
                    key=rset.name, display=display, color=color,
                    mean=gm, spread=g_err_m, has_mean=has_mean,
                    frac=gf, frac_spread=g_err_f, has_frac=has_frac,
                    n_above=n_above_val, n_above_spread=n_above_err,
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
            pts = aggregate_well(ctx,
                label, threshold=threshold, use_sem=use_sem,
                val_col=ctx.state.channel.active_val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
            if matched:
                pt = matched[0]
                m, s, f = pt[1], pt[2], pt[3]
                n_above_total = int(pt[4])
                fs = float(pt[6]) if len(pt) >= 7 else 0.0
                n_above_pf_mean = float(pt[7]) if len(pt) >= 8 else 0.0
                n_above_pf_spread = float(pt[8]) if len(pt) >= 9 else 0.0
                if per_fov_spread:
                    n_above_val = n_above_pf_mean
                    n_above_err = n_above_pf_spread
                else:
                    n_above_val = float(n_above_total)
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
                    eff_ctrl_mean = fc_control_mean_at_t
                    eff_ctrl_spread = fc_control_spread_at_t
                    eff_ctrl_frac = fc_control_frac_at_t
                    eff_ctrl_frac_spread = fc_control_frac_spread_at_t
                    eff_ctrl_n = fc_control_n_at_t
                    eff_ctrl_n_spread = fc_control_n_spread_at_t
                    if fc_vs_t0:
                        resolved_tp, t0_mean, t0_spread = _fc.baseline_from_points_resolved(
                            pts, baseline_tp=fc_baseline_tp,
                        )
                        resolved_tp_f, t0_frac, t0_frac_spread = _fc.baseline_frac_from_points_resolved(
                            pts, baseline_tp=fc_baseline_tp,
                        )
                        resolved_tp_n, t0_n, t0_n_spread = _fc.baseline_n_from_points_resolved(
                            pts, per_fov_spread=per_fov_spread, baseline_tp=fc_baseline_tp,
                        )
                        # Cross-axis stacking: ctrl_first → adjust t0 denom; t0_first → adjust control denom.
                        if fc_vs_ctrl and fc_ctrl_lbl and resolved_tp is not None and t0_mean is not None:
                            ctrl_b = ctrl_cache.stats_at(fc_ctrl_lbl, resolved_tp)
                            if ctrl_b is not None:
                                if fc_order == "t0_first":
                                    eff_ctrl_mean, eff_ctrl_spread = _fc._apply_axis(
                                        eff_ctrl_mean, eff_ctrl_spread or 0.0,
                                        ctrl_b[0], ctrl_b[1], fc_op_t0,
                                    )
                                else:
                                    t0_mean, t0_spread = _fc._apply_axis(
                                        t0_mean, t0_spread,
                                        ctrl_b[0], ctrl_b[1], fc_op_ctrl,
                                    )
                        if fc_vs_ctrl and fc_ctrl_lbl and resolved_tp_f is not None and t0_frac is not None:
                            ctrl_bf = ctrl_cache.frac_at(fc_ctrl_lbl, resolved_tp_f)
                            if ctrl_bf is not None:
                                if fc_order == "t0_first":
                                    if eff_ctrl_frac is not None:
                                        eff_ctrl_frac, eff_ctrl_frac_spread = _fc._apply_axis(
                                            eff_ctrl_frac, eff_ctrl_frac_spread or 0.0,
                                            ctrl_bf[0], ctrl_bf[1], fc_op_t0,
                                        )
                                else:
                                    t0_frac, t0_frac_spread = _fc._apply_axis(
                                        t0_frac, t0_frac_spread,
                                        ctrl_bf[0], ctrl_bf[1], fc_op_ctrl,
                                    )
                        if fc_vs_ctrl and fc_ctrl_lbl and resolved_tp_n is not None and t0_n is not None:
                            ctrl_bn = ctrl_cache.n_at(fc_ctrl_lbl, resolved_tp_n)
                            if ctrl_bn is not None:
                                if fc_order == "t0_first":
                                    if eff_ctrl_n is not None:
                                        eff_ctrl_n, eff_ctrl_n_spread = _fc._apply_axis(
                                            eff_ctrl_n, eff_ctrl_n_spread or 0.0,
                                            ctrl_bn[0], ctrl_bn[1], fc_op_t0,
                                        )
                                else:
                                    t0_n, t0_n_spread = _fc._apply_axis(
                                        t0_n, t0_n_spread,
                                        ctrl_bn[0], ctrl_bn[1], fc_op_ctrl,
                                    )
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
                has_mean = not math.isnan(m)
                has_frac = not math.isnan(f)
                out.append(BarItem(
                    key=label, display=display, color=color,
                    mean=m, spread=s, has_mean=has_mean,
                    frac=f, frac_spread=fs, has_frac=has_frac,
                    n_above=n_above_val, n_above_spread=n_above_err,
                    tp=float(target_t), group_key=gk,
                ))
            else:
                out.append(BarItem(
                    key=label, display=display, color=color,
                    mean=float("nan"), spread=0.0, has_mean=False,
                    frac=float("nan"), frac_spread=0.0, has_frac=False,
                    n_above=0.0, n_above_spread=0.0,
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
    return use_groups, items, band_lbl


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
    fc_ctrl_lbl = fc.control_label
    fc_vs_t0 = fc.vs_t0_on

    # Build (key, display, wells, rset_name_or_None, color) member list.
    from well_viewer.domain.plate_layout import WELL_COLORS as _WELL_COLORS
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
                for t, *_ in aggregate_well(ctx, w, threshold=threshold,
                                            use_sem=False, val_col=val_col,
                                            fluor_gates=fluor_gates):
                    all_tps.add(t)
            out_pts: list = []
            for t in sorted(all_tps):
                if rset_per_fov:
                    gm, gerr, gf, gfe, gnpf_m, gnpf_s = compute_rep_per_fov_stats(
                        ctx, rset, t, threshold, use_sem)
                    nval = 0
                else:
                    gm, gerr, gf, _ = compute_rep_stats(ctx, rset, t, threshold, use_sem)
                    # Per-well mean, matching the Bar tab's own numerator.
                    nval, _ = compute_rep_n_above_stats(ctx, rset, t, use_sem)
                    gfe, gnpf_m, gnpf_s = 0.0, 0.0, 0.0
                out_pts.append((t, gm, gerr, gf, nval, 0, gfe, gnpf_m, gnpf_s))
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
    _ctrl_frac_series: dict = {}
    if fc_vs_ctrl and fc_ctrl_lbl:
        _ctrl_frac_series = _fc.member_frac_stats_series(
            ctx, fc_ctrl_lbl, threshold=threshold, val_col=val_col,
            use_sem=use_sem, per_fov_spread=per_fov_spread, fluor_gates=fluor_gates,
        )

    def _format_tp(t: float) -> str:
        # Label only. ``BarItem.key`` keeps the unscaled ``t{t:.4g}`` form,
        # because that is the bar's identity — drag order, the x-position map
        # and the significance-bracket matching all key on it, and none of them
        # should move when the user changes how the axis is *printed*.
        return _rescale_tp(t, ctx.state.plot.time_scale)

    def _lookup_at(series: dict, target_t: float):
        if target_t in series:
            return series[target_t]
        for ct, val in series.items():
            if abs(ct - target_t) < 1e-6:
                return val
        return None

    def _items_for_tp(target_t: float) -> list[BarItem]:
        # Resolve control denominator once per tp — the rep-set statistic
        # (``control_stats_at_for_bar``), matching the members' numerator.
        fc_control_mean_at_t = None
        fc_control_spread_at_t = None
        fc_control_frac_at_t = None
        fc_control_frac_spread_at_t = None
        if fc_vs_ctrl and fc_ctrl_lbl:
            ctrl_stats = ctrl_cache.stats_at(fc_ctrl_lbl, target_t)
            if ctrl_stats is not None:
                fc_control_mean_at_t, fc_control_spread_at_t = ctrl_stats
            _cfrac = _lookup_at(_ctrl_frac_series, target_t)
            if _cfrac is not None:
                fc_control_frac_at_t, fc_control_frac_spread_at_t = _cfrac

        out: list[BarItem] = []
        tp_str = _format_tp(target_t)
        multi_tp = len(target_ts) > 1
        for (key, base_display, wells, rset_name, color) in members:
            pts = member_pts_by_key[key]
            matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
            if matched:
                pt = matched[0]
                m, s, f = pt[1], pt[2], pt[3]
                n_above_total = int(pt[4]) if len(pt) >= 5 else 0
                frac_spread = float(pt[6]) if len(pt) >= 7 else 0.0
                n_above_pf_mean = float(pt[7]) if len(pt) >= 8 else 0.0
                n_above_pf_spread = float(pt[8]) if len(pt) >= 9 else 0.0
                if per_fov_spread:
                    n_above_val = n_above_pf_mean
                    n_above_err = n_above_pf_spread
                else:
                    n_above_val = float(n_above_total)
                    n_above_err = 0.0
                if fc_vs_ctrl or fc_vs_t0:
                    t0_mean = None
                    t0_spread = None
                    t0_frac = None
                    t0_frac_spread = None
                    if fc_vs_t0:
                        t0_mean, t0_spread = _fc.baseline_from_points(
                            pts, baseline_tp=fc_baseline_tp,
                        )
                        t0_frac, t0_frac_spread = _fc.baseline_frac_from_points(
                            pts, baseline_tp=fc_baseline_tp,
                        )
                    m, s = _fc.scale_bar_value(
                        m, s,
                        control_mean=fc_control_mean_at_t if fc_vs_ctrl else None,
                        control_spread=fc_control_spread_at_t if fc_vs_ctrl else None,
                        t0_mean=t0_mean if fc_vs_t0 else None,
                        t0_spread=t0_spread if fc_vs_t0 else None,
                    )
                    f, frac_spread = _fc.scale_bar_value(
                        f, frac_spread,
                        control_mean=fc_control_frac_at_t if fc_vs_ctrl else None,
                        control_spread=fc_control_frac_spread_at_t if fc_vs_ctrl else None,
                        t0_mean=t0_frac if fc_vs_t0 else None,
                        t0_spread=t0_frac_spread if fc_vs_t0 else None,
                    )
                has_mean = not math.isnan(m)
                has_frac = has_mean and not math.isnan(f)
            else:
                m = float("nan"); s = 0.0
                f = float("nan"); frac_spread = 0.0
                n_above_val = 0.0; n_above_err = 0.0
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
    return items


def _pooled_wells_for_fov_mode(ctx) -> list[str]:
    """Wells contributing to the FOV-pooled per-tp distribution.

    When replicate sets are active, every well inside every visible rset is
    pooled together (the user-set pool-replicates toggle is moot here — the
    "Aggregate FOV" mode already collapses well identity). When no rsets are
    active, the user's plate selection is pooled.
    """
    active_rsets = active_replicate_sets(ctx.state.selection.selections, ctx.state.dataset.well_paths)
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
    from well_viewer.data.data_loading import (
        _aggregate_arrays as _agg_arrays,
        df_included_mask as _incl_mask,
        resolve_value_series as _resolve_vals,
    )

    use_sem = ctx.state.plot.use_sem
    band_lbl = "SEM" if use_sem else "SD"
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
        for channel, gate in fluor_gates.items():
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
        if "fov" in df.columns:
            fov_series = (df["fov"].fillna("1").astype(str).str.strip().replace("", "1"))
        else:
            fov_series = _pd.Series(["1"] * len(df), index=df.index)
        fov_arr = fov_series.to_numpy()
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
    _tp_label = ctx.state.plot.time_scale

    items: list[BarItem] = []
    if not tp_chunks:
        for t in target_ts:
            items.append(BarItem(
                key=f"t{t:.4g}",
                display=f"t={_rescale_tp(t, _tp_label)} h",
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
    from well_viewer.domain.plate_layout import WELL_COLORS as _WELL_COLORS
    target_set = set()
    for t in target_ts:
        target_set.add(float(t))
    # _aggregate_arrays groups by exact tp values present in the stream;
    # round to the same tolerance we already use elsewhere (1e-6).
    pts = _agg_arrays(tp_all, val_all, fov_all, threshold, use_sem, True)
    pts_by_t = {float(p[0]): p for p in pts}

    for i, t in enumerate(target_ts):
        # Match within 1e-6 tolerance to handle float rounding from
        # earlier numeric parsing — same tolerance as the per-well branch.
        matched = None
        for pt_t, pt in pts_by_t.items():
            if abs(pt_t - float(t)) < 1e-6:
                matched = pt
                break
        color = _WELL_COLORS[i % len(_WELL_COLORS)]
        display = f"t={_rescale_tp(t, _tp_label)}"
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
    """Same cluster-position arithmetic as :func:`clustered_x_positions`
    but from a parallel list of group_key strings rather than ``BarItem``s.

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


def clustered_x_positions(items: list[BarItem]) -> list[float]:
    """Compute clustered integer x-positions from ``BarItem.group_key``.

    Items sharing the same ``group_key`` get contiguous adjacent positions; a
    one-unit gap is inserted between clusters. When every item has an empty
    or unique ``group_key``, this reduces to ``list(range(len(items)))`` —
    matching the legacy single-tp single-cluster layout exactly.
    """
    xs: list[float] = []
    cur_x = 0.0
    last_gk: str | None = None
    for i, item in enumerate(items):
        gk = item.group_key or ""
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
    """``(err_kind, ci_scale, draw_err)`` for the bar error bars, derived from
    the Properties → Statistics controls (``PlotState.bar_err_kind`` /
    ``bar_show_mode``).

    ``draw_err`` is False when the Show mode is "Mean" (bars only) or the
    error-bar type is "None". ``ci_scale`` is 1.96 for the 95% CI option (a
    normal-approx CI built from the SEM the spreads are already computed as),
    else 1.0. With no ``ctx`` (e.g. unit tests calling the renderer directly)
    the legacy behaviour — always draw — is preserved.
    """
    if ctx is None or not hasattr(ctx, "state"):
        return ("sem", 1.0, True)
    plot = ctx.state.plot
    err_kind = str(getattr(plot, "bar_err_kind", "sem") or "sem")
    show_mode = str(getattr(plot, "bar_show_mode", "mean_spread") or "mean_spread")
    draw_err = (show_mode != "mean") and (err_kind != "none")
    ci_scale = 1.96 if err_kind == "ci95" else 1.0
    return (err_kind, ci_scale, draw_err)
