"""The Rare responders test on the Statistics view tests each nanowell's moving average at the Timepoint when the
moving average is on, taken exactly as the other Statistics tests take it (``moving_compute.rebase`` then
``moving_compute.apply``), and excludes — never zeroes — a nanowell the window leaves without a value.

Driven through ``controller.draw_view`` (the composition the page and Batch Export both call), so a controller that
forgets to pass the window, a ``compose`` that forgets to apply it, or a key that does not change with it all fail.

The plate is built so the raw and averaged values flag *different* nanowells, at the last of five timepoints t0..t4
(a window of 3 at t4 is t2, t3, t4):

* control wells A01..A03: every nanowell holds a steady 2–4 cells (each its own constant, so its window mean is
  that constant);
* treatment wells B01, B02: steady nanowells as the control, plus **spike** nanowells 1, 1, 1, 1, 9 (raw 9 is past the
  control cutoff; the window gives 11/3, inside it), **sustained** nanowells 1, 1, 8, 8, 8 (8 either way: past it
  both ways), and **dip** nanowells 1, 1, 10, 10, 2 (raw 2 is inside; the window gives 22/3, past it);
* B01's nanowell 0 is occluded at t3, so its window at t4 has a gap (``moving_compute.GAP_REASON``);
* treatment well B03 is imaged at t3 and t4 only, so its t4 is the second timepoint of its well and no window of 3
  is complete there (``moving_compute.EARLY_REASON``).
"""

from __future__ import annotations

import pandas as pd
import pytest
from matplotlib.figure import Figure

from review.features.nanowell import _rare_controller as rc
from review.features.nanowell import controller as nwc
from review.features.nanowell import moving_compute as mvc
from review.features.nanowell import rare_data_compute as rdc

TPS = [f"t{i}" for i in range(5)]
STEADY = 12                                     # steady nanowells per well (0..11 in the control wells)
SPIKE, SUSTAINED, DIP = (20, 21), (22, 23), (24, 25)
GROUPS = {"ctrl": ["A01", "A02", "A03"], "drug": ["B01", "B02", "B03"]}


def _plate(tmp_path):
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics
    from tests.test_nanowell_review_data import _dataset, _row

    def site(q, counts, tps=TPS):
        return [_row(tp, q, 0, state="occluded") if n is None else _row(tp, q, 0, n=n, c=0.1 * n)
                for tp, n in zip(tps, counts, strict=True)]

    def steady(q):
        return [2 + (q % 3)] * len(TPS)

    wells = {}
    for well in ("A01", "A02", "A03"):
        wells[well] = [r for q in range(STEADY) for r in site(q, steady(q))]
    for well in ("B01", "B02"):
        rows = [r for q in range(STEADY) for r in site(q, steady(q))]
        if well == "B01":                       # nanowell 0: a gap at t3, inside t4's window
            rows = [r for r in rows if r["nw_q"] != 0] + site(0, [2, 2, 2, None, 2])
        rows += [r for q in SPIKE for r in site(q, [1, 1, 1, 1, 9])]
        rows += [r for q in SUSTAINED for r in site(q, [1, 1, 8, 8, 8])]
        rows += [r for q in DIP for r in site(q, [1, 1, 10, 10, 2])]
        wells[well] = rows
    wells["B03"] = [r for q in range(STEADY) for r in site(q, [3, 3], TPS[3:])]
    starts = {tp: f"2026-03-01T{9 + i:02d}:00:00" for i, tp in enumerate(TPS)}
    data = load_nanowell_data(_dataset(tmp_path, wells, starts=starts))
    return site_metrics(data), data


@pytest.fixture
def plate(tmp_path):
    return _plate(tmp_path)


def _draw(plate, store, moving=0, **kw):
    """``draw_view`` on the Statistics view under the Rare responders test; returns the key asked for and the model."""
    metrics, data = plate
    asked = []
    choices = dict(groups=GROUPS, metric="n", channel=None, axis="wells", timepoint="t4", control="ctrl",
                   baseline="earliest", cohort=False, well="A01", well_timepoint="t4", stats_test="rare",
                   moving=moving, rare={"store": store, "request": lambda key, job: asked.append((key, job))})
    _hit, model = nwc.draw_view(Figure(), "statistics", metrics, data, **{**choices, **kw})
    return asked, model


