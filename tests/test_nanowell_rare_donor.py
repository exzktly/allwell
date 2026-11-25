"""Each donor is its own experiment in the Rare responders test: every treatment is tested against the control group
of its own donor, and Benjamini–Hochberg runs within each donor (``rare_donor_compute``, ``RareParams.families``).

The plate is built so a wrong rule gives a different answer than the right one:

* donor 2's wells all grow ~1.5-fold faster than donor 1's, so donor 2's untreated wells sit past donor 1's
  control cutoff: tested against donor 1's control, **D2 A** (no responders) is "detected"; against its own
  control, it is not;
* the groups are listed with donor 2 first, so a rule that takes the first control it meets, or numbers the
  donors in the order the groups arrive, names the wrong one;
* an *Age* variable holds one value per donor, so a rule that matches the control on every variable finds no
  control for donor 2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.domain.sample_variables import Variable, VariableSet, VariableValue
from review.features.nanowell import rare_donor_compute as dn
from review.features.nanowell import rare_export_compute as rx
from review.features.nanowell import rare_report_compute as rr
from review.features.nanowell.rare_data_compute import RareParams
from review.features.nanowell.statistics_compute import bh_adjust

#: group -> (wells, donor value id, molecule value id)
LAYOUT = {
    "D2 ctrl": (["C01", "C02", "C03", "C04"], "d2", "ctrl"),
    "D2 A": (["D01", "D02", "D03", "D04"], "d2", "a"),
    "D1 ctrl": (["A01", "A02", "A03", "A04"], "d1", "ctrl"),
    "D1 A": (["B01", "B02", "B03", "B04"], "d1", "a"),
    "D1 B": (["E01", "E02", "E03", "E04"], "d1", "b"),
}
AGE = {"d1": "y30", "d2": "y50"}


def _variables(layout=LAYOUT, *, age=True, unassigned=()) -> VariableSet:
    donor = Variable("v_donor", "Donor", values=(VariableValue("d1", "1"), VariableValue("d2", "2")))
    molecule = Variable("v_mol", "Molecule", values=(VariableValue("ctrl", "control"), VariableValue("a", "A"),
                                                       VariableValue("b", "B")))
    variables = [donor, molecule]
    assignments = {"v_donor": {}, "v_mol": {}}
    if age:
        variables.append(Variable("v_age", "Age", values=(VariableValue("y30", "30"), VariableValue("y50", "50"))))
        assignments["v_age"] = {}
    for wells, d, m in layout.values():
        for w in wells:
            if w in unassigned:
                continue
            assignments["v_donor"][w] = d
            assignments["v_mol"][w] = m
            if age:
                assignments["v_age"][w] = AGE[d]
    return VariableSet(tuple(variables), assignments)


def _groups(layout=LAYOUT) -> dict:
    return {name: wells for name, (wells, _d, _m) in layout.items()}


# ── the plan: which donor, which control, which groups refused ────────────────────────────────────────────────
def test_each_donor_s_groups_are_tested_against_the_control_of_that_donor():
    plan = dn.plan(_variables(), _groups(), "D1 ctrl", "v_donor")
    assert plan.variable == "Donor" and not plan.problem and plan.refused == ()
    # In the donor variable's order, not the order the groups arrived in.
    assert plan.families == (dn.Family("Donor 1", "D1 ctrl", ("D1 A", "D1 B")),
                             dn.Family("Donor 2", "D2 ctrl", ("D2 A",)))
    # Choosing donor 2's control finds donor 1's the same way.
    assert dn.plan(_variables(), _groups(), "D2 ctrl", "v_donor").families == plan.families


def test_a_variable_every_group_of_a_donor_shares_does_not_decide_its_control():
    # Age is 30 for donor 1 and 50 for donor 2: matching the control on Age as well would find none for donor 2.
    assert dn.setting_apart(_variables(), "v_donor", _groups(), {g: d for g, (_w, d, _m) in LAYOUT.items()},
                            "D1 ctrl") == ["v_mol"]


def test_a_group_whose_wells_span_donors_is_refused_by_name_and_the_rest_are_tested():
    layout = {**LAYOUT, "mixed": (["F01", "F02"], "d1", "a")}
    vset = _variables(layout)
    vset.assignments["v_donor"]["F02"] = "d2"
    plan = dn.plan(vset, _groups(layout), "D1 ctrl", "v_donor")
    assert plan.refused == ("mixed: not tested — its wells come from more than one Donor (1, 2): define a group per "
                            "Donor.",)
    assert "mixed" not in plan.tested() and len(plan.families) == 2


def test_a_group_with_a_well_that_has_no_donor_value_is_refused_naming_the_well():
    plan = dn.plan(_variables(unassigned=("B03",)), _groups(), "D1 ctrl", "v_donor")
    assert plan.refused == (f"D1 A: not tested — B03 has no Donor value ({dn.WHERE}).",)
    assert plan.families[0] == dn.Family("Donor 1", "D1 ctrl", ("D1 B",))


def test_a_donor_with_no_group_matching_the_control_is_refused_and_not_tested_against_another_donor_s():
    layout = {k: v for k, v in LAYOUT.items() if k != "D2 ctrl"}
    plan = dn.plan(_variables(layout), _groups(layout), "D1 ctrl", "v_donor")
    assert plan.refused == ("Donor 2 (D2 A): not tested — no group has Molecule = control, so it has no control.",)
    assert [f.label for f in plan.families] == ["Donor 1"]


def test_two_groups_matching_the_control_in_one_donor_are_named_rather_than_one_picked():
    layout = {**LAYOUT, "D2 ctrl b": (["G01", "G02"], "d2", "ctrl")}
    plan = dn.plan(_variables(layout), _groups(layout), "D1 ctrl", "v_donor")
    assert len(plan.refused) == 1 and plan.refused[0].startswith(
        "Donor 2 (D2 ctrl, D2 A, D2 ctrl b): not tested — D2 ctrl, D2 ctrl b all have Molecule = control")
    assert [f.label for f in plan.families] == ["Donor 1"]


def test_a_control_from_more_than_one_donor_stops_the_test_and_says_how_to_go_on():
    vset = _variables()
    vset.assignments["v_donor"]["A04"] = "d2"
    plan = dn.plan(vset, _groups(), "D1 ctrl", "v_donor")
    assert plan.families == () and plan.problem == (
        "The control D1 ctrl cannot be placed with one Donor: its wells come from more than one Donor (1, 2): define "
        "a group per Donor. Choose a control from one Donor, or set Donor to none on the band.")


def test_with_nothing_but_the_donor_to_tell_the_control_apart_only_its_own_donor_is_tested():
    vset = _variables(age=False)
    vset = VariableSet(vset.variables[:1], {"v_donor": vset.assignments["v_donor"]})
    plan = dn.plan(vset, _groups(), "D1 ctrl", "v_donor")
    assert plan.families == (dn.Family("Donor 1", "D1 ctrl", ("D1 A", "D1 B")),)
    assert len(plan.refused) == 1 and plan.refused[0].startswith(
        "Donor 2 (D2 ctrl, D2 A): not tested — no variable but Donor sets D1 ctrl apart")


def test_the_donor_variable_is_the_one_named_donor_unless_the_band_chose_another_or_none():
    vset = _variables()
    assert dn.default_donor(vset) == "v_donor"
    assert dn.resolve(vset, dn.AUTO) == "v_donor"
    assert dn.resolve(vset, "v_mol") == "v_mol"
    assert dn.resolve(vset, dn.OFF) == ""
    assert dn.resolve(vset, "v_gone") == "v_donor"                   # a stale id falls back to the default
    renamed = VariableSet((Variable("v_x", "Donor ID"), Variable("v_y", "Dose")), {})
    assert dn.default_donor(renamed) == "v_x"
    assert dn.default_donor(VariableSet((Variable("v_x", "Dose"),), {})) == ""
    assert dn.plan(vset, _groups(), "D1 ctrl", "") == dn.DonorPlan()  # not split: every group against one control


# ── the analysis: units per donor, BH per donor ───────────────────────────────────────────────────────────────
def _table() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for name, (wells, donor, molecule) in LAYOUT.items():
        for well in wells:
            mu = rng.normal(0.6 if donor == "d2" else 0.0, 0.08)     # donor 2 grows faster, every well of it
            n0 = rng.choice([1, 2, 3], size=300)
            nt = rng.poisson(n0 * 2 ** (2 + mu + rng.normal(0, 0.5, 300)))
            if name == "D1 A":
                idx = rng.choice(300, 10, replace=False)
                nt[idx] = (n0[idx] * 2 ** 6.5).astype(int)
            rows += [{"plate_id": "P1", "well_id": well, "condition": name, "nanowell_id": str(i), "n0": n0[i],
                      "nt": nt[i], "t0_h": 0.0, "t_h": 72.0, "crop_path": ""} for i in range(300)]
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def results():
    table = _table()
    plan = dn.plan(_variables(), _groups(), "D1 ctrl", "v_donor")
    families = tuple((f.label, f.control, f.treatments) for f in plan.families)
    split = rr.analyse(table, RareParams(control="D1 ctrl", families=families, max_permutations=200))
    shared = rr.analyse(table, RareParams(control="D1 ctrl", max_permutations=200))
    return split, shared


def test_each_unit_is_tested_against_its_own_donor_s_control_wells(results):
    split, _shared = results
    by = {u.treatment: u for u in split.units}
    assert sorted(by) == ["D1 A", "D1 B", "D2 A"]
    assert {w.key.split(":")[-1] for w in by["D2 A"].controls} == {"C01", "C02", "C03", "C04"}
    assert {w.key.split(":")[-1] for w in by["D1 A"].controls} == {"A01", "A02", "A03", "A04"}
    assert (by["D2 A"].family, by["D2 A"].control_name) == ("Donor 2", "D2 ctrl")


def test_against_its_own_control_donor_2_s_untreated_growth_is_not_a_finding(results):
    split, shared = results
    own = {u.treatment: u.interpretation for u in split.units}
    one = {u.treatment: u.interpretation for u in shared.units}
    assert own["D1 A"] == rr.DETECTED and one["D1 A"] == rr.DETECTED
    # The donor's faster growth, read against donor 1's control, is called a finding; against its own, it is not.
    assert one["D2 A"] == rr.DETECTED and own["D2 A"] == rr.NOT_DETECTED


def test_q_is_each_p_corrected_within_its_donor_only(results):
    split, _shared = results
    for family in ("Donor 1", "Donor 2"):
        mine = [u for u in split.units if u.family == family]
        for key, ps in (("exceedance", [u.primary.p for u in mine]),
                        ("hc", [u.hc_perm.p for u in mine]), ("ad", [u.a2_perm.p for u in mine])):
            want, _corrected = bh_adjust(ps)
            assert [u.q[key] for u in mine] == pytest.approx(want), (family, key)
        assert {u.family_n for u in mine} == {len(mine)}
    # Donor 2 tests one group: its q is its p, not that p corrected against donor 1's groups.
    d2 = next(u for u in split.units if u.family == "Donor 2")
    assert d2.q["exceedance"] == d2.primary.p


def test_the_report_says_each_donor_is_corrected_on_its_own(results):
    split, _shared = results
    d1a = next(u for u in split.units if u.treatment == "D1 A")
    assert rx.deciding_line(d1a, len(split.units), split.prepared.params).endswith(
        f"(q = {rx._num(d1a.q['exceedance'])} across the 2 groups tested for Donor 1).")
    assert rx.test_p(d1a, "exceedance", split.prepared.params, len(split.units)) == (
        f"p = {rx._num(d1a.primary.p)}, q = {rx._num(d1a.q['exceedance'])}")
    assert rx.correction_line(split).startswith(
        "Each donor is its own experiment: its groups are tested against its own control, and q is each p corrected "
        "across the groups tested for the same donor (Benjamini–Hochberg, 2 families: Donor 1, Donor 2)")
    assert rx.correction_line(split) in rx.report_lines(split)
    last = len(split.units) - 1
    assert rx.comparison_title(split, last) == f"Comparison {last + 1} of 3: D2 A vs D2 ctrl (Donor 2)"
    assert rx.groups_line(split).startswith("5 groups tested from 2 donors, each donor against its own control: "
                                            "Donor 1: control D1 ctrl (A01, A02, A03, A04)")
    assert split.table[["treatment", "donor", "control"]].values.tolist() == [
        ["D1 A", "Donor 1", "D1 ctrl"], ["D1 B", "Donor 1", "D1 ctrl"], ["D2 A", "Donor 2", "D2 ctrl"]]


def test_a_donor_whose_control_lost_every_nanowell_is_said_and_the_other_donor_is_still_tested():
    table = _table()
    table = table[table["condition"] != "D2 ctrl"]
    families = (("Donor 1", "D1 ctrl", ("D1 A", "D1 B")), ("Donor 2", "D2 ctrl", ("D2 A",)))
    result = rr.analyse(table, RareParams(control="D1 ctrl", families=families, max_permutations=50))
    assert [u.treatment for u in result.units] == ["D1 A", "D1 B"]
    assert any(w.startswith("Donor 2: not tested — no well is labelled with the control condition 'D2 ctrl'")
               for w in result.warnings)


def test_the_shared_panels_draw_one_donor_each():
    from review.features.nanowell._rare_render import well_dot_chunks

    split = rr.analyse(_table(), RareParams(control="D1 ctrl", max_permutations=20, families=(
        ("Donor 1", "D1 ctrl", ("D1 A", "D1 B")), ("Donor 2", "D2 ctrl", ("D2 A",)))))
    chunks = well_dot_chunks(split.units, 40.0)        # wide enough for every well on one panel
    assert [[u.treatment for u in run] for run in chunks] == [["D1 A", "D1 B"], ["D2 A"]]
