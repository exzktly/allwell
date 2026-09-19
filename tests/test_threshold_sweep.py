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


# ── a stopped sweep must not assert a finding it did not earn ───────────────

def test_a_stopped_sweep_says_so_and_says_so_far():
    """Excludes the title that reports a fragment as a finding.

    With the axes rescaled to the eight points it got and a title reading
    "400 nuclei down to 397", a sweep abandoned before the knee asserts that
    the count barely moves on this frame — which is exactly the conclusion a
    scientist would act on, and an artifact of having stopped. The complete
    sweep of the same frame goes to zero.
    """
    grid = sweep_grid()
    curve = curve_from_counts(grid[:8], [400, 400, 400, 399, 399, 398, 398, 397],
                              requested=len(grid),
                              requested_span=(grid[0], grid[-1]))
    assert curve.truncated is True
    text = curve.describe()
    assert f"Stopped after 8 of {len(grid)}" in text
    assert "so far" in text, "without it the sentence is a finding, not a report"


def test_a_complete_sweep_is_not_marked_truncated():
    grid = sweep_grid(steps=5)
    curve = curve_from_counts(grid, _decay(grid), requested=len(grid))
    assert curve.truncated is False
    assert "Stopped" not in curve.describe()


def test_a_flat_run_cut_short_is_scoped_rather_than_ruled_on():
    """Excludes the verdict drawn from a fragment of the range.

    "Every threshold detected the same 400 nuclei, so this frame says nothing
    about where the cut should go" is a statement about the frame. Derived
    from the three points a user got before pressing Stop — entirely likely,
    since the first sweep of a session also loads TensorFlow — it is wrong.
    """
    grid = sweep_grid()
    curve = curve_from_counts(grid[:3], [400] * 3, requested=len(grid))
    assert "before you stopped" in curve.declined
    assert "The rest of the range was not measured." in curve.declined
    assert "says nothing about where the cut should go" not in curve.declined


def test_a_frame_with_no_nuclei_scopes_its_refusal_when_cut_short():
    grid = sweep_grid()
    full = curve_from_counts(grid, [0] * len(grid), requested=len(grid))
    part = curve_from_counts(grid[:3], [0] * 3, requested=len(grid))
    assert "at any threshold in the sweep" in full.declined
    assert "before you stopped" in part.declined


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
