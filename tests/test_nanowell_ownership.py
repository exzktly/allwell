"""Ownership checks calculate seam geometry without using the implementation."""

import numpy as np
import pytest

from pipeline.nanowell_lattice import TileRectangle, assign_owners


@pytest.mark.parametrize("diameter", [100.0, 50.0])
@pytest.mark.parametrize("overlap_delta", [0.0, -30.0])
def test_overlap_on_both_axes_retains_every_site_and_identifies_exact_seams(diameter, overlap_delta):
    margin = 10.0
    overlap = diameter + 2 * margin + overlap_delta
    width = 500.0
    step = width - overlap
    tiles = [TileRectangle(f"{col},{row}", col * step, row * step,
                           col * step + width, row * step + width)
             for row in range(2) for col in range(2)]
    radius = diameter / 2 + margin
    coordinates = [radius, step - 1, (step + width) / 2, width + 1, step + width - radius]
    sites = np.array([(x, y) for x in coordinates for y in coordinates])
    result = assign_owners(sites, diameter, tiles, margin)
    # A disk crossing a seam fits neither tile iff its centre lies in this gap.
    gap_left, gap_right = width - radius, step + radius
    expected = tuple(i for i, (x, y) in enumerate(sites)
                     if gap_left < x < gap_right or gap_left < y < gap_right)
    assert result.fused_indices == expected
    assert len(result.owners) == len(sites)
    assert tuple(i for i, owner in enumerate(result.owners) if owner is None) == expected
    if overlap_delta == 0:
        assert result.fused_indices == ()
    else:
        assert result.fused_indices


def test_nearest_centre_wins_and_exact_ties_do_not_depend_on_tile_order():
    tiles = [TileRectangle("b", 0, 0, 300, 300), TileRectangle("a", 100, 0, 400, 300)]
    sites = [[170, 150], [230, 150], [200, 150], [-100, 150]]
    expected = ("b", "a", "a", None)
    assert assign_owners(sites, 50, tiles, 10).owners == expected
    assert assign_owners(sites, 50, tiles[::-1], 10).owners == expected
    assert assign_owners(sites, 50, tiles, 10).fused_indices == ()
    assert assign_owners(sites, 50, tiles, 10).unimaged_indices == (3,)


def test_disk_fits_without_margin_but_must_be_fused_with_margin():
    tiles = [TileRectangle("only", 0, 0, 100, 100)]
    assert assign_owners([[30, 50]], 50, tiles, 0).owners == ("only",)
    assert assign_owners([[30, 50]], 50, tiles, 10).owners == (None,)
    assert assign_owners([[30, 50]], 50, tiles, 10).fused_indices == (0,)


def test_outer_edges_are_unimaged_and_seams_are_fused_without_losing_sites():
    tiles = [TileRectangle(f"{col},{row}", col * 100, row * 100,
                           col * 100 + 100, row * 100 + 100)
             for row in range(2) for col in range(2)]
    result = assign_owners([[100, 100], [195, 50], [50, 50], [230, 230]], 40, tiles, 5)
    assert result.owners == (None, None, "0,0", None)
    assert result.fused_indices == (0,)
    assert result.unimaged_indices == (1, 3)
    assert sorted(result.fused_indices + result.unimaged_indices) == [0, 1, 3]


def test_union_coverage_checks_interior_holes_and_the_disk_not_its_bounding_box():
    # A narrow hole lies inside the disk despite every cardinal rim point
    # being covered. Sampling the circumference alone would miss it.
    tiles = [TileRectangle("left", -100, -100, -2, 100),
             TileRectangle("right", 2, -100, 100, 100),
             TileRectangle("top", -100, -100, 100, -2),
             TileRectangle("bottom", -100, 2, 100, 100)]
    assert assign_owners([[0, 0]], 40, tiles, 0).unimaged_indices == (0,)
    # Bounding-box corner gaps outside a disk must not make it unimaged.
    cross = [TileRectangle("horizontal", -25, -15, 25, 15),
             TileRectangle("vertical", -15, -25, 15, 25)]
    result = assign_owners([[0, 0]], 40, cross, 0)
    assert result.fused_indices == (0,) and result.unimaged_indices == ()


def test_invalid_geometry_is_refused_and_empty_sites_are_supported():
    tile = TileRectangle("a", 0, 0, 100, 100)
    result = assign_owners(np.empty((0, 2)), 50, [tile], 0)
    assert result.owners == () and result.fused_indices == ()
    for diameter, margin in [(0, 0), (50, -1), (float("nan"), 0)]:
        with pytest.raises(ValueError):
            assign_owners([[50, 50]], diameter, [tile], margin)
    with pytest.raises(ValueError):
        assign_owners([[50, 50]], 50, [tile, tile], 0)
