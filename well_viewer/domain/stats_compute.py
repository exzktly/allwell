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

import numpy as np
import pandas as pd

from dataclasses import dataclass

from well_viewer.data.data_loading import (
    aggregate_with_threshold_df,
    df_included_mask,
    get_rows,
    resolve_value_series,
)
from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.domain.text_columns import fov_text
from well_viewer.domain.tp_match import tp_close, tp_close_mask
from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates,
    on_cutoff_for_column,
)


# ── Unified replicate model (single source of truth for BOTH stats and plots) ──

def replicate_unit(n_wells: int, use_fov: bool) -> str:
    """What counts as one replicate for a group, given its loaded well count and
    the per-FOV toggle. The ONE rule shared by the Statistics test and the bar/
    line error bars so the test's ``n`` and the plotted spread never disagree.

    - FOV mode ON -> ``"fov"``: every FOV (across *all* the group's loaded wells)
      is an independent replicate — the FOVs are pooled across wells, so a
      multi-well group gets ``n`` = total FOVs, not the well count.
    - FOV mode OFF, ``>=1`` well -> ``"well"``: each well is a replicate (its FOVs
      pooled). A single well then yields ``n=1`` (no spread; the stats test, needing
      ``n>=2``, declines it).
    - ``0`` wells -> ``"none"``.
    """
    if n_wells <= 0:
        return "none"
    if use_fov:
        return "fov"
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


def _norm_fov(df: pd.DataFrame) -> np.ndarray:
    """Normalized FOV id per row (empty -> "1"), matching the plot aggregator."""
    return fov_text(df).to_numpy()


def _safe_rows(ctx, lbl):
    """The well's raw DataFrame from the loaded-row cache, or None when the
    backend exposes only pre-aggregated points (the pure-logic MockApp seeds no
    cache). Reads via the row repository (``rows_or_none`` = cache-only, no lazy
    load) — the same cache the row service fills, so a real app has the frame
    here. The MockApp builds ``AppState`` directly (no ``ViewerContext``), so its
    ``repo`` is ``None`` → ``None``, matching the old empty-cache behaviour (B3)."""
    repo = ctx.state.repo
    return repo.rows_or_none(lbl) if repo is not None else None


def _point_at(pts, target_t) -> tuple | None:
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


def _rep_from(df, pts, target_t, threshold, val_col, fluor_gates, ratios,
              on_col) -> ReplicateStat | None:
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


