"""What the rare-responder design cannot see, measured on the data: a cutoff on a tie, and unequal seeding.

Two conditions make the exceedance test's answer mean less than it says, and neither shows in its p:

* **A cutoff on a tie.** The control cutoff is a percentile of the control pool. When a floored metric (a fold
  change of 0, a count of 0) holds more control nanowells at one value than the tail share, the percentile lands
  on that value, and a nanowell must be *strictly* past it to count. Under *Responders lower* with a few dead
  control nanowells, nothing can be below 0, so every count is 0 and the verdict would read "not detected" for a
  test that could never have counted anything. :func:`cutoff_on_tie` says so; ``rare_report_compute.interpret``
  then answers "cannot be decided" rather than "not detected".
* **Unequal seeding.** The page tests its Metric with no grouping by starting cells. A nanowell seeded with one
  cell has a far wider fold-change distribution than one seeded with four, so a treatment loaded more sparsely
  than its control has more nanowells in the top tail by chance alone — measured: control seeded at 3.5 cells,
  treatment at 1.2, the same growth in both, read as "rare responders detected" at p 0.002–0.03 on five seeds,
  and 0.51–0.92 once stratified. :func:`loading_differs` compares the starting-cell classes of the two sides and
  says so beside the verdict when they differ; it does not change the verdict.

Qt-free and pure.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, fisher_exact

#: Starting-cell classes compared between control and treatment: 1, 2, 3, and 4 or more.
LOADING_CLASSES = (1, 2, 3, 4)
#: The chi-square p below which the two sides' seeding is called different.
LOADING_ALPHA = 0.01
#: The one-sided Fisher p below which the treatment holds more nanowells on a tied cutoff than the control.
TIE_ALPHA = 0.01


def cutoff_on_tie(pooled: dict, treated: dict, thresholds: dict, percentile: float, fixed: float | None) -> str:
    """A sentence when a stratum's cutoff sits on a tie that hides the treatment's nanowells; ``""`` otherwise.

    The tail share is ``1 - percentile / 100``. The cutoff is on a tie when two or more control values sit exactly on
    it and fewer than half the tail share of the control pool is strictly past it. That alone is harmless — a
    whole-number fold change ties at its top and the treatment can still pass it — so it is reported only when the
    *treatment* (*treated*, pooled the same way) holds significantly more of its nanowells at that value than the
    control does (one-sided Fisher exact, p < :data:`TIE_ALPHA`; nanowells pooled, as a trigger for a caveat, not a
    test of the hypothesis): those are the nanowells the test cannot count. A *fixed* threshold is the user's own and
    is never judged."""

    if fixed is not None:
        return ""
    tail = 1.0 - percentile / 100.0
    for s, t in thresholds.items():
        v = np.asarray(pooled.get(s, ()), dtype=float)
        w = np.asarray(treated.get(s, ()), dtype=float)
        if len(v) == 0 or len(w) == 0:
            continue
        on = int(np.sum(v == t))
        if on < 2 or float(np.mean(v > t)) >= tail / 2.0:
            continue
        at = int(np.sum(w == t))
        p = float(fisher_exact([[at, len(w) - at], [on, len(v) - on]], alternative="greater")[1])
        if not (math.isfinite(p) and p < TIE_ALPHA):
            continue
        return (f"The control cutoff sits on a tie: {on} of {len(v)} control nanowells ({on / len(v):.0%}) share its "
                f"value, more than the {tail:.0%} tail it marks, and {float(np.mean(w == t)):.0%} of the treatment's "
                "nanowells sit on it too. A nanowell has to be strictly past that value to count, so the test cannot "
                "see them.")
    return ""


def loading_differs(kept: pd.DataFrame, control_keys, treated_keys) -> str:
    """A sentence when the two sides were seeded differently (chi-square on the starting-cell classes); ``""``
    when they were not, or when it cannot be measured."""
    n0 = pd.to_numeric(kept.get("n0"), errors="coerce") if "n0" in kept else None
    if n0 is None:
        return ""
    cls = np.minimum(n0.to_numpy(dtype=float), LOADING_CLASSES[-1])
    keys = kept["well_key"].to_numpy()
    sides = []
    for side in (set(control_keys), set(treated_keys)):
        mask = np.isin(keys, list(side)) & np.isfinite(cls)
        sides.append([int(np.sum(cls[mask] == c)) for c in LOADING_CLASSES])
    table = np.asarray(sides)
    table = table[:, table.sum(axis=0) > 0]
    if table.shape[1] < 2 or (table.sum(axis=1) == 0).any():
        return ""
    p = float(chi2_contingency(table)[1])
    if not (math.isfinite(p) and p < LOADING_ALPHA):
        return ""
    mean = [sum(c * k for c, k in zip(LOADING_CLASSES, row)) / sum(row) for row in sides]
    return (f"Control and treatment nanowells started with different numbers of cells (mean {mean[0]:.1f} against "
            f"{mean[1]:.1f}, chi-square p = {p:.2g}). Nanowells seeded with fewer cells reach extreme fold changes "
            "more easily, so a difference in seeding alone can look like rare responders.")

