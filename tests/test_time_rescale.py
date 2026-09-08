"""The time-axis label correction (``domain/time_rescale.py``).

The interval multiplier and the fixed offset, together ``filename hours ×
factor + offset``. Pure-logic: no Qt, no app, no dataset. The module is
deliberately tiny — its job is to print a different number, not to change one —
so these tests are mostly about what it refuses to do.
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


# ── a timepoint named in a figure title ─────────────────────────────────────

def test_figure_timepoint_label_is_bare_when_the_factor_is_off():
    assert tr.figure_timepoint_label(1.0, 1) == "t = 1 h"
    assert tr.figure_timepoint_label(0, 1) == "t = 0 h"


def test_figure_timepoint_label_carries_its_own_marker():
    """A scatter figure has no time axis to hang a label on, so the title is the
    only place the timepoint appears and has to say it was rescaled."""
    assert tr.figure_timepoint_label(1.0, 4) == "t = 4 h, filename value × 4"


def test_figure_timepoint_label_agrees_with_the_other_two_helpers():
    """One formatter and one marker phrasing: if these three could drift, a
    figure could name a factor its own ticks did not use."""
    for value, factor in ((1.0, 4), (2.5, 0.5), (0, 4), (3, 1)):
        label = tr.figure_timepoint_label(value, factor)
        assert tr.format_scaled(value, factor) in label
        if tr.is_active(factor):
            assert tr.describe(factor) in label
        else:
            assert "×" not in label


def test_figure_timepoint_label_composes_inside_a_parenthetical():
    """The caller writes ``f"… ({label})"``, so the unit has to stay beside the
    number rather than ending up outside the bracket."""
    title = f"Ctrl — Scatter Cells ({tr.figure_timepoint_label(1.0, 4)})"
    assert title == "Ctrl — Scatter Cells (t = 4 h, filename value × 4)"


def test_figure_timepoint_label_survives_a_non_numeric_value():
    assert tr.figure_timepoint_label("not a number", 4) == ""


def test_figure_timepoint_label_unit_is_overridable():
    assert tr.figure_timepoint_label(1.0, 1, unit="") == "t = 1"


# ── the offset ──────────────────────────────────────────────────────────────
#
# The second half of the correction, and the half that can be wrong in a way the
# first cannot: a factor of one is visibly "off", while an offset silently left
# at zero looks exactly like an offset the user never set.

def test_labels_are_shifted_by_the_offset():
    assert tr.scaled(1, tr.TimeRescale(factor=1, offset=2)) == 3.0
    assert tr.scaled(0, tr.TimeRescale(factor=1, offset=2)) == 2.0


def test_the_offset_is_applied_after_the_multiplier():
    """The interval turns a filename *index* into hours; the offset is a shift
    *in hours*. Applying it first would scale the user's own number out from
    under them — an offset of 2 at a factor of 4 would move the axis by 8."""
    rs = tr.TimeRescale(factor=4, offset=2)
    assert tr.scaled(1, rs) == 6.0          # 1×4 + 2, not (1+2)×4
    assert tr.scaled(3, rs) == 14.0


def test_a_plate_whose_first_image_is_two_hours_in():
    """The reported case for this half: T01…T12 every four hours, with imaging
    starting two hours after the treatment rather than at it."""
    rs = tr.TimeRescale(factor=4, offset=-2)
    labels = [tr.format_scaled(h, rs) for h in range(1, 13)]
    assert labels[0] == "2"
    assert labels[-1] == "46"
    assert labels == [str(4 * h - 2) for h in range(1, 13)]


def test_a_negative_offset_is_ordinary():
    """Unlike the factor, where a negative mirrors the axis, a negative offset
    is the common case — it is what moves a first image off the origin."""
    assert tr.coerce_offset(-2) == -2.0
    assert tr.coerce_offset(-0.5) == -0.5
    assert tr.is_active(tr.TimeRescale(factor=1, offset=-2))
    assert tr.scaled(1, tr.TimeRescale(factor=1, offset=-2)) == -1.0


def test_an_offset_of_zero_is_the_off_state():
    assert not tr.is_active(tr.TimeRescale(factor=1, offset=0))
    assert not tr.is_active(tr.TimeRescale())
    assert tr.describe(tr.TimeRescale(factor=1, offset=0)) == ""
    for base in ("Time (hours)", "Timepoint (h)"):
        assert tr.axis_label(base, tr.TimeRescale(factor=1, offset=0)) == base


def test_an_offset_a_hair_from_zero_is_still_off():
    """Same reason as the factor: it arrives from a text field and round-trips
    through persistence, so a part in a billion is someone typing 0."""
    assert not tr.is_active(tr.TimeRescale(factor=1, offset=1e-12))
    assert not tr.is_active(tr.TimeRescale(factor=1, offset=-1e-12))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 "x", None])
def test_an_unusable_offset_falls_back(bad):
    """Narrower than the factor's rejection list on purpose: only the values
    that are not numbers, or that matplotlib cannot place a tick at."""
    assert tr.coerce_offset(bad, fallback=3.0) == 3.0
    assert tr.coerce_offset(bad) == tr.DEFAULT_OFFSET
    assert not tr.is_active(tr.TimeRescale(factor=1, offset=bad))


def test_an_offset_alone_still_marks_the_axis():
    """An axis shifted by two hours disagrees with the timepoint menus exactly
    as loudly as one multiplied by four, so it needs the marker just as much.
    Unmarked, it is indistinguishable from an axis drawn on real hours — the one
    outcome this module exists to prevent."""
    rs = tr.TimeRescale(factor=1, offset=2)
    assert tr.describe(rs) == "filename value + 2"
    assert tr.axis_label("Time (hours)", rs) == "Time (hours, filename value + 2)"


def test_a_pure_shift_does_not_name_a_multiplier_it_did_not_use():
    """"filename value × 1 + 2" invites the 1 to be read as part of the
    correction. Each term appears only while it is doing something."""
    assert "×" not in tr.describe(tr.TimeRescale(factor=1, offset=2))


def test_the_marker_states_both_terms_when_both_are_in_force():
    assert tr.describe(tr.TimeRescale(factor=4, offset=2)) == \
        "filename value × 4 + 2"


def test_a_negative_offset_reads_as_a_subtraction():
    """The sign is the operator, so the number beside it is never itself signed
    — "+ -2" reads as a typo, and a marker a reader distrusts is no marker."""
    assert tr.describe(tr.TimeRescale(factor=4, offset=-2)) == \
        "filename value × 4 − 2"
    assert "-2" not in tr.describe(tr.TimeRescale(factor=4, offset=-2))


def test_the_marker_states_the_numbers_the_labels_actually_used():
    """A marker naming a different correction than the axis applied would be
    worse than none at all."""
    rs = tr.TimeRescale(factor=4, offset=-2)
    marker = tr.describe(rs)
    assert "4" in marker and "2" in marker
    assert tr.format_scaled(1, rs) == "2"


def test_a_figure_title_carries_the_whole_correction():
    """A scatter figure has no time axis, so the title is the only place the
    timepoint appears — and the only place that can say it was corrected."""
    rs = tr.TimeRescale(factor=4, offset=-2)
    assert tr.figure_timepoint_label(1.0, rs) == "t = 2 h, filename value × 4 − 2"


def test_zero_still_prints_as_zero_under_an_offset():
    """The falsy-zero class again: an offset that lands a timepoint exactly on
    zero must print "0", not a blank tick that reads as a missing one."""
    assert tr.format_scaled(1, tr.TimeRescale(factor=1, offset=-1)) == "0"


def test_a_non_numeric_value_still_formats_as_empty_under_an_offset():
    assert tr.format_scaled("not a number", tr.TimeRescale(factor=4, offset=2)) == ""
    assert math.isnan(tr.scaled(None, tr.TimeRescale(factor=4, offset=2)))


# ── the value that carries both ─────────────────────────────────────────────
#
# One object rather than two arguments, so no call site can pass half a
# correction. `tests/test_time_rescale_is_swept.py` holds the other half of that
# rule at the source level.

def test_a_bare_number_still_reads_as_a_factor_with_no_offset():
    """The pre-offset spelling stays meaningful — it is a real state, and it is
    what the stamp a matplotlib axes carries replays."""
    assert tr.scaled(1, 4) == tr.scaled(1, tr.TimeRescale(factor=4, offset=0))
    assert tr.describe(4) == tr.describe(tr.TimeRescale(factor=4))


def test_the_rescale_value_coerces_what_it_is_built_from():
    """There is no second, unchecked way in: a value built straight from typed
    or persisted input is already safe to format with."""
    assert tr.TimeRescale(factor=0).factor == tr.DEFAULT_FACTOR
    assert tr.TimeRescale(factor=-4).factor == tr.DEFAULT_FACTOR
    assert tr.TimeRescale(offset=float("nan")).offset == tr.DEFAULT_OFFSET
    assert tr.TimeRescale(factor="x", offset="y") == tr.OFF


def test_the_rescale_value_is_frozen():
    """A renderer holding one cannot write back into the sidebar's setting."""
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        tr.TimeRescale(factor=4).factor = 8


