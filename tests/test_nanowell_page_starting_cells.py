"""The Nanowells page's *Starting cells* filter: only the nanowells that held a given number of cells at the
baseline are drawn and summarised, on every view but the Array, which dims the rest.

Asked for: "filter nanowells for purposes of inclusion … only compute statistics/display [nanowells] that start
with a minimum number of cells, and then use that for the nanowell montage, trajectories, distributions display".
The fixture's starting counts are chosen so a view that ignored the filter picks different nanowells: unfiltered,
Extremes' smallest is q1 (a 1× tie broken by address); with "1 (single cell)" q1 (2 cells at baseline) is out and
the smallest is q2 (CLAUDE.md §7.8 rule 2). The page half drives the real builder and the real combo and spin
boxes in the minimal shell the other page tests use, and reads the figures' own artists.
"""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import starting_cells_compute as sc  # noqa: E402
from review.features.nanowell.collect import load_nanowell_data  # noqa: E402
from review.features.nanowell.series_compute import site_metrics  # noqa: E402
from review.features.nanowell.state import NanowellState  # noqa: E402
from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset  # noqa: E402

#: A01: q0–q7 hold 1, 2, 1, 3, 1, 0, ?, 2 cells at t_c (q6 occluded there, 1 cell at t_a); count fold changes at
#: t_d are q0 4, q1 1, q2 1, q3 3, q4 3, q5 seeded later, q6 none, q7 4. B01: two single-cell nanowells.
COUNTS = {"A01": {"t_c": [1, 2, 1, 3, 1, 0, None, 2], "t_a": [2, 2, 1, 6, 2, 1, 1, 4],
                  "t_d": [4, 2, 1, 9, 3, 1, 1, 8]},
          "B01": {"t_c": [1, 1], "t_a": [1, 2], "t_d": [2, 3]}}
SINGLE = (1, 1)


@pytest.fixture
def metrics(tmp_path):
    return site_metrics(load_nanowell_data(_write_nanowell_dataset(tmp_path, counts=COUNTS)))


def _sites(frame):
    return sorted(set(zip(frame["well"], frame["nw_q"].astype(int))))


# ── the filter ───────────────────────────────────────────────────────────────
def test_the_filter_keeps_the_nanowells_whose_baseline_count_is_in_range_and_counts_the_rest(metrics):
    kept, done = sc.apply(metrics, 1, 1, "earliest")
    assert _sites(kept) == [("A01", 0), ("A01", 2), ("A01", 4), ("B01", 0), ("B01", 1)]
    assert (done.total, done.kept, done.outside, done.unknown) == (10, 5, 4, 1)
    assert done.per_well == {"A01": 3, "B01": 2}
    assert ("A01", 6, 0) in done.outside_sites and ("A01", 0, 0) not in done.outside_sites
    assert sc.note(done) == ("Starting cells 1 (count at the earliest timepoint): 5 of 10 nanowells kept — 4 outside "
                             "the range, 1 with no count at baseline. Few nanowells in the filter: A01 (3), B01 (2).")
    assert sc.apply(metrics, None, None, "earliest") == (metrics, None)          # no bound: no filter at all


def test_under_first_measurable_a_count_taken_later_is_kept_and_said(tmp_path):
    data = load_nanowell_data(_write_nanowell_dataset(tmp_path, counts=COUNTS))
    kept, done = sc.apply(site_metrics(data, "first_measurable"), 1, None, "first_measurable", ["A01"])
    assert ("A01", 6) in _sites(kept) and done.late == 1                         # counted at t_a, not t_c
    assert "1 kept were counted after the earliest timepoint, when their cells may already have divided" in sc.note(
        done)


