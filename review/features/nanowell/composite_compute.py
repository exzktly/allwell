"""The window's nanowells packed into a **composite**: an artificial hex array of tiles, one per nanowell, grouped
into one patch per source well and placed in order inside each patch.

It is not a well. Its positions say nothing about where a nanowell sits in its well: each patch is a hexagon filled
**row by row from its top left** (:func:`reading_order`), in the order chosen — the window's rank by default, or a
metric's value at the latest timepoint shown, ascending or descending (the pop-out's **Sort**), over every member
before the page is cut (:func:`ordered`). Patches are laid out left to right in rows, in the order of each well's first
member. Every tile keeps its real nanowell (``source``), which is what
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


def reading_order(n: int) -> list:
    """The first *n* axial addresses ``(q, r)`` of the smallest hexagon that holds them, in reading order: the top
    row left to right, then the next row down (``y`` runs down the composite)."""
    k = radius_for(n)
    cells = [(q, r) for q in range(-k, k + 1) for r in range(-k, k + 1) if max(abs(q), abs(r), abs(q + r)) <= k]
    xs, ys = lattice_xy([q for q, _ in cells], [r for _, r in cells], 1.0)
    ordered = sorted(zip(cells, xs, ys), key=lambda c: (round(float(c[2]), 6), float(c[1])))
    return [cell for cell, _x, _y in ordered][:n]


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

    #: What the tiles are placed by, for the title: "rank" or a metric's sentence.
    placed_by: str = "rank"

    def title(self, n_total: int) -> str:
        return (f"Composite of {len(self.tiles):,} of {n_total:,} nanowells from {self.n_wells} "
                f"well{'s' if self.n_wells != 1 else ''} — not a real array; placed by {self.placed_by} within each "
                "well, from the top left")


def sort_key(values: dict | None, descending: bool):
    """The order of the composite's members: by *values* (``site -> number``), largest first when *descending*,
    a member without a finite value last; ties, and every member when *values* is ``None``, by the window's rank."""
    def key(row):
        if values is None:
            return (0, 0.0, row.rank)
        v = values.get(tuple(row.site))
        if v is None or not math.isfinite(v):
            return (1, 0.0, row.rank)
        return (0, -v if descending else v, row.rank)

    return key


def composite(rows, *, values: dict | None = None, descending: bool = True, placed_by: str = "rank") -> Composite:
    """The composite of *rows* (``ExtremeRow``, ranked), one patch per well, each filled in reading order by
    :func:`sort_key` — the window's rank unless *values* are given."""
    order = sort_key(values, descending)
    by_well: dict = {}
    for row in sorted(rows, key=order):
        by_well.setdefault(row.site[0], []).append(row)
    wells = list(by_well)                                # in the order of each well's first member
    out = Composite(n_wells=len(wells), placed_by=placed_by)
    if not wells:
        return out
    radius = max(radius_for(len(v)) for v in by_well.values())
    step = 2 * radius + 2                               # one empty ring between neighbouring patches
    per_row = max(1, math.ceil(math.sqrt(len(wells))))
    for p, well in enumerate(wells):
        cx, cy = (p % per_row) * step, (p // per_row) * step
        members = by_well[well]
        slots = reading_order(len(members))
        xs, ys = lattice_xy([q for q, _ in slots], [r for _, r in slots], 1.0)
        for row, x, y in zip(members, xs, ys):
            out.tiles.append(CompositeTile(cx + float(x), cy + float(y), tuple(row.site), row.rank, p))
        out.patches.append((well, float(cx), float(cy) - radius - 0.9))
    return out


def ordered(rows, values: dict | None, descending: bool) -> list:
    """*rows* in the order **Sort** asks for (:func:`sort_key`), before they are cut into pages: a page is then the
    next run of that order, not a run of ranks sorted among themselves."""
    return sorted(rows, key=sort_key(values, descending))


def page(rows, index: int, size: int = COMPOSITE_PAGE) -> list:
    """Page *index* of *rows* (clamped into range): the composite is drawn one page of ranks at a time."""
    pages = max(1, math.ceil(len(rows) / size))
    index = min(max(index, 0), pages - 1)
    return rows[index * size:(index + 1) * size]
