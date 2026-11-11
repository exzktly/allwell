"""The nanowell lanes' synthetic-plate helpers stay cheap, and stay exactly what they were.

Most of the nanowell end-to-end lane's time was once these helpers rather than the pipeline under test.
``paint_cell`` tested every pixel of every tile for each cell it drew — 27 ms a cell, hundreds of cells a plate — so
in one profiled test 57 s of 80 were painting and 14 s the pipeline. And ``render_tiles``, ~1 s a well, was called
454 times across the lane for 165 distinct wells. Neither shows in any test's own logic, so nothing else notices
them coming back; these do.

Both speed-ups also promise *nothing changed*, which is the half that matters more: a painter that clips the wrong
pixel, or a cache that hands two tests the same arrays, would move numbers in a lane whose tests assert numbers.
The reference painter below is the whole-tile one, kept as the oracle.

The timing bounds are far from the measured figures in both directions (painting: 0.03 s measured, 1 s allowed,
~6.6 s before; a remembered render: ~2 ms measured, 0.3 s allowed, ~1.2 s before), so a loaded runner does not flake
them and a regression cannot slip under them.
"""

from __future__ import annotations

import copy
import time

import numpy as np

from tests import _nanowell_synth as synth
from tests._nanowell_run_fixture import PX, build_well, paint_cell


def _paint_by_whole_tile(rendered, site_um, *, dx_um=0.0, dy_um=0.0, radius_px=4.0, amount=0.35):
    """The painter as first written: every pixel of every tile tested. The oracle for ``paint_cell``."""
    cx, cy = site_um[0] + dx_um, site_um[1] + dy_um
    for name, image in rendered.images.items():
        left, top = rendered.true_left_top_um[name]
        yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
        inside = np.hypot((xx + 0.5) * PX + left - cx, (yy + 0.5) * PX + top - cy) <= radius_px * PX
        image[inside] += amount


def _cells(rendered, n=120, seed=0):
    """Cells across and beyond the plate — in tiles, in overlaps, off every tile, and on pixel boundaries."""
    rng = np.random.default_rng(seed)
    lefts = [lt[0] for lt in rendered.true_left_top_um.values()]
    tops = [lt[1] for lt in rendered.true_left_top_um.values()]
    w, h = rendered.plan.extent_um
    cells = [((rng.uniform(min(lefts) - 40, max(lefts) + w + 40), rng.uniform(min(tops) - 40, max(tops) + h + 40)),
              float(rng.uniform(0.5, 9.0))) for _ in range(n)]
    cells += [((left + k * PX / 2, top + k * PX / 4), 4.0)
              for left, top in rendered.true_left_top_um.values() for k in range(-6, 6)]
    return cells


def test_paint_cell_paints_exactly_the_pixels_the_whole_tile_painter_paints():
    rendered, _ = build_well(tile=(300, 300), seed=5)      # small tiles: the oracle is the slow painter
    mine, oracle = copy.deepcopy(rendered), copy.deepcopy(rendered)
    for site, radius in _cells(rendered, n=60):
        paint_cell(mine, site, radius_px=radius)
        _paint_by_whole_tile(oracle, site, radius_px=radius)
    changed = sum(int((oracle.images[n] != rendered.images[n]).sum()) for n in oracle.images)
    assert changed > 1000, "the fixture painted almost nothing, so the comparison below would measure nothing"
    for name in oracle.images:
        assert np.array_equal(mine.images[name], oracle.images[name]), name


def test_painting_costs_the_cell_not_the_tile():
    rendered, _ = build_well(seed=5)
    cells = _cells(rendered, n=200)
    started = time.perf_counter()
    for site, radius in cells:
        paint_cell(rendered, site, radius_px=radius)
    took = time.perf_counter() - started
    assert took < 1.0, (f"painting {len(cells)} cells took {took:.2f} s: paint_cell is testing whole tiles again "
                        "rather than each blob's bounding box")


def test_a_remembered_render_is_the_render_byte_for_byte():
    plan = synth.TilePlan(2, 2, (300, 300), 0.1, PX)
    kwargs = dict(pitch_um=172.0, diameter_um=100.0, radius_um=200.0, rotation_deg=7.0, noise_sigma=0.05, seed=11,
                  cells=6)
    fresh = synth._render_tiles(plan, **kwargs)
    first = synth.render_tiles(plan, **kwargs)
    again = synth.render_tiles(plan, **kwargs)
    for name in fresh.images:
        assert np.array_equal(first.images[name], fresh.images[name]), name
        assert np.array_equal(again.images[name], fresh.images[name]), name


def test_each_caller_gets_its_own_tiles():
    """A test paints into the tiles it is handed. Were two tests handed the same arrays, the second would start
    from the first one's cells."""
    first, _ = build_well(seed=5)
    painted = next(iter(first.images))
    first.images[painted][:] = 0.0
    paint_cell(first, (100.0, 100.0))
    second, _ = build_well(seed=5)
    assert second.images is not first.images
    assert all(second.images[n] is not first.images[n] for n in first.images)
    assert second.images[painted].any(), "the second caller received the first caller's painted-over tile"


def test_rendering_a_well_already_rendered_costs_a_copy_not_a_render():
    build_well(seed=5)
    started = time.perf_counter()
    build_well(seed=5)
    took = time.perf_counter() - started
    assert took < 0.3, (f"rendering the same well a second time took {took:.2f} s: render_tiles is rendering again "
                        "rather than copying what this worker already rendered")


def test_an_argument_the_cache_cannot_key_on_is_rendered_not_remembered():
    """An array's repr is elided past a few elements, so two different arrays can share one: a key built from it
    could hand one test another's well. Such a call is rendered every time."""
    assert synth._plain([1, 2.5, "hex", None, (0.0, 1.0), [(1, 2)]])
    assert not synth._plain([np.zeros(3)])
    assert not synth._plain([(np.float64(1.0), np.zeros(2))])