def test_bounds_are_whole_numbers_and_a_minimum_of_zero_is_no_minimum():
    assert sc.normalise(0, None) == (None, None) and sc.normalise(1, 2) == (1, 2)
    for lo, hi in ((3, 2), (True, None), (-1, None), (1.5, None)):
        with pytest.raises(ValueError):
            sc.normalise(lo, hi)
    assert [sc.bounds_text(*b) for b in ((1, None), (1, 1), (1, 2), (None, 5), (None, None))] == [
        "≥1", "1", "1–2", "≤5", "any"]
    assert [sc.preset_of(*b) for b in ((None, None), (1, 1), (2, None), (3, 7))] == [
        "any", "single", "two_plus", "custom"]
    assert [sc.occupancy_in_filter(c, 1, 2) for c in ("0", "1", "2", "3+", None)] == [False, True, True, False,
                                                                                     False]


def test_the_bounds_persist_and_a_saved_pair_that_is_not_a_filter_is_refused():
    st = NanowellState()
    st.start_min, st.start_max = 1, 2
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert (restored.start_min, restored.start_max) == (1, 2)
    for saved in ({"start_min": 3, "start_max": 2}, {"start_min": "1"}, {"start_min": True}, {"start_max": -1}):
        restored.apply_config(saved)
        assert (restored.start_min, restored.start_max) == (None, None)


# ── the page ─────────────────────────────────────────────────────────────────
@pytest.fixture
def page(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(nwc, "crops_for_sites", lambda _d, _m, sites: {s: {} for s in sites})
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=COUNTS))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    _loaded(qtbot, ctx)
    return ctx


def _texts(ctx, fig="_nw_fig"):
    figure = ctx.widgets.get(fig)
    return [t.get_text() for t in list(figure.texts) + [t for ax in figure.axes for t in ax.texts]]


def _choose(ctx, key):
    combo = ctx.widgets.get("_nw_start_cb")
    combo.setCurrentIndex(combo.findData(key))                   # emits currentIndexChanged, as a click does


def _view(ctx, key):
    from review.features.nanowell.tab_view import VIEWS

    ctx.widgets.get("_nw_view_seg").setCurrentIndex([k for k, _l in VIEWS].index(key))


def _dots(ctx):
    st = ctx.state.feature_slice("nanowell")
    return sorted({(s[0], int(s[1])) for _ax, _x, _y, sites in st.hits.entries for s in sites})


def test_choosing_a_preset_draws_only_the_nanowells_in_it_and_the_figure_says_so(page):
    assert ("A01", 1) in _dots(page)                             # unfiltered: every nanowell with a value
    _choose(page, "single")
    st = page.state.feature_slice("nanowell")
    assert (st.start_min, st.start_max) == SINGLE
    assert _dots(page) == [("A01", 0), ("A01", 2), ("A01", 4), ("B01", 0), ("B01", 1)]
    assert any(t.startswith("Starting cells 1 (count at the earliest timepoint): 5 of 10 nanowells kept")
               for t in _texts(page))
    assert page.widgets.get("_nw_start_cb").property("filterActive") is True
    _choose(page, "any")
    assert ("A01", 1) in _dots(page) and page.widgets.get("_nw_start_cb").property("filterActive") is False
    assert not any(t.startswith("Starting cells") for t in _texts(page))


def test_the_occupancy_key_greys_the_classes_the_filter_removes(page):
    _choose(page, "single")
    legend = [t for ax in page.widgets.get("_nw_fig").axes for t in ax.texts if t.get_gid() == "nw-legend"]
    assert legend[0].get_text() == "Cells at baseline (earliest; faint: filtered out):"
    assert {t.get_text(): t.get_alpha() for t in legend[1:]} == {"0": 0.35, "1": 1.0, "2": 0.35, "3+": 0.35,
                                                                "?": 0.35}
    key = next(t.axes for t in legend)
    markers = [c for c in key.collections]
    assert len(markers) == 1 and list(markers[0].get_alpha()) == [0.2, 1.0, 0.2, 0.2, 0.2]   # 0, 1, 2, 3+, ?


