"""The QC view's Resolve walk: a click on a problem takes you to it, says what is wrong, what to click and where, and
what happens next, and goes on to the next problem of the same class.

Requested: "Clicking on partial/inconclusive/missing well/timepoint on the grid should take me to a view of the
specific problem, tell me exactly what is needed and where so I can click on it, and then tell me what needs to happen
next. If there are multiple problem classes, I should be able to select what class I want to resolve, and then have it
show me the nanowell region, tell me what I need to do so I can click, and then take me to the next problem in that
class."

The first half pins the Qt-free rules on records built so a wrong rule answers differently: where the square is looked
for (each source at a different point), which class *Problem* picks, the card's sentences, and what the QC view says
when a walk ends. The second half drives the real page with nothing selected: a real click on a cell, the ring on the
montage and the view centred on it, the guide card, and the card's own button.
"""

from __future__ import annotations

import json

import pytest

from review.features.nanowell import qc_compute as qc
from review.features.nanowell import resolve_compute as rc
from review.features.nanowell.anchor_queue_compute import QueueItem

REFERENCE_SQUARE, RUN_SQUARE, RIM_CENTRE = (101.0, 202.0), (55.0, 66.0), (77.0, 88.0)


def _block(fiducial=True, boundary=True, method=None):
    block = {"status": "ok", "origin_method": method}
    if fiducial:
        block["fiducial"] = {"position_um": list(RUN_SQUARE)}
    if boundary:
        block["array_boundary"] = {"centre_um": list(RIM_CENTRE), "radius_um": 700.0}
    return block


def _entry(ok=True):
    return {"verdict": "contradicted", "reference": "0h", "square": {"ok": ok, "found_um": list(REFERENCE_SQUARE)}}


# ── where the fix is made ────────────────────────────────────────────────────
def test_the_hint_prefers_the_reference_square_then_the_run_s_then_the_rim_centre():
    hint = rc.hint_for(qc.CONTRADICTED, _block(), _entry())
    assert (hint.point_um, hint.source) == (REFERENCE_SQUARE, "reference")
    assert hint.words == "the ring is where 0h's central square matches this montage"
    # The registration was not trusted: its peak is no evidence of where the square is.
    hint = rc.hint_for(qc.INCONCLUSIVE, _block(), _entry(ok=False))
    assert (hint.point_um, hint.source) == (RUN_SQUARE, "run")
    assert hint.words == "the ring is the run's best guess, which it did not trust"
    assert rc.hint_for(qc.CONTRADICTED, _block(method="fiducial"), None).words == "the ring is the square the run used"
    hint = rc.hint_for(qc.NOT_ADDRESSED, _block(fiducial=False), None)
    assert (hint.point_um, hint.source) == (RIM_CENTRE, "centre")
    assert rc.hint_for(qc.NOT_ADDRESSED, _block(fiducial=False, boundary=False), None) == rc.Hint()
    # A partial timepoint registered cleanly, and an anchor waiting is already set: no square to point at.
    assert rc.hint_for(qc.PARTIAL, _block(), _entry()) == rc.Hint()
    assert rc.hint_for(qc.ANCHOR_WAITING, _block(), _entry()) == rc.Hint()


def test_a_partial_timepoint_is_centred_on_the_middle_of_what_it_leaves_out():
    tile = {(4, -4), (4, -3), (4, -2), (5, -3), (5, -2), (3, -3)}
    assert rc.focus_address(tile) == "4,-3"                       # the middle, not the first in address order
    assert rc.focus_address(()) == ""


# ── which class, and what the card says ──────────────────────────────────────
def _matrix(counts):
    return qc.QcMatrix(wells=[], timepoints=[], cells={}, counts=counts)


def test_problem_picks_one_class_and_until_chosen_the_worst_on_the_plate():
    m = _matrix({qc.PARTIAL: 5, qc.INCONCLUSIVE: 2, qc.CONFIRMED: 9})
    assert rc.chosen(m, qc.WALK_DEFAULT) == qc.INCONCLUSIVE        # not addressed and contradicted are empty here
    assert rc.chosen(m, (qc.PARTIAL,)) == qc.PARTIAL               # a choice is kept even when another is worse
    assert rc.choices(m) == [(qc.NOT_ADDRESSED, 0), (qc.CONTRADICTED, 0), (qc.INCONCLUSIVE, 2), (qc.PARTIAL, 5),
                             (qc.NOT_LOCATED, 0)]


