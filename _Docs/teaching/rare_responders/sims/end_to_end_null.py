"""Slide 31 footnote: how often the exceedance test calls a molecule that does nothing, simulated end to end.

Unlike the calibration bars of slide 31, which draw each well's count directly from a beta-binomial, these plates are
simulated nanowell by nanowell; the cutoff is the 99th percentile of the pooled control nanowells, estimated from the
plate, and each well's count is taken against it. Reports the share of plates with p below 0.05, 0.01 and 0.001, with
Monte Carlo standard errors, for 4 + 4 wells. Writes ../results/end_to_end_null.json.
Usage: python end_to_end_null.py [plates] [processes]
"""
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy import stats

from plate_model import plate, counts, z_of, MU, SD, WELL_SD, N

SEED = 20261126
LEVELS = (0.05, 0.01, 0.001)


def chunk(args):
    ci, plates = args
    rng = np.random.default_rng([SEED, ci])
    zs, ks = [], []
    for _ in range(plates):
        c, m = plate(rng, 4, 4)
        kc, km = counts(c, m, 99)
        zs.append(z_of(kc, km)); ks.append(kc + km)
    return zs, ks


if __name__ == '__main__':
    plates = int(sys.argv[1]) if len(sys.argv) > 1 else 40000
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    t0 = time.time()
    zs, ks = [], []
    with Pool(procs) as pool:
        for z, k in pool.imap_unordered(chunk, [(ci, plates // procs) for ci in range(procs)]):
            zs += z; ks += k
    zs = np.array(zs); ks = np.array(ks); n = len(zs)
    rates = {}
    for a in LEVELS:
        f = float((zs > stats.norm.isf(a)).mean()); rates[str(a)] = dict(share=f, se=math.sqrt(f * (1 - f) / n), ratio_to_nominal=f / a)
    kc = ks[:, :4]; km = ks[:, 4:]
    out = dict(script='sims/end_to_end_null.py', settings=dict(nanowells_per_well=N, log_fc_mean=MU, log_fc_sd=SD, well_shift_sd=WELL_SD,
               wells='4 control + 4 molecule', cutoff='99th percentile of the pooled control nanowells', seed=SEED),
               plates=n, rates=rates, z_for_true_5pct=float(np.quantile(zs, 0.95)),
               molecule_well_count_mean=float(km.mean()), molecule_well_count_var=float(km.var(ddof=1)),
               control_count_total_always_40=bool((kc.sum(1) == 40).all()), seconds=round(time.time() - t0, 1))
    Path(__file__).resolve().parents[1].joinpath('results', 'end_to_end_null.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != 'settings'}, indent=1))
