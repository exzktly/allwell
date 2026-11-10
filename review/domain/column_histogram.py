"""Vertical histograms drawn in place of a beeswarm or violin: one per column, mirrored about the column's x.

Qt-free and matplotlib-free: this computes, the renderers draw. Used by Bar Plots (the tab and Batch Export), the
Nanowells page's Distribution view (and Batch Export's Nanowells mode) and the Distribution tab, so a histogram means
the same thing wherever it is offered.

**What makes the columns comparable.** Every sample's bars sum to probability 1 (a well of 90 cells and one of 2,500
read on the same scale), every sample in a figure is binned on one set of edges (so the bins line up across
columns), and every bar's half-width is its probability over the figure's largest bin probability, times the slot's
half-width (so the widest bar in the figure fills its slot and every other bar is measured against it). A violin
scales each column to its own peak; a histogram here does not, which is the point of choosing it.

**Bins.** The user's bin count (:data:`BINS_MIN`–:data:`BINS_MAX`, default :data:`BINS_DEFAULT`) splits the pooled
range evenly — or, on a log value axis, evenly in log space. For an integer metric (a count) the edges sit on half
integers, so no bin splits a value: one bin per value while the range fits in the count asked for, else bins of
``ceil(range / count)`` values, never more bins than asked for.

**What is not drawn, and says so.** Non-finite values are dropped before anything is counted. A sample with fewer
than :data:`MIN_VALUES` finite values gets no histogram (``probs`` is ``None``) and does not set the width scale —
one cell would otherwise be probability 1 and squash every real column to a sliver; the renderer draws its values as
dots. On a log axis a value ≤ 0 has no place, so it stays in the sample's denominator, is counted in ``off_axis``, and
the sample's bars sum to less than 1; :meth:`ColumnHistograms.note` states how many.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

BINS_MIN, BINS_MAX, BINS_DEFAULT = 2, 200, 30
#: The fewest finite values a sample needs for a histogram; the violin's own threshold.
MIN_VALUES = 3


def clamp_bins(value) -> int:
    """*value* as a whole bin count inside ``[BINS_MIN, BINS_MAX]``; anything that is not a finite number (a bool, a
    string, NaN) is :data:`BINS_DEFAULT`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return BINS_DEFAULT
    return int(min(BINS_MAX, max(BINS_MIN, round(value))))


def _finite(sample) -> np.ndarray:
    arr = np.asarray(sample, dtype=float).ravel()
    return arr[np.isfinite(arr)]


def shared_edges(samples, bins, *, integer: bool = False, log: bool = False) -> np.ndarray:
    """One set of bin edges for every sample in *samples*, from their pooled finite values (positive values only when
    *log*). An empty array when nothing can be binned."""
    bins = clamp_bins(bins)
    parts = [_finite(s) for s in samples]
    pooled = np.concatenate(parts) if parts else np.empty(0)
    if log:
        pooled = pooled[pooled > 0]
    if pooled.size == 0:
        return np.empty(0)
    lo, hi = float(pooled.min()), float(pooled.max())
    if integer and not log:
        lo_i, hi_i = int(round(lo)), int(round(hi))
        span = hi_i - lo_i + 1
        step = max(1, math.ceil(span / bins))
        k = math.ceil(span / step)
        return lo_i - 0.5 + step * np.arange(k + 1, dtype=float)
    if lo == hi:
        if log:
            return np.array([lo / 1.05, lo * 1.05])
        pad = 0.05 * max(abs(lo), 1e-6)
        return np.array([lo - pad, hi + pad])
    if log:
        return np.geomspace(lo, hi, bins + 1)
    return np.histogram_bin_edges(pooled, bins=bins)


