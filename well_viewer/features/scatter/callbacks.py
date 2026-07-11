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

        min_dist = float('inf')
        nearest_well = None
        nearest_filename = None
        nearest_nuclear_id = None
        nearest_row_idx = None

        for x, y, meta in points:
            dx = x - event.xdata
            dy = y - event.ydata
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < min_dist:
                min_dist = dist
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

        min_dist = float('inf')
        nearest_info = None
        threshold = 0.5

        for x, y, meta in points:
            dx = x - event.xdata
            dy = y - event.ydata
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < min_dist:
                min_dist = dist
                nearest_info = meta

        if min_dist < threshold and nearest_info:
            well_label, filename, nuclear_id, row_idx = nearest_info
            tooltip_text = f"File: {filename} | Nuclear ID: {nuclear_id}"
            ctx.notify.set_status(tooltip_text)
        else:
            ctx.notify.set_status("")

    except Exception:
        swallowed()  # was: pass (silent except)
