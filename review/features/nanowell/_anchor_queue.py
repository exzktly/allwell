"""The QC view's **Resolve** walk: step through one class of problem, one timepoint after another, each opened where
it is fixed with the guide card saying what is wrong, what to do and where, and what happens next
(``resolve_compute``, drawn by ``resolve_view``).

The walk starts two ways: *Resolve* on the QC view's band walks the class chosen under *Problem* from its first
timepoint (:func:`start`), and a click on a cell of the matrix walks that cell's class from that cell
(:func:`start_class`), so the next one of the same kind is one key away. A timepoint whose class is fixed on the
montage (not addressed, contradicted, inconclusive, not located, anchor waiting) opens exactly as QC Triage's *Set
anchor…* does (``controller.open_for_anchor``): the Array view on that well and timepoint, the anchor layer on in the
well frame, *Set anchor* on once the well's tables are loaded for the classes an anchor fixes, zoomed about the best
evidence of where the square is (``resolve_compute.hint_for``), which is ringed. A partial timepoint opens on the
Identity view instead, its Distance map centred on the nanowells left out (:func:`_open_identity_item`): that is where
they are drawn as such, and there is nothing to set.

A click on the central square writes the anchor as it always does (``controller.set_anchor_at``), which tells the
walk (:func:`anchor_set`); after a short pause — long enough to see where the anchor landed — the next timepoint is
shown. A timepoint with no hint opens at :data:`OPEN_ZOOM` times *Fit* on the centre of the whole-well montage, where
the central square usually is; one with a hint at ``resolve_compute.HINT_ZOOM`` about it. *One per well* limits a
*Resolve* walk to each well's first timepoint. *Skip* moves on, *Back* returns to the previous one (a click there
replaces its anchor), and *Stop* ends the walk; so does choosing a view other than the one the shown timepoint is
on (``controller.set_view``). The walk ends by itself after the last item. Whichever way it ends, the page goes back
to the view the walk started from — the QC view — which says what the walk did and what to do next
(``resolve_compute.finished``: *Re-run anchored wells…* when anything was anchored, the next class otherwise), rings
the cells anchored, and holds *Re-run anchored wells…*, which is what puts the anchors to use.

Each step shows its well beside the selection the queue started with, never accumulating the wells walked past,
and the end restores that selection: plate selections are app-wide, and a walk over thirty wells must not leave
thirty wells selected on every other tab.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer

from review.core.selection_controller import set_selected_wells
from review.features.nanowell import anchor_queue_compute as aqc
from review.features.nanowell import controller as nwc
from review.features.nanowell import _qc_controller as qcc
from review.features.nanowell import qc_compute as qc
from review.features.nanowell import resolve_compute as rc
from util.nanowell_anchors import AnchorsFileError, read_anchors

#: How long the anchor just set stays on screen before the next timepoint replaces it.
ADVANCE_DELAY_MS = 400

#: Each timepoint opens this many times *Fit*, on the centre of the whole-well montage: the central square an anchor
#: marks is usually there, so the click needs no zooming first. *Fit* shows the whole well.
OPEN_ZOOM = 10.0


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def active_queue(ctx) -> aqc.AnchorQueue | None:
    """The queue being walked, or ``None``. A queue built for another dataset is dropped here."""
    st = _state(ctx)
    queue = getattr(st, "anchor_queue", None) if st is not None else None
    if queue is None:
        return None
    if queue.data_dir != str(ctx.state.dataset.data_dir or ""):
        st.anchor_queue = None
        return None
    return queue


def build_queue(ctx, states=None) -> tuple[aqc.AnchorQueue | None, str]:
    """The :class:`anchor_queue_compute.AnchorQueue` of this dataset's timepoints in *states* (the class *Problem*
    chooses when ``None``), or ``(None, why)``. The anchors file is found from the dataset folder, so a click on the
    matrix can start a walk before any well's tables are read."""

    st = _state(ctx)
    data_dir = ctx.state.dataset.data_dir
    if st is None or not data_dir:
        return None, "The nanowell tables are not loaded."
    path = qcc.anchors_path(st, data_dir)
    if path is None:
        return None, "This dataset has no in/ folder beside it to keep anchors in, so none can be set."
    try:
        anchors = read_anchors(path)
    except AnchorsFileError as exc:
        return None, f"The anchors file cannot be used: {exc}"
    del anchors                                          # read only to refuse an unusable file before walking
    matrix = qcc.matrix(ctx)
    if matrix is None:
        return None, "The plate's grid records and identity checks are still being read."
    states = rc.walk_states(matrix, st.anchor_queue_states) if states is None else tuple(states)
    items = qc.queue_items(matrix, states)
    pending = frozenset(k for k, c in matrix.cells.items() if c.state == qc.ANCHOR_WAITING)
    queue = aqc.AnchorQueue(items, selection_before=frozenset(ctx.state.selection.selected_wells),
                            pending_rerun=pending, data_dir=str(data_dir))
    queue.set_per_well(st.anchor_queue_per_well)
    if not queue.items:
        why = f"No timepoint is “{qc.walk_words(states)}”"
        if items:
            # One per well left nothing: every well with a waiting timepoint already has an anchor for a re-run,
            # which is the remedy the message names. Its toggle sits in the group shown only during a walk, so it is
            # turned off here, or nothing on the page could ever walk the rest.
            st.anchor_queue_per_well = False
            why += (f" with One per well: {len(items)} more wait in wells that already have one. One per well is now "
                    "off, so Resolve walks them all")
        why += "."
        if pending:
            why += (f" {len(pending)} timepoint(s) have an anchor that no run has used yet: Re-run anchored wells… "
                    "addresses them.")
        return None, why
    return queue, ""


