"""Rare-responder analysis, Test A (primary): how many nanowells of a well exceed the control tail.

**The threshold.** ``T`` is the chosen percentile (99th by default) of the control values, computed within each
loading stratum when strata are set (``rare_data_compute.assign_strata``) and over the whole pool otherwise — the
page's analysis sets none. A treatment well is counted against ``T`` from every
control well; a control well against ``T`` from every *other* control well (leave-one-out), so a control well's
count is not lowered by its own nanowells having set the bar. A fixed threshold chosen before running
(``RareParams.fixed_threshold``) replaces the percentile in every stratum.

**The count.** ``k_w`` nanowells of well ``w`` exceed their stratum's ``T`` (strictly: ``value > T``) out of the
``n_w`` it keeps in strata with a threshold.

**The group test** (:func:`beta_binomial_test`, with at least ``min_controls_bb`` control and ``min_treated_bb``
treatment wells): ``k_w ~ BetaBinomial(n_w, p_w, ρ)``, ``logit p_w = β0 + β1·treatment [+ plate]``, fitted by
maximum likelihood. ρ is the intra-well correlation, the overdispersion that makes the wells — not the nanowells —
the replicate unit; with ρ → 0 the model is the binomial that treats nanowells as independent. ``β1 > 0`` is tested
by the one-sided likelihood-ratio test (the signed root ``z = sign(β1)·√(2Δℓ)``, ``p = 1 − Φ(z)``). The odds ratio is
``exp(β1)``, with a 95 % profile-likelihood interval. The per-well counts already sit against per-stratum thresholds,
so under the null every stratum's exceedance rate is the same ``1 − percentile``; the stratum is therefore not a
covariate by default (adding it would treat a well's strata as independent replicates of it).

**With too few wells** (:func:`fallback_test`): each treatment well's exceedance rate ``k_w / n_w`` is placed in the
empirical null of the control wells' leave-one-out rates, and a one-sided Fisher exact test of the pooled counts is
reported with the label :data:`FISHER_LABEL` — it ignores between-well variance and is anti-conservative, so no
interpretation rests on it.

Qt-free and matplotlib-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import optimize, stats
from scipy.special import betaln, expit, gammaln, logit

from review.features.nanowell.rare_null_compute import Well, empirical_p, pool

FISHER_LABEL = "Fisher exact, one-sided — ignores between-well variance; anti-conservative"
BB_LABEL = "beta-binomial regression, one-sided likelihood-ratio test"
EMPIRICAL_LABEL = "empirical null of control wells' leave-one-out exceedance rates"
#: Bounds on the regression coefficients and on logit(ρ): a separated design (no treatment exceedance at all)
#: drives β1 to a bound, which is reported as such rather than as a number.
COEF_BOUND = 25.0
RHO_LOGIT_BOUND = 14.0
#: Below this many control values in a stratum, the percentile cannot be resolved: it is the pool's top values.
def min_pool_for(percentile: float) -> int:
    return int(math.ceil(100.0 / (100.0 - percentile)))


def threshold_of(pooled: dict, percentile: float, fixed: float | None) -> dict:
    """Per stratum, the threshold: *fixed* where given, else the *percentile* of the pool."""
    if fixed is not None:
        return {s: float(fixed) for s in pooled}
    return {s: float(np.quantile(v, percentile / 100.0)) for s, v in pooled.items() if len(v)}


def count(well: Well, thresholds: dict) -> tuple[int, int, dict]:
    """``(k, n, {stratum: (k_s, n_s)})`` of *well* against *thresholds*; a stratum with no threshold is not counted."""
    per = {}
    for s, v in well.values.items():
        if s in thresholds:
            per[s] = (int(np.sum(v > thresholds[s])), len(v))
    return sum(k for k, _ in per.values()), sum(n for _, n in per.values()), per


@dataclass
class WellCount:
    key: str
    plate: str
    condition: str
    treated: bool
    k: int
    n: int
    thresholds: dict
    per_stratum: dict
    empirical_p: float = math.nan          # treatment wells: placed in the control wells' leave-one-out rates

    @property
    def rate(self) -> float:
        return self.k / self.n if self.n else math.nan


def well_counts(controls: list[Well], treated: list[Well], percentile: float, fixed: float | None) -> list[WellCount]:
    """Every well's count: treatment wells against the full control pool, control wells leave-one-out."""
    out = []
    for w in controls:
        thr = threshold_of(pool([o for o in controls if o.key != w.key]), percentile, fixed)
        k, n, per = count(w, thr)
        out.append(WellCount(w.key, w.plate, w.condition, False, k, n, thr, per))
    full = threshold_of(pool(controls), percentile, fixed)
    null = [c.rate for c in out]
    for w in treated:
        k, n, per = count(w, full)
        wc = WellCount(w.key, w.plate, w.condition, True, k, n, full, per)
        wc.empirical_p = empirical_p(wc.rate, null)
        out.append(wc)
    return out


