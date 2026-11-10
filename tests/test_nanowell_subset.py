"""The Nanowells page's *Subset*: any number of conditions — starting cells, a metric at a timepoint, ring, distance
from the centre, address, imaging field — that a nanowell must meet, all or any, to be drawn.

Asked for: "Expand the starting cells tool to a subset builder. Allow specification of any combination and number
of fields, metric, position, ring/position distance, etc."

The fixture's counts are those of ``test_nanowell_page_starting_cells`` (A01: q0–q7, B01: q0–q1, over t_c < t_a <
t_d). Each assertion is set where a wrong reading of the condition picks other nanowells (CLAUDE.md §7.8 rule 2):
*at t_a* and *at the latest timepoint* differ on B01 q0; *at every timepoint* and *at any* differ on A01 q5 and q6;
*all* and *any* keep nothing against three nanowells. The position cases are on a hand-built frame with one
hexagonal and one square well, where the ring and the distance of (1, 1) differ between the two lattices.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import subset_compute as ss  # noqa: E402
from review.features.nanowell.collect import load_nanowell_data  # noqa: E402
from review.features.nanowell.series_compute import site_metrics  # noqa: E402
from review.features.nanowell.state import NanowellState  # noqa: E402
from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset  # noqa: E402
from tests.test_nanowell_page_starting_cells import COUNTS, _dots, _texts  # noqa: E402

C = ss.Condition


@pytest.fixture
def metrics(tmp_path):
    return site_metrics(load_nanowell_data(_write_nanowell_dataset(tmp_path, counts=COUNTS)))


def _kept(metrics, *conditions, match="all", data=None):
    kept, done = ss.apply(metrics, ss.Subset(tuple(conditions), match).validated(), "earliest", data=data)
    return sorted(set(zip(kept["well"], kept["nw_q"].astype(int)))), done


# ── a metric, read at a timepoint ────────────────────────────────────────────
def test_a_metric_is_read_at_the_timepoint_the_condition_names(metrics):
    at_ta, _ = _kept(metrics, C("n", "ge", 2, at="tp:t_a"))
    latest, _ = _kept(metrics, C("n", "ge", 2, at="latest"))
    earliest, _ = _kept(metrics, C("n", "ge", 2, at="earliest"))
    assert at_ta == [("A01", 0), ("A01", 1), ("A01", 3), ("A01", 4), ("A01", 7), ("B01", 1)]
    assert latest == [("A01", 0), ("A01", 1), ("A01", 3), ("A01", 4), ("A01", 7), ("B01", 0), ("B01", 1)]
    assert earliest == [("A01", 1), ("A01", 3), ("A01", 7)]


def test_every_timepoint_needs_a_passing_value_at_each_and_any_needs_one(metrics):
    every, done = _kept(metrics, C("n", "ge", 1, at="every"))
    assert ("A01", 5) not in every and ("A01", 6) not in every       # 0 cells at t_c; no count at t_c
    assert (done.kept, done.outside, done.unknown) == (8, 1, 1)      # q5 failed, q6 lacked a value
    anyt, done = _kept(metrics, C("n", "ge", 1, at="any"))
    assert ("A01", 5) in anyt and ("A01", 6) in anyt and done.kept == 10


def test_all_and_any_combine_the_conditions(metrics):
    started_three, fold_four = C("n0", "eq", 3), C("count_fc", "ge", 4.0, at="latest")
    assert _kept(metrics, started_three, fold_four)[0] == []
    assert _kept(metrics, started_three, fold_four, match="any")[0] == [("A01", 0), ("A01", 3), ("A01", 7)]


def test_not_between_keeps_the_values_outside_the_range_and_an_unknown_is_neither(metrics):
    kept, done = _kept(metrics, C("n0", "outside", 1, 2))
    assert kept == [("A01", 3), ("A01", 5)] and (done.outside, done.unknown) == (7, 1)


def test_the_note_names_every_condition_the_baseline_and_the_counts(metrics):
    _kept_sites, done = _kept(metrics, C("n0", "ge", 1), C("count_fc", "ge", 3.0, at="latest"))
    assert ss.note(done) == (
        "Subset: Starting cells ≥1 and Count fold change at the latest timepoint ≥3 (baseline: the earliest "
        "timepoint): 5 of 10 nanowells kept — 4 outside the subset, 1 with no value for a condition. Few "
        "nanowells in the subset: A01 (4), B01 (1).")
    assert ss.empty_text(_kept(metrics, C("n0", "ge", 50), C("ring", "le", 1))[1]) == (
        "No nanowell of the drawn wells is in the subset (Starting cells ≥50 and Ring (steps from the centre) ≤1). "
        "Widen the subset or choose All nanowells.")


def test_a_nanowell_outside_says_which_condition_it_failed_or_lacked(metrics):
    subset = ss.Subset((C("n0", "ge", 1), C("count_fc", "ge", 3.0, at="latest"))).validated()
    assert ss.site_badge(nwc.site_series(metrics, "A01", 1, 0), subset) == (
        "outside the subset (fails Count fold change at the latest timepoint ≥3)")
    assert ss.site_badge(nwc.site_series(metrics, "A01", 6, 0), subset) == (
        "outside the subset (no value for Starting cells ≥1; Count fold change at the latest timepoint ≥3)")
    assert ss.site_badge(nwc.site_series(metrics, "A01", 0, 0), subset) is None


# ── position and imaging field ───────────────────────────────────────────────
ADDRESSES = [(0, 0), (1, 0), (0, 1), (-1, 1), (1, 1), (2, 0)]


def _plate():
    """A hexagonal well A01 and a square well B01 of pitch 100 µm, each nanowell at one timepoint on a tile of its
    own column parity."""
    rows = [{"well": w, "nw_q": q, "nw_r": r, "timepoint": "t0", "tp_order": 0, "n": 1.0, "n0": 1.0,
             "baseline_timepoint": "t0", "owner": f"T{q % 2}"} for w in ("A01", "B01") for q, r in ADDRESSES]
    grids = {"A01": {"t0": {"pitch_um": 100.0, "lattice_kind": "hex"}},
             "B01": {"t0": {"pitch_um": 100.0, "lattice_kind": "square"}}}
    data = SimpleNamespace(grids=grids, timepoints=lambda well=None: ["t0"])
    return pd.DataFrame(rows), data


def _addresses(frame, *conditions, data):
    kept, _done = ss.apply(frame, ss.Subset(tuple(conditions)).validated(), "earliest", data=data)
    return sorted(zip(kept["well"], kept["nw_q"], kept["nw_r"]))


def test_ring_and_distance_follow_each_well_s_own_lattice():
    frame, data = _plate()
    hex_ring_one = [("A01", -1, 1), ("A01", 0, 0), ("A01", 0, 1), ("A01", 1, 0)]
    square_ring_one = [("B01", -1, 1), ("B01", 0, 0), ("B01", 0, 1), ("B01", 1, 0), ("B01", 1, 1)]
    assert _addresses(frame, C("ring", "le", 1), data=data) == hex_ring_one + square_ring_one
    # (1, 1) is 173 µm out on the hexagonal lattice and 141 µm on the square one; (-1, 1) is 100 µm and 141 µm.
    assert _addresses(frame, C("distance_um", "le", 150.0), data=data) == hex_ring_one + square_ring_one
    assert _addresses(frame, C("distance_um", "between", 120.0, 160.0), data=data) == [("B01", -1, 1), ("B01", 1, 1)]


def test_a_distance_with_no_recorded_pitch_is_no_value_not_zero():
    frame, data = _plate()
    data.grids["A01"]["t0"].pop("pitch_um")
    _kept_rows, done = ss.apply(frame, ss.Subset((C("distance_um", "le", 1000.0),)).validated(), "earliest",
                                ["A01"], data)
    assert (done.kept, done.unknown) == (0, len(ADDRESSES))


def test_the_address_and_the_imaging_field_are_conditions():
    frame, data = _plate()
    assert _addresses(frame, C("nw_q", "ge", 1), C("nw_r", "eq", 0), data=data) == [
        ("A01", 1, 0), ("A01", 2, 0), ("B01", 1, 0), ("B01", 2, 0)]
    odd = _addresses(frame, C("owner", "in", values=("T1",), at="any"), data=data)
    assert odd == [(w, q, r) for w in ("A01", "B01") for q, r in sorted(ADDRESSES) if q % 2]
    even = _addresses(frame, C("owner", "not_in", values=("T1",), at="any"), data=data)
    assert sorted(odd + even) == sorted(zip(frame["well"], frame["nw_q"], frame["nw_r"]))


def _seam_plate():
    """Four nanowells over two timepoints: (0,0) never on a seam, (1,0) on one at t1 only (not at the baseline t0),
    (2,0) on one at both, and (3,0) with no flag recorded at t1. The flag comes as the CSV reader gives it — a bool,
    or its text in an older table — and is missing where a run recorded none."""
    flags = {(0, "t0"): False, (0, "t1"): False, (1, "t0"): False, (1, "t1"): True,
             (2, "t0"): True, (2, "t1"): "True", (3, "t0"): "False", (3, "t1"): None}
    rows = [{"well": "A01", "nw_q": q, "nw_r": 0, "timepoint": tp, "tp_order": i, "n": 1.0, "n0": 1.0,
             "baseline_timepoint": "t0", "fused": flags[(q, tp)]} for q in range(4) for i, tp in enumerate(("t0", "t1"))]
    return pd.DataFrame(rows), SimpleNamespace(grids={}, timepoints=lambda well=None: ["t0", "t1"])


def test_the_nanowells_on_a_tile_seam_can_be_kept_alone_or_left_out():
    frame, data = _seam_plate()

    def kept(op, at):
        return sorted({q for _w, q, _r in _addresses(frame, C("fused", op, at=at), data=data)})

    assert kept("yes", "any") == [1, 2]                   # ever on a seam
    assert kept("no", "every") == [0]                     # never on one; (3,0) has no flag at t1, so is undecided
    assert kept("yes", "baseline") == [2]
    assert kept("no", "baseline") == [0, 1, 3]
    assert kept("yes", "tp:t1") == [1, 2]
    _rows, done = ss.apply(frame, ss.Subset((C("fused", "no", at="every"),)).validated(), "earliest", data=data)
    assert (done.kept, done.outside, done.unknown) == (1, 2, 1)
    assert C("fused", "no", at="every").validated().text() == "On a tile seam at every timepoint: no"


def test_a_seam_condition_takes_yes_or_no_and_persists():
    for bad in (C("fused", "ge", 1), C("fused", "in", values=("x",)), C("fused", "yes", at="soon")):
        with pytest.raises(ValueError):
            bad.validated()
    assert C("fused", "yes", 5, 7).validated() == C("fused", "yes", at="baseline")      # no bound is kept
    st = NanowellState()
    st.subset = ss.Subset((C("fused", "no", at="every"), C("n0", "ge", 1))).validated()
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert restored.subset == st.subset
    assert "a" not in st.to_config()["subset"]["conditions"][0]


# ── what a subset is ─────────────────────────────────────────────────────────
def test_a_condition_that_is_not_one_is_refused_with_its_reason():
    for bad in (C("n", "between", 3, 2, at="latest"), C("owner", "in", values=()), C("nope", "ge", 1),
                C("n0", "ge", -1), C("n", "ge", 1.5), C("ring", "in", values=("x",)), C("c", "ge", 0.1, at="soon")):
        with pytest.raises(ValueError):
            bad.validated()
    assert C("growth_rate", "ge", 0.1, at="latest").validated().at == ""          # one value per nanowell


def test_the_subset_persists_and_one_bad_condition_refuses_the_whole_block():
    st = NanowellState()
    st.subset = ss.Subset((C("n0", "eq", 1), C("brightness_fc_GFP", "between", 1.5, 3.0, at="tp:24h"),
                           C("owner", "not_in", values=("T3", "T1"), at="baseline")), "any").validated()
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert restored.subset == st.subset and restored.subset.conditions[2].values == ("T1", "T3")
    block = st.to_config()
    block["subset"]["conditions"][1]["b"] = 1.0                                  # now upside down
    restored.apply_config(block)
    assert restored.subset == ss.Subset()


def test_the_occupancy_key_fades_only_what_every_kept_nanowell_is_known_to_lack():
    ring = C("ring", "le", 3)
    assert ss.Subset((C("n0", "ge", 1), ring)).start_bounds() == (1, None)
    assert ss.Subset((C("n0", "ge", 1), ring), "any").start_bounds() == (None, None)
    assert ss.Subset((C("n0", "ge", 1), C("n0", "le", 2))).start_bounds() == (1, 2)
    assert ss.Subset((C("n0", "outside", 1, 2),)).start_bounds() == (None, None)


# ── the page: the real editor ────────────────────────────────────────────────
@pytest.fixture
def page(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(nwc, "crops_for_sites", lambda _d, _m, sites: {s: {} for s in sites})
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=COUNTS))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    _loaded(qtbot, ctx)
    return ctx


def _editor(ctx):
    ctx.widgets.get("_nw_subset_edit_btn").click()
    return ctx.widgets.get("_nw_subset_editor")


def test_the_editor_builds_a_subset_of_two_conditions_and_the_figure_draws_it(page):
    editor = _editor(page)
    assert editor.isVisibleTo(page.window)
    editor.add_btn.click()                                       # Starting cells ≥1
    editor.add_btn.click()
    row = editor.rows[1]
    row.field_cb.setCurrentIndex(row.field_cb.findData("count_fc"))
    assert row.at_cb.isVisibleTo(page.window)
    row.at_cb.setCurrentIndex(row.at_cb.findData("latest"))
    row.a_spin.setValue(3)
    st = page.state.feature_slice("nanowell")
    assert st.subset == ss.Subset() and editor.apply_btn.isEnabled()  # written, not drawn until Apply
    editor.apply_btn.click()
    assert st.subset == ss.Subset((C("n0", "ge", 1), C("count_fc", "ge", 3.0, at="latest")))
    assert not editor.apply_btn.isEnabled() and not editor.reset_btn.isEnabled()
    assert _dots(page) == [("A01", 0), ("A01", 3), ("A01", 4), ("A01", 7), ("B01", 1)]
    assert any(t.startswith("Subset: Starting cells ≥1 and Count fold change at the latest timepoint ≥3")
               for t in _texts(page))
    assert page.widgets.get("_nw_subset_cb").currentData() == "custom"
    assert page.widgets.get("_nw_subset_cb").property("filterActive") is True
    editor.match_cb.setCurrentIndex(editor.match_cb.findData("any"))
    editor.apply_btn.click()
    assert st.subset.match == "any" and ("A01", 1) in _dots(page)    # 2 cells at the start: meets the first


def test_a_range_typed_upside_down_is_said_and_changes_nothing(page):
    editor = _editor(page)
    editor.add_btn.click()
    st = page.state.feature_slice("nanowell")
    row = editor.rows[0]
    row.op_cb.setCurrentIndex(row.op_cb.findData("between"))         # starts as 1–1: a range, never upside down
    editor.apply_btn.click()
    assert st.subset == ss.Subset.starting(1, 1)
    before = st.subset
    row.a_spin.setValue(5)
    assert not editor.apply_btn.isEnabled()                          # not a subset: nothing to apply
    editor.apply_btn.click()
    assert st.subset == before
    assert editor.status.objectName() == "Warn" and "lower bound (5) is above the upper (1)" in editor.status.text()


def test_removing_and_clearing_conditions_brings_every_nanowell_back(page):
    editor = _editor(page)
    editor.add_btn.click()
    editor.add_btn.click()
    editor.apply_btn.click()
    st = page.state.feature_slice("nanowell")
    editor.rows[0].remove_btn.click()
    editor.apply_btn.click()
    assert len(st.subset.conditions) == 1
    editor.clear_btn.click()
    editor.apply_btn.click()
    assert st.subset == ss.Subset() and ("A01", 5) in _dots(page)
    assert page.widgets.get("_nw_subset_cb").property("filterActive") is False


def test_a_restored_subset_is_shown_in_the_editor(page):
    from review.features.nanowell.view_state_io import apply_nanowell

    st = page.state.feature_slice("nanowell")
    saved = NanowellState()
    saved.subset = ss.Subset((C("n", "ge", 2, at="tp:t_a"), C("ring", "le", 9)), "all").validated()
    apply_nanowell(page, saved.to_config())
    assert st.subset == saved.subset
    editor = page.widgets.get("_nw_subset_editor")
    assert [r.field_cb.currentData() for r in editor.rows] == ["n", "ring"]
    assert editor.rows[0].at_cb.currentData() == "tp:t_a" and editor.rows[0].a_spin.value() == 2
    assert _dots(page) == [("A01", 0), ("A01", 1), ("A01", 3), ("A01", 4), ("A01", 7), ("B01", 1)]


def test_an_imaging_field_condition_is_built_from_the_tiles_the_run_recorded(page):
    """The fixture's nanowells are all cut from tile F0: *is one of F0* keeps them all, *is not one of* none."""
    editor = _editor(page)
    editor.add_btn.click()
    row = editor.rows[0]
    row.field_cb.setCurrentIndex(row.field_cb.findData("owner"))
    assert row.tiles_btn.isVisibleTo(page.window) and not row.a_spin.isVisibleTo(page.window)
    st = page.state.feature_slice("nanowell")
    assert st.subset == ss.Subset() and not editor.apply_btn.isEnabled()   # no tile ticked yet: nothing to apply
    assert editor.status.objectName() == "Warn"
    tiles = [a for a in row.tiles_menu.actions() if a.isCheckable()]
    assert [a.text() for a in tiles] == ["F0"]
    tiles[0].setChecked(True)
    editor.apply_btn.click()
    assert st.subset == ss.Subset((C("owner", "in", values=("F0",), at="baseline"),))
    assert len(_dots(page)) == 10              # every nanowell: an occluded one still records the tile it was on
    row.op_cb.setCurrentIndex(row.op_cb.findData("not_in"))
    editor.apply_btn.click()
    assert any(t.startswith("No nanowell of the drawn wells is in the subset (Imaging field at baseline is not one "
                            "of F0)") for t in _texts(page))


