"""The QC view on the Nanowells page: the plate's wells and timepoints, each with the state that says what needs doing
(``qc_compute``, drawn by ``_qc_render``), and the *Anchor queue* that walks the ones waiting for an anchor.

The records are read for the whole plate, not for the selected wells: the matrix is the one place to see what the run
left to do, and a well nobody has selected yet is exactly the one to find. Reading hashes each well's sites table
(the identity check's staleness test), so it runs on a worker; the result is kept against
``qc_compute.signature`` — the names, times and sizes of the files it read — and read again only when one changes:
a run, the identity check, an anchor set or cleared.

A click on a cell opens it where it is dealt with: an identity verdict on the Identity view, a timepoint not addressed
(or one whose anchor waits for a re-run, or one an anchor cannot change) on the Array view with *Set anchor* on
(``controller.open_for_anchor``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from review.features.nanowell import qc_compute as qc

log = logging.getLogger(__name__)


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def _nwc():
    from review.features.nanowell import controller

    return controller


def anchors_path(st, data_dir) -> Path | None:
    """The anchors file, whether or not the page has read any well's tables yet."""
    from review.features.nanowell import full_montage_compute as fmc

    if st.data is not None:
        return fmc.anchors_file(st.data)
    from review.features.nanowell.collect import in_dir_for
    from util.nanowell_anchors import anchors_path as path_in

    in_dir = in_dir_for(Path(data_dir))
    return None if in_dir is None else path_in(in_dir)


def matrix(ctx) -> qc.QcMatrix | None:
    """The plate's :class:`qc_compute.QcMatrix` from the records last read, or ``None`` before the first read lands.
    Starts a read when the files have changed since (:func:`ensure_read`)."""
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
    """Read the plate's records on a worker unless what is held matches the files on disk."""
    from core.worker_bridge import run_off_thread

    st = _state(ctx)
    data_dir = ctx.state.dataset.data_dir
    if st is None or not data_dir:
        return
    path = anchors_path(st, data_dir)
    sig = qc.signature(data_dir, path)
    if not force and (sig == st.qc_signature or sig == st.qc_reading):
        return
    if st.qc_signature is not None and st.qc_signature[0] != sig[0]:
        st.qc_records, st.qc_signature = None, None      # another dataset: never draw the last one's plate
    st.qc_reading = sig

    def done(records) -> None:
        if st.qc_reading != sig:
            return                                       # a newer read was asked for; it will land instead
        st.qc_records, st.qc_signature, st.qc_reading, st.qc_bridge = records, sig, None, None
        st.qc_error = ""
        _after_read(ctx)

    def failed(message) -> None:
        log.warning("Nanowells QC: the plate's records could not be read: %s", message)
        if st.qc_reading == sig:
            # Held against this signature, so the figure says why rather than reading again on every refresh.
            st.qc_records, st.qc_signature, st.qc_reading, st.qc_bridge = None, sig, None, None
            st.qc_error = str(message)
            _after_read(ctx)

    st.qc_bridge = run_off_thread(lambda: qc.read_plate(data_dir, path), on_done=done, on_failed=failed)


def _after_read(ctx) -> None:
    st = _state(ctx)
    nwc = _nwc()
    nwc._call(ctx, "_nw_refresh_controls")
    if st is not None and st.view == "qc":
        nwc.redraw(ctx)


def draw(ctx, st, fig, canvas) -> None:
    """Draw the QC view (``controller.redraw``'s branch, taken before any check of the selection)."""
    from review.core.plot_style import apply_export_style_to_current
    from review.features.nanowell._qc_render import draw_qc

    st.hits, st.drawn = None, None
    data_dir = ctx.state.dataset.data_dir
    st.qc_drawn = draw_qc(fig, matrix(ctx), which=st.qc_wells, error=st.qc_error, out_dir=Path(data_dir) if data_dir else None,
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
