"""The fused nanowells are given an image to be counted on (``pipeline.nanowell_seams``).

An edge site (wholly inside one tile, short of the margin at the scan's outer edge) is measured on the tile that
holds it; a blend site (straddling a seam) on a seam tile blended from every tile that covers it, its row naming the
real tile covering most of its disk. These pin the assignment, the blend's pixels and its channels; the run's own
counting of a seam nanowell is pinned in ``test_nanowell_run.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from pipeline import nanowell_seams as ns

PX = 1.0
DIAMETER = 20.0
#: Two 60 x 60 µm tiles overlapping by 10 µm along x: the seam band is x in [50, 60].
RECTS = {"L": (0.0, 0.0, 60.0, 60.0), "R": (50.0, 0.0, 110.0, 60.0)}


def _images(level_l=1.0, level_r=3.0):
    return {"L": np.full((60, 60), level_l, dtype=np.float32), "R": np.full((60, 60), level_r, dtype=np.float32)}


def _report(owners, edge=(), blend=()):
    return SimpleNamespace(owners=tuple(owners), edge_indices=tuple(edge), blend_indices=tuple(blend),
                           fused_indices=tuple(sorted((*edge, *blend))))


def test_an_edge_site_is_measured_on_the_tile_that_holds_it():
    # (15, 12): inside L with its 10 µm radius, but within a margin of L's top edge (the scan's outer edge).
    centres = np.array([[15.0, 12.0]])
    seams = ns.seam_tiles(_report([None], edge=[0]), centres, RECTS, _images(), DIAMETER, 30.0, 5.0, PX)
    assert seams.owners == ("L",) and seams.reported == {0: "L"} and not seams.images


def test_a_seam_site_gets_a_blended_tile_and_names_the_tile_covering_most_of_it():
    # (57, 30) straddles the seam; more of its disk lies in R (x 47-67: 3 µm of it left of R's edge at 50).
    centres = np.array([[57.0, 30.0]])
    seams = ns.seam_tiles(_report([None], blend=[0]), centres, RECTS, _images(), DIAMETER, 30.0, 4.0, PX)
    (tile,) = seams.images
    assert ns.is_seam_tile(tile) and seams.owners == (tile,) and seams.reported == {0: "R"}
    image, (left, top) = seams.images[tile], seams.positions[tile]
    assert np.isfinite(image).all()
    assert image.shape[0] >= DIAMETER + 2 * 4.0
    # Pure L left of the seam band, pure R right of it, a blend between: sampled at the site's row.
    row = image[int(30.0 - top)]
    x = left + (np.arange(len(row)) + 0.5) * PX
    assert np.allclose(row[x < 49.0], 1.0) and np.allclose(row[x > 61.0], 3.0)
    between = row[(x > 51.0) & (x < 59.0)]
    assert between.size and (between > 1.0).all() and (between < 3.0).all()


def test_seam_sites_near_one_another_share_one_seam_tile():
    centres = np.array([[55.0, 15.0], [55.0, 35.0], [55.0, 300.0]])
    seams = ns.seam_tiles(_report([None] * 3, blend=[0, 1, 2]), centres, RECTS, _images(), DIAMETER, 30.0, 4.0, PX)
    assert seams.owners[0] == seams.owners[1] != seams.owners[2]


def test_a_seam_tile_s_channel_is_blended_on_the_same_pixels_and_an_ordinary_tile_passes_through():
    centres = np.array([[57.0, 30.0]])
    seams = ns.seam_tiles(_report([None], blend=[0]), centres, RECTS, _images(), DIAMETER, 30.0, 4.0, PX)
    (tile,) = seams.images
    gfp = _images(10.0, 30.0)
    channels = seams.channels({"GFP": gfp.get}, RECTS, PX)
    blended = channels["GFP"](tile)
    assert blended.shape == seams.images[tile].shape
    assert np.allclose(blended, 10.0 * seams.images[tile])          # the same weights, ten times the levels
    assert channels["GFP"]("L") is gfp["L"]
    missing = seams.channels({"RFP": {"L": gfp["L"]}.get}, RECTS, PX)
    assert missing["RFP"](tile) is None                              # a covering tile with no image: none


def test_nothing_fused_changes_nothing():
    centres = np.array([[20.0, 30.0]])
    seams = ns.seam_tiles(_report(["L"]), centres, RECTS, _images(), DIAMETER, 30.0, 4.0, PX)
    assert seams.owners == ("L",) and not seams.images and not seams.reported
