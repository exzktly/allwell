"""The moving average over time on the Nanowells page (``moving_compute``): Distribution and Trajectories can draw
each nanowell's metric as its mean over a trailing window of timepoints.

The pure tests use a hand-built frame whose numbers make each plausible wrong implementation give a different
answer: a partial window at the start (a value at the first timepoint), a centred window (the next timepoint's
value pulled in), a window that reaches past a missing row (a value where the series has a hole), and a mean
over the values present (a value across a gap). The page tests use the page fixture: A01 counts 1, 2, 0, — at
t_c and 2, 4, 3, 1 at t_a, so a window of 2 gives 1.5, 3.0, 1.5 at t_a, a median of 1.5 against 2.5 measured
and 3.0 under the fixed cohort."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from review.features.nanowell import moving_compute as mvc
from review.features.nanowell.distribution_compute import moving_window_for, reason_label


def _frame():
    """Two nanowells over five timepoints, rows shuffled. Site (0, 0) counts 1, 2, 4, 8, 16 with its t4 not
    joinable; site (1, 0) counts 10, 20, —, 40, 50 and has no row at all at t4 of a third site's series."""
    rows = []
    for t, v in enumerate([1.0, 2.0, 4.0, 8.0, 16.0]):
        rows.append({"well": "A01", "nw_q": 0, "nw_r": 0, "tp_order": t, "n": v, "identity_ok": t != 4})
    for t, v in enumerate([10.0, 20.0, np.nan, 40.0, 50.0]):
        rows.append({"well": "A01", "nw_q": 1, "nw_r": 0, "tp_order": t, "n": v, "identity_ok": True})
    # Site (2, 0) has no row at t2: a window over rows would join t1 and t3 as if adjacent.
    for t, v in ((0, 5.0), (1, 6.0), (3, 7.0), (4, 9.0)):
        rows.append({"well": "A01", "nw_q": 2, "nw_r": 0, "tp_order": t, "n": v, "identity_ok": True})
    return pd.DataFrame(rows).sample(frac=1.0, random_state=3)


def _by_site(frame, values):
    out = frame.assign(_v=values).sort_values(["nw_q", "tp_order"])
    return {q: list(g["_v"]) for q, g in out.groupby("nw_q")}


def test_a_window_is_the_trailing_mean_and_only_a_complete_window_has_a_value():
    frame = _frame()
    averaged, why = mvc.moving_mean(frame, frame["n"], 2)
    got = _by_site(frame, averaged)
    nan = math.nan

    def same(a, b):
        return all((math.isnan(x) and math.isnan(y)) or x == y for x, y in zip(a, b, strict=True))

    assert same(got[0], [nan, 1.5, 3.0, 6.0, nan])         # t4 is not joinable
    assert same(got[1], [nan, 15.0, nan, nan, 45.0])       # t2 has no value: both windows that hold it have none
    assert same(got[2], [nan, 5.5, nan, 8.0])              # no row at t2: t3's window is t2-t3, not t1-t3
    reasons = _by_site(frame, why)
    assert reasons[0] == [mvc.EARLY_REASON, "", "", "", mvc.IDENTITY_REASON]
    # The undefined value at t2 keeps its own reason (the caller's), so it is left "" here.
    assert reasons[1] == [mvc.EARLY_REASON, "", "", mvc.GAP_REASON, ""]
    assert reasons[2] == [mvc.EARLY_REASON, "", mvc.GAP_REASON, ""]


def test_a_window_of_three_starts_at_the_third_timepoint():
    frame = _frame()
    averaged, why = mvc.moving_mean(frame, frame["n"], 3)
    got = _by_site(frame, averaged)
    assert math.isnan(got[0][1]) and got[0][2] == pytest.approx(7.0 / 3.0) and got[0][3] == pytest.approx(14.0 / 3.0)
    assert _by_site(frame, why)[0][:2] == [mvc.EARLY_REASON, mvc.EARLY_REASON]


def test_off_returns_the_measured_values_and_the_window_is_clamped():
    frame = _frame()
    for off in (0, 1, None, True, "3", -2):
        averaged, why = mvc.moving_mean(frame, frame["n"], off)
        assert averaged.equals(frame["n"].astype(float)) and (why == "").all()
    assert mvc.clamp_window(50) == mvc.MAX_WINDOW
    assert mvc.describe(0) == ""
    assert mvc.describe(3).startswith("Moving average over 3 timepoints:")


def test_every_reason_it_can_give_has_a_figure_label():
    labels = {reason_label(r) for r in (mvc.EARLY_REASON, mvc.GAP_REASON, mvc.IDENTITY_REASON)}
    assert labels == {"before the first full moving window", "moving window has a gap", "moving window not joinable"}


def test_a_per_nanowell_metric_has_no_window():
    assert moving_window_for("growth_rate", 3) == 0
    assert moving_window_for("occupancy_class", 3) == 0
    assert moving_window_for("n", 3) == 3
    assert moving_window_for("n", 1) == 0