def test_an_identity_verdict_condition_is_built_from_the_verdicts_not_the_tiles(page):
    """Choosing *Identity verdict* after *Imaging field* swaps the menu's tiles for the verdicts, in words; the ticked
    verdicts reach the page's subset as the identity check spells them. The fixture has no identity sidecar, so every
    timepoint is *not checked*: naming it keeps every nanowell, leaving it out keeps none."""
    editor = _editor(page)
    editor.add_btn.click()
    row = editor.rows[0]
    row.field_cb.setCurrentIndex(row.field_cb.findData("owner"))
    row.field_cb.setCurrentIndex(row.field_cb.findData("identity"))
    assert row.tiles_btn.isVisibleTo(page.window) and row.at_cb.isVisibleTo(page.window)
    verdicts = [a for a in row.tiles_menu.actions() if a.isCheckable()]
    assert [a.text() for a in verdicts] == ["confirmed", "reference", "partial", "inconclusive", "contradicted",
                                            "not checked"]
    verdicts[-1].setChecked(True)
    verdicts[0].setChecked(True)
    assert row.tiles_btn.text() == "confirmed, not checked"
    editor.apply_btn.click()
    st = page.state.feature_slice("nanowell")
    assert st.subset == ss.Subset((C("identity", "in", values=("confirmed", "not_checked"), at="baseline"),))
    assert len(_dots(page)) == 10
    row.op_cb.setCurrentIndex(row.op_cb.findData("not_in"))
    editor.apply_btn.click()
    assert any(t.startswith("No nanowell of the drawn wells is in the subset (Identity verdict at baseline is not one "
                            "of confirmed, not checked)") for t in _texts(page))


