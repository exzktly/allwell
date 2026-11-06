"""The window's hex composite: one patch per well, tiles by rank on a spiral, pages.

Asked for: the nanowells inside a Distribution window packed into an artificial hex array, grouped by source well
and placed by rank within each. It is not a well — nothing in its layout says where a nanowell sat in its own — so
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
    assert (by_rank[1].x, by_rank[1].y) == (0.0, 0.0)                  # B02's rank 1 is its patch's centre
    assert (by_rank[2].x, by_rank[2].y) == (4.0, 0.0)                  # A01's rank 2 is the centre of the next patch
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
        "Composite of 5 of 5 nanowells from 2 wells — not a real array; places assigned by rank within each well")


def test_pages_are_clamped():
    rows = [_row("A01", i, i + 1) for i in range(100)]
    sizes = [len(cc.page(rows, i)) for i in (0, 1, 7, -1)]
    assert sizes == [91, 9, 9, 91]
