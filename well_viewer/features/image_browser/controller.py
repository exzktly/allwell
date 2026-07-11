"""Image Browser sub-tab routing: leaf resolution + lazy sub-tab construction.

Reaches the app via the ``app`` argument (never imports ``WellViewerApp``). The
container (``tab_view``) owns the ``QTabWidget``; this module builds the sub-tab
body (and its left sidebar) on first show and re-runs the standard tab-change
dispatch so the sidebar swaps to match the active leaf.

The sub-tab leaf name ("Segmentation") is exactly the name
``core.tab_controller.on_tab_change`` already dispatches on;
``current_centre_tab`` descends into this ``QTabWidget`` so that leaf dispatch
keeps working unchanged.
"""

from __future__ import annotations

import logging

from well_viewer.core.safe import swallowed

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
