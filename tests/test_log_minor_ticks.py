"""A log axis carries the standard subdivisions between its labelled powers.

Without them a log axis is a pair of labelled powers with an unmarked gap, and
a reader cannot tell 2 from 5 inside it. Every plotting package draws them for
that reason; this app drew none, on any tab, because the only thing that put
minor ticks anywhere was the Properties *Minor ticks* row — which is a
preference about tick density on a linear axis, and is off by default.

So they are installed where the scale is set (``apply_axis_log_scale``), which
is the one route every tab's log axis goes through, and the Properties row goes
on governing the linear axes beside them.

Two limits are worth stating because they are matplotlib's own and this file
would otherwise look like it was over-promising:

* the subdivisions appear only while every power is labelled. Once the view is
  wide enough that matplotlib thins the majors — every other decade on a
  six-decade axis — it draws no subs at all, and it is right not to: ticks
  between two *unlabelled* powers say nothing a reader can use.
* a base too small to contain a whole multiple (2, and *e*) has no "2, 3, … 9"
  to draw, so it gets its log-space midpoint instead.
"""

from __future__ import annotations

import math

import pytest

from well_viewer.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS, apply_axis_log_scale, apply_export_style_prefs,
    log_minor_subs,
)


def _fig():
    from matplotlib.figure import Figure

    fig = Figure()
    return fig, fig.add_subplot(1, 1, 1)


def _minor(ax, which="x"):
    ax.figure.canvas_none = None
    ax.figure.draw_without_rendering()
    return [round(float(t), 6) for t in ax.get_xticks(minor=True)] if which == "x" \
        else [round(float(t), 6) for t in ax.get_yticks(minor=True)]


# ── where the subdivisions go ────────────────────────────────────────────────

def test_base_ten_subdivides_at_the_standard_two_through_nine():
    """The value, not "there are some": a locator with the wrong subs still
    produces ticks, and the whole point is *which* ticks."""
    assert log_minor_subs(10.0) == (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)


def test_a_whole_base_subdivides_at_its_whole_multiples():
    assert log_minor_subs(3.0) == (2.0,)
    assert log_minor_subs(5.0) == (2.0, 3.0, 4.0)


def test_base_two_has_no_whole_multiple_inside_it_and_takes_its_midpoint():
    """Base 2 is the only offered base with nothing whole between 1 and itself.

    Returning nothing would leave exactly the base most in need of a mark
    between its labels with none — a base-2 axis labels every doubling and is
    otherwise the emptiest of the lot. ``e`` is *not* this case, however close
    it looks: 2 lies strictly inside it, so it subdivides at 2 like any other
    base. Written out because it was assumed the other way here first.
    """
    subs = log_minor_subs(2.0)
    assert subs == pytest.approx((math.sqrt(2.0),))
    assert 1.0 < subs[0] < 2.0
    assert log_minor_subs(math.e) == (2.0,)


def test_every_base_the_panel_offers_actually_subdivides():
    """The list is a promise: a base one click away that draws no subdivisions
    would be a control that half-works, and only the offered ones are reachable
    without typing. Driven from ``LOG_BASE_CHOICES`` rather than from a list
    written here, so a base added to the panel is covered by having been added.
    """
    from well_viewer.core.plot_style import LOG_BASE_CHOICES, log_base_value

    for text in LOG_BASE_CHOICES:
        base = log_base_value(text)
        subs = log_minor_subs(base)
        assert subs, f"base {text!r} offers no subdivision"
        assert all(1.0 < float(sub) < base for sub in subs), (text, base, subs)


def test_the_offered_bases_are_the_ones_worth_a_click():
    """Pinned as the list, because the field is editable and the list is the
    only part that is a decision — anything else is typed."""
    from well_viewer.core.plot_style import LOG_BASE_CHOICES

    assert LOG_BASE_CHOICES == ("10", "2", "3", "e")


def test_base_three_puts_a_tick_between_each_pair_of_powers():
    """The base added last, measured on the axes rather than in the resolver:
    3 has exactly one whole multiple inside it, so each step from 3^k to 3^(k+1)
    carries a single mark at twice the lower power."""
    fig, ax = _fig()
    ax.plot([1, 9], [1, 2])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    prefs["x_log_base"] = "3"
    apply_export_style_prefs(fig, prefs)
    ticks = [t for t in _minor(ax) if 1.0 <= t <= 9.0]
    assert ticks == pytest.approx([2.0, 6.0]), ticks


