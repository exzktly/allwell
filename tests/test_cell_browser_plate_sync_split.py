"""Split View [Scatter + Cell Browser]: the plate selection drives the gallery.

Confirmed-by-probe defects this pins:
1. The Cell Browser gallery ignored ``selected_wells`` (it sourced from the
   replicate-set groups instead), so clicking wells never changed it.
2. The single shared sidebar plate painted a DIFFERENT highlight depending on
   which pane was focused (group wells when the Scatter was focused, the
   per-well selection when the Cell Browser was focused) — read by the user as
   the selection "toggling" just by clicking into the Cell Browser pane.
3. With groups defined, clicking wells on the plate while the Scatter pane was
   focused was a passive no-op, so the user could not drive the gallery at all.

Desired: when a Cell Browser pane is present in the split, the plate is a
per-well selector for BOTH panes; the gallery follows the selected wells; and the
plate's highlight is the same regardless of which pane is focused. With no
individual selection, the gallery falls back to the Scatter's visible groups.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _enter_split(app):
    from well_viewer.features.image_browser.controller import show_image_browser_subtab
    from well_viewer.core.split_view import enter_split
    assert show_image_browser_subtab(app, "Cell Browser")
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")
    app._notebook.setCurrentByName("Plotting")
    enter_split(app, "Plotting", "Image Browser")
    assert app._is_tab_visible("Cell Browser") and "Scatter Plot" in app._visible_centre_tabs()


def _focus(app, top):
    from well_viewer.core.split_view import focus_split
    focus_split(app, top)
    app._refresh_sidebar_map_now()


def test_gallery_follows_explicit_selection_over_groups(loaded_app):
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_source_wells
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:1])          # group covers wells[0]
    app._app_state.selection.selected_wells = set(wells[:2])  # user picked two wells
    # The gallery must follow the explicit selection, not the group.
    assert cell_browser_source_wells(app) == sorted(wells[:2], key=app._parse_rc)


def test_gallery_follows_groups_when_no_explicit_selection(loaded_app):
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_source_wells
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:1])
    app._app_state.selection.selected_wells = set()   # nothing explicitly selected
    # Falls back to the Scatter's visible group wells (preserve #576/#578 intent).
    assert cell_browser_source_wells(app) == wells[:1]


def test_plate_highlight_consistent_across_focus_in_split(loaded_app):
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:1])
    app._app_state.selection.selected_wells = set(wells[:2])

    _focus(app, "Plotting")
    scatter_view = sorted(app._sidebar_plate.selectedWellIds())
    _focus(app, "Image Browser")
    cellbrowser_view = sorted(app._sidebar_plate.selectedWellIds())

    # The shared plate must not "toggle" the highlight when focus moves between
    # the two panes — it should show the user's selection in both.
    assert scatter_view == cellbrowser_view == sorted(wells[:2])


def test_plate_click_registers_in_group_mode_when_cellbrowser_visible(loaded_app):
    from well_viewer.core import selection_controller as sc
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:1])
    app._app_state.selection.selected_wells = set()

    _focus(app, "Plotting")  # Scatter focused — today this makes the plate "passive"
    # Simulate the user clicking a single well on the plate.
    sc.on_sidebar_plate_selection_changed(app, [wells[1]])
    app._plate_commit_deferred()

    assert app._app_state.selection.selected_wells == {wells[1]}, (
        "plate click should register a per-well selection when the Cell Browser "
        "is a visible consumer, even with groups defined")
