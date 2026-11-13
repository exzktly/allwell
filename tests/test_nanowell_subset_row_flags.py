"""The Nanowells page's *Subset* tests each per-timepoint property of a nanowell on its own: found by the run, its
timepoint not addressed, occluded or clipped, identity contradicted, rim out of focus, in a field left out, measured
without a count.

Asked for: "Some nanowells lose their tracking over time and reappear later … I'd like to be able to make a subset
filter to wells that retain their id consistently through the entire dataset", then "Subset should allow
inclusion/exclusion based on all of these independent properties".

The fixture is a real dataset read through ``load_nanowell_data`` and ``site_metrics`` — with a Cell Gating field
and the rim-focus rule both active, because those two overwrite a row's ``state`` and are what would make one
property stand in for another. A01 has twelve nanowells over t_c < t_a < t_d (twelve, so the rim-focus rule has
enough rims to judge at each timepoint), each touched by at most one property, at t_a:

=====  =========================================================
q0     nothing — a control
q1     no row at t_a: lost by the run, found again at t_d
q2     occluded at t_a
q3     measured at t_a with no count
q4     in field F1, which the gate leaves out at t_a
q5     its rim far softer than the well's at t_a
q6     left out by a partial identity check at t_a
q7     its own rim not fitted at t_a (no rim focus measured): its crop there says "rim not fitted"
q8–11  nothing
=====  =========================================================

B01 has two nanowells and its t_a was never located (a failed grid), so it answers *timepoint not addressed*.

The grid records and the identity verdicts are set so that each reading of them that could be confused picks a
different set. A circular rim was fitted at every timepoint but A01's t_a, whose record says none was
(``array_boundary: null``) — and B01's failed t_a, whose record never placed a lattice and so says nothing (no
``array_boundary`` key): *no* there would be a guess, so it is no value. The verdicts are A01: reference, partial
(leaving q6 out), confirmed; B01: reference, not checked (its t_a has none), inconclusive.

Each property's expected set is a single nanowell, so a reading that took one property from another's column picks
a different one (CLAUDE.md §7.8 rule 2): reading *found* from ``state`` instead of the run's own state would drop q4
(field left out) and q5 (rim out of focus) as well as q1.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.domain.nanowell_rim_focus import RimFocusRule  # noqa: E402
from review.features.nanowell import subset_compute as ss  # noqa: E402
from review.features.nanowell.cell_gate_compute import GateTally, _empty_sites  # noqa: E402
from review.features.nanowell.collect import load_nanowell_data  # noqa: E402
from review.features.nanowell.series_compute import site_metrics  # noqa: E402
from review.features.nanowell.state import NanowellState  # noqa: E402
from tests.test_nanowell_review_data import _dataset, _row  # noqa: E402

C = ss.Condition
TPS = ("t_c", "t_a", "t_d")
STARTS = {"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00", "t_d": "2026-03-01T19:00:00"}


def _a01_rows() -> list:
    rows = []
    for q in range(12):
        for tp in TPS:
            if q == 1 and tp == "t_a":
                continue                                            # lost by the run at t_a
            if q == 2 and tp == "t_a":
                row = _row(tp, q, 0, state="occluded")
            elif q == 3 and tp == "t_a":
                row = _row(tp, q, 0, n=None, c=0.1, gfp=100.0)      # measured, no count
            else:
                row = _row(tp, q, 0, n=2, c=0.2, gfp=200.0)
            if q == 4:
                row["owner"] = "F1"
            if q == 5 and tp == "t_a":
                row["focus"] = 0.05
            if q == 7 and tp == "t_a":
                row["focus"] = None                                 # measured, its own rim not fitted
            rows.append(row)
    return rows


@pytest.fixture(scope="module")
def metrics(tmp_path_factory):
    b01 = [_row(tp, q, 0, n=1, c=0.1, gfp=100.0) for q in range(2) for tp in ("t_c", "t_d")]
    circle = {"centre_um": [0.0, 0.0], "radius_um": 500.0}
    grids = {"A01": {"t_c": {"status": "ok", "array_boundary": circle}, "t_a": {"status": "ok", "array_boundary": None},
                     "t_d": {"status": "ok", "array_boundary": circle}},
             "B01": {"t_c": {"status": "ok", "array_boundary": circle}, "t_a": {"status": "failed"},
                     "t_d": {"status": "ok", "array_boundary": circle}}}
    out = _dataset(tmp_path_factory.mktemp("flags"), {"A01": _a01_rows(), "B01": b01}, starts=STARTS, grids=grids)
    data = load_nanowell_data(out)
    data.identity = {"A01": {"t_c": ("reference", ""), "t_a": ("partial", ""), "t_d": ("confirmed", "")},
                     "B01": {"t_c": ("reference", ""), "t_d": ("inconclusive", "")}}
    data.identity_excluded = {"A01": {"t_a": {(6, 0)}}}
    gate = GateTally(sites=_empty_sites(), fields=frozenset({("A01", "F1", "t_a")}))
    return site_metrics(data, gate=gate, focus=RimFocusRule())


def _kept(metrics, *conditions, match="all"):
    kept, done = ss.apply(metrics, ss.Subset(tuple(conditions), match).validated(), "earliest")
    return sorted(set(zip(kept["well"], kept["nw_q"].astype(int)))), done


def _in_a01(*qs):
    return [("A01", q) for q in qs]


def test_the_fixture_reaches_each_property_through_the_real_pipeline(metrics):
    """Each property's own cause is in the table: without this the assertions below could pass on a fixture that
    never gated a field or judged a rim."""
    at = metrics[(metrics["well"] == "A01") & (metrics["timepoint"] == "t_a")].set_index("nw_q")
    assert at.loc[1, "state"] == "absent"
    assert at.loc[4, "state"] == "field_excluded" and at.loc[4, "run_state"] == "measured"
    assert at.loc[5, "state"] == "out_of_focus" and at.loc[5, "run_state"] == "measured"
    assert not at.loc[6, "identity_ok"]
    b01 = metrics[(metrics["well"] == "B01") & (metrics["timepoint"] == "t_a")]
    assert set(b01["state"]) == {"not_located"}


def test_found_at_every_timepoint_keeps_only_the_nanowells_tracked_throughout(metrics):
    kept, done = _kept(metrics, C("found", "yes", at="every"))
    assert kept == _in_a01(0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)          # q1 lost at t_a; B01 lost its whole t_a
    assert (done.kept, done.outside, done.unknown) == (11, 3, 0)
    assert _kept(metrics, C("found", "no", at="any"))[0] == _in_a01(1) + [("B01", 0), ("B01", 1)]


def test_a_nanowell_lost_and_found_again_is_found_at_both_ends(metrics):
    ends = (C("found", "yes", at="earliest"), C("found", "yes", at="latest"))
    lost_inside = C("found", "no", at="any")
    # B01's two were lost in the middle too, with their whole timepoint: the other property tells the two apart.
    assert _kept(metrics, *ends, lost_inside)[0] == _in_a01(1) + [("B01", 0), ("B01", 1)]
    assert _kept(metrics, *ends, lost_inside, C("tp_unaddressed", "no", at="every"))[0] == _in_a01(1)


@pytest.mark.parametrize("name, touched", [
    ("tp_unaddressed", [("B01", 0), ("B01", 1)]),
    ("occluded", _in_a01(2)),
    ("no_count", _in_a01(3)),
    ("field_left_out", _in_a01(4)),
    ("rim_out_of_focus", _in_a01(5)),
    ("identity_out", _in_a01(6)),
])
def test_each_property_touches_only_its_own_nanowell_and_can_be_kept_or_left_out(metrics, name, touched):
    everyone = sorted(set(zip(metrics["well"], metrics["nw_q"].astype(int))))
    assert _kept(metrics, C(name, "yes", at="any"))[0] == touched
    assert _kept(metrics, C(name, "no", at="every"))[0] == [s for s in everyone if s not in touched]
    assert _kept(metrics, C(name, "yes", at="tp:t_c"))[0] == []      # each was touched at t_a only


def test_the_properties_combine_into_one_clean_tracked_set(metrics):
    clean = (C("found", "yes", at="every"), C("identity_out", "no", at="every"),
             C("rim_out_of_focus", "no", at="every"), C("field_left_out", "no", at="every"))
    assert _kept(metrics, *clean)[0] == _in_a01(0, 2, 3, 7, 8, 9, 10, 11)


def test_a_table_without_the_column_a_property_reads_decides_nothing(metrics):
    kept, done = _kept(metrics.drop(columns=["run_state"]), C("found", "yes", at="every"))
    assert kept == [] and (done.outside, done.unknown) == (0, done.total)


def test_the_properties_are_offered_written_and_persisted():
    offered = {name: group for name, _label, group in ss.field_choices(("GFP",))}
    assert {n: offered[n] for n in ss.ROW_FLAGS} == {
        "found": "Tracking", "tp_unaddressed": "Tracking", "identity_out": "Tracking", "rim_fitted": "Tracking",
        "nw_rim_fitted": "Quality", "occluded": "Quality", "rim_out_of_focus": "Quality", "field_left_out": "Quality",
        "no_count": "Quality"}
    assert offered["identity"] == "Tracking"
    assert C("found", "yes", at="every").validated().text() == "Found by the run at every timepoint: yes"
    for bad in (C("found", "ge", 1), C("occluded", "in", values=("x",))):
        with pytest.raises(ValueError):
            bad.validated()
    st = NanowellState()
    st.subset = ss.Subset((C("found", "yes", at="every"), C("identity_out", "no", at="every"))).validated()
    assert ss.Subset.from_config(st.subset.to_config()) == st.subset


def test_a_nanowell_outside_says_which_property_it_failed(metrics):
    rows = metrics[(metrics["well"] == "A01") & (metrics["nw_q"] == 1)]
    assert ss.site_badge(rows, ss.Subset((C("found", "yes", at="every"),))) == (
        "outside the subset (fails Found by the run at every timepoint: yes)")


# ── the array rim fit ────────────────────────────────────────────────────────
# Asked for: a subset on whether the run fitted the array's rim (``pipeline.nanowell_lattice.fit_array_boundary``).
def test_rim_fitted_reads_the_grid_record_of_each_timepoint(metrics):
    at = metrics.set_index(["well", "timepoint"]).sort_index()["rim_fitted"]
    assert at.loc[("A01", "t_c")].tolist() == [True] * 12 and at.loc[("A01", "t_a")].tolist() == [False] * 12
    assert at.loc[("B01", "t_a")].isna().all()                         # refused before a lattice: never asked


def test_a_timepoint_whose_rim_was_not_fitted_leaves_its_well_out(metrics):
    kept, done = _kept(metrics, C("rim_fitted", "yes", at="every"))
    # A01 was not fitted at t_a; B01's t_a says nothing, so it is undecided, not outside.
    assert kept == [] and (done.outside, done.unknown) == (12, 2)
    assert _kept(metrics, C("rim_fitted", "no", at="any"))[0] == _in_a01(*range(12))
    kept, done = _kept(metrics, C("rim_fitted", "yes", at="tp:t_a"))
    assert kept == [] and (done.outside, done.unknown) == (12, 2)
    assert len(_kept(metrics, C("rim_fitted", "yes", at="tp:t_c"))[0]) == 14


def test_a_table_without_the_rim_column_decides_nothing(metrics):
    kept, done = _kept(metrics.drop(columns=["rim_fitted"]), C("rim_fitted", "no", at="any"))
    assert kept == [] and (done.outside, done.unknown) == (0, done.total)


# ── the identity verdict ─────────────────────────────────────────────────────
# Asked for: a subset on the identity check's verdicts, where *Identity contradicted* only says contradicted or not.
def test_the_identity_verdict_keeps_the_nanowells_of_the_timepoints_named(metrics):
    everyone = sorted(set(zip(metrics["well"], metrics["nw_q"].astype(int))))
    b01 = [("B01", 0), ("B01", 1)]
    assert _kept(metrics, C("identity", "in", values=("inconclusive",), at="any"))[0] == b01
    assert _kept(metrics, C("identity", "in", values=("not_checked",), at="any"))[0] == b01
    assert _kept(metrics, C("identity", "in", values=("partial",), at="tp:t_a"))[0] == _in_a01(*range(12))
    # A partial verdict is the timepoint's: q6, which it left out, is still at a partial timepoint …
    agreed = C("identity", "in", values=("confirmed", "reference", "partial"), at="every")
    assert _kept(metrics, agreed)[0] == _in_a01(*range(12))
    # … and the per-nanowell property is what leaves it out.
    assert _kept(metrics, agreed, C("identity_out", "no", at="every"))[0] == _in_a01(0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11)
    assert _kept(metrics, C("identity", "not_in", values=("reference",), at="earliest"))[0] == []
    assert _kept(metrics, C("identity", "not_in", values=("confirmed",), at="latest"))[0] == b01
    assert len(everyone) == 14


def test_an_identity_condition_is_written_in_words_validated_and_persisted():
    c = C("identity", "in", values=("not_checked", "inconclusive", "reference"), at="every").validated()
    assert c.values == ("reference", "inconclusive", "not_checked")    # the verdicts' order, not the spelling's
    assert c.text() == "Identity verdict at every timepoint is one of reference, inconclusive, not checked"
    assert ss.Subset.from_config(ss.Subset((c,)).to_config()) == ss.Subset((c,))
    for bad in (C("identity", "in", values=("maybe",)), C("identity", "in"), C("identity", "ge", 1)):
        with pytest.raises(ValueError):
            bad.validated()


def test_every_verdict_the_identity_check_gives_can_be_named():
    from pipeline.nanowell_identity import VERDICTS

    assert set(ss.IDENTITY_VERDICTS) == set(VERDICTS)


# ── the nanowell's own rim ───────────────────────────────────────────────────
# Reported: "requiring fitted rim for all timepoints of a given nanowell appears to be dropped" — a nanowell kept by
# *Array rim fitted at every timepoint: yes* showed crops captioned "rim not fitted". The condition was applied; it
# asked about the circle around the well's whole array, which A01 has at every timepoint but t_a and which says
# nothing of one nanowell's rim. *Nanowell rim fitted* asks the question the crop's caption answers.
def test_nanowell_rim_fitted_at_every_timepoint_leaves_out_a_nanowell_whose_own_rim_was_not_fitted(metrics):
    kept, done = _kept(metrics, C("nw_rim_fitted", "yes", at="every"))
    # q7 is outside. q1 (not found at t_a) and B01 (t_a never located) were not measured there, so whether their
    # rim was fitted is unknown — undecided, never kept and never "no". q2, occluded at t_a, had its rim's focus
    # measured there: its rim was fitted, whatever hid its contents.
    assert kept == _in_a01(0, 2, 3, 4, 5, 6, 8, 9, 10, 11)
    assert (done.outside, done.unknown) == (1, 3)
    assert _kept(metrics, C("nw_rim_fitted", "no", at="any"))[0] == _in_a01(7)
    # The array's outline is a different property: A01's was not fitted at t_a, for every nanowell alike.
    assert _kept(metrics, C("rim_fitted", "no", at="any"))[0] == _in_a01(*range(12))


def test_nanowell_rim_fitted_agrees_with_every_crop_caption(metrics):
    """The flag is *no* exactly where a crop is captioned "rim not fitted" (``series_compute.rim_focus_text``)."""
    from review.features.nanowell.series_compute import rim_focus_text

    flags = ss._row_flag(metrics, "nw_rim_fitted")
    captions = [rim_focus_text(row) for row in metrics.to_dict("records")]
    said_not = [c == "rim not fitted" for c in captions]
    assert said_not == [f == 0 for f in flags]
    assert sum(said_not) == 1                                        # q7 at t_a, and nothing else


def test_the_two_rim_properties_are_named_apart():
    assert C("rim_fitted", "yes", at="every").validated().text() == "Array outline fitted at every timepoint: yes"
    assert C("nw_rim_fitted", "yes", at="every").validated().text() == "Nanowell rim fitted at every timepoint: yes"
