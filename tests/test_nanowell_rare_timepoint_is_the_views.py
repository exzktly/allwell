"""The Rare responders test reads the page's Timepoint the way the Statistics view's other tests do.

The page has one Timepoint for every view, so it can name a timepoint chosen on a well that is not drawn here (the
Array view shows one well and offers that well's timepoints). The sample tests then compare the drawn wells at the
latest timepoint they have (``distribution_compute.drawn_timepoint``); the Rare responders test used to keep the
name and report that no nanowell had a row there.

The plate: control wells A01..A03 and treatment wells B01, B02 imaged at t0..t4, and a well C01 — not drawn — imaged
at t4 and t5. Each nanowell's count differs at every timepoint (q, q + 1, … at t0, t1, …), so a test that read any
other timepoint than the one asserted reads other numbers. Driven through ``controller.draw_view``, the composition
the page and Batch Export call.
"""

from __future__ import annotations

import pytest
from matplotlib.figure import Figure

from review.features.nanowell import _rare_controller as rc
from review.features.nanowell import controller as nwc

TPS = [f"t{i}" for i in range(5)]
SITES = 12
GROUPS = {"ctrl": ["A01", "A02", "A03"], "drug": ["B01", "B02"]}


@pytest.fixture
def plate(tmp_path):
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics
    from tests.test_nanowell_review_data import _dataset, _row

    def site(q, tps, first):
        return [_row(tp, q, 0, n=first + i, c=0.1 * (first + i)) for i, tp in enumerate(tps)]

    wells = {well: [r for q in range(SITES) for r in site(q, TPS, 1 + q)] for ws in GROUPS.values() for well in ws}
    wells["C01"] = site(0, ["t4", "t5"], 1)
    starts = {tp: f"2026-03-01T{9 + i:02d}:00:00" for i, tp in enumerate([*TPS, "t5"])}
    data = load_nanowell_data(_dataset(tmp_path, wells, starts=starts))
    return site_metrics(data), data


def _draw(plate, store, timepoint, test="rare"):
    metrics, data = plate
    asked = []
    fig = Figure()
    _hit, model = nwc.draw_view(
        fig, "statistics", metrics, data, groups=GROUPS, metric="n", channel=None, axis="wells", timepoint=timepoint,
        control="ctrl", baseline="earliest", cohort=False, well="A01", well_timepoint="t4", stats_test=test,
        stats_unit="wells", rare={"store": store, "request": lambda key, job: asked.append((key, job))})
    return asked, model, fig


def _rare(plate, timepoint):
    store: dict = {}
    asked, model, fig = _draw(plate, store, timepoint)
    said = [t.get_text() for t in fig.texts] + [t.get_text() for ax in fig.axes for t in ax.texts]
    assert len(asked) == 1, said                 # the test ran, rather than saying why it could not
    key, job = asked[0]
    store[key] = job()
    _asked, model, _fig = _draw(plate, store, timepoint)
    return model


def _tested(result) -> dict:
    kept = result.prepared.kept
    return {(w, int(n.split(",")[0])): v for w, n, v in zip(kept["well_id"], kept["nanowell_id"], kept["fc"])}


def _counts_at(index: int) -> dict:
    """Each drawn nanowell's count at ``TPS[index]``: 1 + q at t0, one more at each timepoint after."""
    return {(w, q): float(1 + q + index) for ws in GROUPS.values() for w in ws for q in range(SITES)}


def test_a_timepoint_the_drawn_wells_do_not_have_falls_back_to_their_latest(plate):
    metrics, _data = plate
    assert rc.drawn_timepoint(metrics, GROUPS, "t5") == "t4"
    result = _rare(plate, "t5")
    assert _tested(result) == _counts_at(4)
    assert any("Timepoint: t4" in note for note in result.page_notes), result.page_notes


def test_it_is_the_timepoint_the_sample_tests_compare_at(plate):
    _asked, welch, _fig = _draw(plate, {}, "t5", test="welch")
    assert welch.timepoint == "t4"
    assert rc.drawn_timepoint(plate[0], GROUPS, "t5") == welch.timepoint


def test_a_timepoint_the_drawn_wells_have_is_the_one_tested(plate):
    assert _tested(_rare(plate, "t2")) == _counts_at(2)


def test_no_timepoint_named_is_the_latest(plate):
    assert _tested(_rare(plate, "")) == _counts_at(4)
