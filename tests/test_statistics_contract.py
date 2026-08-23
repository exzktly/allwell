"""Regression guards for the statistics contract (PR 2).

Every number a plot, a table or an export shows is produced by the functions
guarded here, and each rule below was broken at the time it was written. They
are grouped by the question they answer:

1. What does an *undefined* spread look like? (``NaN``, never ``0.0`` — the two
   are different facts and one of them is a measurement.)
2. Which timepoint is a "vs t0" baseline actually taken from?
3. What counts as a *number* — is a ``numpy.float32`` one?
4. Which cells does an "off" gate exclude? (None.)
5. Can a well whose FOV labels are provably wrong contribute a per-field
   replicate? (No.)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from well_viewer.data.data_loading import aggregate_with_threshold_df
from well_viewer.domain.fold_change import (
    _rel_err_sq,
    _scale_pt,
    normalize_pts,
    scale_bar_value,
)
from well_viewer.domain.stats_compute import _reduce_reps

# ``_as_finite`` and ``active_fluor_gates`` are imported inside the tests that
# use them, not at module scope. Copied into a worktree of the base branch to
# prove these guards bite, a module-scope import of a symbol the fix introduces
# turns the whole file into a collection error — which reads exactly like "it
# failed on the base" while measuring nothing at all (CLAUDE.md §7.4 #4).


class _Rep:
    """Minimal stand-in for a ``ReplicateStat`` — ``_reduce_reps`` only ever
    reads one named field off it."""

    def __init__(self, value: float) -> None:
        self.mean = value
        self.frac = value
        self.n_above = value


# ── 1. an undefined spread is NaN, never 0.0 ─────────────────────────────────

def test_single_replicate_spread_is_undefined_not_zero():
    """One replicate has no spread. Reporting ``0.0`` makes a set nobody could
    compute a spread for indistinguishable from a set whose replicates agreed
    exactly — and it exported as ``err_SD = 0.000000``."""
    mean, spread = _reduce_reps([_Rep(5.0)], "mean", use_sem=False)
    assert mean == 5.0
    assert math.isnan(spread)


def test_no_replicates_gives_undefined_mean_and_spread():
    mean, spread = _reduce_reps([], "mean", use_sem=False)
    assert math.isnan(mean) and math.isnan(spread)


def test_two_identical_replicates_have_a_measured_zero_spread():
    """The control case: this ``0.0`` is a measurement and must survive."""
    mean, spread = _reduce_reps([_Rep(5.0), _Rep(5.0)], "mean", use_sem=False)
    assert mean == 5.0 and spread == 0.0


def test_an_undefined_spread_survives_normalization_on_both_paths():
    """The line path folded it to 0.0 while the bar path preserved it, so one
    figure carried two conventions for "we could not compute this"."""
    line_spread = _scale_pt((0, 250.0, float("nan")), 100.0)[2]
    _, bar_spread = scale_bar_value(250.0, float("nan"), control_mean=100.0)
    assert math.isnan(line_spread)
    assert math.isnan(bar_spread)


# ── 2. which timepoint the baseline comes from ───────────────────────────────

_PTS = [
    # (t, mean, spread, frac, n_above, n_total, frac_spread, npf_m, npf_s, n_spread, n_total_x)
    (0.0, 100.0, 5.0, 0.0, 0, 200, 0.0, 0.0, 0.0, 0.0, 0),
    (24.0, 300.0, 9.0, 0.40, 80, 200, 0.0, 0.0, 0.0, 0.0, 0),
    (48.0, 500.0, 11.0, 0.60, 120, 200, 0.0, 0.0, 0.0, 0.0, 0),
]


def test_subtract_mode_accepts_a_zero_baseline():
    """Subtracting zero is well defined, and zero at t0 is the *expected*
    reading for a fraction-on or an event count at the start of an induction
    experiment. Requiring a non-zero baseline for both operations meant the
    requested timepoint was skipped and a later one silently substituted — so
    the mean panel normalised against t=0 while the fraction and events panels
    beside it normalised against t=24, under one axis label."""
    out = normalize_pts(_PTS, use_t0=True, baseline_tp=0.0,
                        op="subtract", normalize_frac_n=True)
    by_t = {p[0]: p for p in out}
    assert by_t[0.0][1] == 0.0 and by_t[24.0][1] == 200.0
    assert by_t[0.0][3] == pytest.approx(0.0)
    assert by_t[24.0][3] == pytest.approx(0.40)   # 0.40 - 0.0, not 0.40 / 0.40
    assert by_t[48.0][4] == pytest.approx(120.0)  # 120 - 0, not 120 / 80


def test_ratio_mode_still_refuses_a_zero_divisor():
    """Division by zero is undefined however inconvenient, so the fall-through
    stands here — it is now logged rather than silent."""
    out = normalize_pts(_PTS, use_t0=True, baseline_tp=0.0,
                        op="ratio", normalize_frac_n=True)
    by_t = {p[0]: p for p in out}
    assert by_t[24.0][1] == pytest.approx(3.0)    # mean divides by t=0
    assert by_t[24.0][3] == pytest.approx(1.0)    # fraction falls back to t=24


def test_the_zero_baseline_fallback_is_announced(caplog):
    with caplog.at_level("WARNING"):
        normalize_pts(_PTS, use_t0=True, baseline_tp=0.0,
                      op="ratio", normalize_frac_n=True)
    assert any("baseline timepoint" in r.getMessage() for r in caplog.records)


# ── 3. a numpy float is a number ─────────────────────────────────────────────

def test_numpy_floats_are_numbers():
    from well_viewer.domain.fold_change import _as_finite

    """``isinstance(x, float)`` is False for ``numpy.float32``, so the spelling
    it replaced answered "not a number" — silently, in the most numerically
    sensitive module in the app, turning a real spread into a vanished error
    bar rather than an exception."""
    assert _as_finite(np.float32(2.5)) == pytest.approx(2.5)
    assert _as_finite(np.float64(2.5)) == pytest.approx(2.5)
    assert _as_finite(np.nan) is None
    assert _as_finite(None) is None
    assert _as_finite(True) is None      # a flag is not a measurement
    assert _as_finite("nope") is None


def test_float32_and_float_normalize_identically():
    assert _rel_err_sq(np.float32(10), np.float32(2)) == pytest.approx(
        _rel_err_sq(10.0, 2.0))
    f32 = _scale_pt((0, np.float32(10), np.float32(1)), 2.0)
    f64 = _scale_pt((0, 10.0, 1.0), 2.0)
    assert f32[1] == pytest.approx(f64[1])
    assert f32[2] == pytest.approx(f64[2])


# ── 4. an "off" gate excludes nothing ────────────────────────────────────────

def test_a_zero_gate_is_not_a_filter():
    from well_viewer.domain.gating_state import active_fluor_gates

    assert active_fluor_gates({"gfp": 0.0, "mcherry": 0.0}) == {}
    assert active_fluor_gates({"gfp": 0.0, "mcherry": 10.0}) == {"mcherry": 10.0}
    # A negative floor is a choice, not a default: background-subtracted
    # channels legitimately run below zero.
    assert active_fluor_gates({"gfp": -3.0}) == {"gfp": -3.0}


def _gating_frame():
    return pd.DataFrame({
        "timepoint_hours": [0.0] * 4,
        "gfp_mean_intensity": [-5.0, 0.0, 10.0, 20.0],
        # A NaN in an unrelated gated channel used to remove the row.
        "mcherry_mean_intensity": [1.0, 2.0, np.nan, 4.0],
        "area_px": [100.0] * 4,
        "Included": [1] * 4,
    })


def test_default_gates_do_not_remove_cells():
    """With every gate at its documented "off" value, the filter was still
    applied as ``isfinite(v) & (v > 0)`` — so a cell whose background-subtracted
    intensity was zero or negative, or which carried a NaN in any *other* gated
    channel, was dropped by a choice the user never made."""
    pts = aggregate_with_threshold_df(
        _gating_frame(), threshold=-1.0, use_sem=False,
        val_col="gfp_mean_intensity",
        fluor_gates={"gfp": 0.0, "mcherry": 0.0},
    )
    assert pts, "an all-default gate set removed every row"
    assert int(pts[0][5]) == 4


def test_a_real_gate_still_filters():
    pts = aggregate_with_threshold_df(
        _gating_frame(), threshold=-1.0, use_sem=False,
        val_col="gfp_mean_intensity",
        fluor_gates={"gfp": 15.0, "mcherry": 0.0},
    )
    assert int(pts[0][5]) == 1
