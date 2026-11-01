"""The Nanowells page's QC view: every well and timepoint of the plate at once, each with the one state that says what
needs doing, and the *Anchor queue* that starts from it.

Requested: "For nanowells, is there an at-a-glance way to check which wells/timepoints are considered
partial/inconclusive. Perhaps we need a QC tab on nanowells to hold this, as well as the re-anchoring queueing tool
currently on array" — and then "Move the anchor queue off the array completely into the qc panel".

The first half pins the state each cell takes, on records built so the wrong precedence gives a different answer: an
unaddressed timepoint whose identity verdict is *inconclusive* (an unaddressed timepoint has nothing to confirm),
an anchor waiting beside one refused, a status an anchor cannot change. The second half drives the real page — the
view bar, a real click on a cell, the band's buttons — with **no well selected**, because the matrix is about the
plate, not the selection.
"""

from __future__ import annotations

import json

import pytest

from review.features.nanowell import qc_compute as qc
from util.nanowell_anchors import Anchor

REASON = "No accepted fiducial, array centre, or plate-rigid prediction"
TIME_ORDER = {"t_c": 0, "t_a": 1}


def _by_time(tps):
    return sorted(tps, key=TIME_ORDER.__getitem__)


def _records(**overrides):
    grids = {
        # Unaddressed, and the identity check called it inconclusive (it had no addresses): the grid status wins.
        "A01": {"t_c": {"status": "ok"}, "t_a": {"status": "unaddressed", "reason": REASON}},
        # An anchor set and not yet used, and an anchor the last run refused.
        "B01": {"t_c": {"status": "unaddressed", "reason": REASON},
                "t_a": {"status": "unaddressed", "reason": "refused", "manual_anchor_um": [1.0, 2.0]}},
        # A status an anchor cannot change, beside a partial timepoint.
        "C01": {"t_c": {"status": "no_lattice", "reason": "No lattice was found"}, "t_a": {"status": "ok"}},
        "D01": {"t_c": {"status": "ok"}, "t_a": {"status": "ok"}},
    }
    verdicts = {"A01": {"t_c": ("reference", ""), "t_a": ("inconclusive", "the timepoint had no addresses")},
                "B01": {"t_c": ("inconclusive", "no addresses"), "t_a": ("inconclusive", "no addresses")},
                "C01": {"t_c": ("inconclusive", ""), "t_a": ("partial", "tile F3 a step off")},
                "D01": {"t_c": ("reference", ""), "t_a": ("confirmed", "")}}
    anchors = {("B01", "t_c"): Anchor("B01", "t_c", 9.0, 9.0),        # set, never used: waits for a re-run
               ("B01", "t_a"): Anchor("B01", "t_a", 1.0, 2.0)}        # the one the last run refused
    excluded = {"C01": {"t_a": frozenset({(0, 1), (1, 1), (2, 1)})}}
    fields = dict(grids=grids, verdicts=verdicts, excluded=excluded, anchors=anchors, checked_any=True)
    fields.update(overrides)
    return qc.PlateRecords(**fields)


def test_each_cell_takes_the_grid_status_first_then_the_identity_verdict():
    m = qc.plate_qc(_records(), order=_by_time)
    states = {key: cell.state for key, cell in m.cells.items()}
    assert states == {
        ("A01", "t_c"): "reference", ("A01", "t_a"): "not_addressed",
        ("B01", "t_c"): "anchor_waiting", ("B01", "t_a"): "not_addressed",
        ("C01", "t_c"): "not_located", ("C01", "t_a"): "partial",
        ("D01", "t_c"): "reference", ("D01", "t_a"): "confirmed",
    }
    assert m.cells[("B01", "t_a")].refused and not m.cells[("A01", "t_a")].refused
    assert m.cells[("C01", "t_a")].left_out == 3
    assert m.cells[("C01", "t_c")].grid_status == "no_lattice"
    assert m.timepoints == ["t_c", "t_a"]                             # time order, not the labels' order
    assert m.needing() == ["A01", "B01", "C01"]
    assert m.shown_wells("all") == ["A01", "B01", "C01", "D01"]
    assert m.counts == {"reference": 2, "not_addressed": 2, "anchor_waiting": 1, "not_located": 1, "partial": 1,
                        "confirmed": 1}
    assert qc.summary(m) == "5 of 8 timepoints need a look, in 3 of 4 wells."


def test_not_addressed_counts_exactly_what_the_anchor_queue_walks():
    from review.features.nanowell import anchor_queue_compute as aqc

    records = _records()
    items, pending = aqc.waiting_items(records.grids, records.anchors, order=_by_time)
    m = qc.plate_qc(records, order=_by_time)
    assert {(i.well, i.timepoint) for i in items} == {k for k, c in m.cells.items() if c.state == qc.NOT_ADDRESSED}
    assert set(pending) == {k for k, c in m.cells.items() if c.state == qc.ANCHOR_WAITING}


