"""Where each dot of a beeswarm goes: one dot per nanowell, no two overlapping (tracker V3b, decision U1).

Pure geometry in data units, so the renderer only has to say how big a dot is on the axes it draws into.

**A continuous metric keeps its exact value.** Dots are taken in order of value; each takes the free slot
nearest the swarm's centre line, alternating sides, where a slot is free once the last dot in it is a dot's
height below. A dot's y is its value, and no two dots overlap.

**An integer metric is a packed band** (decision U1). A cell count or an occupancy class ties hundreds of
nanowells on one value — the empty nanowells alone are a third of a well — and an exact-value swarm cannot fit
them side by side. Each value's dots are packed into a block within ``± band`` of the value (0.4 by default),
so the block still reads as that value and the figure states the band. ``exact=True`` keeps exact rows.

**A dot that does not fit is counted, never dropped silently.** A row is as wide as the swarm's slot; a tie
too large for it overflows, and :class:`SwarmLayout` reports how many dots at which value were not drawn so
the figure can say "+N more at 0".
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
    if integer and not exact:
        sub_rows = max(int(math.floor(2 * band / dot_h)), 1)
        offsets = [0.0] if sub_rows == 1 else list(np.linspace(-band + dot_h / 2, band - dot_h / 2, sub_rows))
        capacity = len(slots) * len(offsets)
        for value in np.unique(values[order]):
            members = order[values[order] == value]
            for k, idx in enumerate(members[:capacity]):
                xs.append(slots[k % len(slots)])
                ys.append(value + offsets[k // len(slots)])
                kept.append(idx)
            if len(members) > capacity:
                overflow[float(value)] = int(len(members) - capacity)
        return SwarmLayout(np.asarray(kept, dtype=int), np.asarray(xs), np.asarray(ys), overflow)
    # Exact values: each dot, in order of value, takes the free slot nearest the centre line. A slot is free
    # once the last dot placed in it is at least one dot height below, so no two dots overlap.
    last = [-math.inf] * len(slots)
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
            key = float(np.round(y, 12))
            overflow[key] = overflow.get(key, 0) + 1
    return SwarmLayout(np.asarray(kept, dtype=int), np.asarray(xs, dtype=float), np.asarray(ys, dtype=float),
                       overflow)
