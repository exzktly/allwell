"""Statistics computation helpers — replicate-set / group / per-well aggregates.

Hoisted out of ``well_viewer.runtime_app`` (Phase 11 follow-up). Lives in its
own module — not in ``stats_controller`` — because the controllers that
consume these helpers (``barplot_controller``, ``lineplot_controller``,
``scatter_controller``, ``fold_change``) are imported from places that
``stats_controller`` imports back from. Putting them here, behind a thin
imports surface, breaks the cycle.
"""

from __future__ import annotations

import json
import math
import statistics
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from dataclasses import dataclass

from well_viewer.data.data_loading import (
    aggregate_with_threshold_df,
    df_included_mask,
    resolve_value_series,
)
from well_viewer.domain.tp_match import tp_close, tp_close_mask


# ── Unified replicate model (single source of truth for BOTH stats and plots) ──

def replicate_unit(n_wells: int, use_fov: bool) -> str:
    """What counts as one replicate for a group, given its loaded well count and
    the per-FOV toggle. The ONE rule shared by the Statistics test and the bar/
    line error bars so the test's ``n`` and the plotted spread never disagree.

    - ``>=2`` wells -> ``"well"``: each well is a replicate (its FOVs pooled).
    - ``1`` well + FOV mode on -> ``"fov"``: each FOV within the well is a replicate.
    - ``1`` well + FOV mode off -> ``"well"`` (a single replicate; no spread, and
      the stats test — needing ``n>=2`` — will decline it).
    - ``0`` wells -> ``"none"``.
    """
    if n_wells <= 0:
        return "none"
    if n_wells == 1:
        return "fov" if use_fov else "well"
    return "well"


@dataclass
class ReplicateStat:
    """Per-replicate summaries — one record per well (or per FOV) — that every
    consumer reduces. Carries what both the plots and the stats test need."""

    mean: float          # mean of above-threshold values
    median: float        # median of above-threshold values
    mean_all: float      # mean of all included cells (no threshold filter)
    frac: float          # fraction above threshold (or gate-on when on_col set)
    n_above: int
    n_total: int

    def summary(self, statistic: str) -> float:
        """The scalar the Statistics test uses for the chosen statistic."""
        if statistic == "fraction":
            return self.frac
        if statistic == "median":
            return self.median
        if statistic == "mean_all":
            return self.mean_all
        return self.mean


def _norm_fov(df: "pd.DataFrame") -> np.ndarray:
    """Normalized FOV id per row (empty -> "1"), matching the plot aggregator."""
    if "fov" in df.columns:
        s = df["fov"].fillna("1").astype(str).str.strip().replace("", "1")
    else:
        s = pd.Series(["1"] * len(df), index=df.index)
    return s.to_numpy()


def _safe_rows(app, lbl):
    """The well's raw DataFrame, or None when the backend exposes only
    pre-aggregated points (the pure-logic MockApp has no ``_get_rows``)."""
    getter = getattr(app, "_get_rows", None)
    return getter(lbl) if callable(getter) else None


def _point_at(pts, target_t):
    """The AggPoint at *target_t* (``(t, mean, spread, frac, n_above, n_total,…)``)
    or None. Shared by every consumer so mean/frac/n come from the one aggregator
    the plots already use (delegate-aware via :func:`aggregate_well`)."""
    for pt in pts or []:
        if tp_close(pt[0], target_t):
            return pt
    return None


def _raw_extra(df, target_t, threshold, val_col, fluor_gates, ratios, on_col):
    """``(median_above, mean_all)`` from raw cells for the stats-only median /
    mean_all statistics, using the SAME gating chokepoint as the aggregator.
    Returns ``(nan, nan)`` when raw rows aren't available (e.g. the pure-logic
    MockApp, which only exposes pre-aggregated points)."""
    if df is None or len(df) == 0 or "area_px" not in df.columns \
            or "timepoint_hours" not in df.columns:
        return float("nan"), float("nan")
    mask = df_included_mask(df).to_numpy(copy=True)
    area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
    with np.errstate(invalid="ignore"):
        mask &= np.isfinite(area)
    for channel, gate in (fluor_gates or {}).items():
        col = f"{channel}_mean_intensity"
        if col not in df.columns:
            return float("nan"), float("nan")
        gv = pd.to_numeric(df[col], errors="coerce").to_numpy()
        mask &= np.isfinite(gv) & (gv > gate)
    tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
    val = resolve_value_series(df, val_col, ratios).to_numpy()
    with np.errstate(invalid="ignore"):
        mask &= np.isfinite(tp) & tp_close_mask(tp, target_t) & np.isfinite(val)
    if not mask.any():
        return float("nan"), float("nan")
    all_vals = val[mask]
    if on_col and on_col in df.columns:
        above = all_vals[(pd.to_numeric(df[on_col], errors="coerce")
                          .fillna(0).to_numpy() > 0.5)[mask]]
    else:
        above = all_vals[all_vals > threshold]
    return ((float(np.median(above)) if above.size else float("nan")),
            (float(all_vals.mean()) if all_vals.size else float("nan")))