def test_a_rim_fitted_condition_is_offered_as_yes_or_no(page):
    editor = _editor(page)
    editor.add_btn.click()
    row = editor.rows[0]
    row.field_cb.setCurrentIndex(row.field_cb.findData("rim_fitted"))
    assert [row.op_cb.itemData(i) for i in range(row.op_cb.count())] == ["yes", "no"]
    assert not row.a_spin.isVisibleTo(page.window) and not row.tiles_btn.isVisibleTo(page.window)
    row.at_cb.setCurrentIndex(row.at_cb.findData("every"))
    editor.apply_btn.click()
    assert page.state.feature_slice("nanowell").subset == ss.Subset((C("rim_fitted", "yes", at="every"),))


def test_a_seam_condition_is_built_in_the_editor(page):
    """Choosing *On a tile seam* shows yes / no and no number; *no at every timepoint* reaches the page's subset."""
    editor = _editor(page)
    editor.add_btn.click()
    row = editor.rows[0]
    row.field_cb.setCurrentIndex(row.field_cb.findData("fused"))
    assert [row.op_cb.itemData(i) for i in range(row.op_cb.count())] == ["yes", "no"]
    assert not row.a_spin.isVisibleTo(page.window) and not row.tiles_btn.isVisibleTo(page.window)
    assert row.at_cb.isVisibleTo(page.window)
    row.op_cb.setCurrentIndex(row.op_cb.findData("no"))
    row.at_cb.setCurrentIndex(row.at_cb.findData("every"))
    editor.apply_btn.click()
    st = page.state.feature_slice("nanowell")
    assert st.subset == ss.Subset((C("fused", "no", at="every"),))