def _bb_loglik(k, n, eta, theta) -> float:
    p = np.clip(expit(eta), 1e-12, 1 - 1e-12)
    rho = expit(theta)
    s = (1.0 - rho) / rho
    a, b = p * s, (1.0 - p) * s
    return float(np.sum(gammaln(n + 1) - gammaln(k + 1) - gammaln(n - k + 1) + betaln(k + a, n - k + b) - betaln(a, b)))


@dataclass
class BBFit:
    coef: np.ndarray
    theta: float
    loglik: float
    at_bound: bool

    @property
    def rho(self) -> float:
        return float(expit(self.theta))


def fit_bb(k, n, X, fixed: dict | None = None) -> BBFit:
    """Maximum-likelihood beta-binomial regression of *k* of *n* on design *X*; *fixed* pins coefficients by index."""
    k, n, X = np.asarray(k, float), np.asarray(n, float), np.asarray(X, float)
    fixed = fixed or {}
    free = [j for j in range(X.shape[1]) if j not in fixed]
    rate = np.clip((k.sum() + 0.5) / (n.sum() + 1.0), 1e-6, 1 - 1e-6)

    def unpack(x):
        coef = np.zeros(X.shape[1])
        for j, v in fixed.items():
            coef[j] = v
        coef[free] = x[:-1]
        return coef, x[-1]

    def nll(x):
        coef, theta = unpack(x)
        return -_bb_loglik(k, n, X @ coef, theta)

    bounds = [(-COEF_BOUND, COEF_BOUND)] * len(free) + [(-RHO_LOGIT_BOUND, RHO_LOGIT_BOUND)]
    best = None
    for theta0 in (-6.0, -3.0, -1.0):
        x0 = np.zeros(len(free) + 1)
        if 0 in free:
            x0[free.index(0)] = logit(rate) - sum(X[:, j].mean() * v for j, v in fixed.items())
        x0[-1] = theta0
        res = optimize.minimize(nll, x0, method="L-BFGS-B", bounds=bounds)
        if best is None or res.fun < best.fun:
            best = res
    coef, theta = unpack(best.x)
    # Only the treatment coefficient (column 1) at its bound leaves the odds ratio unestimated: a plate covariate
    # driven to the bound by a plate with no exceedance anywhere does not, and β1 stays finite and estimable from the
    # other plates. A design without a treatment column (an intercept-only fit) asks about every coefficient.
    watched = [1] if X.shape[1] > 1 else list(range(X.shape[1]))
    at_bound = bool(any(j not in fixed and abs(coef[j]) >= COEF_BOUND - 1e-3 for j in watched))
    return BBFit(coef, float(theta), float(-best.fun), at_bound)