# ── on the page ─────────────────────────────────────────────────────────────
pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import views_compute as vc  # noqa: E402
from review.features.nanowell.distribution_compute import distribution_model, wells_summary  # noqa: E402
from tests.test_nanowell_page import _loaded, _texts, page  # noqa: E402,F401


def _a01(st, **kw):
    model = distribution_model(st.metrics, "n", channels=st.data.channels, groups={"A01": ["A01"]},
                               time_source=st.data.time_source, **kw)
    return model, {s.label: s for s in model.panels[0].swarms}


def test_the_distribution_draws_the_averages_and_counts_every_value_it_left_out(page, qtbot):  # noqa: F811
    st = _loaded(qtbot, page)
    model, swarms = _a01(st, moving=2)
    late, early = swarms["t_a"], swarms["t_c"]
    assert sorted(late.values) == [1.5, 1.5, 3.0]
    assert late.excluded == {"moving window has a gap": 1}            # nanowell 3 was occluded at t_c
    assert early.excluded == {"before the first full moving window": 3, "occluded": 1}
    for s in (late, early):
        assert len(s.values) + s.n_excluded == s.n_roster == 4
    (x0, m0), (x1, m1) = model.panels[0].medians["A01"]
    assert (x0, x1, m1) == (0.0, 1.0, 1.5) and math.isnan(m0)           # no median at t_c: nothing was averaged
    assert any(n.startswith("Moving average over 2 timepoints:") for n in model.notes)


def test_the_cohort_is_decided_on_the_measured_values_before_the_average(page, qtbot):  # noqa: F811
    """Decided on the averages instead, the cohort would be empty: no nanowell has an average at t_c."""
    st = _loaded(qtbot, page)
    _model, swarms = _a01(st, moving=2, cohort=True)
    assert sorted(swarms["t_a"].values) == [1.5, 1.5, 3.0]
    assert swarms["t_a"].excluded == {"outside the fixed cohort": 1}


def test_the_well_summary_is_the_figure_s_numbers(page, qtbot):  # noqa: F811
    st = _loaded(qtbot, page)
    table = wells_summary(st.metrics, ["A01"], "n", channels=st.data.channels, moving=2).set_index("timepoint")
    assert table.loc["t_a", "median"] == 1.5 and table.loc["t_a", "n_used"] == 3
    assert math.isnan(table.loc["t_c", "median"])


def test_trajectories_draw_each_line_as_its_average(page, qtbot):  # noqa: F811
    st = _loaded(qtbot, page)
    panel = vc.trajectory_panels(st.metrics, ["A01"], "n", moving=2)[0]
    lines = {site[1]: list(y) for site, _occ, y in panel.lines}
    assert set(lines) == {0, 1, 2}                                     # nanowell 3 has no complete window
    assert lines[0][1] == 1.5 and lines[1][1] == 3.0 and lines[2][1] == 1.5
    assert all(math.isnan(y[0]) for y in lines.values())
    assert panel.n_without_value == 1
    cohort = vc.trajectory_panels(st.metrics, ["A01"], "n", moving=2, cohort=True)[0]
    assert len(cohort.lines) == 3                                      # the cohort, on measured values, keeps them


def test_the_spin_box_sets_the_window_and_the_figure_says_so(page, qtbot):  # noqa: F811
    """Through the control a user turns, not the setter: its value has to reach the state and the figure."""
    st = _loaded(qtbot, page)
    page.state.selection.selected_wells = {"A01"}
    nwc.nw_on_selection_changed(page)
    spin = page.widgets.get("_nw_moving_spin")
    assert spin.text() == "Off" and st.moving == 0
    spin.setValue(2)
    assert st.moving == 2
    assert any(t.startswith("Moving average over 2 timepoints:") for t in _texts(page))
    assert isinstance(st.model, type(_a01(st)[0]))
    assert sorted(v for s in st.model.swarms() if s.label == "t_a" for v in s.values) == [1.5, 1.5, 3.0]
    nwc.set_view(page, "trajectories")
    assert any(t.startswith("Moving average over 2 timepoints:") for t in _texts(page))
    assert spin.isVisibleTo(spin.window())
    nwc.set_view(page, "array")
    assert not spin.isVisibleTo(spin.window())
    nwc.set_view(page, "distribution")
    spin.setValue(1)
    assert st.moving == 0
    assert not any(t.startswith("Moving average") for t in _texts(page))


def test_the_window_is_saved_and_restored(page, qtbot):  # noqa: F811
    st = _loaded(qtbot, page)
    nwc.set_moving(page, 3)
    assert st.to_config()["moving"] == 3
    st.apply_config({"moving": 4})
    assert st.moving == 4
    for bad in ("4", True, 1, None, 2.0):
        st.apply_config({"moving": bad})
        assert st.moving == 0
    st.apply_config({"moving": 99})
    assert st.moving == mvc.MAX_WINDOW
