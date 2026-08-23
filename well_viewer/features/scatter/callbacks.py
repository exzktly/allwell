"""Scatter plot canvas click / hover handlers.

The single-cell image viewer they open lives in ``cell_viewer_view.py`` (audit
A7); these handlers resolve which cell a canvas click/hover landed on and route
to the ctx-native ``open_scatter_cell_viewer`` opener there. ``cells_tab_view``
wires them to the canvas events.
"""

from __future__ import annotations

from well_viewer.core.safe import swallowed
from well_viewer.features.scatter.cell_viewer_view import open_scatter_cell_viewer
from well_viewer.features.scatter.gating import gate_edge_at


# ── Canvas pick handlers ─────────────────────────────────────────────────────
# Click/hover handlers for the per-cell scatter canvas live here (next to the
# viewer they open) rather than in controller.py, which stays focused on the
# render pipeline. cells_tab_view wires these to the canvas events.


def _points_for_event(ctx, event) -> list:
    """Return the click-interaction points for the panel under *event*.

    Overlay mode has a single panel; the "Separate axes" layout stacks one
    panel per group with shared x/y axes, so a global nearest-search would be
    ambiguous. ``axes_points`` pairs each panel's points with its axis, letting
    both layouts resolve the panel actually clicked/hovered. Falls back to the
    flat ``points`` list (keyed to the primary axis) for any cache that predates
    ``axes_points``."""
    cache = ctx.runtime.scatter.interaction_cache or {}
    axes_points = cache.get("axes_points")
    if axes_points:
        for ax, pts in axes_points:
            if event.inaxes is ax:
                return pts
        return []
    if event.inaxes is ctx.widgets.get_or_none("_ax_scatter"):
        return cache.get("points", [])
    return []


#: How near, in screen pixels, the cursor has to be for a hover to count as
#: landing on a point. A pixel radius rather than a data-space one because
#: that is the distance the user can actually see.
_HOVER_RADIUS_PX = 12.0


def _nearest_point(event, points):
    """The point nearest *event* in **screen space**, and how far away it is.

    Returns ``(meta, distance_in_pixels)``, or ``(None, inf)``.

    The search used to run in raw data space: ``hypot(x - event.xdata,
    y - event.ydata)`` over two axes that hold different quantities on
    different scales — a GFP intensity in the thousands against an mCherry one
    in the hundreds, and either of them possibly drawn on a log axis. Adding
    those two differences under one square root is a sum of incommensurate
    units, so "nearest" was decided almost entirely by whichever axis had the
    larger numbers, and a point sitting under the cursor could lose to one
    inches away. The hover's ``0.5`` cut-off had the same problem twice over:
    half a data unit is most of one axis and invisible on the other.

    ``ax.transData`` answers the question the user is asking — which dot is
    under the pointer — and answers it identically whichever scale or units the
    axes carry.
    """
    ax = getattr(event, "inaxes", None)
    if ax is None or not points:
        return None, float("inf")
    ex, ey = getattr(event, "x", None), getattr(event, "y", None)
    if ex is None or ey is None:
        return None, float("inf")
    import numpy as np

    coords = np.asarray([(x, y) for x, y, _meta in points], dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        px = ax.transData.transform(coords)
    d = np.hypot(px[:, 0] - float(ex), px[:, 1] - float(ey))
    finite = np.isfinite(d)
    if not finite.any():
        return None, float("inf")
    d = np.where(finite, d, np.inf)
    i = int(np.argmin(d))
    return points[i][2], float(d[i])


def on_scatter_click(ctx, event) -> None:
    """Handle click events on scatter plot datapoints."""
    if event.xdata is None or event.ydata is None or event.button != 1:
        return

    # Drawing/editing a gate owns its own press — skip the cell viewer for those.
    if ctx.runtime.scatter.gate_draw_mode or gate_edge_at(ctx, event) is not None:
        return

    try:
        points = _points_for_event(ctx, event)
        if not points:
            return

        # A click has no radius: the user aimed at the panel and wants the
        # closest cell in it, however far that is. Only the ordering had to be
        # fixed here, not a cut-off.
        meta, _dist = _nearest_point(event, points)
        if meta is None:
            return
        nearest_well, nearest_filename, nearest_nuclear_id, nearest_row_idx = meta

        if nearest_well is not None:
            open_scatter_cell_viewer(
                ctx, nearest_well, nearest_filename, nearest_nuclear_id, nearest_row_idx)

    except Exception as e:
        # The user clicked expecting a popup -- surface the failure instead of
        # swallowing it into a fleeting status line.
        import logging
        import traceback
        from PySide6.QtWidgets import QMessageBox
        logging.getLogger(__name__).exception("Error handling scatter-plot cell click")
        tb = traceback.format_exc()
        ctx.notify.set_status(f"Could not open cell viewer: {e}")
        try:
            box = QMessageBox(ctx.window)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Cell viewer error")
            box.setText("Could not open the cell-image viewer for the clicked point.")
            box.setInformativeText(str(e))
            box.setDetailedText(tb)
            box.exec()
        except Exception:
            swallowed()  # was: pass (silent except)


def on_scatter_motion(ctx, event) -> None:
    """Handle hover events on scatter plot to show tooltips."""
    if event.xdata is None or event.ydata is None:
        return

    try:
        points = _points_for_event(ctx, event)
        if not points:
            return

        nearest_info, min_dist = _nearest_point(event, points)

        # The status line is one shared slot, and everything else that writes
        # to it — the fold-change warnings, the load summary, a failed export —
        # is meant to stay readable. Blanking it on every hover miss wiped
        # those the instant the cursor crossed a scatter axis without landing
        # on a point, which is most of the axis. Clear it only when the thing
        # standing there is a tooltip this handler put there.
        rt = ctx.runtime.scatter
        if min_dist <= _HOVER_RADIUS_PX and nearest_info:
            well_label, filename, nuclear_id, row_idx = nearest_info
            tooltip_text = f"File: {filename} | Nuclear ID: {nuclear_id}"
            ctx.notify.set_status(tooltip_text)
            rt.hover_status = tooltip_text
        elif getattr(rt, "hover_status", None):
            ctx.notify.set_status("")
            rt.hover_status = ""

    except Exception:
        swallowed()  # was: pass (silent except)
