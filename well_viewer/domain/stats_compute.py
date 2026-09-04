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
import logging
import math

import numpy as np
import pandas as pd

from dataclasses import dataclass, replace

from well_viewer.data.data_loading import (
    aggregate_with_threshold_df,
    df_included_mask,
    get_rows,
    resolve_value_series,
)
from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.domain.text_columns import fov_text
from well_viewer.domain.fov_integrity import check_fov_labels
from well_viewer.domain.ratio_models import RATIO_PREFIX
from well_viewer.domain.tp_match import tp_close, tp_close_mask
from well_viewer.domain.gating_state import active_fluor_gates
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
    # Typed float, not int: a well whose FOV labels are provably wrong has a
    # pooled mean and fraction that are still correct, and a per-field count
    # that is undefined. NaN is how "undefined" is spelled everywhere else in
    # this module, and an int cannot hold it.
    n_above: float
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
    # Same "a 0.0 gate is off" rule the aggregation applies, so the median
    # and mean-of-all a ReplicateStat carries describe the same population
    # as the mean, fraction and count beside them.
    for channel, gate in active_fluor_gates(fluor_gates).items():
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


_logger = logging.getLogger(__name__)


def _fov_verdict(ctx, label: str, df):
    """The FOV-label verdict for *label*, computed at most once per well.

    ``data_loading.get_rows`` records the verdict on
    ``ctx.state.dataset.fov_integrity`` as it builds a well's frame — but the
    eager load path (``load_controller.load_directory``) fills
    ``dataset.cache`` itself and never calls ``get_rows``, so in a real session
    that dict is empty and a reader that only *consults* it recomputes every
    time. Checking the labels is two ``groupby.nunique`` passes and a full
    ``duplicated`` over the whole well — 14 ms on a 21k-row well — and this
    runs per well, per timepoint, per group, per channel, which is roughly
    fifteen hundred calls in a batch export. So a miss is written back rather
    than merely tolerated.

    (The same empty dict is why ``load_controller._report_unusable_fov_labels``
    has nothing to name at load time. Fixing that belongs with the load path,
    not here.)
    """
    store = getattr(ctx.state.dataset, "fov_integrity", None)
    if store is None:
        return check_fov_labels(df)
    cached = store.get(label)
    if cached is not None:
        return cached
    verdict = check_fov_labels(df)
    store[label] = verdict
    return verdict


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
            # Memoized: three separately-keyed producers (compute_rep_stats,
            # compute_rep_n_above_stats, compute_rep_n_above_total) each call
            # this function for the same wells at the same timepoint, and each
            # call re-aggregated the whole well to keep one timepoint's row. On
            # a plate of twelve groups over six timepoints that was 1,824 full
            # passes to draw twelve curves; through the memo the distinct keys
            # number one per well.
            pts = aggregate_well_cached(ctx, lbl, threshold=threshold,
                                        use_sem=False, val_col=val_col,
                                        fluor_gates=fluor_gates)
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
                               aggregate_well_cached(ctx, lbl, threshold=threshold,
                                              use_sem=False, val_col=val_col,
                                              fluor_gates=fluor_gates),
                               target_t, threshold, val_col, fluor_gates, ratios,
                               on_col)
                if rs is not None:
                    reps.append(rs)
                continue
            verdict = _fov_verdict(ctx, lbl, df)
            if not verdict.trusted:
                # Every row of this well carries the same field label because
                # the filenames did not match the schema, so splitting on that
                # column yields one "replicate" holding the pooled total — the
                # per-field mean multiplied by however many fields were pooled.
                # ``aggregate_with_threshold_df`` is the model to copy here: it
                # keeps the well's mean and fraction, which are correct pooled
                # statistics whatever the field labels say, and NaNs only the
                # quantities that are genuinely per-field. Dropping the well
                # outright instead would remove real cells from the Mean and
                # Fraction panels, and would make a *solo* untrusted well's bar
                # vanish entirely — indistinguishable from having no data.
                _logger.warning(
                    "FOV labels are unusable for %s (%s) — it contributes one "
                    "pooled replicate with an undefined per-field count, "
                    "rather than a pooled total wearing a per-field label.",
                    lbl, verdict.reason,
                )
                rs = _rep_from(df,
                               aggregate_well_cached(ctx, lbl, threshold=threshold,
                                              use_sem=False, val_col=val_col,
                                              fluor_gates=fluor_gates),
                               target_t, threshold, val_col, fluor_gates, ratios,
                               on_col)
                if rs is not None:
                    reps.append(replace(rs, n_above=float("nan")))
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
    reducer behind every rep-set error bar and the stats spread.

    **An undefined spread is ``NaN``, never ``0.0``.** One replicate has no
    spread; that is not the same fact as a set whose replicates agreed exactly,
    and ``0.0`` is how the two became indistinguishable — a single-well set
    exported ``err_SD = 0.000000`` and drew a bar with no whisker beside sets
    whose whiskers were measured. ``_aggregate_arrays`` and
    ``export.service.bar_metric_row`` each already carry a paragraph promising
    this; this is the producer that was not keeping it. Non-finite spreads are
    blanked by the CSV writers and draw no whisker in matplotlib, which is the
    rendering "undefined" should have.
    """
    vals = [getattr(r, field) for r in reps
            if math.isfinite(getattr(r, field))]
    if not vals:
        return float("nan"), float("nan")
    arr = np.asarray(vals, dtype=float)
    n = arr.size
    m = float(arr.mean())
    if n < 2:
        return m, float("nan")
    sd = float(arr.std(ddof=1))
    return m, (sd / math.sqrt(n) if use_sem else sd)


def replicate_mean(reps, field: str) -> float:
    """The mean across a per-replicate field — the value Bar and Line draw.

    A public name for the reduction half of :func:`_reduce_reps`, for consumers
    that want the central value and not the spread. Sharing the reducer is the
    point: it skips non-finite replicates the same way, so a surface using this
    cannot disagree with a bar drawn from the same replicate list.
    """
    mean, _spread = _reduce_reps(reps, field, use_sem=False)
    return mean


def replicate_mean_and_spread(reps, field: str, *, use_sem: bool) -> tuple[float, float]:
    """``(mean, SD-or-SEM)`` across a per-replicate field, from one reduction.

    The public name for the whole of :func:`_reduce_reps`, for a surface that
    draws the central value and its error together — the Heat Map's per-cell
    ``± err`` is the caller this was named for. Calling
    :func:`replicate_mean` and then reducing the list a second time for the
    spread would give the same answer at twice the cost, and would leave two
    call sites free to drift apart about which replicates are skipped.

    ``use_sem`` is the app-wide Properties → Statistics choice
    (``ctx.state.plot.use_sem``), so a cell's error bar and the bar chart's
    whisker for the same set measure the same thing. The spread of a
    single-replicate set is ``NaN``, never ``0.0`` — see :func:`_reduce_reps`.
    """
    return _reduce_reps(reps, field, use_sem=use_sem)


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


def _ratio_cache_sig(ctx) -> tuple | None:
    """Signature of the active ratio's *definition*, for the stats cache keys.

    Every key folds in ``active_val_col``, which for a ratio metric is the bare
    string ``"ratio:<name>"``. The numerator, denominator, per-channel metrics
    and epsilon live in ``ctx.state.channel.ratio_index`` and appear in no key
    at all, so editing a ratio *in place* — keeping its name — left every cached
    replicate-set statistic valid by key and wrong by value, while the per-well
    bars beside them recomputed.

    Two details this has to get right, and got wrong first time round.
    ``ratio_index`` is keyed by ``RatioMetric.key()`` — the full ``"ratio:R"``
    string, not the bare name — so looking up the split-off name resolves
    nothing; and the fields are ``numerator_channel`` / ``numerator_metric`` /
    ``denominator_channel`` / ``denominator_metric``, not ``numerator`` /
    ``denominator`` / ``aggregation``. Either mistake alone makes this a
    constant, i.e. a signature that signs nothing.

    Returns ``None`` when the active column is not a ratio, leaving a
    plain-channel session's keys byte-identical.
    """
    col = str(ctx.state.channel.active_val_col or "")
    if not col.startswith(RATIO_PREFIX):
        return None
    ratio = (ctx.state.channel.ratio_index or {}).get(col)
    if ratio is None:
        return (col,)
    return (
        col,
        ratio.numerator_channel,
        ratio.numerator_metric,
        ratio.denominator_channel,
        ratio.denominator_metric,
        repr(ratio.epsilon),
    )


def _ratio_sig_for(ctx, val_col: str) -> tuple | None:
    """Signature of the ratio definition *this call* will actually resolve.

    :func:`_ratio_cache_sig` signs ``ctx.state.channel.active_val_col``, which is
    right for the replicate-set keys because those read the active column. The
    per-well memo below does not: :func:`aggregate_well` resolves the ``val_col``
    it was *handed*, and the two coincide today only because
    ``batch_export.base_panel._app_val_col_scope`` swaps the active column for
    the duration of an off-screen export.

    That is a caller-side convention holding up a cache key. A future caller
    passing ``val_col`` without the swap would get a key signing a different
    ratio's definition than the one it computes with, so editing ratio B in
    place while ratio A is active would return B's stale numbers under A's key.
    Signing the argument closes that off regardless of what any caller does.
    """
    col = str(val_col or "")
    if not col.startswith(RATIO_PREFIX):
        return None
    ratio = (ctx.state.channel.ratio_index or {}).get(col)
    if ratio is None:
        return (col,)
    return (
        col,
        ratio.numerator_channel,
        ratio.numerator_metric,
        ratio.denominator_channel,
        ratio.denominator_metric,
        repr(ratio.epsilon),
    )


def aggregate_well_cached(
    ctx,
    label: str,
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates: dict[str, float] | None = None,
    per_fov_spread: bool = False,
    tp_col: str = "timepoint_hours",
) -> tuple:
    """:func:`aggregate_well`, memoized per well for the duration of the inputs.

    The collectors call this; :func:`aggregate_well` itself is left alone. That
    split is deliberate rather than stylistic. ``aggregate_well`` reads exactly
    four things off ``ctx`` — the aggregation delegate, the ratio index, the
    thresholding on-column, and the dataset — and a number of pure-logic tests
    build hand-rolled contexts that satisfy precisely that surface and nothing
    else. Memoizing inside it widens its contract to include the stats cache and
    the gate signature, and those tests fail on the attributes they never had to
    supply. Widening a domain function's dependencies to buy a cache would trade
    a real architectural property for a speed-up that belongs a layer up.

    **Why this is worth the risk it carries.** Selecting one more well used to
    re-aggregate every well already selected: the per-well plot branches called
    ``aggregate_well`` straight through, so a ninety-sixth click cost
    ninety-six full passes over 21,600-row frames. Only the replicate-set
    results were ever cached.

    The key is the whole of the correctness, so each part earns its place:

    ``label`` / ``val_col`` / ``threshold`` / ``use_sem`` / ``per_fov_spread`` /
    ``tp_col``
        The arguments the computation is a function of. ``use_sem`` and
        ``per_fov_spread`` change which spread is reported and which trailing
        per-FOV fields are filled, not merely how a number is presented.

    ``fluor_gates``
        Per-channel gates filter rows out before anything is computed.

    :func:`_gate_cache_sig`
        The scatter-gate layers, including the thresholding on-column that
        :func:`_thresh_on_col` resolves — none of which appears in the arguments.

    :func:`_ratio_sig_for`
        The ratio *definition* behind ``val_col``, keyed on the argument rather
        than on the active column. See that function.

    ``dataset.rows_version``
        Everything about the frame itself. QC and crop verdicts, gating
        rewrites, manual curation overrides, a frame replaced after a write-back
        and a whole dataset swap all change what the rows say without changing
        any argument here. ``tests/test_row_cache_writers.py`` enumerates those
        sites from the syntax tree, so a new one fails the build rather than
        silently returning yesterday's numbers.

    Read the version into the key *before* computing, never after: the gating
    worker mutates frames from a ``QThread``, and a version sampled afterwards
    could stamp a pre-edit result with a post-edit tick.

    The result is a ``tuple``. ``aggregate_well`` returns a fresh ``list`` on
    every call and several callers treat that as theirs to keep; handing the
    same list to all of them would make one caller's edit everyone's.
    """
    delegate = ctx.runtime.aggregate.well
    if callable(delegate):
        # A test backend answers from its own in-memory map, which the test may
        # rewrite between calls. It is not ours to cache.
        return tuple(aggregate_well(
            ctx, label, threshold=threshold, use_sem=use_sem, val_col=val_col,
            fluor_gates=fluor_gates, per_fov_spread=per_fov_spread,
            tp_col=tp_col,
        ) or ())

    store = getattr(ctx.state, "stats_cache", None)
    if store is None:  # a context too thin to cache with: compute, don't guess
        return tuple(aggregate_well(
            ctx, label, threshold=threshold, use_sem=use_sem, val_col=val_col,
            fluor_gates=fluor_gates, per_fov_spread=per_fov_spread,
            tp_col=tp_col,
        ) or ())

    key = (
        "well_agg", label, val_col, threshold, use_sem, per_fov_spread, tp_col,
        tuple(sorted((fluor_gates or {}).items())),
        _gate_cache_sig(ctx),
        _ratio_sig_for(ctx, val_col),
        ctx.state.dataset.rows_version,
    )
    hit = store.cache.get(key)
    if hit is not None:
        return hit
    result = tuple(aggregate_well(
        ctx, label, threshold=threshold, use_sem=use_sem, val_col=val_col,
        fluor_gates=fluor_gates, per_fov_spread=per_fov_spread, tp_col=tp_col,
    ) or ())
    store.cache[key] = result
    return result


def cdf_values_cached(ctx, label: str, *, val_col: str,
                      fluor_gates: dict[str, float] | None, compute):
    """The per-cell values behind one well's CDF curve, memoized.

    The CDF panel needs every included cell's value, not an aggregate, so this
    is a filter over the whole frame rather than a reduction of it — and it ran
    once per well on every redraw. With the aggregates memoized it became almost
    the entire remaining cost of a collect: 95 ms of 99 ms for ninety-six wells.

    *compute* is the filter itself, passed in rather than imported, because the
    caller already receives it as a parameter and a second copy resolved here
    could drift from the one actually used. Its identity is part of the key, so
    a caller supplying a different filter cannot be served another's answer.

    Keyed like :func:`aggregate_well_cached` minus the arguments that do not
    reach it: no threshold and no ``use_sem``, because the CDF draws the raw
    filtered population rather than a statistic of it. ``rows_version`` carries
    everything about the frame, so a gating pass or a dataset swap declines
    here exactly as it does there.

    **This hands every caller the same array**, where the aggregate memo goes to
    the trouble of returning a tuple. The asymmetry is deliberate but it is a
    hazard worth naming: an ndarray has no immutable counterpart to convert to
    without copying the very thing the memo exists to avoid copying, and a
    plate-wide selection is tens of megabytes of them. No consumer mutates it
    today — the painter sorts out of place, the collector concatenates, the
    x-range reads — and one that did would corrupt every other caller's curve.
    If a mutating consumer ever appears, give it ``.copy()`` at that call site
    rather than making this one copy for everybody.
    """
    store = getattr(ctx.state, "stats_cache", None)
    rows = get_rows(ctx, label)
    if store is None:
        return compute(rows, val_col=val_col, fluor_gates=fluor_gates,
                       ratios=ctx.state.channel.ratio_index)
    key = (
        "well_cdf", label, val_col,
        tuple(sorted((fluor_gates or {}).items())),
        _ratio_sig_for(ctx, val_col),
        ctx.state.dataset.rows_version,
        # The callable itself, not ``id(compute)``. An id is unique only among
        # *live* objects, and a key tuple holding the bare integer keeps no
        # reference to the function — so a caller passing a partial, a bound
        # method or a closure could see that id reused after collection and be
        # served another filter's answer. Functions, bound methods and partials
        # are all hashable, and holding the reference is what makes the identity
        # mean anything.
        compute,
    )
    hit = store.cache.get(key)
    if hit is not None:
        return hit
    result = compute(rows, val_col=val_col, fluor_gates=fluor_gates,
                     ratios=ctx.state.channel.ratio_index)
    store.cache[key] = result
    return result


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
    *,
    val_col: str | None = None,
) -> tuple:
    """
    Mean fluor ± SD/SEM for a single ReplicateSet at *target_t*.

    *val_col* overrides the shared ``ctx.state.channel.active_val_col`` for a
    tab that owns its own Property control rather than reading the plotting
    band's — Response Curves, as the aggregate Scatter tab already does with
    its own combos. ``None`` keeps the shared column, so every existing caller
    is unchanged. The resolved column is what goes into the cache key, so a
    tab-local choice cannot collide with the shared one in the cache.

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
    col = val_col or ctx.state.channel.active_val_col
    cache_key = ("rep", rset.name, tuple(rset.wells), target_t, threshold, use_sem,
                  col, tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx), _ratio_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    reps = group_replicate_stats(
        ctx, rset.wells, target_t, use_fov=False,
        val_col=col,
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
    *,
    val_col: str | None = None,
) -> tuple:
    """Pool every FOV across all loaded wells of *rset* and compute stats.

    *val_col* overrides the shared active column, exactly as in
    :func:`compute_rep_stats` — the two are switched between by one caller on
    the strength of a toggle, so a seam on one and not the other would make a
    tab-local Property silently revert the moment Spread: FOV went on.

    Returns ``(mean, mean_err, frac, frac_err, n_above_pf_mean,
    n_above_pf_spread)`` where the error fields are the SD/SEM across the
    pooled per-FOV means/fractions/counts. Each FOV of each well is one
    replicate (``group_replicate_stats`` processes the wells' frames
    separately, so identical FOV numbers in different wells stay distinct).
    """
    fluor_gates = get_all_fluor_gates(ctx)
    col = val_col or ctx.state.channel.active_val_col
    cache_key = ("rep_pf", rset.name, tuple(rset.wells), target_t, threshold,
                 use_sem, col,
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx), _ratio_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    # Per-FOV replicates come from the shared producer (``use_fov=True``), so the
    # spread here is taken across the exact same FOV replicate values the Stats
    # tab tests: every FOV across all of the set's loaded wells. The call site
    # only routes here when ``replicate_unit`` is "fov" (i.e. FOV mode on).
    reps = group_replicate_stats(
        ctx, rset.wells, target_t, use_fov=True,
        val_col=col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    gm, gerr = _reduce_reps(reps, "mean", use_sem)
    gf, ferr = _reduce_reps(reps, "frac", use_sem)
    nm, ns = _reduce_reps(reps, "n_above", use_sem)
    # ``nm`` stays NaN when it is undefined. Coercing it to 0.0 put a measured
    # zero and a never-computed value in the same tuple as the mean and
    # fraction beside them, which keep NaN — so the Mean and Fraction panels
    # drew nothing while the Events panel drew a bar at exactly zero.
    result = (gm, gerr, gf, ferr, nm, ns)
    ctx.state.stats_cache.cache[cache_key] = result
    return result


def _on_cutoff(ctx, threshold: float | None) -> float:
    """The caller's cutoff, or the active channel's when it did not name one.

    The two events producers used to ignore what they were passed and always
    re-derive from ``active_channel``, while ``compute_rep_stats`` beside them
    took the caller's number. On the Line tab those agree by coincidence — the
    tab resolves its threshold with the identical expression — but a batch
    export of a channel other than the one the tab is showing does not:
    ``base_panel._app_val_col_scope`` swaps ``active_val_col`` and leaves
    ``active_channel`` alone, so the panel's own cutoff reached
    ``compute_rep_stats`` while the events producers went on reading the tab's.

    Reproduced before it was fixed: exporting ``mcherry`` (cutoff 5.0) while the
    tab showed ``gfp`` (cutoff 50.0) put both numbers into one collect, so the
    Mean and Fraction panels of a single exported figure described one
    population and the Events panel beside them described another.

    The default is kept so every existing caller keeps its behaviour verbatim;
    a caller that knows its cutoff now passes it and is believed.
    """
    if threshold is not None:
        return threshold
    return on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)