def _rep_from(app, df, pts, target_t, threshold, val_col, fluor_gates, ratios,
              on_col) -> "Optional[ReplicateStat]":
    """Build a ReplicateStat: mean/frac/n from the aggregator point *pts* (plot-
    identical, delegate-aware), median/mean_all from raw *df* when available."""
    pt = _point_at(pts, target_t)
    if pt is None:
        return None
    median, mean_all = _raw_extra(df, target_t, threshold, val_col, fluor_gates,
                                  ratios, on_col)
    return ReplicateStat(
        mean=float(pt[1]), median=median, mean_all=mean_all,
        frac=float(pt[3]), n_above=int(pt[4]), n_total=int(pt[5]),
    )


def group_replicate_stats(app, wells, target_t, *, use_fov, val_col, threshold,
                          fluor_gates=None) -> "List[ReplicateStat]":
    """THE canonical producer of per-replicate summaries for a group at a
    timepoint. One record per replicate (per well, or per FOV of the single well
    when ``replicate_unit`` is ``"fov"``); ``[]`` when there are no loaded wells.
    Statistics and both plot error-bar paths reduce this same list, so they can
    never describe different units of replication. Mean / fraction / n come from
    :func:`aggregate_well` (the same aggregator the plots use, honoring the
    MockApp delegate)."""
    loaded = [w for w in wells if w in app._app_state.dataset.well_paths]
    unit = replicate_unit(len(loaded), use_fov)
    if unit == "none":
        return []
    if fluor_gates is None:
        fluor_gates = app._get_all_fluor_gates()
    ratios = app._app_state.channel.ratio_index
    on_col = _thresh_on_col(app)
    reps: "List[ReplicateStat]" = []
    if unit == "well":
        for lbl in loaded:
            pts = aggregate_well(app, lbl, threshold=threshold, use_sem=False,
                                 val_col=val_col, fluor_gates=fluor_gates)
            rs = _rep_from(app, _safe_rows(app, lbl), pts, target_t, threshold,
                           val_col, fluor_gates, ratios, on_col)
            if rs is not None:
                reps.append(rs)
    else:  # "fov": exactly one loaded well; each of its FOVs is a replicate
        lbl = loaded[0]
        df = _safe_rows(app, lbl)
        if df is None or len(df) == 0:
            # Pre-aggregated backends (MockApp) have no raw rows to split — the
            # well is its own single replicate.
            rs = _rep_from(app, None,
                           aggregate_well(app, lbl, threshold=threshold,
                                          use_sem=False, val_col=val_col,
                                          fluor_gates=fluor_gates),
                           target_t, threshold, val_col, fluor_gates, ratios, on_col)
            if rs is not None:
                reps.append(rs)
        else:
            fov = _norm_fov(df)
            for fov_id in pd.unique(fov):
                sub = df[fov == fov_id]
                pts = aggregate_with_threshold_df(
                    sub, threshold=threshold, use_sem=False, val_col=val_col,
                    fluor_gates=fluor_gates, per_fov_spread=False,
                    ratios=ratios, on_col=on_col)
                rs = _rep_from(app, sub, pts, target_t, threshold, val_col,
                               fluor_gates, ratios, on_col)
                if rs is not None:
                    reps.append(rs)
    return reps


def _reduce_reps(reps, field: str, use_sem: bool) -> tuple[float, float]:
    """Mean and SD/SEM (ddof=1) across a per-replicate field — the shared
    reducer behind every rep-set error bar and the stats spread."""
    vals = [getattr(r, field) for r in reps
            if math.isfinite(getattr(r, field))]
    if not vals:
        return float("nan"), 0.0
    arr = np.asarray(vals, dtype=float)
    n = arr.size
    m = float(arr.mean())
    sd = float(arr.std(ddof=1)) if n > 1 else 0.0
    return m, (sd / math.sqrt(n) if (use_sem and n > 1) else sd)


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


