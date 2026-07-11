"""Image Browser: the Segmentation review-image surface lives under one rail
entry as a sub-tab and resolves as a leaf tab.

The thin build keeps only Segmentation (Image Table / Cell Browser / Playground
were stripped); this guards that the single-sub-tab container still builds, its
body materialises via the core app bridge, and it resolves as the effective
centre leaf.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.controller import (
    build_image_browser_subtab,
    on_image_browser_subtab_changed,
    show_image_browser_subtab,
)

_SUBTABS = ("Segmentation",)


def test_segmentation_is_the_only_subtab():
    from well_viewer.features.image_browser.tab_view import SUBTABS
    assert SUBTABS == ("Segmentation",)


def _build_image_browser_parent(app):
    """Force the lazy Image Browser container to build (Segmentation eager)."""
    build_pending = app._ctx.runtime.centre.build_pending
    if build_pending is not None:
        build_pending("Image Browser")
    sub = app._ctx.runtime.image_browser.subtabs
    assert sub is not None, "Image Browser parent tab was not built"
    return sub


def test_subtab_chain_is_ctx_native_and_builds_the_body(viewer_app):
    """The ctx-native sub-tab lifecycle builds the Segmentation body and records
    it in ``ctx.runtime.image_browser.built``; the branch materialises the
    review-image canvas via the core app bridge."""
    app = viewer_app
    ctx = app._ctx
    sub = _build_image_browser_parent(app)

    # Eager Segmentation build (mirrors tab_view's initial build) went through the
    # ctx-native path and produced the review-image canvas + channel combo.
    assert ctx.runtime.image_browser.built.get("Segmentation") is True
    assert app._ctx.runtime.review_image.label is not None
    assert hasattr(app, "_review_image_chan_cb")

    app._notebook.setCurrentByName("Image Browser")

    # Drive each sub-tab through the ctx-native change handler and confirm the
    # body builds and the effective centre leaf tracks the active sub-tab.
    for idx in range(sub.count()):
        name = sub.tabText(idx)
        sub.setCurrentIndex(idx)
        on_image_browser_subtab_changed(ctx, idx)
        assert ctx.runtime.image_browser.built.get(name) is True, (
            f"{name} sub-tab body did not build"
        )
        assert app._current_centre_tab() == name

    # Idempotent: rebuilding an already-built sub-tab is a no-op (no raise).
    build_image_browser_subtab(ctx, 0)


def test_image_browser_is_single_rail_entry(viewer_app):
    app = viewer_app
    names = app._notebook.pageNames()
    assert "Image Browser" in names
    # The image-review surfaces are sub-tabs, never top-level rail entries.
    for leaf in _SUBTABS:
        assert leaf not in names
    assert app._SECTION_ICONS.get("Image Browser")
    # It is lazy-only: the container (and its sub-tab) is deferred until first
    # click, so it does not drain at load time.
    assert "Image Browser" in app._centre_lazy_only_titles


def test_subtabs_resolve_as_leaf_tabs(viewer_app):
    app = viewer_app
    for leaf in _SUBTABS:
        assert show_image_browser_subtab(app._ctx, leaf), f"could not open {leaf}"
        assert app._current_centre_tab() == leaf