@dataclass(frozen=True)
class ColumnHistograms:
    """The histograms of one figure's columns, on shared edges and one width scale."""

    edges: np.ndarray
    #: Per sample: the probability of each bin (sums to 1, or less on a log axis with values ≤ 0), or ``None`` for a
    #: sample with fewer than :data:`MIN_VALUES` finite values.
    probs: tuple
    #: Per sample: its finite values — the denominator of its probabilities.
    n: tuple
    #: Per sample: values ≤ 0 left off a log axis (still in the denominator).
    off_axis: tuple
    #: The largest bin probability over the drawn samples; NaN when none is drawn.
    p_max: float
    integer: bool = False
    log: bool = False

    @property
    def bins(self) -> int:
        return max(len(self.edges) - 1, 0)

    @property
    def one_per_value(self) -> bool:
        return self.integer and not self.log and self.bins > 0 and bool(np.allclose(np.diff(self.edges), 1.0))

    def drawn(self, i: int) -> bool:
        return self.probs[i] is not None and math.isfinite(self.p_max) and self.p_max > 0

    def half_widths(self, i: int, half: float) -> np.ndarray:
        """Each bin's half-width for sample *i* in a slot of half-width *half*: its probability over the figure's
        largest, times *half*."""
        if not self.drawn(i):
            return np.zeros(self.bins)
        return np.asarray(self.probs[i], dtype=float) / self.p_max * float(half)

    def bars(self, i: int, x: float, half: float) -> tuple:
        """``(bottoms, heights, lefts, widths)`` of sample *i*'s bars mirrored about *x*, bins of probability 0 left
        out — the arguments ``Axes.barh(bottoms, widths, height=heights, left=lefts, align="edge")`` takes."""
        w = self.half_widths(i, half)
        keep = w > 0
        bottoms = self.edges[:-1][keep]
        heights = np.diff(self.edges)[keep]
        return bottoms, heights, float(x) - w[keep], 2.0 * w[keep]

    def note(self, unit: str = "column") -> str:
        """``"30 bins · widest bar = 23% of a column"``: what a bar's width means, for the figure. ``""`` when nothing
        is drawn. On a log axis, how many values ≤ 0 could not be placed."""
        if not any(self.drawn(i) for i in range(len(self.probs))):
            return ""
        count = f"{self.bins} bin{'s' if self.bins != 1 else ''}"
        if self.one_per_value:
            count += " (one per value)"
        elif self.log:
            count += " (log-spaced)"
        text = f"{count} · widest bar = {_percent(self.p_max)} of a {unit}"
        off = sum(self.off_axis)
        if off:
            text += f" · {off:,} value{'s' if off != 1 else ''} ≤ 0 not on the log axis"
        return text


def _percent(p: float) -> str:
    if p >= 0.01:
        return f"{p:.0%}"
    return f"{p * 100:.2g}%"


def column_histograms(samples, bins, *, integer: bool = False, log: bool = False) -> ColumnHistograms:
    """The histograms of *samples* (one sequence of values per column) at *bins* bins, sharing edges and one width
    scale. *integer* bins on whole numbers (ignored when *log*); *log* spaces the bins in log space."""
    samples = [_finite(s) for s in samples]
    integer = bool(integer) and not log
    edges = shared_edges(samples, bins, integer=integer, log=log)
    counts = [np.histogram(s[s > 0] if log else s, edges)[0] if edges.size >= 2 else None for s in samples]
    offs = [int(np.count_nonzero(s <= 0)) if log else 0 for s in samples]
    return histograms_from_counts(edges, counts, [int(s.size) for s in samples], off_axis=offs, integer=integer,
                                  log=log)


def histograms_from_counts(edges, counts, n, *, off_axis=None, integer: bool = False,
                           log: bool = False) -> ColumnHistograms:
    """The histograms of columns already counted on *edges*: *counts* per column (bin counts, or ``None``), *n* each
    column's finite values (the denominator). For a caller that counts every value of a sampled column itself, as
    the Distribution tab does; :func:`column_histograms` is this over values."""
    edges = np.asarray(edges, dtype=float)
    offs = tuple(off_axis) if off_axis is not None else (0,) * len(counts)
    probs = []
    for c, k in zip(counts, n):
        if c is None or k < MIN_VALUES or edges.size < 2:
            probs.append(None)
            continue
        probs.append(np.asarray(c, dtype=float) / k)
    drawn = [p for p in probs if p is not None and p.size]
    p_max = float(max(p.max() for p in drawn)) if drawn else float("nan")
    return ColumnHistograms(edges, tuple(probs), tuple(int(k) for k in n), offs, p_max, integer, log)
