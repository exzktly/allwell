"""Rare-responder analysis, Test B: Higher Criticism, threshold-free and sensitive to a sparse excess.

**Per-nanowell p-values.** Each nanowell of the well under test gets a one-sided p-value from the control pool of
its own loading stratum (the whole pool when no strata are set, as on the page), ``p_i = (1 + #{control >= x_i}) / (N_control + 1)`` (:func:`p_values`). A treatment well is
scored against every control well; a control well, for its null, against every *other* control well. A nanowell in
a stratum the pool has no value in gets no p-value, and is counted in :attr:`HC.unscored`.

**The statistic** (:func:`hc_plus`), with ``p_(1) <= … <= p_(n)`` the well's sorted p-values,

    HC+ = max over i with 1/n < p_(i) and i <= alpha0·n of  √n · (i/n − p_(i)) / √(p_(i)·(1 − p_(i)))

(``alpha0 = 0.1`` by default). The ``1/n < p_(i)`` side is what makes it HC+: the smallest p-values, where
``p(1 − p)`` is tiny and a single nanowell dominates, are left out. A p-value of 1 has no variance and is skipped.

**Calibration.** No asymptotic null: a treatment well's HC is placed among the control wells' leave-one-out HCs
(``rare_null_compute.empirical_p``), and a treatment's group test permutes whole wells with the mean HC of the
treatment wells as the statistic (``rare_null_compute.permutation_test``).

**Candidate responders.** The ``i*`` that maximises HC, the p-value ``p_(i*)`` it corresponds to, and the nanowells
at or below it (:attr:`HC.candidates`).

**Resolution.** The smallest p-value a pool of ``N`` gives is ``1 / (N + 1)``; when that is not well below the
``1/n`` scale HC works at — a pool from a single control well (which is also every control well's leave-one-out
pool when there are only two), or ``N + 1 < 3·n`` — :func:`resolution_warning` says so.

Qt-free and matplotlib-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from review.features.nanowell.rare_null_compute import Well, pool_size

#: ``N_control + 1`` should be at least this many times a well's nanowell count for p-values to resolve 1/n.
RESOLUTION_FACTOR = 3


def p_values(well: Well, pooled: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """``(p, nanowell ids, strata, unscored)`` of *well* against the per-stratum *pooled* controls."""
    ps, ids, strata = [], [], []
    unscored = 0
    for s, x in well.values.items():
        ref = pooled.get(s)
        if ref is None or len(ref) == 0:
            unscored += len(x)
            continue
        ge = len(ref) - np.searchsorted(ref, x, side="left")
        ps.append((1.0 + ge) / (len(ref) + 1.0))
        ids.append(well.ids[s])
        strata.append(np.full(len(x), s, dtype=object))
    if not ps:
        return np.array([]), np.array([]), np.array([], dtype=object), unscored
    return np.concatenate(ps), np.concatenate(ids), np.concatenate(strata), unscored


@dataclass
class HC:
    value: float                       # NaN when no index qualifies
    i_star: int                        # 1-based; 0 when undefined
    p_star: float
    n: int
    curve_x: np.ndarray                # i / n at each qualifying index
    curve_y: np.ndarray                # HC_i there
    candidates: list = field(default_factory=list)     # (nanowell id, stratum, p) at or below p_star
    unscored: int = 0
    reason: str = ""


#: Why a well has no HC score when its most extreme nanowells are all at p ≤ 1/n — past all but a few control values
#: (``p = (1 + #{control >= x}) / (N + 1) <= 1/n`` lets up to ``(N + 1)/n − 1`` control values reach them), the
#: strongest responders there are. HC+ leaves those out by definition, so the figure marks the well with this rather
#: than leaving it undrawn.
BEYOND_CONTROLS = ("no HC score: its most extreme nanowells are past all but a few control values (p ≤ 1/n), "
                   "which HC leaves out")


def min_nanowells(alpha0: float) -> int:
    """The fewest nanowells a well needs for HC+ to have any index to maximise over: the smallest *n* with
    ``alpha0·n >= 1``, the condition :func:`hc_plus` applies to ``i = 1`` (10 at the default ``alpha0 = 0.1``)."""
    n = max(1, math.ceil(1.0 / alpha0))
    while alpha0 * n < 1.0:                              # 1/alpha0 rounded down by floating point
        n += 1
    return n


def unscored_reason(ps: np.ndarray, alpha0: float) -> str:
    """Why no index of the sorted p-values *ps* qualifies for HC+. First the well's size: with ``alpha0·n < 1`` no
    index qualifies whatever the values, so a well of fewer than :func:`min_nanowells` says that, and is never called
    beyond the controls for being small. Then :data:`BEYOND_CONTROLS` when the top ``alpha0·n`` are all at or below
    1/n, else that none of them is below 1 (nothing extreme to score)."""
    n = len(ps)
    if alpha0 * n < 1.0:
        return f"no HC score: a well needs at least {min_nanowells(alpha0)} nanowells for HC, and it has {n}"
    top = ps[: int(alpha0 * n)]
    if (top <= 1.0 / n).all():
        return BEYOND_CONTROLS
    return f"no HC score: none of its {int(alpha0 * n)} most extreme nanowells has 1/n < p < 1"


def hc_plus(p: np.ndarray, alpha0: float) -> tuple[float, int, float, np.ndarray, np.ndarray, str]:
    """HC+ of the p-values *p*: ``(value, i*, p_(i*), i/n, HC_i, reason)``."""
    n = len(p)
    if n == 0:
        return math.nan, 0, math.nan, np.array([]), np.array([]), "no nanowell has a p-value"
    ps = np.sort(np.asarray(p, dtype=float))
    i = np.arange(1, n + 1)
    ok = (ps > 1.0 / n) & (ps < 1.0) & (i <= alpha0 * n)
    if not ok.any():
        return math.nan, 0, math.nan, np.array([]), np.array([]), unscored_reason(ps, alpha0)
    idx = np.flatnonzero(ok)
    q = ps[idx]
    curve = np.sqrt(n) * (i[idx] / n - q) / np.sqrt(q * (1.0 - q))
    best = int(np.argmax(curve))
    return float(curve[best]), int(idx[best] + 1), float(q[best]), i[idx] / n, curve, ""


def higher_criticism(well: Well, pooled: dict, alpha0: float) -> HC:
    p, ids, strata, unscored = p_values(well, pooled)
    value, i_star, p_star, cx, cy, reason = hc_plus(p, alpha0)
    out = HC(value, i_star, p_star, len(p), cx, cy, unscored=unscored, reason=reason)
    if np.isfinite(p_star):
        hit = p <= p_star
        order = np.argsort(p[hit], kind="stable")
        out.candidates = [(ids[hit][j], strata[hit][j], float(p[hit][j])) for j in order]
    return out


def statistic(alpha0: float):
    """The HC+ value of a well against a pool, as the permutation and leave-one-out nulls call it."""
    def stat(well: Well, pooled: dict) -> float:
        p, _ids, _strata, _unscored = p_values(well, pooled)
        return hc_plus(p, alpha0)[0]
    return stat


def resolution_warning(n_control_wells: int, pooled: dict, n: int) -> str:
    """A sentence when the pool cannot resolve p-values at the ``1/n`` scale; ``""`` when it can."""
    size = pool_size(pooled)
    if n_control_wells == 2:
        return ("with two control wells, each control well's leave-one-out pool is the single other control well: "
                "the control null of HC cannot resolve p-values at the 1/n scale")
    if n_control_wells == 0:
        return "there is no control well: HC has no control null to compare against"
    if n_control_wells == 1:
        return (f"the control pool is a single control well ({size} nanowells): its p-values cannot resolve the 1/n "
                f"scale of a {n}-nanowell well, so HC is poorly calibrated")
    if size + 1 < RESOLUTION_FACTOR * n:
        return (f"the control pool holds {size} nanowells, so its smallest p-value is 1/{size + 1}: fewer than "
                f"{RESOLUTION_FACTOR}× a well's {n} nanowells, which blunts HC's resolution at the 1/n scale")
    return ""