def start(ctx) -> bool:
    """*Resolve*: build the walk of the class *Problem* chooses and show its first timepoint; when there is none,
    say so on the figure."""
    return _begin(ctx, *build_queue(ctx))


def start_class(ctx, state: str, well: str, timepoint: str) -> bool:
    """A click on the matrix: walk every timepoint of *state*'s class, starting at *well* at *timepoint*, so the next
    one of the same kind follows. *One per well* is not applied: the cell clicked is always the one shown."""
    st = _state(ctx)
    if st is None or rc.class_of(state) is None:
        return False
    per_well, st.anchor_queue_per_well = st.anchor_queue_per_well, False
    try:
        queue, why = build_queue(ctx, (state,))
    finally:
        st.anchor_queue_per_well = per_well
    if queue is not None:
        keys = [(item.well, item.timepoint) for item in queue.items]
        queue.index = keys.index((well, timepoint)) if (well, timepoint) in keys else 0
    return _begin(ctx, queue, why)


def _begin(ctx, queue, why: str) -> bool:
    st = _state(ctx)
    if st is None:
        return False
    st.qc_message, st.qc_highlight = "", frozenset()
    if queue is None:
        if st.view == "qc":
            st.qc_message = why
        else:
            st.array_message = why
        nwc._call(ctx, "_nw_refresh_controls")
        nwc.redraw(ctx)
        return False
    queue.return_view = st.view
    st.anchor_queue = queue
    return _open_current(ctx, queue)


def _open_current(ctx, queue) -> bool:
    """Open the queue's current item (the well beside the starting selection), or end the walk past the last."""

    item = queue.current
    if item is None:
        return stop(ctx)
    wanted = set(queue.selection_before) | {item.well}
    if set(ctx.state.selection.selected_wells) != wanted:
        set_selected_wells(ctx, wanted)
    st = _state(ctx)
    cls = rc.class_of(item.state) or rc.CLASSES[qc.NOT_ADDRESSED]
    hint = _hint(ctx, st, item)
    st.resolve_hint = hint
    if cls.view == "identity":
        shown = _open_identity_item(ctx, st, item)
        nwc._call(ctx, "_nw_refresh_controls")
        return shown
    _forget_focus(st)
    armed = cls.armed
    zoom = rc.HINT_ZOOM if hint.point_um is not None else OPEN_ZOOM
    shown = nwc.open_for_anchor(ctx, item.well, item.timepoint, zoom=zoom, arm=armed, at=hint.point_um)
    if shown and st is not None:
        if not armed:
            st.array_message = ""                       # the guide card says what to do
        elif item.refused and not st.array_message:
            st.array_message = ("The last run could not address this timepoint from the anchor set here: click the "
                                "central square again.")
        elif st.array_message and not st.anchor_mode:
            # Set anchor could not be turned on here (the figure says why): say how the walk goes on.
            st.array_message += " Skip (→) goes on to the next one."
    nwc._call(ctx, "_nw_refresh_controls")
    return shown


