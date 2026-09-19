"""The threshold curve (``review.domain.threshold_sweep``).

The module's job is to describe a sweep without recommending a threshold, and
most of these tests exist to keep it that way. The measurement behind that
decision is in the module docstring: the plateau rule this replaced returns
either the sweep's zero tail (suggesting *detect nothing*) or its low-threshold
shoulder (suggesting 0.200, which is the constant the feature exists to let the
user argue with), decided by how ``log(0)`` happens to be guarded.
"""

from __future__ import annotations

import math

import pytest

from review.domain.threshold_sweep import (
    DEFAULT_HI, DEFAULT_LO, DEFAULT_STEPS, MAX_STEPS, MIN_STEPS,
    ThresholdCurve, curve_from_counts, sweep_grid,
)


def _decay(thresholds, total=400, mid=0.5, width=0.05):
    """A realistic monotone count curve with a zero tail — the shape every
    degenerate rule was measured against."""
    return [int(round(total / (1 + math.exp((t - mid) / width))))
            for t in thresholds]


# ── the grid ────────────────────────────────────────────────────────────────

def test_the_grid_spans_the_default_range_inclusive():
    grid = sweep_grid()
    assert len(grid) == DEFAULT_STEPS
    assert grid[0] == pytest.approx(DEFAULT_LO)
    assert grid[-1] == pytest.approx(DEFAULT_HI)
    assert grid == sorted(grid)


def test_the_grid_does_not_run_to_zero_or_one():
    """Excludes the grid that sweeps the whole unit interval.

    At τ=0 StarDist keeps every candidate polygon the network emitted — tens of
    thousands of objects and minutes of NMS on a noisy frame — and at τ=1
    nothing survives by construction. Neither end is a threshold anyone would
    run, and both cost the most.
    """
    assert DEFAULT_LO > 0.0 and DEFAULT_HI < 1.0


@pytest.mark.parametrize("steps,expected", [
    (0, MIN_STEPS), (1, MIN_STEPS), (2, MIN_STEPS),
    (10_000, MAX_STEPS), (19, 19),
])
def test_a_step_count_out_of_range_is_clamped_not_refused(steps, expected):
    """Every point is a segmentation, so the bound is a time budget. A sweep
    that refuses to run is worse evidence than a coarse one."""
    assert len(sweep_grid(steps=steps)) == expected


def test_a_reversed_range_is_ordered_rather_than_empty():
    assert sweep_grid(0.9, 0.1, 5) == sweep_grid(0.1, 0.9, 5)


def test_a_degenerate_range_yields_one_point():
    assert sweep_grid(0.4, 0.4, 9) == [0.4]


# ── the curve, and its three refusals ───────────────────────────────────────

def test_a_real_decay_becomes_a_curve_with_no_recommendation():
    """Excludes the module that grew a suggestion back.

    Named explicitly rather than left implicit: a `suggested` attribute is the
    single most likely thing for a later reader to add, and the reason not to
    is a measurement they would have to go and repeat.
    """
    grid = sweep_grid()
    curve = curve_from_counts(grid, _decay(grid), current=0.2)

    assert curve.declined == ""
    assert len(curve.points) == len(grid)
    assert curve.current == 0.2
    for banned in ("suggested", "suggestion", "recommended", "best", "optimal"):
        assert not hasattr(curve, banned), (
            f"ThresholdCurve grew a {banned!r} — see the module docstring for "
            f"the measurement that says not to")
    text = curve.describe().lower()
    assert "suggest" not in text and "recommend" not in text


def test_a_sweep_that_never_ran_says_so_rather_than_drawing_nothing():
    curve = curve_from_counts([], [])
    assert curve.points == ()
    assert "did not run" in curve.declined


def test_a_frame_with_no_nuclei_anywhere_is_declined_not_drawn_along_zero():
    """Excludes the curve that plots a flat line at zero.

    A line along zero looks like a measurement of a plate whose nuclei all sit
    below the lowest threshold. It is far more often a wrong segmentation
    channel or a blank frame, and the difference is the whole of the user's
    next move.
    """
    grid = sweep_grid(steps=5)
    curve = curve_from_counts(grid, [0] * len(grid))
    assert curve.points == ()
    assert "No nuclei were detected at any threshold" in curve.declined
    assert "segmentation channel" in curve.declined


def test_a_count_that_never_moves_is_declined_with_the_number_named():
    """Excludes the flat line drawn as though it carried information."""
    grid = sweep_grid(steps=7)
    curve = curve_from_counts(grid, [12] * len(grid))
    assert curve.points == ()
    assert "same 12 nuclei" in curve.declined
    assert "says nothing about where the cut should go" in curve.declined


def test_a_single_moving_count_is_a_curve_not_a_refusal():
    """The boundary either side of the flat-curve refusal."""
    curve = curve_from_counts([0.1, 0.2], [9, 8])
    assert curve.declined == ""
    assert curve.counts == (9, 8)


# ── the readout ─────────────────────────────────────────────────────────────

def test_the_readout_states_the_span_and_both_extremes():
    grid = sweep_grid(steps=5)
    curve = curve_from_counts(grid, _decay(grid))
    text = curve.describe()
    assert f"{len(grid)} thresholds" in text
    assert f"{max(curve.counts):,} nuclei down to {min(curve.counts):,}" in text


def test_a_cropped_sweep_says_it_was_cropped():
    """Excludes the readout that presents part of a frame as the frame.

    The crop is the fallback when a full-frame sweep is not affordable, and a
    measurement of a centre crop described as a measurement of the frame is the
    silent substitution CLAUDE.md §7 forbids.
    """
    curve = curve_from_counts([0.1, 0.2], [9, 4], crop_px=512)
    assert "512×512 centre crop" in curve.describe()
    assert "not the whole frame" in curve.describe()


def test_an_uncropped_sweep_makes_no_claim_about_cropping():
    assert "crop" not in curve_from_counts([0.1, 0.2], [9, 4]).describe()


def test_a_declined_curve_describes_its_refusal_and_nothing_else():
    """The refusal sentence is the whole readout — a declined sweep must not
    also print a span it never measured."""
    curve = curve_from_counts([0.1, 0.2], [0, 0])
    assert curve.describe() == curve.declined
    assert "thresholds from" not in curve.describe()


def test_an_empty_curve_built_by_hand_still_refuses_to_claim_a_span():
    """``ThresholdCurve()`` is reachable as a default; it must not raise or
    invent a range when asked to describe itself."""
    assert ThresholdCurve().describe() == ""