def test_a_base_that_is_not_a_base_falls_back_to_ten():
    """The field is editable, so this is reachable from a typo rather than only
    from a bug — and a locator built on base 1 is a division by zero."""
    for junk in (1.0, 0.0, -3.0, float("nan")):
        assert log_minor_subs(junk) == log_minor_subs(10.0)


# ── and onto the axes ────────────────────────────────────────────────────────

def test_a_log_axis_carries_them_with_the_minor_tick_row_off():
    """The row is off by default, which is why there were none of these at all.

    Asserted as the decade's own subdivisions rather than as "the list is not
    empty", so a locator left on matplotlib's linear ``AutoMinorLocator`` — which
    also produces ticks — could not pass.
    """
    fig, ax = _fig()
    ax.plot([1, 10], [1, 2])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    assert prefs["tick_minor"] is False, "the default changed; this test is moot"
    apply_export_style_prefs(fig, prefs)
    ticks = [t for t in _minor(ax) if 1.0 < t < 10.0]
    assert ticks == pytest.approx([2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])


def test_a_linear_axis_still_obeys_the_minor_tick_row():
    """The row was not repurposed. A log axis keeps its subdivisions whatever it
    says, and everything else is exactly as it was — otherwise this would be a
    control that stopped working, which is the defect the change is meant to
    avoid rather than cause."""
    fig, ax = _fig()
    ax.plot([1, 10], [1, 2])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["tick_minor"] = False
    apply_export_style_prefs(fig, prefs)
    assert _minor(ax) == []

    prefs["tick_minor"] = True
    apply_export_style_prefs(fig, prefs)
    assert _minor(ax), "the Minor ticks row stopped putting ticks on a linear axis"


def test_the_y_axis_is_governed_independently_of_the_x():
    """A figure with a log y and a linear x must not get x subdivisions from
    the y's scale — ``minorticks_on``/``off`` act on the whole axes, so the two
    were one decision until this split them."""
    fig, ax = _fig()
    ax.plot([1, 10], [1, 100])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["y_log"] = True
    apply_export_style_prefs(fig, prefs)
    assert [t for t in _minor(ax, "y") if 1.0 < t < 100.0], "no y subdivisions"
    assert _minor(ax, "x") == [], "the linear x axis picked up minor ticks"


def test_they_survive_the_restyle_that_runs_after_every_redraw():
    """``minorticks_off`` installs a ``NullLocator``, so a renderer that set the
    scale itself and a restyle that then cleared it would leave the axis bare —
    which is the shape of every "applied then silently removed" defect on this
    figure so far."""
    fig, ax = _fig()
    ax.plot([1, 10], [1, 2])
    apply_axis_log_scale(ax, "x", True, 10.0)
    before = [t for t in _minor(ax) if 1.0 < t < 10.0]
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    apply_export_style_prefs(fig, prefs)
    assert [t for t in _minor(ax) if 1.0 < t < 10.0] == pytest.approx(before)


def test_they_are_unlabelled():
    """Nine numbers per decade would crowd the axis past legibility, which is
    why matplotlib labels none of them either. The tick is a ruler, not a
    reading."""
    fig, ax = _fig()
    ax.plot([1, 10], [1, 2])
    apply_axis_log_scale(ax, "x", True, 10.0)
    ax.figure.draw_without_rendering()
    assert all(not t.get_text() for t in ax.get_xticklabels(minor=True))


def test_a_thinned_axis_draws_none_and_that_is_matplotlib_s_rule():
    """Stated rather than worked around: once the majors are every *other*
    power, a tick between two unlabelled powers says nothing a reader can use.

    Here so that the limit is a decision on the record — a later reader finding
    a six-decade axis with no subdivisions should find this rather than a bug
    report.
    """
    fig, ax = _fig()
    ax.plot([1, 1e6], [1, 2])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    apply_export_style_prefs(fig, prefs)
    majors = [t for t in ax.get_xticks() if 1.0 <= t <= 1e6]
    assert len(majors) < 7, f"matplotlib stopped thinning; re-measure: {majors}"
    assert _minor(ax) == []


# ── an axis whose majors are not the powers ──────────────────────────────────
#
# Reported as "there are not a constant number of minor ticks between major
# ticks on the x axis", in base 10, and the number is the whole diagnosis. A
# subdivision is defined against the power it sits under, so it can only be
# evenly spaced between *powers*. Put decade subdivisions under a set of majors
# that are something else and the counts between labels differ by construction:
# a 0.1 / 0.3 / 1 / 3 / 10 dose axis measured 1, 6, 1, 6.