def _hint(ctx, st, item) -> rc.Hint:
    """Where *item*'s fix is made, from its grid block and its entry in the well's identity sidecar (read without
    re-hashing the tables: the matrix this walk was built from already decided the verdict)."""
    from pipeline.nanowell_identity import read_sidecar

    records = getattr(st, "qc_records", None)
    block = ((records.grids.get(item.well) or {}).get(item.timepoint)) if records is not None else None
    entry = None
    if item.state in (qc.CONTRADICTED, qc.INCONCLUSIVE):
        out_dir = _out_dir(ctx)
        payload = read_sidecar(out_dir, item.well, verify=False) if out_dir is not None else None
        entry = ((payload or {}).get("timepoints") or {}).get(item.timepoint)
    return rc.hint_for(item.state, block, entry)


def _out_dir(ctx):
    from pathlib import Path

    from review.data.nanowell_focus_gate import nanowell_dir

    folder = nanowell_dir(ctx.state.dataset.data_dir)
    return Path(folder).parent if folder is not None else None


def _open_identity_item(ctx, st, item) -> bool:
    """A partial timepoint: the Identity view's Distance map of it against its reference, coloured by status and
    centred on the nanowells it leaves out (the *Nanowell* box, which the walk fills and clears)."""
    records = getattr(st, "qc_records", None)
    left_out = ((records.excluded.get(item.well) or {}).get(item.timepoint)) if records is not None else ()
    focus = rc.focus_address(left_out)
    st.identity_mode = "distance"
    if st.identity_colour == "distance":
        st.identity_colour = "auto"                     # Auto colours a partial timepoint by status
    st.resolve_focus = st.identity_focus = focus
    st.anchor_mode = False
    return qcc.open_identity(ctx, item.well, item.timepoint)


def _forget_focus(st) -> None:
    """Clear the *Nanowell* box when the walk wrote what it holds; a box the reader typed is theirs."""
    if st.resolve_focus and st.identity_focus == st.resolve_focus:
        st.identity_focus = ""
    st.resolve_focus = ""


def current_view(ctx) -> str | None:
    """The view the walk's current timepoint is shown on, or ``None`` with no walk."""
    queue = active_queue(ctx)
    item = queue.current if queue is not None else None
    cls = rc.class_of(item.state) if item is not None else None
    return None if item is None else (cls.view if cls is not None else "array")


def current_guide(ctx) -> rc.Guide | None:
    """The guide card's text for the timepoint shown (``resolve_compute.guide_for``), or ``None`` with no walk."""
    st = _state(ctx)
    queue = active_queue(ctx)
    item = queue.current if queue is not None else None
    if st is None or item is None:
        return None
    cls = rc.class_of(item.state)
    armed = bool(cls is not None and cls.armed and (st.anchor_mode or st.anchor_pending is not None))
    records = st.qc_records
    left_out = None
    hours = ""
    if records is not None:
        left_out = len((records.excluded.get(item.well) or {}).get(item.timepoint) or ()) or None
        hours = rc.hours_suffix(item.timepoint, qc.hours_of(records.times_h, item.timepoint))
    hint = st.resolve_hint if isinstance(st.resolve_hint, rc.Hint) else rc.Hint()
    return rc.guide_for(item, hint, index=queue.index, total=len(queue.items), armed=armed, left_out=left_out,
                        hours=hours)


def target(ctx, well: str, timepoint: str) -> rc.Hint | None:
    """The hint to ring on the Array view when it draws *well* at *timepoint* during a walk, or ``None``."""
    st = _state(ctx)
    queue = active_queue(ctx)
    item = queue.current if queue is not None else None
    if st is None or item is None or (item.well, item.timepoint) != (well, timepoint):
        return None
    hint = st.resolve_hint
    return hint if isinstance(hint, rc.Hint) and hint.point_um is not None else None


