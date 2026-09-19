"""Two-population threshold optimizer — the shared numeric core.

Picks the threshold best separating two per-cell intensity populations (a
positive- and a negative-control well). The score at each candidate threshold is
the **lower confidence bound on the separation it achieves**: Youden's
``J = TPR - w * FPR`` over the two control wells, reduced by the width of its own
95 % confidence interval, so a cut that looks perfect because almost nothing
survives it scores near zero rather than near one. :func:`supported_separation`
states the arithmetic and :func:`optimize_between_populations` the search.

Shared by ``process_microscopy``'s end-of-run pass and the Cell Gating tab's
"Optimize ThreshFracOn" button, so the two cannot give different answers on the
same data. The pipeline may not import ``review`` (ARCHITECTURE §6.2), so
the core lives here and the viewer's fold-change layer stays behind in
``gating_compute`` — moving it would drag ``domain.fold_change`` into the
pipeline's import graph.

Stdlib + numpy only, and listed in
``services.remote_runner._REQUIRED_REMOTE_FILES``.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import math
from dataclasses import dataclass

import numpy as np


# Defaults for callers with no user to ask (the pipeline); the GUI uses its own
# fields.
#
# ``SWEEP_HI_PERCENTILE`` truncates the *relative-gap* grid, and only that grid.
# That objective is a count ratio, so a handful of saturated cells stretch the
# scan into a region where it can score its maximum on nothing; clipping the top
# half a percent was the cheapest way to keep the argmax inside the data. The
# supported-separation objective needs no such clip — its score collapses in
# that region on its own — and :func:`candidate_thresholds` therefore spans the
# whole pooled range.
DEFAULT_SWEEP_STEPS = 512
SWEEP_HI_PERCENTILE = 99.5
MIN_SWEEP_STEPS = 2
MAX_SWEEP_STEPS = 10001

# 95 % two-sided normal quantile — the confidence level every bound in this
# module is stated at. Written out rather than pulled from ``scipy``: the
# pipeline ships to headless hosts with a deliberately thin dependency set.
DEFAULT_CONFIDENCE_Z = 1.959963984540054

# How much a false positive in the negative control costs relative to a false
# negative in the positive control. 1.0 is plain Youden — the textbook ROC cut
# point, and what the pipeline uses. The Cell Gating optimizer exposes the field
# because the right answer is a property of the assay: background-on in the
# negative control contaminates every other well's fraction-on readout, so an
# assay read against a near-zero baseline wants more than symmetric cost.
DEFAULT_SPECIFICITY_WEIGHT = 1.0

# A candidate threshold must leave at least this many cells above it in one of
# the two wells to be considered at all. The confidence bound already collapses
# where the counts are thin, so this is a floor rather than the mechanism; it
# exists for the fold-change scan in
# ``review.features.home.cell_gating.gating_compute``, whose adjusted fractions
# are not ``k / n`` and therefore have no confidence bound to collapse.
DEFAULT_MIN_SUPPORT = 10

# Ceiling on the exact candidate grid. The grid is one point between each pair
# of consecutive distinct pooled values, so a two-well pair of 100k cells yields
# ~200k candidates; above this many the grid is subsampled evenly. Chosen so the
# spacing is finer than any threshold a user would distinguish while the scan
# stays well under a tenth of a second.
DEFAULT_MAX_CANDIDATES = 20001

# Significant figures kept when a threshold is stored. See
# :func:`round_stored_threshold`.
_STORED_SIG_FIGS = 3

# The two objectives a result can carry. See ``ThreshFracOptimizationResult``.
OBJECTIVE_SUPPORTED = "supported-separation"
OBJECTIVE_RELATIVE_GAP = "relative-gap"


def round_stored_threshold(value: float) -> float:
    """Round an estimated threshold for storage without rounding it away.

    Both estimators used to store ``float(round(x))``, which is right for camera
    counts — a gating cut of 547.7 reads better as 548, and it is the number the
    user can type back into the form — and catastrophic below 1. ``_safe_imwrite``
    writes float32 TIFFs, so a channel normalised to 0–1 is a real input, and its
    Otsu threshold of 0.4048 was stored as **0.0**: a gate that passes every cell
    (measured: 29.9 % of cells ON became 99.8 %), written as the one value a
    reader cannot tell apart from "no threshold was computed", which is exactly
    what both callers mean when they omit a channel.

    So: keep at least three significant figures, and never fewer digits than the
    integer rounding used to give. ``|value| >= 100`` — every real camera-intensity
    threshold — is unchanged, because three significant figures there is already
    the integer. Below that the value keeps enough resolution to stay non-zero:
    0.4048 → 0.405, 0.000123 → 0.000123.

    Non-finite input is returned unchanged; a caller storing a threshold checks
    for that itself rather than letting this function invent a number.
    """
    v = float(value)
    if not math.isfinite(v) or v == 0.0:
        return v
    digits = max(0, (_STORED_SIG_FIGS - 1) - int(math.floor(math.log10(abs(v)))))
    return float(round(v, digits))


@dataclass(frozen=True)
class ThreshFracOptimizationResult:
    """Best threshold scan result for a two-population fraction-on comparison.

    ``tie_count`` and ``tie_span`` describe the *plateau* the reported threshold
    was picked from: how many grid points scored the identical best objective,
    and how wide a range of thresholds those points span. ``argmax`` returns the
    first of them, so without these two numbers a flat run of 73 grid points is
    presented as if the optimiser had found one answer.

    ``objective`` names which score produced ``difference``, because the two are
    not comparable numbers: ``OBJECTIVE_SUPPORTED`` is a lower confidence bound
    on ``TPR - weight * FPR`` (so 0.87 means "at least 87 points of separation,
    at 95 % confidence") while ``OBJECTIVE_RELATIVE_GAP`` is the unitless ratio
    ``|fa-fb| / max(|fa|, |fb|)``, which reaches 1.0 on a single cell. A caller
    printing one of these without naming which it holds is printing a number
    nobody can interpret.

    ``k_a`` / ``k_b`` are the cells left above the reported threshold in each
    well and ``youden`` the point estimate the bound was taken from; all three
    are ``None`` under ``OBJECTIVE_RELATIVE_GAP``, where the scanned fractions
    have been through a fold change and are no longer counts over a total.
    ``support_floor`` is the minimum ``max(k_a, k_b)`` a candidate had to clear
    to be scanned at all, ``0`` when no floor was applied.

    ``tie_span`` is the distance between the lowest and highest tied threshold,
    which is only the width of a *plateau* when the tied points are adjacent on
    the grid. They need not be: a bimodal objective can score its maximum at two
    separated points with lower ground between them, and the span then covers
    thresholds that did not tie at all. ``tie_contiguous`` says which of the two
    a caller is holding, so a log line can describe a range or a set of runs
    rather than claiming a plateau it did not measure.
    """

    threshold: float
    frac_a: float
    frac_b: float
    difference: float
    n_a: int
    n_b: int
    tie_count: int = 1
    tie_span: float = 0.0
    tie_contiguous: bool = True
    objective: str = OBJECTIVE_RELATIVE_GAP
    k_a: int | None = None
    k_b: int | None = None
    youden: float | None = None
    weight: float = DEFAULT_SPECIFICITY_WEIGHT
    support_floor: int = 0


def fraction_on_for_threshold(values: np.ndarray, threshold: float) -> float:
    """Fraction of *values* strictly above *threshold*; NaN for an empty array."""
    if values.size == 0:
        return float("nan")
    return float(np.count_nonzero(values > threshold) / values.size)


def frac_curve(values: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    """Fraction-on of *values* at each scanned threshold."""
    return np.array(
        [fraction_on_for_threshold(values, float(t)) for t in thresholds],
        dtype=float,
    )


def relative_gap(fa: np.ndarray, fb: np.ndarray, *,
                 n_a: int | None = None, n_b: int | None = None) -> np.ndarray:
    """Per-threshold ``|fa-fb| / max(|fa|, |fb|)``, with 0.0 where undefined.

    Relative rather than absolute because for skewed distributions the ratio is
    what a fraction-on cut discriminates: 5% on vs 0.5% on is a 10x difference
    but only 0.045 absolute.

    The denominator is a **magnitude**, not a signed ``max(fa, fb)``: under a
    subtractive fold-change the fractions can go negative, and a signed
    denominator would zero the gap across that region and spike at the sign
    crossing, inverting the chosen threshold.

    **Statistical support.** Given the population sizes *n_a* and *n_b*, both
    fractions are Laplace-smoothed — ``(k + 1) / (n + 2)`` — before the ratio is
    taken. Without that, the ratio is exactly 1.0 at *every* threshold where one
    arm is empty and the other holds even a single cell, so the objective could
    not tell "97 % of positives on against 0 % of negatives" from "1 % on
    against 0 %". Smoothing costs nothing where the counts are real (0.998
    rather than 1.000 for a clean separation) and collapses the score where they
    are not (0.5 for one cell against none, at n=500).

    **What that does and does not fix.** It shortens the tied run: measured on
    two well-separated control populations of 400 cells each, the grid points
    scoring the maximum fall from 485 of 512 to 354, and on overlapping
    populations from 80 to 3. Every point it removes is at the *top* of the run,
    where the positive arm has thinned to a handful of cells — the region the
    unsmoothed ratio could not distinguish from a clean separation.

    It does not move the reported threshold. ``argmax`` returns the *first* tied
    index, which is the bottom of the run, and the bottom is set by where the
    negative control's fraction-on reaches zero — a place smoothing does not
    touch. On that same pair, moving one negative-control cell out to 1500 moves
    the reported gate by 1252 intensity units, smoothed or not; only the tie
    count changes (174 to 43). The instability is real and is still there. What
    ships against it is visibility, not a cure: ``tie_count``, ``tie_span`` and
    ``tie_contiguous`` are recorded and logged, so a gate picked off a 354-point
    run is no longer printed like one picked off a peak. Which point of a
    plateau *should* be reported is a domain question about the assay, not one
    this module can decide.

    Counts are optional because the fold-change layer in
    ``review.features.home.cell_gating.gating_compute`` scans *adjusted*
    fractions, which are no longer ``k / n`` — they can exceed 1 and go
    negative — so there is no ``k`` to add one to. Pass counts only for genuine
    fraction-on curves.
    """
    fa = np.asarray(fa, dtype=float)
    fb = np.asarray(fb, dtype=float)
    if n_a is not None and n_b is not None and int(n_a) > 0 and int(n_b) > 0:
        fa = (fa * int(n_a) + 1.0) / (int(n_a) + 2.0)
        fb = (fb * int(n_b) + 1.0) / (int(n_b) + 2.0)
    abs_gap = np.abs(fa - fb)
    larger = np.maximum(np.abs(fa), np.abs(fb))
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = np.divide(abs_gap, larger, out=np.zeros_like(abs_gap), where=larger > 0)
    return np.where(np.isfinite(diffs), diffs, 0.0)


def sweep_thresholds(min_threshold: float, max_threshold: float,
                     steps: int) -> np.ndarray:
    """The candidate-threshold grid, or ``ValueError`` describing why not.

    Shared so both callers reject the same inputs; the GUI shows the message.
    """
    lo = float(min_threshold)
    hi = float(max_threshold)
    if not np.isfinite(lo) or not np.isfinite(hi):
        raise ValueError("threshold range must be finite.")
    if hi < lo:
        raise ValueError("max threshold must be greater than or equal to min threshold.")
    if int(steps) < MIN_SWEEP_STEPS:
        raise ValueError(f"steps must be at least {MIN_SWEEP_STEPS}.")
    if int(steps) > MAX_SWEEP_STEPS:
        raise ValueError(f"steps must be {MAX_SWEEP_STEPS:,} or fewer.")
    return np.linspace(lo, hi, int(steps))


def best_from_curves(
    thresholds: np.ndarray,
    fa: np.ndarray,
    fb: np.ndarray,
    diffs: np.ndarray,
    *,
    n_a: int,
    n_b: int,
    support_floor: int = 0,
) -> ThreshFracOptimizationResult:
    """Package the argmax of *diffs* as a result, plateau included.

    ``np.argmax`` takes the FIRST index at the maximum, so a run of tied
    thresholds (common — fraction-on is a step function) reports the lowest.
    That is a defensible choice and an indefensible thing to leave unsaid, so
    the result also carries how many grid points scored that same maximum and
    how wide a threshold range they cover. A caller that prints one number can
    then say whether it was picked from a plateau or from a peak.
    """
    diffs = np.asarray(diffs, dtype=float)
    thresholds = np.asarray(thresholds, dtype=float)
    best_idx = int(np.argmax(diffs))
    best = float(diffs[best_idx])
    tied_idx = np.flatnonzero(diffs == best)
    tied = thresholds[tied_idx]
    # The tied points are a plateau only when they are adjacent on the grid.
    # Read off the indices rather than the threshold values: the sweep is
    # uniformly spaced, so an index run is exactly the question being asked and
    # it does not have to compare floats for equality a second time.
    contiguous = bool(
        tied_idx.size <= 1
        or tied_idx.size == int(tied_idx[-1] - tied_idx[0]) + 1
    )
    return ThreshFracOptimizationResult(
        threshold=float(thresholds[best_idx]),
        frac_a=float(fa[best_idx]),
        frac_b=float(fb[best_idx]),
        difference=best,
        n_a=int(n_a),
        n_b=int(n_b),
        tie_count=int(tied.size),
        tie_span=float(tied.max() - tied.min()) if tied.size else 0.0,
        tie_contiguous=contiguous,
        objective=OBJECTIVE_RELATIVE_GAP,
        support_floor=int(support_floor),
    )


def sweep_bounds(*populations: np.ndarray) -> tuple[float, float]:
    """``(lo, hi)`` sweep bounds from the pooled *populations*.

    ``lo`` is the pooled minimum, not 0.0 — channels sit on a background offset,
    and starting at zero wastes half the grid where both fractions are 1.0.
    Returns a degenerate ``(lo, lo)`` when there is no spread to scan.
    """
    finite = [np.asarray(p, dtype=float).ravel() for p in populations if p is not None]
    pooled = np.concatenate(finite) if finite else np.array([], dtype=float)
    pooled = pooled[np.isfinite(pooled)]
    if pooled.size == 0:
        return 0.0, 0.0
    lo = float(pooled.min())
    hi = float(np.percentile(pooled, SWEEP_HI_PERCENTILE))
    if not np.isfinite(hi) or hi <= lo:
        hi = float(pooled.max())
    return lo, hi


# ── The supported-separation objective ───────────────────────────────────────
#
# What a threshold is actually being asked for here is a classifier: cells above
# it are called ON, and the two control wells are the labelled examples. The
# quality of that classifier at a candidate cut is its true-positive rate in the
# positive control against its false-positive rate in the negative one, and the
# standard summary of the pair is Youden's ``J = TPR - FPR`` — the height of the
# ROC curve above the diagonal, and equivalently the Kolmogorov-Smirnov distance
# between the two intensity distributions.
#
# ``J`` on its own is a point estimate from two binomial samples, and the whole
# defect being fixed here is a score that ignores how many cells it was measured
# on. So the score is not ``J`` but a **lower confidence bound** on it. Where the
# counts are large the bound sits just under the estimate and the two agree;
# where a cut has thinned one arm to a handful of cells the interval swallows the
# estimate and the score collapses. No separate penalty term, no tuning constant:
# the uncertainty in the measurement is the penalty.


def wilson_bounds(k, n: int, z: float = DEFAULT_CONFIDENCE_Z):
    """``(lo, hi)`` Wilson score interval for ``k / n``. Vectorised over *k*.

    Wilson rather than the textbook ``p ± z·sqrt(p(1-p)/n)`` because this
    objective lives or dies at ``k = 0`` and ``k = n``, where the Wald standard
    error is exactly zero and would report a threshold that let no negative-
    control cell through as having *no* uncertainty at all — which is the
    arithmetic behind the bug this module is being rewritten for. The Wilson
    interval is derived by inverting the score test instead, so it keeps a
    sensible width at both ends: ``0 / 100000`` carries an upper bound of
    ~3.8e-5 rather than 0.

    *n* of zero returns ``(0, 1)`` — no information, the widest honest answer.
    """
    k = np.asarray(k, dtype=float)
    n = float(n)
    if n <= 0:
        return np.zeros_like(k), np.ones_like(k)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = (z / denom) * np.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return np.clip(centre - half, 0.0, 1.0), np.clip(centre + half, 0.0, 1.0)


def supported_separation(
    k_pos, n_pos: int, k_neg, n_neg: int, *,
    weight: float = DEFAULT_SPECIFICITY_WEIGHT,
    z: float = DEFAULT_CONFIDENCE_Z,
):
    """Lower confidence bound on ``TPR - weight * FPR``, per candidate threshold.

    ``k_pos`` / ``k_neg`` are the cells above the candidate in the positive and
    negative control; ``n_pos`` / ``n_neg`` the two well totals. Vectorised over
    the candidate axis; returns one score per candidate.

    The bound is Newcombe's hybrid-score method for the difference of two
    independent proportions: take each arm's Wilson interval and combine the
    distances that move the difference downward,

        lcb = (p1 - w·p2) - sqrt( (p1 - l1)² + (w·(u2 - p2))² )

    where ``(l1, ·)`` bounds the positive arm and ``(·, u2)`` the negative one.
    Newcombe rather than a pooled normal approximation for the same reason
    :func:`wilson_bounds` is used at all: the method is built to stay honest when
    a proportion sits against 0 or 1, which is exactly where a fraction-on cut
    spends its time.

    *weight* scales the false-positive arm, in the estimate and in its
    uncertainty alike — a cost this large on the error rate is a cost that large
    on being wrong about the error rate. ``1.0`` is plain Youden.

    **What this buys over the point estimate.** On two 100,000-cell controls with
    overlapping tails, the score at the correct cut (~60 % of the positive
    control ON) is 0.54 and at the far-tail cut that the relative-gap objective
    chose (9.9 % ON) it is a small positive number that loses by an order of
    magnitude. On a threshold holding ten positive cells against zero negative
    ones the bound is driven to roughly zero by the negative arm's Wilson upper
    bound alone.

    **And the refusal it makes possible.** The bound is signed, so
    ``max(score) <= 0`` is a statement and not a heuristic: no candidate
    threshold separates these two wells at this confidence level. Measured
    -0.0011 for two samples drawn from one distribution, against +0.54 to +0.67
    for every separable pair tested. :func:`optimize_between_populations` raises
    on it rather than reporting the argmax of noise.
    """
    n_pos = int(n_pos)
    n_neg = int(n_neg)
    w = float(weight)
    p1 = np.asarray(k_pos, dtype=float) / max(n_pos, 1)
    p2 = np.asarray(k_neg, dtype=float) / max(n_neg, 1)
    lo1, _hi1 = wilson_bounds(k_pos, n_pos, z)
    _lo2, hi2 = wilson_bounds(k_neg, n_neg, z)
    estimate = p1 - w * p2
    width = np.sqrt((p1 - lo1) ** 2 + (w * (hi2 - p2)) ** 2)
    return estimate - width


def counts_above(values_sorted: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    """How many of *values_sorted* are strictly above each of *thresholds*.

    *values_sorted* must already be sorted ascending; the caller sorts once and
    reuses it. ``searchsorted`` makes the whole sweep ``O((N + T) log N)`` rather
    than the ``O(N·T)`` of counting the array once per threshold — which matters
    because :func:`candidate_thresholds` produces one candidate per distinct
    value rather than a fixed 512.
    """
    return values_sorted.size - np.searchsorted(
        values_sorted, np.asarray(thresholds, dtype=float), side="right")


def candidate_thresholds(
    *populations: np.ndarray,
    lo: float | None = None,
    hi: float | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> np.ndarray:
    """The exact candidate grid: midpoints between consecutive pooled values.

    Fraction-on is a step function of the threshold, so it takes exactly one
    value between any two adjacent observations and every threshold in that
    interval classifies every cell identically. Scanning the midpoints therefore
    evaluates *every distinguishable cut* and nothing else — there is no
    resolution to choose, no step size to get wrong, and no grid point that lands
    where the curve is mid-step.

    The midpoint rather than the observation itself so the reported threshold
    does not sit on a cell's own value, where a float comparison decides whether
    that cell is in or out.

    *lo* / *hi* clip the range when the caller has bounds to impose (the Cell
    Gating window's Min/Max fields). *max_candidates* caps the result, subsampled
    evenly, for populations large enough that one candidate per value is more
    resolution than anyone can act on.

    Returns an empty array when the pooled values carry no spread — the caller
    treats that as "nothing to scan" rather than scanning one point.
    """
    finite = [np.asarray(p, dtype=float).ravel() for p in populations if p is not None]
    pooled = np.concatenate(finite) if finite else np.array([], dtype=float)
    pooled = np.unique(pooled[np.isfinite(pooled)])
    if pooled.size < 2:
        return np.array([], dtype=float)
    grid = (pooled[:-1] + pooled[1:]) / 2.0
    if lo is not None:
        grid = grid[grid >= float(lo)]
    if hi is not None:
        grid = grid[grid <= float(hi)]
    cap = max(int(max_candidates), MIN_SWEEP_STEPS)
    if grid.size > cap:
        grid = grid[np.linspace(0, grid.size - 1, cap).round().astype(np.int64)]
    return grid


def support_floor(n_a: int, n_b: int,
                  requested: int = DEFAULT_MIN_SUPPORT) -> int:
    """The smallest ``max(k_a, k_b)`` a candidate threshold must clear.

    *requested* cells, except on populations too small to spare them: a floor of
    10 against two wells of 12 cells would reject every cut worth making, so it
    drops to a tenth of the larger well. Returns 0 when even that is meaningless.
    """
    biggest = max(int(n_a), int(n_b))
    if biggest <= 0:
        return 0
    return max(1, min(int(requested), biggest // 10))


def floor_unsupported(
    scores: np.ndarray,
    values_a: np.ndarray,
    values_b: np.ndarray,
    thresholds: np.ndarray,
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
) -> tuple[np.ndarray, int]:
    """Blank out candidates that neither population supports; ``(scores, floor)``.

    For the fold-change scan, whose adjusted fractions have been through
    ``scale_bar_value`` and are no longer ``k / n`` — they exceed 1 and go
    negative — so :func:`supported_separation` cannot be computed on them and
    there is no confidence bound to collapse where the counts run out. The
    relative-gap objective is unbounded in that region: it reaches its maximum
    wherever one arm empties first, on however few cells, which at plate-scale
    ``n`` is a threshold holding a handful of cells out of a hundred thousand.

    A count floor is the coarse version of the same idea and the only version
    available here: a candidate is scanned only if at least one of the two wells
    still holds :func:`support_floor` cells above it. Masked candidates are set
    to ``-inf`` rather than dropped, so every array stays on the grid's own index
    and the curve the popup plots is still the curve that was scored.

    Returns the floor actually applied, which is ``0`` when the floor would have
    excluded every candidate — a two-well pair small enough for that is one where
    the caller is better off with an unsupported answer it can see than with no
    answer at all, and the returned ``0`` is how it can see it.
    """
    scores = np.asarray(scores, dtype=float)
    floor = support_floor(np.size(values_a), np.size(values_b), min_support)
    if floor <= 0:
        return scores, 0
    ka = counts_above(np.sort(np.asarray(values_a, dtype=float).ravel()), thresholds)
    kb = counts_above(np.sort(np.asarray(values_b, dtype=float).ravel()), thresholds)
    supported = np.maximum(ka, kb) >= floor
    if not supported.any():
        return scores, 0
    return np.where(supported, scores, -np.inf), floor


def plateau_pick(thresholds: np.ndarray, scores: np.ndarray
                 ) -> tuple[int, float, int, float, bool]:
    """``(idx, threshold, tie_count, tie_span, contiguous)`` for the best score.

    ``np.argmax`` returns the first index at the maximum, so on a plateau — and a
    clean assay has a real one, the empty valley between the two populations,
    where every cut classifies identically — it reports the bottom edge. The
    bottom edge is set by the brightest cell in the negative control, which is
    the single least stable number in the data.

    So the reported threshold is the **midpoint of the contiguous run containing
    that argmax**, which is the same answer for a plateau of one and the middle
    of the valley otherwise. Ties elsewhere on the grid are counted and spanned
    for the caller to report, but they do not move the point: a bimodal objective
    can score its maximum at two separated places, and the midpoint *between*
    those is a threshold that scored lower than both.
    """
    scores = np.asarray(scores, dtype=float)
    thresholds = np.asarray(thresholds, dtype=float)
    best = float(scores.max())
    tied = np.flatnonzero(scores == best)
    first = int(tied[0])
    contiguous = bool(tied.size <= 1
                      or tied.size == int(tied[-1] - tied[0]) + 1)
    span = float(thresholds[tied].max() - thresholds[tied].min()) if tied.size else 0.0
    # Walk out from the argmax while the score holds, so a non-contiguous tie set
    # contributes only the run the argmax is actually standing on.
    end = first
    while end + 1 < scores.size and scores[end + 1] == best:
        end += 1
    idx = (first + end) // 2
    return idx, float(thresholds[idx]), int(tied.size), span, contiguous


def _normal_cdf(z: float) -> float:
    """Standard normal CDF, for turning *z* back into the confidence it states."""
    return 0.5 * (1.0 + math.erf(float(z) / math.sqrt(2.0)))


def optimize_between_populations(
    values_a: np.ndarray,
    values_b: np.ndarray,
    *,
    min_threshold: float | None = None,
    max_threshold: float | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    weight: float = DEFAULT_SPECIFICITY_WEIGHT,
    min_support: int = DEFAULT_MIN_SUPPORT,
    z: float = DEFAULT_CONFIDENCE_Z,
) -> ThreshFracOptimizationResult:
    """Threshold best separating *values_a* (positive) from *values_b* (negative).

    Scores every distinguishable cut (:func:`candidate_thresholds`) by the lower
    confidence bound on its separation (:func:`supported_separation`) and returns
    the midpoint of the best plateau (:func:`plateau_pick`).

    *values_a* is the arm the score is written to favour: the objective is
    ``TPR(a) - weight * FPR(b)``, so passing the wells the other way round is a
    different question and will usually return a different answer. The symmetric
    objective this replaced could not tell the two apart at all, which is why its
    caller had to guess at a swap from the reported fractions afterwards.

    Raises ``ValueError``, so callers skip the channel rather than record a
    guess, when either population is empty, when the pooled values carry no
    spread to scan, or when **no candidate separates the two wells at this
    confidence level** — the last being a real answer about the data (an assay
    that did not work, two wells that are not the controls they were named as)
    and not a failure of the search.
    """
    values_a = np.asarray(values_a, dtype=float).ravel()
    values_b = np.asarray(values_b, dtype=float).ravel()
    values_a = values_a[np.isfinite(values_a)]
    values_b = values_b[np.isfinite(values_b)]
    if values_a.size == 0:
        raise ValueError("Population A has no valid finite values.")
    if values_b.size == 0:
        raise ValueError("Population B has no valid finite values.")

    grid = candidate_thresholds(
        values_a, values_b,
        lo=min_threshold, hi=max_threshold, max_candidates=max_candidates,
    )
    if grid.size == 0:
        raise ValueError(
            "threshold range is degenerate — the populations carry no spread "
            "to scan across."
        )

    sorted_a = np.sort(values_a)
    sorted_b = np.sort(values_b)
    k_a = counts_above(sorted_a, grid)
    k_b = counts_above(sorted_b, grid)
    scores = supported_separation(
        k_a, values_a.size, k_b, values_b.size, weight=weight, z=z)

    # The floor is the coarse guard the confidence bound makes largely redundant;
    # it is applied anyway, through the same helper the fold-change scan uses, so
    # the two paths cannot end up with two definitions of "enough cells to judge
    # this cut on".
    scores, floor = floor_unsupported(
        scores, values_a, values_b, grid, min_support=min_support)

    if not np.isfinite(scores).any() or float(np.nanmax(scores)) <= 0.0:
        raise ValueError(
            "no threshold separates these two populations at the "
            f"{100 * (1 - 2 * (1 - _normal_cdf(z))):.0f}% confidence level — "
            "every cut's advantage is within the noise of the cell counts "
            "behind it. Check that the two wells are the controls you meant, "
            "and that the assay worked."
        )

    idx, threshold, tie_count, tie_span, contiguous = plateau_pick(grid, scores)
    n_a = int(values_a.size)
    n_b = int(values_b.size)
    # Recount at the reported midpoint rather than reading the plateau's first
    # index: on a plateau the counts are identical by construction, and where
    # they are not the reported number must describe the threshold reported.
    ka = int(counts_above(sorted_a, np.array([threshold]))[0])
    kb = int(counts_above(sorted_b, np.array([threshold]))[0])
    tpr = ka / n_a
    fpr = kb / n_b
    return ThreshFracOptimizationResult(
        threshold=threshold,
        frac_a=tpr,
        frac_b=fpr,
        difference=float(scores[idx]),
        n_a=n_a,
        n_b=n_b,
        tie_count=tie_count,
        tie_span=tie_span,
        tie_contiguous=contiguous,
        objective=OBJECTIVE_SUPPORTED,
        k_a=ka,
        k_b=kb,
        youden=float(tpr - float(weight) * fpr),
        weight=float(weight),
        support_floor=int(floor),
    )


# ── Pipeline-side layer: per-well CSVs → per-channel thresholds ──────────────
#
# Reads the per-well CSVs rather than re-deriving values from the ``*_out.zip``
# mask/tophat pairs. Two reasons: those CSV values are what the Cell Gating tab
# plots, so the threshold lands where the user will see it; and
# ``sample_cell_and_bg`` pools cell means with *background* pixels, which is
# right for an Otsu split and wrong here — background lands identically in both
# control wells and dilutes the gap being maximised.

def _row_passes(row: dict) -> bool:
    """Honour ``QC_Pass`` / ``Crop_Pass`` when the CSV carries them.

    The pipeline does not write these (they are Review-side layers), so this is
    all-pass in practice. It keeps a hand-curated or sidecar-merged CSV read on
    the same population as ``gating_compute._cell_gating_valid_mask``.
    """
    for col in ("QC_Pass", "Crop_Pass"):
        raw = row.get(col)
        if raw is None or str(raw).strip() == "":
            continue
        if str(raw).strip().lower() in ("0", "false", "no"):
            return False
    return True


def read_channel_values(csv_path, channel: str) -> np.ndarray:
    """Finite per-cell ``<channel>_mean_intensity`` values from one per-well CSV.

    Returns an empty array for a missing file, a missing column, or a column with
    nothing parseable — every one of which the caller treats the same way, by
    skipping the channel rather than recording a guess.
    """
    import csv

    # The pipeline writes the column as ``tok.lower()`` but the token arrives in
    # whatever case was typed on the command line, so match case-insensitively.
    want = f"{str(channel).strip().lower()}_mean_intensity"
    out: list[float] = []
    try:
        with open(csv_path, newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return np.array([], dtype=float)
            col = next((f for f in reader.fieldnames
                        if str(f).strip().lower() == want), None)
            if col is None:
                return np.array([], dtype=float)
            for row in reader:
                if not _row_passes(row):
                    continue
                try:
                    val = float(row.get(col, ""))
                except (TypeError, ValueError):
                    continue
                if np.isfinite(val):
                    out.append(val)
    except OSError:
        return np.array([], dtype=float)
    return np.asarray(out, dtype=float)


def find_well_csv(output_dir, csv_prefix: str, well: str) -> Path | None:
    """The per-well CSV for *well*, or ``None``.

    Tries the exact spelling first, then falls back to canonicalising both sides
    so a user who typed ``b3`` still matches ``<prefix>_B03.csv``.
    """
    from pathlib import Path

    from util.well_token import canonical_well_label

    output_dir = Path(output_dir)
    exact = output_dir / f"{csv_prefix}_{well}.csv"
    if exact.is_file():
        return exact
    want = canonical_well_label(well)
    if not want:
        return None
    for path in sorted(output_dir.glob(f"{csv_prefix}_*.csv")):
        stem_well = path.stem[len(csv_prefix) + 1:]
        if canonical_well_label(stem_well) == want:
            return path
    return None


def estimate_thresholds_from_controls(
    output_dir,
    *,
    fluor_channels,
    csv_prefix: str,
    pos_well: str,
    neg_well: str,
    log,
    weight: float = DEFAULT_SPECIFICITY_WEIGHT,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, float]:
    """Per-channel thresholds separating the positive from the negative control.

    A channel missing from either well, with no spread to scan, or with no cut
    that separates the two wells at 95 % confidence is skipped with a warning
    rather than falling back to Otsu — a sidecar mixing two estimators is
    unexplainable. Stored through :func:`round_stored_threshold`, matching the
    Otsu pass.
    """
    pos_csv = find_well_csv(output_dir, csv_prefix, pos_well)
    neg_csv = find_well_csv(output_dir, csv_prefix, neg_well)
    if pos_csv is None or neg_csv is None:
        missing = ", ".join(
            w for w, p in ((pos_well, pos_csv), (neg_well, neg_csv)) if p is None
        )
        log.warning(
            "Auto-threshold: no per-well CSV for control well(s) %s — "
            "skipping the control-based pass.", missing,
        )
        return {}

    out: dict[str, float] = {}
    for channel in fluor_channels:
        ch = str(channel).strip().lower()
        if not ch:
            continue
        values_pos = read_channel_values(pos_csv, channel)
        values_neg = read_channel_values(neg_csv, channel)
        if values_pos.size == 0 or values_neg.size == 0:
            log.warning(
                "Auto-threshold: skipping %s — no usable values in %s (%d) / %s (%d).",
                ch, pos_well, values_pos.size, neg_well, values_neg.size,
            )
            continue
        try:
            result = optimize_between_populations(
                values_pos, values_neg,
                weight=weight, max_candidates=max_candidates,
            )
        except ValueError as exc:
            log.warning("Auto-threshold: skipping %s — %s", ch, exc)
            continue
        if not np.isfinite(result.threshold):
            # Not a number the sidecar can carry, and storing it as 0.0 would be
            # a gate that passes everything, written as the value that also
            # means "not computed".
            log.warning(
                "Auto-threshold: skipping %s — the optimiser returned a "
                "non-finite threshold (%r).", ch, result.threshold,
            )
            continue
        out[ch] = round_stored_threshold(result.threshold)
        # Both counts and both totals, not just the fractions: the whole point of
        # this estimator is that a cut is only as good as the cells behind it, so
        # the line that reports the cut reports them too.
        log.info(
            "Auto-threshold %s: %.4g  (%s %d/%d ON = %.1f%%, %s %d/%d ON = %.2f%%; "
            "separation \u2265 %.3f at 95%% confidence, Youden J = %.3f)",
            ch, out[ch],
            pos_well, result.k_a, result.n_a, 100.0 * result.frac_a,
            neg_well, result.k_b, result.n_b, 100.0 * result.frac_b,
            result.difference, result.youden,
        )
        # A plateau is the honest shape of the answer on a clean assay — the
        # empty valley between the two populations, every cut inside it
        # classifying identically. The reported threshold is its midpoint, and
        # saying so is the difference between a range and a false precision.
        if result.tie_count > 1:
            shape = (
                "a contiguous run spanning %.4g intensity unit(s)"
                if result.tie_contiguous else
                "%.4g intensity unit(s) apart but NOT adjacent on the grid, so "
                "cuts between them scored lower"
            )
            log.info(
                "Auto-threshold %s: %d candidate cut(s) scored that same "
                "maximum, " + shape + "; the midpoint of the run around the "
                "best is what was stored.",
                ch, result.tie_count, result.tie_span,
            )
        if result.k_b == 0:
            log.info(
                "Auto-threshold %s: the negative control has no cell above the "
                "stored cut, so its fraction-on floor is set by well size "
                "(< %.3f%%) rather than measured.",
                ch, 100.0 / result.n_b,
            )
    return out
