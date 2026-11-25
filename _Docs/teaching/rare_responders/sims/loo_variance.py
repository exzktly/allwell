"""Slide 37: how much control wells' counts vary, under three ways of setting each control well's cutoff.

Four control wells of 1,000 nanowells, each with its own small shift in log fold change (the plate model of
plate_model.py). For each well, K is counted against (a) the 99th percentile of all four wells pooled, including its own
nanowells; (b) the 99th percentile of the other three wells only (leave-one-out); (c) a cutoff known in advance, the
99th percentile of the curve the nanowells are drawn from, which is the variance the other two try to estimate.
Writes ../results/loo_variance.json.  Usage: python loo_variance.py [plates]
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

from plate_model import MU, SD, WELL_SD, N

SEED = 5

if __name__ == '__main__':
    plates = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    rng = np.random.default_rng(SEED); own, loo, known = [], [], []
    fixed = MU + stats.norm.ppf(0.99) * np.sqrt(SD ** 2 + WELL_SD ** 2)
    for _ in range(plates):
        wells = [rng.normal(MU + rng.normal(0, WELL_SD), SD, N) for _ in range(4)]
        pooled = np.quantile(np.concatenate(wells), 0.99)
        for i in range(4):
            own.append(int((wells[i] > pooled).sum()))
            loo.append(int((wells[i] > np.quantile(np.concatenate([w for j, w in enumerate(wells) if j != i]), 0.99)).sum()))
            known.append(int((wells[i] > fixed).sum()))
    own, loo, known = map(np.array, (own, loo, known))
    q = lambda a: [float(x) for x in np.quantile(a, [.025, .975])]
    out = dict(script='sims/loo_variance.py', settings=dict(plates=plates, wells_per_plate=4, nanowells_per_well=N, log_fc_mean=MU, log_fc_sd=SD, well_shift_sd=WELL_SD, seed=SEED),
               own_mean=float(own.mean()), own_var=float(own.var()), own_q=q(own), loo_mean=float(loo.mean()), loo_var=float(loo.var()), loo_q=q(loo),
               known_mean=float(known.mean()), known_var=float(known.var()), own=own[:200].tolist(), loo=loo[:200].tolist())
    Path(__file__).resolve().parents[1].joinpath('results', 'loo_variance.json').write_text(json.dumps(out, indent=1))
    print({k: v for k, v in out.items() if k not in ('own', 'loo', 'settings')})
