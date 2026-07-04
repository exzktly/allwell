"""Image Browser consolidation: the four image-review surfaces live under one
rail entry as sibling sub-tabs, and each resolves as a leaf tab."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.controller import (
    build_image_browser_subtab,
    on_image_browser_subtab_changed,
    show_image_browser_subtab,
)

_SUBTABS = ("Segmentation", "Image Table", "Cell Browser", "Playground")


def _build_image_browser_parent(app):
    """Force the lazy Image Browser container to build (Segmentation eager)."""
    build_pending = getattr(app, "_centre_build_pending", None)
    if build_pending is not None:
        build_pending("Image Browser")
    sub = app._ctx.runtime.image_browser.subtabs
    assert sub is not None, "Image Browser parent tab was not built"
    return sub


def test_subtab_chain_is_ctx_native_and_builds_each_body(viewer_app):
    """Characterization: the ctx-native sub-tab lifecycle builds each body and
    records it in ``ctx.runtime.image_browser.built``, and the Segmentation
    branch materialises the review-image canvas via the core app bridge.

    Guards the D5 ``ctx.app`` decomposition: the controller now reads the sub-tab
    bar / built-map off ``ctx.runtime`` and the sidebar frames off ``ctx.widgets``
    — a mis-mapped attr would surface here as an unbuilt body / missing widget.
    """
    app = viewer_app
    ctx = app._ctx
    sub = _build_image_browser_parent(app)

    # The two sidebar frames the controller reads back are registered (and still
    # stamped onto the app for the legacy visibility readers).
    for name in ("_sidebar_image_table_frame", "_sidebar_cell_browser_frame"):
        assert ctx.widgets.has(name), f"{name} not registered"
        assert getattr(app, name, None) is ctx.widgets.get(name)

    # Eager Segmentation build (mirrors tab_view's initial build) went through the
    # ctx-native path and produced the review-image canvas + channel combo.
    assert ctx.runtime.image_browser.built.get("Segmentation") is True
    assert hasattr(app, "_review_image_label")
    assert hasattr(app, "_review_image_chan_cb")

    # Navigate the rail to Image Browser so its active sub-tab is the effective
    # centre leaf (current_centre_tab descends into the sub-stack only for the
    # current top-level page).
    app._notebook.setCurrentByName("Image Browser")

    # Drive each remaining sub-tab through the ctx-native change handler (the same
    # call tab_view wires to currentChanged) and confirm each body builds once.
    for idx in range(sub.count()):
        name = sub.tabText(idx)
        sub.setCurrentIndex(idx)
        on_image_browser_subtab_changed(ctx, idx)
        assert ctx.runtime.image_browser.built.get(name) is True, (
            f"{name} sub-tab body did not build"
        )
        # The change handler re-runs the standard leaf dispatch, so the effective
        # centre tab tracks the active sub-tab.
        assert app._current_centre_tab() == name

    # Idempotent: rebuilding an already-built sub-tab is a no-op (no raise).
    build_image_browser_subtab(ctx, 0)


def test_image_browser_is_single_rail_entry(viewer_app):
    app = viewer_app
    names = app._notebook.pageNames()
    assert "Image Browser" in names
    # The former top-level image tabs are gone from the rail — they're sub-tabs.
    for leaf in _SUBTABS:
        assert leaf not in names
    # The rail entry carries the section icon.
    assert app._SECTION_ICONS.get("Image Browser")
    # It IS lazy-only: the Image Browser container (and all its sub-tabs) is
    # deferred until the user first clicks the rail entry, so it does not drain
    # at load time. smFISH is also lazy.
    assert "Image Browser" in app._centre_lazy_only_titles
    assert "smFISH" in names
    assert "smFISH" in app._centre_lazy_only_titles


def test_subtabs_resolve_as_leaf_tabs(viewer_app):
    app = viewer_app
    for leaf in _SUBTABS:
        assert show_image_browser_subtab(app._ctx, leaf), f"could not open {leaf}"
        assert app._current_centre_tab() == leaf


def test_playground_is_flat_sibling_not_nested_in_segmentation(viewer_app):
    app = viewer_app
    show_image_browser_subtab(app._ctx, "Playground")
    # The old Segmentation sub-stack (Review Image + Playground) is gone.
    assert not hasattr(app, "_segmentation_subtabs")
    assert not hasattr(app, "_playground_subtab_frame")
    # Playground is one of the Image Browser sub-tabs.
    sub = app._image_browser_subtabs
    assert "Playground" in [sub.tabText(i) for i in range(sub.count())]
