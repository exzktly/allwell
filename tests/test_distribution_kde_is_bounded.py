"""The Distribution tab's KDE holds a bounded block of the grid x values matrix.

``_gaussian_kde`` used to build the whole ``grid x values`` matrix at once, four
temporaries of ``8 * 200 * n`` bytes. At the tab's 200,000-value sample cap that
is about 1.3 GB on the GUI thread for one curve, and a build without the cap
reached tens of GB on a plate of large wells and took the process down after the
dataset had finished loading. These tests hold the two halves of the fix: the
curve is the one the dense formula gives, and the memory it takes does not grow
with the number of values.
"""

from __future__ import annotations

import math
import tracemalloc

import numpy as np
import pytest

from review.features.distribution import controller


def _dense_reference(values, grid):
    """The formula as it was written before blocking, for comparison."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = arr.size
    sd = float(np.std(arr, ddof=1))
    iqr = float(np.subtract(*np.percentile(arr, [75, 25])))
    sigma = min(sd, iqr / 1.34) if iqr > 0 else sd
    h = 0.9 * sigma * (n ** (-1.0 / 5.0))
    diff = (grid[:, None] - arr[None, :]) / h
    kernel = np.exp(-0.5 * diff * diff) / math.sqrt(2.0 * math.pi)
    return kernel.sum(axis=1) / (n * h)


@pytest.mark.parametrize("block_cells", [1, 7 * 50, 50 * 1000, 2_000_000])
def test_the_blocked_curve_is_the_dense_curve(monkeypatch, block_cells):
    # A bimodal sample with NaNs in it: the blocks must cover every finite
    # value exactly once, including a last block shorter than the rest (1,003
    # values do not divide into blocks of 7).
    rng = np.random.default_rng(3)
    vals = np.concatenate([rng.normal(10, 2, 600), rng.normal(30, 5, 403), [np.nan, np.inf]])
    grid = np.linspace(0, 50, 50)
    monkeypatch.setattr(controller, "_KDE_BLOCK_CELLS", block_cells)
    got = controller._gaussian_kde(vals, grid)
    np.testing.assert_allclose(got, _dense_reference(vals, grid), rtol=1e-10, atol=0)


def test_the_kde_does_not_allocate_the_whole_matrix():
    # 200,000 values is the sample cap the tab draws curves from. The dense
    # matrix there is 200 x 200,000 x 8 B = 320 MB per temporary; the values
    # themselves are 1.6 MB. numpy reports its buffers to tracemalloc.
    rng = np.random.default_rng(0)
    vals = rng.normal(100.0, 15.0, 200_000)
    grid = np.linspace(0.0, 200.0, 200)
    tracemalloc.start()
    try:
        controller._gaussian_kde(vals, grid)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 96 * 2**20, f"KDE peaked at {peak / 2**20:.0f} MiB for 200,000 values"