def test_rescale_for_reads_both_halves_off_the_state():
    class _Plot:
        time_scale = 4.0
        time_offset = -2.0
    assert tr.rescale_for(_Plot()) == tr.TimeRescale(factor=4, offset=-2)


def test_rescale_for_survives_a_state_object_that_has_neither():
    """It runs from tick formatters and from tests holding a bare stand-in."""
    assert tr.rescale_for(object()) == tr.OFF


# ── the interval the control states ─────────────────────────────────────────
#
# The row is labelled "Interval per timepoint" and used to show the multiplier's
# neutral 1 on every plate, including one imaged every four hours. These pin the
# arithmetic that lets it state the plate's own spacing instead — and, as much,
# the cases where there is no spacing to state, because a number invented for
# those would be the reported bug with a different value.

def test_a_regular_plate_reports_its_own_spacing():
    assert tr.observed_interval([0, 4, 8, 12]) == 4.0
    assert tr.observed_interval([1, 2, 3, 4]) == 1.0
    assert tr.observed_interval([0, 0.5, 1.0]) == 0.5


def test_the_order_of_the_timepoints_does_not_matter():
    """They arrive from a set in ``rebuild_timepoints`` and are sorted there,
    but a caller handing over an unsorted list must not get a negative gap."""
    assert tr.observed_interval([8, 0, 12, 4]) == 4.0


