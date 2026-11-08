"""Pair-by-variable: each sample's control is chosen by what the plate says it is.

The fixtures are laid out **against** the draw order on purpose (CLAUDE.md
§7.8 #2). The members are interleaved — HeLa drug, U2OS vehicle, HeLa vehicle,
U2OS drug — so pairing by position (``auto_pair`` in blocks), pairing every
member with the first vehicle, and pairing by annotation each give a different
mapping. A fixture in block order would let any of those wrong rules pass.
"""
from __future__ import annotations

import pytest

from review.domain import sample_variables as sv
from review.domain import variable_pairing as vp
from review.domain.control_ref import ControlRef
from review.domain.fold_change_pairing import auto_pair

_SET = ControlRef.for_replicate_set


def _value(vset, vid, label):
    return next(v.id for v in vset.by_id(vid).values if v.label == label)


def _plate():
    """Two cell lines × (vehicle, drug at two doses), interleaved in draw order."""
    vset, line = sv.new_variable(sv.VariableSet(), "Cell line")
    vset, compound = sv.new_variable(vset, "Compound")
    vset, dose = sv.new_variable(vset, "Dose", kind=sv.NUMBER, unit="µM")
    for vid, labels in ((line, ("HeLa", "U2OS")), (compound, ("DMSO", "DrugA")),
                        (dose, ("0", "1", "10"))):
        for label in labels:
            vset = sv.add_value(vset, vid, label)
    members = {
        # name: (wells, cell line, compound, dose)
        "HeLa-A1":  (["A01", "A02"], "HeLa", "DrugA", "1"),
        "U2OS-veh": (["B01", "B02"], "U2OS", "DMSO", "0"),
        "HeLa-veh": (["C01", "C02"], "HeLa", "DMSO", "0"),
        "U2OS-A1":  (["D01", "D02"], "U2OS", "DrugA", "1"),
        "HeLa-A10": (["E01", "E02"], "HeLa", "DrugA", "10"),
        "U2OS-A10": (["F01", "F02"], "U2OS", "DrugA", "10"),
    }
    for wells, cl, cp, ds in members.values():
        vset = sv.assign(vset, line, wells, _value(vset, line, cl))
        vset = sv.assign(vset, compound, wells, _value(vset, compound, cp))
        vset = sv.assign(vset, dose, wells, _value(vset, dose, ds))
    member_wells = {k: v[0] for k, v in members.items()}
    refs = {k: _SET(k) for k in members}
    ids = {"line": line, "compound": compound, "dose": dose,
           "dmso": _value(vset, compound, "DMSO")}
    return vset, member_wells, refs, ids


def test_each_sample_pairs_with_the_vehicle_of_its_own_cell_line():
    vset, wells, refs, ids = _plate()
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id=ids["dmso"], within=[ids["line"]])
    assert pairing.mapping == {
        "HeLa-A1": _SET("HeLa-veh"), "U2OS-veh": _SET("U2OS-veh"),
        "HeLa-veh": _SET("HeLa-veh"), "U2OS-A1": _SET("U2OS-veh"),
        "HeLa-A10": _SET("HeLa-veh"), "U2OS-A10": _SET("U2OS-veh"),
    }
    assert pairing.unpaired == ()
    assert vp.summary_text(pairing) == "Paired 6 of 6."


def test_position_based_pairing_gives_a_different_answer_on_this_fixture():
    """The fixture is only a test of the rule if the wrong rules fail on it."""
    vset, wells, refs, ids = _plate()
    by_variable = vp.pair_by_variable(
        vset, wells, refs, variable_id=ids["compound"], value_id=ids["dmso"],
        within=[ids["line"]]).mapping
    for size in (2, 3):
        assert auto_pair(list(wells), refs, size=size) != by_variable
    first_vehicle = {k: _SET("U2OS-veh") for k in wells}
    assert first_vehicle != by_variable


def test_the_default_within_is_the_variable_the_vehicles_differ_on():
    """Both vehicles sit at Dose 0, so Dose is not ticked; they differ on Cell line."""
    vset, wells, _refs, ids = _plate()
    assert vp.default_within(vset, wells, ids["compound"], ids["dmso"]) == (ids["line"],)


def test_matching_on_a_variable_the_controls_do_not_vary_pairs_only_the_controls():
    """Ticking Dose demands a vehicle at every dose: the drug wells are named, not paired."""
    vset, wells, refs, ids = _plate()
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id=ids["dmso"],
                                  within=[ids["line"], ids["dose"]])
    assert set(pairing.mapping) == {"HeLa-veh", "U2OS-veh"}
    unpaired = {u.member: u for u in pairing.unpaired}
    assert unpaired["HeLa-A1"].reason == vp.NO_MATCH
    assert unpaired["HeLa-A1"].detail == (
        "no sample with Compound = DMSO and Cell line = HeLa and Dose = 1 µM")


