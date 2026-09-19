"""The per-cell focus distribution: the evidence, and the refusal to summarise it.

Two things this file exists to hold. The first is arithmetic — a percentile, a
count either side of a cut — and it is easy. The second is the harder one: the
module's whole contribution is that it **does not** recommend a threshold, and
"does not do something" is the property most likely to be quietly undone by a
later change that looks like an improvement.

So the refusal is asserted against the figure, against the sentence, and
against the module's own surface, and §D.2d's measurement is quoted where each
one lives, because a rule with no reason attached is one nobody can defend
against the next person who thinks Otsu would be nice here.
"""
from __future__ import annotations

import math

import pytest

from review.domain.focus_distribution import (
    NO_CELLS,
    NO_RECOMMENDATION,
    NOTHING_MEASURED,
    FocusDistribution,
    distribution_from_values,
)


# ── construction, and the three states with nothing to draw ─────────────────

def test_values_come_back_sorted_whatever_order_they_arrived_in():
    """The percentile helpers and the cumulative curve both index this tuple,
    so an unsorted one would let them disagree while both looked right."""
    d = distribution_from_values([0.3, 0.1, 0.2])
    assert d.values == (0.1, 0.2, 0.3)


def test_unmeasurable_cells_are_counted_rather_than_filtered_away():
    """"Forty cells, six unmeasurable" and "thirty-four cells" are different
    statements about a frame and only the first is true. Excludes the
    comprehension that drops them."""
    d = distribution_from_values([0.3, None, 0.2, None])
    assert d.values == (0.2, 0.3)
    assert d.unmeasurable == 2
    assert d.total == 4
    assert d.measurable == 2


@pytest.mark.parametrize("blank", [None, float("nan"), float("inf"), "x"])
def test_every_spelling_of_no_value_counts_as_unmeasurable(blank):
    """``None`` from a list, NaN from a pandas column, and a string from a CSV
    read without coercion all mean the same thing here. Excludes the version
    that handles only the one the author happened to test with."""
    d = distribution_from_values([0.3, blank])
    assert d.unmeasurable == 1
    assert d.values == (0.3,)


def test_a_frame_with_no_cells_declines_with_its_own_reason():
    d = distribution_from_values([])
    assert d.declined == NO_CELLS
    assert d.values == ()


def test_a_frame_where_nothing_could_be_measured_declines_differently():
    """A frame with cells whose outlines all failed is a **finding**, not an
    empty result — an outline needs contrast and pixels either side, which is
    what severe defocus destroys. Excludes collapsing it into "no cells"."""
    d = distribution_from_values([None, None, None])
    assert d.declined == NOTHING_MEASURED
    assert d.declined != NO_CELLS
    assert d.unmeasurable == 3


def test_the_callers_own_refusal_wins_over_anything_derived():
    """The caller knows why it did not run; this module can only see that
    nothing arrived."""
    d = distribution_from_values([], declined="Segment the frame first.")
    assert d.declined == "Segment the frame first."


# ── the cut, and what it does ───────────────────────────────────────────────

def test_kept_and_dropped_split_at_the_cut_inclusively():
    """At the cut is kept, below it is dropped — the same inequality the gate
    applies, asserted here so the plot cannot promise what the filter will not
    do. Excludes the comparison written the other way round, which a test
    using only clearly-separated values cannot see."""
    d = distribution_from_values([0.09, 0.10, 0.11], cut=0.10)
    assert (d.kept, d.dropped) == (2, 1)


def test_the_unmeasurable_are_not_counted_as_dropped():
    """They are **kept**, and folding them into ``dropped`` would tell a
    reader the gate removed cells nobody could judge. Excludes the
    implementation that derives ``dropped`` from ``total``."""
    d = distribution_from_values([0.05, 0.30, None, None], cut=0.10)
    assert d.dropped == 1
    assert d.kept == 1
    assert d.total == 4


