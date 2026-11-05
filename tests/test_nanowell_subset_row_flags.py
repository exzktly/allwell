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
q7–11  nothing
=====  =========================================================

B01 has two nanowells and its t_a was never located (a failed grid), so it answers *timepoint not addressed*.

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
            rows.append(row)
    return rows


@pytest.fixture(scope="module")
def metrics(tmp_path_factory):
    b01 = [_row(tp, q, 0, n=1, c=0.1, gfp=100.0) for q in range(2) for tp in ("t_c", "t_d")]
    grids = {"A01": {tp: {"status": "ok"} for tp in TPS},
             "B01": {"t_c": {"status": "ok"}, "t_a": {"status": "failed"}, "t_d": {"status": "ok"}}}
    out = _dataset(tmp_path_factory.mktemp("flags"), {"A01": _a01_rows(), "B01": b01}, starts=STARTS, grids=grids)
    data = load_nanowell_data(out)
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
        "found": "Tracking", "tp_unaddressed": "Tracking", "identity_out": "Tracking", "occluded": "Quality",
        "rim_out_of_focus": "Quality", "field_left_out": "Quality", "no_count": "Quality"}
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