def ring_target(ctx, drawn, well: str, timepoint: str) -> None:
    """Ring the walk's hint on the Array view just drawn (*drawn*, ``renderer.ArrayDrawn``) when it shows the walk's
    current timepoint; nothing otherwise."""
    from review.features.nanowell._resolve_render import draw_resolve_target

    hint = target(ctx, well, timepoint)
    if hint is not None and getattr(drawn, "ax", None) is not None:
        draw_resolve_target(drawn.ax, drawn.region, drawn.size_px, hint.point_um)


def exclude_current(ctx) -> bool:
    """The guide card's *Exclude well* (a timepoint not located): leave the shown well out of the page's figures,
    then go on to the next timepoint."""
    queue = active_queue(ctx)
    item = queue.current if queue is not None else None
    if item is None:
        return False
    nwc.set_well_excluded(ctx, item.well, True)
    return step(ctx, +1)


def step(ctx, delta: int) -> bool:
    """*Skip* (``+1``) or *Back* (``-1``): show the next or the previous item. Back stops at the first."""
    queue = active_queue(ctx)
    if queue is None:
        return False
    queue.index = max(0, min(len(queue.items), queue.index + delta))
    return _open_current(ctx, queue)


def set_per_well(ctx, on: bool) -> None:
    """*One per well*: walk only each well's first waiting timepoint, or every one. Kept on the page state for the
    session (the next queue starts the same way); a running walk switches in place (``AnchorQueue.set_per_well``)
    and shows whatever it lands on."""
    st = _state(ctx)
    if st is None:
        return
    st.anchor_queue_per_well = bool(on)
    queue = active_queue(ctx)
    if queue is None or queue.per_well == st.anchor_queue_per_well:
        return
    shown = queue.current
    queue.set_per_well(st.anchor_queue_per_well)
    if queue.current != shown or queue.current is None:
        _open_current(ctx, queue)
    else:
        nwc._call(ctx, "_nw_refresh_controls")


def anchor_set(ctx, well: str, timepoint: str) -> None:
    """``controller.set_anchor_at`` saved an anchor: when it is the queue's current item, record it and move on
    after :data:`ADVANCE_DELAY_MS`. The move is dropped if the walk moved meanwhile (Back, Skip, Stop)."""

    queue = active_queue(ctx)
    item = queue.current if queue is not None else None
    if item is None or (item.well, item.timepoint) != (well, timepoint):
        return
    queue.anchored.add((well, timepoint))
    at = queue.index

    def advance() -> None:
        if active_queue(ctx) is queue and queue.index == at:
            step(ctx, +1)

    QTimer.singleShot(ADVANCE_DELAY_MS, advance)


def stop(ctx, land_on: str | None = None) -> bool:
    """*Stop*, the end of the walk past its last item, and leaving the Array view mid-walk: restore the starting
    selection, go back to the view the walk started from (or *land_on*, the view chosen instead of Array), and say
    what the walk did (what it never reached counts as skipped) — on the QC view's figure, ringing the cells it
    anchored, and on the status line, which is seen whichever view the page lands on."""

    st = _state(ctx)
    queue = active_queue(ctx)
    if st is None or queue is None:
        return False
    st.anchor_queue = None
    st.anchor_mode = False
    st.resolve_hint = None
    _forget_focus(st)
    if set(ctx.state.selection.selected_wells) != set(queue.selection_before):
        set_selected_wells(ctx, set(queue.selection_before))
    passed = min(queue.index, len(queue.items))
    queue.index = len(queue.items)
    state = queue.items[0].state if queue.items else None
    text = rc.finished(state, anchored=len(queue.anchored), walked=queue.walked(), skipped=queue.skipped(),
                       looked=passed, matrix=qcc.matrix(ctx))
    st.view = land_on or queue.return_view or "qc"
    if st.view == "array":
        st.array_message = text
    else:
        st.array_message = ""
        st.qc_message, st.qc_highlight = text, frozenset(queue.anchored)
    nwc._save_soon(ctx)
    notify = getattr(ctx, "notify", None)
    if notify is not None:
        notify.set_status(text)
    nwc._call(ctx, "_nw_refresh_controls")
    nwc.redraw(ctx)
    return True
