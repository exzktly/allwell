"""The QC view on the Nanowells page: the plate's wells and timepoints, each with the state that says what needs doing
(``qc_compute``, drawn by ``_qc_render``), and the *Resolve* walk that steps through one class of problem.

The records are read for the whole plate, not for the selected wells: the matrix is the one place to see what the run
left to do, and a well nobody has selected yet is exactly the one to find. Reading hashes each well's sites table
(the identity check's staleness test), so it runs on a worker; the result is kept against
``qc_compute.signature`` — the names, times and sizes of the files it read — and read again only when one changes:
a run, the identity check, an anchor set or cleared.

A click on a cell that holds a problem starts the *Resolve* walk of that cell's class at that cell
(``_anchor_queue.start_class``): it opens where the problem is fixed, with the guide card saying what is wrong, what
to click and where, and what happens next, and the next timepoint of the same class is one key away. A cell with
nothing to fix (confirmed, the reference, not checked) opens on the Identity view.
"""

from __future__ import annotations

import logging
from pathlib import Path

from review.features.nanowell import _anchor_queue as aq
from review.features.nanowell import controller as _controller
from review.features.nanowell import resolve_compute as rc
from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell import qc_compute as qc
from review.features.nanowell._qc_render import draw_qc
from review.features.nanowell.collect import in_dir_for

log = logging.getLogger(__name__)


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def _nwc():
    return _controller


def anchors_path(st, data_dir) -> Path | None:
    """The anchors file, whether or not the page has read any well's tables yet. Without the page's data this looks
    for the ``in/`` folder on disk, so it is called on the worker (:func:`ensure_read`), never on the GUI thread."""
    from util.nanowell_anchors import anchors_path as path_in

    if st.data is not None:
        return fmc.anchors_file(st.data)

    in_dir = in_dir_for(Path(data_dir))
    return None if in_dir is None else path_in(in_dir)


def matrix(ctx) -> qc.QcMatrix | None:
    """The plate's :class:`qc_compute.QcMatrix` from the records last read, or ``None`` before the first read lands.
    Starts a check of the files on a worker (:func:`ensure_read`); never waits for it."""
    st = _state(ctx)
    if st is None or not ctx.state.dataset.data_dir:
        return None
    ensure_read(ctx)
    records = st.qc_records
    if records is None:
        return None
    if st.qc_matrix_for != id(records):
        st.qc_matrix, st.qc_matrix_for = qc.plate_qc(records), id(records)
    return st.qc_matrix


def ensure_read(ctx, *, force: bool = False) -> None:
    """Check the plate's files on a worker, and read its records there too when they have changed since the last read.

    **Nothing here touches the disk on the GUI thread.** The signature (``qc_compute.signature``) is a ``stat`` of
    every well's grid record, identity sidecar and sites table — about three files a well — and it is asked for on
    every refresh of the page's controls and every draw of the QC view. On a fast local disk that is a millisecond;
    on a slow or synced one it held the window unresponsive for 30 to 100 s at a time, with every faulthandler dump
    of the GUI thread inside ``pathlib.stat`` here. So the check runs on a worker, one at a time: a request while one
    is in flight is remembered (``qc_recheck``) and runs once it lands, so a file changed during a check is still
    found at the next refresh. A dataset switch is noticed without any file access, by the signature's folder."""
    from core.worker_bridge import run_off_thread

    st = _state(ctx)
    data_dir = ctx.state.dataset.data_dir
    if st is None or not data_dir:
        return
    if st.qc_signature is not None and st.qc_signature[0] != str(data_dir):
        st.qc_records, st.qc_signature = None, None      # another dataset: never draw the last one's plate
    if st.qc_reading is not None:
        st.qc_recheck = True                             # one check at a time; this one runs when it lands
        return
    held = None if force else st.qc_signature
    path = fmc.anchors_file(st.data) if st.data is not None else None
    needs_lookup = st.data is None
    token = object()
    st.qc_reading, st.qc_recheck = token, False

    def job():
        anchors = anchors_path(st, data_dir) if needs_lookup else path
        sig = qc.signature(data_dir, anchors)
        if sig == held:
            return sig, None, ""
        try:
            return sig, qc.read_plate(data_dir, anchors), ""
        except Exception as exc:                         # held against *sig*, so it is not read again until it changes
            log.warning("Nanowells QC: the plate's records could not be read: %s", exc, exc_info=True)
            return sig, None, str(exc) or type(exc).__name__

    def again() -> None:
        if st.qc_recheck:
            ensure_read(ctx)

    def done(found) -> None:
        if st.qc_reading is not token:
            return
        sig, records, error = found
        st.qc_reading, st.qc_bridge = None, None
        if str(ctx.state.dataset.data_dir or "") != sig[0]:
            again()                                      # read for a dataset no longer loaded: drop it
            return
        if records is not None or error:
            # A failure is held against the files as they were, so the figure says why rather than reading again on
            # every refresh.
            st.qc_records, st.qc_signature, st.qc_error = records, sig, error
            _after_read(ctx)
        again()

    def failed(message) -> None:
        # Only the signature itself can fail here (``job`` holds a failed read); the next refresh tries again.
        log.warning("Nanowells QC: the plate's files could not be checked: %s", message)
        if st.qc_reading is token:
            st.qc_reading, st.qc_bridge = None, None

    st.qc_bridge = run_off_thread(job, on_done=done, on_failed=failed)


