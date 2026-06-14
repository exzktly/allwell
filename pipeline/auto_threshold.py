"""Shared auto-threshold helpers used by both the pipeline and the GUI.

The pipeline (`process_microscopy.py`) and the Cell Gating tab
(`well_viewer/auto_threshold.py`) used to maintain parallel copies of
the same timepoint parser, endpoint picker, and per-image sampler.
The copies had already diverged (different sort orders, different
parser semantics) so the two paths produced different default
`thresh_frac_on` values on the same dataset.

This module is the single source of truth. It depends only on
`numpy` and `random` — no Qt, no `well_viewer.*` — so the pipeline
(which must run on headless hosts where `well_viewer` may not be
installed) can import it freely. See ARCHITECTURE.md §6.2 for the
pipeline-must-be-standalone contract.
"""

from __future__ import annotations

import random
import re
from typing import Iterable, List, Optional, Tuple

import numpy as np


DEFAULT_CELLS_PER_IMAGE_CAP = 800


def parse_tp_hours(tp: str) -> Optional[float]:
    """Convert a timepoint string to hours.

    Recognises:
      - plain numerics (``"24"``, ``"1.5"``) — interpreted as hours
      - ``DDdHHhMMm`` / ``2d`` / ``48h`` / ``30m`` / ``45s``
      - any string containing digits — the first integer run is used
        as a fallback ordinal

    Returns ``None`` when nothing parseable is found.
    """
    s = str(tp or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    total = 0.0
    matched = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", s, flags=re.IGNORECASE):
        matched = True
        v = float(value)
        u = unit.lower()
        if u == "d":
            total += v * 24.0
        elif u == "h":
            total += v
        elif u == "m":
            total += v / 60.0
        elif u == "s":
            total += v / 3600.0
    if matched:
        return total
    m = re.search(r"\d+", s)
    if m:
        try:
            return float(int(m.group(0)))
        except ValueError:
            return None
    return None


def pick_endpoint_timepoints(tps: Iterable[str]) -> List[str]:
    """Return up to three timepoints from *tps*: first, middle, last.

    Sorted chronologically using :func:`parse_tp_hours`; duplicates
    dropped so a one- or two-timepoint dataset still works.
    """
    sorted_tps = sorted(
        {str(t).strip() for t in tps if str(t).strip()},
        key=lambda t: (parse_tp_hours(t) is None, parse_tp_hours(t) or 0.0, t),
    )
    if not sorted_tps:
        return []
    if len(sorted_tps) <= 2:
        return list(sorted_tps)
    mid = sorted_tps[len(sorted_tps) // 2]
    picked: List[str] = []
    for tp in (sorted_tps[0], mid, sorted_tps[-1]):
        if tp not in picked:
            picked.append(tp)
    return picked


def sample_cell_and_bg(
    labels: np.ndarray,
    fluor: np.ndarray,
    *,
    cap: int,
    rng: random.Random,
) -> Tuple[List[float], List[float]]:
    """Return ``(cell_means, bg_pixels)`` sampled from one image.

    For every distinct cell label in ``labels`` (excluding background
    ``0``), one mean fluorescence intensity is added to ``cell_means``
    plus one random outside-cell pixel value to ``bg_pixels``. Both
    lists are capped to ``cap`` entries to keep memory bounded on
    dense fields.
    """
    if labels.shape != fluor.shape:
        return [], []
    flat_labels = labels.ravel().astype(np.int64, copy=False)
    flat_fluor = fluor.ravel().astype(np.float64, copy=False)
    nonzero = flat_labels > 0
    if not nonzero.any():
        return [], []
    bg_indices = np.flatnonzero(~nonzero)
    if bg_indices.size == 0:
        return [], []

    n_labels = int(flat_labels.max()) + 1
    sums = np.bincount(flat_labels, weights=flat_fluor, minlength=n_labels)
    counts = np.bincount(flat_labels, minlength=n_labels)
    valid = np.arange(1, n_labels)
    valid_counts = counts[1:]
    keep = valid_counts > 0
    valid = valid[keep]
    if valid.size == 0:
        return [], []
    means = (sums[valid] / counts[valid]).astype(np.float64)

    if means.size > cap:
        idx = np.array(rng.sample(range(means.size), cap), dtype=np.int64)
        means = means[idx]

    n = int(means.size)
    bg_pick = rng.choices(bg_indices.tolist(), k=n)
    bg_values = flat_fluor[np.asarray(bg_pick, dtype=np.int64)]
    return means.tolist(), bg_values.tolist()
