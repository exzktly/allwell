"""Image Browser sub-tab routing: leaf resolution + lazy sub-tab construction.

Reaches the app via the ``app`` argument (never imports ``ReviewApp``). The
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

from core.safe import swallowed

# Each sub-tab's body and rail-frame builder is its descriptor's ``build``
# (``features/registry.py``), which imports the sub-tab's module only when
# called, so a build profile that drops a sub-tab — physically excluding its
# module from the binary — never imports it.

_logger = logging.getLogger("review")


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
    # Each leaf builds itself (``FeatureDescriptor.build``, declared in
    # ``features/registry.py`` with its imports inside, so a profile that strips
    # a sub-tab never imports it) — the same seam Home's container uses.
    desc = ctx.features.get(name)
    if desc is None or desc.build is None:
        return
    try:
        desc.build(ctx, frame)
        built[name] = True
    except Exception:
        _logger.exception("Image Browser: build sub-tab %r failed", name)
        return
    # This is the Image Browser's own lazy-build funnel — the sub-tab bodies do
    # not go through ``centre_view._build_pending``, so the refresh that flips a
    # newly-registered empty-state stack off page 0 has to be repeated here. A
    # sub-tab built after the dataset was loaded would otherwise sit on "Load a
    # directory." for the rest of the session, which is precisely the bug this
    # branch fixed on Spatial and smFISH.
    try:
        from review.core.ui_helpers import refresh_plot_empty_states
        refresh_plot_empty_states(ctx.window)
    except Exception:
        swallowed()


def on_image_browser_subtab_changed(ctx, idx: int) -> None:
    """Lazy-build the now-current sub-tab, then refresh the sidebar to match.

    Mirrors the Plotting sub-tab handler — the sub-tab leaf is now the effective
    centre tab, so the standard ``on_tab_change`` dispatch (sidebar swap + the
    leaf's own refresh) must re-run. Both the build and the refresh route through
    the single core ctx→app bridge, so this handler names no god-object.
    """
    build_image_browser_subtab(ctx, idx)
    from review.core.tab_controller import image_browser_subtab_app_bridge
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