def _after_read(ctx) -> None:
    st = _state(ctx)
    nwc = _nwc()
    nwc._call(ctx, "_nw_refresh_controls")
    if st is not None and st.view == "qc":
        nwc.redraw(ctx)


def draw(ctx, st, fig, canvas) -> None:
    """Draw the QC view (``controller.redraw``'s branch, taken before any check of the selection)."""
    from review.core.plot_style import apply_export_style_to_current

    st.hits, st.drawn = None, None
    data_dir = ctx.state.dataset.data_dir
    plate = matrix(ctx)
    st.qc_drawn = draw_qc(fig, plate, which=st.qc_wells, error=st.qc_error,
                          walk=rc.walk_states(plate, st.anchor_queue_states), out_dir=Path(data_dir) if data_dir else None,
                          message=st.qc_message, highlight=st.qc_highlight, excluded=st.excluded_wells)
    apply_export_style_to_current(ctx, fig, canvas)
    if st.qc_drawn is not None and st.qc_drawn.relabel is not None:
        st.qc_drawn.relabel()
    canvas.draw_idle()


def _cell(st, event) -> qc.QcCell | None:
    drawn = st.qc_drawn
    if drawn is None or drawn.ax is None or event.inaxes is not drawn.ax:
        return None
    return drawn.cell_at(event.xdata, event.ydata)


def on_motion(ctx, event) -> None:
    """The full sentence for the cell under the pointer, as a tooltip; none off the cells."""
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QToolTip

    st = _state(ctx)
    cell = _cell(st, event) if st is not None else None
    if cell is None:
        QToolTip.hideText()
        return
    hours = qc.hours_of(st.qc_records.times_h if st.qc_records is not None else {}, cell.timepoint)
    QToolTip.showText(QCursor.pos(), qc.hover_text(cell, hours), ctx.widgets.get_or_none("_nw_canvas"))


def on_click(ctx, event) -> None:
    """A left click on a cell opens it where it is dealt with (the module docstring); anywhere else does nothing."""
    st = _state(ctx)
    if st is None or event.button != 1:
        return
    cell = _cell(st, event)
    if cell is None:
        return
    st.qc_message, st.qc_highlight = "", frozenset()
    if rc.class_of(cell.state) is not None and aq.start_class(ctx, cell.state, cell.well, cell.timepoint):
        return
    if cell.state in qc.ARRAY_STATES:
        _nwc().open_for_anchor(ctx, cell.well, cell.timepoint)
    else:
        open_identity(ctx, cell.well, "" if cell.state == qc.REFERENCE else cell.timepoint)


def open_identity(ctx, well: str, timepoint: str) -> bool:
    """Show *well* at *timepoint* on the Identity view (its reference compared with the timepoint). The well is
    added to the plate selection, never replacing it, so the page reads its tables; ``""`` lets the view choose the
    timepoint it opens on (the well's first not confirmed)."""
    from review.core.selection_controller import set_selected_wells

    st = _state(ctx)
    if st is None or not well:
        return False
    nwc = _nwc()
    selected = set(ctx.state.selection.selected_wells)
    if well not in selected:
        set_selected_wells(ctx, selected | {well})
    st.view, st.well, st.timepoint = "identity", well, timepoint or ""
    st.identity_zoom, st.identity_zoom_for = {}, None
    nwc._save_soon(ctx)
    nwc.ensure_loaded(ctx)
    nwc._call(ctx, "_nw_refresh_controls")
    nwc.redraw(ctx)
    return True


def set_wells(ctx, which: str) -> None:
    """*Wells*: a row for each well needing a look, or for every well."""
    st = _state(ctx)
    if st is None or which not in qc.WELL_FILTERS:
        return
    st.qc_message, st.qc_highlight = "", frozenset()
    _nwc()._set(ctx, "qc_wells", which)
