"""The window's hex composite: one patch per well, tiles in order from each patch's top left, pages.

Asked for: the nanowells inside a Distribution window packed into an artificial hex array, grouped by source well
and placed within each in order — the window's rank, or a metric's value ascending or descending (**Sort**) —
filling the patch row by row from its top left. It is not a well — nothing in its layout says where a nanowell sat in its own — so
the tests hold the places to the ranks (the rows are handed over against rank order, so insertion order and rank
disagree), the patches to each well's best rank, and the tiles apart.
"""

from __future__ import annotations

import itertools
import math

from review.features.nanowell import composite_compute as cc
from review.features.nanowell.views_compute import hex_ring
from review.features.nanowell.window_compute import WindowRow


def _row(well, q, rank):
    return WindowRow((well, q, 0), rank, 0.0, {})


def _two_wells():
    """B02 holds ranks 1, 3, 4 and A01 ranks 2, 5: by well name A01 would come first, by best rank B02 does."""
    return ([_row("B02", 5, 1), _row("B02", 6, 3), _row("B02", 7, 4)] + [_row("A01", 1, 2), _row("A01", 2, 5)])


def test_the_spiral_fills_rings_outward_without_repeating():
    for n, radius in ((1, 0), (7, 1), (19, 2), (37, 3), (91, 5)):
        slots = cc.spiral(n)
        rings = [int(hex_ring(q, r)) for q, r in slots]
        assert len(set(slots)) == n and rings == sorted(rings) and max(rings) == cc.radius_for(n) == radius


def test_one_patch_per_well_in_the_order_of_each_well_s_best_rank():
    comp = cc.composite(_two_wells())
    assert [patch[0] for patch in comp.patches] == ["B02", "A01"]
    by_rank = {t.rank: t for t in comp.tiles}
    for well, first in (("B02", 1), ("A01", 2)):                         # each well's best rank at its top left
        mine = [t for t in comp.tiles if t.source[0] == well]
        assert min(mine, key=lambda t: (round(t.y, 9), t.x)) is by_rank[first]
    assert {t.source[0] for t in comp.tiles if t.patch == 0} == {"B02"}


def test_tiles_never_overlap():
    """Two wells of seven fill their patches to the edge, so patches set too close would put tiles on one another."""
    rows = [_row("A01", q, 2 * q + 1) for q in range(7)] + [_row("B02", q, 2 * q + 2) for q in range(7)]
    points = [(t.x, t.y) for t in cc.composite(rows).tiles]
    assert len(points) == 14
    nearest = min(math.dist(a, b) for a, b in itertools.combinations(points, 2))
    assert abs(nearest - 1.0) < 1e-9


def test_rank_not_insertion_order_places_the_tiles():
    forward = cc.composite(sorted(_two_wells(), key=lambda r: r.rank))
    backward = cc.composite(sorted(_two_wells(), key=lambda r: -r.rank))

    def places(comp):
        return {t.rank: (round(t.x, 9), round(t.y, 9), t.source) for t in comp.tiles}

    assert places(forward) == places(backward)


def test_the_title_says_it_is_not_a_well():
    assert cc.composite(_two_wells()).title(5) == (
        "Composite of 5 of 5 nanowells from 2 wells — not a real array; placed by rank within each well, from the "
        "top left")


def test_pages_are_clamped():
    rows = [_row("A01", i, i + 1) for i in range(100)]
    sizes = [len(cc.page(rows, i)) for i in (0, 1, 7, -1)]
    assert sizes == [91, 9, 9, 91]


def _reading(comp, well):
    """*well*'s tiles in the patch's reading order: top row left to right, then down."""
    return [t for t in sorted((t for t in comp.tiles if t.source[0] == well), key=lambda t: (round(t.y, 9), t.x))]


def test_the_reading_order_fills_rows_left_to_right_from_the_top():
    for n in (1, 2, 3, 7, 12, 19, 91):
        slots = cc.reading_order(n)
        from review.features.nanowell.views_compute import lattice_xy

        xs, ys = lattice_xy([q for q, _ in slots], [r for _, r in slots], 1.0)
        places = [(round(float(y), 9), float(x)) for x, y in zip(xs, ys)]
        assert len(set(slots)) == n and places == sorted(places)
        assert max(int(hex_ring(q, r)) for q, r in slots) <= cc.radius_for(n)


def test_rank_order_reads_from_the_top_left():
    rows = [_row("A01", q, 7 - q) for q in range(7)]                    # handed over against rank order
    comp = cc.composite(rows)
    assert [t.rank for t in _reading(comp, "A01")] == [1, 2, 3, 4, 5, 6, 7]


def test_a_metric_places_the_tiles_largest_or_smallest_first_and_one_with_no_value_last():
    """The values run against the ranks, so a sort that kept rank order would put them the other way round."""
    rows = [_row("A01", q, q + 1) for q in range(5)]
    values = {("A01", 0, 0): 1.0, ("A01", 1, 0): 5.0, ("A01", 2, 0): 3.0, ("A01", 3, 0): float("nan"),
              ("A01", 4, 0): 5.0}
    down = cc.composite(rows, values=values, descending=True, placed_by="Cell count at t0, largest first")
    assert [t.source[1] for t in _reading(down, "A01")] == [1, 4, 2, 0, 3]   # ties by rank; no value last
    up = cc.composite(rows, values=values, descending=False)
    assert [t.source[1] for t in _reading(up, "A01")] == [0, 2, 1, 4, 3]
    assert "placed by Cell count at t0, largest first within each well" in down.title(5)


def test_the_wells_follow_the_sort_too():
    rows = [_row("A01", 0, 1), _row("B02", 0, 2)]
    comp = cc.composite(rows, values={("A01", 0, 0): 1.0, ("B02", 0, 0): 9.0})
    assert [well for well, _x, _y in comp.patches] == ["B02", "A01"]
