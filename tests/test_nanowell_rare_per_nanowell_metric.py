"""The Rare responders test keeps, for a metric with one value per nanowell, the nanowells the sample tests keep.

A growth rate (and the occupancy class) is one value per nanowell and none per timepoint. The Statistics view's sample
tests read it from each well's first-timepoint rows and leave a nanowell out only where the metric is undefined
(``distribution_compute``, ``series_compute.exclusion_reason``). The Rare responders test used to read the rows at the
page's Timepoint and exclude a nanowell by its *state there* — a timepoint the metric does not have — so a nanowell
occluded at the last timepoint, whose growth rate is fitted from the four before it, was in Welch's test and out of
this one.

The plate (t0..t4, twelve nanowells a well) holds one nanowell of each kind the two rules disagree on:

* B02's nanowells 0..3 are occluded at t4 only: a growth rate, so kept (the old rule dropped all four);
* B01's nanowell 6 is occluded at t0 only: a growth rate, so kept (a rule reading the first row's state drops it);
* B01's nanowell 5 has cells at t0 and t4 only: no growth rate, so excluded with the sample tests' reason;
* B03 was imaged at t0..t2 only: it has no row at t4, and all twelve of its nanowells have a growth rate.
"""

from __future__ import annotations

import pytest
from matplotlib.figure import Figure

from review.features.nanowell import _rare_controller as rc
from review.features.nanowell import controller as nwc
from review.features.nanowell import rare_data_compute as rdc

TPS = [f"t{i}" for i in range(5)]
SITES = 12
GROUPS = {"ctrl": ["A01", "A02", "A03"], "drug": ["B01", "B02", "B03"]}
NO_RATE = "fewer than three timepoints with cells and a time"


@pytest.fixture
def plate(tmp_path):
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics
    from tests.test_nanowell_review_data import _dataset, _row

    def site(q, counts, tps=TPS):
        return [_row(tp, q, 0, state="occluded") if n is None else _row(tp, q, 0, n=n, c=0.1 * n)
                for tp, n in zip(tps, counts, strict=True)]

    def grown(q):
        return [1, 1, 2, 3, 4 + q % 3]

    wells = {well: [r for q in range(SITES) for r in site(q, grown(q))] for well in ("A01", "A02", "A03")}
    wells["B01"] = [r for q in range(SITES) for r in site(
        q, [1, None, None, None, 4] if q == 5 else [None, 1, 2, 3, 5] if q == 6 else grown(q))]
    wells["B02"] = [r for q in range(SITES) for r in site(q, [*grown(q)[:4], None] if q < 4 else grown(q))]
    wells["B03"] = [r for q in range(SITES) for r in site(q, [1, 2, 3 + q % 2], TPS[:3])]
    starts = {tp: f"2026-03-01T{9 + i:02d}:00:00" for i, tp in enumerate(TPS)}
    data = load_nanowell_data(_dataset(tmp_path, wells, starts=starts))
    return site_metrics(data), data


def _draw(plate, store, metric, timepoint="t4", test="rare", unit="wells"):
    metrics, data = plate
    asked = []
    _hit, model = nwc.draw_view(
        Figure(), "statistics", metrics, data, groups=GROUPS, metric=metric, channel=None, axis="wells",
        timepoint=timepoint, control="ctrl", baseline="earliest", cohort=False, well="A01", well_timepoint="t4",
        stats_test=test, stats_unit=unit, rare={"store": store, "request": lambda key, job: asked.append((key, job))})
    return asked, model


def _rare(plate, metric, timepoint="t4"):
    store: dict = {}
    asked, _model = _draw(plate, store, metric, timepoint)
    assert len(asked) == 1
    key, job = asked[0]
    store[key] = job()
    _asked, model = _draw(plate, store, metric, timepoint)
    return key, model


def _kept(result) -> dict:
    wells = result.prepared.wells
    return dict(zip(wells["well_id"], (int(n) for n in wells["n_kept"])))


WANT = {"A01": 12, "A02": 12, "A03": 12, "B01": 11, "B02": 12, "B03": 12}


def test_a_growth_rate_keeps_every_nanowell_that_has_one(plate):
    _key, result = _rare(plate, "growth_rate")
    assert _kept(result) == WANT
    assert result.log["exclusions_by_reason"] == {NO_RATE: 1}


def test_they_are_the_nanowells_the_sample_tests_compare(plate):
    _asked, welch = _draw(plate, {}, "growth_rate", test="welch", unit="nanowells")
    assert {s.name: s.n for s in welch.samples} == WANT
    _key, result = _rare(plate, "growth_rate")
    assert _kept(result) == {s.name: s.n for s in welch.samples}


def test_the_values_tested_are_the_growth_rates(plate):
    metrics, _data = plate
    _key, result = _rare(plate, "growth_rate")
    kept = result.prepared.kept
    got = {(w, int(n.split(",")[0])): v for w, n, v in zip(kept["well_id"], kept["nanowell_id"], kept["fc"])}
    first = metrics[metrics["tp_order"].eq(0) & metrics["growth_rate"].notna()]
    want = {(w, int(q)): float(v) for w, q, v in zip(first["well"], first["nw_q"], first["growth_rate"])}
    assert len(want) == sum(WANT.values())
    assert got == pytest.approx(want)


def test_the_timepoint_does_not_apply_to_it_and_details_says_so(plate):
    at_t4, result = _rare(plate, "growth_rate", "t4")
    at_t2, _result = _rare(plate, "growth_rate", "t2")
    assert at_t4 == at_t2                                   # one result whatever the Timepoint names
    assert result.page_notes[0] == rc.VALUE_NOTE_PER_NANOWELL
    assert not any("Timepoint: t" in note for note in result.page_notes)


def test_a_metric_with_a_value_per_timepoint_is_still_excluded_by_its_state_there(plate):
    _key, result = _rare(plate, "n")
    assert _kept(result) == {"A01": 12, "A02": 12, "A03": 12, "B01": 12, "B02": 8}      # B03 has no row at t4
    # Each of the four is counted under both of its reasons: its state, and the count it therefore lacks.
    assert result.log["exclusions_by_reason"] == {"QC: state (occluded)": 4, rdc.VALUE_MISSING: 4}
