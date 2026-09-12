"""What a gesture on the Segmentation review image *does*.

The rubber-band mechanics live in ``box_select.py`` and the Qt event handlers in
``controller.py``; this module holds the action a finished drag performs in
delete mode, plus the override-map writer that action and the single-cell click
share.

A drag deletes on the displayed frame by pixel overlap: the label mask is in
hand, so the box means exactly what it looks like. The Delete-scope control can
arm further wells, FOVs and timepoints, and the same rectangle is then applied
to those frames as well — by centroid, since deciding overlap for a frame that
is not displayed would mean decoding its mask out of the well's ``_out.zip``,
one decode per well × FOV × timepoint. Both rules are reported separately on the
status line so the difference is never invisible.

Imports run one way — ``controller`` imports this module, never the reverse — so
the write path stays reachable from the click handler without a cycle.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

import numpy as _np

from core.safe import swallowed
from well_viewer.features.crop.region import normalize_region
from well_viewer.data.curation import apply_curation_to_cache
from well_viewer.features.image_browser.review_image.collect import (
    collect_region_delete_targets,
)
from well_viewer.features.image_browser.review_image.renderer import (
    refresh_review_image_ctx, render_review_image_display,
)

_logger = logging.getLogger(__name__)


def override_key(well: str, fov: object, tp: object, nid: object) -> tuple | None:
    """Canonical ``(well, fov, tp, nid)`` override-map key.

    The FOV / timepoint / nucleus tokens go through
    :class:`~well_viewer.data.image_identity.CellKey`, the same constructor
    ``collect.review_row_keys_series`` uses to look overrides back up when
    projecting them onto the row cache, so those three cannot drift between the
    write and the read.

    The **well** is deliberately left exactly as the app spells it. Every reader
    keys off the dataset's own label — ``collect.review_effective_included``
    looks up ``(label, …)`` and ``curation.apply_curation_to_cache`` skips
    anything not in ``dataset.well_paths`` — and those labels are
    ``extract_well_token(stem) or stem`` (``core/load_controller.py``), so a CSV
    whose stem is not a well position keeps its raw stem. Canonicalising the
    well here would upper-case such a label into something no reader ever asks
    for, and the delete would report success while changing nothing.

    Returns ``None`` when any component normalises away to empty, which means
    the row cannot be identified and must not be written.
    """
    from well_viewer.data.image_identity import CellKey
    key = CellKey.from_row({"fov": fov, "timepoint": tp, "nucleus_id": nid}, well="")
    well = str(well or "").strip()
    if not (well and key.fov and key.tp and key.nid):
        return None
    return (well, key.fov, key.tp, key.nid)


def commit_included_overrides(ctx, items) -> int:
    """Write ``[(key, included), …]`` overrides and refresh once for the lot.

    ``key`` is a canonical four-tuple from :func:`override_key`, so this is the
    one writer that can span wells — the per-well cache projection runs once per
    touched well, and the stats invalidation, debounced save, override-version
    bump and canvas refresh run once in total. Without that coalescing a
    hundred-cell box delete would redraw a hundred times.

    The canvas view transform is restored around the refresh so a delete never
    throws away the user's zoom and pan. Returns the number of overrides written.
    """
    from well_viewer.core.selection_controller import invalidate_stats_cache
    wells: set[str] = set()
    # Distinct keys, not writes: a CSV carrying two rows for the same
    # (fov, tp, nucleus_id) produces two items and one override, and the caller
    # reports this number to the user as "cells deleted".
    touched: set = set()
    for key, included in items or ():
        if key is None:
            continue
        ctx.runtime.review.included_overrides[key] = str(included).strip() or "1"
        wells.add(key[0])
        touched.add(key)
    written = len(touched)
    if not written:
        return 0
    for well in wells:
        apply_curation_to_cache(ctx, well)
    try:
        invalidate_stats_cache(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    ctx.persistence.schedule_save("cell_overrides")
    ctx.state.review_image.override_version += 1
    prev_zoom = float(ctx.state.review_image.zoom)
    prev_pan_x = float(ctx.state.review_image.pan_x)
    prev_pan_y = float(ctx.state.review_image.pan_y)
    refresh_review_image_ctx(ctx)
    ctx.state.review_image.zoom = prev_zoom
    ctx.state.review_image.pan_x = prev_pan_x
    ctx.state.review_image.pan_y = prev_pan_y
    render_review_image_display(ctx)
    return written


def delete_scope_is_armed(ctx) -> bool:
    """Whether the Delete-scope control names anything beyond the shown frame."""
    ri = ctx.state.review_image
    return (
        ri.delete_scope_wells is not None
        or ri.delete_scope_fovs is not None
        or ri.delete_scope_tps is not None
    )


def resolve_delete_scope(ctx, well: str, fov: str, tp: str):
    """Resolve the armed drag-delete scope into concrete target sets.

    Returns ``(wells, fovs, tps)``. FOVs and timepoints are normalised the same
    way the override keys are; wells are left as the dataset spells them, for
    the reason given in :func:`override_key`. Each armed field that is ``None``
    — the default — resolves to the frame currently on screen, so an un-armed
    scope means "just this frame" and the delete behaves exactly as it always
    has. An armed field the user emptied resolves to an empty set, which selects
    nothing rather than everything.
    """
    from well_viewer.data.image_identity import normalize_fov, normalize_tp
    ri = ctx.state.review_image
    wells = ({str(well or "").strip()} if ri.delete_scope_wells is None
             else {str(w).strip() for w in ri.delete_scope_wells})
    fovs = ({normalize_fov(fov)} if ri.delete_scope_fovs is None
            else {normalize_fov(f) for f in ri.delete_scope_fovs})
    tps = ({normalize_tp(tp)} if ri.delete_scope_tps is None
           else {normalize_tp(t) for t in ri.delete_scope_tps})
    return wells, fovs, tps


@contextmanager
def _busy(ctx, n_wells: int):
    """Wait cursor + a "working" status for a scoped delete, and log its cost.

    Only for a scope wide enough to be felt — a single well is fast enough that
    flicking the cursor would be noise. The elapsed time goes to the log so the
    decision about whether this needs a worker is made from a measurement rather
    than an impression.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # ``setOverrideCursor`` aborts the process when no QApplication exists, so
    # the cursor half is conditional on one being live; the timing half is not,
    # which keeps this callable from a headless test.
    app = QApplication.instance()
    wide = n_wells > 1
    if wide:
        ctx.notify.set_status(f"Region delete: scanning {n_wells:,} well(s)…")
        if app is not None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            app.processEvents()
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        if wide:
            if app is not None:
                QApplication.restoreOverrideCursor()
            _logger.info(
                "Region delete: scanned %d well(s) in %.2fs", n_wells, elapsed,
            )


