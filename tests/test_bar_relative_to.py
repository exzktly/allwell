"""The Bar tab's *Then relative to*: each bar over its baseline bar, and the test.

The plate is the reported design — RNA (WT / Mut) × Treatment (Vehicle / Drug),
three wells each — with group means WT-veh 100, WT-drug 200, Mut-veh 50,
Mut-drug 150. Stage 1 (per-sample control, RNA = WT within each Treatment)
gives Mut-drug 0.75 and Mut-veh 0.5, so R = 1.5. The draw order puts the
mutant groups between the WT groups, so a rule matching by position or by first
candidate picks a different baseline (CLAUDE.md §7.8).
"""
from __future__ import annotations

import math

import pytest

from review.domain import ratio_of_ratios as rr
from review.domain import sample_variables as sv
from review.domain import stats_tests as st
from review.domain import variable_pairing as vp
from review.domain.control_ref import ControlRef
from review.features.barplot.collect import FC_STATE_OFF, collect_bar_items

from tests.conftest import MockApp, make_agg

_FACTORS = (0.9, 1.0, 1.1)
_GROUPS = {          # name: (row, rna, treatment, mean)
    "WT-veh": ("A", "WT", "Vehicle", 100.0),
    "Mut-drug": ("B", "Mut", "Drug", 150.0),
    "Mut-veh": ("C", "Mut", "Vehicle", 50.0),
    "WT-drug": ("D", "WT", "Drug", 200.0),
}


def _value(vset, vid, label):
    return next(v.id for v in vset.by_id(vid).values if v.label == label)


def _app(*, factors=_FACTORS):
    pts, sels, wells_of = {}, [], {}
    vset, rna = sv.new_variable(sv.VariableSet(), "RNA")
    vset, treat = sv.new_variable(vset, "Treatment")
    for vid, labels in ((rna, ("WT", "Mut")), (treat, ("Vehicle", "Drug"))):
        for label in labels:
            vset = sv.add_value(vset, vid, label)
    for name, (row, r, t, mean) in _GROUPS.items():
        wells = [f"{row}0{i + 1}" for i in range(len(factors))]
        wells_of[name] = wells
        for well, f in zip(wells, factors):
            pts[well] = [make_agg(0.0, mean * f)]
        sels.append({"name": name, "wells": wells, "hidden": False})
        vset = sv.assign(vset, rna, wells, _value(vset, rna, r))
        vset = sv.assign(vset, treat, wells, _value(vset, treat, t))
    app = MockApp(per_well_pts=pts, selections=sels)
    ctx = app._ctx
    ctx.state.selection.variables = vset
    plot = ctx.state.plot
    plot.fc_vs_control_on = True
    plot.fc_control_mode = "per_member"
    member_wells = {n: wells_of[n] for n in _GROUPS}
    plot.fc_control_map = vp.pair_by_variable(
        vset, member_wells, {n: ControlRef.for_replicate_set(n) for n in _GROUPS},
        variable_id=rna, value_id=_value(vset, rna, "WT"), within=[treat]).mapping
    ids = {"rna": rna, "treat": treat, "veh": _value(vset, treat, "Vehicle")}
    return ctx, ids


def _on(ctx, ids):
    bar = ctx.state.feature_slice("bar")
    bar.rel_variable_id, bar.rel_value_id = ids["treat"], ids["veh"]
    bar.rel_within = [ids["rna"]]
    return bar


def _items(ctx, **kw):
    _use, items, _lbl = collect_bar_items(ctx, 0.0, **kw)
    return {it.key: it for it in items}


def test_the_mutant_under_drug_is_its_ratio_over_the_vehicle_ratio():
    ctx, ids = _app()
    _on(ctx, ids)
    items = _items(ctx)
    drug = items["Mut-drug"]
    assert drug.rel_outcome == rr.OK and drug.rel_baseline == "Mut-veh"
    assert drug.mean == pytest.approx((150 / 200) / (50 / 100))
    assert drug.stage1[0] == pytest.approx(150 / 200)
    for ref in ("WT-veh", "WT-drug", "Mut-veh"):
        assert items[ref].rel_outcome == rr.REFERENCE, ref


def test_the_interaction_test_is_run_on_the_bar_s_four_conditions():
    ctx, ids = _app()
    _on(ctx, ids)
    test = _items(ctx)["Mut-drug"].interaction
    cells = {name: [mean * f for f in _FACTORS] for name, (_r, _a, _b, mean) in _GROUPS.items()}
    expected = st.contrast_t_test(
        cells, {"Mut-drug": 1, "WT-drug": -1, "Mut-veh": -1, "WT-veh": 1}, log=True)
    assert test.ok, test.undefined
    assert (test.estimate, test.p, test.df) == pytest.approx(
        (expected.estimate, expected.p, expected.df))
    assert _items(ctx)["Mut-drug"].interaction_label == "interaction, log scale"


