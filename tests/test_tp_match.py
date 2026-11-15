"""Audit S2 — the shared relative timepoint matcher. Fractional timepoints
round-tripped through a ``:.4g`` combo label must match (an absolute 1e-6
tolerance missed them, so Statistics / aggregation returned "no data" while
fold-change matched); distinct timepoints must still stay separate.
"""
from __future__ import annotations

import numpy as np

from domain.tp_match import tp_close, tp_close_mask


def test_whole_numbers_match_and_distinguish():
    assert tp_close(24, 24)
    assert not tp_close(24, 48)
    assert not tp_close(0.5, 1.5)


def test_fractional_4g_roundtrip_matches_where_abs_1e6_failed():
    orig = 2 / 3                       # 0.666666…
    parsed = float(f"{orig:.4g}")      # 0.6667 — as a combo label round-trips
    assert not (abs(orig - parsed) < 1e-6), "abs 1e-6 would have missed this"
    assert tp_close(orig, parsed)


def test_mask_matches_fractional_only_at_target():
    orig = 2 / 3
    parsed = float(f"{orig:.4g}")
    arr = np.array([0.0, orig, 24.0])
    assert tp_close_mask(arr, parsed).tolist() == [False, True, False]


def test_mask_distinguishes_adjacent_whole_timepoints():
    arr = np.array([0.0, 24.0, 48.0])
    assert tp_close_mask(arr, 24.0).tolist() == [False, True, False]


# ── the tolerance is the label's rounding error, not a share of the hour value ──

import pytest  # noqa: E402

from domain.tp_match import tp_tolerance  # noqa: E402


@pytest.mark.parametrize("hours", [0.0, 2 / 3, 7.123456, 23.99951, 99.91667, 100.0833, 483.25, 1234.567])
def test_every_value_matches_its_own_4g_label(hours):
    """The one thing the tolerance exists for, at every magnitude."""
    assert tp_close(hours, float(f"{hours:.4g}"))
    assert tp_close_mask(np.array([hours]), float(f"{hours:.4g}")).tolist() == [True]


@pytest.mark.parametrize("minutes, at_hours", [(5, 100.0), (5, 500.0), (10, 900.0), (2, 99.0), (60, 5000.0)])
def test_neighbouring_frames_of_a_long_time_lapse_stay_separate(minutes, at_hours):
    """A tolerance of one part in a thousand pooled 5-minute frames from 83 h on (three frames at 100 h)."""
    step = minutes / 60.0
    frames = np.array([at_hours - step, at_hours, at_hours + step])
    assert tp_close_mask(frames, at_hours).tolist() == [False, True, False]
    assert not tp_close(at_hours, at_hours + step)
    assert not tp_close(at_hours - step, at_hours)


def test_the_tolerance_is_half_a_unit_of_the_fourth_significant_figure():
    assert tp_tolerance(24.0, 24.0) == pytest.approx(0.005)
    assert tp_tolerance(100.0, 100.0) == pytest.approx(0.05)
    assert tp_tolerance(0.0, 0.0) == pytest.approx(0.0005)


def test_nan_matches_nothing():
    assert not tp_close(float("nan"), 24.0)
    assert tp_close_mask(np.array([np.nan, 24.0]), 24.0).tolist() == [False, True]
