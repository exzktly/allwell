"""Slide 27: how the percentile chosen for the cutoff trades false positives against detection.

4 control and 4 molecule wells of 1,000 nanowells, one molecule per plate (no correction). Each plate is tested at all
three percentiles. Each cell reports the share of plates with p < 0.05 and its Monte Carlo standard error.
Writes ../results/cutoff_tradeoff.json.  Usage: python cutoff_tradeoff.py [plates_per_cell] [processes]
"""
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from plate_model import plate, counts, z_of, MU, SD, WELL_SD, N

SCENARIOS = [('no effect', 0.0, None),
             ('1 % responders, far out (2–4× the 99th)', 0.01, (2, 4)),
             ('1 % responders, just past (1–1.5× the 99th)', 0.01, (1, 1.5)),
             ('3 % responders, modest (0.8–1.2× the 99th)', 0.03, (0.8, 1.2))]
PCTS = (95, 99, 99.9)
SEED = 20261125
Z05 = 1.6448536269514722


def chunk(args):
    si, ci, plates = args
    _, share, mult = SCENARIOS[si]
    rng = np.random.default_rng([SEED, si, ci])
    hits = {p: 0 for p in PCTS}
    for _ in range(plates):
        c, m = plate(rng, 4, 4, share, mult)
        for p in PCTS:
            hits[p] += z_of(*counts(c, m, p)) > Z05
    return si, hits, plates


if __name__ == '__main__':
    plates = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    t0 = time.time()
    jobs = [(si, ci, plates // procs) for si in range(len(SCENARIOS)) for ci in range(procs)]
    tot = {si: {p: 0 for p in PCTS} for si in range(len(SCENARIOS))}; ns = {si: 0 for si in range(len(SCENARIOS))}
    with Pool(procs) as pool:
        for si, hits, n in pool.imap_unordered(chunk, jobs):
            ns[si] += n
            for p in PCTS: tot[si][p] += hits[p]
    rows = []
    for si, (name, share, mult) in enumerate(SCENARIOS):
        cells = {}
        for p in PCTS:
            f = tot[si][p] / ns[si]
            cells[str(p)] = dict(share=f, se=math.sqrt(f * (1 - f) / ns[si]))
        rows.append(dict(scenario=name, responder_share=share, multiple_of_q99=mult, plates=ns[si], cells=cells))
    out = dict(script='sims/cutoff_tradeoff.py', settings=dict(nanowells_per_well=N, log_fc_mean=MU, log_fc_sd=SD, well_shift_sd=WELL_SD,
               wells='4 control + 4 molecule', cutoff='percentile of the pooled control nanowells', threshold='one-sided p < 0.05', seed=SEED),
               rows=rows, seconds=round(time.time() - t0, 1))
    Path(__file__).resolve().parents[1].joinpath('results', 'cutoff_tradeoff.json').write_text(json.dumps(out, indent=1))
    for r in rows: print(r['scenario'], {p: f"{c['share']*100:.1f} ± {c['se']*100:.1f}" for p, c in r['cells'].items()})
    print(out['seconds'], 's')
