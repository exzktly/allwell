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
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple
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


def _grow(new: list, idx_a: int, idx_b: Optional[int]) -> None:
    """Pad *new* with 0.0 so indices ``idx_a`` and ``idx_b`` are addressable."""
    need = idx_a if idx_b is None else max(idx_a, idx_b)
    while len(new) <= need:
        new.append(0.0)


def _scale_field(
    pt: tuple, val_idx: int, spread_idx: Optional[int], factor: float, *,
    factor_rel_err_sq: float = 0.0,
) -> tuple:
    """Generic ``_scale_pt`` over an arbitrary ``(val_idx, spread_idx)`` field.

    Used to fold-change the fraction field ([3]/[6]) and the N-events field
    ([4] non-FOV, no spread; [7]/[8] per-FOV) the same way the mean field
    ([1]/[2]) is scaled. ``spread_idx=None`` marks a count-like field with no
    spread — the value is scaled and no spread is written.
    """
    new = list(pt)
    _grow(new, val_idx, spread_idx)
    if factor == 0 or not math.isfinite(factor):
        new[val_idx] = float("nan")
        if spread_idx is not None:
            new[spread_idx] = 0.0
        return tuple(new)
    v = pt[val_idx] if val_idx < len(pt) else None
    scaled_val = v / factor if isinstance(v, (int, float)) and math.isfinite(v) else float("nan")
    new[val_idx] = scaled_val
    if spread_idx is not None:
        s = pt[spread_idx] if spread_idx < len(pt) else None
        s_val = s if isinstance(s, (int, float)) and math.isfinite(s) else 0.0
        new[spread_idx] = _propagate_spread(
            scaled_val, v if isinstance(v, (int, float)) else float("nan"),
            s_val, factor_rel_err_sq,
        )
    return tuple(new)


def _subtract_field(
    pt: tuple, val_idx: int, spread_idx: Optional[int], shift: float, *,
    shift_spread: float = 0.0,
) -> tuple:
    """Generic ``_subtract_pt`` over an arbitrary ``(val_idx, spread_idx)`` field."""
    new = list(pt)
    _grow(new, val_idx, spread_idx)
    v = pt[val_idx] if val_idx < len(pt) else None
    if not (isinstance(v, (int, float)) and math.isfinite(v)) or not math.isfinite(shift):
        new[val_idx] = float("nan")
        if spread_idx is not None:
            new[spread_idx] = 0.0
        return tuple(new)
    new[val_idx] = float(v) - float(shift)
    if spread_idx is not None:
        s = pt[spread_idx] if spread_idx < len(pt) else None
        s_val = s if isinstance(s, (int, float)) and math.isfinite(s) else 0.0
        shift_var = float(shift_spread) ** 2 if math.isfinite(shift_spread) else 0.0
        new[spread_idx] = math.sqrt(s_val * s_val + shift_var)
    return tuple(new)


def _resolve_stat(
    t: float, stats: Dict[float, Tuple[float, float]],
    default: Tuple[float, float],
) -> Tuple[float, float]:
    """Resolve the ``(value, spread)`` for *t* in *stats* under relative tol."""
    if t in stats:
        return stats[t]
    for ct, vs in stats.items():
        if abs(ct - t) < _TP_REL_TOL * max(1.0, abs(ct), abs(t)):
            return vs
    return default


def _series_at(
    series: Dict[float, Tuple[float, float]], target_t: float,
) -> Optional[Tuple[float, float]]:
    """The ``(value, spread)`` a member's series carries at *target_t*.

    Matches an exact key first, then any key within the relative timepoint
    tolerance. Returns ``None`` when the series is empty or carries no sample
    near *target_t* — the "control can't be resolved" case the bar-plot
    ``control_*_at_for_bar`` wrappers report upward.
    """
    if not series:
        return None
    if target_t in series:
        return series[target_t]
    for ct, val in series.items():
        if abs(ct - target_t) < _TP_REL_TOL * max(1.0, abs(ct), abs(target_t)):
            return val
    return None