def _result(plate, moving=0, **kw):
    """The result the page draws once the worker has run: asked for, computed, then drawn from the store."""
    store: dict = {}
    asked, model = _draw(plate, store, moving, **kw)
    assert model is None and len(asked) == 1
    key, job = asked[0]
    store[key] = job()
    again, model = _draw(plate, store, moving, **kw)
    assert again == [] and model is store[key]
    return key, model


def _flagged(result) -> set:
    return {(row["site"][0], row["site"][1]) for row in rc.flagged_rows(result)}


def _tested(result) -> dict:
    """``(well, q) -> the value tested`` for every kept nanowell."""
    kept = result.prepared.kept
    return {(w, int(n.split(",")[0])): v for w, n, v in zip(kept["well_id"], kept["nanowell_id"], kept["fc"])}


def test_the_tested_values_are_the_moving_averages_at_the_timepoint(plate):
    metrics, _data = plate
    _key, result = _result(plate, moving=3)
    # moving_compute's own windowed values at t4 for the same wells: the test's values are exactly these.
    frame = metrics[metrics["well"].isin([w for ws in GROUPS.values() for w in ws])]
    want_frame, _why = mvc.apply(frame, "n", pd.Series("", index=frame.index, dtype=object), 3)
    at = want_frame[want_frame["timepoint"] == "t4"]
    want = {(w, int(q)): v for w, q, v in zip(at["well"], at["nw_q"], at["n"]) if pd.notna(v)}
    assert _tested(result) == pytest.approx(want)
    # And those are the hand numbers, not the raw counts: a spike flattens to 11/3, a dip rises to 22/3.
    tested = _tested(result)
    assert tested[("B01", SPIKE[0])] == pytest.approx(11 / 3)
    assert tested[("B02", DIP[0])] == pytest.approx(22 / 3)
    assert tested[("B01", SUSTAINED[0])] == pytest.approx(8.0)
    # So the window flags the sustained and dip nanowells, and the raw test the sustained and spike ones.
    treated = {(w, q) for w in ("B01", "B02") for q in SUSTAINED + DIP}
    assert _flagged(result) == treated
    _key, raw = _result(plate, moving=0)
    assert _flagged(raw) == {(w, q) for w in ("B01", "B02") for q in SUSTAINED + SPIKE}
    assert _tested(raw)[("B01", SPIKE[0])] == 9.0


def test_a_nanowell_the_window_leaves_without_a_value_is_excluded_with_its_reason(plate):
    _key, result = _result(plate, moving=3)
    counts = result.log["exclusions_by_reason"]
    # B03's twelve nanowells: t4 is the second timepoint of their well; B01's nanowell 0: a gap at t3.
    assert counts.get(mvc.EARLY_REASON) == STEADY
    assert counts.get(mvc.GAP_REASON) == 1
    assert rdc.VALUE_MISSING not in counts                     # each is excluded by the window's reason, not "missing"
    excluded = result.prepared.exclusions
    early = excluded[excluded["reason"] == mvc.EARLY_REASON]
    assert set(early["well_id"]) == {"B03"}
    tested = _tested(result)
    assert not any(w == "B03" for w, _q in tested) and ("B01", 0) not in tested     # never tested as a number
    # Raw, those nanowells are measured at t4 and tested.
    _key, raw = _result(plate, moving=0)
    assert ("B01", 0) in _tested(raw) and ("B03", 0) in _tested(raw)
    assert not raw.log["exclusions_by_reason"]


def test_a_window_that_leaves_nothing_at_the_timepoint_says_so(plate):
    metrics, data = plate
    fig = Figure()
    asked = []
    _hit, model = nwc.draw_view(
        fig, "statistics", metrics, data, groups=GROUPS, metric="n", channel=None, axis="wells", timepoint="t1",
        control="ctrl", baseline="earliest", cohort=False, well="A01", well_timepoint="t1", stats_test="rare",
        moving=3, rare={"store": {}, "request": lambda key, job: asked.append(key)})
    assert model is None and asked == []                      # nothing tested, nothing asked of the worker
    frame = metrics[metrics["well"].isin([w for ws in GROUPS.values() for w in ws])]
    want = mvc.nothing_left_text(frame, "n", 3, timepoint="t1")
    assert want
    from matplotlib.text import Text

    assert [t.get_text() for t in fig.findobj(Text) if t.get_gid() == "nw-moving-empty"] == [want]


