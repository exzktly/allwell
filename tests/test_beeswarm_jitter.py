"""Beeswarm: standard non-overlapping 'swarm' packing (deterministic)."""
from __future__ import annotations

import numpy as np

from review.data.data_loading import _beeswarm_jitter


def test_widely_spaced_points_sit_on_midline():
    # Points far apart in y (> one dot diameter) never collide → all on the
    # column midline (no spurious spread).
    xs, ys = _beeswarm_jitter([0.0, 100.0, 200.0, 300.0], x_center=2.0,
                              max_spread=0.35, n_bins=40)
    assert all(abs(x - 2.0) < 1e-9 for x in xs)


def test_width_tracks_local_density():
    # Unimodal cloud: widest at the mode, slim in the tails (not a U/V).
    vals = np.random.default_rng(1).normal(0.0, 1.0, 3000).tolist()
    xs, ys = _beeswarm_jitter(vals, x_center=0.0, max_spread=0.35)
    xs, ys = np.asarray(xs), np.asarray(ys)
    assert np.all(np.abs(xs) <= 0.35 + 1e-9)
    core = np.abs(xs[np.abs(ys) < 0.3]).mean()
    tail = np.abs(xs[np.abs(ys) > 2.0]).mean()
    assert core > 3 * tail


def test_symmetric_about_midline_and_no_xy_correlation():
    vals = np.random.default_rng(2).normal(0.0, 1.0, 2000).tolist()
    xs, ys = _beeswarm_jitter(vals, x_center=0.0)
    assert abs(float(np.mean(xs))) < 0.02                  # symmetric fill
    assert abs(float(np.corrcoef(xs, ys)[0, 1])) < 0.1     # no chevron


def test_no_overlap_same_y_row():
    # All identical y → a single packed row; every dot lands at a distinct x.
    xs, _ = _beeswarm_jitter([5.0] * 9, x_center=0.0, max_spread=0.35, n_bins=40)
    assert len({round(x, 6) for x in xs}) == 9


def test_deterministic_across_calls():
    vals = [1.0, 2.0, 2.0, 2.0, 3.0, 5.0, 2.0, 2.0]
    assert _beeswarm_jitter(vals)[0] == _beeswarm_jitter(vals)[0]


def test_empty_and_single():
    assert _beeswarm_jitter([]) == ([], [])
    xs, ys = _beeswarm_jitter([4.2], x_center=1.0)
    assert ys == [4.2] and xs == [1.0]