def _series_baseline(
    series: Dict[float, Tuple[float, float]], baseline_tp: Optional[float],
) -> Optional[Tuple[float, float, float]]:
    """``(tp, value, spread)`` for a member's baseline timepoint.

    Prefers the sample at *baseline_tp* (under the relative tolerance) so a bar
    plot can look the control up at the SAME tp the member used — needed for
    correct post-control baseline stacking when ops are mixed. Falls through to
    the earliest timepoint when no baseline is given, when it is non-finite, or
    when the series has no sample near it. Returns ``None`` only for an empty
    series.
    """
    if not series:
        return None
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for t, vs in series.items():
            if abs(t - tgt) <= _TP_REL_TOL * max(1.0, abs(t), abs(tgt)):
                return (float(t), vs[0], vs[1])
    earliest = min(series)
    vs = series[earliest]
    return (float(earliest), vs[0], vs[1])


def _field_baseline(
    pts, val_idx: int, baseline_tp: Optional[float],
) -> Tuple[Optional[float], float]:
    """``(baseline_val, baseline_spread_unused)`` for a field's vs-t0 step.

    Mirrors the mean baseline logic: prefer the point at *baseline_tp*, else the
    earliest finite non-zero value. The spread returned is always the field's
    own spread at that point is *not* needed here — the count/fraction t0
    propagation reuses the field's own spread via ``_scale_field`` — so this
    returns only the divisor; the caller passes ``shift_spread=0`` / a 0 rel-err
    for the baseline (matching the mean path, which folds only the control and
    the member's own per-point spread, not a separate baseline spread term).
    """
    candidates = pts
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for pt in pts:
            if abs(float(pt[0]) - tgt) <= _TP_REL_TOL * max(1.0, abs(float(pt[0])), abs(tgt)):
                v = pt[val_idx] if len(pt) > val_idx else None
                if isinstance(v, (int, float)) and math.isfinite(v) and v != 0:
                    return float(v), 0.0
                break
    for pt in sorted(candidates, key=lambda p: p[0]):
        v = pt[val_idx] if len(pt) > val_idx else None
        if isinstance(v, (int, float)) and math.isfinite(v) and v != 0:
            return float(v), 0.0
    return None, 0.0


