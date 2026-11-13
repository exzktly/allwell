"""Rare-responder analysis, the self-check: how often the tests fire on nothing, and how small an effect they find.

Run with the main analysis (``rare_report_compute.analyse(..., validate=True)``), on the dataset's own control
wells, so its answer is about *this* design — this many wells, this many nanowells, this loading — rather than an
idealised one.

* **False positives** (:func:`false_positive_check`): the control wells are split at random into a fake treatment
  group (half of them) and a fake control group, ``validation_splits`` times, each split distinct, and all three
  group tests are run on each. A calibrated test's share of p < 0.05 is about 5 % or less. The splits share wells,
  so the shares are correlated estimates, and with few control wells only a handful of distinct splits exist —
  both are stated in the result.
* **Power** (:func:`power_check`): for each spike count ``k`` (1, 3, 5, 10, 20), ``validation_replicates`` times,
  a random split is made and ``k`` nanowells of each fake treatment well are replaced by synthetic responders whose
  value is drawn uniformly from ``spike_range`` (2× to 4×) times the 99th percentile of their stratum in the fake
  control wells. The share of replicates whose p < 0.05 is each test's power at ``k``.

Each replicate runs the tests exactly as the main analysis does, with the permutation cap lowered to
``validation_permutations`` and no confidence interval, and with its own seed derived from ``RareParams.seed``.

Qt-free and matplotlib-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from review.features.nanowell import rare_ad_compute as ad
from review.features.nanowell import rare_exceedance_compute as ex
from review.features.nanowell import rare_hc_compute as hc
from review.features.nanowell.rare_data_compute import Prepared, RareParams
from review.features.nanowell.rare_null_compute import Well, permutation_test, pool, wells_from

TESTS = ("exceedance", "hc", "ad")
LEVEL = 0.05


@dataclass
class Validation:
    false_positive: pd.DataFrame          # per test: splits run, share of p < 0.05, smallest attainable p
    power: pd.DataFrame                   # per test and k: replicates run, detection power
    notes: list = field(default_factory=list)

    @property
    def log(self) -> dict:
        return {"false_positive": self.false_positive.to_dict(orient="records"),
                "power": self.power.to_dict(orient="records"), "notes": list(self.notes)}


def group_ps(controls: list[Well], treated: list[Well], params: RareParams, seed: int) -> dict:
    """The three group tests' p-values (and smallest attainable p) for *treated* against *controls*."""
    bb = len(controls) >= params.min_controls_bb and len(treated) >= params.min_treated_bb
    a = ex.exceedance_test(controls, treated, params.percentile, params.fixed_threshold,
                           bb_allowed=bb, group_allowed=True, ci=False)
    units = controls + treated
    keys = {w.key for w in treated}
    cap = min(params.max_permutations, params.validation_permutations)
    h = permutation_test(units, keys, hc.statistic(params.alpha0), cap, seed)
    d = permutation_test(units, keys, ad.statistic, cap, seed)
    return {"exceedance": (a.p, 0.0 if bb else math.nan), "hc": (h.p, h.min_p), "ad": (d.p, d.min_p)}


def _splits(keys: list[str], n_splits: int, seed: int) -> list[frozenset]:
    """Up to *n_splits* distinct halves of *keys*, as the fake treatment group of each split."""
    half = len(keys) // 2
    total = math.comb(len(keys), half)
    rng = np.random.default_rng(seed)
    seen: set = set()
    out = []
    while len(out) < min(n_splits, total):
        lab = frozenset(rng.choice(keys, size=half, replace=False).tolist())
        if lab not in seen:
            seen.add(lab)
            out.append(lab)
    return out


def _controls(prepared: Prepared) -> list[Well]:
    return [w for w in wells_from(prepared.kept).values() if w.condition == prepared.params.control]


def _enough(controls: list[Well], params: RareParams) -> str:
    need = 2 * params.min_wells_per_condition
    if len(controls) < need:
        return (f"{len(controls)} control well(s): the self-check needs at least {need} to split them into two groups "
                f"of {params.min_wells_per_condition}")
    return ""


