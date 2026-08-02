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

import numpy as _np

from well_viewer.core.safe import swallowed
from well_viewer.features.crop.region import normalize_region
from well_viewer.features.image_browser.review_image.collect import (
    apply_review_overrides_to_cache, collect_region_delete_targets,
)
from well_viewer.features.image_browser.review_image.renderer import (
    refresh_review_image_ctx, render_review_image_display,
)

_logger = logging.getLogger(__name__)


def override_key(well: str, fov: object, tp: object, nid: object):
    """Canonical ``(well, fov, tp, nid)`` override-map key.

    Built through :class:`~well_viewer.data.image_identity.CellKey`, the same
    constructor ``cell_overrides.json`` is read with and that
    ``collect.review_row_keys_series`` uses to look overrides back up when
    projecting them onto the row cache — so the write, the read and the on-disk
    form cannot drift apart. Returns ``None`` when any component normalises away
    to empty, which means the row cannot be identified and must not be written.
    """
    from well_viewer.data.image_identity import CellKey
    key = CellKey.from_row(
        {"fov": fov, "timepoint": tp, "nucleus_id": nid}, well=well,
    )
    if not (key.well and key.fov and key.tp and key.nid):
        return None
    return key.as_tuple()


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
    written = 0
    for key, included in items or ():
        if key is None:
            continue
        ctx.runtime.review.included_overrides[key] = str(included).strip() or "1"
        wells.add(key[0])
        written += 1
    if not written:
        return 0
    for well in wells:
        apply_review_overrides_to_cache(ctx, well)
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

    Returns ``(wells, fovs, tps)``, normalised the same way the override keys
    are. Each armed field that is ``None`` — the default — resolves to the frame
    currently on screen, so an un-armed scope means "just this frame" and the
    delete behaves exactly as it always has. An armed field the user emptied
    resolves to an empty set, which selects nothing rather than everything.
    """
    from well_viewer.data.image_identity import (
        normalize_fov, normalize_tp, normalize_well,
    )
    ri = ctx.state.review_image
    wells = ({normalize_well(well)} if ri.delete_scope_wells is None
             else {normalize_well(w) for w in ri.delete_scope_wells})
    fovs = ({normalize_fov(fov)} if ri.delete_scope_fovs is None
            else {normalize_fov(f) for f in ri.delete_scope_fovs})
    tps = ({normalize_tp(tp)} if ri.delete_scope_tps is None
           else {normalize_tp(t) for t in ri.delete_scope_tps})
    return wells, fovs, tps


def _region_delete_status(ctx, n_frame: int, n_scoped: int, stats: dict,
                          frame_in_scope: bool) -> None:
    """Report what the delete actually did, including everything it could not do.

    The two halves are counted separately because different rules decide them —
    the drawn frame by pixel overlap against its label mask, every other frame by
    centroid — and someone comparing the visible result against a neighbouring
    well needs to know that. Wells that could not be judged at all are named
    rather than folded silently into a smaller total.
    """
    parts = [
        f"{n_frame:,} in this frame (pixel overlap)" if frame_in_scope
        else "this frame is outside the armed scope",
        f"{n_scoped:,} across {stats['frames']:,} other frame(s) in "
        f"{stats['wells']:,} well(s) (centroid inside)",
    ]
    ctx.notify.set_status(
        f"Region delete: {n_frame + n_scoped:,} cell(s) Included=0 — "
        + "; ".join(parts) + "."
    )
    skipped = list(stats.get("no_centroid_wells") or ())
    unreadable = list(stats.get("unreadable_wells") or ())
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
    if mask is None or well is None:
        return
    h, w = int(mask.shape[0]), int(mask.shape[1])
    xa, xb = sorted((max(0, min(w, int(x0))), max(0, min(w, int(x1)))))
    ya, yb = sorted((max(0, min(h, int(y0))), max(0, min(h, int(y1)))))
    if xb - xa < 1 or yb - ya < 1:
        return
    fov_cb = (ctx.runtime.review_image.fov_menu
              or ctx.widgets.get_or_none("_preview_fov_cb"))
    if fov_cb is None:
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
    stats = {"frames": 0, "wells": 0,
             "no_centroid_wells": [], "unreadable_wells": []}
    if scope_armed:
        # The box is drawn in this FOV's pixel space; every FOV in a dataset
        # shares image dimensions, so the same (row, col) rectangle carries.
        targets, stats = collect_region_delete_targets(
            ctx, normalize_region(ya, xa, yb, xb),
            wells=wells, fovs=fovs, tps=tps,
            skip=frame[:3] if frame is not None else None,
        )
        items += [(key, "0") for key in targets]

    if not items:
        if scope_armed:
            _region_delete_status(ctx, 0, 0, stats, frame_in_scope)
        else:
            ctx.notify.set_status("Box selection: no cells inside rectangle.")
        return
    commit_included_overrides(ctx, items)
    if scope_armed:
        _region_delete_status(ctx, len(nids), len(targets), stats, frame_in_scope)
    else:
        ctx.notify.set_status(
            f"Box delete: marked {len(nids)} cell(s) Included=0."
        )