_DOSES = (0.1, 0.3, 1.0, 3.0, 10.0)


def _response_axis():
    """The Response tab's dose axis, drawn by its own renderer.

    Through the real renderer rather than by hand-setting fixed ticks: the tab
    both pins the ticks *and* clears the minors, and a fixture that only did
    the first would be measuring an axis the app never draws."""
    from types import SimpleNamespace

    from well_viewer.features.response.collect import (
        ResponseCurve, ResponseData, ResponsePoint,
    )
    from well_viewer.features.response.renderer import draw_response

    fig, ax = _fig()
    points = [ResponsePoint(x=x, y=1.0 + i, err=float("nan"), n=3,
                            member=f"m{i}")
              for i, x in enumerate(_DOSES)]
    data = ResponseData(variable_name="Dose",
                        curves=[ResponseCurve(timepoint=0.0, series_id="",
                                              points=points)])
    draw_response(SimpleNamespace(), ax, data, log_x=True, log_base=10.0)
    return fig, ax


def _minors_between_majors(ax) -> list[int]:
    """How many minor ticks fall between each adjacent pair of major ticks."""
    ax.figure.draw_without_rendering()
    lo, hi = ax.get_xlim()
    majors = [float(t) for t in ax.xaxis.get_majorticklocs() if lo <= t <= hi]
    minors = [float(t) for t in ax.xaxis.get_minorticklocs() if lo <= t <= hi]
    return [sum(1 for m in minors if a < m < b)
            for a, b in zip(majors, majors[1:])]


def test_the_restyle_leaves_a_dose_axis_unsubdivided():
    """The reported case, measured end to end.

    The renderer clears the minors and the restyle put them straight back, so
    the assertion is made *after* the restyle — which is the only place the
    defect was ever visible. The majors are checked too: an implementation that
    fixed the counts by throwing away the dose ticks would satisfy a bare "no
    minor ticks" and would have broken the tab this axis belongs to.
    """
    fig, ax = _response_axis()
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    prefs["x_log_base"] = "10"
    apply_export_style_prefs(fig, prefs)
    lo, hi = ax.get_xlim()
    majors = [round(float(t), 6) for t in ax.xaxis.get_majorticklocs()
              if lo <= t <= hi]
    assert majors == pytest.approx(list(_DOSES)), (
        "the dose ticks are gone, so this axis is no longer the one reported")
    assert _minors_between_majors(ax) == [0, 0, 0, 0]


def test_a_decade_axis_still_carries_eight_between_every_pair():
    """The other half of the same rule, and the reason it is a rule about the
    *majors* rather than about the Response tab. Counted per pair rather than
    in total: the report was about the counts differing, and a total of 24 is
    equally consistent with 8, 8, 8 and with 1, 6, 17."""
    fig, ax = _fig()
    ax.plot([1, 1000], [1, 2])
    ax.set_xlim(1, 1000)
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    apply_export_style_prefs(fig, prefs)
    assert _minors_between_majors(ax) == [8, 8, 8]


def test_a_subdivision_set_left_over_from_an_earlier_draw_is_taken_away():
    """Declining to install is not enough on its own. The locator installed by
    the previous call outlives the redraw that pinned the new majors, so an
    axis that becomes dose-ticked would keep the decades it had before."""
    from matplotlib.ticker import LogLocator

    fig, ax = _fig()
    ax.plot([1, 100], [1, 2])
    apply_axis_log_scale(ax, "x", True, 10.0)
    assert isinstance(ax.xaxis.get_minor_locator(), LogLocator), (
        "the fixture never installed the subdivisions it is about to check for")
    ax.set_xticks([1.0, 3.0, 100.0])
    apply_axis_log_scale(ax, "x", True, 10.0)
    assert _minor(ax) == []


def test_a_minor_locator_this_module_did_not_install_is_left_alone():
    """It removes only what it puts there. A renderer that chose its own minor
    ticks on a log axis owns them — taking those away would be this same defect
    again, one layer down."""
    from matplotlib.ticker import FixedLocator

    fig, ax = _fig()
    ax.plot([1, 100], [1, 2])
    apply_axis_log_scale(ax, "x", True, 10.0)
    ax.set_xticks([1.0, 3.0, 100.0])
    ax.xaxis.set_minor_locator(FixedLocator([2.0, 50.0]))
    apply_axis_log_scale(ax, "x", True, 10.0)
    assert _minor(ax) == pytest.approx([2.0, 50.0])