def _normalize_field(
    pts, *, val_idx: int, spread_idx: Optional[int],
    control_stats: Optional[Dict[float, Tuple[float, float]]],
    use_t0: bool, baseline_tp: Optional[float],
    op_ctrl: str, op_t0: str,
) -> list:
    """Apply vs-control + vs-t0 normalization to ONE AggPoint field.

    Generic counterpart to the mean steps in :func:`normalize_pts`, used for the
    fraction and N-events fields so all three line-plot panels fold-change
    consistently. The mean field is left untouched (it has its own steps). A
    point with no matching control sample has only *this* field set to NaN — the
    mean step already recorded the miss in ``miss_sink``.
    """
    # Step A: control normalization.
    if control_stats:
        means_only = {t: vs[0] for t, vs in control_stats.items()}
        stage: list = []
        for pt in pts:
            t = float(pt[0])
            cm = _match_control_mean(t, means_only)
            if cm is None:
                new = list(pt)
                _grow(new, val_idx, spread_idx)
                new[val_idx] = float("nan")
                if spread_idx is not None:
                    new[spread_idx] = 0.0
                stage.append(tuple(new))
                continue
            cval, cspread = _resolve_stat(t, control_stats, (cm, 0.0))
            if op_ctrl == "subtract":
                stage.append(_subtract_field(pt, val_idx, spread_idx, cval,
                                             shift_spread=cspread or 0.0))
            else:
                stage.append(_scale_field(pt, val_idx, spread_idx, cval,
                                          factor_rel_err_sq=_rel_err_sq(cval, cspread)))
        pts = stage

    # Step B: vs-t0 baseline (uses each member's own post-control values).
    if use_t0:
        base, base_spread = _field_baseline(pts, val_idx, baseline_tp)
        if base is not None:
            if op_t0 == "subtract":
                pts = [_subtract_field(pt, val_idx, spread_idx, base,
                                       shift_spread=base_spread) for pt in pts]
            else:
                pts = [_scale_field(pt, val_idx, spread_idx, base,
                                    factor_rel_err_sq=_rel_err_sq(base, base_spread))
                       for pt in pts]
    return pts


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
    normalize_frac_n: bool = False,
    n_per_fov: bool = False,
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
    Normalizes the fraction field (value [3], spread [6]) with the same
    vs-control / vs-t0 logic as the mean, so the Line Graphs' OnFraction
    panel rescales under fold change instead of staying at raw values.

    *control_n_stats* — ``{t: (n, 0.0)}`` for the control's N-events value.
    Normalizes the N-events field with the same logic. ``n_per_fov`` selects
    which AggPoint field the N panel actually plots: the per-FOV-mean count
    ([7]/[8]) when FOV-spread is on, else the total count ([4], no spread).
    The control denominator in *control_n_stats* must be computed for the
    matching representation (see ``member_n_stats_series``).

    The vs-t0 baseline applies to the mean, fraction, and N fields alike, so
    every panel responds to "vs t0" consistently. When only ``use_t0`` is set
    (no control selected) the fraction / N fields still normalize against their
    own t0 even though *control_frac_stats* / *control_n_stats* are empty.

    *use_t0* — when True, finds the member's earliest finite mean and
    divides every point by it after the control step. The earliest
    point itself becomes 1.0 ± propagated spread by construction.
    The baseline's OWN spread (taken from the same AggPoint) is folded
    into the propagation.

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
        elif effective_op_t0 == "subtract":
            stage2 = [_subtract_pt(pt, baseline, shift_spread=baseline_spread)
                      for pt in stage1]
        else:
            baseline_rel_err_sq = _rel_err_sq(baseline, baseline_spread)
            stage2 = [_scale_pt(pt, baseline,
                                factor_rel_err_sq=baseline_rel_err_sq)
                      for pt in stage1]
    else:
        stage2 = stage1

    # The fraction / N-events fields are normalized only for the Line Graphs
    # panels that plot them (``normalize_frac_n``). Bar / batch / CSV callers
    # leave the flag off so their AggPoint count semantics are unchanged.
    if normalize_frac_n:
        # Steps 3+4: fraction field ([3]/[6]) — same vs-control + vs-t0 logic.
        stage2 = _normalize_field(
            stage2, val_idx=3, spread_idx=6,
            control_stats=control_frac_stats, use_t0=use_t0,
            baseline_tp=baseline_tp,
            op_ctrl=effective_op_ctrl, op_t0=effective_op_t0,
        )
        # Steps 5+6: N-events field — [7]/[8] per-FOV-mean (the panel's
        # FOV-spread series) or [4] total count (no spread) otherwise.
        if n_per_fov:
            n_val_idx, n_spread_idx = 7, 8
        else:
            n_val_idx, n_spread_idx = 4, None
        stage2 = _normalize_field(
            stage2, val_idx=n_val_idx, spread_idx=n_spread_idx,
            control_stats=control_n_stats, use_t0=use_t0,
            baseline_tp=baseline_tp,
            op_ctrl=effective_op_ctrl, op_t0=effective_op_t0,
        )

    return stage2


# ── State accessors / ctx integration ───────────────────────────────────────