def _gate_cache_sig(app):
    """Signature of the scatter-gate on-population state for the stats cache.

    ``threshold`` alone does not capture gate-based fraction-on: when scatter-gate
    thresholding is active, the on-population is *gate membership*, so a gate
    resize/rename changes the computed fraction/n without changing ``threshold``.
    Folding the active gates' full definition into the cache key stops a gate
    edit from returning a stale cached statistic (audit S4). Returns ``None`` in
    plain threshold mode, leaving the existing keys byte-identical there.
    """
    if _thresh_on_col(app) is None:
        return None
    gates = getattr(app, "_scatter_gates", None) or []
    choices = getattr(app, "_cell_gating_thresh_choices", None)
    try:
        gates_key = json.dumps(gates, sort_keys=True, default=str)
    except (TypeError, ValueError):
        gates_key = repr(gates)
    choices_key = None if choices is None else tuple(sorted(map(str, choices)))
    return ("Gate_On", choices_key, gates_key)


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
            if tp_close(pt[0], target_t):
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
                  app._app_state.channel.active_val_col, tuple(sorted(fluor_gates.items())), _gate_cache_sig(app))
    if cache_key in app._app_state.stats_cache.cache:
        return app._app_state.stats_cache.cache[cache_key]

    reps = group_replicate_stats(
        app, rset.wells, target_t, use_fov=False,
        val_col=app._app_state.channel.active_val_col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    gm, gerr = _reduce_reps(reps, "mean", use_sem)
    gf, ferr = _reduce_reps(reps, "frac", use_sem)

    result = (gm, gerr, gf, ferr)
    app._app_state.stats_cache.cache[cache_key] = result
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
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(app))
    if cache_key in app._app_state.stats_cache.cache:
        return app._app_state.stats_cache.cache[cache_key]

    # Per-FOV replicates come from the shared producer (``use_fov=True``), so the
    # spread here is taken across the exact same FOV replicate values the Stats
    # tab tests. For a single-well set that's the well's FOVs; the call site only
    # routes here when ``replicate_unit`` is "fov".
    reps = group_replicate_stats(
        app, rset.wells, target_t, use_fov=True,
        val_col=app._app_state.channel.active_val_col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    gm, gerr = _reduce_reps(reps, "mean", use_sem)
    gf, ferr = _reduce_reps(reps, "frac", use_sem)
    nm, ns = _reduce_reps(reps, "n_above", use_sem)
    result = (gm, gerr, gf, ferr,
              (nm if math.isfinite(nm) else 0.0), ns)
    app._app_state.stats_cache.cache[cache_key] = result
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
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(app))
    if cache_key in app._app_state.stats_cache.cache:
        return app._app_state.stats_cache.cache[cache_key]

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
        matched = [pt for pt in pts if tp_close(pt[0], target_t)]
        if matched:
            # AggPoint index 4 = n_above; index 5 = n_total.
            total += int(matched[0][4])
    app._app_state.stats_cache.cache[cache_key] = total
    return total


def compute_rep_n_above_stats(app, rset: "ReplicateSet", target_t: float,
                              use_sem: bool) -> tuple:
    """Mean and SD/SEM of events-above-threshold *across the set's wells*.

    Each loaded well contributes one ``n_above`` value (AggPoint index 4); the
    spread is the ddof=1 SD (or SEM, when ``use_sem``) across those per-well
    counts. Returns ``(mean_n_above, spread)`` — the replicate version of the
    Events panel, mirroring how :func:`compute_rep_stats` aggregates the Mean
    and Fraction panels (a per-well mean with a cross-well spread), so the
    Events panel gets replicate error bars instead of a spread-less total.
    """
    threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
    fluor_gates = app._get_all_fluor_gates()
    cache_key = ("rep_n_above_stats", rset.name, tuple(rset.wells), target_t,
                 threshold, use_sem, app._app_state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(app))
    if cache_key in app._app_state.stats_cache.cache:
        return app._app_state.stats_cache.cache[cache_key]

    reps = group_replicate_stats(
        app, rset.wells, target_t, use_fov=False,
        val_col=app._app_state.channel.active_val_col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    mean, spread = _reduce_reps(reps, "n_above", use_sem)
    result = ((mean if math.isfinite(mean) else 0.0), spread)
    app._app_state.stats_cache.cache[cache_key] = result
    return result


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


