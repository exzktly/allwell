"""Replicate spread for the bar plot's Events-above panel.

``compute_rep_n_above_stats`` is the events-panel analogue of
``compute_rep_stats`` — it returns the per-well mean N-above and the SD/SEM
across the set's wells, so the Events panel gets replicate error bars (the
panel previously reported a spread-less total). Qt-free, runs headless.
"""

from __future__ import annotations

import math

from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.domain.stats_compute import compute_rep_n_above_stats

from tests.conftest import MockApp, make_agg


def _app(n_above_by_well: dict, t: float = 24.0) -> MockApp:
    return MockApp(
        per_well_pts={
            w: [make_agg(t, mean=100.0, n_above=n)]
            for w, n in n_above_by_well.items()
        }
    )


def test_n_above_stats_mean_and_sd_across_wells():
    app = _app({"A01": 4, "A02": 6, "A03": 8})
    rset = ReplicateSet("R", ["A01", "A02", "A03"])
    mean, spread = compute_rep_n_above_stats(app._ctx, rset, 24.0, use_sem=False)
    assert mean == 6.0
    # ddof=1 SD of {4,6,8} = 2.0
    assert math.isclose(spread, 2.0, rel_tol=1e-9)


def test_n_above_stats_sem_scales_sd():
    app = _app({"A01": 4, "A02": 6, "A03": 8})
    rset = ReplicateSet("R", ["A01", "A02", "A03"])
    _mean, sd = compute_rep_n_above_stats(app._ctx, rset, 24.0, use_sem=False)
    _mean2, sem = compute_rep_n_above_stats(app._ctx, rset, 24.0, use_sem=True)
    assert math.isclose(sem, sd / math.sqrt(3), rel_tol=1e-9)


def test_n_above_stats_single_well_zero_spread():
    app = _app({"A01": 7})
    rset = ReplicateSet("R", ["A01"])
    mean, spread = compute_rep_n_above_stats(app._ctx, rset, 24.0, use_sem=False)
    assert mean == 7.0
    assert spread == 0.0


def test_n_above_stats_empty_is_zero():
    app = _app({"A01": 5})
    rset = ReplicateSet("R", ["NOPE"])  # not in well_paths
    mean, spread = compute_rep_n_above_stats(app._ctx, rset, 24.0, use_sem=False)
    assert mean == 0.0 and spread == 0.0


def test_grouped_collect_emits_spread_on_all_three_panels():
    """End-to-end through the (Qt-free) collector: a replicate set with
    differing per-well mean / fraction / N-above yields a non-zero spread on
    every panel — Events included (previously hard-coded to 0)."""
    from well_viewer.features.barplot.collect import collect_bar_items
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(1.0, 100.0, frac=0.2, n_above=4)],
            "A02": [make_agg(1.0, 200.0, frac=0.4, n_above=8)],
        },
        selections=[{"name": "R", "wells": ["A01", "A02"], "hidden": False}],
    )
    use_groups, items, _ = collect_bar_items(app, 1.0)
    assert use_groups is True
    item = next(it for it in items if it.key == "R")
    assert item.spread > 0          # Mean panel
    assert item.frac_spread > 0     # Fraction panel
    assert item.n_above_spread > 0  # Events panel — the fix
    assert item.n_above == 6.0      # per-well mean of {4, 8}