def fold_change_state(ctx) -> Tuple[bool, str, bool]:
    """Return the active ``(vs_control_on, control_label, vs_t0_on)`` triple.

    Used by both the on-screen plot tabs and the per-tab CSV exporter so the
    plot and the export stay in sync.
    """
    return (
        bool(ctx.state.plot.fc_vs_control_on),
        str(ctx.state.plot.fc_control_label or ""),
        bool(ctx.state.plot.fc_vs_t0_on),
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
    ctx, label: str, *,
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
    rset = _label_to_replicate_set(ctx, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(ctx,
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
                _, _, _, _, n_mean, n_spread = compute_rep_per_fov_stats(ctx,
                    rset, t, threshold, use_sem,
                )
                out[t] = (float(n_mean),
                          float(n_spread) if math.isfinite(n_spread) else 0.0)
            else:
                n_total = compute_rep_n_above(ctx, rset, t)
                out[t] = (float(n_total), 0.0)
        return out
    if label in (ctx.state.dataset.well_paths or {}):
        pts = aggregate_well(ctx,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        return {float(pt[0]): _pt_n_above(pt, per_fov_spread) for pt in pts}
    return {}


def control_n_at_for_bar(
    ctx, control_label: str, target_t: float, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(n_above, n_above_spread)`` of the control at *target_t* for the
    bar's N-events panel. Returns ``None`` when the control can't be
    resolved or has no sample at *target_t*."""
    return _series_at(member_n_series(
        ctx, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    ), target_t)


def member_first_tp_n_resolved(
    ctx, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    baseline_tp: Optional[float] = None,
) -> Optional[Tuple[float, float, float]]:
    """``(tp, n_above, n_above_spread)`` for the member's baseline timepoint.

    N-events counterpart of :func:`member_first_tp_frac_resolved` with the
    same matched-tp + fall-through-to-earliest semantics.
    """
    return _series_baseline(member_n_series(
        ctx, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    ), baseline_tp)


def parse_fc_op_ctrl(ctx) -> str:
    """Return the user's chosen vs-control operator (``"ratio"`` or
    ``"subtract"``)."""
    op = getattr(ctx.state.plot, "fc_op_ctrl", "ratio") or "ratio"
    return op if op in ("ratio", "subtract") else "ratio"


def parse_fc_op_t0(ctx) -> str:
    """Return the user's chosen vs-baseline-tp operator (``"ratio"`` or
    ``"subtract"``)."""
    op = getattr(ctx.state.plot, "fc_op_t0", "ratio") or "ratio"
    return op if op in ("ratio", "subtract") else "ratio"


def parse_fc_order(ctx) -> str:
    """Return the user's chosen FC application order (``"ctrl_first"`` or
    ``"t0_first"``). Defaults to ``"ctrl_first"`` for back-compat."""
    order = getattr(ctx.state.plot, "fc_order", "ctrl_first") or "ctrl_first"
    return order if order in ("ctrl_first", "t0_first") else "ctrl_first"


def parse_baseline_tp(ctx) -> Optional[float]:
    """Resolve ``PlotState.fc_baseline_tp`` (a tp string) to a float.

    Returns ``None`` when no baseline tp is set, when the saved string
    isn't parseable as a float, or when the user has parameterised
    "earliest available point" via an empty value (which ``normalize_pts``
    handles as the original ``vs t0`` semantics).
    """
    raw = getattr(ctx.state.plot, "fc_baseline_tp", "") or ""
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def is_active(ctx) -> bool:
    vs_ctrl, _, vs_t0 = fold_change_state(ctx)
    return vs_ctrl or vs_t0


def _fc_op_word(op: str) -> str:
    """Human word for a fold-change operator token."""
    return "subtraction" if str(op) == "subtract" else "ratio"


def fold_change_suffix(vs_control_on: bool, vs_t0_on: bool,
                       control_label: str = "",
                       op_ctrl: str = "ratio", op_t0: str = "ratio",
                       baseline_label: str = "") -> str:
    """Compact " (fold-change …)" label fragment for axis titles.

    Names the operator (ratio vs subtraction) for each active axis so the plot
    title makes explicit *how* the values were normalised, and — for the vs-t0
    axis — *which* timepoint is the baseline (``baseline_label``, e.g.
    ``"t=24h"``; falls back to ``"t0"``). Example:
    ``"  (fold change vs DMSO (ratio), vs t=0h (subtraction))"``.
    """
    parts: List[str] = []
    if vs_control_on:
        lbl = f"vs {control_label}" if control_label else "vs control"
        parts.append(f"{lbl} ({_fc_op_word(op_ctrl)})")
    if vs_t0_on:
        base = baseline_label or "t0"
        parts.append(f"vs {base} ({_fc_op_word(op_t0)})")
    if not parts:
        return ""
    return "  (fold change " + ", ".join(parts) + ")"


def fold_change_suffix_for(ctx) -> str:
    """``fold_change_suffix`` built from the live fold-change state
    (per-axis ratio/subtract operators + the resolved baseline timepoint)."""
    plot = ctx.state.plot
    base_tp = parse_baseline_tp(ctx)
    baseline_label = f"t={base_tp:g}h" if base_tp is not None else "first timepoint"
    return fold_change_suffix(
        bool(getattr(plot, "fc_vs_control_on", False)),
        bool(getattr(plot, "fc_vs_t0_on", False)),
        str(getattr(plot, "fc_control_label", "") or ""),
        op_ctrl=str(getattr(plot, "fc_op_ctrl", "ratio") or "ratio"),
        op_t0=str(getattr(plot, "fc_op_t0", "ratio") or "ratio"),
        baseline_label=baseline_label,
    )


# ── Control-series helpers ──────────────────────────────────────────────────

_WELL_DISAMBIG_SUFFIX = " (well)"


def resolve_control_wells(ctx, control_label: str) -> List[str]:
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
        if bare in (ctx.state.dataset.well_paths or {}):
            return [bare]
        return []
    for s in (ctx.state.selection.selections or []):
        if s.get("name") == label:
            wells = [w for w in (s.get("wells") or []) if w in ctx.state.dataset.well_paths]
            if wells:
                return wells
    if label in (ctx.state.dataset.well_paths or {}):
        return [label]
    return []


def control_pts_for_line(ctx, control_label: str, *, threshold: float,
                         val_col: str,
                         fluor_gates) -> list:
    """Aggregate the control selection as a single pooled series.

    Used by the line-plot path where we want a control mean at every
    timepoint the data exposes. Returns an AggPoint list (or [] if the
    control is unresolved).
    """
    wells = resolve_control_wells(ctx, control_label)
    if not wells:
        return []
    return aggregate_group(ctx,
        wells, threshold=threshold, use_sem=False,
        val_col=val_col,
        fluor_gates=fluor_gates,
    )


def _label_to_replicate_set(ctx, label: str):
    """Resolve a control label to a ReplicateSet OR ``None``.

    Returns the ReplicateSet view of a Sample-Definitions selection when
    the label matches one (and the selection has ≥1 loaded well). Returns
    ``None`` for unknown labels and bare well tokens — the caller should
    fall back to per-well handling in the well-token case.
    """
    if not label:
        return None
    for s in (ctx.state.selection.selections or []):
        if s.get("name") == label:
            wells = [w for w in (s.get("wells") or [])
                     if w in ctx.state.dataset.well_paths]
            if not wells:
                return None
            from well_viewer.domain.batch_models import ReplicateSet
            return ReplicateSet(label, wells)
    return None


def member_mean_series(
    ctx, label: str, *,
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
    rset = _label_to_replicate_set(ctx, label)
    if rset is not None:
        # Collect the union of timepoints across the rep-set's wells.
        # We use the per-well agg (use_sem=False; spread isn't used here)
        # only to enumerate the timepoint axis.
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(ctx,
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
                gm, *_ = compute_rep_per_fov_stats(ctx,
                    rset, t, threshold, use_sem,
                )
            else:
                gm, *_ = compute_rep_stats(ctx,
                    rset, t, threshold, use_sem,
                )
            if math.isfinite(gm):
                out[t] = float(gm)
        return out
    # Bare well token.
    if label in (ctx.state.dataset.well_paths or {}):
        pts = aggregate_well(ctx,
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
    ctx, control_label: str, target_t: float, *,
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
        ctx, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    return _match_control_mean(target_t, series)


class CellScaleSpec(NamedTuple):
    """Per-(well, tp) fold-change transform for a per-cell overlay.

    Mirrors the axis arguments of :func:`scale_bar_value` so an overlaid
    per-cell distribution lands on the *same* fold-change axis as its bar.
    ``missing`` is set when a requested baseline could not be resolved (or a
    ratio denominator is non-finite/zero) — the caller renders a blank slot
    rather than misleading raw values.
    """
    control_mean: Optional[float]
    op_ctrl: str
    t0_mean: Optional[float]
    op_t0: str
    order: str
    missing: bool


def apply_cell_scale(vals, spec: Optional["CellScaleSpec"]):
    """Apply *spec* to a 1-D array of per-cell values.

    Uses the same per-axis operator (``"ratio"`` → divide, ``"subtract"`` →
    subtract) and application ``order`` as :func:`scale_bar_value`, but
    element-wise and without error propagation (individual cells carry no
    spread). Returns the transformed array; an all-``nan`` array when a ratio
    denominator is bad (so the caller can drop it). A ``None`` spec is a
    no-op.
    """
    import numpy as _np

    if spec is None:
        return vals
    out = _np.asarray(vals, dtype=float)

    def _axis(a, denom, op):
        if denom is None:
            return a
        if op == "subtract":
            return a - float(denom)
        # ratio
        if not (isinstance(denom, (int, float)) and math.isfinite(denom) and denom != 0):
            return _np.full_like(a, _np.nan)
        return a / float(denom)

    if spec.order == "t0_first":
        out = _axis(out, spec.t0_mean, spec.op_t0)
        out = _axis(out, spec.control_mean, spec.op_ctrl)
    else:
        out = _axis(out, spec.control_mean, spec.op_ctrl)
        out = _axis(out, spec.t0_mean, spec.op_t0)
    return out


def build_cell_scaling(
    ctx, wells, *, fc_state: Tuple[bool, str, bool],
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    op_ctrl: str = "ratio", op_t0: str = "ratio",
    order: str = "ctrl_first", baseline_tp: Optional[float] = None,
) -> Callable[[str, float], CellScaleSpec]:
    """Build a ``scale_for(well, tp) -> CellScaleSpec`` resolver for per-cell
    violin/beeswarm fold-change overlays.

    The returned spec applies the SAME per-axis operator (ratio vs subtract),
    application ``order``, and ``baseline_tp`` the bars use
    (:func:`scale_bar_value` / :func:`baseline_from_points`), so an overlaid
    per-cell distribution sits on the same fold-change axis as its bar —
    fixing the old always-divide behaviour (audit S1). ``tp`` is honoured per
    call, so a multi-timepoint selection scales each column at its own
    timepoint instead of collapsing to the first (audit S3).

      * control_mean — single scalar across all wells at the requested tp,
        from :func:`control_mean_at_for_bar` (the bar's stat), cached per tp.
        ``None`` / ``missing`` when vs-control is requested but unresolved.
      * t0_mean — each well's own baseline from :func:`member_baseline_value`
        (honouring ``baseline_tp``), resolved once per well.
    """
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state

    # t0 baseline is per-well and tp-independent — resolve once up front.
    t0_by_well: Dict[str, Optional[float]] = {}
    if fc_vs_t0:
        for w in wells:
            t0_by_well[w] = member_baseline_value(
                ctx, w, baseline_tp=baseline_tp,
                threshold=threshold, val_col=val_col, use_sem=use_sem,
                per_fov_spread=per_fov_spread, fluor_gates=fluor_gates,
            )

    control_cache: Dict[float, Optional[float]] = {}

    def _control_at(tp: float) -> Optional[float]:
        if not (fc_vs_ctrl and fc_ctrl_lbl):
            return None
        if tp not in control_cache:
            control_cache[tp] = control_mean_at_for_bar(
                ctx, fc_ctrl_lbl, tp,
                threshold=threshold, val_col=val_col, use_sem=use_sem,
                per_fov_spread=per_fov_spread, fluor_gates=fluor_gates,
            )
        return control_cache[tp]

    def scale_for(well: str, tp: float) -> CellScaleSpec:
        missing = False
        cm: Optional[float] = None
        if fc_vs_ctrl and fc_ctrl_lbl:
            cm = _control_at(float(tp))
            if cm is None:
                missing = True
            elif op_ctrl == "ratio" and (not math.isfinite(cm) or cm == 0):
                missing = True
        t0: Optional[float] = None
        if fc_vs_t0:
            t0 = t0_by_well.get(well)
            if t0 is None:
                missing = True
            elif op_t0 == "ratio" and (not math.isfinite(t0) or t0 == 0):
                missing = True
        return CellScaleSpec(
            control_mean=cm if fc_vs_ctrl else None,
            op_ctrl=op_ctrl,
            t0_mean=t0 if fc_vs_t0 else None,
            op_t0=op_t0,
            order=order,
            missing=missing,
        )

    return scale_for


def member_baseline_value(
    ctx, label: str, *,
    baseline_tp: Optional[float],
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[float]:
    """Member baseline honouring *baseline_tp* (audit S1).

    Picks the member's own mean at ``baseline_tp`` when set and sampled;
    otherwise falls back to the earliest finite mean — matching
    :func:`baseline_from_points` / :func:`member_first_tp_value` so the
    per-cell overlay baseline agrees with the bar's.
    """
    series = member_mean_series(
        ctx, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    if baseline_tp is not None and math.isfinite(baseline_tp):
        tgt = float(baseline_tp)
        for t, m in series.items():
            if (abs(float(t) - tgt) <= _TP_REL_TOL * max(1.0, abs(float(t)), abs(tgt))
                    and isinstance(m, (int, float)) and math.isfinite(m) and m != 0):
                return float(m)
        # Requested tp not sampled — fall through to earliest (mirrors the bar).
    earliest = min(series)
    return series[earliest]


def member_first_tp_value(
    ctx, label: str, *,
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
        ctx, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    earliest = min(series)
    return series[earliest]


def member_stats_series(
    ctx, label: str, *,
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
    rset = _label_to_replicate_set(ctx, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(ctx,
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
                gm, gerr, *_ = compute_rep_per_fov_stats(ctx,
                    rset, t, threshold, use_sem,
                )
            else:
                gm, gerr, *_ = compute_rep_stats(ctx,
                    rset, t, threshold, use_sem,
                )
            if math.isfinite(gm):
                spread = float(gerr) if (
                    isinstance(gerr, (int, float)) and math.isfinite(gerr)
                ) else 0.0
                out[t] = (float(gm), spread)
        return out
    if label in (ctx.state.dataset.well_paths or {}):
        pts = aggregate_well(ctx,
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
    ctx, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, Tuple[float, float]]:
    """``{t: (frac, frac_spread)}`` for a member — fraction-above-threshold + spread.

    Mirrors :func:`member_stats_series` but extracts the fraction field. The
    fraction lives at index [2] and its spread at index [3] of BOTH
    ``compute_rep_stats`` (4-tuple) and ``compute_rep_per_fov_stats`` (6-tuple),
    so front-indexing ``res[2]``/``res[3]`` is correct for either — a ``*_, gf,
    ferr`` tail-unpack would wrongly grab the per-FOV count fields from the
    6-tuple. Used by line-plot FC to scale the OnFraction panel.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(ctx, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(ctx,
                w, threshold=threshold, use_sem=False,
                val_col=val_col, fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        out: Dict[float, Tuple[float, float]] = {}
        for t in tps:
            if per_fov_spread:
                res = compute_rep_per_fov_stats(ctx, rset, t, threshold, use_sem)
            else:
                res = compute_rep_stats(ctx, rset, t, threshold, use_sem)
            gf, ferr = res[2], res[3]
            if isinstance(gf, (int, float)) and math.isfinite(gf):
                spread = float(ferr) if (
                    isinstance(ferr, (int, float)) and math.isfinite(ferr)
                ) else 0.0
                out[t] = (float(gf), spread)
        return out
    if label in (ctx.state.dataset.well_paths or {}):
        pts = aggregate_well(ctx,
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col, fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        out = {}
        for pt in pts:
            f = pt[3] if len(pt) > 3 else None
            if isinstance(f, (int, float)) and math.isfinite(f):
                s = pt[6] if len(pt) > 6 else 0.0
                spread = float(s) if (
                    isinstance(s, (int, float)) and math.isfinite(s)
                ) else 0.0
                out[float(pt[0])] = (float(f), spread)
        return out
    return {}


def member_n_stats_series(
    ctx, label: str, *,
    threshold: float, val_col: str,
    per_fov_spread: bool,
    fluor_gates,
) -> Dict[float, Tuple[float, float]]:
    """``{t: (n, spread)}`` for a member — N-events denominator for the N panel.

    Returns the value in the SAME representation the N panel plots, selected by
    *per_fov_spread*: the per-FOV-mean count (± per-FOV spread) when FOV-spread
    is on, else the total events-above-threshold (spread 0, counts have none).
    Used by line-plot FC to scale the N panel; the caller passes ``n_per_fov``
    to :func:`normalize_pts` so the scaled field matches this denominator.
    """
    label = (label or "").strip()
    if not label:
        return {}
    rset = _label_to_replicate_set(ctx, label)
    if rset is not None:
        out: Dict[float, Tuple[float, float]] = {}
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(ctx,
                w, threshold=threshold, use_sem=False,
                val_col=val_col, fluor_gates=fluor_gates,
                per_fov_spread=per_fov_spread,
            )
            for pt in pts:
                tps.add(float(pt[0]))
        for t in tps:
            if per_fov_spread:
                res = compute_rep_per_fov_stats(ctx, rset, t, threshold, False)
                n_mean, n_spread = res[4], res[5]
                if isinstance(n_mean, (int, float)) and math.isfinite(n_mean):
                    spread = float(n_spread) if (
                        isinstance(n_spread, (int, float)) and math.isfinite(n_spread)
                    ) else 0.0
                    out[t] = (float(n_mean), spread)
            else:
                n_above = compute_rep_n_above(ctx, rset, t)
                if isinstance(n_above, int) and n_above >= 0:
                    out[t] = (float(n_above), 0.0)
        return out
    if label in (ctx.state.dataset.well_paths or {}):
        pts = aggregate_well(ctx,
            label, threshold=threshold, use_sem=False,
            val_col=val_col, fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        out = {}
        for pt in pts:
            if per_fov_spread:
                n = pt[7] if len(pt) > 7 else None
                s = pt[8] if len(pt) > 8 else 0.0
            else:
                n = pt[4] if len(pt) > 4 else None
                s = 0.0
            if isinstance(n, (int, float)) and math.isfinite(n):
                spread = float(s) if (
                    isinstance(s, (int, float)) and math.isfinite(s)
                ) else 0.0
                out[float(pt[0])] = (float(n), spread)
        return out
    return {}


def control_stats_at_for_bar(
    ctx, control_label: str, target_t: float, *,
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
    return _series_at(member_stats_series(
        ctx, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    ), target_t)


def member_first_tp_stats(
    ctx, label: str, *,
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
        ctx, label,
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
    ctx, label: str, *,
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
    return _series_baseline(member_stats_series(
        ctx, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    ), baseline_tp)


# ── Bar-plot fraction-on FC helpers ─────────────────────────────────────────
#
# The "OnFraction" panel (fraction of cells above the gate) is its own bar
# row. Mean and frac are independent quantities; FC for frac normalises
# against the control / baseline's frac, not its mean. These helpers mirror
# the mean-side trio so ``collect_bar_items`` can scale the frac panel the
# same way it scales the mean panel.


def member_frac_series(
    ctx, label: str, *,
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
    rset = _label_to_replicate_set(ctx, label)
    if rset is not None:
        tps: Set[float] = set()
        for w in rset.wells:
            pts = aggregate_well(ctx,
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
                _, _, gf, gerr_f, *_ = compute_rep_per_fov_stats(ctx,
                    rset, t, threshold, use_sem,
                )
            else:
                _, _, gf, gerr_f = compute_rep_stats(ctx,
                    rset, t, threshold, use_sem,
                )
            if isinstance(gf, (int, float)) and math.isfinite(gf):
                fs = float(gerr_f) if (
                    isinstance(gerr_f, (int, float)) and math.isfinite(gerr_f)
                ) else 0.0
                out[t] = (float(gf), fs)
        return out
    if label in (ctx.state.dataset.well_paths or {}):
        pts = aggregate_well(ctx,
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
    ctx, control_label: str, target_t: float, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(frac, frac_spread)`` of the control at *target_t* for the bar's frac
    panel. Returns ``None`` when the control can't be resolved or has no
    sample at *target_t*."""
    return _series_at(member_frac_series(
        ctx, control_label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    ), target_t)


def member_first_tp_frac_resolved(
    ctx, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    fluor_gates,
    baseline_tp: Optional[float] = None,
) -> Optional[Tuple[float, float, float]]:
    """``(tp, frac, frac_spread)`` for the member's baseline timepoint —
    the frac counterpart of :func:`member_first_tp_stats_resolved`."""
    return _series_baseline(member_frac_series(
        ctx, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        fluor_gates=fluor_gates,
    ), baseline_tp)


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