@pytest.mark.parametrize("timepoints,why", [
    ([], "nothing to measure"),
    ([4.0], "one point has no gap"),
    ([0, 4, 8, 24], "the gaps disagree"),
    ([0, 0, 4], "a zero gap is duplicates, not a cadence"),
    ([0, float("nan"), 8], "a non-finite point"),
    (None, "no list at all"),
])
def test_a_plate_with_no_single_interval_says_so(timepoints, why):
    """``None``, never a guess. A mean or a modal gap would always produce a
    number, and on a plate whose last point is overnight that number describes
    no pair of ticks on the axis — degraded output indistinguishable from good,
    which is the rule this repository is built around."""
    assert tr.observed_interval(timepoints) is None, why


def test_a_float_wobble_is_still_a_regular_plate():
    """The hours come from filename parsing and float arithmetic, so exact
    equality would call an ordinary four-hourly plate irregular."""
    assert tr.observed_interval([0, 3.9999999, 8.0]) == pytest.approx(4.0)


def test_the_interval_shown_is_the_one_the_axis_prints():
    """What the control states has to be what a reader gets by subtracting two
    adjacent ticks — otherwise the row is describing a different figure."""
    rescale = tr.TimeRescale(factor=2.0)
    assert tr.displayed_interval([0, 4, 8], rescale) == 8.0
    ticks = [tr.scaled(t, rescale) for t in (0, 4, 8)]
    assert ticks[1] - ticks[0] == 8.0


def test_the_offset_does_not_change_the_interval():
    """It shifts every label by the same amount, so it cancels between two
    adjacent ticks. A row that folded it in would move when the user typed an
    offset, which changes no spacing at all."""
    shifted = tr.TimeRescale(factor=2.0, offset=-6.0)
    assert tr.displayed_interval([0, 4, 8], shifted) == 8.0


def test_typing_an_interval_becomes_the_multiplier_that_prints_it():
    """The round trip the sidebar relies on: the user says how far apart the
    timepoints are, ``PlotState`` goes on storing a factor, and the axis prints
    what was asked for."""
    for timepoints, wanted in (([1, 2, 3], 4.0), ([0, 4, 8], 8.0),
                               ([0, 6, 12], 2.0)):
        factor = tr.factor_for_interval(timepoints, wanted)
        assert tr.displayed_interval(timepoints, tr.TimeRescale(factor=factor)) == (
            pytest.approx(wanted))


def test_typing_the_measured_interval_leaves_the_axis_alone():
    """The case the old behaviour got wrong: on a plate already carrying real
    hours, typing 4 used to multiply by four and draw 0, 16, 32. Typing what is
    already true must be a no-op."""
    factor = tr.factor_for_interval([0, 4, 8, 12], 4.0)
    assert factor == pytest.approx(tr.DEFAULT_FACTOR)
    assert not tr.is_active(tr.TimeRescale(factor=factor))


def test_a_plate_with_no_interval_reads_a_typed_number_as_the_multiplier():
    """The fallback is the behaviour the control has always had, so the plate
    that cannot state an interval keeps working rather than dividing by a
    number that does not exist."""
    assert tr.factor_for_interval([0, 4, 8, 24], 8.0) == 8.0
    assert tr.factor_for_interval([], 8.0) == 8.0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), "x", None])
def test_a_factor_that_would_collapse_the_axis_is_refused(bad):
    """``coerce_factor``'s rule, reached through the interval door: a zero or
    negative interval collapses or mirrors the axis, and a control that took one
    would leave the user looking at a plot that cannot be right."""
    assert tr.factor_for_interval([0, 4, 8], bad, fallback=3.0) == 3.0
