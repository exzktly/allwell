"""Fold-change normalization helpers for the Bar and Line plot tabs.

Two independent modes are supported and may be combined:

1. ``vs control`` — every bar / curve is divided by the control well's
   (or replicate-set's) mean at the same timepoint.
2. ``vs t0``     — every bar / curve is divided by its own value at the
   earliest available timepoint (each member's own baseline).

The helpers operate on aggregation-point ("AggPoint") tuples produced by
``aggregate_with_threshold_df`` / ``_aggregate_well`` / ``_aggregate_group``:

    (t, mean, spread, frac, n_above, n_total,
     frac_spread?, n_above_pf_mean?, n_above_pf_spread?)

``mean``/``spread`` are rescaled by the bar's mean panel, and ``frac``/
``frac_spread`` are independently rescaled by the bar's OnFraction panel
(its FC denominator is the control's / baseline's frac, not its mean — they
are independent quantities and shouldn't share a denominator). Counts and
per-FOV spreads of counts are left untouched. The transform returns new
tuples with the same arity.

Stacking order: when both axes are active, ``vs t0`` is applied to the
post-control values. The final value is::

    Y(t) = (X(t) / C(t)) / (X(0) / C(0))

i.e. the ΔΔCt-style ratio-of-ratios, not ``(X(t) / X(0)) / (C(t) / C(0))``.
The two forms are NOT equivalent in general; the docstring of
``normalize_pts`` carries the math.

Error propagation: each fold-change denominator (control mean, t0
baseline) is divided through with its OWN spread, and the resulting
relative error combines in quadrature:

    (σ_Y / Y)² = (σ_X / X)² + (σ_C / C)² + (σ_B / B)²    (B = t0)

The relevant helpers (``scale_bar_value``, ``normalize_pts``) accept
denominator spreads via ``control_spread`` / ``t0_spread`` /
``control_stats``. When a denominator spread isn't supplied it
defaults to 0 (the "treat as exact" behaviour) — that's still the
backwards-compatible mode and matches the unit tests' simpler form.
``member_stats_series`` / ``control_stats_at_for_bar`` return both
mean and spread so the plot tab and line tab pick up the full
propagation automatically.

Statistical consistency: ``member_mean_series`` and
``control_mean_at_for_bar`` resolve the control's mean using the SAME
statistic the displayed bar / curve uses — mean-of-per-well-means for
rep-set members (via ``_compute_rep_stats`` / ``_compute_rep_per_fov_stats``),
single-well aggregation for well members. Mixing statistics across
numerator and denominator gave biased ratios in the original
implementation; that's fixed here.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Set, Tuple
from well_viewer.domain.stats_compute import compute_rep_stats
from well_viewer.domain.stats_compute import compute_rep_per_fov_stats
from well_viewer.domain.stats_compute import compute_rep_n_above
from well_viewer.domain.stats_compute import aggregate_well
from well_viewer.domain.stats_compute import aggregate_group


CONTROL_NONE = ""

# Relative-tolerance match for floating-point timepoint comparisons.
# Combo widgets format their items with ``:.4g`` (≈4 significant figures),
# so a fractional timepoint like 2/3 ≈ 0.6667 can be ~3e-5 off the original
# float when parsed back from text. An absolute 1e-6 tolerance misses
# those cases, so we use a relative one — passes for both whole-number
# and fractional timepoints, fails only on truly mismatched values.
_TP_REL_TOL = 1e-3


def _rel_err_sq(value: float, spread: float) -> float:
    """``(spread / value)²``, falling back to 0 for non-finite or zero values."""
    if (not isinstance(value, (int, float)) or value == 0
            or not math.isfinite(value)
            or not isinstance(spread, (int, float))
            or not math.isfinite(spread)):
        return 0.0
    return (spread / value) ** 2


def _propagate_spread(
    new_mean: float, mean: float, spread: float,
    denom_rel_err_sq: float,
) -> float:
    """Combined spread for Y = X / D under independent-error quadrature.

    ``denom_rel_err_sq`` is the sum of ``(σ_Di / Di)²`` over every
    denominator. When it's 0 the formula collapses to ``spread / factor``
    (the "treat denominator as exact" behaviour). Always returns a
    non-negative value.
    """
    if not math.isfinite(new_mean):
        return 0.0
    rel_sq = _rel_err_sq(mean, spread) + denom_rel_err_sq
    if rel_sq <= 0:
        return 0.0
    return abs(new_mean) * math.sqrt(rel_sq)


def _subtract_pt(
    pt: tuple, shift: float, *,
    shift_spread: float = 0.0,
) -> tuple:
    """Return a copy of *pt* with ``shift`` subtracted from the mean.

    Absolute variances add in quadrature: ``σ_Y² = σ_X² + σ_shift²``.
    Companion to :func:`_scale_pt` for the additive (``op="subtract"``)
    fold-change branch.
    """
    m = pt[1]
    s = pt[2]
    if not (isinstance(m, (int, float)) and math.isfinite(m)):
        new = list(pt); new[1] = float("nan"); new[2] = 0.0
        return tuple(new)
    if not math.isfinite(shift):
        new = list(pt); new[1] = float("nan"); new[2] = 0.0
        return tuple(new)
    s_val = s if isinstance(s, (int, float)) and math.isfinite(s) else 0.0
    shift_var = float(shift_spread) ** 2 if math.isfinite(shift_spread) else 0.0
    new_spread = math.sqrt(s_val * s_val + shift_var)
    new = list(pt)
    new[1] = float(m) - float(shift)
    new[2] = float(new_spread)
    return tuple(new)


def _scale_field(
    pt: tuple, val_idx: int, spread_idx: int, factor: float, *,
    factor_rel_err_sq: float = 0.0,
) -> tuple:
    """Return a copy of *pt* with field at *val_idx* & *spread_idx* scaled by ``1/factor``.

    Generic version of _scale_pt for use on arbitrary field pairs (e.g., fraction,
    N_above) instead of just the mean/spread at indices [1]/[2].
    """
    if factor == 0 or not math.isfinite(factor):
        scaled_val = float("nan")
        scaled_spread = 0.0
    else:
        v = pt[val_idx] if val_idx < len(pt) else None
        s = pt[spread_idx] if spread_idx < len(pt) else None
        if isinstance(v, (int, float)) and math.isfinite(v):
            scaled_val = v / factor
        else:
            scaled_val = float("nan")
        s_val = s if isinstance(s, (int, float)) and math.isfinite(s) else 0.0
        scaled_spread = _propagate_spread(
            scaled_val, v if isinstance(v, (int, float)) else float("nan"),
            s_val, factor_rel_err_sq,
        )
    new = list(pt)
    while len(new) <= max(val_idx, spread_idx):
        new.append(0.0)
    new[val_idx] = scaled_val
    new[spread_idx] = scaled_spread
    return tuple(new)


def _subtract_field(
    pt: tuple, val_idx: int, spread_idx: int, shift: float, *,
    shift_spread: float = 0.0,
) -> tuple:
    """Return a copy of *pt* with field at *val_idx* & *spread_idx* shifted by ``shift``.

    Generic version of _subtract_pt for use on arbitrary field pairs.
    """
    v = pt[val_idx] if val_idx < len(pt) else None
    s = pt[spread_idx] if spread_idx < len(pt) else None
    if not (isinstance(v, (int, float)) and math.isfinite(v)):
        new = list(pt)
        while len(new) <= max(val_idx, spread_idx):
            new.append(0.0)
        new[val_idx] = float("nan")
        new[spread_idx] = 0.0
        return tuple(new)
    if not math.isfinite(shift):
        new = list(pt)
        while len(new) <= max(val_idx, spread_idx):
            new.append(0.0)
        new[val_idx] = float("nan")
        new[spread_idx] = 0.0
        return tuple(new)
    s_val = s if isinstance(s, (int, float)) and math.isfinite(s) else 0.0
    shift_var = float(shift_spread) ** 2 if math.isfinite(shift_spread) else 0.0
    new_spread = math.sqrt(s_val * s_val + shift_var)
    new = list(pt)
    while len(new) <= max(val_idx, spread_idx):
        new.append(0.0)
    new[val_idx] = float(v) - float(shift)
    new[spread_idx] = float(new_spread)
    return tuple(new)


def _scale_pt(
    pt: tuple, factor: float, *,
    factor_rel_err_sq: float = 0.0,
) -> tuple:
    """Return a copy of *pt* with mean & spread scaled by ``1/factor``.

    *factor_rel_err_sq* — pre-computed ``(σ_D / D)²`` for the divisor,
    folded into the result's spread via quadrature. Default 0 preserves
    the legacy "denominator treated as exact" behaviour.
    """
    if factor == 0 or not math.isfinite(factor):
        scaled_mean = float("nan")
        scaled_spread = 0.0
    else:
        m = pt[1]
        s = pt[2]
        if isinstance(m, (int, float)) and math.isfinite(m):
            scaled_mean = m / factor
        else:
            scaled_mean = float("nan")
        s_val = s if isinstance(s, (int, float)) and math.isfinite(s) else 0.0
        scaled_spread = _propagate_spread(
            scaled_mean, m if isinstance(m, (int, float)) else float("nan"),
            s_val, factor_rel_err_sq,
        )
    new = list(pt)
    new[1] = scaled_mean
    new[2] = scaled_spread
    return tuple(new)


def pts_to_mean_by_t(pts) -> Dict[float, float]:
    """Build a ``{t: mean}`` dict from an AggPoint list, skipping NaN means."""
    out: Dict[float, float] = {}
    for pt in pts or ():
        t = float(pt[0])
        m = pt[1]
        if isinstance(m, (int, float)) and math.isfinite(m):
            out[t] = float(m)
    return out


def _match_control_mean(t: float, control_means: Dict[float, float]) -> Optional[float]:
    """Resolve the control mean at timepoint *t* under relative tolerance.

    Uses a relative-tolerance match (``abs(a - b) < 1e-3 * max(1, |a|, |b|)``)
    instead of the previous absolute 1e-6 — the bar tp combo formats
    its items with ``:.4g`` so fractional timepoints lose precision
    when parsed back, which the old tolerance rejected.
    """
    if not control_means:
        return None
    if t in control_means:
        return control_means[t]
    for ct, cm in control_means.items():
        if abs(ct - t) < _TP_REL_TOL * max(1.0, abs(ct), abs(t)):
            return cm
    return None


def normalize_pts(
    pts,
    *,
    control_means: Optional[Dict[float, float]] = None,
    control_stats: Optional[Dict[float, Tuple[float, float]]] = None,
    control_frac_stats: Optional[Dict[float, Tuple[float, float]]] = None,
    control_n_stats: Optional[Dict[float, Tuple[float, float]]] = None,
    use_t0: bool = False,
    baseline_tp: Optional[float] = None,
    op: str = "ratio",
    op_ctrl: Optional[str] = None,
    op_t0: Optional[str] = None,
    miss_sink: Optional[Set[float]] = None,
) -> list:
    """Apply optional vs-control and/or vs-t0 normalization to AggPoint list.

    *control_means* — ``{t: mean}`` for the control. Each point's mean is
    divided by the control's mean at the matching timepoint. Points
    with no matching control sample produce NaN means.

    *control_stats* — richer ``{t: (mean, spread)}`` variant. When
    supplied, takes precedence over *control_means* and the resulting
    spread folds the control's relative error in quadrature. Pass this
    for proper error propagation; pass *control_means* alone for the
    legacy "treat denominator as exact" behaviour.

    *control_frac_stats* — ``{t: (frac, frac_spread)}`` for the control.
    Normalizes the fraction field (index [3]) and its spread (index [6])
    using the same control/vs_t0 logic as the mean. Used by line-plot FC
    normalization to scale fraction panels.

    *control_n_stats* — ``{t: (n_above, 0.0)}`` for the control.
    Normalizes the N_above field (index [4]) using control logic.
    Spread is always 0 since N_above is a count. Used by line-plot FC
    to scale N panels. Not subject to vs_t0 baseline normalization.

    *use_t0* — when True, finds the member's earliest finite mean and
    divides every point by it after the control step. The earliest
    point itself becomes 1.0 ± propagated spread by construction.
    The baseline's OWN spread (taken from the same AggPoint) is folded
    into the propagation. Also applies to frac/frac_spread if control_frac_stats
    is present.

    *miss_sink* — optional set the caller passes in to collect the
    timepoints where the control had no sample (so the renderer can
    surface a single end-of-redraw status warning rather than silently
    drop bars / points).

    Stacking: with both axes active the result is
    ``(X(t) / C(t)) / (X(0) / C(0))`` (ΔΔCt-style), because the t0
    step uses the post-control values.
    """
    if not pts:
        return []

    # Per-axis ops fall through to the legacy ``op`` when unspecified, so
    # callers that still pass a single ``op`` keep their old contract.
    effective_op_ctrl = op_ctrl if op_ctrl is not None else op
    effective_op_t0 = op_t0 if op_t0 is not None else op

    # Use control_stats when provided; fall back to control_means (with
    # zero spread per tp) for the legacy callers / tests.
    effective_stats: Optional[Dict[float, Tuple[float, float]]] = None
    if control_stats:
        effective_stats = control_stats
    elif control_means:
        effective_stats = {t: (m, 0.0) for t, m in control_means.items()}

    # Step 1: control normalization.
    if effective_stats:
        stage1: list = []
        means_only = {t: ms[0] for t, ms in effective_stats.items()}
        for pt in pts:
            t = float(pt[0])
            cm = _match_control_mean(t, means_only)
            if cm is None:
                # No control sample at this t — drop mean to NaN and
                # record the miss so the caller can surface it.
                if miss_sink is not None:
                    miss_sink.add(t)
                new = list(pt)
                new[1] = float("nan")
                new[2] = 0.0
                stage1.append(tuple(new))
            else:
                # Pick the (mean, spread) that _match_control_mean
                # resolved — relative tolerance on the tp key.
                ctrl_mean, ctrl_spread = effective_stats.get(
                    t, (cm, 0.0)
                ) if t in effective_stats else next(
                    ((m, s) for ct, (m, s) in effective_stats.items()
                     if abs(ct - t) < _TP_REL_TOL * max(1.0, abs(ct), abs(t))),
                    (cm, 0.0),
                )
                if effective_op_ctrl == "subtract":
                    stage1.append(_subtract_pt(pt, ctrl_mean,
                                               shift_spread=ctrl_spread or 0.0))
                else:
                    rel_err_sq = _rel_err_sq(ctrl_mean, ctrl_spread)
                    stage1.append(_scale_pt(pt, ctrl_mean,
                                             factor_rel_err_sq=rel_err_sq))
    else:
        stage1 = list(pts)

    # Step 2: baseline normalization (uses post-control values). The
    # baseline's own spread folds into every downstream point's spread via
    # the same quadrature formula.
    #
    # ``baseline_tp`` (when finite) names the exact timepoint to use as the
    # divisor — its mean / spread are pulled from the point at that tp
    # (with a relative tolerance to absorb float-printing drift). When
    # ``baseline_tp`` is ``None`` we fall back to the earliest finite
    # non-zero mean — the original "vs t0" semantics — so saved state
    # that only carries ``use_t0=True`` keeps working.
    if use_t0:
        baseline = None
        baseline_spread = 0.0
        if baseline_tp is not None and math.isfinite(baseline_tp):
            tgt = float(baseline_tp)
            for pt in stage1:
                t = float(pt[0])
                if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                    m = pt[1]
                    if isinstance(m, (int, float)) and math.isfinite(m) and m != 0:
                        baseline = float(m)
                        s = pt[2] if len(pt) > 2 else 0.0
                        baseline_spread = (
                            float(s) if isinstance(s, (int, float)) and math.isfinite(s)
                            else 0.0
                        )
                    break
        if baseline is None:
            # Either no ``baseline_tp`` was supplied or the requested
            # timepoint had no finite non-zero mean — fall through to
            # earliest-finite behaviour so the user still gets a useful
            # normalisation rather than a silent no-op.
            for pt in sorted(stage1, key=lambda p: p[0]):
                m = pt[1]
                if isinstance(m, (int, float)) and math.isfinite(m) and m != 0:
                    baseline = float(m)
                    s = pt[2] if len(pt) > 2 else 0.0
                    baseline_spread = (
                        float(s) if isinstance(s, (int, float)) and math.isfinite(s)
                        else 0.0
                    )
                    break
        if baseline is None:
            stage2 = stage1
        else:
            if effective_op_t0 == "subtract":
                stage2 = [_subtract_pt(pt, baseline, shift_spread=baseline_spread)
                        for pt in stage1]
            else:
                baseline_rel_err_sq = _rel_err_sq(baseline, baseline_spread)
                stage2 = [_scale_pt(pt, baseline,
                                  factor_rel_err_sq=baseline_rel_err_sq)
                        for pt in stage1]
    else:
        stage2 = stage1

    # Step 3: Fraction normalization (indices [3] / [6])
    if control_frac_stats:
        stage3: list = []
        frac_means = {t: ms[0] for t, ms in control_frac_stats.items()}
        for pt in stage2:
            t = float(pt[0])
            cm = _match_control_mean(t, frac_means)
            if cm is None:
                new = list(pt)
                while len(new) <= 6:
                    new.append(0.0)
                new[3] = float("nan")
                new[6] = 0.0
                stage3.append(tuple(new))
            else:
                ctrl_frac, ctrl_frac_spread = control_frac_stats.get(
                    t, (cm, 0.0)
                ) if t in control_frac_stats else next(
                    ((f, fs) for ct, (f, fs) in control_frac_stats.items()
                     if abs(ct - t) < _TP_REL_TOL * max(1.0, abs(ct), abs(t))),
                    (cm, 0.0),
                )
                if effective_op_ctrl == "subtract":
                    stage3.append(_subtract_field(pt, 3, 6, ctrl_frac,
                                                   shift_spread=ctrl_frac_spread or 0.0))
                else:
                    frac_rel_err_sq = _rel_err_sq(ctrl_frac, ctrl_frac_spread)
                    stage3.append(_scale_field(pt, 3, 6, ctrl_frac,
                                               factor_rel_err_sq=frac_rel_err_sq))
    else:
        stage3 = stage2

    # Step 4: Fraction vs_t0 baseline normalization (indices [3] / [6])
    if use_t0 and control_frac_stats:
        frac_baseline = None
        frac_baseline_spread = 0.0
        if baseline_tp is not None and math.isfinite(baseline_tp):
            tgt = float(baseline_tp)
            for pt in stage3:
                t = float(pt[0])
                if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                    f = pt[3] if len(pt) > 3 else 0.0
                    if isinstance(f, (int, float)) and math.isfinite(f) and f != 0:
                        frac_baseline = float(f)
                        fs = pt[6] if len(pt) > 6 else 0.0
                        frac_baseline_spread = (
                            float(fs) if isinstance(fs, (int, float)) and math.isfinite(fs)
                            else 0.0
                        )
                    break
        if frac_baseline is None:
            for pt in sorted(stage3, key=lambda p: p[0]):
                f = pt[3] if len(pt) > 3 else 0.0
                if isinstance(f, (int, float)) and math.isfinite(f) and f != 0:
                    frac_baseline = float(f)
                    fs = pt[6] if len(pt) > 6 else 0.0
                    frac_baseline_spread = (
                        float(fs) if isinstance(fs, (int, float)) and math.isfinite(fs)
                        else 0.0
                    )
                    break
        if frac_baseline is not None:
            if effective_op_t0 == "subtract":
                stage3 = [_subtract_field(pt, 3, 6, frac_baseline,
                                         shift_spread=frac_baseline_spread)
                         for pt in stage3]
            else:
                frac_baseline_rel_err_sq = _rel_err_sq(frac_baseline, frac_baseline_spread)
                stage3 = [_scale_field(pt, 3, 6, frac_baseline,
                                      factor_rel_err_sq=frac_baseline_rel_err_sq)
                         for pt in stage3]

    # Step 5: N_above normalization (index [4], no spread)
    if control_n_stats:
        stage4: list = []
        n_means = {t: ms[0] for t, ms in control_n_stats.items()}
        for pt in stage3:
            t = float(pt[0])
            cm = _match_control_mean(t, n_means)
            if cm is None:
                new = list(pt)
                while len(new) <= 4:
                    new.append(0.0)
                new[4] = float("nan")
                stage4.append(tuple(new))
            else:
                ctrl_n = control_n_stats.get(
                    t, (cm, 0.0)
                )[0] if t in control_n_stats else next(
                    (n for ct, (n, _) in control_n_stats.items()
                     if abs(ct - t) < _TP_REL_TOL * max(1.0, abs(ct), abs(t))),
                    cm,
                )
                if effective_op_ctrl == "subtract":
                    new = list(pt)
                    while len(new) <= 4:
                        new.append(0.0)
                    n = pt[4] if len(pt) > 4 else 0.0
                    if isinstance(n, (int, float)) and math.isfinite(n):
                        new[4] = float(n) - float(ctrl_n)
                    else:
                        new[4] = float("nan")
                    stage4.append(tuple(new))
                else:
                    if ctrl_n == 0 or not math.isfinite(ctrl_n):
                        new = list(pt)
                        while len(new) <= 4:
                            new.append(0.0)
                        new[4] = float("nan")
                        stage4.append(tuple(new))
                    else:
                        new = list(pt)
                        while len(new) <= 4:
                            new.append(0.0)
                        n = pt[4] if len(pt) > 4 else 0.0
                        if isinstance(n, (int, float)) and math.isfinite(n):
                            new[4] = float(n) / float(ctrl_n)
                        else:
                            new[4] = float("nan")
                        stage4.append(tuple(new))
    else:
        stage4 = stage3

    return stage4


# ── State accessors / app integration ───────────────────────────────────────

def fold_change_state(app) -> Tuple[bool, str, bool]:
    """Return the active ``(vs_control_on, control_label, vs_t0_on)`` triple.

    Used by both the on-screen plot tabs and the per-tab CSV exporter so the
    plot and the export stay in sync.
    """
    return (
        bool(app._app_state.plot.fc_vs_control_on),
        str(app._app_state.plot.fc_control_label or ""),
        bool(app._app_state.plot.fc_vs_t0_on),
    )


def _pt_frac(pt) -> Tuple[Optional[float], float]:
    """Extract ``(frac, frac_spread)`` from an AggPoint, with NaN guarding."""
    if len(pt) < 4:
        return None, 0.0
    f = pt[3]
    if not isinstance(f, (int, float)) or not math.isfinite(f):
        return None, 0.0
    fs = pt[6] if len(pt) >= 7 else 0.0
    fs = float(fs) if isinstance(fs, (int, float)) and math.isfinite(fs) else 0.0
    return float(f), fs


def baseline_from_points(
    pts, baseline_tp: Optional[float] = None,
) -> Tuple[Optional[float], float]:
    """Pull the ``(mean, spread)`` baseline out of an AggPoint sequence.

    ``baseline_tp`` (when finite) picks the point at that timepoint;
    otherwise the earliest finite non-zero mean wins (the original "vs
    t0" semantics). Returns ``(None, 0.0)`` when no point qualifies, so
    callers can treat that as "no baseline available" without divide-by-
    zero or NaN propagation surprises.
    """
    if not pts:
        return None, 0.0
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for pt in pts:
            t = float(pt[0])
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                m = pt[1]
                if isinstance(m, (int, float)) and math.isfinite(m) and m != 0:
                    s = pt[2] if len(pt) > 2 else 0.0
                    return (
                        float(m),
                        float(s) if isinstance(s, (int, float)) and math.isfinite(s) else 0.0,
                    )
                # Matched tp but its mean isn't finite/non-zero — fall
                # through to earliest so the bar still normalises.
                break
        # Requested baseline tp wasn't found (or had bad data); fall
        # through to earliest-finite — matches normalize_pts so the
        # bar plot doesn't silently no-op when the user picks a tp
        # the per-well series doesn't sample.
    for pt in sorted(pts, key=lambda p: p[0]):
        m = pt[1]
        if isinstance(m, (int, float)) and math.isfinite(m) and m != 0:
            s = pt[2] if len(pt) > 2 else 0.0
            return (
                float(m),
                float(s) if isinstance(s, (int, float)) and math.isfinite(s) else 0.0,
            )
    return None, 0.0


def baseline_from_points_resolved(
    pts, baseline_tp: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float], float]:
    """Like :func:`baseline_from_points` but also returns the resolved tp.

    Returns ``(tp, mean, spread)``. ``tp`` is ``None`` when no baseline
    qualifies. Used by the bar's per-well path so the caller can look up
    the control's value at the SAME tp the per-well baseline used."""
    if not pts:
        return None, None, 0.0
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for pt in pts:
            t = float(pt[0])
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                m = pt[1]
                if isinstance(m, (int, float)) and math.isfinite(m) and m != 0:
                    s = pt[2] if len(pt) > 2 else 0.0
                    return (
                        t,
                        float(m),
                        float(s) if isinstance(s, (int, float)) and math.isfinite(s) else 0.0,
                    )
                break
    for pt in sorted(pts, key=lambda p: p[0]):
        m = pt[1]
        if isinstance(m, (int, float)) and math.isfinite(m) and m != 0:
            s = pt[2] if len(pt) > 2 else 0.0
            return (
                float(pt[0]),
                float(m),
                float(s) if isinstance(s, (int, float)) and math.isfinite(s) else 0.0,
            )
    return None, None, 0.0


def baseline_frac_from_points_resolved(
    pts, baseline_tp: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float], float]:
    """Frac counterpart of :func:`baseline_from_points_resolved` —
    returns ``(tp, frac, frac_spread)`` with ``tp=None`` for the no-match case."""
    if not pts:
        return None, None, 0.0
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for pt in pts:
            t = float(pt[0])
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                f, fs = _pt_frac(pt)
                if f is not None and f != 0:
                    return t, f, fs
                break
    for pt in sorted(pts, key=lambda p: p[0]):
        f, fs = _pt_frac(pt)
        if f is not None and f != 0:
            return float(pt[0]), f, fs
    return None, None, 0.0


