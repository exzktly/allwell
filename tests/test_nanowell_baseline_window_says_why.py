"""The baseline's own window takes the moving average's kind, and a baseline window that cannot be complete says so.

Reported: a T-cell fold change at the second-from-last timepoint, Baseline the second timepoint, a **centred** moving
average of 3 and a Baseline average of 3 drew nothing in any view, and the Rare responders test said "no well is
labelled with control condition …; conditions found:" with nothing listed. The baseline's window was trailing
whatever the moving average's kind — t−1, t0, t1, never complete at the second timepoint — so every fold change was
undefined, and no view said why. The user's ruling: centred means centred, so the baseline's window is centred with
the moving average (t0, t1, t2 here). A window that still cannot fit — trailing, or centred and wider than the
timepoints either side of the baseline — is said in place of the plot.

The plate is ``test_nanowell_rare_moving_average``'s (five timepoints t0..t4), on the count fold change from t1. Its
sustained nanowells count 1, 1, 8, 8, 8 and its dip nanowells 1, 1, 10, 10, 2, so the baseline's window decides the
numbers: centred over t0..t2 the baselines are 10/3 and 4, where the single baseline is 1 and a trailing window of 3
has no value at all.
"""

from __future__ import annotations

import pandas as pd
import pytest
from matplotlib.figure import Figure
from matplotlib.text import Text

from review.features.nanowell import controller as nwc
from review.features.nanowell import moving_compute as mvc
from tests.test_nanowell_rare_moving_average import DIP, GROUPS, SUSTAINED, _plate, _tested

TRAILING_3 = ("Baseline average over 3 timepoints: no fold change is left to draw{at} — the baseline's window ends at "
              "the baseline (t1) and reaches back 2 timepoints, but no nanowell's well has that many before its "
              "baseline. Set Baseline average to 2 or less, or choose another Baseline.")
CENTRED_5 = ("Baseline average over 5 timepoints, centred: no fold change is left to draw{at} — the baseline's window "
             "is centred on the baseline (t1) and reaches 2 timepoints each way, but no nanowell's well has 2 "
             "timepoints before its baseline. Set Baseline average to 3 or less, or choose another Baseline.")


@pytest.fixture(scope="module")
def plate(tmp_path_factory):
    from review.features.nanowell.series_compute import site_metrics

    _metrics, data = _plate(tmp_path_factory.mktemp("bw"))
    return site_metrics(data, "tp:t1"), data


def _draw(plate, view, test="welch", *, baseline_moving=3, centred=True, store=None):
    metrics, data = plate
    asked = []
    fig = Figure()
    _hit, model = nwc.draw_view(
        fig, view, metrics, data, groups=GROUPS, metric="count_fc", channel=None, axis="wells", timepoint="t3",
        control="ctrl", baseline="tp:t1", cohort=False, well="A01", well_timepoint="t3", stats_test=test, moving=3,
        moving_centred=centred, baseline_moving=baseline_moving,
        rare={"store": store if store is not None else {}, "request": lambda key, job: asked.append((key, job))})
    return [t.get_text() for t in fig.findobj(Text) if t.get_gid() == "nw-moving-empty"], asked, model


def test_the_reported_settings_draw_with_the_baseline_centred(plate):
    """Centred 3, Baseline average 3 at t1: the baseline is the mean over t0, t1, t2, and the rare test tests the
    endpoint's centred mean (t2, t3, t4) of the fold changes over it."""
    said, asked, _model = _draw(plate, "statistics", "rare")
    assert said == [] and len(asked) == 1
    key, job = asked[0]
    _said, _asked, result = _draw(plate, "statistics", "rare", store={key: job()})
    tested = _tested(result)
    assert tested[("B01", SUSTAINED[0])] == pytest.approx(8 / (10 / 3))                # 8 over (1 + 1 + 8) / 3
    assert tested[("B01", DIP[0])] == pytest.approx((10 / 4 + 10 / 4 + 2 / 4) / 3)     # over (1 + 1 + 10) / 3
    said, _asked, model = _draw(plate, "distribution")
    assert said == [] and sum(len(s.values) for s in model.swarms()) > 0


