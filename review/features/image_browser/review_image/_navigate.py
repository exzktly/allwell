"""Show one field — or one cell in it — in Image Browser → Segmentation.

The one implementation of "jump to this field", shared by every surface that
offers it: the Cell Browser's cell click, the Review CSV's double-click, the
focused-cell chip and QC triage's *Open in Segmentation*. A jump to a cell makes
it the focused cell (``review/core/cell_focus.py``), and Segmentation outlines
whichever focused cell lies in the field it shows.
It lives beside the Segmentation tab rather than in either caller because
Segmentation is the one sub-tab no profile can remove, while the Cell Browser
can be stripped from a build — a caller that reached this through the Cell
Browser's package would lose it with that package.
"""

from __future__ import annotations

import logging

from review.core.ui_helpers import norm_timepoint
from review.features.image_browser.review_image.collect import review_row_keys
from review.features.image_browser.review_image.renderer import (
    refresh_review_image_ctx,
    update_preview_ctx,
    zoom_review_image_to_selected_nucleus_ctx,
)

_logger = logging.getLogger("review")


def show_in_segmentation(ctx, well: str, fov: str, timepoint: str,
                         nucleus_id: str = "") -> None:
    """Navigate to *well* / *fov* / *timepoint*, highlighting *nucleus_id* if given.

    *fov* and *timepoint* are the raw CSV tokens; the Segmentation combos hold
    normalised ones, so they are normalised here the way the Review-CSV jump
    does — passing the raw token to ``setCurrentText`` silently matches no item
    and leaves the view on the previous field.

    With a *nucleus_id* that cell becomes the focused cell. Without one the
    focus is left alone: Segmentation outlines the focused cell only in its own
    field, so a stale highlight cannot land on another field's cell of the same
    number.
    """
    from review.core.cell_focus import focus_cell

    if getattr(ctx.window, "_notebook", None) is not None:
        from review.features.image_browser.controller import (
            show_image_browser_subtab,
        )
        show_image_browser_subtab(ctx, "Segmentation")
    # Focus the cell before any refresh so intermediate renders already draw
    # the highlight.
    selected = focus_cell(ctx, well, fov, timepoint, nucleus_id) if nucleus_id else None
    # Switch wells *through* update_preview so the target well's image maps
    # are decoded and the FOV menu is repopulated — otherwise jumping to a
    # field in a different well leaves the FOV combo (and image) stale.
    ctx.state.preview.selected_well = well
    update_preview_ctx(ctx, well)
    fov_norm = review_row_keys(
        ctx, {"fov": fov, "timepoint": timepoint, "nucleus_id": nucleus_id})[0]
    tp_norm = norm_timepoint(ctx, timepoint)
    fov_cb = getattr(ctx.window, "_preview_fov_cb", None)
    if fov_cb is not None and fov_norm:
        fov_cb.setCurrentText(fov_norm)
    refresh_review_image_ctx(ctx)
    # The TP menu now holds the target FOV's timepoints; setting it fires
    # currentIndexChanged → refresh, rendering the correct frame.
    tp_cb = ctx.runtime.review_image.tp_cb
    if tp_cb is not None and tp_norm:
        tp_cb.setCurrentText(tp_norm)
    if selected is not None:
        # Centre + zoom on the highlighted cell so it's actually visible. Twice:
        # now, and again once the page switched to above has been laid out. The
        # centring reads the canvas viewport's size, and a viewport that has not
        # been laid out yet reports a sliver, which centred the cell for a
        # 100-pixel window and left it off-screen in the real one.
        zoom_review_image_to_selected_nucleus_ctx(ctx)
        from PySide6.QtCore import QTimer

        def _recentre(cell=selected):
            from review.core.cell_focus import focused_cell
            if focused_cell(ctx) == cell:
                zoom_review_image_to_selected_nucleus_ctx(ctx)

        QTimer.singleShot(0, _recentre)


def show_cell(ctx, well: str, fov: str, timepoint: str, nucleus_id: str) -> bool:
    """The ``image_browser.show_cell`` service: the field, with the cell outlined and centred."""
    try:
        show_in_segmentation(ctx, well, fov, timepoint, nucleus_id)
    except Exception as exc:
        _logger.exception("could not open %s field %s at %s in Segmentation",
                          well, fov, timepoint)
        ctx.notify.set_status(f"Could not open the cell in Segmentation: {exc}")
        return False
    return True


def show_field(ctx, well: str, fov: str, timepoint: str) -> None:
    """The ``image_browser.show_field`` service: a field, no cell highlighted."""
    try:
        show_in_segmentation(ctx, well, fov, timepoint)
    except Exception as exc:
        _logger.exception("could not open %s field %s at %s in Segmentation",
                          well, fov, timepoint)
        ctx.notify.set_status(f"Could not open the field in Segmentation: {exc}")