def baseline_frac_from_points(
    pts, baseline_tp: Optional[float] = None,
) -> Tuple[Optional[float], float]:
    """Pull the ``(frac, frac_spread)`` baseline out of an AggPoint sequence.

    Fraction-on counterpart of :func:`baseline_from_points`. Same fall-through
    semantics: matched ``baseline_tp`` wins; otherwise the earliest finite tp
    is used. Returns ``(None, 0.0)`` when nothing qualifies — that signals
    "no baseline available" so the fraction bar passes through raw rather than
    div-by-zeroing.
    """
    if not pts:
        return None, 0.0
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for pt in pts:
            t = float(pt[0])
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                f, fs = _pt_frac(pt)
                if f is not None and f != 0:
                    return f, fs
                break
    for pt in sorted(pts, key=lambda p: p[0]):
        f, fs = _pt_frac(pt)
        if f is not None and f != 0:
            return f, fs
    return None, 0.0


def _pt_n_above(pt, per_fov_spread: bool = False) -> Tuple[float, float]:
    """Extract ``(n_above, n_above_spread)`` from an AggPoint."""
    if per_fov_spread:
        n = float(pt[7]) if len(pt) >= 8 else 0.0
        ns = float(pt[8]) if len(pt) >= 9 else 0.0
        return n, ns
    n = float(int(pt[4])) if len(pt) >= 5 else 0.0
    return n, 0.0


