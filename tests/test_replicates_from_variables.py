"""*Make replicates from variables* (Home → Sample Map → Replicates), Qt-free half.

Wells whose variable assignments are identical become one replicate group.
The button itself is pressed in ``tests/test_replicates_from_variables_app.py``.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.domain import sample_variables as sv
from review.features.home.groups.controller import (
    _made_replicates_sentence, replicates_from_variables,
)


def _var(vid, name, *labels, unit=""):
    return sv.Variable(id=vid, name=name, kind=sv.TEXT, unit=unit,
                       values=tuple(sv.VariableValue(f"{vid}{i}", lab, None)
                                    for i, lab in enumerate(labels)))


def _ctx(wells, variables, assignments, group_by=""):
    return SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: f"/{w}.csv" for w in wells}),
        selection=SimpleNamespace(
            selections=[], variables=sv.VariableSet(
                variables=tuple(variables), assignments=assignments)),
        plot=SimpleNamespace(group_by_variable=group_by),
    ))


# HeLa is the *second* row on the plate but the *first* declared value, so a
# result that followed plate order instead of the variables' order comes out
# with U2OS first. HeLa · 1 µM is split across B02 and B04 with a different
# condition between them, so grouping adjacent wells cannot pass either.
_LINE = _var("line", "Cell line", "HeLa", "U2OS")
_DOSE = _var("dose", "Dose", "1", "10", unit="µM")
_WELLS = ["A01", "A02", "A03", "B01", "B02", "B03", "B04", "C01", "C02", "C03"]
_ASSIGN = {
    "line": {"A01": "line1", "A02": "line1", "A03": "line1",
             "B01": "line0", "B02": "line0", "B04": "line0",
             "C01": "line0", "C02": "line0"},       # B03, C03: no cell line
    "dose": {"A01": "dose1", "A02": "dose0", "A03": "dose1",
             "B01": "dose1", "B02": "dose0", "B03": "dose0", "B04": "dose0"},
    # C01, C02: HeLa with no dose. C03: nothing at all.
}


def test_identical_assignments_are_replicates_of_one_group():
    ctx = _ctx(_WELLS, [_LINE, _DOSE], _ASSIGN)
    sets, left_out = replicates_from_variables(ctx)
    assert [(r.name, r.wells) for r in sets] == [
        ("HeLa · 1 µM", ["B02", "B04"]),
        ("HeLa · 10 µM", ["B01"]),
        # Same recorded condition (HeLa, no dose) -> replicates; the name
        # carries its wells because it does not describe a whole condition.
        ("HeLa (C01/C02)", ["C01", "C02"]),
        ("U2OS · 1 µM", ["A02"]),
        ("U2OS · 10 µM", ["A01", "A03"]),
        ("1 µM (B03)", ["B03"]),
    ]
    assert all(r.name_is_verbatim for r in sets)
    assert left_out == ["C03"]


def test_one_variable_alone_does_not_pool_across_the_others():
    # The defect this replaced: grouping by Cell line put HeLa's two doses in
    # one "replicate" group.
    ctx = _ctx(_WELLS, [_LINE, _DOSE], _ASSIGN)
    sets, _ = replicates_from_variables(ctx)
    assert not any({"B01", "B02"} <= set(r.wells) for r in sets)


def test_an_unfilled_variable_does_not_split_or_rename_anything():
    empty = _var("time", "Timepoint")              # begun, never filled
    sets, left_out = replicates_from_variables(
        _ctx(_WELLS, [_LINE, _DOSE, empty], _ASSIGN))
    plain, _ = replicates_from_variables(_ctx(_WELLS, [_LINE, _DOSE], _ASSIGN))
    assert [(r.name, r.wells) for r in sets] == [(r.name, r.wells) for r in plain]
    assert left_out == ["C03"]


def test_a_value_the_definition_no_longer_holds_counts_as_no_value():
    assign = {"dose": {"A01": "dose0", "A02": "dose0", "A03": "gone"}}
    groups, left_out = sv.replicate_groups(
        sv.VariableSet(variables=(_DOSE,), assignments=assign),
        ["A01", "A02", "A03"])
    assert groups == [["A01", "A02"]]
    assert left_out == ["A03"]


def test_no_variables_makes_nothing_and_leaves_every_well_out():
    sets, left_out = replicates_from_variables(_ctx(["A02", "A01"], [], {}))
    assert sets == []
    assert left_out == ["A01", "A02"]


def test_the_sentence_names_single_wells_and_wells_left_out():
    ctx = _ctx(_WELLS, [_LINE, _DOSE], _ASSIGN)
    sets, left_out = replicates_from_variables(ctx)
    assert _made_replicates_sentence(ctx, sets, left_out) == (
        "Made 6 replicate groups from the variables. 3 groups have a single "
        "well (HeLa · 10 µM, U2OS · 1 µM, 1 µM (B03)). 1 well with no values "
        "is in no group (C03).")


def test_the_sentence_says_when_a_variable_grouping_still_hides_the_groups():
    ctx = _ctx(["A01", "A02"], [_DOSE], {"dose": {"A01": "dose0", "A02": "dose0"}},
               group_by="dose")
    sets, left_out = replicates_from_variables(ctx)
    assert _made_replicates_sentence(ctx, sets, left_out) == (
        "Made 1 replicate group from the variables. The plots are still "
        "grouped by a variable — set Group by to Wells / groups on the "
        "plotting band to plot these groups.")