def test_the_baseline_window_is_centred_only_under_a_centred_moving_average(plate):
    metrics, _data = plate
    blank = pd.Series("", index=metrics.index, dtype=object)
    centred, _why = mvc.rebase(metrics, "count_fc", blank, 3, centred=True)
    trailing, why = mvc.rebase(metrics, "count_fc", blank, 3)
    site = (metrics["well"] == "B01") & (metrics["nw_q"] == SUSTAINED[0]) & (metrics["timepoint"] == "t3")
    assert centred.loc[site, "count_fc"].item() == pytest.approx(2.4)
    assert pd.isna(trailing.loc[site, "count_fc"].item())                    # t−1 does not exist
    assert why[site].item() == mvc.BASE_EARLY_REASON


@pytest.mark.parametrize("view, test, at", [("distribution", "welch", " at t3"), ("statistics", "welch", " at t3"),
                                            ("statistics", "rare", " at t3"), ("trajectories", "welch", "")])
@pytest.mark.parametrize("centred, window, said", [(True, 5, CENTRED_5), (False, 3, TRAILING_3)])
def test_every_view_says_a_baseline_window_that_cannot_fit(plate, view, test, at, centred, window, said):
    shown, asked, _model = _draw(plate, view, test, baseline_moving=window, centred=centred)
    assert shown == [said.format(at=at)]
    assert asked == []                                        # the rare test is not sent to the worker to fail there


def test_a_centred_baseline_window_past_the_last_timepoint_says_so(plate):
    """A baseline at the last timepoint has nothing after it for a centred window to reach."""
    from review.features.nanowell.series_compute import site_metrics

    _metrics, data = plate
    metrics = site_metrics(data, "tp:t4")
    said = mvc.baseline_nothing_left_text(metrics[metrics["well"] == "A01"], "count_fc", 3, centred=True)
    assert said == ("Baseline average over 3 timepoints, centred: no fold change is left to draw — the baseline's "
                    "window is centred on the baseline (t4) and reaches 1 timepoint each way, but no nanowell's well "
                    "has 1 timepoint after its baseline. Set Baseline average to 1 (off), or choose another Baseline.")


def test_the_sentence_is_only_for_a_fold_change_and_a_window_that_is_on(plate):
    metrics, _data = plate
    assert mvc.baseline_nothing_left_text(metrics, "count_fc", 3, timepoint="t3") == TRAILING_3.format(at=" at t3")
    assert mvc.baseline_nothing_left_text(metrics, "count_fc", 3, timepoint="t3", centred=True) == ""
    assert mvc.baseline_nothing_left_text(metrics, "count_fc", 1, timepoint="t3") == ""
    assert mvc.baseline_nothing_left_text(metrics, "n", 3, timepoint="t3") == ""          # not a fold change


def test_the_footer_and_tag_say_the_baseline_window_is_centred():
    assert mvc.describe(3, "count_fc", 3, centred=True).endswith(
        "The baseline is the mean of the nanowell's measured values at the baseline timepoint, the 1 before it and "
        "the 1 after it, only where all 3 have one.")
    assert mvc.figure_tag(3, "count_fc", "tp:t1", 3, centred=True) == (
        "Moving average over 3 timepoints (centred); baseline over 3")
    assert mvc.baseline_window_for(3, 2, "tp:t1", centred=True) == 0       # an even window has no middle: off
    assert mvc.baseline_window_for(3, 2, "tp:t1") == 2


def test_a_centred_baseline_window_s_new_reason_has_a_figure_label():
    from review.features.nanowell.distribution_compute import reason_label

    assert reason_label(mvc.BASE_LATE_REASON) == "baseline window after the last timepoint"
