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
        "step off. Click to see which, then the next one like it.")
    assert qc.hover_text(m.cells[("B01", "t_a")]) == (
        "B01 · t_a — not addressed: the last run could not address it from the anchor set here; set it again. Click "
        "to resolve it, then the next one like it.")
    assert qc.hover_text(m.cells[("C01", "t_c")]) == (
        "C01 · t_c — not located (no lattice): No lattice was found. An anchor set by hand cannot change this. "
        "Click to see it, then the next one like it.")
    assert qc.hover_text(m.cells[("D01", "t_a")]) == (
        "D01 · t_a — confirmed: its addresses name the same nanowells as at the reference timepoint. Click to open "
        "it on Identity.")
    assert qc.short_text(m.cells[("C01", "t_a")]) == "C01 · t_a · partial (3 left out)"
    assert qc.queue_tip(m) == ""
    none_waiting = qc.plate_qc(_records(grids={"D01": _records().grids["D01"]}), order=_by_time)
    assert qc.queue_tip(none_waiting, (qc.NOT_ADDRESSED,)) == "No timepoint is “not addressed”."


def test_each_state_is_drawn_with_its_own_texture_and_the_line_plots_hatches():
    from review.features.nanowell._qc_render import HATCH

    assert HATCH == {"not_addressed": "", "anchor_waiting": "----", "not_located": "||||", "contradicted": "xxxx",
                     "inconclusive": "////", "partial": "\\\\\\\\", "not_checked": "....", "reference": "",
                     "confirmed": "",
                     # Needing no look, so in the confirmed ink, keeping the texture of the check's own verdict.
                     "minor_partial": "\\\\\\\\", "addresses_agree": "////"}


# ── the page ─────────────────────────────────────────────────────────────────
def _write_sidecar(out, well, verdicts, excluded=()):
    from pipeline.nanowell_identity import IDENTITY_VERSION, inputs_digest, sidecar_path

    entries = {tp: {"verdict": v, "reason": ""} for tp, v in verdicts.items()}
    for tp, sites in excluded:
        entries[tp]["excluded"] = [[q, r, "landed on a neighbour"] for q, r in sites]
    path = sidecar_path(out, well)
    reference = next(tp for tp, v in verdicts.items() if v == "reference")
    path.write_text(json.dumps({"version": IDENTITY_VERSION, "well": well, "inputs": inputs_digest(out, well),
                                "reference": reference, "reference_reason": "", "timepoints": entries}))


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
    # The nanowell tables load on a worker of their own (``controller.ensure_loaded``), separately from the QC
    # records above, and the anchor queue refuses to build until they are in. On a slow runner the QC records
    # can land first, so the page is only ready once both have.
    qtbot.waitUntil(lambda: st.data is not None and not st.computing, timeout=20000)
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
    assert queue is not None, _why
    # One class at a time: Problem's default is the worst class on the plate, here the two not addressed, in plate
    # then time order — and not the partial one beside them.
    assert [(i.well, i.timepoint, i.state) for i in queue.all_items] == [
        ("A01", "t_a", "not_addressed"), ("B01", "t_c", "not_addressed")]
    assert len(queue.all_items) == qc.queue_size(st.qc_matrix, False, ("not_addressed",))
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
    # What it did, and the next class on the plate to resolve, since nothing was anchored to re-run.
    assert st.qc_message == ("Resolve finished: 0 of 2 not addressed timepoint(s) anchored, 2 skipped. Next: choose "
                             "partial (1) under Problem and press Resolve.")
    assert not ctx.state.selection.selected_wells                  # the selection it started with


def test_a_plate_that_cannot_be_read_says_why_on_the_figure_and_is_not_read_again(qc_page, qtbot, monkeypatch):
    from review.features.nanowell import controller as nwc

    ctx, st, out = qc_page
    calls = []

    def broken(*_args):
        calls.append(1)
        raise OSError("disk unplugged")

    monkeypatch.setattr(qc, "read_plate", broken)
    import os
    os.utime(out / "nanowells" / "A01_grid.json", ns=(1, 1))
    nwc.redraw(ctx)
    qtbot.waitUntil(lambda: bool(st.qc_error), timeout=10000)
    texts = [t.get_text() for ax in ctx.widgets.get("_nw_fig").axes for t in ax.texts]
    assert texts == ["The plate's grid records and identity checks could not be read: disk unplugged"]
    nwc.redraw(ctx)
    nwc.redraw(ctx)
    assert len(calls) == 1


