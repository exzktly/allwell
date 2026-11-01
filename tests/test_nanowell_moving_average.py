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


# ── a fold change: the window starts after the baseline, and the baseline's own window ─────────────────────
_TPS = [f"t{i}" for i in range(6)]


def _fold_change_plate(tmp_path, baseline="tp:t2"):
    """Six timepoints t0..t5, one well, baseline t2 by default. Every number is chosen so each wrong rule gives a
    different answer:

    * site 0 counts 2, 4, 6, 8, 10, 12 — a single baseline of 6; a baseline window of 2 trailing gives 5, of 3 gives
      4, where a forward window (6, 8) would give 7 and a centred one (4, 6, 8) 6;
    * site 1 is occluded at t1, inside a trailing baseline window but outside a forward one;
    * site 2 is occluded at its baseline, t2;
    * site 3 is empty at t0..t2 and holds 3 cells after, so every baseline mean is 0 (seeded later)."""
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics
    from tests.test_nanowell_review_data import _dataset, _row

    def site(q, counts):
        return [_row(tp, q, 0, state="occluded") if n is None
                else _row(tp, q, 0, n=n, c=0.1 * n, state="empty" if n == 0 else "measured")
                for tp, n in zip(_TPS, counts, strict=True)]

    rows = (site(0, [2, 4, 6, 8, 10, 12]) + site(1, [2, None, 6, 8, 10, 12]) + site(2, [2, 4, None, 8, 10, 12])
            + site(3, [0, 0, 0, 3, 3, 3]))
    starts = {tp: f"2026-03-01T{9 + i:02d}:00:00" for i, tp in enumerate(_TPS)}
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts=starts))
    return site_metrics(data, baseline), data


def _grid(frame, series):
    """``{q: [value at t0..t5]}`` for *series* on *frame*."""
    out = frame.assign(_v=series).sort_values(["nw_q", "tp_order"])
    return {int(q): list(g["_v"]) for q, g in out.groupby("nw_q")}


def _close(got, want):
    return all((isinstance(w, float) and math.isnan(w) and math.isnan(g)) or g == pytest.approx(w)
               for g, w in zip(got, want, strict=True))


def _pipeline(metrics, data, column, *, moving=2, baseline_window=0):
    from review.features.nanowell.series_compute import exclusion_reason

    reasons = exclusion_reason(metrics, column, data.channels)
    frame, reasons = mvc.rebase(metrics, column, reasons, baseline_window)
    return mvc.apply(frame, column, reasons, moving)


def test_a_fold_change_s_window_never_reaches_its_baseline(tmp_path):
    """Site 0's fold changes from t2 are 1/3, 2/3, 1, 4/3, 5/3, 2. A window of 2 may start at t3 at the earliest:
    t4 is (4/3 + 5/3) / 2 = 1.5 and t5 (5/3 + 2) / 2. Letting the window reach the baseline would give t3 the mean
    of 1 and 4/3; letting it reach before it would give t1 and t2 values too."""
    metrics, data = _fold_change_plate(tmp_path)
    frame, why = _pipeline(metrics, data, "count_fc")
    nan = math.nan
    assert _close(_grid(frame, frame["count_fc"])[0], [nan, nan, nan, nan, 1.5, 11 / 6])
    assert _grid(frame, why)[0] == [mvc.BASELINE_REASON] * 4 + ["", ""]
    # The baseline reason wins over the early one: t0 could never have had a window, but it is the baseline that
    # rules out t0..t3, and one reason for the lot reads as one cause.
    assert why[frame["tp_order"] == 0].isin([mvc.BASELINE_REASON, "baseline occluded", "empty at baseline"]).all()


def test_a_measured_value_s_window_is_not_bounded_by_the_baseline(tmp_path):
    metrics, data = _fold_change_plate(tmp_path)
    frame, _why = _pipeline(metrics, data, "n")
    assert _close(_grid(frame, frame["n"])[0], [math.nan, 3.0, 5.0, 7.0, 9.0, 11.0])