def test_a_per_timepoint_property_is_built_in_the_editor_and_the_figure_draws_it(page):
    """A nanowell property (``subset_compute.ROW_FLAGS``) is offered as yes / no at a timepoint, like the seam, and
    reaches the figure: *Occluded or clipped at every timepoint: no* leaves out A01 q6, occluded at t_c."""
    editor = _editor(page)
    editor.add_btn.click()
    row = editor.rows[0]
    row.field_cb.setCurrentIndex(row.field_cb.findData("occluded"))
    assert [row.op_cb.itemData(i) for i in range(row.op_cb.count())] == ["yes", "no"]
    assert not row.a_spin.isVisibleTo(page.window) and row.at_cb.isVisibleTo(page.window)
    row.op_cb.setCurrentIndex(row.op_cb.findData("no"))
    row.at_cb.setCurrentIndex(row.at_cb.findData("every"))
    editor.apply_btn.click()
    st = page.state.feature_slice("nanowell")
    assert st.subset == ss.Subset((C("occluded", "no", at="every"),))
    assert any(t.startswith("Subset: Occluded or clipped at every timepoint: no: 9 of 10 nanowells kept — 1 outside")
               for t in _texts(page))


# ── Apply and Reset ──────────────────────────────────────────────────────────
# Asked for: "add an apply/reset button so that it updates only then, not after every change". Every edit used to
# redraw every view at once, including for each half-typed bound.
def test_edits_redraw_nothing_until_apply(page, monkeypatch):
    redraws = []
    real = nwc.redraw
    monkeypatch.setattr(nwc, "redraw", lambda ctx, *a, **k: (redraws.append(1), real(ctx, *a, **k))[1])
    editor = _editor(page)
    before = _dots(page)
    editor.add_btn.click()
    row = editor.rows[0]
    row.a_spin.setValue(2)
    row.op_cb.setCurrentIndex(row.op_cb.findData("le"))
    assert redraws == [] and _dots(page) == before
    assert editor.status.text().startswith("Not applied yet — Starting cells ≤2")
    editor.apply_btn.click()
    assert len(redraws) == 1 and page.state.feature_slice("nanowell").subset == ss.Subset((C("n0", "le", 2),))


