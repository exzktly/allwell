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
from collections.abc import Iterable

import numpy as np

from util.timepoint import parse_hours as _parse_hours, sort_key as tp_sort_key


DEFAULT_CELLS_PER_IMAGE_CAP = 800


def parse_tp_hours(tp: str) -> float | None:
    """Convert a timepoint string to hours; ``None`` when unparseable.

    Delegates to :func:`util.timepoint.parse_hours`. This used to be a looser
    parser that scavenged the first digit run from anywhere in the token, which
    made it read ``"5%"`` as 5 hours and disagree with every other parser about
    ``"a1b2"``. It only fed the sort in :func:`pick_endpoint_timepoints`, so
    nothing broke — but two answers to "what hour is this token" is one too
    many, and the strict grammar is the one that produces canonical keys.
    """
    return _parse_hours(tp)


def pick_endpoint_timepoints(tps: Iterable[str]) -> list[str]:
    """Return up to three timepoints from *tps*: first, middle, last.

    Sorted chronologically using :func:`parse_tp_hours`; duplicates
    dropped so a one- or two-timepoint dataset still works.
    """
    sorted_tps = sorted(
        {str(t).strip() for t in tps if str(t).strip()},
        key=tp_sort_key,
    )
    if not sorted_tps:
        return []
    if len(sorted_tps) <= 2:
        return list(sorted_tps)
    mid = sorted_tps[len(sorted_tps) // 2]
    picked: list[str] = []
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
) -> tuple[list[float], list[float]]:
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
