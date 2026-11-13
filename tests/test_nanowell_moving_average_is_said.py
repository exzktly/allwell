"""A Nanowells figure drawn from moving averages says so on its top-left line, beside the subset's name.

Reported: "if a plot is composed of data made of moving averages, it must say so; can include on the same line as
the subset name is written". The full sentence (``moving_compute.describe``) was in the footer only — which the page
folds into its Details field, so a figure saved or copied from the page carried no trace of it, and the Statistics
view, whose samples are the same averaged values, did not write it at all. An averaged value draws exactly like a
measured one (CLAUDE.md §7), so the figure itself must say which it is.

Driven through the page's real controls (``_nw_moving_spin``, ``_nw_moving_kind_cb``) and ``controller.redraw``,
so a view that forgets the tag, a control that does not reach it, or a stamp that drops it all fail here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import moving_compute as mvc  # noqa: E402
from review.features.nanowell import subset_compute as ss  # noqa: E402
from tests.test_nanowell_subset import C, _stamp, page  # noqa: E402,F401


def test_the_tag_names_the_window_its_kind_and_a_baseline_s_own_window():
    assert mvc.figure_tag(0) == mvc.figure_tag(1) == ""
    assert mvc.figure_tag(3, "n") == "Moving average over 3 timepoints (trailing)"
    assert mvc.figure_tag(4, "n", centred=True) == "Moving average over 3 timepoints (centred)"   # odd only
    # A fold change from a named timepoint, its baseline averaged over 2: said; from Earliest it cannot be, so not.
    assert mvc.figure_tag(3, "count_fc", "t_b", 2) == "Moving average over 3 timepoints (trailing); baseline over 2"
    assert mvc.figure_tag(3, "count_fc", "earliest", 2) == "Moving average over 3 timepoints (trailing)"
    assert mvc.figure_tag(3, "n", "t_b", 2) == "Moving average over 3 timepoints (trailing)"   # no baseline to average


def _set_moving(ctx, window, centred=False):
    ctx.widgets.get("_nw_moving_kind_cb").setCurrentIndex(ctx.widgets.get("_nw_moving_kind_cb").findData(centred))
    ctx.widgets.get("_nw_moving_spin").setValue(window)


@pytest.mark.parametrize("view", ["distribution", "trajectories", "statistics"])
def test_a_view_drawn_from_moving_averages_says_so_with_and_without_a_subset(page, view):  # noqa: F811
    st = page.state.feature_slice("nanowell")
    st.view, st.metric = view, "n"
    _set_moving(page, 3)
    assert st.moving == 3                                         # the control reached the state
    nwc.redraw(page)
    assert _stamp(page) == ["Moving average over 3 timepoints (trailing)"]
    nwc.set_subset(page, ss.Subset((C("n", "ge", 2, at="tp:t_a"),)).validated())
    assert nwc.save_subset(page, "Big") == ""
    nwc.redraw(page)
    assert _stamp(page) == ["Subset: “Big” · Moving average over 3 timepoints (trailing)"]
    _set_moving(page, 3, centred=True)
    nwc.redraw(page)
    assert _stamp(page) == ["Subset: “Big” · Moving average over 3 timepoints (centred)"]
    _set_moving(page, 1)                                          # off: the subset alone, as before
    nwc.redraw(page)
    assert _stamp(page) == ["Subset: “Big”"]


@pytest.mark.parametrize("view", ["position", "array", "extremes"])
def test_a_view_the_moving_average_does_not_reach_does_not_claim_it(page, view):  # noqa: F811
    st = page.state.feature_slice("nanowell")
    st.view, st.metric = view, "n"
    _set_moving(page, 3)
    nwc.redraw(page)
    assert _stamp(page) == []


def test_a_metric_with_one_value_per_nanowell_is_not_averaged_and_does_not_say_it_is(page):  # noqa: F811
    st = page.state.feature_slice("nanowell")
    st.view, st.metric = "distribution", "occupancy_class"
    _set_moving(page, 3)
    nwc.redraw(page)
    assert _stamp(page) == []
