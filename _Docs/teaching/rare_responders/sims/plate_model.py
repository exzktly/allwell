"""The simulated plate shared by the cutoff and calibration simulations.

Each nanowell's log fold change is normal with mean MU and SD SD. Each well adds its own shift, normal with mean 0
and SD WELL_SD, so wells differ a little (this is what makes r above 0). A molecule well may replace a share of its
nanowells with responders whose fold change is a multiple of Q99, the 99th percentile of the generating curve.

The test is the exceedance test as the slides and the chapter describe it: one cutoff for every well, the chosen
percentile of all control nanowells pooled; each well reduced to K, its count above the cutoff; a beta-binomial fit
with and without a gap between the groups; D = 2 × the gain in log-likelihood; z = sign(gap) × √D; one-sided p.
"""
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))   # the repository root
from review.features.nanowell.rare_exceedance_compute import fit_bb  # noqa: E402

N = 1000                  # nanowells per well
MU, SD = -0.05, 0.36      # log fold change of an ordinary nanowell
WELL_SD = 0.05            # SD of a well's own shift in log fold change
Q99 = math.exp(MU + stats.norm.ppf(0.99) * SD)   # FC 2.2


def plate(rng, n_ctrl, n_mol, share=0.0, mult=None):
    """Log fold changes of one plate: a list of control wells, then a list of molecule wells."""
    def well(resp):
        v = rng.normal(MU + rng.normal(0, WELL_SD), SD, N)
        if resp and share:
            m = rng.binomial(N, share)
            v[:m] = np.log(Q99 * rng.uniform(mult[0], mult[1], m))
        return v
    return [well(False) for _ in range(n_ctrl)], [well(True) for _ in range(n_mol)]


def counts(ctrl, mol, pct):
    """K of every well against one cutoff: the pct percentile of all control nanowells pooled."""
    cut = np.quantile(np.concatenate(ctrl), pct / 100)
    return [int((w > cut).sum()) for w in ctrl], [int((w > cut).sum()) for w in mol]


def z_of(kc, km):
    """Signed root of the likelihood-ratio statistic for a gap between the two groups."""
    k = np.array(kc + km, float)
    n = np.full(len(k), float(N))
    X = np.column_stack([np.ones(len(k)), [0] * len(kc) + [1] * len(km)])
    f1, f0 = fit_bb(k, n, X), fit_bb(k, n, X, fixed={1: 0.0})
    return math.copysign(math.sqrt(max(2 * (f1.loglik - f0.loglik), 0.0)), f1.coef[1])