def test_an_unreadable_anchors_file_leaves_every_unaddressed_timepoint_waiting_and_says_so():
    m = qc.plate_qc(_records(anchors={}, anchors_problem="line 1: not JSON"), order=_by_time)
    assert m.cells[("B01", "t_c")].state == qc.NOT_ADDRESSED
    assert any(n.startswith("The anchors file cannot be used: line 1: not JSON.") for n in qc.notes(m, "needing", "."))


def test_the_sentences_say_what_a_click_does():
    m = qc.plate_qc(_records(), order=_by_time)
    assert qc.hover_text(m.cells[("C01", "t_a")], 5.0) == (
        "C01 · t_a (5 h) — partial: 3 nanowell(s) left out of comparisons over time; the rest are used. tile F3 a "
        "step off. Click to open it on Identity.")
    assert qc.hover_text(m.cells[("B01", "t_a")]) == (
        "B01 · t_a — not addressed: the last run could not address it from the anchor set here. Click to set it "
        "again on Array.")
    assert qc.hover_text(m.cells[("C01", "t_c")]) == (
        "C01 · t_c — not located (no lattice): No lattice was found. An anchor set by hand cannot change this. "
        "Click to see it on Array.")
    assert qc.short_text(m.cells[("C01", "t_a")]) == "C01 · t_a · partial (3 left out)"
    assert qc.queue_tip(m) == ""
    none_waiting = qc.plate_qc(_records(grids={"D01": _records().grids["D01"]}), order=_by_time)
    assert qc.queue_tip(none_waiting) == "No timepoint is waiting for an anchor."


def test_each_state_is_drawn_with_its_own_texture_and_the_line_plots_hatches():
    from review.features.nanowell._qc_render import HATCH

    assert HATCH == {"not_addressed": "", "anchor_waiting": "----", "not_located": "||||", "contradicted": "xxxx",
                     "inconclusive": "////", "partial": "\\\\\\\\", "not_checked": "....", "reference": "",
                     "confirmed": ""}


# ── the page ─────────────────────────────────────────────────────────────────
def _write_sidecar(out, well, verdicts, excluded=()):
    from pipeline.nanowell_identity import IDENTITY_VERSION, inputs_digest, sidecar_path

    entries = {tp: {"verdict": v, "reason": ""} for tp, v in verdicts.items()}
    for tp, sites in excluded:
        entries[tp]["excluded"] = [[q, r, "landed on a neighbour"] for q, r in sites]
    path = sidecar_path(out, well)
    path.write_text(json.dumps({"version": IDENTITY_VERSION, "well": well, "inputs": inputs_digest(out, well),
                                "timepoints": entries}))


@pytest.fixture
def qc_page(qtbot, tmp_path):
    """A01 t_a and B01 t_c not addressed, B01 t_a with an anchor waiting for a re-run, A02 checked (its t_a partial),
    A01 t_c never checked — and nothing selected on the plate."""
    from review.features.nanowell import controller as nwc
    from tests.test_an_unaddressed_nanowell_timepoint_offers_its_anchor import _Rail
    from tests.test_nanowell_anchor_queue import _dataset_with_unaddressed
    from tests.test_nanowell_page import _page
    from util.nanowell_anchors import anchors_path, write_anchors

    out = _dataset_with_unaddressed(tmp_path)
    _write_sidecar(out, "A02", {"t_c": "reference", "t_a": "partial"}, excluded=[("t_a", [(0, 0), (1, 0)])])
    write_anchors(anchors_path(out.parent / "in"), {("B01", "t_a"): Anchor("B01", "t_a", 50.0, 60.0)})
    ctx = _page(qtbot, out)
    ctx.window._notebook = _Rail(ctx)
    ctx.state.selection.selected_wells = set()
    st = ctx.state.feature_slice("nanowell")
    nwc.nw_on_activate(ctx)
    bar = ctx.widgets.get("_nw_view_bar")
    bar.setCurrentIndex([bar.tabData(i) for i in range(bar.count())].index("qc"))     # as a click does
    qtbot.waitUntil(lambda: st.qc_records is not None and st.qc_drawn is not None and bool(st.qc_drawn.cells),
                    timeout=20000)
    return ctx, st, out


def _cell_point(ctx, st, well, tp):
    from PySide6.QtCore import QPoint

    drawn = st.qc_drawn
    top = drawn.row_y[drawn.wells.index(well)]
    px, py = drawn.ax.transData.transform((drawn.timepoints.index(tp) + 0.5, top + 0.5))
    canvas = ctx.widgets.get("_nw_canvas")
    ratio = canvas.devicePixelRatioF()
    return canvas, QPoint(int(round(px / ratio)), int(round(canvas.height() - py / ratio)))


