"""The line plots' marks for timepoints whose nanowell identity is not confirmed (pure matplotlib).

Trajectories and the Distribution view over time join each nanowell across timepoints by address. A timepoint the
identity check (``pipeline/nanowell_identity.py``) could not confirm is drawn where it is, but as what it is: a
hatched band at its place on the time axis with the verdict written in it, and every line segment into or out of
it dashed, so the join through it reads as unverified at a glance (``identity_compute.timepoint_marks``). A
contradicted timepoint has no values (``series_compute`` leaves it out), so its band explains the gap.
"""
from __future__ import annotations

import numpy as np

from pipeline.nanowell_identity import CONTRADICTED, INCONCLUSIVE, NOT_CHECKED
from review.features.nanowell.identity_compute import MARK_WORDS

#: The hatch each verdict's band is drawn with.
HATCH = {INCONCLUSIVE: "////", CONTRADICTED: "xxxx", NOT_CHECKED: "...."}


def mark_timepoints(ax, xs, marks: dict, colours: dict, *, width: float, fontsize: float, ground: str) -> list:
    """A hatched band of *width* centred on each ``x -> verdict`` of *marks*, its verdict written just above the axes
    on the figure's *ground* so the word stays legible over the hatching; returns the bands. *colours* maps a verdict
    to its ink."""
    from matplotlib.transforms import blended_transform_factory

    top = blended_transform_factory(ax.transData, ax.transAxes)
    bands = []
    for x in xs:
        verdict = marks.get(x)
        if verdict not in HATCH:
            continue
        band = ax.axvspan(x - width / 2, x + width / 2, facecolor="none", edgecolor=colours[verdict],
                          hatch=HATCH[verdict], linewidth=0, alpha=0.55, zorder=0.6, gid="nw-identity-band")
        band.aw_verdict = verdict
        bands.append(band)
        ax.text(x, 0.985, MARK_WORDS[verdict], transform=top, ha="center", va="top", fontsize=fontsize,
                color=colours[verdict], fontweight="bold", zorder=5, gid="nw-identity-mark", clip_on=False,
                bbox={"facecolor": ground, "edgecolor": colours[verdict], "linewidth": 0.6, "pad": 1.5,
                      "boxstyle": "round,pad=0.25"})
    return bands


def split_segments(xs, ys, dashed_xs) -> tuple[list, list]:
    """``(solid, dashed)`` two-point segments of the polyline *xs*, *ys*, skipping a gap (a NaN at either end): a
    segment is dashed when either end is in *dashed_xs*."""
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    solid, dashed = [], []
    for i in range(len(xs) - 1):
        if not (np.isfinite(ys[i]) and np.isfinite(ys[i + 1])):
            continue
        segment = [(xs[i], ys[i]), (xs[i + 1], ys[i + 1])]
        (dashed if xs[i] in dashed_xs or xs[i + 1] in dashed_xs else solid).append(segment)
    return solid, dashed
