"""Slide 35: the share of plates in which the exceedance test detects a molecule, by design, spread r and odds ratio.

The control rate is the example plate's control rate (../results/example_plate.json); a molecule multiplies the odds
of a nanowell passing the cutoff by the stated odds ratio. One-sided p < 0.05. Writes ../results/power.json.
Usage: python power.py [plates_per_cell] [processes]
"""
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from count_model import draw, zstat

ROOT = Path(__file__).resolve().parents[1]
EX = json.loads((ROOT / 'results' / 'example_plate.json').read_text())
MU0 = EX['A']['mu_c']
R_STEP4 = round(EX['A']['r0'], 4)
DESIGNS = ((4, 4), (8, 4)); RS = (0.0, R_STEP4, 0.008); ORS = (1.5, 2.0, 2.5, 3.0)
CELLS = [(d, r, o) for d in DESIGNS for r in RS for o in ORS]
SEED = 20261128


def cell(args):
    ci, plates = args
    (c, t), r, o = CELLS[ci]; tr = np.array([0] * c + [1] * t)
    odds = MU0 / (1 - MU0) * o; mu1 = odds / (1 + odds)
    rng = np.random.default_rng([SEED, ci]); hits = 0
    for _ in range(plates):
        k = np.concatenate([draw(rng, MU0, r, c), draw(rng, mu1, r, t)])
        hits += zstat(k, tr) > 1.6448536269514722
    return ci, hits, plates


if __name__ == '__main__':
    plates = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    t0 = time.time(); res = {}
    with Pool(procs) as pool:
        for ci, h, n in pool.imap_unordered(cell, [(ci, plates) for ci in range(len(CELLS))]):
            res[ci] = (h, n)
    out = dict(script='sims/power.py', settings=dict(control_rate=MU0, seed=SEED, threshold='one-sided p < 0.05'), cells={})
    for ci, ((c, t), r, o) in enumerate(CELLS):
        h, n = res[ci]; f = h / n
        out['cells'][f'{c}+{t}|{r}|{o}'] = dict(power=f, se=math.sqrt(f * (1 - f) / n), plates=n)
    out['seconds'] = round(time.time() - t0, 1)
    (ROOT / 'results' / 'power.json').write_text(json.dumps(out, indent=1)); print({k: round(v['power'], 3) for k, v in out['cells'].items()})
