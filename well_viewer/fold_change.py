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

Only ``mean`` and its ``spread`` field are rescaled; fractions, counts and
per-FOV spreads of counts are left untouched (they're not interpretable as
fold-change). The transform returns new tuples with the same arity.

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
from well_viewer.stats_compute import compute_rep_stats
from well_viewer.stats_compute import compute_rep_per_fov_stats


CONTROL_NONE = ""

# Relative-tolerance match for floating-point timepoint comparisons.
# Combo widgets format their items with ``:.4g`` (≈4 significant figures),
# so a fractional timepoint like 2/3 ≈ 0.6667 can be ~3e-5 off the original
# float when parsed back from text. An absolute 1e-6 tolerance misses
# those cases, so we use a relative one — passes for both whole-number
# and fractional timepoints, fails only on truly mismatched values.
_TP_REL_TOL = 1e-3


def _safe_div(numer: float, denom: float) -> float:
    if denom == 0 or not math.isfinite(denom):
        return float("nan")
    return numer / denom


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
    use_t0: bool = False,
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
                rel_err_sq = _rel_err_sq(ctrl_mean, ctrl_spread)
                stage1.append(_scale_pt(pt, ctrl_mean,
                                         factor_rel_err_sq=rel_err_sq))
    else:
        stage1 = list(pts)

    # Step 2: t0 normalization (uses post-control values). The baseline's
    # own spread folds into every downstream point's spread via the same
    # quadrature formula.
    if use_t0:
        baseline = None
        baseline_spread = 0.0
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
            return stage1
        baseline_rel_err_sq = _rel_err_sq(baseline, baseline_spread)
        return [_scale_pt(pt, baseline,
                          factor_rel_err_sq=baseline_rel_err_sq)
                for pt in stage1]

    return stage1


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
                         val_col: str, cell_area_threshold: float,
                         fluor_gates) -> list:
    """Aggregate the control selection as a single pooled series.

    Used by the line-plot path where we want a control mean at every
    timepoint the data exposes. Returns an AggPoint list (or [] if the
    control is unresolved).
    """
    wells = resolve_control_wells(app, control_label)
    if not wells:
        return []
    return app._aggregate_group(
        wells, threshold=threshold, use_sem=False,
        val_col=val_col,
        cell_area_threshold=cell_area_threshold,
        fluor_gates=fluor_gates,
    )


def control_mean_at(app, control_label: str, target_t: float, *,
                    threshold: float, val_col: str,
                    cell_area_threshold: float, fluor_gates) -> Optional[float]:
    """Return the control's pooled mean at *target_t* (or None).

    .. deprecated:: Use :func:`control_mean_at_for_bar` /
       :func:`member_mean_series` instead — they compute the control
       denominator using the SAME stat the displayed bar/curve uses
       (mean-of-per-well-means for rep-sets), so the resulting ratio is
       a clean fold-change rather than a mix of two different averaging
       methods. Kept temporarily for callers not yet migrated.
    """
    pts = control_pts_for_line(
        app, control_label, threshold=threshold, val_col=val_col,
        cell_area_threshold=cell_area_threshold, fluor_gates=fluor_gates,
    )
    means = pts_to_mean_by_t(pts)
    return _match_control_mean(target_t, means)


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
            from well_viewer.batch_models import ReplicateSet
            return ReplicateSet(label, wells)
    return None