def _region_delete_status(ctx, written: int, n_scoped: int, stats: dict,
                          frame_note: str) -> None:
    """Report what the delete actually did, including everything it could not do.

    ``written`` is the override map's own count, not the sum of the two halves:
    a CSV carrying more than one row for the same ``(fov, tp, nucleus_id)``
    yields two hits and one override, and reporting the larger number would
    overstate the edit. The halves are still shown separately because different
    rules decide them — the drawn frame by pixel overlap against its label mask,
    every other frame by centroid — and someone comparing the visible result
    against a neighbouring well needs to know that.

    Everything the delete could not judge is named rather than folded silently
    into a smaller total: wells with no centroid columns, wells whose rows would
    not load, and wells named in the scope that this dataset does not have.
    """
    ctx.notify.set_status(
        f"Region delete: {written:,} cell(s) Included=0 — {frame_note}; "
        f"{n_scoped:,} across {stats['frames']:,} other frame(s) in "
        f"{stats['wells']:,} well(s) (centroid inside)."
    )
    skipped = list(stats.get("no_centroid_wells") or ())
    unreadable = list(stats.get("unreadable_wells") or ())
    missing = list(stats.get("unknown_wells") or ())
    if missing:
        _logger.warning(
            "Region delete: %d armed well(s) are not in this dataset and were "
            "skipped: %s", len(missing), ", ".join(sorted(missing)),
        )
        ctx.notify.toast(
            f"{len(missing)} armed well(s) are not in this dataset — nothing "
            "was deleted there.",
            kind="warning", msec=6000,
        )
    if skipped:
        _logger.warning(
            "Region delete: %d well(s) have no shape_centroid_* columns and were "
            "left untouched: %s", len(skipped), ", ".join(sorted(skipped)),
        )
        ctx.notify.toast(
            f"{len(skipped)} well(s) have no centroid columns — no cells were "
            "deleted there. Re-run the pipeline with shape features enabled.",
            kind="warning", msec=6000,
        )
    if unreadable:
        _logger.warning(
            "Region delete: %d well(s) could not be read: %s",
            len(unreadable), ", ".join(sorted(unreadable)),
        )
        ctx.notify.toast(
            f"{len(unreadable)} well(s) could not be read — see the log.",
            kind="warning", msec=6000,
        )


