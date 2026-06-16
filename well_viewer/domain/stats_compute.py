"""Statistics computation helpers — replicate-set / group / per-well aggregates.

Hoisted out of ``well_viewer.runtime_app`` (Phase 11 follow-up). Lives in its
own module — not in ``stats_controller`` — because the controllers that
consume these helpers (``barplot_controller``, ``lineplot_controller``,
``scatter_controller``, ``fold_change``) are imported from places that
``stats_controller`` imports back from. Putting them here, behind a thin
imports surface, breaks the cycle.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from well_viewer.data.data_loading import aggregate_with_threshold_df


def _thresh_on_col(app) -> Optional[str]:
    """Return ``"Gate_On"`` when scatter-gate *thresholding* is active (mode on
    and a gate selected), else ``None``.

    Picks the fraction-on definition for :func:`aggregate_with_threshold_df`:
    with ``"Gate_On"`` the on-population is gate membership (fraction-on by
    gate) instead of ``value > ThreshFracOn``. Inlined here (no
    ``well_viewer.features`` import) to keep the domain layer acyclic — see
    ``tests/test_layering.py``.
    """
    if not getattr(app, "_cell_gating_use_scatter_thresh", False):
        return None
    gates = getattr(app, "_scatter_gates", None) or []
    if not gates:
        return None
    # Thresholding has its own gate selection (a set of names; None ⇒ all). Fall
    # back to the ThreshFracOn cutoff (return None) when the user has explicitly
    # selected no existing gate for the thresholding layer.
    choices = getattr(app, "_cell_gating_thresh_choices", None)
    if choices is not None:
        chosen = set(choices)
        if not any(str(g.get("name") or "") in chosen for g in gates):
            return None
    return "Gate_On"


def well_aggregate_stats(
    app,
    wells,
    target_t: float,
    *,
    threshold: float,
    val_col: str,
    fluor_gates: Dict[str, float],
    use_sem: bool,
    per_fov_spread: bool = False,
) -> tuple[float, float, float, float]:
    """Pool per-well means and fractions at *target_t* and return
    ``(mean, mean_err, frac, frac_err)``.

    Each loaded well contributes one mean / fraction value (per the
    active gating); sample SD (ddof=1) is taken across those values
    and divided by √n when ``use_sem`` is True. Shared between
    :meth:`_compute_rep_stats` (line / bar plot rep-set stats) and
    ``scatter_controller._agg_wells`` (scatter aggregate plot) so the
    three paths agree on what "mean across wells under active
    gating" means.

    When ``per_fov_spread`` is True the spread is taken across
    per-FOV means inside each well instead of across wells: the
    per-well mean stays the same but the returned ``mean_err`` is
    the mean of the per-well FOV spreads (a conservative pooling
    that keeps the answer well-defined for both single- and
    multi-well groups).
    """
    well_means: List[float] = []
    well_fracs: List[float] = []
    fov_mean_spreads: List[float] = []
    fov_frac_spreads: List[float] = []
    for lbl in wells:
        if lbl not in app._app_state.dataset.well_paths:
            continue
        pts = aggregate_well(
            app,
            lbl, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        for pt in pts:
            if abs(pt[0] - target_t) < 1e-6:
                _, m, sd, f, fsd, *_ = pt
                if not math.isnan(m):
                    well_means.append(m)
                    if per_fov_spread and math.isfinite(sd):
                        fov_mean_spreads.append(sd)
                if not math.isnan(f):
                    well_fracs.append(f)
                    if per_fov_spread and math.isfinite(fsd):
                        fov_frac_spreads.append(fsd)
                break

    if well_means:
        arr = np.asarray(well_means, dtype=float)
        gm = float(arr.mean())
        n = arr.size
        if per_fov_spread:
            gerr = (float(np.mean(fov_mean_spreads))
                    if fov_mean_spreads else 0.0)
        else:
            gsd = float(arr.std(ddof=1)) if n > 1 else 0.0
            gerr = gsd / math.sqrt(n) if (use_sem and n > 1) else gsd
    else:
        gm, gerr = float("nan"), 0.0

    if well_fracs:
        arr = np.asarray(well_fracs, dtype=float)
        gf = float(arr.mean())
        nf = arr.size
        if per_fov_spread:
            ferr = (float(np.mean(fov_frac_spreads))
                    if fov_frac_spreads else 0.0)
        else:
            fsd = float(arr.std(ddof=1)) if nf > 1 else 0.0
            ferr = fsd / math.sqrt(nf) if (use_sem and nf > 1) else fsd
    else:
        gf, ferr = float("nan"), 0.0

    return gm, gerr, gf, ferr


def compute_rep_stats(
    app,
    rset: "ReplicateSet",
    target_t: float,
    threshold: float,
    use_sem: bool,
) -> tuple:
    """
    Mean fluor ± SD/SEM for a single ReplicateSet at *target_t*.

    Statistics are computed across the loaded wells of the set.
    Each well contributes one mean-fluor value; SD/SEM is across those values.
    Results are cached in _stats_cache.

    Thin wrapper over :meth:`_well_aggregate_stats` which carries the
    shared per-well aggregation + ddof=1 stats logic. ``_agg_wells``
    in scatter_controller delegates to the same helper so all three
    plot paths (line / bar / scatter-agg) compute the "mean across
    wells under active gating" identically.
    """
    fluor_gates = app._get_all_fluor_gates()
    cache_key = ("rep", rset.name, tuple(rset.wells), target_t, threshold, use_sem,
                  app._app_state.channel.active_val_col, tuple(sorted(fluor_gates.items())))
    if cache_key in app._app_state.stats.cache:
        return app._app_state.stats.cache[cache_key]

    gm, gerr, gf, ferr = well_aggregate_stats(
        app,
        rset.wells, target_t,
        threshold=threshold,
        val_col=app._app_state.channel.active_val_col,
        fluor_gates=fluor_gates,
        use_sem=use_sem,
    )

    result = (gm, gerr, gf, ferr)
    app._app_state.stats.cache[cache_key] = result
    return result


def compute_rep_per_fov_stats(
    app,
    rset: "ReplicateSet",
    target_t: float,
    threshold: float,
    use_sem: bool,
) -> tuple:
    """Pool every FOV across all loaded wells of *rset* and compute stats.

    Returns ``(mean, mean_err, frac, frac_err, n_above_pf_mean,
    n_above_pf_spread)`` where the error fields are the SD/SEM across the
    pooled per-FOV means/fractions/counts. FOV identifiers are
    disambiguated by prefixing with the source well label so identical FOV
    numbers in different wells stay distinct.
    """
    fluor_gates = app._get_all_fluor_gates()
    cache_key = ("rep_pf", rset.name, tuple(rset.wells), target_t, threshold,
                 use_sem, app._app_state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())))
    if cache_key in app._app_state.stats.cache:
        return app._app_state.stats.cache[cache_key]

    frames = []
    for lbl in rset.wells:
        if lbl not in app._app_state.dataset.well_paths:
            continue
        df = app._get_rows(lbl)
        if df is None or len(df) == 0:
            continue
        if "fov" in df.columns:
            fov_str = (df["fov"].fillna("1").astype(str)
                       .str.strip().replace("", "1"))
        else:
            fov_str = pd.Series(["1"] * len(df), index=df.index)
        df = df.assign(fov=(lbl + "::" + fov_str).to_numpy())
        frames.append(df)

    if not frames:
        result = (float("nan"), 0.0, float("nan"), 0.0, 0.0, 0.0)
        app._app_state.stats.cache[cache_key] = result
        return result

    pooled = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    pts = aggregate_with_threshold_df(
        pooled,
        threshold=threshold,
        use_sem=use_sem,
        val_col=app._app_state.channel.active_val_col,
        fluor_gates=fluor_gates,
        per_fov_spread=True,
        ratios=app._app_state.channel.ratio_index,
        on_col=_thresh_on_col(app),
    )
    matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
    if matched:
        pt = matched[0]
        result = (
            float(pt[1]),
            float(pt[2]),
            float(pt[3]),
            float(pt[6]) if len(pt) >= 7 else 0.0,
            float(pt[7]) if len(pt) >= 8 else 0.0,
            float(pt[8]) if len(pt) >= 9 else 0.0,
        )
    else:
        result = (float("nan"), 0.0, float("nan"), 0.0, 0.0, 0.0)
    app._app_state.stats.cache[cache_key] = result
    return result


def compute_rep_n_above(app, rset: "ReplicateSet", target_t: float) -> int:
    """Total events-above-threshold contributing to *rset* at *target_t*.

    Sums ``n_above`` (AggPoint index 4) across loaded wells of the set
    after gating, so the bar plot's third panel reports the count of
    cells that actually exceeded the active threshold — the same
    numerator that drives the fraction-above-threshold value.
    """
    threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
    fluor_gates = app._get_all_fluor_gates()
    cache_key = ("rep_n_above", rset.name, tuple(rset.wells), target_t, threshold,
                 app._app_state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())))
    if cache_key in app._app_state.stats.cache:
        return app._app_state.stats.cache[cache_key]

    total = 0
    for lbl in rset.wells:
        if lbl not in app._app_state.dataset.well_paths:
            continue
        pts = aggregate_well(
            app,
            lbl, threshold=threshold, use_sem=False,
            val_col=app._app_state.channel.active_val_col,
            fluor_gates=fluor_gates,
        )
        matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
        if matched:
            # AggPoint index 4 = n_above; index 5 = n_total.
            total += int(matched[0][4])
    app._app_state.stats.cache[cache_key] = total
    return total


def aggregate_well(
    app,
    label: str,
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates: Optional[Dict[str, float]] = None,
    per_fov_spread: bool = False,
    tp_col: str = "timepoint_hours",
):
    """Aggregate a single well via the vectorized DataFrame path.

    If ``app`` exposes a callable ``_aggregate_well`` (the pure-logic
    test MockApp does, returning precomputed AggPoints from its
    in-memory map), delegate to it. Production ``WellViewerApp`` does
    not define this method, so the DataFrame path is used at runtime.
    """
    delegate = getattr(app, "_aggregate_well", None)
    if callable(delegate):
        return delegate(
            label,
            threshold=threshold,
            use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
            tp_col=tp_col,
        )
    return aggregate_with_threshold_df(
        app._get_rows(label),
        threshold=threshold,
        use_sem=use_sem,
        tp_col=tp_col,
        val_col=val_col,
        fluor_gates=fluor_gates or {},
        per_fov_spread=per_fov_spread,
        ratios=app._app_state.channel.ratio_index,
        on_col=_thresh_on_col(app),
    )


def aggregate_group(
    app,
    wells: List[str],
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates: Optional[Dict[str, float]] = None,
    per_fov_spread: bool = False,
    tp_col: str = "timepoint_hours",
):
    """Aggregate a list of wells via the vectorized path (concatenated when len>1)."""
    delegate = getattr(app, "_aggregate_group", None)
    if callable(delegate):
        return delegate(
            wells,
            threshold=threshold,
            use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
            tp_col=tp_col,
        )
    valid = [w for w in wells if w in app._app_state.dataset.well_paths]
    if not valid:
        return []
    if len(valid) == 1:
        return aggregate_well(
            app,
            valid[0], threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates, per_fov_spread=per_fov_spread,
            tp_col=tp_col,
        )
    frames = []
    for w in valid:
        sub = app._get_rows(w)
        if per_fov_spread and len(sub) > 0:
            # Prefix FOV ids with the well label so identical FOV numbers
            # across replicate wells stay distinct when pooled.
            if "fov" in sub.columns:
                fov_str = (sub["fov"].fillna("1").astype(str)
                           .str.strip().replace("", "1"))
            else:
                fov_str = pd.Series(["1"] * len(sub), index=sub.index)
            sub = sub.assign(fov=(w + "::" + fov_str).to_numpy())
        frames.append(sub)
    df = pd.concat(frames, ignore_index=True)
    return aggregate_with_threshold_df(
        df,
        threshold=threshold,
        use_sem=use_sem,
        tp_col=tp_col,
        val_col=val_col,
        fluor_gates=fluor_gates or {},
        per_fov_spread=per_fov_spread,
        ratios=app._app_state.channel.ratio_index,
        on_col=_thresh_on_col(app),
    )