def test_reset_puts_back_the_subset_in_use(page):
    st = page.state.feature_slice("nanowell")
    nwc.set_subset(page, ss.Subset((C("n0", "ge", 2), C("ring", "le", 9))).validated())
    editor = _editor(page)
    editor.rows[0].a_spin.setValue(5)
    editor.rows[1].remove_btn.click()
    editor.add_btn.click()
    assert editor.reset_btn.isEnabled()
    editor.reset_btn.click()
    assert [r.condition().validated() for r in editor.rows] == list(st.subset.conditions)
    assert not editor.reset_btn.isEnabled() and not editor.apply_btn.isEnabled()


def test_a_draft_survives_another_control_redrawing_the_page(page):
    """The rows are rebuilt from the page only when the subset in use changed — not when an unrelated control
    refreshes the band, or edits not yet applied would vanish."""
    editor = _editor(page)
    editor.add_btn.click()
    editor.rows[0].a_spin.setValue(3)
    page.widgets.get("_nw_refresh_controls")()
    nwc.redraw(page)
    assert [r.condition().validated() for r in editor.rows] == [C("n0", "ge", 3).validated()] and editor.apply_btn.isEnabled()
    nwc.set_starting_cells(page, 1, 1)                          # a preset chosen in the box: the draft gives way
    assert [r.condition().validated() for r in editor.rows] == list(ss.Subset.starting(1, 1).conditions)


