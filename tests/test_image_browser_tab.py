"""Image Browser consolidation: the four image-review surfaces live under one
rail entry as sibling sub-tabs, and each resolves as a leaf tab."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.controller import show_image_browser_subtab

_SUBTABS = ("Segmentation", "Image Table", "Cell Browser", "Playground")


def test_image_browser_is_single_rail_entry(viewer_app):
    app = viewer_app
    names = app._notebook.pageNames()
    assert "Image Browser" in names
    # The former top-level image tabs are gone from the rail — they're sub-tabs.
    for leaf in _SUBTABS:
        assert leaf not in names
    # The rail entry carries the section icon.
    assert app._SECTION_ICONS.get("Image Browser")
    # It is NOT lazy-only: like the old Image Table / Segmentation tabs it
    # drains and eagerly builds its Segmentation + Image Table sub-tabs (the
    # heavier Cell Browser / Playground sub-tabs stay lazy). smFISH stays its
    # own separate, lazy-only rail entry (not an image-review surface).
    assert "Image Browser" not in app._centre_lazy_only_titles
    assert "smFISH" in names
    assert "smFISH" in app._centre_lazy_only_titles


def test_subtabs_resolve_as_leaf_tabs(viewer_app):
    app = viewer_app
    for leaf in _SUBTABS:
        assert show_image_browser_subtab(app, leaf), f"could not open {leaf}"
        assert app._current_centre_tab() == leaf


def test_playground_is_flat_sibling_not_nested_in_segmentation(viewer_app):
    app = viewer_app
    show_image_browser_subtab(app, "Playground")
    # The old Segmentation sub-stack (Review Image + Playground) is gone.
    assert not hasattr(app, "_segmentation_subtabs")
    assert not hasattr(app, "_playground_subtab_frame")
    # Playground is one of the Image Browser sub-tabs.
    sub = app._image_browser_subtabs
    assert "Playground" in [sub.tabText(i) for i in range(sub.count())]