def test_the_window_starts_after_each_nanowell_s_own_baseline_under_first_measurable(tmp_path):
    """Under First measurable each nanowell has its own baseline: site 3 is empty (measurable, n = 0) from t0, so
    its count baseline is t0 and a window of 2 starts at t1 — t2 is the first with a value — while it would have
    started at t3 had the bound been site 0's baseline or the plate's."""
    metrics, data = _fold_change_plate(tmp_path, baseline="first_measurable")
    frame, why = _pipeline(metrics, data, "log2_count_fc")
    got = _grid(frame, frame["log2_count_fc"])[3]
    assert math.isnan(got[1]) and got[2] == pytest.approx(0.0) and got[3] == pytest.approx(1.0)
    assert _grid(frame, why)[3][:2] == [mvc.BASELINE_REASON, mvc.BASELINE_REASON]


def test_the_baseline_s_window_is_the_trailing_mean_of_the_measured_value(tmp_path):
    """Site 0 with a baseline window of 2: baseline (4 + 6) / 2 = 5, so the fold changes after it are 1.6, 2, 2.4
    and their window-2 averages 1.8 and 2.2. A window of 3: baseline 4, averages 2.25 and 2.75. A forward window
    (6, 8 → 7) or a centred one (4, 6, 8 → 6) gives other numbers; so does averaging fold changes rather than
    counts (the baseline's fold changes from itself are not numbers to average)."""
    metrics, data = _fold_change_plate(tmp_path)
    nan = math.nan
    for window, want in ((2, [1.8, 2.2]), (3, [2.25, 2.75])):
        frame, why = _pipeline(metrics, data, "count_fc", baseline_window=window)
        assert _close(_grid(frame, frame["count_fc"])[0], [nan] * 4 + want)
        assert _grid(frame, why)[0][4:] == ["", ""]


def test_the_baseline_s_window_without_the_endpoint_s_average(tmp_path):
    """The rebase alone (the step before the endpoint's window): each fold change over the averaged baseline."""
    from review.features.nanowell.series_compute import exclusion_reason

    metrics, data = _fold_change_plate(tmp_path)
    frame, why = mvc.rebase(metrics, "count_fc", exclusion_reason(metrics, "count_fc", data.channels), 2)
    assert _close(_grid(frame, frame["count_fc"])[0], [0.4, 0.8, 1.2, 1.6, 2.0, 2.4])
    # log₂ is taken against the averaged count, (n + 1) / (5 + 1), as for a single baseline.
    frame, _why = mvc.rebase(metrics, "log2_count_fc", exclusion_reason(metrics, "log2_count_fc", data.channels), 2)
    assert _grid(frame, frame["log2_count_fc"])[0][3] == pytest.approx(math.log2(9 / 6))


def test_every_value_the_baseline_s_window_leaves_out_says_why(tmp_path):
    """Site 1's baseline window holds its occluded t1, a gap; site 2's baseline row is occluded, which the single
    baseline's own reason already says; site 3's averaged baseline is 0, so its later counts are seeded later; a
    window of 3 ending at t1 would start before t0; a timepoint in the window not joinable is its own reason."""
    from review.features.nanowell.series_compute import exclusion_reason

    metrics, data = _fold_change_plate(tmp_path)
    reasons = exclusion_reason(metrics, "count_fc", data.channels)
    frame, why = mvc.rebase(metrics, "count_fc", reasons, 2)
    by_site = _grid(frame, why)
    assert by_site[1] == [mvc.BASE_GAP_REASON, "occluded"] + [mvc.BASE_GAP_REASON] * 4
    assert by_site[2] == ["baseline occluded"] * 2 + ["occluded"] + ["baseline occluded"] * 3
    assert by_site[3][3:] == ["seeded later"] * 3
    assert frame.loc[why != "", "count_fc"].isna().all() and frame.loc[why == "", "count_fc"].notna().all()

    early, _ = _fold_change_plate(tmp_path / "early", baseline="tp:t1")
    frame, why = mvc.rebase(early, "count_fc", exclusion_reason(early, "count_fc", data.channels), 3)
    assert set(why[frame["nw_q"] == 0]) == {mvc.BASE_EARLY_REASON}

    foreign = metrics.copy()
    t1 = (foreign["nw_q"] == 0) & (foreign["timepoint"] == "t1")
    foreign.loc[t1, "identity_ok"] = False
    foreign.loc[t1, "count_fc"] = np.nan                    # as ``site_metrics`` leaves a row that joins nothing
    frame, why = mvc.rebase(foreign, "count_fc", exclusion_reason(foreign, "count_fc", data.channels), 2)
    site0 = _grid(frame, why)[0]
    assert site0[1] == "identity_contradicted" and set(site0[2:]) == {mvc.BASE_IDENTITY_REASON}