def test_raw_and_each_window_are_keyed_apart(plate):
    keys = [_result(plate, moving=m)[0] for m in (0, 3, 5)]
    assert len(set(keys)) == 3
    # And the same window asks for the same key again, so a held result is reused.
    assert _result(plate, moving=3)[0] == keys[1]


def test_details_say_the_values_are_moving_averages(plate):
    _key, result = _result(plate, moving=3)
    notes = result.page_notes
    assert notes[0].startswith(rc.VALUE_NOTE_MOVING)
    assert mvc.describe(3, "n") in notes
    assert rc.NOT_APPLIED not in notes                        # the Fixed cohort is not set
    _key, raw = _result(plate, moving=0)
    assert raw.page_notes[0].startswith(rc.VALUE_NOTE) and not raw.page_notes[0].startswith(rc.VALUE_NOTE_MOVING)
    assert not any(n.startswith("Moving average") for n in raw.page_notes)
    # The Fixed cohort still does not apply, and is the only thing the note names.
    _key, cohort = _result(plate, moving=3, cohort=True)
    assert rc.NOT_APPLIED in cohort.page_notes
    assert rc.NOT_APPLIED == "The Fixed cohort does not apply to the rare-responder tests."


def _stamp(fig) -> list:
    return [t.get_text() for t in fig.texts if t.get_gid() == "nw-subset-label"]


@pytest.mark.parametrize("moving, want", [(3, ["Moving average over 3 timepoints (trailing)"]), (0, [])])
def test_the_rare_figure_carries_the_moving_average_tag_only_when_it_is_on(plate, moving, want):
    metrics, data = plate
    for store in ({}, None):                       # while computing, and drawn from a held result
        fig = Figure()
        held: dict = {}
        if store is None:
            key, result = _result(plate, moving=moving)
            held = {key: result}
        nwc.draw_view(fig, "statistics", metrics, data, groups=GROUPS, metric="n", channel=None, axis="wells",
                      timepoint="t4", control="ctrl", baseline="earliest", cohort=False, well="A01",
                      well_timepoint="t4", stats_test="rare", moving=moving,
                      rare={"store": held, "request": lambda key, job: None})
        assert _stamp(fig) == want


@pytest.mark.parametrize("size", [(15.0, 8.6), (4.3, 7.3)], ids=["desk", "phone"])
@pytest.mark.parametrize("moving, subset_label", [(3, ""), (0, "Subset: “Big”"), (3, "Subset: “Big”")])
def test_the_top_left_label_never_overlaps_the_headline(plate, size, moving, subset_label):
    """Seen on the page: with the moving average on, "Moving average over 3 timepoints (centred)" was drawn across the
    start of the verdict's headline. The label is stamped at the top left after the view is drawn, and the Rare
    responders page started its headline in that same strip. A saved subset's name collided the same way. Measured
    on the drawn figure: the label's box ends above the headline's."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    metrics, data = plate
    key, result = _result(plate, moving=moving)
    fig = Figure(figsize=size)
    canvas = FigureCanvasAgg(fig)
    nwc.draw_view(fig, "statistics", metrics, data, groups=GROUPS, metric="n", channel=None, axis="wells",
                  timepoint="t4", control="ctrl", baseline="earliest", cohort=False, well="A01", well_timepoint="t4",
                  stats_test="rare", moving=moving, subset_label=subset_label,
                  rare={"store": {key: result}, "request": lambda k, j: None})
    canvas.draw()
    renderer = canvas.get_renderer()
    (label,) = [t for t in fig.texts if t.get_gid() == "nw-subset-label"]
    # The figure's first line names the groups tested; the headline comes under it.
    (head,) = [t for t in fig.texts if t.get_gid() == "nw-rare-groups"]
    verdict = [t for t in fig.texts if t.get_gid() == "nw-rare-unit"][0]
    assert label.get_text() and head.get_text().startswith("2 groups tested:")
    assert verdict.get_text().startswith("drug:")
    label_box, head_box = label.get_window_extent(renderer), head.get_window_extent(renderer)
    assert label_box.y0 >= head_box.y1, (label_box, head_box)        # display y grows upwards
    assert head_box.y0 >= verdict.get_window_extent(renderer).y1
