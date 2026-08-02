"""The time-axis label multiplier (``domain/time_rescale.py``).

Pure-logic: no Qt, no app, no dataset. The module is deliberately tiny — its job
is to print a different number, not to change one — so these tests are mostly
about what it refuses to do.
"""

from __future__ import annotations

import math

import pytest

from well_viewer.domain import time_rescale as tr


# ── the multiplication ──────────────────────────────────────────────────────

def test_labels_are_the_filename_value_times_the_factor():
    assert tr.scaled(1, 4) == 4.0
    assert tr.scaled(3, 4) == 12.0
    assert tr.scaled(0, 4) == 0.0


def test_a_t01_to_t12_plate_at_four_hours_reads_four_to_forty_eight():
    """The reported case: files named T01…T12, imaged every four hours."""
    labels = [tr.format_scaled(h, 4) for h in range(1, 13)]
    assert labels[0] == "4"
    assert labels[-1] == "48"
    assert labels == [str(4 * h) for h in range(1, 13)]


def test_fractional_factors_work_both_directions():
    assert tr.scaled(4, 0.5) == 2.0
    assert tr.format_scaled(1, 1 / 3) == "0.333333"


def test_formatting_goes_through_the_canonical_key_form():
    """A rescaled tick must read like every other timepoint in the app."""
    from well_viewer.data.image_identity import normalize_tp
    for value, factor in ((1, 4), (2.5, 2), (0, 4), (7, 1 / 12)):
        assert tr.format_scaled(value, factor) == normalize_tp(value * factor)


def test_zero_still_prints_as_zero():
    """Regression against the falsy-zero class: the baseline timepoint is a
    timepoint, and a blank first tick would read as a missing one."""
    assert tr.format_scaled(0, 4) == "0"
    assert tr.format_scaled(0.0, 1) == "0"


# ── what it refuses ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [0, -1, -0.5, float("nan"), float("inf"), "x", None])
def test_an_unusable_factor_falls_back_instead_of_drawing_a_broken_axis(bad):
    """Zero collapses the axis, a negative one mirrors it, and neither is what
    anyone typed on purpose. The last good factor stays on screen so the caller
    can report what it ignored."""
    assert tr.coerce_factor(bad, fallback=4.0) == 4.0
    assert tr.coerce_factor(bad) == tr.DEFAULT_FACTOR


def test_a_usable_factor_is_taken_as_given():
    for good in (0.5, 1, 4, 60, 1 / 12):
        assert tr.coerce_factor(good) == pytest.approx(float(good))


def test_non_numeric_values_format_as_empty_rather_than_raising():
    """This runs inside a matplotlib tick formatter, where an exception takes
    the whole figure down rather than one label."""
    assert tr.format_scaled("not a number", 4) == ""
    assert tr.format_scaled(None, 4) == ""
    assert math.isnan(tr.scaled("not a number", 4))


# ── the off state ───────────────────────────────────────────────────────────

def test_a_factor_of_one_is_the_off_state():
    assert not tr.is_active(1)
    assert not tr.is_active(1.0)
    assert not tr.is_active(tr.DEFAULT_FACTOR)
    assert tr.is_active(4)


def test_a_factor_a_hair_from_one_is_still_off():
    """It arrives from a text field and round-trips through persistence; a part
    in ten million is someone typing 1, not a request to shift every label."""
    assert not tr.is_active(0.9999999999)
    assert not tr.is_active(1.0000000001)


def test_an_unusable_factor_is_not_active():
    for bad in (0, -4, float("nan"), "x", None):
        assert not tr.is_active(bad)


def test_labels_are_untouched_when_the_factor_is_off():
    for h in (0, 1, 2.5, 24):
        assert tr.format_scaled(h, 1) == tr.format_scaled(h, tr.DEFAULT_FACTOR)
        assert tr.scaled(h, 1) == float(h)


# ── the marker, which is not optional ───────────────────────────────────────

def test_a_rescaled_axis_says_so():
    """The numbers on a rescaled axis disagree, deliberately, with the timepoint
    menus and with every exported CSV. The label is the only place on the figure
    that says why, so an unmarked rescaled axis is the one outcome this feature
    must not produce."""
    assert tr.axis_label("Time (hours)", 4) == "Time (hours, filename value × 4)"
    assert tr.axis_label("Timepoint (h)", 0.5) == "Timepoint (h, filename value × 0.5)"


def test_a_label_without_brackets_gains_its_own():
    assert tr.axis_label("Time", 4) == "Time (filename value × 4)"


def test_an_inactive_factor_leaves_the_label_byte_identical():
    for base in ("Time (hours)", "Timepoint (h)", "Time"):
        assert tr.axis_label(base, 1) == base
        assert tr.axis_label(base, tr.DEFAULT_FACTOR) == base
        assert tr.axis_label(base, 0) == base          # rejected factor, no marker


def test_describe_is_empty_when_nothing_changed():
    assert tr.describe(1) == ""
    assert tr.describe(4) == "filename value × 4"


def test_the_marker_states_the_factor_the_labels_actually_used():
    """A marker naming a different number than the axis used would be worse than
    none at all."""
    factor = 1 / 12
    assert tr.describe(factor).endswith(tr.format_scaled(1, factor))