def test_the_card_says_what_is_wrong_what_to_click_and_where_and_what_happens_next():
    item = QueueItem("B04", "24h", "No accepted fiducial", False, qc.NOT_ADDRESSED)
    hint = rc.hint_for(qc.NOT_ADDRESSED, _block(), None)
    guide = rc.guide_for(item, hint, index=2, total=5, armed=True, hours=rc.hours_suffix("24h", 24.0))
    assert (guide.kicker, guide.title, guide.button) == ("Resolving: not addressed · 3 of 5", "B04 at 24h", "")
    assert guide.problem == ("The run could not find this well's central square (No accepted fiducial), so no "
                             "nanowell here has an address and the timepoint is left out of every comparison over "
                             "time.")
    assert guide.action == ("Click the middle of the central square — the one square among the round nanowells. "
                            "The ring is the run's best guess, which it did not trust.")
    assert guide.then.startswith("The anchor is saved and the next one opens by itself. 2 more not addressed "
                                 "timepoints after this one. When the class is done, press Re-run anchored wells…")
    refused = rc.guide_for(QueueItem("B04", "24h", "", True, qc.NOT_ADDRESSED), rc.Hint(), index=4, total=5,
                           armed=True)
    assert refused.problem.startswith("The last run tried the anchor you set here and could not use it")
    assert "This is the last one" in refused.then
    partial = rc.guide_for(QueueItem("A01", "24h", "7 of 73 nanowells left out (misregistered_tile)", False,
                                     qc.PARTIAL), rc.Hint(), index=0, total=1, armed=False, left_out=7)
    assert partial.button == "next" and partial.action.startswith("Nothing to click.")
    assert partial.problem == ("7 nanowells at this timepoint did not land on themselves at the reference (in a tile "
                               "placed a lattice step off), so they are left out of comparisons over time. The rest "
                               "are used.")
    assert partial.then.startswith("No re-run is needed")
    located = rc.guide_for(QueueItem("C01", "0h", "No lattice was found", False, qc.NOT_LOCATED), rc.Hint(),
                           index=0, total=1, armed=False)
    assert located.button == "exclude" and "An anchor cannot change that" in located.problem
    waiting = rc.guide_for(QueueItem("B01", "0h", "", False, qc.ANCHOR_WAITING), rc.Hint(), index=0, total=1,
                           armed=False)
    assert waiting.button == "rerun"


def test_the_hours_are_said_once():
    assert rc.hours_suffix("24h", 24.0) == ""
    assert rc.hours_suffix("t_a", 5.0) == "(5 h)"
    assert rc.hours_suffix("T03", 48.0) == "(48 h)"
    assert rc.hours_suffix("t_a", None) == ""


def test_the_end_of_a_walk_names_the_next_step():
    m = _matrix({qc.NOT_ADDRESSED: 1, qc.PARTIAL: 3})
    assert rc.finished(qc.NOT_ADDRESSED, anchored=2, walked=3, skipped=1, matrix=m) == (
        "Resolve finished: 2 of 3 not addressed timepoint(s) anchored, 1 skipped. Next: press Re-run anchored "
        "wells… so the run addresses them from your anchors.")
    # Nothing anchored: the next class on the plate, never the one just walked.
    assert rc.finished(qc.NOT_ADDRESSED, anchored=0, walked=1, skipped=1, matrix=m) == (
        "Resolve finished: 0 of 1 not addressed timepoint(s) anchored, 1 skipped. Next: choose partial (3) under "
        "Problem and press Resolve.")
    assert rc.finished(qc.PARTIAL, anchored=0, walked=3, skipped=3, looked=2, matrix=m) == (
        "Resolve finished: looked at 2 of 3 partial timepoint(s). Next: choose not addressed (1) under Problem and "
        "press Resolve.")


# ── the page ─────────────────────────────────────────────────────────────────
from tests.test_nanowell_qc_view import _click, qc_page  # noqa: E402,F401 — the QC view's page, nothing selected


def _opened(qtbot, st, view):
    qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.view == view, timeout=20000)