def false_positive_check(prepared: Prepared) -> tuple[pd.DataFrame, list[str]]:
    params = prepared.params
    controls = _controls(prepared)
    why = _enough(controls, params)
    if why:
        return pd.DataFrame(columns=["test", "splits", "share_below_0.05", "min_attainable_p"]), [why]
    by_key = {w.key: w for w in controls}
    splits = _splits(sorted(by_key), params.validation_splits, params.seed)
    found: dict[str, list] = {t: [] for t in TESTS}
    floors: dict[str, list] = {t: [] for t in TESTS}
    for i, fake in enumerate(splits):
        res = group_ps([by_key[k] for k in by_key if k not in fake], [by_key[k] for k in fake], params,
                       params.seed + 1 + i)
        for t in TESTS:
            found[t].append(res[t][0])
            floors[t].append(res[t][1])
    rows = []
    for t in TESTS:
        ps = np.array([p for p in found[t] if np.isfinite(p)])
        rows.append({"test": t, "splits": int(ps.size),
                     "share_below_0.05": float(np.mean(ps < LEVEL)) if ps.size else math.nan,
                     "min_attainable_p": float(np.nanmax(floors[t])) if ps.size else math.nan})
    notes = [f"{len(splits)} distinct split(s) of {len(controls)} control wells; splits share wells, so their "
             "p-values are correlated"]
    return pd.DataFrame(rows), notes


def spike(well: Well, k: int, reference: dict, spike_range, rng) -> Well:
    """*well* with *k* random nanowells replaced by values of ``U(spike_range) × reference[stratum]``."""
    flat = [(s, j) for s, v in well.values.items() for j in range(len(v)) if s in reference]
    k = min(k, len(flat))
    chosen = rng.choice(len(flat), size=k, replace=False) if k else []
    values = {s: v.copy() for s, v in well.values.items()}
    ids = {s: v.copy() for s, v in well.ids.items()}
    for idx in chosen:
        s, j = flat[idx]
        values[s][j] = rng.uniform(*spike_range) * reference[s]
        ids[s][j] = f"spike:{ids[s][j]}"
    for s in values:
        order = np.argsort(values[s], kind="stable")
        values[s], ids[s] = values[s][order], ids[s][order]
    return Well(well.key, well.plate, well.condition, values, ids)


def power_check(prepared: Prepared) -> tuple[pd.DataFrame, list[str]]:
    params = prepared.params
    controls = _controls(prepared)
    why = _enough(controls, params)
    if why:
        return pd.DataFrame(columns=["test", "k", "replicates", "power"]), [why]
    by_key = {w.key: w for w in controls}
    rng = np.random.default_rng(params.seed + 7919)
    rows = []
    for k in params.spike_counts:
        hits: dict[str, list] = {t: [] for t in TESTS}
        for rep in range(params.validation_replicates):
            fake = _splits(sorted(by_key), 1, int(rng.integers(0, 2 ** 31)))[0]
            ctrl = [by_key[key] for key in by_key if key not in fake]
            p99 = ex.threshold_of(pool(ctrl), 99.0, None)
            treated = [spike(by_key[key], k, p99, params.spike_range, rng) for key in sorted(fake)]
            res = group_ps(ctrl, treated, params, params.seed + 101 * (rep + 1) + k)
            for t in TESTS:
                if np.isfinite(res[t][0]):
                    hits[t].append(res[t][0] < LEVEL)
        for t in TESTS:
            rows.append({"test": t, "k": k, "replicates": len(hits[t]),
                         "power": float(np.mean(hits[t])) if hits[t] else math.nan})
    notes = [f"{params.validation_replicates} replicate(s) per k; spikes are {params.spike_range[0]:g}–"
             f"{params.spike_range[1]:g}× the stratum's control 99th percentile, k per fake-treatment well"]
    return pd.DataFrame(rows), notes


def validate(prepared: Prepared, params: RareParams | None = None) -> Validation:
    if params is not None:
        prepared = replace(prepared, params=params)
    fp, fp_notes = false_positive_check(prepared)
    pw, pw_notes = power_check(prepared)
    return Validation(fp, pw, fp_notes + pw_notes)