def compute_rep_n_above_total(ctx, rset: ReplicateSet, target_t: float,
                              threshold: float | None = None) -> int:
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
    threshold = _on_cutoff(ctx, threshold)
    fluor_gates = get_all_fluor_gates(ctx)
    cache_key = ("rep_n_above_total", rset.name, tuple(rset.wells), target_t,
                 threshold, ctx.state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx), _ratio_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    total = 0
    for lbl in rset.wells:
        if lbl not in ctx.state.dataset.well_paths:
            continue
        # Through the memo like every other per-well aggregation here. This
        # loop was the last one reaching the raw function, and it is called once
        # per group per timepoint — 576 full-well passes on a plate of twelve
        # groups over six timepoints, all of them recomputing wells the
        # neighbouring producers had already aggregated at the same settings.
        pts = aggregate_well_cached(
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
                              use_sem: bool,
                              threshold: float | None = None) -> tuple:
    """Mean and SD/SEM of events-above-threshold *across the set's wells*.

    Each loaded well contributes one ``n_above`` value (AggPoint index 4); the
    spread is the ddof=1 SD (or SEM, when ``use_sem``) across those per-well
    counts. Returns ``(mean_n_above, spread)`` — the replicate version of the
    Events panel, mirroring how :func:`compute_rep_stats` aggregates the Mean
    and Fraction panels (a per-well mean with a cross-well spread), so the
    Events panel gets replicate error bars instead of a spread-less total.
    """
    threshold = _on_cutoff(ctx, threshold)
    fluor_gates = get_all_fluor_gates(ctx)
    cache_key = ("rep_n_above_stats", rset.name, tuple(rset.wells), target_t,
                 threshold, use_sem, ctx.state.channel.active_val_col,
                 tuple(sorted(fluor_gates.items())), _gate_cache_sig(ctx), _ratio_cache_sig(ctx))
    if cache_key in ctx.state.stats_cache.cache:
        return ctx.state.stats_cache.cache[cache_key]

    reps = group_replicate_stats(
        ctx, rset.wells, target_t, use_fov=False,
        val_col=ctx.state.channel.active_val_col,
        threshold=threshold, fluor_gates=fluor_gates,
    )
    mean, spread = _reduce_reps(reps, "n_above", use_sem)
    # NaN, not 0.0 — see the note in ``compute_rep_per_fov_stats``.
    result = (mean, spread)
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


