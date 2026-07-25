"""Shared timepoint matching (audit S2).

Combo widgets format timepoints with ``:.4g`` (~4 significant figures), so a
fractional timepoint like 2/3 h ≈ 0.6667 can be ~3e-5 off the original float
once parsed back from text. ``fold_change`` already matched with a *relative*
tolerance for exactly this reason, but the rest of the pipeline (Statistics,
per-cell aggregation, scatter gating, image lookup) still used an absolute
``1e-6`` — so a fractional timepoint matched in a fold-change lookup but
returned "no data" everywhere else. This module is the one relative matcher
they all share.

The ``max(1.0, …)`` floor keeps the absolute tolerance at ≥ 1e-3 h (~3.6 s), far
below any realistic timepoint spacing, so distinct timepoints never merge.
"""
from __future__ import annotations

import numpy as np

TP_REL_TOL = 1e-3


def tp_close(a: float, b: float) -> bool:
    """True if two scalar timepoints match within the relative tolerance."""
    a = float(a)
    b = float(b)
    return abs(a - b) <= TP_REL_TOL * max(1.0, abs(a), abs(b))


def tp_close_mask(values, target: float) -> np.ndarray:
    """Boolean mask of which ``values`` match ``target`` within tolerance."""
    arr = np.asarray(values, dtype=float)
    tgt = float(target)
    tol = TP_REL_TOL * np.maximum(1.0, np.maximum(np.abs(arr), abs(tgt)))
    return np.abs(arr - tgt) <= tol