def _click(ctx, st, well, tp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    canvas, at = _cell_point(ctx, st, well, tp)
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, at)


def test_the_qc_view_draws_the_whole_plate_with_nothing_selected(qc_page):
    ctx, st, _out = qc_page
    assert not ctx.state.selection.selected_wells
    drawn = st.qc_drawn
    states = {key: cell.state for key, cell in drawn.cells.items()}
    assert states == {("A01", "t_c"): "not_checked", ("A01", "t_a"): "not_addressed",
                      ("A02", "t_c"): "reference", ("A02", "t_a"): "partial",
                      ("B01", "t_c"): "not_addressed", ("B01", "t_a"): "anchor_waiting"}
    assert drawn.cells[("A02", "t_a")].left_out == 2
    assert drawn.timepoints == ["t_c", "t_a"]
    # Every well needs a look here (A02's partial), so every well has a row; the figure says how many and which.
    assert drawn.wells == ["A01", "A02", "B01"]
    fig = ctx.widgets.get("_nw_fig")
    assert [t.get_text() for t in fig.texts if t.get_gid() == "nw-qc-summary"] == [
        "5 of 6 timepoints need a look, in 3 of 3 wells."]
    hatched = {c.aw_state: c.get_hatch() for c in drawn.ax.collections if c.get_gid().endswith("-hatch")}
    assert hatched == {"anchor_waiting": "----", "partial": "\\\\\\\\", "not_checked": "...."}
    # The readout names the cell under the pointer.
    assert drawn.ax._aw_hover_label(1.5, drawn.row_y[1] + 0.5) == "A02 · t_a · partial (2 left out)"
    assert drawn.ax._aw_hover_label(2.5, drawn.row_y[1] + 0.5) is None          # right of the last column


def test_the_anchor_queue_is_on_the_qc_view_only_and_walks_what_the_matrix_counts(qc_page):
    from review.features.nanowell import _anchor_queue as aq
    from review.features.nanowell import controller as nwc

    ctx, st, _out = qc_page
    window = ctx.widgets.get("_nw_canvas").window()
    queue_btn = ctx.widgets.get("_nw_queue_btn")
    assert queue_btn.isVisibleTo(window) and queue_btn.isEnabled()
    queue, _why = aq.build_queue(ctx)
    assert len(queue.all_items) == st.qc_matrix.counts["not_addressed"] == 2
    nwc.set_view(ctx, "array")
    assert not queue_btn.isVisibleTo(window)                       # off the Array view completely
    assert ctx.widgets.get("_nw_anchor_btn").isVisibleTo(window)   # Set anchor stays where anchors are set


def test_a_click_on_a_cell_opens_it_where_it_is_dealt_with(qc_page, qtbot):
    ctx, st, _out = qc_page
    _click(ctx, st, "A02", "t_a")                                  # partial: the identity check, on Identity
    assert (st.view, st.well, st.timepoint) == ("identity", "A02", "t_a")
    assert "A02" in ctx.state.selection.selected_wells

    bar = ctx.widgets.get("_nw_view_bar")
    bar.setCurrentIndex([bar.tabData(i) for i in range(bar.count())].index("qc"))
    qtbot.waitUntil(lambda: st.qc_drawn is not None and bool(st.qc_drawn.cells), timeout=20000)
    _click(ctx, st, "B01", "t_c")                                  # not addressed: Array, Set anchor on
    qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.anchor_mode, timeout=20000)
    assert (st.view, st.well, st.timepoint) == ("array", "B01", "t_c")


def test_leaving_the_array_view_mid_walk_ends_the_walk_there(qc_page, qtbot):
    ctx, st, _out = qc_page
    ctx.widgets.get("_nw_queue_btn").click()
    qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.anchor_mode, timeout=20000)
    assert st.view == "array" and st.anchor_queue is not None
    assert ctx.widgets.get("_nw_queue_lbl").isVisibleTo(ctx.widgets.get("_nw_canvas").window())
    bar = ctx.widgets.get("_nw_view_bar")
    bar.setCurrentIndex([bar.tabData(i) for i in range(bar.count())].index("trajectories"))
    assert st.anchor_queue is None and not st.anchor_mode
    assert st.view == "trajectories"                               # the view chosen, not dragged back to QC
    assert st.qc_message == ("Anchor queue finished: 0 of 2 timepoint(s) anchored, 2 skipped. Re-run anchored "
                             "wells… to address them from the anchors.")
    assert not ctx.state.selection.selected_wells                  # the selection it started with
