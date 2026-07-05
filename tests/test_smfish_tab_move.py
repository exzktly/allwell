"""smFISH is now its own top-level rail tab (promoted out of the Segmentation
sub-stack); Segmentation is a single review-image panel.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_smfish_is_top_level_tab(viewer_app):
    app = viewer_app
    names = app._notebook.pageNames()
    assert "smFISH" in names, "smFISH should be its own top-level tab"
    # Segmentation is now a sub-tab of the unified Image Browser rail entry.
    assert "Image Browser" in names
    assert "Segmentation" not in names
    # Its own rail icon.
    assert app._SECTION_ICONS.get("smFISH") == "dna"
    # Heavy deps → lazy-only (builds on first click, not during the drain).
    assert "smFISH" in app._centre_lazy_only_titles
    # The old Segmentation sub-stack is gone.
    assert not hasattr(app, "_cell_segmentation_notebook")


def test_segmentation_and_smfish_resolve_as_leaf_tabs(viewer_app):
    app = viewer_app
    # Segmentation resolves as a leaf via the Image Browser sub-tab descent.
    from well_viewer.features.image_browser.controller import show_image_browser_subtab
    assert show_image_browser_subtab(app._ctx, "Segmentation")
    assert app._current_centre_tab() == "Segmentation"
    app._notebook.setCurrentByName("smFISH")
    assert app._current_centre_tab() == "smFISH"