def test_without_a_cut_every_measurable_cell_is_kept():
    """Not zero, and not "all four": the absence of a cut excludes nothing and
    says nothing about the two cells with no value."""
    d = distribution_from_values([0.05, 0.30, None], cut=None)
    assert (d.kept, d.dropped) == (2, 0)


def test_the_percentile_interpolates_rather_than_picking_a_neighbour():
    """The cumulative curve is drawn by linear interpolation, so this must be
    too or the line and the number under it disagree."""
    d = distribution_from_values([0.0, 1.0, 2.0, 3.0])
    assert d.percentile(50.0) == pytest.approx(1.5)
    assert d.percentile(0.0) == pytest.approx(0.0)
    assert d.percentile(100.0) == pytest.approx(3.0)


def test_a_single_value_has_a_percentile_rather_than_an_index_error():
    d = distribution_from_values([0.4])
    assert d.percentile(50.0) == pytest.approx(0.4)


def test_a_distribution_nobody_has_built_says_nothing_rather_than_raising():
    """The dataclass's defaults make this state reachable, and a readout that
    raises on first paint is a worse failure than one that says nothing."""
    assert FocusDistribution().describe() == ""


# ── the sentence ────────────────────────────────────────────────────────────

def test_the_sentence_names_the_consequence_of_the_current_cut():
    d = distribution_from_values([0.05, 0.20, 0.30, 0.40], cut=0.10)
    text = d.describe()
    assert "4 of 4 cells measured" in text
    assert "At 0.1: 3 kept, 1 excluded." in text


def test_the_sentence_says_the_unmeasurable_are_kept():
    """Excludes the sentence that reports only what the cut removes, which
    leaves a reader believing the frame has been cleaned."""
    d = distribution_from_values([0.05, 0.30, None, None], cut=0.10)
    text = d.describe()
    assert "2 cells carry no value and are kept whatever the cut is" in text


def test_the_singular_sentence_agrees_with_itself():
    d = distribution_from_values([0.30, None], cut=0.10)
    text = d.describe()
    assert "1 cell carries no value and is kept" in text
    assert "cell carry" not in text and "cell are" not in text


def test_a_frame_with_no_cut_says_so_rather_than_implying_one():
    d = distribution_from_values([0.05, 0.30])
    assert d.describe().endswith("No cut set.")


def test_a_declined_distribution_says_why_instead_of_reporting_numbers():
    d = distribution_from_values([None, None])
    assert d.describe() == NOTHING_MEASURED


# ── the refusal ─────────────────────────────────────────────────────────────

def test_the_module_offers_no_way_to_ask_for_a_recommendation():
    """**The property this module is for**, asserted against its surface.

    §D.2d measured what a recommendation would do: on a clean plate Otsu's cut
    discards 39.5 % of good cells, and on a uniformly defocused one it keeps
    76 % of the bad ones, with its own separability unable to tell either case
    from the useful ones. A later change that adds ``suggest_threshold`` would
    otherwise pass every other test in this file.
    """
    import review.domain.focus_distribution as mod

    offered = [n for n in dir(mod) if not n.startswith("_")]
    forbidden = ("otsu", "suggest", "recommend", "optimal", "best", "auto")
    assert not [n for n in offered
                if any(w in n.lower() for w in forbidden)
                # The constant that *states* the refusal is the exception, and
                # it is named so it can be quoted rather than re-composed.
                and n != "NO_RECOMMENDATION"], offered


def test_the_refusal_says_it_was_measured_rather_than_that_it_is_cautious():
    """A refusal with no reason reads as timidity and is the first thing a
    later reader removes. These are the two numbers from §D.2d."""
    assert "two fifths" in NO_RECOMMENDATION
    assert "three quarters" in NO_RECOMMENDATION
    assert "measured" in NO_RECOMMENDATION


# ── the figure ──────────────────────────────────────────────────────────────

