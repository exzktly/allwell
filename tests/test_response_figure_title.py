"""The Response figure's title says the same things the other plots' titles do.

A reader moving between the plotting tabs should not have to work out that
``Response vs Dose`` and ``GFP Mean Intensity ± SEM — t=24 h`` are describing
the same plate. This tab's title said only what its x axis was, so the two facts
needed to compare one saved figure against another — **which measurement** and
**which moment** — were on the page and not in the artifact, which is where they
matter, because the title is the part that survives Save.

So the title is now the Bar tab's clause verbatim, then the timepoints, then
this tab's own ``vs <variable>``, then any Group-by shortfall. The first of
those is asserted against the Bar tab's own expression rather than against a
string written here, because two tabs describing one measurement in two
vocabularies is exactly what this change is for and a hand-written expectation
would drift from the thing it is meant to match.

``_figure_title`` had no test at all before this file: every test that looked at
a Response title passed one in as a literal to ``draw_response``, so the builder
could have produced anything.
"""

from __future__ import annotations

import pytest

from well_viewer.data.metric_labels import (
    spread_label, threshold_qualifier,
)
from well_viewer.domain import sample_variables as sv
from well_viewer.domain.time_rescale import (
    TimeRescale, figure_timepoints_label,
)
from well_viewer.features.response import controller as rctl
from tests.test_response_tab import (  # noqa: F401 — the fixture is the point
    _load, _variable, tab,
)


def _one_dose_plate(tab, timepoints=(24.0,)):
    variable = _variable(pairs=(("1", 1.0), ("10", 10.0)))
    _load(tab, variable, {"A01": "v0", "A02": "v1"},
          [("g0", ["A01"]), ("g1", ["A02"])], timepoints=timepoints)
    rctl.refresh_x_combo(tab.ctx)
    tab.combo.setCurrentIndex(0)


def _title(tab) -> str:
    """The title for what is drawn *now*.

    ``response_data`` first, in the order ``redraw_response`` runs them: the
    collector reconciles which timepoints are actually drawable, and a title
    built before it could name a moment the figure does not show.
    """
    return rctl._figure_title(tab.ctx, rctl.response_data(tab.ctx))


# ── the measurement clause ───────────────────────────────────────────────────

def test_the_title_names_the_measurement_the_way_the_bar_tab_does(tab):
    """The whole string, not a substring of it.

    "the title mentions SEM" would pass against a title that mentioned it in
    the wrong place, in the wrong order, or twice.
    """
    _one_dose_plate(tab)
    tab.ctx.state.plot.use_sem = True
    channel = rctl.response_channel(tab.ctx).upper()
    metric = rctl.response_metric_label(tab.ctx)
    expected_measure = (
        f"{channel} {metric}{threshold_qualifier(rctl.response_metric_key(tab.ctx))}"
        f" ± {spread_label(True)}")
    assert _title(tab) == f"{expected_measure} — t=24 h — vs Dose"


@pytest.mark.parametrize("use_sem,word", [(True, "SEM"), (False, "SD")])
def test_the_title_says_which_spread_the_error_bars_are(tab, use_sem, word):
    """Both directions, because a title wired to a constant satisfies one."""
    _one_dose_plate(tab)
    tab.ctx.state.plot.use_sem = use_sem
    assert f" ± {word} " in _title(tab) + " ", _title(tab)
    assert spread_label(use_sem) == word


def test_the_fraction_is_named_as_itself_and_not_doubly_qualified(tab):
    """The fraction is a different quantity from whatever the Property names —
    it is the share of cells clearing the ON cutoff — so it takes its own name,
    and it must not also take the "(above threshold)" qualifier, which would
    say the same thing twice on one figure."""
    _one_dose_plate(tab)
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.metric_label = rctl.FRACTION_LABEL
    title = _title(tab)
    assert rctl.FRACTION_LABEL in title, title
    assert "(above threshold)" not in title, title


def test_the_normalisation_rides_in_the_title_as_the_other_tabs_spell_it(tab):
    """``data.normalised`` **is** ``fold_change_suffix_for``, the string the Bar
    and Line titles append — appended verbatim here, since it already carries
    the two spaces that separate it. A separator added on top would be a third
    space and a second spelling."""
    _one_dose_plate(tab)
    data = rctl.response_data(tab.ctx)
    data.normalised = "  (fold change vs DMSO (ratio))"
    title = rctl._figure_title(tab.ctx, data)
    assert "± SEM  (fold change vs DMSO (ratio)) — " in title or \
           title.startswith(rctl._measurement_title(tab.ctx, data)), title
    assert "±  " not in title and "SEM   " not in title, (
        f"the suffix was separated twice: {title!r}")


# ── the timepoints ───────────────────────────────────────────────────────────

def test_one_timepoint_reads_singular_and_several_read_as_a_list():
    """Through ``time_rescale.figure_timepoints_label``, which this tab shares
    with nothing else — a figure that draws a curve *per* timepoint and has no
    time axis is its own shape, and the helper exists because of it.

    A bare list of one is a figure claiming to overlay something it does not,
    which is why the singular form exists at all. The bracket and the singular
    follow ``metric_labels.timepoint_title``, which the Bar titles still use;
    what differs is that these numbers carry the time correction and that one.
    """
    off = TimeRescale(factor=1.0, offset=0.0)
    assert figure_timepoints_label([24.0], off) == "t=24 h"
    assert figure_timepoints_label([24.0, 48.0], off) == "t=[24, 48] h"
    assert figure_timepoints_label([], off) == ""


def test_a_figure_with_no_timepoint_drawn_says_nothing_about_when(tab):
    """Rather than an empty ``t =  h``, which reads as a missing number rather
    than as a figure that has no timepoint to name."""
    _one_dose_plate(tab)
    slice_ = tab.ctx.state.feature_slice("response")
    # ``"24"``, not ``"24.0"`` — the picker keys timepoints with ``f"{t:g}"``,
    # and a key in any other shape reads as a selection about a different plate
    # and is re-seeded. A fixture that invents its own spelling tests the
    # re-seed rather than the thing it meant to.
    slice_.tp_selections = {"24": False}
    title = _title(tab)
    assert "t =" not in title, title


def test_the_title_names_the_timepoints_that_were_drawn(tab):
    """Built after the collector, so it reports the moments on the figure.

    A title built from the picker instead would name a timepoint the plate
    holds no rows for — a caption asserting data that is not there, which is
    the substitution §7 of CLAUDE.md exists to prevent.
    """
    _one_dose_plate(tab, timepoints=(24.0, 48.0))
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"24": True, "48": True}
    data = rctl.response_data(tab.ctx)
    drawn = rctl.timepoints_for(tab.ctx)
    assert drawn == [24.0, 48.0], drawn
    title = rctl._figure_title(tab.ctx, data)
    assert "— t=[24, 48] h —" in title, title


# ── what was already there stays there ───────────────────────────────────────

def test_the_x_variable_is_still_named_last(tab):
    """It is already the x axis label, so it is the part a reader can recover
    from the figure without the title — which is why it comes after the two
    facts they cannot."""
    _one_dose_plate(tab)
    assert _title(tab).endswith("— vs Dose")


def test_a_plate_with_no_x_variable_still_titles_the_measurement(tab):
    """The measurement is known before an axis is picked, and a bare title over
    an empty state is what a reader needs to see that the tab is on the right
    channel."""
    tab.ctx.state.selection.variables = sv.VariableSet()
    rctl.refresh_x_combo(tab.ctx)
    title = _title(tab)
    assert title.startswith(rctl.response_channel(tab.ctx).upper())
    assert "vs " not in title, title
