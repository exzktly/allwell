"""Beeswarm jitter: random deviation with width ∝ local density (no chevrons)."""
from __future__ import annotations

import numpy as np

from well_viewer.data.data_loading import _beeswarm_jitter


def test_width_tracks_local_density():
    # Unimodal cloud: dense near the mode, sparse in the tails. The swarm must
    # be widest at the mode and pinch in at the tails (not a U/V).
    vals = np.random.default_rng(1).normal(0.0, 1.0, 3000).tolist()
    xs, ys = _beeswarm_jitter(vals, x_center=0.0, max_spread=0.35)
    xs, ys = np.asarray(xs), np.asarray(ys)
    assert np.all(np.abs(xs) <= 0.35 + 1e-9)            # within max_spread
    core = np.abs(xs[np.abs(ys) < 0.3]).mean()
    tail = np.abs(xs[np.abs(ys) > 2.0]).mean()
    assert core > 3 * tail                               # dense ≫ sparse


def test_no_monotonic_x_vs_y_chevron():
    # The old rank-along-y placement made |x| grow monotonically with y inside
    # each bin (the chevron). With random jitter, x is essentially uncorrelated
    # with y across the column.
    vals = np.random.default_rng(2).normal(0.0, 1.0, 2000).tolist()
    xs, ys = _beeswarm_jitter(vals)
    assert abs(float(np.corrcoef(xs, ys)[0, 1])) < 0.1


def test_deterministic_across_calls():
    vals = [1.0, 2.0, 2.0, 2.0, 3.0, 5.0]
    a, _ = _beeswarm_jitter(vals)
    b, _ = _beeswarm_jitter(vals)
    assert a == b


def test_empty_and_single():
    assert _beeswarm_jitter([]) == ([], [])
    xs, ys = _beeswarm_jitter([4.2])
    assert ys == [4.2] and len(xs) == 1