def test_the_queue_button_says_what_it_walks_and_one_per_well_visibly_changes_it(qc_page):
    """Reported: "I click on one per well, and expected to be given a stitched well in which to identify the
    fiducial … But clicking on one per well does nothing". It was a toggle that changed only what a separate, plain
    button would later walk. It is now a checkbox, and the walk's button — the page's primary action — says what
    pressing it walks, so the option visibly changes something; the figure says what to press."""
    from PySide6.QtWidgets import QCheckBox

    ctx, st, _out = qc_page
    queue_btn, per_well = ctx.widgets.get("_nw_queue_btn"), ctx.widgets.get("_nw_qc_per_well_btn")
    assert isinstance(per_well, QCheckBox) and queue_btn.property("variant") == "primary"
    # A01 t_a and B01 t_c are not addressed; B01 also has an anchor waiting, so one per well leaves B01 out.
    assert queue_btn.text() == "Resolve: 2 timepoints"
    per_well.click()
    assert st.anchor_queue_per_well and queue_btn.text() == "Resolve: 1 well"
    per_well.click()
    assert queue_btn.text() == "Resolve: 2 timepoints"
    fig = ctx.widgets.get("_nw_fig")
    assert [t.get_text() for t in fig.texts if t.get_gid() == "nw-qc-message"] == [
        "Press Resolve to step through the 2 not addressed timepoints, each opened where it is fixed with what to do, "
        "or click any cell to start at it. Problem chooses the class."]


def test_a_plate_with_nothing_unaddressed_still_has_a_queue_of_what_needs_a_look():
    """Reported: "Says 336 of 732 need a look, but anchor queue says 0" — not located 23, inconclusive 68, partial
    239. The queue walked only timepoints the run could not address, and this plate had none. It walks the class
    chosen under *Problem*, and until one is chosen the worst class with anything on the plate, so it is never empty
    while something needs a look."""
    ok = {"status": "ok"}
    records = _records(grids={"A01": {"t_c": ok, "t_a": ok}, "B01": {"t_c": ok, "t_a": ok},
                              "C01": {"t_c": {"status": "no_lattice", "reason": "No lattice was found"}, "t_a": ok}},
                       verdicts={"A01": {"t_c": ("reference", ""), "t_a": ("inconclusive", "no central square")},
                                 "B01": {"t_c": ("reference", ""), "t_a": ("partial", "")},
                                 "C01": {"t_c": ("inconclusive", ""), "t_a": ("partial", "")}},
                       anchors={}, excluded={})
    from review.features.nanowell import resolve_compute as rc

    m = qc.plate_qc(records, order=_by_time)
    assert m.counts.get(qc.NOT_ADDRESSED, 0) == 0 and m.needing_count() == 4
    # Problem's default is the worst class with anything on the plate: here inconclusive, not the empty not addressed.
    assert rc.walk_states(m, qc.WALK_DEFAULT) == (qc.INCONCLUSIVE,)
    assert [(i.well, i.timepoint, i.state) for i in qc.queue_items(m, (qc.PARTIAL,))] == [
        ("B01", "t_a", "partial"), ("C01", "t_a", "partial")]
    assert qc.queue_label(m, False, (qc.INCONCLUSIVE,)) == "Resolve: 1 timepoint"
    assert qc.queue_label(m, False, (qc.PARTIAL,)) == "Resolve: 2 timepoints"
    assert qc.queue_label(m, False, (qc.NOT_LOCATED,)) == "Resolve: 1 timepoint"
    assert qc.queue_label(m, True, (qc.PARTIAL,)) == "Resolve: 2 wells"


