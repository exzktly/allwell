"""The Nanowells page's **Statistics** view: wells (their nanowells the values) or groups (their wells' medians the
values) compared by a t-test, Mann-Whitney U, Kolmogorov-Smirnov or Dunnett, with an omnibus, adjusted p-values and
effect sizes.

The pure layer (``statistics_compute``) is checked against scipy computed independently here; the page is driven
through its real builder and controls (``build_nanowell_tab``, the view switch, the *Compare*, *Test*, *vs* and
*Timepoint* combos) and read back from the figure's text artists (CLAUDE.md §7.4 #1, §7.6, §7.8).
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.stats as st

from review.domain.significance import p_phrase
from review.features.nanowell import statistics_compute as sc
from review.features.nanowell.distribution_compute import WELLS, distribution_model
from review.features.nanowell.state import NanowellState


#: The seed ``stats_tests.dunnett_vs_control`` integrates Dunnett's p with, so scipy here gives the same p.
_SEED = 20240517


def _sample(name, values, **kw):
    return sc.Sample(name, (name,), np.asarray(values, dtype=float), **kw)


# ── the pure layer ───────────────────────────────────────────────────────────
def test_bh_adjustment_matches_scipy_and_a_single_p_is_left_alone():
    ps = [0.01, 0.04, 0.03, None, 0.2]
    adjusted, applied = sc.bh_adjust(ps)
    expected = st.false_discovery_control([0.01, 0.04, 0.03, 0.2])
    assert applied and adjusted[3] is None
    assert [adjusted[i] for i in (0, 1, 2, 4)] == pytest.approx(list(expected))
    # A family of one has nothing to correct: the p comes back as it went in, and the flag says no correction.
    assert sc.bh_adjust([0.03, None]) == ([0.03, None], False)


@pytest.mark.parametrize("key, oracle", [
    ("welch", lambda a, b: st.ttest_ind(a, b, equal_var=False)),
    ("student", lambda a, b: st.ttest_ind(a, b, equal_var=True)),
    ("mannwhitney", lambda a, b: st.mannwhitneyu(a, b, alternative="two-sided")),
    ("ks", lambda a, b: st.ks_2samp(a, b)),
])
def test_each_pairwise_test_is_the_scipy_test_it_names(key, oracle):
    # Unequal sizes and spreads, so Welch and Student give different answers and a swapped branch cannot pass.
    a, b = [1.0, 2.0, 2.5, 3.0, 9.0], [4.0, 4.5, 5.0]
    result = sc.compare([_sample("A01", a), _sample("B01", b)], key, label="Cell count", timepoint="t_a")
    (c,) = result.comparisons
    expected = oracle(a, b)
    assert (c.a, c.b, c.stat, c.p) == ("A01", "B01", pytest.approx(expected.statistic), pytest.approx(expected.pvalue))
    assert not c.adjusted and c.p_used == c.p                    # one comparison: no family to correct
    assert c.effect.diff == pytest.approx(np.mean(a) - np.mean(b))


def test_every_pair_is_compared_and_adjusted_or_each_against_the_control():
    samples = [_sample("A01", [1, 2, 3, 4]), _sample("A02", [2, 3, 4, 5]), _sample("B01", [6, 7, 8, 9.5])]
    every = sc.compare(samples, "welch")
    assert [(c.a, c.b) for c in every.comparisons] == [("A01", "A02"), ("A01", "B01"), ("A02", "B01")]
    raw = [st.ttest_ind(x.values, y.values, equal_var=False).pvalue
           for x, y in ((samples[0], samples[1]), (samples[0], samples[2]), (samples[1], samples[2]))]
    assert [c.p_adj for c in every.comparisons] == pytest.approx(list(st.false_discovery_control(raw)))
    assert every.omnibus.name == "One-way ANOVA"
    assert every.omnibus.p == pytest.approx(st.f_oneway(*[s.values for s in samples]).pvalue)
    vs = sc.compare(samples, "welch", control="A02")
    assert [(c.a, c.b) for c in vs.comparisons] == [("A01", "A02"), ("B01", "A02")]
    assert vs.family == "each against A02"
    # A rank test is gated by the rank omnibus.
    assert sc.compare(samples, "mannwhitney").omnibus.name == "Kruskal-Wallis"


def test_dunnett_is_scipys_dunnett_and_needs_a_control():
    ctrl, t1, t2 = [1.0, 1.2, 0.9, 1.1], [2.0, 2.2, 1.9], [1.05, 1.15, 0.95]
    samples = [_sample("A01", ctrl), _sample("A02", t1), _sample("B01", t2)]
    assert sc.compare(samples, "dunnett").undefined.startswith("Dunnett compares each sample against a control")
    result = sc.compare(samples, "dunnett", control="A01")
    expected = st.dunnett(np.array(t1), np.array(t2), control=np.array(ctrl), random_state=_SEED)
    assert [(c.a, c.b) for c in result.comparisons] == [("A02", "A01"), ("B01", "A01")]
    assert [c.p for c in result.comparisons] == pytest.approx(list(expected.pvalue))
    assert result.correction == "Dunnett's correction"
    # The same samples give the same p every time: scipy integrates it at random unless seeded.
    again = sc.compare(samples, "dunnett", control="A01")
    assert [c.p for c in again.comparisons] == [c.p for c in result.comparisons]
    lines = [t for _g, t in sc.report_lines(result)]
    assert any(line.startswith("B01 vs A01   t = ") and "p_Dunnett = " in line for line in lines)


def test_what_cannot_be_tested_says_why_and_is_never_a_number():
    one = sc.compare([_sample("A01", [1, 2]), _sample("B01", [3.0])], "welch")
    (c,) = one.comparisons
    assert (c.p, c.undefined) == (None, "fewer than 2 values in B01")
    flat = sc.compare([_sample("A01", [2, 2, 2]), _sample("B01", [2, 2])], "welch")
    assert flat.comparisons[0].undefined.startswith("both samples hold the same constant value")
    lonely = sc.compare([_sample("A01", [1, 2])], "welch", unit=sc.WELLS)
    assert lonely.undefined.startswith("A test compares two or more groups; the figure draws 1.")
    lines = sc.report_lines(one)
    assert ("nw-stats-pair", "A01 vs B01   not computed: fewer than 2 values in B01") in lines


def test_a_family_too_large_to_read_is_refused_with_how_to_narrow_it():
    samples = [_sample(f"W{i}", [i, i + 1.0, i + 3.0]) for i in range(7)]          # 21 pairs
    refused = sc.compare(samples, "welch")
    assert refused.comparisons == [] and refused.undefined.startswith("7 samples make 21 comparisons")
    assert "Choose a control under vs" in refused.undefined
    narrowed = sc.compare(samples, "welch", control="W0")
    assert (narrowed.undefined, len(narrowed.comparisons)) == ("", 6)


def test_a_single_comparison_is_never_labelled_adjusted():
    result = sc.compare([_sample("A01", [1, 2, 3]), _sample("B01", [4, 5, 7])], "welch")
    pair = next(t for g, t in sc.report_lines(result) if g == sc.GID_PAIR)
    p = st.ttest_ind([1, 2, 3], [4, 5, 7], equal_var=False).pvalue
    assert f"   {p_phrase(p)}   " in pair and "p_BH" not in pair


def _metrics_frame():
    """A metrics frame as ``site_metrics`` gives it: two wells, one timepoint, a nanowell of B01 without a count."""
    import pandas as pd

    rows = []
    for well, counts in (("A01", [2, 4, 3, 1]), ("A02", [5, 5, 6, 7]), ("B01", [3, None, 1, 2])):
        for q, n in enumerate(counts):
            rows.append({"well": well, "timepoint": "t_a", "time_h": 24.0, "tp_order": 0, "nw_q": q, "nw_r": 0,
                         "n": n, "occupancy_class": "1", "state": "occluded" if n is None else "measured"})
    return pd.DataFrame(rows)


def test_samples_are_the_distribution_views_own_dots_in_either_unit():
    groups = {"ctrl": ["A01", "A02"], "B01": ["B01"]}
    model = distribution_model(_metrics_frame(), "n", axis=WELLS, groups=groups, timepoint="t_a")
    by_well = sc.samples_from_model(model, groups, sc.NANOWELLS)
    assert [(s.name, list(s.values), s.n_roster) for s in by_well] == [
        ("A01", [2, 4, 3, 1], 4), ("A02", [5, 5, 6, 7], 4), ("B01", [3, 1, 2], 4)]
    assert sum(by_well[2].excluded.values()) == 1
    by_group = sc.samples_from_model(model, groups, sc.WELLS)
    # Each well's median, once: A01's 2.5 and A02's 5.5 — not the eight nanowells pooled (median 4.5).
    assert [(s.name, list(s.values), s.wells) for s in by_group] == [
        ("ctrl", [2.5, 5.5], ("A01", "A02")), ("B01", [2.0], ("B01",))]


def test_the_result_is_kept_until_something_it_was_computed_from_changes(monkeypatch):
    from review.features.nanowell import controller as nwc

    calls = []
    real = sc.compare
    monkeypatch.setattr(sc, "compare", lambda *a, **k: calls.append(1) or real(*a, **k))
    groups = {"A01": ["A01"], "A02": ["A02"]}
    model = distribution_model(_metrics_frame(), "n", axis=WELLS, groups=groups, timepoint="t_a")
    cache = {}
    first = nwc.statistics_result(model, groups, unit=sc.NANOWELLS, test="welch", cache=cache)
    assert nwc.statistics_result(model, groups, unit=sc.NANOWELLS, test="welch", cache=cache) is first
    assert len(calls) == 1
    nwc.statistics_result(model, groups, unit=sc.NANOWELLS, test="mannwhitney", cache=cache)
    model.panels[0].swarms[0].values = model.panels[0].swarms[0].values + 1.0      # a Subset or baseline moved it
    again = nwc.statistics_result(model, groups, unit=sc.NANOWELLS, test="mannwhitney", cache=cache)
    assert len(calls) == 3 and again.samples[0].mean() == pytest.approx(3.5)


def test_the_views_choices_are_kept_and_a_damaged_block_restores_the_defaults():
    state = NanowellState(view="statistics", stats_unit="wells", stats_test="ks")
    restored = NanowellState()
    restored.apply_config(state.to_config())
    assert (restored.view, restored.stats_unit, restored.stats_test) == ("statistics", "wells", "ks")
    restored.apply_config({"stats_unit": "pooled", "stats_test": "anova"})
    assert (restored.stats_unit, restored.stats_test) == ("nanowells", "welch")


# ── the page ─────────────────────────────────────────────────────────────────
pytest.importorskip("PySide6")

from tests.test_nanowell_page import _loaded, _page, _texts, _write_nanowell_dataset  # noqa: E402

#: Four wells, two timepoints, four nanowells each; None is occluded. A01 and A02 are alike, B01 and B02 are not.
PLATE = {"A01": {"t_c": [1, 2, 0, None], "t_a": [2, 4, 3, 1]},
         "A02": {"t_c": [1, 1, 1, 1], "t_a": [2, 3, 3, 2]},
         "B01": {"t_c": [3, 0, 1, 1], "t_a": [6, None, 7, 9]},
         "B02": {"t_c": [2, 2, 1, 1], "t_a": [8, 7, 9, 8]}}


@pytest.fixture
def page(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=PLATE))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def _choose(ctx, name, data):
    combo = ctx.widgets.get(name)
    combo.setCurrentIndex(combo.findData(data))          # emits currentIndexChanged, as a choice does


def _open_statistics(qtbot, ctx):
    from review.features.nanowell.tab_view import VIEWS

    st = _loaded(qtbot, ctx)
    ctx.widgets.get("_nw_view_seg").setCurrentIndex([k for k, _l in VIEWS].index("statistics"))
    return st


def _report(ctx, gid):
    return _texts(ctx, gid)


def test_the_statistics_view_tests_the_wells_drawn_and_names_what_it_compared(page, qtbot):
    state = _open_statistics(qtbot, page)
    assert state.view == "statistics"
    seg = page.widgets.get("_nw_view_seg")
    assert seg.segmentText(seg.count() - 1) == "Statistics"
    header = " ".join(_report(page, sc.GID_HEADER))
    assert header.startswith("t-test (Welch, unequal var) · Cell count · at t_a")
    assert "This compares these wells, not the conditions behind them" in header
    samples = " ".join(_report(page, sc.GID_SAMPLE))
    assert "A01:  n = 4   mean = 2.5" in samples
    # B01's occluded nanowell is not a value and is counted, not dropped without a word.
    assert "B01:  n = 3   mean = 7.333" in samples and "(1 of 4 nanowells excluded)" in samples
    pair = " ".join(_report(page, sc.GID_PAIR))
    expected = st.ttest_ind([2, 4, 3, 1], [6, 7, 9], equal_var=False)
    assert pair.startswith(f"A01 vs B01   t = {expected.statistic:.4g}   {p_phrase(expected.pvalue)}   ")
    # The boxes are drawn, one per well.
    ax = page.widgets.get("_nw_fig").axes[-1]             # the plot; the first is the report's ground
    assert [t.get_text() for t in ax.get_xticklabels()] == ["A01\nn = 4", "B01\nn = 3"]


def test_the_band_shows_the_views_controls_and_each_one_changes_the_test(page, qtbot):
    page.state.selection.selected_wells = {"A01", "A02", "B01"}
    _open_statistics(qtbot, page)
    for name in ("_nw_stats_unit_cb", "_nw_stats_test_cb", "_nw_control_cb", "_nw_tp_cb", "_nw_metric_cb"):
        assert page.widgets.get(name).isVisibleTo(page.window), name
    for name in ("_nw_axis_seg", "_nw_well_cb", "_nw_extremes_spin"):
        assert not page.widgets.get(name).isVisibleTo(page.window), name
    assert " ".join(_report(page, sc.GID_OMNIBUS)).startswith("Omnibus: One-way ANOVA   F(2, 8)")
    assert len(_report(page, sc.GID_PAIR)) >= 3

    _choose(page, "_nw_stats_test_cb", "mannwhitney")
    u = st.mannwhitneyu([2, 4, 3, 1], [6, 7, 9], alternative="two-sided")
    assert any(t.startswith(f"A01 vs B01   U = {u.statistic:.4g}   ") for t in _report(page, sc.GID_PAIR))
    assert " ".join(_report(page, sc.GID_OMNIBUS)).startswith("Omnibus: Kruskal-Wallis")

    # The vs combo lists the wells on this unit; choosing one compares each other well with it.
    control = page.widgets.get("_nw_control_cb")
    assert [control.itemData(i) for i in range(control.count())] == ["", "A01", "A02", "B01"]
    _choose(page, "_nw_control_cb", "A02")
    pairs = [t for t in _report(page, sc.GID_PAIR) if " vs " in t]
    assert [t.split("   ")[0] for t in pairs] == ["A01 vs A02", "B01 vs A02"]

    # Dunnett runs against that control, corrected for its family.
    _choose(page, "_nw_stats_test_cb", "dunnett")
    pairs = " ".join(_report(page, sc.GID_PAIR))
    expected = st.dunnett(np.array([2.0, 4, 3, 1]), np.array([6.0, 7, 9]), control=np.array([2.0, 3, 3, 2]),
                          random_state=_SEED)
    assert f"A01 vs A02   t = {expected.statistic[0]:.4g}   p_Dunnett = " in pairs

    # Another timepoint is another test: at t_c, A01's occluded nanowell is the excluded one.
    _choose(page, "_nw_stats_test_cb", "welch")
    _choose(page, "_nw_tp_cb", "t_c")
    assert "· at t_c" in _report(page, sc.GID_HEADER)[0]
    assert "A01:  n = 3   mean = 1" in " ".join(_report(page, sc.GID_SAMPLE))


def test_groups_compare_one_median_per_well(page, qtbot, monkeypatch):
    from review.domain import plot_members
    from review.domain.batch_models import ReplicateSet

    monkeypatch.setattr(plot_members, "plot_replicate_sets",
                        lambda ctx: [ReplicateSet("ctrl", ["A01", "A02"]), ReplicateSet("drug", ["B01", "B02"])])
    page.state.selection.selected_wells = {"A01", "A02", "B01", "B02"}
    _open_statistics(qtbot, page)
    _choose(page, "_nw_stats_unit_cb", sc.WELLS)
    assert "one median per well (n = wells)" in " ".join(_report(page, sc.GID_HEADER))
    control = page.widgets.get("_nw_control_cb")
    assert [control.itemData(i) for i in range(control.count())] == ["", "ctrl", "drug"]
    samples = " ".join(_report(page, sc.GID_SAMPLE))
    # ctrl's wells' medians are 2.5 and 2.5; drug's 7 and 8. Pooling the nanowells would give n = 8 and n = 7.
    assert "ctrl:  n = 2   mean = 2.5" in samples and "drug:  n = 2   mean = 7.5" in samples
    t = st.ttest_ind([2.5, 2.5], [7.0, 8.0], equal_var=False)
    assert " ".join(_report(page, sc.GID_PAIR)).startswith(f"ctrl vs drug   t = {t.statistic:.4g}   {p_phrase(t.pvalue)}")


def test_the_moving_average_distribution_draws_is_the_one_statistics_tests(page, qtbot):
    """Tracker M9: Statistics tests the dots Distribution draws *Along x: Wells*, so the moving average set there is
    applied to the values tested, the control stays on the band, and the report states the window. A Statistics
    view that tested the raw values while Distribution drew averaged ones would compare A01's n = 4, mean 2.5."""
    from review.features.nanowell.tab_view import VIEWS

    state = _loaded(qtbot, page)
    _choose(page, "_nw_metric_cb", "n")
    page.widgets.get("_nw_axis_seg").setCurrentIndex(1)                    # Along x: Wells
    page.widgets.get("_nw_moving_spin").setValue(2)
    assert (state.axis, state.moving) == ("wells", 2)
    page.widgets.get("_nw_view_seg").setCurrentIndex([k for k, _l in VIEWS].index("statistics"))
    assert state.view == "statistics"
    assert page.widgets.get("_nw_moving_spin").isVisibleTo(page.window)
    # At t_a over 2 timepoints: A01 (1+2)/2, (2+4)/2, (0+3)/2 and its fourth is occluded at t_c, so no value;
    # B01 (3+6)/2, then occluded at t_a, then (1+7)/2, (1+9)/2.
    samples = " ".join(_report(page, sc.GID_SAMPLE))
    assert "A01:  n = 3   mean = 2   " in samples and "B01:  n = 3   mean = 4.5   " in samples
    pair = " ".join(_report(page, sc.GID_PAIR))
    expected = st.ttest_ind([1.5, 3.0, 1.5], [4.5, 4.0, 5.0], equal_var=False)
    assert pair.startswith(f"A01 vs B01   t = {expected.statistic:.4g}   ")
    notes = _report(page, sc.GID_NOTE)
    assert any(n.startswith("Moving average over 2 timepoints") for n in notes), notes