def test_the_baseline_s_window_needs_the_endpoint_s_and_a_baseline_with_something_before_it():
    assert mvc.baseline_window_for(2, 3, "tp:t2") == 3
    # First measurable is the first timepoint with a value, so every earlier one has none: a complete window can
    # never exist there, and leaving it on blanked every fold change of every nanowell.
    assert mvc.baseline_window_for(2, 3, "first_measurable") == 0
    assert mvc.baseline_window_for(0, 3, "tp:t2") == 0                # the endpoint's average is off
    assert mvc.baseline_window_for(2, 3, "earliest") == 0             # nothing before the earliest timepoint
    assert mvc.baseline_window_for(2, 1, "tp:t2") == 0
    assert mvc.baseline_window_for(2, 99, "tp:t2") == mvc.MAX_WINDOW


def test_the_fold_change_columns_agree_with_the_data_layer_s_kinds():
    from review.features.nanowell.series_compute import metric_kinds

    channels = ["GFP", "mCherry_2"]
    for column, (kind, value) in metric_kinds(channels).items():
        assert mvc.base_value(column) == (value if kind == "fc" else None), column


def test_the_figure_says_how_the_baseline_was_taken():
    plain = mvc.describe(2, "n")
    assert "baseline" not in plain
    single = mvc.describe(2, "count_fc")
    assert "starts after the baseline" in single and "its one measured value, not averaged" in single
    windowed = mvc.describe(2, "count_fc", 3)
    assert "the baseline timepoint and the 2 before it, only where all 3 have one" in windowed
    assert "averaged as logs" in mvc.describe(2, "log2_count_fc") and "logs" not in single
    assert mvc.describe(0, "count_fc", 3) == ""


def test_every_new_reason_has_a_figure_label():
    reasons = (mvc.BASELINE_REASON, mvc.BASE_EARLY_REASON, mvc.BASE_GAP_REASON, mvc.BASE_IDENTITY_REASON)
    labels = [reason_label(r) for r in reasons]
    assert labels == ["moving window reaches the baseline", "baseline window before the first timepoint",
                      "baseline window has a gap", "baseline window not joinable"]


def test_the_distribution_the_summary_and_trajectories_agree_on_the_baseline_s_window(tmp_path):
    """The three consumers take the same path: same values at t4 and t5 for site 0 (1.8, 2.2)."""
    from review.features.nanowell import views_compute as vc
    from review.features.nanowell.distribution_compute import distribution_model, wells_summary

    metrics, data = _fold_change_plate(tmp_path)
    model = distribution_model(metrics, "count_fc", channels=data.channels, groups={"A01": ["A01"]},
                               time_source=data.time_source, moving=2, baseline_moving=2, baseline="tp:t2")
    swarms = {s.label: s for s in model.panels[0].swarms}
    assert list(swarms["t4"].values) == pytest.approx([1.8]) and list(swarms["t5"].values) == pytest.approx([2.2])
    assert swarms["t4"].excluded == {"baseline window has a gap": 1, "baseline occluded": 1,
                                     "seeded later (0 at baseline)": 1}
    assert any("measured values at the baseline timepoint and the 1 before it" in n for n in model.notes)
    table = wells_summary(metrics, ["A01"], "count_fc", channels=data.channels, moving=2,
                          baseline_moving=2).set_index("timepoint")
    assert table.loc["t5", "median"] == pytest.approx(2.2) and table.loc["t5", "n_used"] == 1
    panel = vc.trajectory_panels(metrics, ["A01"], "count_fc", moving=2, baseline_moving=2)[0]
    lines = {site[1]: y for site, _occ, y in panel.lines}
    assert set(lines) == {0} and list(lines[0][4:]) == pytest.approx([1.8, 2.2])
    # With the endpoint's average off, the baseline's window is not used either.
    off = wells_summary(metrics, ["A01"], "count_fc", channels=data.channels, moving=0,
                        baseline_moving=2).set_index("timepoint")
    assert off.loc["t5", "median"] == pytest.approx(2.0)                # 12 / 6, the single baseline


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
    # The box holds the bare number (1 is off; its band label says so): an "Off" or a unit inside the box would
    # refuse the keystrokes below, which is how the typed value used to be ignored.
    assert spin.text() == "1" and st.moving == 0
    from PySide6.QtCore import Qt

    edit = spin.lineEdit()
    edit.setFocus()
    edit.setCursorPosition(len(edit.text()))
    qtbot.keyClick(edit, Qt.Key_Backspace)
    qtbot.keyClicks(edit, "2")
    qtbot.keyClick(edit, Qt.Key_Return)
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


