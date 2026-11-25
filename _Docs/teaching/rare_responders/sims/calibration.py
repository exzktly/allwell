"""Slide 31's bars: how often the exceedance test calls a molecule that does nothing, by design.

Counts are drawn directly from the beta-binomial fitted to the example plate without a gap (its shared rate π and r,
read from ../results/example_plate.json). For each design, the share of plates with z above the one-sided 5 % and
1 % points, and the z that a true 5 % would need. Writes ../results/calibration.json.
Usage: python calibration.py [plates_per_design] [processes]
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
EX = json.loads((ROOT / 'results' / 'example_plate.json').read_text())['A']
MU, R = EX['mu0'], EX['r0']
DESIGNS = ((4, 4), (6, 4), (8, 4), (8, 8))
SEED = 20261127


def chunk(args):
    di, ci, plates = args
    c, t = DESIGNS[di]; tr = np.array([0] * c + [1] * t)
    rng = np.random.default_rng([SEED, di, ci])
    return di, [zstat(draw(rng, MU, R, c + t), tr) for _ in range(plates)]


if __name__ == '__main__':
    plates = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    t0 = time.time(); zs = {di: [] for di in range(len(DESIGNS))}
    with Pool(procs) as pool:
        for di, z in pool.imap_unordered(chunk, [(di, ci, plates // procs) for di in range(len(DESIGNS)) for ci in range(procs)]):
            zs[di] += z
    out = dict(script='sims/calibration.py', settings=dict(rate=MU, r=R, nanowells_per_well=1000, seed=SEED, source='example_plate.json, molecule A, fit without a gap'), designs={})
    for di, (c, t) in enumerate(DESIGNS):
        z = np.array(zs[di]); n = len(z)
        p05 = float((z > 1.6448536269514722).mean()); p01 = float((z > 2.3263478740408408).mean())
        out['designs'][f'{c}+{t}'] = dict(plates=n, p05=p05, p05_se=math.sqrt(p05 * (1 - p05) / n), p01=p01, p01_se=math.sqrt(p01 * (1 - p01) / n),
                                         tcrit=float(np.quantile(z, 0.95)), sd=float(z.std()))
    out['seconds'] = round(time.time() - t0, 1)
    (ROOT / 'results' / 'calibration.json').write_text(json.dumps(out, indent=1)); print(json.dumps(out['designs'], indent=1))
