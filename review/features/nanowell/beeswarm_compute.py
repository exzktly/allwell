"""Where each dot of a beeswarm goes: one dot per nanowell, no two overlapping (tracker V3b, decision U1).

Pure geometry in data units, so the renderer only has to say how big a dot is on the axes it draws into.

**A continuous metric keeps its exact value.** Dots are taken in order of value; each takes the free slot
nearest the swarm's centre line, alternating sides, where a slot is free once the last dot in it is a dot's
height below. A dot's y is its value, and no two dots overlap.

**An integer metric is a packed band** (decision U1). A cell count or an occupancy class ties hundreds of
nanowells on one value — the empty nanowells alone are a third of a well — and an exact-value swarm cannot fit
them side by side. Each value's dots are packed into a block within ``± band`` of the value (0.4 by default),
filling the row on the value first and then the rows above and below it, so a value with a few dots is drawn
on it and the block reads as that value. When a dot is taller than the band (many panels, a small figure)
there is no room for rows, and the integer values are laid out exactly instead. ``exact=True`` keeps exact
rows.

**A dot that does not fit is counted, never dropped silently.** A row is as wide as the swarm's slot; a tie
too large for it overflows, and :class:`SwarmLayout` reports how many dots were not drawn — per value for a
packed band ("+N more at 0"), and once for an exact-value swarm with the range of the hidden values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

#: Half the height of a packed band, in the metric's units (decision U1).
BAND = 0.4


@dataclass
class SwarmLayout:
    """Offsets for the dots drawn, and what did not fit.

    ``index`` are positions into the values given (the dots drawn, in the order of ``x`` and ``y``);
    ``overflow`` maps a value (a row's value, or a band's integer) to the number of its dots not drawn.
    """
    index: np.ndarray
    x: np.ndarray
    y: np.ndarray
    overflow: dict = field(default_factory=dict)
    #: For an exact-value swarm, the lowest and highest hidden value (its one ``overflow`` entry is at the lowest).
    overflow_range: tuple | None = None

    @property
    def n_hidden(self) -> int:
        return int(sum(self.overflow.values()))


def is_integer_metric(values) -> bool:
    """True when every finite value is a whole number (counts, occupancy classes)."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return bool(len(finite)) and bool(np.all(finite == np.round(finite)))


def _slots(half_width: float, dot_w: float) -> list[float]:
    """Centre-out x offsets that fit in ``[-half_width, half_width]``: 0, +w, -w, +2w, -2w, …"""
    n_side = max(int(math.floor((half_width - dot_w / 2) / dot_w + 1e-9)), 0)
    out = [0.0]
    for k in range(1, n_side + 1):
        out += [k * dot_w, -k * dot_w]
    return out


def _band_offsets(dot_h: float, band: float) -> list[float]:
    """Centre-out row offsets inside ``± band``: 0, +h, −h, +2h, −2h, … — so a value's first dots sit on it.

    Empty when a dot is taller than the band, which the caller lays out exactly instead.
    """
    if dot_h / 2 > band:
        return []
    n_side = int(math.floor((band - dot_h / 2) / dot_h + 1e-9))
    out = [0.0]
    for k in range(1, n_side + 1):
        out += [k * dot_h, -k * dot_h]
    return out


def swarm_layout(values, *, half_width: float, dot_w: float, dot_h: float, integer: bool | None = None,
                 band: float = BAND, exact: bool = False) -> SwarmLayout:
    """Lay out one swarm. NaN values are not dots; the caller counts them as exclusions.

    *half_width* is half the slot's width and *dot_w*, *dot_h* a dot's size, all in data units.
    *integer* defaults to :func:`is_integer_metric` of the values.
    """
    values = np.asarray(values, dtype=float)
    finite = np.flatnonzero(np.isfinite(values))
    if integer is None:
        integer = is_integer_metric(values)
    slots = _slots(half_width, dot_w)
    order = finite[np.argsort(values[finite], kind="stable")]
    xs, ys, kept, overflow = [], [], [], {}
    offsets = _band_offsets(dot_h, band) if integer and not exact else []
    if offsets:
        # Each value's dots fill the centre row first, across the slot, then the rows above and below it in
        # turn — a value with few dots is drawn on it, and the block never reaches the next value's.
        capacity = len(slots) * len(offsets)
        for value in np.unique(values[order]):
            members = order[values[order] == value]
            for k, idx in enumerate(members[:capacity]):
                xs.append(slots[k % len(slots)])
                ys.append(value + offsets[k // len(slots)])
                kept.append(idx)
            if len(members) > capacity:
                overflow[float(value)] = int(len(members) - capacity)
        return SwarmLayout(np.asarray(kept, dtype=int), np.asarray(xs, dtype=float), np.asarray(ys, dtype=float),
                           overflow)
    # Exact values (a continuous metric, or an integer one whose dots are taller than the band): each dot, in
    # order of value, takes the free slot nearest the centre line. A slot is free once the last dot placed in it
    # is at least one dot height below, so no two dots overlap, across values as within one.
    last = [-math.inf] * len(slots)
    hidden = []
    for idx in order:
        y = values[idx]
        for k, slot_x in enumerate(slots):
            if y - last[k] >= dot_h - 1e-12:
                last[k] = y
                xs.append(slot_x)
                ys.append(y)
                kept.append(idx)
                break
        else:
            hidden.append(y)
    layout = SwarmLayout(np.asarray(kept, dtype=int), np.asarray(xs, dtype=float), np.asarray(ys, dtype=float), {})
    if hidden:
        # One count for the swarm, not one per value: a crowded continuous swarm hides hundreds of distinct
        # values, and a label each would cover the figure. The range says where they were.
        lo, hi = float(min(hidden)), float(max(hidden))
        layout.overflow = {lo: len(hidden)}
        layout.overflow_range = (lo, hi)
    return layout