def test_a_click_on_a_problem_walks_its_class_from_that_cell(qc_page, qtbot):
    """B01 at t_c is the second not addressed timepoint on the plate: the click opens it, not the first, and Back and
    Skip move within not addressed — never onto A02's partial timepoint between them in plate order."""
    ctx, st, _out = qc_page
    _click(ctx, st, "B01", "t_c")
    _opened(qtbot, st, "array")
    queue = st.anchor_queue
    assert queue is not None and {item.state for item in queue.items} == {"not_addressed"}
    assert [(i.well, i.timepoint) for i in queue.items] == [("A01", "t_a"), ("B01", "t_c")]
    assert (st.well, st.timepoint, queue.index) == ("B01", "t_c", 1)
    window = ctx.widgets.get("_nw_canvas").window()
    assert ctx.widgets.get("_nw_resolve_guide").isVisibleTo(window)
    assert ctx.widgets.get("_nw_resolve_kicker").text() == "Resolving: not addressed · 2 of 2"
    assert ctx.widgets.get("_nw_resolve_action").text().startswith("Click the middle of the central square")
    assert ctx.widgets.get("_nw_resolve_then").text().startswith("The anchor is saved and the next one opens by "
                                                                 "itself. This is the last one.")
    ctx.widgets.get("_nw_queue_back_btn").click()
    _opened(qtbot, st, "array")
    assert (st.well, st.timepoint) == ("A01", "t_a")
    # The partial cell opens on Identity, its class's walk, with the card's own button to go on.
    ctx.widgets.get("_nw_queue_stop_btn").click()
    _click(ctx, st, "A02", "t_a")
    _opened(qtbot, st, "identity")
    assert {item.state for item in st.anchor_queue.items} == {"partial"}
    assert ctx.widgets.get("_nw_resolve_action_btn").text() == "Looks right — next"
    # Choosing Array while a partial timepoint is shown ends the walk there: its card belongs to Identity.
    from review.features.nanowell import controller as nwc

    nwc.set_view(ctx, "array")
    assert st.anchor_queue is None and st.view == "array" and st.identity_focus == ""


def _with_grid(out, well, tp, **fields):
    path = out / "nanowells" / f"{well}_grid.json"
    grid = json.loads(path.read_text())
    grid["timepoints"][tp].update(fields)
    path.write_text(json.dumps(grid))


def _reread(ctx, st, qtbot):
    from review.features.nanowell import controller as nwc

    nwc.redraw(ctx)
    qtbot.waitUntil(lambda: st.qc_reading is None and st.qc_records is not None and st.qc_drawn is not None
                    and bool(st.qc_drawn.cells), timeout=20000)


def test_the_view_opens_on_the_hint_and_rings_it(qc_page, qtbot):
    """The run's own square candidate, placed well off the montage's centre so a walk that still opened on the centre
    — the old behaviour — draws it somewhere else."""
    from review.features.nanowell import controller as nwc

    ctx, st, out = qc_page
    point = (60.0, 180.0)
    _with_grid(out, "A01", "t_a", fiducial={"position_um": list(point), "reason": "not accepted"})
    _reread(ctx, st, qtbot)
    _click(ctx, st, "A01", "t_a")
    qtbot.waitUntil(lambda: st.anchor_pending is None and not st.computing and st.drawn is not None
                    and st.drawn.ax is not None, timeout=20000)
    nwc.redraw(ctx)
    left, top, right, bottom = st.drawn.region
    assert ((left + right) / 2, (top + bottom) / 2) == pytest.approx(point, abs=0.5)
    rings = [p for p in st.drawn.ax.patches if p.get_gid() == "nw-resolve-target"]
    assert rings and all(tuple(p.center) == pytest.approx(point) for p in rings)
    assert ctx.widgets.get("_nw_resolve_action").text().endswith(
        "The ring is the run's best guess, which it did not trust.")
    assert st.anchor_mode                                             # the click on it sets the anchor


def test_a_timepoint_not_located_offers_to_exclude_its_well(qc_page, qtbot):
    ctx, st, out = qc_page
    _with_grid(out, "A01", "t_c", status="no_lattice", reason="No lattice was found")
    _reread(ctx, st, qtbot)
    assert st.qc_matrix.cells[("A01", "t_c")].state == qc.NOT_LOCATED
    _click(ctx, st, "A01", "t_c")
    _opened(qtbot, st, "array")
    assert not st.anchor_mode                                         # nothing to set: a click must not set one
    button = ctx.widgets.get("_nw_resolve_action_btn")
    assert button.text() == "Exclude well" and button.isVisibleTo(ctx.widgets.get("_nw_canvas").window())
    button.click()
    assert "A01" in st.excluded_wells
    assert st.anchor_queue is None and st.view == "qc"                # the only one: the walk ended