def test_custom_bounds_are_typed_in_the_spin_boxes_and_stay_custom(page):
    _choose(page, "custom")
    lo, hi = page.widgets.get("_nw_start_min"), page.widgets.get("_nw_start_max")
    assert lo.isVisibleTo(page.window) and hi.isVisibleTo(page.window)
    st = page.state.feature_slice("nanowell")
    assert (st.start_min, st.start_max) == (None, None)          # choosing Custom changes nothing yet
    lo.setValue(2)
    hi.setValue(3)
    assert (st.start_min, st.start_max) == (2, 3)
    assert page.widgets.get("_nw_start_cb").currentData() == "custom"
    assert _dots(page) == [("A01", 1), ("A01", 3), ("A01", 7)]
    hi.setValue(-1)                                              # "any"
    assert (st.start_min, st.start_max) == (2, None)


def test_trajectories_and_position_carry_the_note(page):
    _choose(page, "single")
    for view in ("trajectories", "position"):
        _view(page, view)
        assert any("Starting cells 1 (count at the earliest timepoint): 5 of 10" in t for t in _texts(page)), view


def test_extremes_ranks_only_the_nanowells_in_the_filter(page):
    _view(page, "extremes")
    page.widgets.get("_nw_extremes_spin").setValue(1)
    rows = lambda: [t.get_text().split("\n")[0] for t in page.widgets.get("_nw_fig").texts  # noqa: E731
                    if t.get_gid() == "nw-extremes-row"]
    assert rows() == ["#1  q 0, r 0", "#1  q 1, r 0"]
    _choose(page, "single")
    assert rows() == ["#1  q 0, r 0", "#1  q 2, r 0"]
    assert any(t.startswith("Starting cells 1 (count at the earliest timepoint): 3 of 8 nanowells kept")
               for t in _texts(page))


def test_a_filter_that_keeps_nothing_says_so(page):
    _choose(page, "custom")
    page.widgets.get("_nw_start_min").setValue(50)
    assert _texts(page) == ["No nanowell of the drawn wells started with ≥50 cells (count at the earliest "
                            "timepoint). Widen Starting cells or choose Any."]


def test_the_array_view_dims_the_nanowells_outside_and_counts_them(page):
    _view(page, "array")
    _choose(page, "single")
    fig = page.widgets.get("_nw_fig")
    veils = [c for ax in fig.axes for c in ax.collections if c.get_gid() == "nw-array-dimmed"]
    st = page.state.feature_slice("nanowell")
    assert st.drawn is not None, _texts(page)
    shown = {tuple(s) for s in st.drawn.sites.sites}
    assert len(veils) == 1 and len(veils[0].get_offsets()) == len({("A01", q, 0) for q in (1, 3, 5, 6, 7)} & shown)
    assert any(t.startswith("5 outside Starting cells 1, drawn dimmed.") for t in _texts(page))


def test_a_selected_nanowell_outside_the_filter_says_so_in_its_detail(page):
    _choose(page, "single")
    st = page.state.feature_slice("nanowell")
    st.selected_sites = [["A01", 3, 0], ["A01", 0, 0]]
    nwc.selection_changed(page)
    detail = [t.get_text() for t in page.widgets.get("_nw_detail_fig").texts if t.get_gid() == "nw-detail-states"]
    assert detail[0].endswith(" · outside Starting cells 1 (3 cells at baseline)")
    assert "outside Starting cells" not in detail[1]


def test_the_long_csv_keeps_every_row_and_flags_it(page, tmp_path):
    _choose(page, "single")
    st = page.state.feature_slice("nanowell")
    path = tmp_path / "long.csv"
    nwc.export_long_csv(st, path, ["A01", "B01"])
    table = pd.read_csv(path)
    assert len(table) == 10 * 3                                  # ten nanowells, three timepoints, none dropped
    flagged = table.drop_duplicates(["well", "nw_q"])
    assert sorted(zip(flagged["well"], flagged["nw_q"], flagged["in_starting_cells_filter"])) == [
        ("A01", 0, True), ("A01", 1, False), ("A01", 2, True), ("A01", 3, False), ("A01", 4, True),
        ("A01", 5, False), ("A01", 6, False), ("A01", 7, False), ("B01", 0, True), ("B01", 1, True)]
    assert set(zip(table["starting_cells_min"], table["starting_cells_max"], table["starting_cells_baseline"])) == {
        (1, 1, "earliest")}
