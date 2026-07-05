"""Characterization: focusing the Cell Browser pane must NOT change the selection.

Reproduces the reported bug: in Split View [Scatter Plot + Cell Browser] with
groups defined (and after a scatter gate), merely clicking into / focusing the
Cell Browser pane silently toggles ``selected_wells`` to a different set — the
user never touched a well. The mutation is a feedback loop: the focus change
repaints the sidebar plate programmatically (``refresh_sidebar_map_now`` →
``setSelectedWellIds``), the plate re-emits ``selectionChanged``, and
``on_sidebar_plate_selection_changed`` commits the echoed (filtered) set.

These tests print the exact before/enabled/echoed/after values so the precise
mismatch is confirmed empirically, then assert the selection is preserved.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _enter_scatter_cellbrowser_split(app):
    """Real two-pane split: Scatter Plot (Plotting) + Cell Browser (Image Browser)."""
    from well_viewer.features.image_browser.controller import show_image_browser_subtab
    from well_viewer.core.split_view import enter_split

    # Build the Cell Browser sub-tab (lazy) and make it the active Image Browser leaf.
    assert show_image_browser_subtab(app._ctx, "Cell Browser")
    # Make Scatter Plot the active Plotting leaf.
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")
    app._notebook.setCurrentByName("Plotting")
    enter_split(app, "Plotting", "Image Browser")
    assert app._is_tab_visible("Cell Browser")
    assert "Scatter Plot" in app._visible_centre_tabs()


def _focus(app, top):
    from well_viewer.core.split_view import focus_split
    focus_split(app, top)
    app._refresh_sidebar_map_now()  # drain the deferred sidebar repaint synchronously


def test_focusing_cell_browser_preserves_selection(loaded_app):
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    assert len(wells) >= 2, "mini dataset should expose at least two wells"
    g_wells = wells[:1]          # the group covers well[0]
    user_sel = set(wells[:2])    # the user's individual selection: two wells

    _enter_scatter_cellbrowser_split(app)
    # Groups defined → the Scatter plate runs in rep-mode while Scatter is focused.
    app._sel_add(name="g1", wells=g_wells)
    app._app_state.selection.selected_wells = set(user_sel)

    _focus(app, "Plotting")          # focus the Scatter pane
    before = set(app._app_state.selection.selected_wells)
    enabled = list(app._app_state.dataset.well_paths.keys())

    _focus(app, "Image Browser")     # focus the Cell Browser pane — the bug trigger
    after = set(app._app_state.selection.selected_wells)

    print(f"\n[focus-sync] group={g_wells} user_sel={sorted(user_sel)}")
    print(f"[focus-sync] before_focus_cellbrowser={sorted(before)}")
    print(f"[focus-sync] enabled_wells={len(enabled)} after={sorted(after)}")

    assert after == before, (
        f"Focusing the Cell Browser changed the selection: {sorted(before)} -> "
        f"{sorted(after)} (no well was clicked)")


def test_focus_after_gate_preserves_selection(loaded_app):
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    assert len(wells) >= 2
    _enter_scatter_cellbrowser_split(app)
    app._sel_add(name="g1", wells=wells[:1])
    user_sel = set(wells[:2])
    app._app_state.selection.selected_wells = set(user_sel)

    # Register a scatter gate (mirrors the user's "after drawing a gate" repro).
    app._app_state.cell_gating.scatter_gates = [{
        "x_col": "gfp_mean_intensity", "y_col": "gfp_mean_intensity",
        "x0": 0.0, "x1": 1e12, "y0": 0.0, "y1": 1e12,
        "name": "G1", "wells": wells[:1],
    }]

    _focus(app, "Plotting")
    before = set(app._app_state.selection.selected_wells)
    _focus(app, "Image Browser")
    after = set(app._app_state.selection.selected_wells)

    print(f"\n[gate-focus-sync] before={sorted(before)} after={sorted(after)}")
    assert after == before, (
        f"Focusing the Cell Browser after a gate changed the selection: "
        f"{sorted(before)} -> {sorted(after)}")