# ── a walk skips what an anchor cannot change ────────────────────────────────
#: Reasons as ``pipeline.nanowell_identity.compare`` words them. The reported plate had 65 timepoints needing a look
#: that were inconclusive on the first alone, every address landing on itself; the walk armed an anchor at each, the
#: re-run addressed them from the same square, and nothing changed.
CENTRE = ("every address lands on itself, but the array's fitted centre lands 0.90 pitch from this timepoint's once "
          "registered on the square: the square may have moved against the array (a decoy), or a fitted rim is loose")
MARGIN = ("the square's best match (0.40) leads the next place more than half a pitch away by only 0.05, under 0.1: "
          "something else in the window looks as much like it")
NOT_ACCEPTED = "the run did not accept a central square (squareness 0.21 against a runner-up of 0.12)"
TILE = ("tile(s) 3 (120 um, 1.00 pitch) placed a lattice step differently against the rest of the grid than at the "
        "reference, so their nanowells' addresses are wrong in one of the two")
STEP = "40 of 60 addresses land on other nanowells: a lattice step"


def _walk_matrix():
    cells = {("A01", "t1"): qc.QcCell("A01", "t1", qc.INCONCLUSIVE, CENTRE),
             ("A02", "t1"): qc.QcCell("A02", "t1", qc.INCONCLUSIVE, MARGIN),
             ("A03", "t1"): qc.QcCell("A03", "t1", qc.INCONCLUSIVE, NOT_ACCEPTED),
             ("A04", "t1"): qc.QcCell("A04", "t1", qc.CONTRADICTED, TILE),
             ("A05", "t1"): qc.QcCell("A05", "t1", qc.CONTRADICTED, STEP)}
    return qc.QcMatrix(wells=["A01", "A02", "A03", "A04", "A05"], timepoints=["t1"], cells=cells)


def test_a_walk_leaves_out_what_an_anchor_cannot_change_and_says_so():
    m = _walk_matrix()
    walked = lambda state: [i.well for i in qc.queue_items(m, (state,))]       # noqa: E731
    assert walked(qc.INCONCLUSIVE) == ["A02", "A03"]          # the square's own doubt; "central square" is not set aside
    assert walked(qc.CONTRADICTED) == ["A05"]
    assert qc.set_aside(m, (qc.INCONCLUSIVE,)) == {"centre": 1}
    assert qc.set_aside(m, (qc.CONTRADICTED,)) == {"tile": 1}
    assert qc.queue_label(m, False, (qc.INCONCLUSIVE,)) == "Resolve: 2 timepoints"
    assert ("1 timepoint of this class is not walked: the doubt is the array's fitted outline (1), not the central "
            "square") in qc.action_line(m, (qc.INCONCLUSIVE,))
    # The cell clicked on the matrix is opened whatever its reason.
    assert [i.well for i in qc.queue_items(m, (qc.INCONCLUSIVE,), keep=("A01", "t1"))] == ["A01", "A02", "A03"]


def test_only_a_timepoint_an_anchor_can_change_opens_armed_and_its_card_says_so():
    centre, margin = QueueItem("A01", "t1", CENTRE, state=qc.INCONCLUSIVE), QueueItem("A02", "t1", MARGIN,
                                                                                    state=qc.INCONCLUSIVE)
    assert (rc.armed_for(centre), rc.armed_for(margin)) == (False, True)
    assert rc.armed_for(QueueItem("A04", "t1", TILE, state=qc.CONTRADICTED)) is False
    assert rc.armed_for(QueueItem("A05", "t1", STEP, state=qc.CONTRADICTED)) is True
    card = rc.guide_for(centre, rc.Hint(), index=0, total=1, armed=False)
    assert card.action.startswith("Nothing to click: the doubt here is the array's fitted outline, not the central "
                                  "square")
    assert "Click the middle" not in card.action
    assert rc.guide_for(margin, rc.Hint(), index=0, total=1, armed=True).action.startswith(
        "Click the middle of the true central square.")
