"""The Array view's **Anchor queue**: walk every timepoint waiting for an anchor, one montage after another.

*Anchor queue* (on the band's Anchor group) reads which timepoints are waiting (``anchor_queue_compute``) and opens
the first exactly as QC Triage's *Set anchor…* does (``controller.open_for_anchor``): the Array view on that well
and timepoint, the anchor layer on in the well frame, *Set anchor* on once the well's tables are loaded. A click on
the central square writes the anchor as it always does (``controller.set_anchor_at``), which tells the queue
(:func:`anchor_set`); after a short pause — long enough to see where the anchor landed — the next timepoint is
shown. Each timepoint opens at :data:`OPEN_ZOOM` times *Fit* on the centre of the whole-well montage, where the
central square usually is. *One per well* limits the walk to each well's first waiting timepoint. *Skip* moves on without an anchor, *Back* returns to the previous one (a click there replaces its anchor),
and *Stop* ends the walk. The walk ends by itself after the last item, saying on the figure how many were anchored
and pointing at *Re-run anchored wells…*, which is what puts the anchors to use.

Each step shows its well beside the selection the queue started with, never accumulating the wells walked past,
and the end restores that selection: plate selections are app-wide, and a walk over thirty wells must not leave
thirty wells selected on every other tab.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer

from review.core.selection_controller import set_selected_wells
from review.features.nanowell import anchor_queue_compute as aqc
from review.features.nanowell import controller as nwc
from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell.collect import nanowell_dir
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


def build_queue(ctx) -> tuple[aqc.AnchorQueue | None, str]:
    """The :class:`anchor_queue_compute.AnchorQueue` of this dataset's waiting timepoints, or ``(None, why)``."""

    st = _state(ctx)
    data_dir = ctx.state.dataset.data_dir
    if st is None or st.data is None or not data_dir:
        return None, "The nanowell tables are not loaded."
    path = fmc.anchors_file(st.data)
    if path is None:
        return None, "This dataset has no in/ folder beside it to keep anchors in, so none can be set."
    try:
        anchors = read_anchors(path)
    except AnchorsFileError as exc:
        return None, f"The anchors file cannot be used: {exc}"
    items, pending = aqc.waiting_items(aqc.grid_statuses(nanowell_dir(data_dir)), anchors, order=st.data.order)
    queue = aqc.AnchorQueue(items, selection_before=frozenset(ctx.state.selection.selected_wells),
                            pending_rerun=pending, data_dir=str(data_dir))
    queue.set_per_well(st.anchor_queue_per_well)
    if not queue.items:
        why = "No nanowell timepoint is waiting for an anchor"
        if items:
            why += f" (One per well is on: {len(items)} more wait in wells that already have one)"
        why += "."
        if pending:
            why += (f" {len(pending)} timepoint(s) have an anchor that no run has used yet: Re-run anchored wells… "
                    "addresses them.")
        return None, why
    return queue, ""


def start(ctx) -> bool:
    """*Anchor queue*: build the queue and show its first timepoint; when nothing waits, say so on the figure."""

    st = _state(ctx)
    if st is None:
        return False
    queue, why = build_queue(ctx)
    if queue is None:
        st.array_message = why
        nwc._call(ctx, "_nw_refresh_controls")
        nwc.redraw(ctx)
        return False
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
    shown = nwc.open_for_anchor(ctx, item.well, item.timepoint, zoom=OPEN_ZOOM)
    if shown and item.refused:
        st = _state(ctx)
        if st is not None and not st.array_message:
            st.array_message = ("The last run could not address this timepoint from the anchor set here: click the "
                                "central square again.")
    nwc._call(ctx, "_nw_refresh_controls")
    return shown


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


def stop(ctx) -> bool:
    """*Stop*, and the end of the walk past its last item: restore the starting selection and say on the figure
    what the walk did (what it never reached counts as skipped)."""

    st = _state(ctx)
    queue = active_queue(ctx)
    if st is None or queue is None:
        return False
    st.anchor_queue = None
    st.anchor_mode = False
    if set(ctx.state.selection.selected_wells) != set(queue.selection_before):
        set_selected_wells(ctx, set(queue.selection_before))
    queue.index = len(queue.items)
    st.array_message = queue.summary()
    nwc._call(ctx, "_nw_refresh_controls")
    nwc.redraw(ctx)
    return True