def member_mean_series(
    app, label: str, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    cell_area_threshold: float, fluor_gates,
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
            pts = app._aggregate_well(
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                cell_area_threshold=cell_area_threshold,
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
        pts = app._aggregate_well(
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            cell_area_threshold=cell_area_threshold,
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
    cell_area_threshold: float, fluor_gates,
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
        cell_area_threshold=cell_area_threshold,
        fluor_gates=fluor_gates,
    )
    return _match_control_mean(target_t, series)


def build_cell_scaling(
    app, wells, *, fc_state: Tuple[bool, str, bool], target_t: float,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    cell_area_threshold: float, fluor_gates,
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
            cell_area_threshold=cell_area_threshold,
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
                cell_area_threshold=cell_area_threshold,
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
    cell_area_threshold: float, fluor_gates,
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
        cell_area_threshold=cell_area_threshold,
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
    cell_area_threshold: float, fluor_gates,
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
            pts = app._aggregate_well(
                w, threshold=threshold, use_sem=False,
                val_col=val_col,
                cell_area_threshold=cell_area_threshold,
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
        pts = app._aggregate_well(
            label, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            cell_area_threshold=cell_area_threshold,
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


def control_stats_at_for_bar(
    app, control_label: str, target_t: float, *,
    threshold: float, val_col: str, use_sem: bool,
    per_fov_spread: bool,
    cell_area_threshold: float, fluor_gates,
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
        cell_area_threshold=cell_area_threshold,
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
    cell_area_threshold: float, fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(mean, spread)`` of the pooled control at *target_t* (batch stat).

    Pool-of-cells variant used by the batch bar exporter — matches the
    batch numerator (also pool-of-cells via ``_aggregate_group``). The
    plot-tab uses :func:`control_stats_at_for_bar` instead.
    """
    pts = control_pts_for_line(
        app, control_label, threshold=threshold, val_col=val_col,
        cell_area_threshold=cell_area_threshold, fluor_gates=fluor_gates,
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
    cell_area_threshold: float, fluor_gates,
) -> Optional[Tuple[float, float]]:
    """``(mean, spread)`` for the member's earliest finite tp.

    The t0 baseline counterpart that lets the bar exporter propagate
    the baseline's own uncertainty into the displayed spread. Returns
    ``None`` for unknown / empty members.
    """
    series = member_stats_series(
        app, label,
        threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=per_fov_spread,
        cell_area_threshold=cell_area_threshold,
        fluor_gates=fluor_gates,
    )
    if not series:
        return None
    earliest = min(series)
    return series[earliest]


# ── Bar-plot scaling ────────────────────────────────────────────────────────

def scale_bar_value(
    mean: float,
    spread: float,
    *,
    control_mean: Optional[float] = None,
    control_spread: Optional[float] = None,
    t0_mean: Optional[float] = None,
    t0_spread: Optional[float] = None,
) -> Tuple[float, float]:
    """Apply control and/or t0 normalization with error propagation.

    For ``Y = X / (C · B)`` (one or both denominators present) with all
    three quantities independent, the relative variance adds:

        (σ_Y / Y)² = (σ_X / X)² + (σ_C / C)² + (σ_B / B)²

    *control_spread* / *t0_spread* default to None (treated as 0), which
    recovers the original "denominator treated as exact" behaviour for
    callers that don't have a meaningful uncertainty for the
    denominator. With both spreads supplied the result's spread is the
    properly-propagated value, not the naive ``spread / factor``.

    Returns ``(scaled_mean, scaled_spread)`` with NaN propagation when
    any factor is non-finite or zero. Spread is always non-negative.
    """
    factor = 1.0
    denom_rel_err_sq = 0.0
    if control_mean is not None:
        if not math.isfinite(control_mean) or control_mean == 0:
            return float("nan"), 0.0
        factor *= control_mean
        if control_spread is not None:
            denom_rel_err_sq += _rel_err_sq(control_mean, control_spread)
    if t0_mean is not None:
        if not math.isfinite(t0_mean) or t0_mean == 0:
            return float("nan"), 0.0
        factor *= t0_mean
        if t0_spread is not None:
            denom_rel_err_sq += _rel_err_sq(t0_mean, t0_spread)
    if not math.isfinite(mean):
        return float("nan"), 0.0
    new_mean = mean / factor
    new_spread = _propagate_spread(new_mean, mean, spread, denom_rel_err_sq)
    return float(new_mean), float(new_spread)


def first_tp_value(pts) -> Optional[float]:
    """Return the mean of the earliest finite-mean AggPoint, or None."""
    if not pts:
        return None
    for pt in sorted(pts, key=lambda p: p[0]):
        m = pt[1]
        if isinstance(m, (int, float)) and math.isfinite(m):
            return float(m)
    return None