def test_two_matching_controls_are_named_never_picked_between():
    vset, wells, refs, ids = _plate()
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id=ids["dmso"], within=[])
    assert pairing.mapping == {}
    first = pairing.unpaired[0]
    assert first.reason == vp.AMBIGUOUS
    assert first.detail.startswith("U2OS-veh, HeLa-veh all have Compound = DMSO")


def test_a_member_whose_wells_disagree_is_named_with_that_reason():
    vset, wells, refs, ids = _plate()
    wells["HeLa-A1"] = ["A01", "B01"]           # one HeLa well, one U2OS well
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id=ids["dmso"], within=[ids["line"]])
    assert "HeLa-A1" not in pairing.mapping
    (item,) = [u for u in pairing.unpaired if u.member == "HeLa-A1"]
    assert (item.reason, item.detail) == (vp.WELLS_DISAGREE,
                                          "its wells disagree on Cell line")


def test_an_unannotated_member_is_named_with_that_reason():
    vset, wells, refs, ids = _plate()
    vset = sv.assign(vset, ids["line"], ["E01", "E02"], None)
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id=ids["dmso"], within=[ids["line"]])
    (item,) = pairing.unpaired
    assert (item.member, item.reason, item.detail) == (
        "HeLa-A10", vp.NOT_ANNOTATED, "Cell line is not set")
    assert len(pairing.mapping) == 5


def test_a_control_that_cannot_be_addressed_is_named():
    vset, wells, refs, ids = _plate()
    del refs["HeLa-veh"]
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id=ids["dmso"], within=[ids["line"]])
    reasons = {u.member: u.reason for u in pairing.unpaired}
    assert reasons == {"HeLa-A1": vp.UNADDRESSABLE, "HeLa-veh": vp.UNADDRESSABLE,
                       "HeLa-A10": vp.UNADDRESSABLE}
    assert pairing.mapping["U2OS-A1"] == _SET("U2OS-veh")


def test_applying_a_pairing_clears_what_the_rule_could_not_pair():
    vset, wells, refs, ids = _plate()
    vset = sv.assign(vset, ids["line"], ["E01", "E02"], None)
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id=ids["dmso"], within=[ids["line"]])
    before = {"HeLa-A10": _SET("U2OS-veh"), "elsewhere": _SET("X")}
    after = vp.apply_pairing(before, pairing)
    assert "HeLa-A10" not in after
    assert after["elsewhere"] == _SET("X")
    assert after["HeLa-A1"] == _SET("HeLa-veh")


def test_a_reference_value_that_no_longer_exists_pairs_nothing():
    vset, wells, refs, ids = _plate()
    pairing = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                  value_id="gone", within=[ids["line"]])
    assert pairing.mapping == {}
    assert len(pairing.unpaired) == 6


@pytest.mark.parametrize("n,expected_tail", [(4, "."), (6, "; and 2 more.")])
def test_the_summary_names_a_few_and_counts_the_rest(n, expected_tail):
    items = tuple(vp.Unpaired(f"M{i}", vp.NO_MATCH, "x") for i in range(n))
    text = vp.summary_text(vp.VariablePairing(unpaired=items, total=n))
    assert text.startswith(f"Paired 0 of {n}. Not paired: M0 — x; M1 — x")
    assert text.endswith(expected_tail)


def test_matching_names_the_member_not_a_reference():
    """``match_by_variable`` is the same rule keyed member to member — what the
    Bar tab's *Then relative to* needs, where the answer is a bar."""
    vset, wells, refs, ids = _plate()
    matched = vp.match_by_variable(vset, wells, variable_id=ids["compound"],
                                   value_id=ids["dmso"], within=[ids["line"]])
    assert matched.mapping == {
        "HeLa-A1": "HeLa-veh", "U2OS-veh": "U2OS-veh", "HeLa-veh": "HeLa-veh",
        "U2OS-A1": "U2OS-veh", "HeLa-A10": "HeLa-veh", "U2OS-A10": "U2OS-veh",
    }
    del refs["HeLa-veh"]
    paired = vp.pair_by_variable(vset, wells, refs, variable_id=ids["compound"],
                                 value_id=ids["dmso"], within=[ids["line"]])
    # Unaddressable members keep their draw-order place among the unpaired.
    assert [u.member for u in paired.unpaired] == ["HeLa-A1", "HeLa-veh", "HeLa-A10"]
