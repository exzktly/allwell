"""Rare-responder analysis, part 2: wells as units, leave-one-well-out control pools, and whole-well permutations.

Every calibrated p-value of the analysis comes from here, in one of two shapes:

* **Per well, an empirical null.** A statistic computed for each *control* well against the pool of every *other*
  control well (leave-one-well-out, so a control well is never scored against itself) is the null distribution;
  a treatment well's statistic, computed against the pool of all control wells, is placed in it by
  :func:`empirical_p`, ``(1 + #{null >= observed}) / (n_null + 1)``.
* **Per treatment, a permutation test over whole wells.** :func:`permutation_test` relabels *wells* — never
  nanowells — between treatment and control, recomputes each pseudo-treatment well's statistic against the pool of
  the pseudo-control wells, and compares the mean over the pseudo-treatment wells with the observed one. With wells
  on more than one plate the relabelling stays within each plate (the treatment count of each plate is kept), so a
  plate effect cannot pass for a treatment effect. Up to ``max_permutations`` labelings the set is enumerated
  exhaustively and ``p = #{T_perm >= T_obs} / n`` (the observed labeling among them); above it, distinct labelings
  are drawn with a fixed seed and ``p = (1 + #{T_perm >= T_obs}) / (B + 1)``. Either way the result carries the
  number of distinct labelings used and the smallest p it can reach.

Qt-free and matplotlib-free.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Well:
    """One well's kept nanowells, by loading stratum, sorted by value (ids aligned)."""

    key: str
    plate: str
    condition: str
    values: dict          # stratum -> sorted float array
    ids: dict             # stratum -> nanowell ids, in the same order

    @property
    def n(self) -> int:
        return int(sum(len(v) for v in self.values.values()))


def wells_from(kept: pd.DataFrame) -> dict[str, Well]:
    """Every well of a prepared table (``rare_data_compute.Prepared.kept``) as a :class:`Well`."""
    out = {}
    for key, rows in kept.groupby("well_key", sort=True):
        values, ids = {}, {}
        for stratum, part in rows.groupby("stratum", sort=True):
            order = np.argsort(part["value"].to_numpy(dtype=float), kind="stable")
            values[stratum] = part["value"].to_numpy(dtype=float)[order]
            ids[stratum] = part["nanowell_id"].to_numpy()[order]
        out[key] = Well(key, str(rows["plate_id"].iloc[0]), str(rows["condition"].iloc[0]), values, ids)
    return out


def pool(wells, strata=None) -> dict:
    """The control pool of *wells*: per stratum, every value of every well, sorted."""
    strata = strata if strata is not None else sorted({s for w in wells for s in w.values})
    out = {}
    for s in strata:
        parts = [w.values[s] for w in wells if s in w.values]
        if parts:
            out[s] = np.sort(np.concatenate(parts))
    return out


def pool_size(pooled: dict) -> int:
    return int(sum(len(v) for v in pooled.values()))


def empirical_p(observed: float, null) -> float:
    """``(1 + #{null >= observed}) / (n + 1)`` over the finite values of *null*; NaN when *observed* is undefined or
    the null is empty."""
    null = np.asarray([v for v in null if v is not None and np.isfinite(v)], dtype=float)
    if observed is None or not np.isfinite(observed) or null.size == 0:
        return math.nan
    return float((1 + np.sum(null >= observed)) / (null.size + 1))


def loo_null(controls: list[Well], statistic) -> dict[str, float]:
    """Each control well's *statistic* (``statistic(well, pool) -> float``) against its leave-one-out pool."""
    return {w.key: statistic(w, pool([o for o in controls if o.key != w.key])) for w in controls}


@dataclass
class Permutation:
    """A permutation test's result. ``p`` is NaN with ``reason`` set when it could not be computed."""

    observed: float
    p: float
    n_labelings: int
    exhaustive: bool
    min_p: float
    reason: str = ""
    null: list = field(default_factory=list)


def _by_plate(units: list[Well], treated: set) -> list[tuple[list[str], int]]:
    plates: dict[str, list[str]] = {}
    for w in units:
        plates.setdefault(w.plate, []).append(w.key)
    return [(sorted(keys), sum(k in treated for k in keys)) for _, keys in sorted(plates.items())]


def labelings(units: list[Well], treated: set, max_labelings: int, seed: int) -> tuple[list[frozenset], bool]:
    """The labelings a permutation test runs over, the observed one first, and whether they are all of them."""
    plates = _by_plate(units, treated)
    total = math.prod(math.comb(len(keys), t) for keys, t in plates)
    observed = frozenset(treated)
    if total <= max_labelings:
        combos = [itertools.combinations(keys, t) for keys, t in plates]
        every = [frozenset(itertools.chain.from_iterable(c)) for c in itertools.product(*combos)]
        return [observed] + [lab for lab in every if lab != observed], True
    rng = np.random.default_rng(seed)
    seen = {observed}
    out = [observed]
    target = max_labelings
    while len(out) < target:
        lab = frozenset(itertools.chain.from_iterable(
            rng.choice(keys, size=t, replace=False).tolist() for keys, t in plates))
        if lab not in seen:
            seen.add(lab)
            out.append(lab)
    return out, False


def permutation_test(units: list[Well], treated: set, statistic, max_labelings: int, seed: int) -> Permutation:
    """The mean of *statistic* over the treatment wells, against its distribution over whole-well relabelings."""
    cache: dict = {}

    def mean_stat(labeled: frozenset) -> float:
        controls = [w for w in units if w.key not in labeled]
        pooled_key = frozenset(w.key for w in controls)
        values = []
        for w in units:
            if w.key in labeled:
                key = (w.key, pooled_key)
                if key not in cache:
                    cache[key] = statistic(w, pool(controls))
                values.append(cache[key])
        finite = [v for v in values if np.isfinite(v)]
        return float(np.mean(finite)) if finite else math.nan

    labs, exhaustive = labelings(units, treated, max_labelings, seed)
    stats = [mean_stat(lab) for lab in labs]
    observed = stats[0]
    n = len(labs)
    if n < 2:
        return Permutation(observed, math.nan, n, exhaustive, math.nan, "only one labeling of the wells is possible")
    if not np.isfinite(observed):
        return Permutation(observed, math.nan, n, exhaustive, math.nan, "the statistic is undefined for every "
                           "treatment well")
    others = np.asarray(stats[1:], dtype=float)
    ge = int(np.sum(np.nan_to_num(others, nan=-np.inf) >= observed))
    # Exhaustive: the observed labeling is one of n, so p = (1 + ge) / n. Sampled: the same formula with B + 1 = n.
    p = (1 + ge) / n
    return Permutation(observed, float(p), n, exhaustive, 1.0 / n, null=stats[1:])
