"""The Nanowells page's pure layer: the beeswarm geometry and the Distribution model (tracker V3b, V3d, V3e).

The model is what the figure draws, so asserting it here asserts the figure's numbers; the figure's own text
is read back in ``test_nanowell_page.py``.
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from review.features.nanowell import distribution_compute as dc
from review.features.nanowell.beeswarm_compute import BAND, swarm_layout
from review.features.nanowell.collect import load_nanowell_data
from review.features.nanowell.series_compute import site_metrics
from tests.test_nanowell_review_data import STARTS, _dataset, _row


# ── the beeswarm ─────────────────────────────────────────────────────────────
def _overlaps(layout, dot_w, dot_h) -> int:
    pts = np.column_stack([layout.x, layout.y])
    n = 0
    for i in range(len(pts)):
        d = np.abs(pts[i + 1:] - pts[i])
        n += int(np.sum((d[:, 0] < dot_w - 1e-9) & (d[:, 1] < dot_h - 1e-9)))
    return n


def test_a_continuous_swarm_keeps_every_value_exact_and_no_two_dots_overlap():
    values = np.random.default_rng(1).normal(0.4, 0.1, 400)
    layout = swarm_layout(values, half_width=0.45, dot_w=0.02, dot_h=0.004)
    assert np.array_equal(layout.y, values[layout.index])
    assert _overlaps(layout, 0.02, 0.004) == 0
    assert len(layout.index) + layout.n_hidden == 400
    assert np.all(np.abs(layout.x) <= 0.45)


def test_integer_values_are_packed_within_the_band_and_overflow_is_counted_by_value():
    values = np.array([0] * 300 + [1] * 40 + [5] * 3 + [np.nan] * 7, dtype=float)
    layout = swarm_layout(values, half_width=0.45, dot_w=0.05, dot_h=0.1)
    drawn = values[layout.index]
    assert np.all(np.abs(layout.y - drawn) <= BAND + 1e-9)
    assert _overlaps(layout, 0.05, 0.1) == 0
    capacity = 17 * 7                           # 17 slots across 0.9; rows at 0, ±0.1, ±0.2, ±0.3 inside ±0.4
    assert layout.overflow == {0.0: 300 - capacity}
    assert len(layout.index) + layout.n_hidden == 343           # NaN are not dots: the caller counts them


def test_a_value_with_few_dots_is_drawn_on_its_value_not_below_it():
    values = np.array([0.0] * 3 + [1.0] + [2.0] * 2)
    layout = swarm_layout(values, half_width=0.45, dot_w=0.05, dot_h=0.1)
    assert set(layout.y) == {0.0, 1.0, 2.0}                     # one row's worth: the centre row, on the value


def test_dots_taller_than_the_band_are_laid_out_exactly_and_never_overlap_across_values():
    values = np.random.default_rng(3).poisson(15, 600).astype(float)
    layout = swarm_layout(values, half_width=0.45, dot_w=0.05, dot_h=1.03)
    assert np.array_equal(layout.y, values[layout.index])
    assert _overlaps(layout, 0.05, 1.03) == 0
    assert len(layout.index) + layout.n_hidden == 600


def test_a_crowded_continuous_swarm_reports_its_hidden_dots_once_with_their_range():
    values = np.random.default_rng(4).uniform(0, 1, 2000)
    layout = swarm_layout(values, half_width=0.45, dot_w=0.05, dot_h=0.01)
    assert len(layout.overflow) == 1 and layout.n_hidden == 2000 - len(layout.index) > 0
    lo, hi = layout.overflow_range
    assert lo == min(layout.overflow) and 0 <= lo < hi <= 1


def test_exact_rows_keeps_integer_values_on_their_value():
    layout = swarm_layout(np.array([2.0] * 5), half_width=0.45, dot_w=0.05, dot_h=0.1, exact=True)
    assert set(layout.y) == {2.0}


# ── the Distribution model ───────────────────────────────────────────────────
def _plate(tmp_path, per_well: dict, tps=("t_c", "t_a")):
    """``per_well[well] = {timepoint: [n per nanowell]}``; None marks an occluded nanowell."""
    wells = {}
    for well, by_tp in per_well.items():
        rows = []
        for tp in tps:
            for q, n in enumerate(by_tp[tp]):
                rows.append(_row(tp, q, 0, state="occluded") if n is None
                            else _row(tp, q, 0, n=n, c=0.1 * n, state="empty" if n == 0 else "measured"))
        wells[well] = rows
    data = load_nanowell_data(_dataset(tmp_path, wells, starts={k: STARTS[k] for k in tps}))
    return site_metrics(data), data


def test_over_time_each_well_is_a_panel_and_every_nanowell_is_drawn_or_excluded_by_name(tmp_path):
    metrics, data = _plate(tmp_path, {"A01": {"t_c": [1, 2, 0, None], "t_a": [2, None, 4, 3]},
                                      "B01": {"t_c": [1, 1, 1, 1], "t_a": [3, 3, 3, 3]}})
    model = dc.distribution_model(metrics, "n", channels=data.channels, axis=dc.TIME,
                                  groups={"A01": ["A01"], "B01": ["B01"]}, time_source=data.time_source)
    assert [p.title for p in model.panels] == ["A01", "B01"]
    a = model.panels[0]
    assert [s.label for s in a.swarms] == ["t_c", "t_a"]
    assert sorted(a.swarms[0].values) == [0.0, 1.0, 2.0] and a.swarms[0].excluded == {"occluded": 1}
    assert a.medians["A01"] == [(0.0, 1.0), (1.0, 3.0)]
    assert model.n_used + sum(model.excluded().values()) == model.n_roster == 16
    assert model.integer and model.notes[0].startswith("Whole-number values are packed within ±0.4")


def test_a_replicate_set_over_time_is_one_panel_with_each_well_its_own_sub_swarm(tmp_path):
    metrics, data = _plate(tmp_path, {w: {"t_c": [1, 1], "t_a": [2, 2]} for w in ("A01", "A02", "B01")})
    model = dc.distribution_model(metrics, "n", channels=data.channels, axis=dc.TIME,
                                  groups={"dose 1": ["A01", "A02"], "B01": ["B01"]})
    first = model.panels[0]
    assert first.title == "dose 1 (2 wells)"
    assert [(s.label, s.well) for s in first.swarms] == [("t_c", "A01"), ("t_c", "A02"), ("t_a", "A01"), ("t_a", "A02")]
    assert first.swarms[0].x < 0 < first.swarms[1].x                           # side by side, never pooled
    assert set(first.medians) == {"A01", "A02"}


def test_along_wells_takes_the_latest_timepoint_and_states_its_statistic_over_wells(tmp_path):
    metrics, data = _plate(tmp_path, {"A01": {"t_c": [1, 1, 1], "t_a": [4, 4, 4]},
                                      "A02": {"t_c": [1, 1], "t_a": [2, 2]},
                                      "C01": {"t_c": [1], "t_a": [1]}, "C02": {"t_c": [1], "t_a": [3]}})
    model = dc.distribution_model(metrics, "count_fc", channels=data.channels, axis=dc.WELLS,
                                  groups={"treated": ["A01", "A02"], "control": ["C01", "C02"]}, control="control")
    assert model.timepoint == "t_a" and len(model.panels) == 1
    stats = {s.group: s for s in model.panels[0].stats}
    assert stats["treated"].centre == 3.0 and stats["treated"].n_wells == 2 and stats["treated"].df == 1
    assert stats["treated"].vs_control == pytest.approx(1.5)
    assert any("df 1" in note and "nanowells are not replicates" in note for note in model.notes)
    assert [t[1] for t in model.panels[0].ticks] == ["treated", "control"]


def test_a_metric_with_one_value_per_nanowell_is_drawn_across_wells_once(tmp_path):
    metrics, data = _plate(tmp_path, {"A01": {"t_c": [0, 1, 5], "t_a": [1, 2, 6]}})
    model = dc.distribution_model(metrics, "occupancy_class", channels=data.channels, axis=dc.TIME,
                                  groups={"A01": ["A01"]})
    assert model.axis == dc.WELLS and model.n_roster == 3
    assert sorted(model.panels[0].swarms[0].values) == [0.0, 1.0, 3.0]


def test_a_channel_metric_takes_the_shared_selector_s_channel():
    assert dc.match_channel("gfp", ["GFP", "RFP"]) == "GFP" and dc.match_channel("phase", ["GFP"]) is None
    assert dc.metric_column("brightness_fc", "GFP") == "brightness_fc_GFP"
    with pytest.raises(ValueError, match="needs a fluorescence channel"):
        dc.metric_column("signal", None)


def test_every_reason_the_data_layer_can_give_has_a_figure_label():
    from review.features.nanowell.collect import MEASURABLE, NOT_LOCATED, UNADDRESSED
    from review.features.nanowell.series_compute import ABSENT, _NEVER

    states = ["occluded", "clipped", UNADDRESSED, NOT_LOCATED, ABSENT]
    value_reasons = ["fused (no count)", "no count", "no confluence", "no cells", "no fluorescence"]
    reasons = (states + value_reasons + [f"baseline {s}" for s in states + [_NEVER]]
               + [f"baseline: {v}" for v in value_reasons]
               + ["seeded later", "empty at baseline", "no confluence at baseline",
                  "no signal above background at baseline", "fewer than three timepoints with cells and a time",
                  "no count at baseline"])
    assert MEASURABLE.isdisjoint(states)
    for reason in reasons:
        assert dc.reason_label(reason)
    with pytest.raises(KeyError):
        dc.reason_label("a reason nobody wrote a label for")


def test_every_reason_produced_on_a_varied_dataset_has_a_label(tmp_path):
    """Run every metric over a dataset with each state, a fused site and a seeded-later site."""
    from review.features.nanowell.series_compute import exclusion_reason, metric_kinds
    from tests.test_nanowell_review_data import _series_well

    rows = _series_well() + [_row("t_c", 2, 0, n=None, c=0.5, fused=True), _row("t_a", 2, 0, state="clipped"),
                             _row("t_d", 2, 0, n=None, c=0.2, fused=True), _row("t_b", 2, 0, n=1, c=0.1)]
    metrics = site_metrics(load_nanowell_data(_dataset(tmp_path, {"A01": rows})), "first_measurable")
    seen = set()
    for metric in metric_kinds(["GFP"]):
        seen |= set(exclusion_reason(metrics, metric, ["GFP"])) - {""}
    assert len(seen) >= 8
    for reason in seen:
        assert dc.reason_label(reason)


def test_the_state_s_metric_keys_are_the_page_s_metrics():
    from review.features.nanowell.state import METRIC_KEYS

    assert tuple(dc.METRICS) == METRIC_KEYS



def test_a_fold_change_on_one_timepoint_is_not_packed_like_a_count(tmp_path):
    metrics, data = _plate(tmp_path, {"A01": {"t_c": [1, 2, 3]}}, tps=("t_c",))
    for metric, integer in (("count_fc", False), ("n", True), ("occupancy_class", True)):
        model = dc.distribution_model(metrics, metric, channels=data.channels, groups={"A01": ["A01"]})
        assert model.integer is integer, metric
        assert any("packed" in n for n in model.notes) is integer


def test_per_nanowell_metrics_keep_every_well_when_wells_start_at_different_timepoints(tmp_path):
    rows_a = {"t_c": [1, 2], "t_a": [2, 4], "t_d": [4, 8]}
    metrics, data = _plate(tmp_path, {"A01": rows_a}, tps=("t_c", "t_a", "t_d"))
    tmp2 = tmp_path / "second"
    tmp2.mkdir()
    other, _ = _plate(tmp2, {"A02": {"t_a": [1, 1], "t_d": [2, 2], "t_b": [4, 4]}}, tps=("t_a", "t_d", "t_b"))
    both = pd.concat([metrics, other], ignore_index=True)
    model = dc.distribution_model(both, "occupancy_class", channels=data.channels,
                                  groups={"set": ["A01", "A02"]})
    stat = model.panels[0].stats[0]
    assert stat.n_wells == 2 and stat.wells_without_value == []


def test_past_the_panel_cap_the_footer_counts_only_what_is_drawn():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.features.nanowell.renderer import MAX_PANELS, render_distribution

    swarms = [dc.Swarm("t", f"W{i:02d}", 0.0, np.ones(10), [(f"W{i:02d}", j, 0) for j in range(10)], ["1"] * 10, 10)
              for i in range(MAX_PANELS + 2)]
    model = dc.DistributionModel("n", "n", "Cell count", dc.TIME, True,
                                 [dc.Panel(s.well, [s], [(0.0, "t")]) for s in swarms])
    fig = Figure(figsize=(8, 6.4))
    render_distribution(fig, model)
    texts = [t.get_text() for t in fig.texts]
    assert texts and any(t.startswith(f"Nanowell × timepoint values used {MAX_PANELS * 10} of {MAX_PANELS * 10}")
                         for t in texts)
    assert any(t.startswith("2 more panel(s) not drawn (20 values") for t in texts)


def test_a_set_of_more_than_four_wells_is_drawn_as_densities_and_says_so(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.features.nanowell.renderer import render_distribution

    wells = [f"A{i:02d}" for i in range(1, 6)]
    metrics, data = _plate(tmp_path, {w: {"t_c": [1, 2, 3], "t_a": [2, 3, 5]} for w in wells})
    model = dc.distribution_model(metrics, "n", channels=data.channels, groups={"set": wells})
    fig = Figure(figsize=(8, 6.4))
    hits = render_distribution(fig, model)
    assert not hits.entries                                      # no dots to click: densities
    assert any(c.get_gid() == "nw-density" for c in fig.axes[0].collections)
    assert any("drawn as densities" in t.get_text() for t in fig.texts)


def test_the_comparison_to_control_keeps_three_significant_figures():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.features.nanowell.renderer import render_distribution

    swarm = dc.Swarm("A01", "A01", 0.0, np.array([1.0, 1.1]), [("A01", 0, 0), ("A01", 1, 0)], ["1", "1"], 2)
    stat = dc.GroupStat("g", 0.0, 1.04, 1.0, 1.08, 2, 1, 1.04, [])
    model = dc.DistributionModel("count_fc", "count_fc", "Count fold change", dc.WELLS, False,
                                 [dc.Panel("t", [swarm], [(0.0, "g")], stats=[stat])])
    fig = Figure(figsize=(8, 6.4))
    render_distribution(fig, model)
    assert [t.get_text() for t in fig.axes[0].texts if t.get_gid() == "nw-vs-control"] == [" ×1.04"]


def test_a_swarm_too_full_for_its_slot_says_how_many_dots_are_hidden_under_it():
    """Read from the figure: each swarm's 'n =' is every value, '+N at v' the ones not drawn as dots, and the
    dots on the axes are the difference — so a gutter that counted only the drawn dots would be caught."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.features.nanowell.renderer import render_distribution

    values = np.array([0.0] * 3000 + [1.0] * 40)
    swarm = dc.Swarm("t_c", "A01", 0.0, values, [("A01", i, 0) for i in range(3040)], ["0"] * 3000 + ["1"] * 40,
                     3050, {"occluded": 10})
    model = dc.DistributionModel("n", "n", "Cell count", dc.TIME, True, [dc.Panel("A01", [swarm], [(0.0, "t_c")])])
    fig = Figure(figsize=(5, 4))
    hits = render_distribution(fig, model)
    ax = fig.axes[0]
    texts = {t.get_gid(): t.get_text() for t in ax.texts}
    drawn = sum(len(entry[1]) for entry in hits.entries)
    hidden = int(texts["nw-hidden"].split(" ")[0].lstrip("+").replace(",", ""))
    assert texts["nw-used"] == "n = 3,040" and texts["nw-excluded"] == "excl. 10"
    assert texts["nw-hidden"].endswith("at 0") and drawn + hidden == 3040 and hidden > 0