def group_replicate_stats(ctx, wells, target_t, *, use_fov, val_col, threshold,
                          fluor_gates=None) -> list[ReplicateStat]:
    """THE canonical producer of per-replicate summaries for a group at a
    timepoint. One record per replicate — per well, or per FOV (pooled across all
    of the group's wells) when ``replicate_unit`` is ``"fov"``; ``[]`` when there
    are no loaded wells. Statistics and both plot error-bar paths reduce this same
    list, so they can never describe different units of replication. Mean /
    fraction / n come from :func:`aggregate_well` (the same aggregator the plots
    use, honoring the MockApp delegate)."""
    loaded = [w for w in wells if w in ctx.state.dataset.well_paths]
    unit = replicate_unit(len(loaded), use_fov)
    if unit == "none":
        return []
    if fluor_gates is None:
        fluor_gates = get_all_fluor_gates(ctx)
    ratios = ctx.state.channel.ratio_index
    on_col = _thresh_on_col(ctx)
    reps: list[ReplicateStat] = []
    if unit == "well":
        for lbl in loaded:
            pts = aggregate_well(ctx, lbl, threshold=threshold, use_sem=False,
                                 val_col=val_col, fluor_gates=fluor_gates)
            rs = _rep_from(_safe_rows(ctx, lbl), pts, target_t, threshold,
                           val_col, fluor_gates, ratios, on_col)
            if rs is not None:
                reps.append(rs)
    else:  # "fov": every FOV across all loaded wells is one replicate (pooled)
        for lbl in loaded:
            df = _safe_rows(ctx, lbl)
            if df is None or len(df) == 0:
                # Pre-aggregated backends (MockApp) have no raw rows to split —
                # the well contributes a single replicate.
                rs = _rep_from(None,
                               aggregate_well(ctx, lbl, threshold=threshold,
                                              use_sem=False, val_col=val_col,
                                              fluor_gates=fluor_gates),
                               target_t, threshold, val_col, fluor_gates, ratios,
                               on_col)
                if rs is not None:
                    reps.append(rs)
                continue
            fov = _norm_fov(df)
            for fov_id in pd.unique(fov):
                sub = df[fov == fov_id]
                pts = aggregate_with_threshold_df(
                    sub, threshold=threshold, use_sem=False, val_col=val_col,
                    fluor_gates=fluor_gates, per_fov_spread=False,
                    ratios=ratios, on_col=on_col)
                rs = _rep_from(sub, pts, target_t, threshold, val_col,
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


def _thresh_on_col(ctx) -> str | None:
    """Return ``"Gate_On"`` when scatter-gate *thresholding* is active (mode on
    and a gate selected), else ``None``.

    Picks the fraction-on definition for :func:`aggregate_with_threshold_df`:
    with ``"Gate_On"`` the on-population is gate membership (fraction-on by
    gate) instead of ``value > ThreshFracOn``. Inlined here (no
    ``well_viewer.features`` import) to keep the domain layer acyclic — see
    ``tests/test_layering.py``.

    A8 R-leaf: takes ``ctx`` — the scatter-gate / cell-gating flags read through
    ``ctx.runtime`` (the same stamped attributes, without naming the god-object).
    """
    if not ctx.state.cell_gating.use_scatter_thresh:
        return None
    gates = ctx.state.cell_gating.scatter_gates or []
    if not gates:
        return None
    # Thresholding has its own gate selection (a set of names; None ⇒ all). Fall
    # back to the ThreshFracOn cutoff (return None) when the user has explicitly
    # selected no existing gate for the thresholding layer.
    choices = ctx.state.cell_gating.thresh_choices
    if choices is not None:
        chosen = set(choices)
        if not any(str(g.get("name") or "") in chosen for g in gates):
            return None
    return "Gate_On"


def _gate_cache_sig(ctx) -> tuple | None:
    """Signature of the scatter-gate state for the stats cache — both layers.

    Neither layer is captured by the rest of the key:

    * **Thresholding.** ``threshold`` alone does not capture gate-based
      fraction-on: when scatter-gate thresholding is active the on-population is
      gate *membership*, so a gate resize/rename changes the computed fraction/n
      without changing ``threshold`` (audit S4).
    * **Inclusion.** "Use scatter gates" replaces the per-channel FluorGating
      floors: the worker sets ``Included=1`` and puts membership in
      ``Gate_Pass``. The key already folds in ``fluor_gates``, but those values
      do not move when the checkbox is toggled — so every cached statistic came
      back unchanged and ticking the box appeared to do nothing, while editing a
      numeric threshold (which does move ``fluor_gates``) worked. Note the flag
      matters even with no gates drawn: mode-on-no-gates still bypasses the
      intensity floors, so it is not the same population as mode-off.

    Returns ``None`` when neither layer is active, leaving the keys of a plain
    threshold-mode session byte-identical.
    """
    thresh_col = _thresh_on_col(ctx)
    include_by_gate = bool(ctx.state.cell_gating.use_scatter_gates)
    if thresh_col is None and not include_by_gate:
        return None
    gates = ctx.state.cell_gating.scatter_gates or []
    try:
        gates_key = json.dumps(gates, sort_keys=True, default=str)
    except (TypeError, ValueError):
        gates_key = repr(gates)

    def _choices_key(choices):
        return None if choices is None else tuple(sorted(map(str, choices)))

    # Leading element stays ``"Gate_On"`` whenever thresholding is active, so a
    # reader (and the existing test) can tell the layers apart at a glance.
    return (
        thresh_col or "Gate_Pass",
        _choices_key(ctx.state.cell_gating.thresh_choices) if thresh_col else None,
        include_by_gate,
        _choices_key(ctx.state.cell_gating.gating_choices) if include_by_gate else None,
        gates_key,
    )


def compute_rep_stats(
    ctx,
    rset: ReplicateSet,
    target_t: float,
    threshold: float,
    use_sem: bool,
) -> tuple:
    """
    Mean fluor ± SD/SEM for a single ReplicateSet at *target_t*.

    Statistics are computed across the loaded wells of the set.
    Each well contributes one mean-fluor value; SD/SEM is across those values.
    Results are cached in _stats_cache.

    Reduces :func:`group_replicate_stats` (the ONE canonical per-replicate
    producer) via :func:`_reduce_reps`. The scatter-aggregate plot's
    ``_agg_wells`` reduces the very same producer, so all three plot paths
    (line / bar / scatter-agg) — and the Stats tab — compute "mean across
    replicates under active gating" identically in both well and FOV modes.
    """
    fluor_gates = get_all_fluor_gates(ctx)
    cache_key = ("rep", rset.name, tuple(rset.wells), target_t, threshold, use_sem,
                  ctx.state.channel.active_val_col, tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    reps = group_replicate_stats(
        ctx, rset.wells, target_t, use_fov=False,
        val_col=ctx.state.channel.active_val_col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    gm, gerr = _reduce_reps(reps, "mean", use_sem)
    gf, ferr = _reduce_reps(reps, "frac", use_sem)

    result = (gm, gerr, gf, ferr)
    ctx.state.stats_cache.cache[cache_key] = result
    return result


def compute_rep_per_fov_stats(
    ctx,
    rset: ReplicateSet,
    target_t: float,
    threshold: float,
    use_sem: bool,
) -> tuple:
    """Pool every FOV across all loaded wells of *rset* and compute stats.

    Returns ``(mean, mean_err, frac, frac_err, n_above_pf_mean,
    n_above_pf_spread)`` where the error fields are the SD/SEM across the
    pooled per-FOV means/fractions/counts. Each FOV of each well is one
    replicate (``group_replicate_stats`` processes the wells' frames
    separately, so identical FOV numbers in different wells stay distinct).
    """
    fluor_gates = get_all_fluor_gates(ctx)
    cache_key = ("rep_pf", rset.name, tuple(rset.wells), target_t, threshold,
                 use_sem, ctx.state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    # Per-FOV replicates come from the shared producer (``use_fov=True``), so the
    # spread here is taken across the exact same FOV replicate values the Stats
    # tab tests: every FOV across all of the set's loaded wells. The call site
    # only routes here when ``replicate_unit`` is "fov" (i.e. FOV mode on).
    reps = group_replicate_stats(
        ctx, rset.wells, target_t, use_fov=True,
        val_col=ctx.state.channel.active_val_col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    gm, gerr = _reduce_reps(reps, "mean", use_sem)
    gf, ferr = _reduce_reps(reps, "frac", use_sem)
    nm, ns = _reduce_reps(reps, "n_above", use_sem)
    result = (gm, gerr, gf, ferr,
              (nm if math.isfinite(nm) else 0.0), ns)
    ctx.state.stats_cache.cache[cache_key] = result
    return result


def compute_rep_n_above_total(ctx, rset: ReplicateSet, target_t: float) -> int:
    """Cross-well **total** events-above-threshold for *rset* at *target_t*.

    A reporting figure, not a plotted one. The CSV's ``n_above_threshold``
    column has always carried the number of cells that exceeded the threshold
    across the whole replicate set, and downstream scripts read it as an
    integer count.

    **Never use this as a plotted value.** Every panel draws
    :func:`compute_rep_n_above_stats` — the mean of the per-well counts with
    the cross-well spread — because that is the statistic the Mean and
    Fraction panels beside it use, and because a set normalised against itself
    has to land on 1.0 rather than 1/n. Reaching for a total because its name
    was shorter is exactly how the two plotting tabs came to disagree; the
    guard in ``tests/test_events_total_isolation.py`` fails if a renderer or a
    collector of plotted values imports this.
    """
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)
    fluor_gates = get_all_fluor_gates(ctx)
    cache_key = ("rep_n_above_total", rset.name, tuple(rset.wells), target_t,
                 threshold, ctx.state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    total = 0
    for lbl in rset.wells:
        if lbl not in ctx.state.dataset.well_paths:
            continue
        pts = aggregate_well(
            ctx,
            lbl, threshold=threshold, use_sem=False,
            val_col=ctx.state.channel.active_val_col,
            fluor_gates=fluor_gates,
        )
        matched = [pt for pt in pts if tp_close(pt[0], target_t)]
        if matched:
            # AggPoint index 4 = n_above; index 5 = n_total.
            total += int(matched[0][4])
    ctx.state.stats_cache.cache[cache_key] = total
    return total


def compute_rep_n_above_stats(ctx, rset: ReplicateSet, target_t: float,
                              use_sem: bool) -> tuple:
    """Mean and SD/SEM of events-above-threshold *across the set's wells*.

    Each loaded well contributes one ``n_above`` value (AggPoint index 4); the
    spread is the ddof=1 SD (or SEM, when ``use_sem``) across those per-well
    counts. Returns ``(mean_n_above, spread)`` — the replicate version of the
    Events panel, mirroring how :func:`compute_rep_stats` aggregates the Mean
    and Fraction panels (a per-well mean with a cross-well spread), so the
    Events panel gets replicate error bars instead of a spread-less total.
    """
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)
    fluor_gates = get_all_fluor_gates(ctx)
    cache_key = ("rep_n_above_stats", rset.name, tuple(rset.wells), target_t,
                 threshold, use_sem, ctx.state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    reps = group_replicate_stats(
        ctx, rset.wells, target_t, use_fov=False,
        val_col=ctx.state.channel.active_val_col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    mean, spread = _reduce_reps(reps, "n_above", use_sem)
    result = ((mean if math.isfinite(mean) else 0.0), spread)
    ctx.state.stats_cache.cache[cache_key] = result
    return result


def aggregate_well(
    ctx,
    label: str,
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates: dict[str, float] | None = None,
    per_fov_spread: bool = False,
    tp_col: str = "timepoint_hours",
):
    """Aggregate a single well via the vectorized DataFrame path.

    If the app exposes a callable ``_aggregate_well`` (the pure-logic
    test MockApp does, returning precomputed AggPoints from its
    in-memory map), delegate to it. Production ``WellViewerApp`` does
    not define this method, so the DataFrame path is used at runtime.

    A8 R-leaf: takes ``ctx`` — rows via the ctx-native ``get_rows`` data
    service, ratios/gate-on state via ``ctx.state`` / ``ctx.runtime``. The
    optional aggregation delegate is resolved through ``ctx.runtime.aggregate``
    (backed by ``app._aggregate_well``) instead of naming the god-object.
    """
    delegate = ctx.runtime.aggregate.well
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
        get_rows(ctx, label),
        threshold=threshold,
        use_sem=use_sem,
        tp_col=tp_col,
        val_col=val_col,
        fluor_gates=fluor_gates or {},
        per_fov_spread=per_fov_spread,
        ratios=ctx.state.channel.ratio_index,
        on_col=_thresh_on_col(ctx),
    )


def aggregate_group(
    ctx,
    wells: list[str],
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates: dict[str, float] | None = None,
    per_fov_spread: bool = False,
    tp_col: str = "timepoint_hours",
):
    """Aggregate a list of wells via the vectorized path (concatenated when len>1).

    A8 R-leaf: takes ``ctx`` — see :func:`aggregate_well`. The optional group
    delegate resolves through ``ctx.runtime.aggregate`` (backed by
    ``app._aggregate_group``) instead of naming the god-object.
    """
    delegate = ctx.runtime.aggregate.group
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
    valid = [w for w in wells if w in ctx.state.dataset.well_paths]
    if not valid:
        return []
    if len(valid) == 1:
        return aggregate_well(
            ctx,
            valid[0], threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates, per_fov_spread=per_fov_spread,
            tp_col=tp_col,
        )
    frames = []
    for w in valid:
        sub = get_rows(ctx, w)
        if per_fov_spread and len(sub) > 0:
            # Prefix FOV ids with the well label so identical FOV numbers
            # across replicate wells stay distinct when pooled.
            fov_str = fov_text(sub)
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
        ratios=ctx.state.channel.ratio_index,
        on_col=_thresh_on_col(ctx),
    )