def design(rows: list[WellCount]) -> tuple[np.ndarray, list[str], list[str]]:
    """Intercept, treatment, and a plate column per plate past the first that keeps the design full rank."""
    plates = sorted({r.plate for r in rows})
    cols = [np.ones(len(rows)), np.array([1.0 if r.treated else 0.0 for r in rows])]
    names = ["intercept", "treatment"]
    dropped = []
    for plate in plates[1:]:
        col = np.array([1.0 if r.plate == plate else 0.0 for r in rows])
        trial = np.column_stack(cols + [col])
        if np.linalg.matrix_rank(trial) == trial.shape[1]:
            cols.append(col)
            names.append(f"plate[{plate}]")
        else:
            dropped.append(plate)
    return np.column_stack(cols), names, dropped


@dataclass
class ExceedanceResult:
    percentile: float
    method: str                          # BB_LABEL, or "fallback" when too few wells, or "refused"
    counts: list
    p: float = math.nan                  # the calibrated group p (beta-binomial); NaN in the fallback
    odds_ratio: float = math.nan
    ci: tuple = (math.nan, math.nan)
    rho: float = math.nan
    beta1: float = math.nan
    lrt: float = math.nan
    fisher_p: float = math.nan
    fisher_or: float = math.nan
    min_p: float = math.nan              # the smallest p the empirical null can give a single well
    notes: list = field(default_factory=list)


def _profile_ci(k, n, X, full: BBFit, level: float = 0.95) -> tuple[float, float]:
    crit = stats.chi2.ppf(level, 1)
    b1 = full.coef[1]

    def gap(b):
        return 2.0 * (full.loglik - fit_bb(k, n, X, fixed={1: b}).loglik) - crit

    ends = []
    for direction in (-1.0, 1.0):
        step, edge = 0.5, b1
        found = None
        while abs(edge - b1) < 2 * COEF_BOUND:
            nxt = edge + direction * step
            if abs(nxt) >= COEF_BOUND:
                break
            if gap(nxt) > 0:
                found = optimize.brentq(gap, min(edge, nxt), max(edge, nxt), xtol=1e-4)
                break
            edge, step = nxt, step * 1.6
        ends.append(found if found is not None else direction * math.inf)
    return math.exp(ends[0]) if np.isfinite(ends[0]) else 0.0, math.exp(ends[1]) if np.isfinite(ends[1]) else math.inf


def beta_binomial_test(counts: list[WellCount], percentile: float, *, ci: bool = True) -> ExceedanceResult:
    """The one-sided beta-binomial likelihood-ratio test of a higher exceedance odds in the treatment wells (*ci*:
    also the profile-likelihood interval of the odds ratio, which costs a few dozen more fits)."""
    rows = [c for c in counts if c.n > 0]
    result = ExceedanceResult(percentile, BB_LABEL, counts, min_p=_min_p(counts))
    k = np.array([r.k for r in rows], float)
    n = np.array([r.n for r in rows], float)
    if k.sum() == 0:
        result.notes.append("no nanowell of any well exceeds the threshold: the odds ratio is undefined")
        return result
    X, names, dropped = design(rows)
    if dropped:
        result.notes.append(f"plate effect not estimable for {', '.join(dropped)} (confounded with treatment)")
    full = fit_bb(k, n, X)
    null = fit_bb(k, n, X, fixed={1: 0.0})
    lrt = max(0.0, 2.0 * (full.loglik - null.loglik))
    z = math.copysign(math.sqrt(lrt), full.coef[1])
    result.beta1, result.lrt, result.rho = float(full.coef[1]), lrt, full.rho
    result.p = float(stats.norm.sf(z))
    # One arm with no exceedance at all separates the design: the likelihood keeps rising as the odds ratio runs off
    # to 0 or ∞, and wherever the optimiser stopped is no estimate. Say so, never print that number.
    separated = _separation(rows)
    result.odds_ratio = (math.exp(full.coef[1]) if not (full.at_bound or separated)
                         else (0.0 if separated == "treatment" or (not separated and full.coef[1] < 0) else math.inf))
    if ci:
        result.ci = _profile_ci(k, n, X, full)
    if separated:
        result.notes.append(f"no {separated} well has a nanowell above the threshold, so the odds ratio is unbounded "
                            f"(reported as {'0' if separated == 'treatment' else '∞'}); the p-value and the "
                            "interval's finite end stand")
    elif full.at_bound:
        result.notes.append("complete separation: the treatment estimate reached its bound, so the odds ratio is "
                            f"reported as {'0' if full.coef[1] < 0 else '∞'}")
    if len(names) > 2:
        result.notes.append("covariates: " + ", ".join(names[2:]))
    return result


