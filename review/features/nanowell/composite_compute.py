"""The window's nanowells packed into a **composite**: an artificial hex array of tiles, one per nanowell, grouped
into one patch per source well and placed by rank inside each patch.

It is not a well. Its positions say nothing about where a nanowell sits in its well: rank 1 of each well is at its
patch's centre and the rest spiral outward in rank order (:func:`spiral`). Patches are laid out left to right in
rows, in the order of each well's best-ranked member. Every tile keeps its real nanowell (``source``), which is what
a hover names and a click selects. Positions are in **pitch units**: neighbouring tiles' centres are 1 apart.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from review.features.nanowell.views_compute import lattice_xy

#: The most tiles one composite page holds: the hexagonal number of a radius-5 patch.
COMPOSITE_PAGE = 91
#: Axial steps around a ring, in walking order (east, then counter-clockwise on screen).
_DIRS = ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))


def spiral(n: int) -> list:
    """The first *n* axial addresses ``(q, r)`` from the centre outward: ``(0, 0)``, then ring 1's six, ring 2's
    twelve, … each ring walked from ``(0, -k)`` eastward and round counter-clockwise."""
    out = [(0, 0)][:n]
    k = 1
    while len(out) < n:
        q, r = 0, -k
        for dq, dr in _DIRS:
            for _ in range(k):
                if len(out) < n:
                    out.append((q, r))
                q, r = q + dq, r + dr
        k += 1
    return out


def radius_for(n: int) -> int:
    """The smallest ring count whose hexagon holds *n* tiles: 1 + 3k(k + 1) >= n."""
    k = 0
    while 1 + 3 * k * (k + 1) < n:
        k += 1
    return k


@dataclass(frozen=True)
class CompositeTile:
    x: float
    y: float                    # pitch units, y down
    source: tuple               # (well, q, r): the real nanowell
    rank: int                   # its rank in the window (1 is first)
    patch: int                  # index into Composite.patches


@dataclass
class Composite:
    tiles: list = field(default_factory=list)
    patches: list = field(default_factory=list)      # (well, label x, label y), label above the patch
    n_wells: int = 0

    def title(self, n_total: int) -> str:
        return (f"Composite of {len(self.tiles):,} of {n_total:,} nanowells from {self.n_wells} "
                f"well{'s' if self.n_wells != 1 else ''} — not a real array; places assigned by rank within each well")


def composite(rows) -> Composite:
    """The composite of *rows* (``ExtremeRow``, ranked), one patch per well."""
    by_well: dict = {}
    for row in rows:
        by_well.setdefault(row.site[0], []).append(row)
    wells = sorted(by_well, key=lambda w: min(r.rank for r in by_well[w]))
    out = Composite(n_wells=len(wells))
    if not wells:
        return out
    radius = max(radius_for(len(v)) for v in by_well.values())
    step = 2 * radius + 2                               # one empty ring between neighbouring patches
    per_row = max(1, math.ceil(math.sqrt(len(wells))))
    for p, well in enumerate(wells):
        cx, cy = (p % per_row) * step, (p // per_row) * step
        members = sorted(by_well[well], key=lambda r: r.rank)
        slots = spiral(len(members))
        xs, ys = lattice_xy([q for q, _ in slots], [r for _, r in slots], 1.0)
        for row, x, y in zip(members, xs, ys):
            out.tiles.append(CompositeTile(cx + float(x), cy + float(y), tuple(row.site), row.rank, p))
        out.patches.append((well, float(cx), float(cy) - radius - 0.9))
    return out


def page(rows, index: int, size: int = COMPOSITE_PAGE) -> list:
    """Page *index* of *rows* (clamped into range): the composite is drawn one page of ranks at a time."""
    pages = max(1, math.ceil(len(rows) / size))
    index = min(max(index, 0), pages - 1)
    return rows[index * size:(index + 1) * size]