def test_the_page_s_trajectories_are_drawn_from_the_averages(page, qtbot, monkeypatch):  # noqa: F811
    """At the renderer's door: the panels the page hands to it, not a recomputation beside it."""
    st = _loaded(qtbot, page)
    page.state.selection.selected_wells = {"A01"}
    nwc.nw_on_selection_changed(page)
    drawn = []
    real = nwc.render_trajectories
    monkeypatch.setattr(nwc, "render_trajectories", lambda fig, panels, **kw: (drawn.append(panels),
                                                                              real(fig, panels, **kw)))
    nwc.set_moving(page, 2)
    nwc.set_view(page, "trajectories")
    lines = {site[1]: y[1] for site, _occ, y in drawn[-1][0].lines}
    assert lines == {0: 1.5, 1: 3.0, 2: 1.5}
    assert st.moving == 2



def _rebaselined(qtbot, page, st, baseline):  # noqa: F811
    nwc.set_page_baseline(page, baseline)
    qtbot.waitUntil(lambda: st.loaded_for is not None and st.loaded_for[1] == baseline and not st.computing,
                    timeout=20000)


def test_the_baseline_s_window_control_is_greyed_until_it_can_reach_the_figure(page, qtbot):  # noqa: F811
    """Through the spin box a user turns. Greyed, it says why — and a value left in it changes nothing, so a
    greyed control never alters a figure."""
    st = _loaded(qtbot, page)
    page.state.selection.selected_wells = {"A01"}
    nwc.nw_on_selection_changed(page)
    spin = page.widgets.get("_nw_baseline_moving_spin")
    assert spin.text() == "1" and st.baseline_moving == 0
    assert spin.isVisibleTo(spin.window()) and not spin.isEnabled()
    assert "Turn on the Moving average first" in spin.toolTip()
    nwc.set_metric(page, "count_fc")
    page.widgets.get("_nw_moving_spin").setValue(2)
    assert not spin.isEnabled() and "no measured timepoint before them" in spin.toolTip()
    st.baseline_moving = 2                                   # left in the control from an earlier baseline
    nwc.redraw(page)
    assert any("its one measured value, not averaged" in t for t in _texts(page))
    _rebaselined(qtbot, page, st, "first_measurable")
    assert not spin.isEnabled() and "no measured timepoint before them" in spin.toolTip()
    _rebaselined(qtbot, page, st, "tp:t_a")
    assert spin.isEnabled() and "\n\n(" not in spin.toolTip()
    spin.setValue(3)
    assert st.baseline_moving == 3
    assert any("the baseline timepoint and the 2 before it, only where all 3 have one" in t for t in _texts(page))
    nwc.set_metric(page, "n")
    assert not spin.isEnabled() and "Only a fold change has a baseline" in spin.toolTip()
    nwc.set_view(page, "array")
    assert not spin.isVisibleTo(spin.window())


def test_the_page_s_fold_change_windows_start_after_the_baseline(page, qtbot):  # noqa: F811
    """The fixture has two timepoints, so with the earliest baseline a window of 2 always reaches it: every fold
    change is left out, and the figure counts them under that reason rather than averaging in the baseline's 1."""
    st = _loaded(qtbot, page)
    page.state.selection.selected_wells = {"A01"}
    nwc.nw_on_selection_changed(page)
    nwc.set_metric(page, "count_fc")
    nwc.set_moving(page, 2)
    late = next(s for s in st.model.swarms() if s.label == "t_a")
    assert len(late.values) == 0 and late.excluded.get("moving window reaches the baseline", 0) >= 1
    assert any("Each window starts after the baseline" in t for t in _texts(page))


def test_the_baseline_s_window_is_saved_and_restored(page, qtbot):  # noqa: F811
    st = _loaded(qtbot, page)
    nwc.set_baseline_moving(page, 3)
    assert st.to_config()["baseline_moving"] == 3
    st.apply_config({"baseline_moving": 4})
    assert st.baseline_moving == 4
    for bad in ("4", True, 1, None, 2.0):
        st.apply_config({"baseline_moving": bad})
        assert st.baseline_moving == 0
    st.apply_config({"baseline_moving": 99})
    assert st.baseline_moving == mvc.MAX_WINDOW