def _separation(rows: list[WellCount]) -> str:
    """``"control"`` or ``"treatment"`` when that arm has no exceedance at all (the other having some), else ``""``."""
    kt = sum(r.k for r in rows if r.treated)
    kc = sum(r.k for r in rows if not r.treated)
    if kc == 0 and kt > 0:
        return "control"
    if kt == 0 and kc > 0:
        return "treatment"
    return ""


def _min_p(counts: list[WellCount]) -> float:
    n_ctrl = sum(1 for c in counts if not c.treated)
    return 1.0 / (n_ctrl + 1) if n_ctrl else math.nan


def fisher(counts: list[WellCount]) -> tuple[float, float]:
    """One-sided Fisher exact test of the pooled treatment counts against the pooled control counts."""
    kt = sum(c.k for c in counts if c.treated)
    nt = sum(c.n for c in counts if c.treated)
    kc = sum(c.k for c in counts if not c.treated)
    nc = sum(c.n for c in counts if not c.treated)
    if nt == 0 or nc == 0:
        return math.nan, math.nan
    res = stats.fisher_exact([[kt, nt - kt], [kc, nc - kc]], alternative="greater")
    return float(res.statistic), float(res.pvalue)


def fallback_test(counts: list[WellCount], percentile: float, group_allowed: bool) -> ExceedanceResult:
    """Per-well empirical p-values, and the pooled Fisher test when group-level inference is allowed at all."""
    result = ExceedanceResult(percentile, EMPIRICAL_LABEL, counts, min_p=_min_p(counts))
    if sum(1 for c in counts if not c.treated) < 2:
        # A lone control well's leave-one-out pool is empty: there is no control null and no per-well p either.
        result.notes.append("fewer than 2 control wells: no control null, so no calibrated result, per well or group")
    else:
        result.notes.append("too few wells for the beta-binomial regression: per-well empirical p-values only")
    if group_allowed:
        result.fisher_or, result.fisher_p = fisher(counts)
    return result


def exceedance_test(controls: list[Well], treated: list[Well], percentile: float, fixed: float | None,
                    *, bb_allowed: bool, group_allowed: bool, ci: bool = True) -> ExceedanceResult:
    counts = well_counts(controls, treated, percentile, fixed)
    if bb_allowed:
        result = beta_binomial_test(counts, percentile, ci=ci)
        result.fisher_or, result.fisher_p = fisher(counts)
    else:
        result = fallback_test(counts, percentile, group_allowed)
    if fixed is not None:
        result.notes.append(f"fixed threshold {fixed:g} (chosen before running) in every stratum")
    else:
        small = sorted({s for c in counts if not c.treated for s in c.thresholds}
                       - {s for s, v in pool(controls).items() if len(v) >= min_pool_for(percentile)})
        if small:
            result.notes.append(f"stratum {', '.join(small)}: fewer than {min_pool_for(percentile)} control "
                                f"nanowells, so its {percentile:g}th percentile is the pool's top value(s)")
    return result


def counts_frame(result: ExceedanceResult) -> pd.DataFrame:
    """One row per well: its count, rate, and (treatment wells) empirical p."""
    return pd.DataFrame([{"well_key": c.key, "plate_id": c.plate, "condition": c.condition, "treated": c.treated,
                          "percentile": result.percentile, "k": c.k, "n": c.n, "rate": c.rate,
                          "empirical_p": c.empirical_p} for c in result.counts])