def test_the_raw_pass_and_an_off_rule_are_untouched():
    ctx, ids = _app()
    plain = _items(ctx)["Mut-drug"]
    _on(ctx, ids)
    raw = _items(ctx, fc_state=FC_STATE_OFF)["Mut-drug"]
    assert raw.rel_outcome == "" and raw.mean == pytest.approx(150.0)
    assert plain.rel_outcome == "" and plain.mean == pytest.approx(0.75)


def test_with_a_single_control_it_is_declined_and_says_so():
    ctx, ids = _app()
    bar = _on(ctx, ids)
    ctx.state.plot.fc_control_mode = "single"
    item = _items(ctx)["Mut-drug"]
    assert item.rel_outcome == ""
    assert "needs Fold change vs a per-sample control" in bar.rel_notice
    assert bar.rel_applied is False


def test_with_vs_t0_on_it_is_declined_and_says_so():
    ctx, ids = _app()
    bar = _on(ctx, ids)
    ctx.state.plot.fc_vs_t0_on = True
    _items(ctx)
    assert "Baseline (vs t0) is set" in bar.rel_notice and not bar.rel_applied


def test_a_missing_baseline_is_a_named_gap():
    ctx, ids = _app()
    bar = _on(ctx, ids)
    vset = ctx.state.selection.variables
    ctx.state.selection.variables = sv.assign(vset, ids["treat"],
                                              ["C01", "C02", "C03"], None)
    item = _items(ctx)["Mut-drug"]
    assert item.rel_outcome == rr.NO_BASELINE and not item.has_mean
    assert math.isnan(item.mean)
    assert "Mut-drug: no sample with Treatment = Vehicle and RNA = Mut" in bar.rel_notice


def test_one_well_per_condition_draws_the_bar_and_names_the_missing_test():
    ctx, ids = _app(factors=(1.0,))
    bar = _on(ctx, ids)
    item = _items(ctx)["Mut-drug"]
    assert item.mean == pytest.approx(1.5)
    assert not item.interaction.ok
    assert "no test — no replicate spread" in bar.rel_notice


def test_the_note_names_the_rule_and_the_estimate():
    ctx, ids = _app()
    bar = _on(ctx, ids)
    _items(ctx)
    assert bar.rel_notice.startswith("Relative to Treatment = Vehicle within each RNA.")
    assert "ratio of geometric means" in bar.rel_notice


def test_the_csv_s_raw_pass_does_not_overwrite_the_figure_s_note():
    """The export collects a raw pass after the figure is drawn; the note and the
    axis suffix the figure reads must still describe the live pass."""
    ctx, ids = _app()
    bar = _on(ctx, ids)
    _items(ctx)
    notice = bar.rel_notice
    _items(ctx, fc_state=FC_STATE_OFF)
    assert bar.rel_applied is True and bar.rel_notice == notice


def test_one_shared_stage_one_control_is_refused_not_drawn_as_a_ratio_of_ratios():
    """The reported wrong number. With every bar divided by WT-veh, the control
    cancels and R would be Mut-drug / Mut-veh — 3.0 on this plate, against a
    true ratio of ratios of 1.5 — drawn on the same axis under the same label."""
    from review.domain.control_ref import ControlRef

    ctx, ids = _app()
    bar = _on(ctx, ids)
    ctx.state.plot.fc_control_map = {n: ControlRef.for_replicate_set("WT-veh") for n in _GROUPS}
    drug = _items(ctx)["Mut-drug"]
    assert drug.rel_outcome == rr.NOT_CROSSED and not drug.has_mean
    assert math.isnan(drug.mean)
    assert "WT-veh is not at its own Treatment" in bar.rel_notice


def test_swapped_stage_one_controls_are_refused():
    """Four distinct conditions, the wrong four: Mut-drug over WT-veh and Mut-veh
    over WT-drug is a valid-looking contrast that is not the interaction."""
    from review.domain.control_ref import ControlRef

    ctx, ids = _app()
    bar = _on(ctx, ids)
    S = ControlRef.for_replicate_set
    ctx.state.plot.fc_control_map = {
        "WT-veh": S("WT-veh"), "WT-drug": S("WT-drug"),
        "Mut-drug": S("WT-veh"), "Mut-veh": S("WT-drug")}
    assert _items(ctx)["Mut-drug"].rel_outcome == rr.NOT_CROSSED
    assert "would not be a ratio of ratios" in bar.rel_notice
