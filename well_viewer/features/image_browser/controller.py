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
# (Segmentation's ``build_review_image_panel`` is a god-method that still needs
# the app handle, so it is reached through the core ``image_browser_subtab_app_bridge``
# rather than named here — this controller stays fully ctx-native.)
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
    sub = app._ctx.runtime.image_browser.subtabs
    if sub is None or sub.count() == 0:
        return ""
    try:
        return sub.tabText(sub.currentIndex())
    except Exception:
        swallowed()
        return ""


def build_image_browser_subtab(ctx, idx: int) -> None:
    """Build the sub-tab body (+ its sidebar) at *idx* on first show.

    Idempotent: the per-name ``ctx.runtime.image_browser.built`` flag guards
    against rebuilding when the user flips back to an already-constructed sub-tab.

    Fully ctx-native: the sub-tab bar / built-flag map come from
    ``ctx.runtime.image_browser`` and the two sidebar frames from ``ctx.widgets``;
    the Segmentation branch's app-native review-image build is reached through the
    single core ``image_browser_subtab_app_bridge`` seam.
    """
    sub = ctx.runtime.image_browser.subtabs
    if sub is None or idx < 0 or idx >= sub.count():
        return
    name = sub.tabText(idx)
    built = ctx.runtime.image_browser.built
    if built.get(name):
        return
    frame = sub.widget(idx)
    try:
        if name == "Segmentation":
            from well_viewer.core.tab_controller import image_browser_subtab_app_bridge
            image_browser_subtab_app_bridge(ctx, segmentation_frame=frame)
        elif name == "Image Table":
            build_image_table_tab(ctx, frame)
            build_image_table_picker(ctx, ctx.widgets.get("_sidebar_image_table_frame"))
        elif name == "Cell Browser":
            build_cell_browser_tab(ctx, frame)
            build_cell_browser_sidebar(ctx, ctx.widgets.get("_sidebar_cell_browser_frame"))
        elif name == "Playground":
            if frame.layout() is None:
                QHBoxLayout(frame).setContentsMargins(12, 12, 12, 12)
            build_playground_tab(ctx, frame)
        else:
            return
        built[name] = True
    except Exception:
        _logger.exception("Image Browser: build sub-tab %r failed", name)


def on_image_browser_subtab_changed(ctx, idx: int) -> None:
    """Lazy-build the now-current sub-tab, then refresh the sidebar to match.

    Mirrors the Plotting sub-tab handler — the sub-tab leaf is now the effective
    centre tab, so the standard ``on_tab_change`` dispatch (sidebar swap + the
    leaf's own refresh) must re-run. Both the build and the refresh route through
    the single core ctx→app bridge, so this handler names no god-object.
    """
    build_image_browser_subtab(ctx, idx)
    from well_viewer.core.tab_controller import image_browser_subtab_app_bridge
    image_browser_subtab_app_bridge(ctx, refresh_active_tab=True)


def show_image_browser_subtab(ctx, name: str) -> bool:
    """Navigate to the Image Browser rail entry and select sub-tab *name*.

    The single entry point cross-feature jump-to-image navigation uses (Review
    CSV double-click, Cell Browser cell-click) now that the image-review
    surfaces are sub-tabs rather than top-level pages.
    """
    nb = getattr(ctx.window, "_notebook", None)
    if nb is None:
        return False
    nb.setCurrentByName("Image Browser")
    sub = ctx.runtime.image_browser.subtabs
    if sub is None:
        return False
    for i in range(sub.count()):
        if sub.tabText(i) == name:
            sub.setCurrentIndex(i)
            return True
    return False