def test_problem_chooses_one_class_and_each_opens_where_it_is_fixed(qc_page, qtbot):
    """Requested: "If there are multiple problem classes, I should be able to select what class I want to resolve,
    and then have it show me the nanowell region, tell me what I need to do so I can click, and then take me to the
    next problem in that class." *Problem* is one class at a time; an anchor's class opens on Array with Set anchor
    on, a partial one on Identity centred on the nanowells it leaves out, and the guide card says what to do."""
    from review.features.nanowell import controller as nwc

    ctx, st, out = qc_page
    _write_sidecar(out, "A01", {"t_c": "inconclusive", "t_a": "reference"})
    nwc.redraw(ctx)                                                   # a changed file: the plate is read again
    qtbot.waitUntil(lambda: st.qc_reading is None and st.qc_records is not None
                    and (st.qc_matrix is None or st.qc_matrix.counts.get("inconclusive")), timeout=20000)
    nwc._call(ctx, "_nw_refresh_controls")
    queue_btn, problem = ctx.widgets.get("_nw_queue_btn"), ctx.widgets.get("_nw_qc_walk_btn")
    assert (problem.text(), queue_btn.text()) == ("Problem: not addressed", "Resolve: 2 timepoints")
    walk_partial = ctx.widgets.get("_nw_qc_walk_partial")
    assert walk_partial.text() == "partial — some nanowells left out (1)"
    ctx.widgets.get("_nw_qc_walk_inconclusive").trigger()
    assert (problem.text(), queue_btn.text()) == ("Problem: inconclusive", "Resolve: 1 timepoint")
    assert st.anchor_queue_states == ("inconclusive",) and not walk_partial.isChecked()
    window = ctx.widgets.get("_nw_canvas").window()

    def opened(view):
        qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.view == view, timeout=20000)
        return ctx.widgets.get("_nw_queue_lbl").text(), st.anchor_mode

    queue_btn.click()
    assert opened("array") == ("1 of 1 · A01 at t_c · inconclusive", True)      # an anchor can fix it: armed
    assert ctx.widgets.get("_nw_resolve_guide").isVisibleTo(window)
    assert ctx.widgets.get("_nw_resolve_kicker").text() == "Resolving: inconclusive · 1 of 1"
    assert ctx.widgets.get("_nw_resolve_action").text().startswith("Click the middle of the true central square.")
    ctx.widgets.get("_nw_queue_stop_btn").click()
    assert not ctx.widgets.get("_nw_resolve_guide").isVisibleTo(window)

    walk_partial.trigger()
    assert queue_btn.text() == "Resolve: 1 timepoint"
    queue_btn.click()
    assert opened("identity") == ("1 of 1 · A02 at t_a · partial", False)       # to look at: nothing to set
    assert st.well == "A02" and st.timepoint == "t_a" and st.identity_mode == "distance"
    assert st.identity_focus in ("0,0", "1,0")                                  # centred on the two left out
    assert ctx.widgets.get("_nw_queue_lbl").isVisibleTo(window)                  # Back, Skip, Stop on Identity too
    action = ctx.widgets.get("_nw_resolve_action_btn")
    assert action.isVisibleTo(window) and action.text() == "Looks right — next"
    assert ctx.widgets.get("_nw_resolve_problem").text().startswith("2 nanowells at this timepoint did not land")
    action.click()                                                    # the last partial: back on QC, saying so
    assert st.anchor_queue is None and st.view == "qc" and st.identity_focus == ""
    assert st.qc_message == ("Resolve finished: looked at 1 of 1 partial timepoint(s). Next: choose not addressed "
                             "(2) under Problem and press Resolve.")


# ── what needs no look though the check did not say confirmed ───────────────
#: ``nanowell_identity.compare``'s wording for an inconclusive timepoint doubted only by the array's fitted centre.
CENTRE_ONLY = ("every address lands on itself, but the array's fitted centre lands 1.30 pitch from this timepoint's "
               "once registered on the square: the square may have moved against the array (a decoy), or a fitted "
               "rim is loose")


def _eased_records():
    ok = {"status": "ok"}
    grids = {"A01": {tp: dict(ok) for tp in ("t0", "t1", "t2", "t3", "t4", "t5")}}
    verdicts = {"A01": {"t0": ("reference", ""),
                        "t1": ("partial", "5 of 100 nanowells left out"),        # exactly 5 %: minor
                        "t2": ("partial", "6 of 100 nanowells left out"),        # just over: still needs a look
                        "t3": ("partial", "1 of ? nanowells left out"),          # compared unknown: not eased
                        "t4": ("inconclusive", CENTRE_ONLY),
                        "t5": ("inconclusive", "the square's best match (0.40) leads the next place more than half a "
                                               "pitch away by only 0.05, under 0.1")}}
    left = lambda n: frozenset((0, i) for i in range(n))                         # noqa: E731
    excluded = {"A01": {"t1": left(5), "t2": left(6), "t3": left(1)}}
    compared = {"A01": {"t1": 100, "t2": 100}}
    return qc.PlateRecords(grids=grids, verdicts=verdicts, excluded=excluded, anchors={}, checked_any=True,
                           compared=compared)


