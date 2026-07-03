"""Image Browser sub-tab routing: leaf resolution + lazy sub-tab construction.

Reaches the app via the ``app`` argument (never imports ``WellViewerApp``). The
container (``tab_view``) owns the ``QTabWidget``; this module builds each sub-tab
body (and its left sidebar, where it has one) on first show and re-runs the
standard tab-change dispatch so the sidebar swaps to match the active leaf.

The sub-tab leaf names ("Segmentation" / "Image Table" / "Cell Browser" /
"Playground") are exactly the names ``core.tab_controller.on_tab_change`` already
dispatches on; ``current_centre_tab`` descends into this ``QTabWidget`` so that
leaf dispatch keeps working unchanged.
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QHBoxLayout

from well_viewer.core.safe import swallowed
# Sub-tab body/sidebar builders. Module-scope (not deferred): these aren't a
# cycle break — they only resolve when the Image Browser page is first built
# (centre_view imports this package lazily), so app-startup laziness is
# preserved while each sub-tab's *widget construction* still happens on demand.
from well_viewer.features.preview.panel_view import build_review_image_panel
from well_viewer.features.image_browser.image_table.tab_view import build_image_table_tab
from well_viewer.features.image_browser.image_table.picker_view import build_image_table_picker
from well_viewer.features.image_browser.cell_browser.tab_view import (
    build_cell_browser_sidebar,
    build_cell_browser_tab,
)
from well_viewer.features.image_browser.playground.tab_view import build_playground_tab

_logger = logging.getLogger("well_viewer")


def image_browser_current_subtab(app) -> str:
    """Leaf name of the active Image Browser sub-tab ("" when none/unbuilt)."""
    sub = getattr(app, "_image_browser_subtabs", None)
    if sub is None or sub.count() == 0:
        return ""
    try:
        return sub.tabText(sub.currentIndex())
    except Exception:
        swallowed()
        return ""


def build_image_browser_subtab(app, idx: int) -> None:
    """Build the sub-tab body (+ its sidebar) at *idx* on first show.

    Idempotent: the per-name ``app._image_browser_built`` flag guards against
    rebuilding when the user flips back to an already-constructed sub-tab.
    """
    sub = getattr(app, "_image_browser_subtabs", None)
    if sub is None or idx < 0 or idx >= sub.count():
        return
    name = sub.tabText(idx)
    built = app._image_browser_built
    if built.get(name):
        return
    frame = sub.widget(idx)
    ctx = app._ctx
    try:
        if name == "Segmentation":
            build_review_image_panel(app, frame)
            # The review-image channel combo is born empty (just a "GFP"
            # placeholder); when the panel builds *after* a dataset is already
            # loaded (it builds lazily now), nothing has populated it yet, so
            # fill it from the current channel state. Harmless before any load.
            from well_viewer.core.channel_state_controller import update_channel_selector
            update_channel_selector(app)
        elif name == "Image Table":
            build_image_table_tab(ctx, frame)
            build_image_table_picker(ctx, app._sidebar_image_table_frame)
        elif name == "Cell Browser":
            build_cell_browser_tab(ctx, frame)
            build_cell_browser_sidebar(ctx, app._sidebar_cell_browser_frame)
        elif name == "Playground":
            if frame.layout() is None:
                QHBoxLayout(frame).setContentsMargins(12, 12, 12, 12)
            build_playground_tab(ctx, frame)
        else:
            return
        built[name] = True
    except Exception:
        _logger.exception("Image Browser: build sub-tab %r failed", name)


def on_image_browser_subtab_changed(app, idx: int) -> None:
    """Lazy-build the now-current sub-tab, then refresh the sidebar to match.

    Mirrors the Plotting sub-tab handler — the sub-tab leaf is now the effective
    centre tab, so the standard ``on_tab_change`` dispatch (sidebar swap + the
    leaf's own refresh) must re-run.
    """
    build_image_browser_subtab(app, idx)
    from well_viewer.core.tab_controller import on_tab_change
    on_tab_change(app, None)


def show_image_browser_subtab(app, name: str) -> bool:
    """Navigate to the Image Browser rail entry and select sub-tab *name*.

    The single entry point cross-feature jump-to-image navigation uses (Review
    CSV double-click, Cell Browser cell-click) now that the image-review
    surfaces are sub-tabs rather than top-level pages.
    """
    nb = getattr(app, "_notebook", None)
    if nb is None:
        return False
    nb.setCurrentByName("Image Browser")
    sub = getattr(app, "_image_browser_subtabs", None)
    if sub is None:
        return False
    for i in range(sub.count()):
        if sub.tabText(i) == name:
            sub.setCurrentIndex(i)
            return True
    return False