def _fig():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    return Figure(figsize=(6, 4))


def test_the_curve_that_is_drawn_is_the_cumulative_share():
    """§7.4 #1: assert what was **drawn**, not what was computed. The values
    are deliberately uneven so a line drawn from an index rather than from the
    values themselves would show."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    d = distribution_from_values([0.1, 0.4, 0.9, 0.95], cut=0.5)
    fig = _fig()
    draw_focus_distribution(fig, d)
    ax = fig.axes[0]
    line = next(ln for ln in ax.lines if len(ln.get_xdata()) == 4)
    assert list(line.get_xdata()) == [0.1, 0.4, 0.9, 0.95]
    assert list(line.get_ydata()) == [25.0, 50.0, 75.0, 100.0]


def test_the_users_cut_is_drawn_and_no_other_vertical_is():
    """The one mark on this plot is where the user is. Excludes a later
    version that adds a suggested-threshold line — which is the specific
    change §D.2d exists to prevent, and which every numeric test here would
    pass."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    d = distribution_from_values([0.1, 0.4, 0.9], cut=0.5)
    fig = _fig()
    draw_focus_distribution(fig, d)
    ax = fig.axes[0]
    verticals = [ln for ln in ax.lines
                 if len(set(ln.get_xdata())) == 1 and len(ln.get_xdata()) > 1]
    assert len(verticals) == 1
    assert verticals[0].get_xdata()[0] == pytest.approx(0.5)


def test_no_cut_draws_no_vertical_at_all():
    """Excludes the version that marks the median, or a percentile, as a
    stand-in for a cut the user has not chosen."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    fig = _fig()
    draw_focus_distribution(fig, distribution_from_values([0.1, 0.4, 0.9]))
    ax = fig.axes[0]
    verticals = [ln for ln in ax.lines
                 if len(set(ln.get_xdata())) == 1 and len(ln.get_xdata()) > 1]
    assert verticals == []


def test_the_refusal_is_on_the_figure_rather_than_only_in_a_tooltip():
    """The plot is savable, and a reader who does not know the absence of a
    recommendation was measured will read it as an omission."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    fig = _fig()
    draw_focus_distribution(fig, distribution_from_values([0.1, 0.4], cut=0.2))
    texts = " ".join(t.get_text() for t in fig.axes[0].texts)
    assert "two fifths" in texts


def test_a_declined_distribution_draws_its_reason_rather_than_empty_axes():
    """The refusal IS the figure. Excludes the blank plot, and the no-window
    path — the user pressed a button and is owed an answer."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    fig = _fig()
    draw_focus_distribution(fig, distribution_from_values([None, None]))
    ax = fig.axes[0]
    assert len(ax.lines) == 0
    # The reason itself, not a phrase from it: a substring test passes against
    # a figure that drew some *other* refusal.
    assert any(t.get_text() == NOTHING_MEASURED for t in ax.texts)


def test_the_figure_names_the_frame_it_came_from():
    """Three consecutive runs repaint one window, and it has a Save button —
    without this they leave for a notebook with no attribution."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    d = distribution_from_values([0.1, 0.4], cut=0.2,
                                 subject="DAPI · B03 · FOV 2 · live StarDist")
    fig = _fig()
    draw_focus_distribution(fig, d)
    assert "B03" in fig.axes[0].get_title()
    assert "live StarDist" in fig.axes[0].get_title()


def test_the_axis_spans_the_whole_measured_range_without_a_nan():
    """A cheap smoke over the arithmetic the plot does inline, since a NaN
    anywhere here renders as an empty panel rather than as an error."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )

    fig = _fig()
    draw_focus_distribution(fig, distribution_from_values([0.1, 0.4, 0.9],
                                                          cut=0.2))
    lo, hi = fig.axes[0].get_xlim()
    assert not math.isnan(lo) and not math.isnan(hi)
    assert lo <= 0.1 and hi >= 0.9
