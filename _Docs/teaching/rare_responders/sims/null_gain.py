"""Slides 18 and 31, chapter Figure 15: how much the fit with a gap gains when the molecule does nothing.

1,500 plates of 4 control and 4 molecule wells of 1,000 nanowells. Every well draws its own rate from the beta
distribution of the example plate's fit without a gap (molecule A; shared rate and r read from
../results/example_plate.json), so the plates scatter as much as that fit says real wells do. Both fits are run on each
plate with several starting values, and the signed root z = sign(gap) × √D is kept.
Writes ../results/null_gain.npy (the 1,500 z values) and ../results/null_gain.json (their summary).
Usage: python null_gain.py [plates]
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import optimize
from scipy.special import logit

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))   # the repository root
from review.features.nanowell.rare_exceedance_compute import fit_bb, _bb_loglik  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EX = json.loads((ROOT / 'results' / 'example_plate.json').read_text())['A']
MU, R = EX['mu0'], EX['r0']
SEED = 18


def best(k, n, tr, gap):
    b0 = logit(max(k.mean(), 0.5) / 1000); res = []
    for th in (-12, -6, -3.5):
        if gap:
            res.append(optimize.minimize(lambda v: -_bb_loglik(k, n, v[0] + v[1] * tr, v[2]), [b0, 0.0, th], method='L-BFGS-B', bounds=[(-9, -1), (-4, 4), (-16, -1)]))
        else:
            res.append(optimize.minimize(lambda v: -_bb_loglik(k, n, np.full(8, v[0]), v[1]), [b0, th], method='L-BFGS-B', bounds=[(-9, -1), (-16, -1)]))
    r = min(res, key=lambda r: r.fun); return -r.fun, r.x


if __name__ == '__main__':
    plates = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    rng = np.random.default_rng(SEED); n = np.full(8, 1000.0); tr = np.array([0] * 4 + [1] * 4); X = np.column_stack([np.ones(8), tr])
    a, b = MU * (1 - R) / R, (1 - MU) * (1 - R) / R
    zs = []
    for _ in range(plates):
        k = rng.binomial(1000, rng.beta(a, b, 8)).astype(float)
        l1, x1 = best(k, n, tr, True); l0, _ = best(k, n, tr, False)
        f1 = fit_bb(k, n, X)
        if f1.loglik > l1:
            l1, x1 = f1.loglik, [f1.coef[0], f1.coef[1], 0]
        zs.append(math.copysign(math.sqrt(max(2 * (l1 - l0), 0)), x1[1]))
    zs = np.array(zs); g = zs ** 2 / 2; gA = EX['l1'] - EX['l0']
    np.save(ROOT / 'results' / 'null_gain.npy', zs)
    out = dict(script='sims/null_gain.py', settings=dict(plates=plates, rate=MU, r=R, seed=SEED), share_z_above_1645=float((zs > 1.645).mean()),
               share_z_above_2326=float((zs > 2.326).mean()), share_z_above_309=float((zs > 3.09).mean()), z_max=float(zs.max()), z_min=float(zs.min()),
               z_for_true_5pct=float(np.quantile(zs, 0.95)), gain_median=float(np.median(g)), gain_p95=float(np.percentile(g, 95)), gain_max=float(g.max()),
               example_gain=gA, plates_reaching_example_gain=int((g >= gA).sum()))
    (ROOT / 'results' / 'null_gain.json').write_text(json.dumps(out, indent=1)); print(out)