def apply_box_delete(ctx, x0: int, y0: int, x1: int, y1: int) -> None:
    """Mark every cell the drawn rectangle covers as ``Included=0``.

    Coordinates are in mask-array pixel space (already converted from
    label-local pixels by ``controller.on_review_image_release`` using the
    current ``_review_image_scale``, so window position, scroll, and zoom are
    already accounted for).
    """
    label = ctx.runtime.review_image.label
    mask = getattr(label, "_mask_arr", None) if label is not None else None
    well = ctx.state.preview.selected_well
    # These three bail-outs used to be silent. A drag that lands on nothing is
    # indistinguishable from a drag that failed, and with a wide scope armed the
    # user has every reason to expect a large edit — so say why there wasn't one.
    if mask is None or well is None:
        ctx.notify.set_status(
            "Delete: no segmentation mask is loaded for this frame — nothing "
            "to delete."
        )
        return
    h, w = int(mask.shape[0]), int(mask.shape[1])
    xa, xb = sorted((max(0, min(w, int(x0))), max(0, min(w, int(x1)))))
    ya, yb = sorted((max(0, min(h, int(y0))), max(0, min(h, int(y1)))))
    if xb - xa < 1 or yb - ya < 1:
        ctx.notify.set_status(
            "Delete: the box has no area inside the image — nothing deleted."
        )
        return
    fov_cb = (ctx.runtime.review_image.fov_menu
              or ctx.widgets.get_or_none("_preview_fov_cb"))
    if fov_cb is None:
        ctx.notify.set_status("Delete: no FOV is selected — nothing deleted.")
        return
    fov = fov_cb.currentText().strip()
    tp_cb = ctx.runtime.review_image.tp_cb
    tp = tp_cb.currentText().strip() if tp_cb is not None else ""

    scope_armed = delete_scope_is_armed(ctx)
    wells, fovs, tps = resolve_delete_scope(ctx, well, fov, tp)
    # The nucleus ID passed here is irrelevant — this only borrows CellKey's
    # normalisation of the frame's three identifying tokens.
    frame = override_key(well, fov, tp, "1")
    frame_in_scope = frame is not None and (
        frame[0] in wells and frame[1] in fovs and frame[2] in tps
    )

    items: list = []
    nids: list[int] = []
    if frame_in_scope:
        nids = [int(n) for n in _np.unique(mask[ya:yb, xa:xb]) if int(n) > 0]
        items = [(override_key(well, fov, tp, nid), "0") for nid in nids]
    targets: list = []
    stats = {"frames": 0, "wells": 0, "no_centroid_wells": [],
             "unreadable_wells": [], "unknown_wells": []}
    if scope_armed:
        # The box is drawn in this FOV's pixel space; every FOV in a dataset
        # shares image dimensions, so the same (row, col) rectangle carries.
        #
        # This runs on the UI thread, and a plate-wide scope is seconds of work
        # — the row scan plus one override projection per touched well. Until it
        # moves to a worker, at least say so: a frozen window with no cursor
        # change and no message reads as a crash.
        with _busy(ctx, len(wells)):
            targets, stats = collect_region_delete_targets(
                ctx, normalize_region(ya, xa, yb, xb),
                wells=wells, fovs=fovs, tps=tps,
                skip=frame[:3] if frame is not None else None,
            )
            items += [(key, "0") for key in targets]

    if not scope_armed:
        if not items:
            ctx.notify.set_status("Box selection: no cells inside rectangle.")
            return
        written = commit_included_overrides(ctx, items)
        ctx.notify.set_status(f"Box delete: marked {written} cell(s) Included=0.")
        return

    # The frame key is unbuildable when no timepoint is selected. That is a
    # different thing from the frame being outside the armed scope, and saying
    # the wrong one sends the user to the scope control to fix a picker.
    if frame is None:
        frame_note = "this frame has no timepoint selected, so it was skipped"
    elif frame_in_scope:
        frame_note = f"{len(nids):,} in this frame (pixel overlap)"
    else:
        frame_note = "this frame is outside the armed scope"
    written = commit_included_overrides(ctx, items)
    _region_delete_status(ctx, written, len(targets), stats, frame_note)
