"""Plates drawn at the level of counts, and the exceedance test's signed root z with a careful multi-start fit.

Each well's count K is binomial(1,000, πᵢ), with πᵢ drawn from a beta distribution of mean π and intra-well
correlation r (r = 0 gives a plain binomial). The fit tries three starting values of r and keeps the best, so a fit
stuck near a boundary does not decide the result.
"""
import math
import sys
from pathlib import Path

import numpy as np
from scipy import optimize
from scipy.special import logit

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))   # the repository root
from review.features.nanowell.rare_exceedance_compute import _bb_loglik  # noqa: E402

N = 1000


def draw(rng, mu, r, m):
    if r <= 0:
        return rng.binomial(N, mu, m).astype(float)
    return rng.binomial(N, rng.beta(mu * (1 - r) / r, (1 - mu) * (1 - r) / r, m)).astype(float)


def zstat(k, tr):
    n = np.full(len(k), float(N)); b0 = logit(max(k.mean(), 0.5) / N); r1 = []; r0 = []
    for th in (-12, -6, -3.5):
        r1.append(optimize.minimize(lambda v: -_bb_loglik(k, n, v[0] + v[1] * tr, v[2]), [b0, 0.0, th], method='L-BFGS-B', bounds=[(-9, -1), (-4, 4), (-16, -1)]))
        r0.append(optimize.minimize(lambda v: -_bb_loglik(k, n, np.full(len(k), v[0]), v[1]), [b0, th], method='L-BFGS-B', bounds=[(-9, -1), (-16, -1)]))
    a = min(r1, key=lambda r: r.fun); b = min(r0, key=lambda r: r.fun)
    return math.copysign(math.sqrt(max(2 * (b.fun - a.fun), 0)), a.x[1])
