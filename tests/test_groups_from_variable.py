"""*Make groups from variable* (Home → Sample Map → Replicates), Qt-free half.

What groups a variable's values become, what they are called, and which wells
are left out. The button itself is pressed in
``tests/test_groups_from_variable_app.py``.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.domain import sample_variables as sv
from review.features.home.groups.controller import (
    ALL_VARIABLES, _made_groups_sentence, groups_from_variable,
    variable_group_choices,
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


# Values assigned *against* plate order, so a result that followed the wells
# rather than the variable's declared value order would come out reversed.
_LINE = _var("line", "Cell line", "HeLa", "U2OS")
_DOSE = _var("dose", "Dose", "1", "10", unit="µM")
_WELLS = ["A01", "A02", "A03", "B01", "B02", "B03"]
_ASSIGN = {
    "line": {"A01": "line1", "A02": "line1", "A03": "line1",
             "B01": "line0", "B02": "line0"},          # B03: no cell line
    "dose": {"A01": "dose1", "A02": "dose0", "A03": "dose1",
             "B01": "dose1", "B02": "dose0", "B03": "dose0"},
}


def test_one_group_per_value_in_the_variable_s_own_order():
    ctx = _ctx(_WELLS, [_LINE, _DOSE], _ASSIGN)
    sets, left_out = groups_from_variable(ctx, "line")
    assert [(r.name, r.wells) for r in sets] == [
        ("HeLa", ["B01", "B02"]), ("U2OS", ["A01", "A02", "A03"])]
    assert all(r.name_is_verbatim for r in sets)
    assert left_out == ["B03"]


def test_every_variable_combined_crosses_them():
    ctx = _ctx(_WELLS, [_LINE, _DOSE], _ASSIGN)
    sets, left_out = groups_from_variable(ctx, ALL_VARIABLES)
    assert [(r.name, r.wells) for r in sets] == [
        ("HeLa · 1 µM", ["B02"]), ("HeLa · 10 µM", ["B01"]),
        ("U2OS · 1 µM", ["A02"]), ("U2OS · 10 µM", ["A01", "A03"])]
    # B03 has a dose but no cell line, so it fits no combination.
    assert left_out == ["B03"]


def test_combined_is_offered_only_when_there_is_something_to_combine():
    assert variable_group_choices(_ctx(_WELLS, [_DOSE], {})) == [("Dose", "dose")]
    assert variable_group_choices(_ctx(_WELLS, [_LINE, _DOSE], {})) == [
        ("Cell line", "line"), ("Dose", "dose"),
        ("Every variable combined", ALL_VARIABLES)]
    assert variable_group_choices(_ctx(_WELLS, [], {})) == []


def test_an_unknown_choice_makes_nothing():
    ctx = _ctx(_WELLS, [_DOSE], _ASSIGN)
    assert groups_from_variable(ctx, "deleted") == ([], [])


def test_the_sentence_names_the_wells_left_out():
    ctx = _ctx(_WELLS, [_LINE, _DOSE], _ASSIGN)
    sets, left_out = groups_from_variable(ctx, "line")
    assert _made_groups_sentence(ctx, "Cell line", sets, left_out) == (
        "Made 2 groups from Cell line. 1 well with no value is in no group (B03).")


def test_the_sentence_says_when_a_variable_grouping_still_hides_the_groups():
    ctx = _ctx(_WELLS, [_LINE, _DOSE], _ASSIGN, group_by="dose")
    sets, _ = groups_from_variable(ctx, "dose")
    assert _made_groups_sentence(ctx, "Dose", sets, []) == (
        "Made 2 groups from Dose. The plots are still grouped by a variable — "
        "set Group by to Wells / groups on the plotting band to plot these groups.")
