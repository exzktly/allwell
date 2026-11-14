"""A Baseline average that can never be complete says so on every view, instead of drawing nothing.

Reported: a T-cell fold change at the second-from-last timepoint, Baseline the second timepoint, moving average 3
centred and Baseline average 3: every view drew nothing, and the Rare responders test said "no well is labelled with
control condition …; conditions found:" with nothing listed. The baseline's window is trailing (it ends at the
baseline and reaches back), so a baseline at the second timepoint has one timepoint before it and a window of 3 is
never complete: every fold change is undefined. That is the design; drawing an empty figure without saying so is
the defect (CLAUDE.md §7). ``moving_compute.nothing_left_text`` could not say it, because it is measured on the frame
the baseline's window had already emptied, where there is nothing to average.

The plate is ``test_nanowell_rare_moving_average``'s (five timepoints t0..t4), on the count fold change from t1.
"""

from __future__ import annotations

import pytest
from matplotlib.figure import Figure
from matplotlib.text import Text

from review.features.nanowell import controller as nwc
from review.features.nanowell import moving_compute as mvc
from tests.test_nanowell_rare_moving_average import GROUPS, _plate

SAID = ("Baseline average over 3 timepoints: no fold change is left to draw{at} — the baseline's window ends at the "
        "baseline (t1) and reaches back 2 timepoints, but no nanowell's well has that many before its baseline. The "
        "baseline's window never reaches forward, since a later timepoint may already carry the treatment, so it is "
        "not centred even when the moving average is. Set Baseline average to 2 or less, or choose a later Baseline.")


@pytest.fixture(scope="module")
def plate(tmp_path_factory):
    from review.features.nanowell.series_compute import site_metrics

    _metrics, data = _plate(tmp_path_factory.mktemp("bw"))
    return site_metrics(data, "tp:t1"), data


def _draw(plate, view, test="welch", baseline_moving=3):
    metrics, data = plate
    asked = []
    fig = Figure()
    nwc.draw_view(fig, view, metrics, data, groups=GROUPS, metric="count_fc", channel=None, axis="wells",
                  timepoint="t3", control="ctrl", baseline="tp:t1", cohort=False, well="A01", well_timepoint="t3",
                  stats_test=test, moving=3, moving_centred=True, baseline_moving=baseline_moving,
                  rare={"store": {}, "request": lambda key, job: asked.append(job)})
    return [t.get_text() for t in fig.findobj(Text) if t.get_gid() == "nw-moving-empty"], asked


@pytest.mark.parametrize("view, test, at", [("distribution", "welch", " at t3"), ("statistics", "welch", " at t3"),
                                            ("statistics", "rare", " at t3"), ("trajectories", "welch", "")])
def test_every_view_says_the_baseline_window_cannot_be_complete(plate, view, test, at):
    said, asked = _draw(plate, view, test)
    assert said == [SAID.format(at=at)]
    assert asked == []                                        # the rare test is not sent to the worker to fail there


@pytest.mark.parametrize("view, test", [("distribution", "welch"), ("statistics", "rare")])
def test_a_baseline_window_that_fits_draws_and_says_nothing_of_it(plate, view, test):
    said, asked = _draw(plate, view, test, baseline_moving=2)
    assert said == []
    assert (len(asked) == 1) == (test == "rare")


def test_the_sentence_is_only_for_a_fold_change_and_a_window_that_is_on(plate):
    metrics, _data = plate
    assert mvc.baseline_nothing_left_text(metrics, "count_fc", 3, timepoint="t3") == SAID.format(at=" at t3")
    assert mvc.baseline_nothing_left_text(metrics, "count_fc", 2, timepoint="t3") == ""
    assert mvc.baseline_nothing_left_text(metrics, "count_fc", 1, timepoint="t3") == ""
    assert mvc.baseline_nothing_left_text(metrics, "n", 3, timepoint="t3") == ""          # not a fold change
