"""The unified FOV-replicate rule: what counts as a replicate for the stats test
is decided by ``replicate_unit`` and produced by ``group_replicate_stats`` — the
same values the bar/line error bars reduce, so the test's ``n`` and the plotted
spread can't disagree.

Rule:
- FOV mode on -> per-FOV (n = total FOVs, pooled across all of the group's wells).
- FOV mode off, >=2 wells -> per-well (n = #wells).
- FOV mode off, 1 well -> a single value (n=1); the test is declined.
"""

from __future__ import annotations

import pytest

from review.domain.batch_models import BarGroup
from review.domain.stats_compute import (
    replicate_unit, group_replicate_stats, compute_rep_stats,
    compute_rep_per_fov_stats,
)
from review.features.stats.controller import collect_group_values, compute_pairwise

pytestmark = pytest.mark.qt

_VAL = "gfp_mean_intensity"


def test_replicate_unit_rule():
    assert replicate_unit(2, False) == "well"
    assert replicate_unit(2, True) == "fov"       # FOV mode pools FOVs across wells
    assert replicate_unit(1, True) == "fov"
    assert replicate_unit(1, False) == "well"     # single well, no FOV replicates
    assert replicate_unit(0, True) == "none"


def _grp(name, wells):
    return BarGroup(name, solo_wells=list(wells))


def test_multi_well_pools_fovs_when_fov_on(loaded_app_multifov):
    """A multi-well group tests per-well with FOV off (n = #wells) and per-FOV,
    pooled across wells, with FOV on (n = total FOVs)."""
    app = loaded_app_multifov
    grp = _grp("G", ["A01", "A02"])
    vals_off = collect_group_values(app._ctx, grp, 0.0, val_col=_VAL, threshold=0.0,
                                    statistic="mean", use_fov=False)
    assert len(vals_off) == 2  # two wells → two replicates
    vals_on = collect_group_values(app._ctx, grp, 0.0, val_col=_VAL, threshold=0.0,
                                   statistic="mean", use_fov=True)
    assert len(vals_on) == 6   # 2 wells × 3 FOVs → six replicates


def test_single_well_fov_on_is_per_fov(loaded_app_multifov):
    """A single-well group with FOV mode on tests across the well's FOVs."""
    app = loaded_app_multifov
    grp = _grp("solo", ["A01"])
    vals = collect_group_values(app._ctx, grp, 0.0, val_col=_VAL, threshold=0.0,
                                statistic="mean", use_fov=True)
    assert len(vals) == 3  # 3 FOVs → 3 replicates
    assert len(set(round(v, 6) for v in vals)) == 3  # per-FOV means differ


def test_single_well_fov_off_yields_one_value_untestable(loaded_app_multifov):
    """A single well with FOV mode off has no replicates → 1 value; the test is
    declined (compute_pairwise needs n>=2 per group)."""
    app = loaded_app_multifov
    grp = _grp("solo", ["A01"])
    vals = collect_group_values(app._ctx, grp, 0.0, val_col=_VAL, threshold=0.0,
                                statistic="mean", use_fov=False)
    assert len(vals) == 1
    # Two such single-well groups, FOV off → no test.
    pw = compute_pairwise(app._ctx, groups=[_grp("a", ["A01"]), _grp("b", ["B01"])],
                          target_t=0.0, val_col=_VAL, statistic="mean",
                          threshold=0.0, test="t-test (Welch)", use_fov=False)
    assert pw == []
    # Same groups WITH FOV mode on → a test runs (per-FOV, n=3 each).
    pw_on = compute_pairwise(app._ctx, groups=[_grp("a", ["A01"]), _grp("b", ["B01"])],
                             target_t=0.0, val_col=_VAL, statistic="mean",
                             threshold=0.0, test="t-test (Welch)", use_fov=True)
    assert pw_on and "p" in pw_on[0]


def test_saved_plot_captures_and_reruns_fov_mode(loaded_app_multifov):
    """A saved plot records the FOV-replicate mode and re-runs the test under it,
    so restored brackets match regardless of the live toggle."""
    from review.features.saved_plots.collect import _capture_stats_config
    from review.features.saved_plots.controller import _restore_stats
    app = loaded_app_multifov
    groups = [_grp("a", ["A01"]), _grp("b", ["B01"])]  # single-well groups
    app._app_state.stats.groups = groups
    app._app_state.plot.use_fov_replicates = True       # FOV mode ON
    app._app_state.stats.pairwise = compute_pairwise(
        app._ctx, groups=groups, target_t=0.0, val_col=_VAL, statistic="mean",
        threshold=0.0, test="t-test (Welch)", use_fov=True)
    assert app._app_state.stats.pairwise  # per-FOV test ran

    cfg = _capture_stats_config(app._ctx)
    assert cfg is not None and cfg["use_fov"] is True

    # Simulate a reload with the toggle now OFF; restore must still reproduce the
    # per-FOV result from the saved config (not the live toggle).
    app._app_state.plot.use_fov_replicates = False
    app._app_state.stats.pairwise = []
    _restore_stats(app._ctx, cfg)
    assert app._app_state.stats.pairwise and "p" in app._app_state.stats.pairwise[0]


def test_unity_plot_spread_reduces_same_replicates_as_stats(loaded_app_multifov):
    """The bar/line rep-stats reduce the SAME per-replicate values the stats test
    uses — so plot spread and test n describe the same replicate unit."""
    from review.domain.batch_models import ReplicateSet
    import numpy as np
    app = loaded_app_multifov

    # Multi-well, FOV off: plot per-well spread == SD of the stats' per-well values.
    rset = ReplicateSet("R", ["A01", "A02"])
    reps = group_replicate_stats(app._ctx, rset.wells, 0.0, use_fov=False,
                                 val_col=_VAL, threshold=0.0)
    assert len(reps) == 2
    gm, gerr, _gf, _fe = compute_rep_stats(app._ctx, rset, 0.0, 0.0, use_sem=False)
    means = np.array([r.mean for r in reps])
    assert np.isclose(gm, means.mean())
    assert np.isclose(gerr, means.std(ddof=1))

    # Multi-well, FOV on: plot per-FOV spread == SD of the stats' pooled per-FOV
    # values (FOVs pooled across both wells).
    fov_reps_multi = group_replicate_stats(app._ctx, rset.wells, 0.0, use_fov=True,
                                           val_col=_VAL, threshold=0.0)
    assert len(fov_reps_multi) == 6
    fm_m, ferr_m, *_ = compute_rep_per_fov_stats(app._ctx, rset, 0.0, 0.0, use_sem=False)
    fmeans_m = np.array([r.mean for r in fov_reps_multi])
    assert np.isclose(fm_m, fmeans_m.mean())
    assert np.isclose(ferr_m, fmeans_m.std(ddof=1))

    # Single well: plot per-FOV spread == SD of the stats' per-FOV values.
    solo = ReplicateSet("S", ["A01"])
    fov_reps = group_replicate_stats(app._ctx, solo.wells, 0.0, use_fov=True,
                                     val_col=_VAL, threshold=0.0)
    assert len(fov_reps) == 3
    fm, ferr, *_ = compute_rep_per_fov_stats(app._ctx, solo, 0.0, 0.0, use_sem=False)
    fmeans = np.array([r.mean for r in fov_reps])
    assert np.isclose(fm, fmeans.mean())
    assert np.isclose(ferr, fmeans.std(ddof=1))
