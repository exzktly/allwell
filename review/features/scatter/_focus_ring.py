"""The focused cell's ring on the per-cell Scatter (tracker F5).

Every drawn cell point carries ``(well, filename, nucleus_id, row)`` in the
interaction cache the click handler reads. The focused cell
(``review/core/cell_focus.py``) is found among them by well and nucleus id, and
then by its row's field and timepoint, since nucleus ids repeat across fields
and timepoints. Painted after each redraw and again whenever the focus moves,
without redrawing the cloud. A cell that is not among the drawn points — not
selected, outside the timepoints, below an axis floor, or not in a sample —
gets no ring; the status-bar chip still names it.
"""

from __future__ import annotations

from core.safe import swallowed
from review.core.cell_picking import RING_GID, clear_rings, draw_ring

__all__ = ["RING_GID", "focused_points", "paint_focus_ring", "store_interaction_points"]


def focused_points(ctx, axes_points) -> list[tuple[object, float, float]]:
    """``(axes, x, y)`` of every drawn point that is the focused cell."""
    from review.core.cell_focus import focused_cell, same_frame
    from review.data.data_loading import get_rows

    cell = focused_cell(ctx)
    if cell is None:
        return []
    rows_by_well: dict = {}
    out = []
    for ax, pts in axes_points or ():
        for x, y, meta in pts or ():
            try:
                well, _filename, nid, row = meta
                if str(well) != cell.well or int(float(nid)) != cell.nucleus_id:
                    continue
            except (TypeError, ValueError):
                continue
            rows = rows_by_well.get(well)
            if rows is None:
                rows = rows_by_well[well] = get_rows(ctx, well)
            if row is None or not 0 <= int(row) < len(rows):
                continue
            r = rows.iloc[int(row)]
            if same_frame(cell, well, str(r.get("fov", "")), str(r.get("timepoint", ""))):
                out.append((ax, float(x), float(y)))
    return out


def paint_focus_ring(ctx, *, draw: bool = True) -> int:
    """Remove the old ring and ring the focused cell's points; return how many."""
    cache = ctx.runtime.scatter.interaction_cache or {}
    axes_points = cache.get("axes_points") or []
    for ax, _pts in axes_points:
        clear_rings(ax)
    try:
        hits = focused_points(ctx, axes_points)
    except Exception:
        swallowed("scatter: locating the focused cell")
        hits = []
    for ax, x, y in hits:
        draw_ring(ax, x, y)
    if draw:
        canvas = ctx.widgets.get_or_none("_scatter_canvas")
        if canvas is not None:
            canvas.draw_idle()
    return len(hits)


def store_interaction_points(ctx, cache: dict) -> None:
    """Keep a redraw's click-interaction points and ring the focused cell among them.

    The one place a redraw files its points, so a layout cannot file them and
    forget the ring; the canvas is drawn by the redraw itself.
    """
    ctx.runtime.scatter.interaction_cache = cache
    paint_focus_ring(ctx, draw=False)
