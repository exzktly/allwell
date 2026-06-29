"""Replicate error bars reach all three bar-plot panels — Mean, Fraction, and
Events — in BOTH cross-replicate ("Across: Replicates") and per-FOV
("Across: FOV") spread modes.

The grouped bar renderer draws an error bar on each panel only when the
corresponding ``BarItem`` spread is > 0 (``item.spread`` / ``item.frac_spread``
/ ``item.n_above_spread``). These tests pin that the spread-producing helpers
emit non-zero values on every panel when the underlying wells / FOVs genuinely
differ — so a regression that silently zeroes one panel's spread (the
Fraction/Events "no error bars" bug) fails here instead of in the UI.

Qt-free: ``compute_rep_stats`` / ``compute_rep_n_above_stats`` run off the
in-memory ``MockApp`` AggPoints; the per-FOV path runs against a tiny real
DataFrame app stand-in so it exercises ``aggregate_with_threshold_df`` for real.
"""

from __future__ import annotations

import pandas as pd
import pytest

from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.domain.stats_compute import (
    compute_rep_n_above_stats,
    compute_rep_per_fov_stats,
    compute_rep_stats,
)

from tests.conftest import MockApp, make_agg


# ── "Across: Replicates" mode — cross-well spread on all three panels ────────

def test_replicates_mode_spread_on_all_three_panels():
    """Two wells with deliberately different per-well mean / fraction / N-above
    yield a non-zero cross-well spread on every panel."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(1.0, mean=100.0, frac=0.20, n_above=4)],
            "A02": [make_agg(1.0, mean=200.0, frac=0.40, n_above=8)],
        },
        selections=[{"name": "R", "wells": ["A01", "A02"], "hidden": False}],
    )
    rset = ReplicateSet("R", ["A01", "A02"])

    gm, gerr, gf, ferr = compute_rep_stats(app, rset, 1.0, threshold=50.0,
                                           use_sem=False)
    n_mean, n_spread = compute_rep_n_above_stats(app, rset, 1.0, use_sem=False)

    assert gm == 150.0 and gerr > 0      # Mean panel
    assert gf == pytest.approx(0.30) and ferr > 0  # Fraction panel
    assert n_mean == 6.0 and n_spread > 0  # Events panel


def test_replicates_mode_identical_wells_zero_spread():
    """Sanity floor: wells that share the same fraction / N-above (but differ in
    mean) legitimately produce zero spread on the lower two panels — the lower
    panels being blank is *correct* here, not the bug."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(1.0, mean=100.0, frac=0.30, n_above=5)],
            "A02": [make_agg(1.0, mean=200.0, frac=0.30, n_above=5)],
        },
        selections=[{"name": "R", "wells": ["A01", "A02"], "hidden": False}],
    )
    rset = ReplicateSet("R", ["A01", "A02"])
    _gm, gerr, _gf, ferr = compute_rep_stats(app, rset, 1.0, threshold=50.0,
                                             use_sem=False)
    _n, n_spread = compute_rep_n_above_stats(app, rset, 1.0, use_sem=False)
    assert gerr > 0          # mean differs ⇒ spread
    assert ferr == 0.0       # fraction identical ⇒ no spread (legitimately)
    assert n_spread == 0.0   # n_above identical ⇒ no spread (legitimately)


# ── "Across: FOV" mode — per-FOV spread through the real aggregator ──────────

class _FovApp:
    """Minimal real-DataFrame app stand-in for the per-FOV stats path.

    ``compute_rep_per_fov_stats`` pools each well's rows (``_get_rows``),
    prefixes the FOV ids, and runs ``aggregate_with_threshold_df`` for real —
    so this stand-in exercises the production per-FOV spread maths, unlike the
    MockApp mock which returns a hard-coded 0.0.
    """

    def __init__(self, rows_by_well: dict[str, pd.DataFrame]):
        self._rows = rows_by_well
        from well_viewer.core.app_state import AppState
        from well_viewer.features.registry import attach_feature_state
        st = AppState()
        attach_feature_state(st)
        st.dataset.well_paths = {w: w for w in rows_by_well}
        st.channel.active_val_col = "gfp_mean_intensity"
        self.__app_state = st

    @property
    def _app_state(self):
        return self.__app_state

    def _get_rows(self, label):
        return self._rows.get(label)

    def _get_all_fluor_gates(self):
        return {}


def _fov_df(rows):
    """rows: list of (fov, timepoint_hours, gfp_mean_intensity)."""
    return pd.DataFrame(
        rows, columns=["fov", "timepoint_hours", "gfp_mean_intensity"]
    ).assign(area_px=100.0, Included=1)


def test_fov_mode_spread_on_all_three_panels():
    """A replicate set whose pooled FOVs differ in per-FOV mean, fraction-on,
    and above-threshold count yields non-zero spread on all three panels in
    per-FOV mode."""
    # Threshold = 50: each FOV has a different on-count / on-mean / fraction.
    # FOV f1: values {10, 60, 70}      → 2 above (mean 65), frac 2/3
    # FOV f2: values {10, 20, 80, 90}  → 2 above (mean 85), frac 2/4
    # FOV f3 (well 2): {100, 200}      → 2 above (mean 150), frac 2/2
    # FOV f4 (well 2): {10, 10, 10, 55}→ 1 above (mean 55),  frac 1/4
    well1 = _fov_df([
        ("f1", 1.0, 10.0), ("f1", 1.0, 60.0), ("f1", 1.0, 70.0),
        ("f2", 1.0, 10.0), ("f2", 1.0, 20.0), ("f2", 1.0, 80.0), ("f2", 1.0, 90.0),
    ])
    well2 = _fov_df([
        ("f3", 1.0, 100.0), ("f3", 1.0, 200.0),
        ("f4", 1.0, 10.0), ("f4", 1.0, 10.0), ("f4", 1.0, 10.0), ("f4", 1.0, 55.0),
    ])
    app = _FovApp({"W1": well1, "W2": well2})
    rset = ReplicateSet("R", ["W1", "W2"])

    mean, mean_err, frac, frac_err, n_pf_mean, n_pf_spread = (
        compute_rep_per_fov_stats(app, rset, 1.0, threshold=50.0, use_sem=False)
    )

    assert mean_err > 0     # Mean panel — per-FOV mean intensities differ
    assert frac_err > 0     # Fraction panel — per-FOV fraction-on differs
    assert n_pf_spread > 0  # Events panel — per-FOV above-counts differ
    assert n_pf_mean > 0


def test_fov_mode_single_fov_zero_spread():
    """One pooled FOV ⇒ no per-FOV spread anywhere (nothing to vary across)."""
    well1 = _fov_df([("f1", 1.0, 60.0), ("f1", 1.0, 70.0)])
    app = _FovApp({"W1": well1})
    rset = ReplicateSet("R", ["W1"])
    _m, mean_err, _f, frac_err, _nm, n_pf_spread = compute_rep_per_fov_stats(
        app, rset, 1.0, threshold=50.0, use_sem=False
    )
    assert mean_err == 0.0 and frac_err == 0.0 and n_pf_spread == 0.0
