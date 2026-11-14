"""Rare-responder analysis, Test C (complementary): a one-sided, upper-tail-weighted Anderson–Darling statistic.

Within each loading stratum, with ``F0`` the pooled control ECDF and ``Fn`` the ECDF of the ``n`` nanowells of the
well under test,

    A²_R = n ∫_{x > median(F0)} [max(F0(x) − Fn(x), 0)]² / (1 − F0(x)) dF0(x)

(:func:`a2r_stratum`). ``F0(x) − Fn(x) > 0`` means the well has *less* mass at or below ``x`` than the controls —
more above it — so only an excess of mass in the upper tail counts; a deficit counts nothing, and nothing below the
control median counts at all. The integral is the sum over the control pool's distinct values ``u`` at or above its
median of ``c(u)/N · [max(F0(u) − Fn(u), 0)]² / (1 − F0(u))``, with ``F0(u) = #{control ≤ u} / (N + 1)`` — the
``(1 + #) / (N + 1)`` convention of every empirical p here — so ``1 − F0`` never reaches 0 and the pool's **top value
carries weight**: mass beyond everything the controls reach is exactly a rare responder, and with ``F0 = #/N`` it
counted nothing. With whole-number fold changes (2×, 3×, 4×) that had left no value to sum over at all.

**Strata combine** by a weighted sum, each stratum's ``A²_R`` weighted by its share of the well's nanowells
(:func:`a2r`); the per-stratum values are reported beside it.

**Calibration** is the same as Higher Criticism's: the control wells' leave-one-out values are the null of a single
well, and a whole-well permutation of labels is the group test (``rare_null_compute``).

:func:`scholz_stephens` is the standard two-sided k-sample Anderson–Darling test of the pooled treatment nanowells
against the pooled control nanowells. It treats nanowells as independent, its p-value is uncalibrated, and it is
reported as description only, under :data:`SCHOLZ_LABEL`.

Qt-free and matplotlib-free.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
from scipy import stats

from review.features.nanowell.rare_null_compute import Well

SCHOLZ_LABEL = "k-sample Anderson–Darling (Scholz–Stephens), two-sided — uncalibrated; i.i.d. assumption"


def tail_start(ref) -> float:
    """Where the upper tail ``A²_R`` sums over begins: the control pool's median. One definition for the statistic
    (:func:`a2r_stratum`) and for the figure that shades it (:func:`drawn_excess`)."""
    return float(np.median(np.asarray(ref, dtype=float)))


def drawn_excess(control, treated, sign: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """What the Rare responders figure shades on its cumulative panel as the shape test's region: ``(x, F0, Fn,
    excess)`` over every distinct value of *control* and *treated* (the values as measured), with ``F0`` / ``Fn`` the
    plain cumulative shares the panel draws and *excess* true where :func:`a2r_stratum` counts — on the analysed
    tail's side of the control median, where the treated nanowells have more mass beyond ``x`` than the controls.

    *sign* is the analysis's (``Prepared.sign``): +1 looks above the median for treated mass the controls lack
    (``F0 > Fn``), −1 — *Responders lower*, analysed as negated values — below it (``Fn > F0``). The statistic itself
    scores each treatment well against the pooled controls, with a weight that grows towards the top; the shading is of
    the treated wells pooled, as the panel draws them, so it shows where the test looks and in which direction, not
    its value."""
    c = np.sort(np.asarray(control, dtype=float))
    t = np.sort(np.asarray(treated, dtype=float))
    if not len(c) or not len(t):
        empty = np.array([])
        return empty, empty, empty, empty.astype(bool)
    x = np.unique(np.concatenate([c, t]))
    f0 = np.searchsorted(c, x, side="right") / len(c)
    fn = np.searchsorted(t, x, side="right") / len(t)
    start = sign * tail_start(sign * c)                  # the analysed values' median, as measured
    side = x >= start if sign > 0 else x <= start
    excess = side & ((f0 - fn) * sign > 0)
    return x, f0, fn, excess


def a2r_stratum(x: np.ndarray, ref: np.ndarray) -> float:
    """``A²_R`` of the values *x* against the sorted control values *ref*; NaN when either is empty, and NaN when the
    pool holds a single value (it has no upper tail to compare with — not a measured zero)."""
    x = np.sort(np.asarray(x, dtype=float))
    ref = np.asarray(ref, dtype=float)
    n, N = len(x), len(ref)
    if n == 0 or N == 0:
        return math.nan
    u, c = np.unique(ref, return_counts=True)
    if len(u) < 2:
        return math.nan
    F0 = np.cumsum(c) / (N + 1)                          # < 1 everywhere: the top value carries weight
    Fn = np.searchsorted(x, u, side="right") / n
    keep = u >= tail_start(ref)
    if not keep.any():
        return math.nan
    excess = np.maximum(F0[keep] - Fn[keep], 0.0)
    return float(n * np.sum((c[keep] / N) * excess ** 2 / (1.0 - F0[keep])))


def a2r(well: Well, pooled: dict) -> tuple[float, dict]:
    """The well's strata-weighted ``A²_R`` and each stratum's own value. A stratum whose value is undefined
    (:func:`a2r_stratum`) is reported and left out of the weighting; NaN when no stratum has a value."""
    per, weights = {}, {}
    for s, x in well.values.items():
        ref = pooled.get(s)
        if ref is None or len(ref) == 0:
            continue
        per[s] = a2r_stratum(x, ref)
        if np.isfinite(per[s]):
            weights[s] = len(x)
    total = sum(weights.values())
    if not total:
        return math.nan, per
    return float(sum(weights[s] / total * per[s] for s in weights)), per


def statistic(well: Well, pooled: dict) -> float:
    return a2r(well, pooled)[0]


def scholz_stephens(treated: np.ndarray, control: np.ndarray) -> tuple[float, float, str]:
    """``(statistic, p, note)`` of scipy's k-sample AD test; p is capped by scipy to [0.001, 0.25]."""
    treated, control = np.asarray(treated, float), np.asarray(control, float)
    if len(treated) < 2 or len(control) < 2 or np.unique(np.concatenate([treated, control])).size < 2:
        return math.nan, math.nan, "too few distinct values"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = stats.anderson_ksamp([treated, control])
    note = "p capped by scipy to [0.001, 0.25]" if caught else ""
    return float(res.statistic), float(res.significance_level), note
