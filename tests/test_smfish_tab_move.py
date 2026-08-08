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


def test_smfish_shows_the_shared_empty_state_before_a_dataset_loads(viewer_app, qtbot):
    """The no-dataset placeholder must be the one every other plot tab shows.

    smFISH used to paint its own warning as matplotlib text on the image axes —
    a muted string with no glyph and no "Open…" hint — so the one tab a user
    might land on first looked unlike every other tab in the same state. It is
    the only plot tab that never wrapped itself in the shared EmptyState; the
    on-canvas messages it keeps are for states *within* a loaded dataset, which
    is where the other tabs paint on the axes too.
    """
    from PySide6.QtWidgets import QLabel, QStackedWidget, QWidget

    from well_viewer.core.ui_helpers import refresh_plot_empty_states
    from well_viewer.features.smfish.tab_view import build_smfish_tab
    from widgets.empty_state import EmptyState

    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_smfish_tab(app._ctx, parent)
    refresh_plot_empty_states(app)

    canvas = app._ctx.widgets.get_or_none("_smfish_canvas_img")
    assert canvas is not None
    stack = canvas
    while stack is not None and not (
        isinstance(stack, QStackedWidget) and stack.objectName() == "PlotEmptyStateStack"
    ):
        stack = stack.parentWidget()
    assert stack is not None, "the smFISH canvas is not inside an empty-state stack"

    placeholder = stack.widget(0)
    assert isinstance(placeholder, EmptyState)
    assert stack.currentIndex() == 0, "no dataset is loaded, so the placeholder should show"

    # Word-for-word what the other tabs show — wrap_with_empty_state's defaults.
    texts = tuple(lbl.text() for lbl in placeholder.findChildren(QLabel) if lbl.text())
    assert "Load a directory." in texts
    assert any("Open…" in t for t in texts)

    # The toolbar swaps out with the canvas. Wrapping the canvas alone would
    # leave a live-looking toolbar stranded under the placeholder; every other
    # tab wraps a PlotCard, which carries its own.
    toolbar = app._ctx.widgets.get_or_none("_smfish_toolbar")
    host, w = stack.widget(1), toolbar
    while w is not None and w is not host:
        w = w.parentWidget()
    assert w is host, "the smFISH toolbar is outside the empty-state stack"