def baseline_n_from_points_resolved(
    pts, *, per_fov_spread: bool = False, baseline_tp: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float], float]:
    """``(tp, n_above, n_above_spread)`` for the member's baseline tp.

    N-events counterpart of :func:`baseline_from_points_resolved`.
    """
    if not pts:
        return None, None, 0.0
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for pt in pts:
            t = float(pt[0])
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                n, ns = _pt_n_above(pt, per_fov_spread)
                if n != 0:
                    return t, n, ns
                break
    for pt in sorted(pts, key=lambda p: p[0]):
        n, ns = _pt_n_above(pt, per_fov_spread)
        if n != 0:
            return float(pt[0]), n, ns
    return None, None, 0.0


def member_n_series(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, Tuple[float, float]]:
    """``{t: (n_above, n_above_spread)}`` for a member.

    N-events counterpart of :func:`member_frac_series`. Same rset /
    solo-well dispatch.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(app, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(app,
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        out: Dict[float, Tuple[float, float]] = {}
        for t in tps:
            if per_fov_spread:
                _, _, _, _, n_mean, n_spread = compute_rep_per_fov_stats(app,
                    rset, t, threshold, use_sem,
                )
                out[t] = (float(n_mean),
                          float(n_spread) if math.isfinite(n_spread) else 0.0)
            else:
                n_total = compute_rep_n_above(app, rset, t)
                out[t] = (float(n_total), 0.0)
        return out
    if label in (app._app_state.dataset.well_paths or {}):
        pts = aggregate_well(app,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        return {float(pt[0]): _pt_n_above(pt, per_fov_spread) for pt in pts}
    return {}


def control_n_at_for_bar(
    app, control_label: str, target_t: float, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(n_above, n_above_spread)`` of the control at *target_t* for the
    bar's N-events panel. Returns ``None`` when the control can't be
    resolved or has no sample at *target_t*."""
    series = member_n_series(
        app, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if target_t in series:
        return series[target_t]
    for ct, val in series.items():
        if abs(ct - target_t) < _TP_REL_TOL * max(1.0, abs(ct), abs(target_t)):
            return val
    return None


def member_first_tp_n_resolved(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    baseline_tp: Optional[float] = None,
) -> Optional[Tuple[float, float, float]]:
    """``(tp, n_above, n_above_spread)`` for the member's baseline timepoint.

    N-events counterpart of :func:`member_first_tp_frac_resolved` with the
    same matched-tp + fall-through-to-earliest semantics.
    """
    series = member_n_series(
        app, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for t, ns in series.items():
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                return (float(t), ns[0], ns[1])
    earliest = min(series)
    ns = series[earliest]
    return (float(earliest), ns[0], ns[1])


def parse_fc_op(app) -> str:
    """Return the user's chosen fold-change operator (``"ratio"`` or
    ``"subtract"``). Defaults to ``"ratio"`` for back-compat with
    ``PlotState`` snapshots that don't carry the field.

    Legacy single-op accessor — returns the control-axis op. New callers
    should use :func:`parse_fc_op_ctrl` / :func:`parse_fc_op_t0` to read
    the per-axis ops independently.
    """
    op = getattr(app._app_state.plot, "fc_op", "ratio") or "ratio"
    return op if op in ("ratio", "subtract") else "ratio"


def parse_fc_op_ctrl(app) -> str:
    """Return the user's chosen vs-control operator (``"ratio"`` or
    ``"subtract"``)."""
    op = getattr(app._app_state.plot, "fc_op_ctrl", "ratio") or "ratio"
    return op if op in ("ratio", "subtract") else "ratio"


def parse_fc_op_t0(app) -> str:
    """Return the user's chosen vs-baseline-tp operator (``"ratio"`` or
    ``"subtract"``)."""
    op = getattr(app._app_state.plot, "fc_op_t0", "ratio") or "ratio"
    return op if op in ("ratio", "subtract") else "ratio"


def parse_fc_order(app) -> str:
    """Return the user's chosen FC application order (``"ctrl_first"`` or
    ``"t0_first"``). Defaults to ``"ctrl_first"`` for back-compat."""
    order = getattr(app._app_state.plot, "fc_order", "ctrl_first") or "ctrl_first"
    return order if order in ("ctrl_first", "t0_first") else "ctrl_first"


def parse_baseline_tp(app) -> Optional[float]:
    """Resolve ``PlotState.fc_baseline_tp`` (a tp string) to a float.

    Returns ``None`` when no baseline tp is set, when the saved string
    isn't parseable as a float, or when the user has parameterised
    "earliest available point" via an empty value (which ``normalize_pts``
    handles as the original ``vs t0`` semantics).
    """
    raw = getattr(app._app_state.plot, "fc_baseline_tp", "") or ""
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def is_active(app) -> bool:
    vs_ctrl, _, vs_t0 = fold_change_state(app)
    return vs_ctrl or vs_t0


def fold_change_suffix(vs_control_on: bool, vs_t0_on: bool,
                       control_label: str = "") -> str:
    """Compact " (fold-change …)" label fragment for axis titles."""
    parts: List[str] = []
    if vs_control_on:
        parts.append(f"vs {control_label}" if control_label else "vs control")
    if vs_t0_on:
        parts.append("vs t0")
    if not parts:
        return ""
    return "  (fold change " + ", ".join(parts) + ")"


# ── Control-series helpers ──────────────────────────────────────────────────

_WELL_DISAMBIG_SUFFIX = " (well)"


def resolve_control_wells(app, control_label: str) -> List[str]:
    """Translate a control selection string into a list of loaded well tokens.

    Resolution order:
      1. Suffixed " (well)" — explicit "this is a well token". The suffix
         is stripped and the bare token is looked up in ``_well_paths``.
      2. ReplicateSet name match in ``_selections``.
      3. Bare well token in ``_well_paths``.

    The suffix exists for the rep-set-name-vs-well-token collision case
    (someone named a rep-set "A01"): the UI combo emits the suffixed
    form for the well entry, so picking it bypasses the rep-set
    precedence in (2). Unknown / empty selections return ``[]``.
    """
    label = str(control_label or "").strip()
    if not label:
        return []
    if label.endswith(_WELL_DISAMBIG_SUFFIX):
        bare = label[: -len(_WELL_DISAMBIG_SUFFIX)]
        if bare in (app._app_state.dataset.well_paths or {}):
            return [bare]
        return []
    for s in (app._app_state.selection.selections or []):
        if s.get("name") == label:
            wells = [w for w in (s.get("wells") or []) if w in app._app_state.dataset.well_paths]
            if wells:
                return wells
    if label in (app._app_state.dataset.well_paths or {}):
        return [label]
    return []


def control_pts_for_line(app, control_label: str, *, threshold: float,
                         val_col: str,
                         fluor_gates) -> list:
    """Aggregate the control selection as a single pooled series.

    Used by the line-plot path where we want a control mean at every
    timepoint the data exposes. Returns an AggPoint list (or [] if the
    control is unresolved).
    """
    wells = resolve_control_wells(app, control_label)
    if not wells:
        return []
    return aggregate_group(app,
        wells, threshold=threshold, use_sem=False,
        val_col=val_col,
        fluor_gates=fluor_gates,
    )


def _label_to_replicate_set(app, label: str):
    """Resolve a control label to a ReplicateSet OR ``None``.

    Returns the ReplicateSet view of a Sample-Definitions selection when
    the label matches one (and the selection has ≥1 loaded well). Returns
    ``None`` for unknown labels and bare well tokens — the caller should
    fall back to per-well handling in the well-token case.
    """
    if not label:
        return None
    for s in (app._app_state.selection.selections or []):
        if s.get("name") == label:
            wells = [w for w in (s.get("wells") or [])
                     if w in app._app_state.dataset.well_paths]
            if not wells:
                return None
            from well_viewer.domain.batch_models import ReplicateSet
            return ReplicateSet(label, wells)
    return None


def member_mean_series(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, float]:
    """``{t: mean}`` for a member (rep-set name or well token).

    Uses the SAME stat the bar / curve renderer uses for that kind of
    member:

      * Rep-set, default mode → ``_compute_rep_stats`` (mean of per-well
        means at each timepoint).
      * Rep-set, Aggregate-FOVs mode → ``_compute_rep_per_fov_stats``
        (per-FOV pool at each timepoint).
      * Well token → ``_aggregate_well`` (the well's own per-cell mean).

    This is the fix for the math inconsistency of the original
    fold-change: the bar plot's numerator (e.g. mean-of-means via
    ``_compute_rep_stats``) was being divided by a pooled-cells mean
    via ``_aggregate_group`` — two different statistics, so the ratio
    wasn't a clean fold-change. Both sides now use the same stat.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(app, label)
    if rset is not None:
        # Collect the union of timepoints across the rep-set's wells.
        # We use the per-well agg (use_sem=False; spread isn't used here)
        # only to enumerate the timepoint axis.
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(app,
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        out: Dict[float, float] = {}
        for t in tps:
            if per_fov_spread:
                gm, *_ = compute_rep_per_fov_stats(app,
                    rset, t, threshold, use_sem,
                )
            else:
                gm, *_ = compute_rep_stats(app,
                    rset, t, threshold, use_sem,
                )
            if math.isfinite(gm):
                out[t] = float(gm)
        return out
    # Bare well token.
    if label in (app._app_state.dataset.well_paths or {}):
        pts = aggregate_well(app,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        return {float(pt[0]): float(pt[1])
                for pt in pts
                if isinstance(pt[1], (int, float)) and math.isfinite(pt[1])}
    return {}


def control_mean_at_for_bar(
    app, control_label: str, target_t: float, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[float]:
    """Control mean at *target_t* using the same stat the bar plot uses.

    For a rep-set control: mean-of-per-well-means (or per-FOV pool when
    Aggregate-FOVs is on) — matches the rep-set bar's numerator. For a
    single-well control: the well's own mean — matches the per-well
    bar's numerator. Returns ``None`` when the control can't be resolved
    or has no sample at *target_t*.
    """
    series = member_mean_series(
        app, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    return _match_control_mean(target_t, series)


def build_cell_scaling(
    app, wells, *, fc_state: Tuple[bool, str, bool], target_t: float,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[str, float]:
    """Per-well multiplicative denominator for per-cell violin/beeswarm plots.

    Returns ``{well: factor}`` where each cell's value should be DIVIDED
    by ``factor`` before plotting. With no fold-change active, factor is
    1.0 for every well (the dict acts as a no-op).

    Stacking matches :func:`normalize_pts`: factor = control_mean * t0_mean,
    where:

      * control_mean — single scalar across all wells, from
        :func:`control_mean_at_for_bar` (uses the bar's stat). ``None``
        when vs-control is off; the entire well is set to NaN when
        vs-control is requested but unresolved (no silent fall-back).
      * t0_mean — each well's own earliest mean from
        :func:`member_first_tp_value`. Returns NaN for that well when
        no t0 sample exists.
    """
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
    control_mean: Optional[float] = None
    control_missing = False
    if fc_vs_ctrl and fc_ctrl_lbl:
        control_mean = control_mean_at_for_bar(
            app, fc_ctrl_lbl, target_t,
            threshold=threshold, val_col=val_col,
            use_sem=use_sem, per_fov_spread=per_fov_spread,
            fluor_gates=fluor_gates,
        )
        if control_mean is None:
            control_missing = True

    out: Dict[str, float] = {}
    for w in wells:
        if control_missing:
            out[w] = float("nan")
            continue
        factor = 1.0
        if control_mean is not None:
            factor *= control_mean
        if fc_vs_t0:
            t0 = member_first_tp_value(
                app, w,
                threshold=threshold, val_col=val_col, use_sem=use_sem,
                per_fov_spread=per_fov_spread,
                fluor_gates=fluor_gates,
            )
            if t0 is None or not math.isfinite(t0) or t0 == 0:
                out[w] = float("nan")
                continue
            factor *= t0
        out[w] = factor
    return out


def member_first_tp_value(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[float]:
    """Earliest finite mean for a member (using the member's own stat).

    The t0 baseline counterpart to :func:`control_mean_at_for_bar`. Used
    for the ``vs t0`` axis so the baseline divisor matches the numerator's
    statistic — for a rep-set member this is mean-of-per-well-means at
    the earliest tp where the rep-set has data, not the pool-of-cells
    mean of the prior implementation.
    """
    series = member_mean_series(
        app, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    earliest = min(series)
    return series[earliest]


def member_stats_series(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, Tuple[float, float]]:
    """``{t: (mean, spread)}`` for a member — like :func:`member_mean_series`
    but also returns the spread so callers can propagate error properly.

    The spread is read from the same helper that produces the mean:
    ``_compute_rep_stats[_per_fov]`` for rep-set members, the
    ``_aggregate_well`` AggPoint's spread field for solo wells.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(app, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(app,
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        out: Dict[float, Tuple[float, float]] = {}
        for t in tps:
            if per_fov_spread:
                gm, gerr, *_ = compute_rep_per_fov_stats(app,
                    rset, t, threshold, use_sem,
                )
            else:
                gm, gerr, *_ = compute_rep_stats(app,
                    rset, t, threshold, use_sem,
                )
            if math.isfinite(gm):
                spread = float(gerr) if (
                    isinstance(gerr, (int, float)) and math.isfinite(gerr)
                ) else 0.0
                out[t] = (float(gm), spread)
        return out
    if label in (app._app_state.dataset.well_paths or {}):
        pts = aggregate_well(app,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        out = {}
        for pt in pts:
            m = pt[1]
            if isinstance(m, (int, float)) and math.isfinite(m):
                s = pt[2] if len(pt) > 2 else 0.0
                spread = float(s) if (
                    isinstance(s, (int, float)) and math.isfinite(s)
                ) else 0.0
                out[float(pt[0])] = (float(m), spread)
        return out
    return {}


def member_frac_stats_series(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, Tuple[float, float]]:
    """``{t: (frac, frac_spread)}`` for a member — fraction above threshold + spread.

    Mirrors :func:`member_stats_series` but extracts fraction fields instead of mean.
    Used by FC normalization on line plots to scale fraction panels.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(app, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(app,
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        out: Dict[float, Tuple[float, float]] = {}
        for t in tps:
            if per_fov_spread:
                *_, gf, ferr = compute_rep_per_fov_stats(app,
                    rset, t, threshold, use_sem,
                )
            else:
                *_, gf, ferr = compute_rep_stats(app,
                    rset, t, threshold, use_sem,
                )
            if math.isfinite(gf):
                spread = float(ferr) if (
                    isinstance(ferr, (int, float)) and math.isfinite(ferr)
                ) else 0.0
                out[t] = (float(gf), spread)
        return out
    if label in (app._app_state.dataset.well_paths or {}):
        pts = aggregate_well(app,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        out = {}
        for pt in pts:
            f = pt[3]
            if isinstance(f, (int, float)) and math.isfinite(f):
                s = pt[6] if len(pt) > 6 else 0.0
                spread = float(s) if (
                    isinstance(s, (int, float)) and math.isfinite(s)
                ) else 0.0
                out[float(pt[0])] = (float(f), spread)
        return out
    return {}


def member_n_stats_series(
    app, label: str, *,
    threshold: float, val_col: str,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, Tuple[float, float]]:
    """``{t: (n_above, 0.0)}`` for a member — N above threshold (spread is always 0).

    Mirrors :func:`member_stats_series` but extracts N_above counts.
    Used by FC normalization on line plots to scale N panels.
    Spread is always 0.0 since N_above is a total count, not a mean.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(app, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(app,
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        out: Dict[float, Tuple[float, float]] = {}
        for t in tps:
            n_above = compute_rep_n_above(app,
                rset, t, threshold,
            )
            if isinstance(n_above, int) and n_above >= 0:
                out[t] = (float(n_above), 0.0)
        return out
    if label in (app._app_state.dataset.well_paths or {}):
        pts = aggregate_well(app,
            label, threshold=threshold, use_sem=False,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        out = {}
        for pt in pts:
            n = pt[4]
            if isinstance(n, (int, float)) and math.isfinite(n):
                out[float(pt[0])] = (float(n), 0.0)
        return out
    return {}


def control_stats_at_for_bar(
    app, control_label: str, target_t: float, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(mean, spread)`` of the control at *target_t* using the bar's stat.

    Stats-returning counterpart to :func:`control_mean_at_for_bar` —
    the spread is needed for proper error propagation in
    :func:`scale_bar_value`. Returns ``None`` when the control can't
    be resolved or has no sample at *target_t*.
    """
    series = member_stats_series(
        app, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if target_t in series:
        return series[target_t]
    for ct, val in series.items():
        if abs(ct - target_t) < _TP_REL_TOL * max(1.0, abs(ct), abs(target_t)):
            return val
    return None


def control_stats_at(
    app, control_label: str, target_t: float, *,
    threshold: float, val_col: str,
    fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(mean, spread)`` of the pooled control at *target_t* (batch stat).

    Pool-of-cells variant used by the batch bar exporter — matches the
    batch numerator (also pool-of-cells via ``_aggregate_group``). The
    plot-tab uses :func:`control_stats_at_for_bar` instead.
    """
    pts = control_pts_for_line(
        app, control_label, threshold=threshold, val_col=val_col,
        fluor_gates=fluor_gates,
    )
    if not pts:
        return None
    for pt in pts:
        if abs(pt[0] - target_t) < _TP_REL_TOL * max(1.0, abs(pt[0]),
                                                     abs(target_t)):
            m = pt[1]
            if isinstance(m, (int, float)) and math.isfinite(m):
                s = pt[2] if len(pt) > 2 else 0.0
                spread = float(s) if (
                    isinstance(s, (int, float)) and math.isfinite(s)
                ) else 0.0
                return (float(m), spread)
    return None


def member_first_tp_stats(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    baseline_tp: Optional[float] = None,
) -> Optional[Tuple[float, float]]:
    """``(mean, spread)`` for the member's baseline timepoint.

    Returns the stats at ``baseline_tp`` when supplied (matched with a
    relative tolerance to absorb float-printing drift) and falls back to
    the member's earliest finite tp otherwise — matching the
    ``normalize_pts`` line-scope semantics so a user who picks a
    baseline tp the bar's rep-set doesn't sample gets the same fallback
    the line scope already gives them, instead of a silent no-op that
    looks indistinguishable from FC-off. Returns ``None`` only for
    unknown / empty members.
    """
    series = member_stats_series(
        app, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for t, ms in series.items():
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                return ms
        # Requested baseline tp not present for this member — fall
        # through to earliest-finite so the bar still normalises, same
        # as ``normalize_pts``. Without this the FC toggle is a silent
        # no-op visually (see test_collect_bar_items_baseline_missing
        # _tp_falls_back_silently for the regression).
    earliest = min(series)
    return series[earliest]


def member_first_tp_stats_resolved(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    baseline_tp: Optional[float] = None,
) -> Optional[Tuple[float, float, float]]:
    """Like :func:`member_first_tp_stats` but also returns the resolved tp.

    Returns ``(tp, mean, spread)`` so the bar plot can look up the control's
    value at the SAME baseline tp the member used — needed for correct
    post-control baseline stacking when ops are mixed (e.g. ratio-control
    + subtract-baseline). Returns ``None`` only for unknown / empty members.
    """
    series = member_stats_series(
        app, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for t, ms in series.items():
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                return (float(t), ms[0], ms[1])
    earliest = min(series)
    ms = series[earliest]
    return (float(earliest), ms[0], ms[1])


# ── Bar-plot fraction-on FC helpers ─────────────────────────────────────────
#
# The "OnFraction" panel (fraction of cells above the gate) is its own bar
# row. Mean and frac are independent quantities; FC for frac normalises
# against the control / baseline's frac, not its mean. These helpers mirror
# the mean-side trio so ``collect_bar_items`` can scale the frac panel the
# same way it scales the mean panel.


def member_frac_series(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, Tuple[float, float]]:
    """``{t: (frac, frac_spread)}`` for a member.

    Fraction-on counterpart of :func:`member_stats_series`. Same rset /
    solo-well dispatch.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(app, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(app,
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        out: Dict[float, Tuple[float, float]] = {}
        for t in tps:
            if per_fov_spread:
                _, _, gf, gerr_f, *_ = compute_rep_per_fov_stats(app,
                    rset, t, threshold, use_sem,
                )
            else:
                _, _, gf, gerr_f = compute_rep_stats(app,
                    rset, t, threshold, use_sem,
                )
            if isinstance(gf, (int, float)) and math.isfinite(gf):
                fs = float(gerr_f) if (
                    isinstance(gerr_f, (int, float)) and math.isfinite(gerr_f)
                ) else 0.0
                out[t] = (float(gf), fs)
        return out
    if label in (app._app_state.dataset.well_paths or {}):
        pts = aggregate_well(app,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        out = {}
        for pt in pts:
            f, fs = _pt_frac(pt)
            if f is not None:
                out[float(pt[0])] = (f, fs)
        return out
    return {}


def control_frac_at_for_bar(
    app, control_label: str, target_t: float, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(frac, frac_spread)`` of the control at *target_t* for the bar's frac
    panel. Returns ``None`` when the control can't be resolved or has no
    sample at *target_t*."""
    series = member_frac_series(
        app, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if target_t in series:
        return series[target_t]
    for ct, val in series.items():
        if abs(ct - target_t) < _TP_REL_TOL * max(1.0, abs(ct), abs(target_t)):
            return val
    return None


def member_first_tp_frac(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    baseline_tp: Optional[float] = None,
) -> Optional[Tuple[float, float]]:
    """``(frac, frac_spread)`` for the member's baseline timepoint.

    Fraction-on counterpart of :func:`member_first_tp_stats` with the same
    matched-tp + fall-through-to-earliest semantics.
    """
    series = member_frac_series(
        app, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for t, fs in series.items():
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                return fs
    earliest = min(series)
    return series[earliest]


def member_first_tp_frac_resolved(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    baseline_tp: Optional[float] = None,
) -> Optional[Tuple[float, float, float]]:
    """``(tp, frac, frac_spread)`` for the member's baseline timepoint —
    the frac counterpart of :func:`member_first_tp_stats_resolved`."""
    series = member_frac_series(
        app, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for t, fs in series.items():
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                return (float(t), fs[0], fs[1])
    earliest = min(series)
    fs = series[earliest]
    return (float(earliest), fs[0], fs[1])


# ── Bar-plot scaling ────────────────────────────────────────────────────────

def _apply_axis(value: float, spread: float, d_mean: Optional[float],
                d_spread: Optional[float], op: str) -> Tuple[float, float]:
    """Apply a single FC axis to a (value, spread) pair.

    ``ratio`` divides; ``subtract`` subtracts. Variances combine in
    quadrature in both cases (independent quantities). Returns
    ``(nan, 0.0)`` when the ratio denominator is non-finite or zero.
    """
    if d_mean is None:
        return value, spread
    if not math.isfinite(d_mean):
        return float("nan"), 0.0
    ds = float(d_spread) if (d_spread is not None and math.isfinite(d_spread)) else 0.0
    s = float(spread) if (spread is not None and math.isfinite(spread)) else 0.0
    if op == "subtract":
        new_val = value - float(d_mean)
        new_spread = math.sqrt(s * s + ds * ds)
        return float(new_val), float(new_spread)
    # default: ratio
    if d_mean == 0:
        return float("nan"), 0.0
    new_val = value / float(d_mean)
    rel_x_sq = _rel_err_sq(value, s)
    rel_d_sq = _rel_err_sq(d_mean, ds)
    if not math.isfinite(new_val):
        return float("nan"), 0.0
    new_spread = abs(new_val) * math.sqrt(rel_x_sq + rel_d_sq) if (rel_x_sq + rel_d_sq) > 0 else 0.0
    return float(new_val), float(new_spread)


def scale_bar_value(
    mean: float,
    spread: float,
    *,
    control_mean: Optional[float] = None,
    control_spread: Optional[float] = None,
    t0_mean: Optional[float] = None,
    t0_spread: Optional[float] = None,
    op: str = "ratio",
    op_ctrl: Optional[str] = None,
    op_t0: Optional[str] = None,
    order: str = "ctrl_first",
) -> Tuple[float, float]:
    """Apply control and/or t0 normalization with error propagation.

    ``op_ctrl`` and ``op_t0`` select how each axis's denominator is
    applied — independent so users can subtract one and divide by the
    other. Each is ``"ratio"`` (``Y = X / D``, relative variances add in
    quadrature) or ``"subtract"`` (``Y = X - D``, absolute variances add
    in quadrature). When ``op_ctrl`` / ``op_t0`` are ``None`` they fall
    back to ``op`` — the legacy single-op contract.

    Application order: control first, then t0 on the post-control value
    (preserving the ΔΔCt-style stacking when both are ratios).

    *control_spread* / *t0_spread* default to ``None`` (treated as 0),
    recovering the original "denominator treated as exact" behaviour.

    Returns ``(scaled_mean, scaled_spread)`` with NaN propagation when
    a ratio denominator is non-finite or zero. Spread is non-negative.
    """
    if not math.isfinite(mean):
        return float("nan"), 0.0
    effective_op_ctrl = op_ctrl if op_ctrl is not None else op
    effective_op_t0 = op_t0 if op_t0 is not None else op
    val, sp = float(mean), float(spread) if (spread is not None and math.isfinite(spread)) else 0.0
    if order == "t0_first":
        val, sp = _apply_axis(val, sp, t0_mean, t0_spread, effective_op_t0)
        if not math.isfinite(val):
            return float("nan"), 0.0
        val, sp = _apply_axis(val, sp, control_mean, control_spread, effective_op_ctrl)
    else:
        val, sp = _apply_axis(val, sp, control_mean, control_spread, effective_op_ctrl)
        if not math.isfinite(val):
            return float("nan"), 0.0
        val, sp = _apply_axis(val, sp, t0_mean, t0_spread, effective_op_t0)
    if not math.isfinite(val):
        return float("nan"), 0.0
    return val, sp


def first_tp_value(pts) -> Optional[float]:
    """Return the mean of the earliest finite-mean AggPoint, or None."""
    if not pts:
        return None
    for pt in sorted(pts, key=lambda p: p[0]):
        m = pt[1]
        if isinstance(m, (int, float)) and math.isfinite(m):
            return float(m)
    return None
