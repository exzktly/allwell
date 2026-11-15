"""The mesh background sits where the background is, edge to edge.

``robust_mesh`` estimates one background level per tile and interpolates
between them. A tile's level belongs at the tile's centre. Placing the first
and last levels on the frame's first and last pixels instead moves every level
outward by up to half a tile, so under an illumination gradient the estimate is
wrong by that distance times the gradient — across the whole frame, with
opposite signs at its two sides. A cell then reads brighter or dimmer by where
in the field it happens to lie.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.background import _robust_grid_background, subtract_background

BOX = 100


def _ramp(size: int, per_px_x: float = 0.5, per_px_y: float = 0.0) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    return (200 + per_px_x * xx + per_px_y * yy).astype(np.float32)


@pytest.mark.parametrize("size", [1000, 1024, 2048, 513])
def test_a_pure_ramp_is_recovered_at_every_pixel(size):
    """No cells, no noise: the background *is* the frame. 1000 divides into tiles; the rest do not."""
    truth = _ramp(size)
    error = _robust_grid_background(truth, BOX) - truth
    mid = size // 2
    assert error[mid, 0] == pytest.approx(0.0, abs=0.05)          # was +24.75
    assert error[mid, size // 3] == pytest.approx(0.0, abs=0.05)  # was about +8: not an edge effect
    assert error[mid, -1] == pytest.approx(0.0, abs=0.05)         # was -24.75 to -26.5
    assert float(np.abs(error).max()) < 0.05


def test_a_gradient_along_both_axes_is_recovered_at_the_border():
    """The border nodes' smoothing must not be lopsided: this is the case that shows it."""
    truth = _ramp(1000, per_px_x=0.3, per_px_y=0.1)
    error = _robust_grid_background(truth, BOX) - truth
    assert float(np.abs(error).max()) < 0.05                      # was 29.8


def _field_with_cells(size: int, seed: int = 3):
    """A two-axis gradient with read noise and bright 9 x 9 cells, none touching another."""
    rng = np.random.default_rng(seed)
    truth = _ramp(size, per_px_x=0.3, per_px_y=0.1)
    img = truth + rng.normal(0, 5, truth.shape).astype(np.float32)
    cells = []
    for cy in range(20, size - 20, 40):
        for cx in range(20, size - 20, 40):
            y, x = cy + int(rng.integers(-8, 9)), cx + int(rng.integers(-8, 9))
            img[y - 4:y + 5, x - 4:x + 5] += 800
            cells.append((y, x))
    return truth, img, cells


def test_a_noisy_field_of_cells_is_within_a_count_of_the_truth():
    truth, img, _cells = _field_with_cells(1024)
    error = _robust_grid_background(img, BOX) - truth
    assert float(np.sqrt((error ** 2).mean())) < 1.0              # was 9.2
    assert abs(float(error[512, 0])) < 3.0
    assert abs(float(error[512, -1])) < 3.0


def test_a_cell_reads_the_same_at_the_left_of_the_field_and_the_right():
    """The number a user gets: identical cells, corrected, by where they lie."""
    _truth, img, cells = _field_with_cells(1024)
    corrected = subtract_background(img, radius=300, method="robust_mesh", mesh_ratio=3.0)

    def mean_of(where) -> float:
        picked = [corrected[y - 2:y + 3, x - 2:x + 3].mean() for y, x in cells if where(x)]
        assert len(picked) >= 15
        return float(np.mean(picked))

    left, right = mean_of(lambda x: x < 150), mean_of(lambda x: x > 874)
    assert left == pytest.approx(800.0, abs=4.0)                  # was 784.8
    assert right == pytest.approx(800.0, abs=4.0)
    assert abs(left - right) < 4.0


def test_a_border_tile_swamped_by_one_object_is_still_dropped():
    """What the mesh's median is for, kept at the border the fix touches."""
    truth = _ramp(1000, per_px_x=0.3, per_px_y=0.1)
    img = truth.copy()
    img[400:500, 0:100] += 5000                                   # one whole border tile
    error = _robust_grid_background(img, BOX) - truth
    # Replaced by a neighbour's level, so off by at most one tile's step (30), not by 5,000.
    assert abs(float(error[450, 50])) < 35.0
    assert abs(float(error[450, 600])) < 1.0


def test_a_frame_smaller_than_one_tile_is_one_level():
    frame = np.full((40, 40), 321.0, np.float32)
    background = _robust_grid_background(frame, BOX)
    assert background.shape == (40, 40)
    np.testing.assert_allclose(background, 321.0, atol=1e-3)


def test_a_frame_one_tile_high_is_interpolated_along_its_length():
    strip = np.tile(np.arange(300, dtype=np.float32), (20, 1))
    background = _robust_grid_background(strip, BOX)
    np.testing.assert_allclose(background[10, [0, 150, 299]], [0.0, 150.0, 299.0], atol=0.05)