def test_a_minor_partial_and_an_outline_only_doubt_need_no_look_and_say_so():
    m = qc.plate_qc(_eased_records(), order=sorted)
    assert {tp: m.cells[("A01", tp)].state for tp in ("t1", "t2", "t3", "t4", "t5")} == {
        "t1": qc.MINOR_PARTIAL, "t2": qc.PARTIAL, "t3": qc.PARTIAL, "t4": qc.ADDRESSES_AGREE, "t5": qc.INCONCLUSIVE}
    assert m.needing_count() == 3
    assert qc.summary(m) == "3 of 6 timepoints need a look, in 1 of 1 wells."
    assert ("Not counted as needing a look: 1 partial timepoint(s) leave out at most 5% of their nanowells; 1 "
            "inconclusive one(s) have every address landing on itself and only the array's outline in doubt.") \
        in qc.notes(m, "needing", None)
    assert m.cells[("A01", "t1")].left_out == 5
    assert qc.hover_text(m.cells[("A01", "t4")]).startswith(
        "A01 · t4 — addresses agree: every address lands on itself at the reference")
    # Neither is walked: an anchor has nothing to do at either.
    assert [i.timepoint for i in qc.queue_items(m, (qc.INCONCLUSIVE,))] == ["t5"]
    assert [i.timepoint for i in qc.queue_items(m, (qc.PARTIAL,))] == ["t2", "t3"]


def test_a_plate_with_only_eased_timepoints_says_nothing_needs_a_look():
    records = _eased_records()
    keep = {"t0", "t1", "t4"}
    records = qc.PlateRecords(grids={"A01": {tp: b for tp, b in records.grids["A01"].items() if tp in keep}},
                              verdicts=records.verdicts, excluded=records.excluded, anchors={}, checked_any=True,
                              compared=records.compared)
    m = qc.plate_qc(records, order=sorted)
    assert qc.summary(m) == ("Nothing needs a look: all 3 timepoints of 1 wells are addressed, 2 of them a minor "
                             "partial or with only the array's outline in doubt.")


def test_the_count_compared_is_read_from_the_well_s_sidecar(tmp_path):
    from pipeline.nanowell_identity import IDENTITY_VERSION, sidecar_path

    (tmp_path / "nanowells").mkdir()
    sidecar_path(tmp_path, "A01").write_text(json.dumps({"version": IDENTITY_VERSION, "well": "A01", "timepoints": {
        "t0": {"verdict": "reference"}, "t1": {"verdict": "partial", "compared": 140}, "t2": {"compared": "x"}}}))
    assert qc._compared(tmp_path, "A01") == {"t1": 140}
    assert qc._compared(tmp_path, "B01") == {}


def test_the_plate_s_files_are_never_checked_on_the_gui_thread(qc_page, qtbot, monkeypatch):
    """Reported: the window stopped responding for 30 to 100 s at a time, and every faulthandler dump of the GUI
    thread was inside ``pathlib.stat`` under ``qc_compute.signature``, called from the QC view's control refresh and
    draw. The signature is about three ``stat`` calls a well; on a slow or synced disk that is the freeze. Every
    check must run on a worker, and the page's refreshes and draws must still pick up a file that changed."""
    import threading

    from review.features.nanowell import controller as nwc
    from review.features.nanowell import qc_compute

    ctx, st, out = qc_page
    real, threads = qc_compute.signature, []

    def spy(*args, **kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(qc_compute, "signature", spy)
    for _ in range(3):
        nwc._call(ctx, "_nw_refresh_controls")
        nwc.redraw(ctx)
    qtbot.waitUntil(lambda: st.qc_reading is None, timeout=20000)
    assert threads and not any(threads), threads
    # A file written while the page is open is found by the next refresh, without one on the GUI thread.
    _write_sidecar(out, "A01", {"t_c": "inconclusive", "t_a": "reference"})
    nwc.redraw(ctx)
    qtbot.waitUntil(lambda: st.qc_reading is None and st.qc_matrix is not None
                    and st.qc_matrix.counts.get("inconclusive"), timeout=20000)
    assert not any(threads)


def test_a_check_asked_for_while_one_runs_runs_when_it_lands(qc_page, qtbot, monkeypatch):
    """One check at a time: a refresh during a check is remembered, so a file changed after the check began is not
    missed until some later, unrelated refresh."""
    import time

    from review.features.nanowell import controller as nwc
    from review.features.nanowell import qc_compute

    ctx, st, out = qc_page
    qtbot.waitUntil(lambda: st.qc_reading is None and st.qc_records is not None, timeout=20000)
    real = qc_compute.signature

    def slow(*args, **kwargs):
        sig = real(*args, **kwargs)
        time.sleep(0.4)                                   # the files change while this check is in flight
        return sig

    monkeypatch.setattr(qc_compute, "signature", slow)
    nwc.redraw(ctx)                                       # starts a check
    assert st.qc_reading is not None
    _write_sidecar(out, "A01", {"t_c": "inconclusive", "t_a": "reference"})
    nwc.redraw(ctx)                                       # asked for during it: remembered, not dropped
    assert st.qc_recheck
    qtbot.waitUntil(lambda: st.qc_reading is None and not st.qc_recheck and st.qc_matrix is not None
                    and st.qc_matrix.counts.get("inconclusive"), timeout=20000)