def test_reset_and_apply_sit_beside_add_condition_and_clear(page):  # noqa: F811
    """The editor's four buttons read as one group at the left of their row: Add condition, Clear, Reset, Apply, then
    the space — Reset and Apply were pushed to the far end, away from the edits they act on."""
    editor = _editor(page)
    outer = editor.layout()
    (row,) = [outer.itemAt(i).layout() for i in range(outer.count()) if outer.itemAt(i).layout() is not None
              and outer.itemAt(i).layout().indexOf(editor.add_btn) >= 0]
    items = [row.itemAt(j) for j in range(row.count())]
    assert [item.widget() for item in items[:4]] == [editor.add_btn, editor.clear_btn, editor.reset_btn,
                                                     editor.apply_btn]
    assert items[4].spacerItem() is not None and len(items) == 5


def test_restoring_a_saved_plot_leaves_the_saved_subset_library_as_it_is_now(page):
    """A Saved Plot restore brings back the Subset that drew the figure, but never the library of named subsets as
    it stood at save time: that replaced the library wholesale, deleting every subset saved after the plot and
    bringing back every one deleted since. The fixture differs on both counts — "Later" was saved after the plot,
    "Deleted" was in the plot's copy and is gone now — so either half of the old behaviour fails it."""
    from review.features.nanowell.view_state_io import apply_nanowell

    st = page.state.feature_slice("nanowell")
    drawn = ss.Subset((C("n", "ge", 2, at="tp:t_a"),)).validated()
    then = NanowellState()
    then.subset = drawn
    then.saved_subsets = {"Deleted": drawn}
    later = ss.Subset((C("ring", "le", 9),)).validated()
    st.saved_subsets = {"Later": later}

    apply_nanowell(page, then.to_config())

    assert st.saved_subsets == {"Later": later}
    assert st.subset == drawn                       # the figure's own population still comes back
    assert ss.saved_name_of(st.saved_subsets, st.subset) == ""     # shown as unsaved, which it now is
