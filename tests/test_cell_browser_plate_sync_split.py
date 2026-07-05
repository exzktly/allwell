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
    assert show_image_browser_subtab(app._ctx, "Cell Browser")
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")
    app._notebook.setCurrentByName("Plotting")
    enter_split(app, "Plotting", "Image Browser")
    assert app._is_tab_visible("Cell Browser") and "Scatter Plot" in app._visible_centre_tabs()


def _focus(app, top):
    from well_viewer.core.split_view import focus_split
    focus_split(app, top)
    app._refresh_sidebar_map_now()


def test_gallery_mirrors_groups_over_stale_selection(loaded_app):
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_source_wells
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:1])                  # group covers wells[0]
    app._app_state.selection.selected_wells = set(wells[1:2])  # stale individual sel
    # With groups defined the gallery MIRRORS the scatter's group wells, not a
    # leftover individual selection — so the two panes always agree.
    assert cell_browser_source_wells(app._ctx) == wells[:1]


def test_gallery_follows_groups_when_no_explicit_selection(loaded_app):
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_source_wells
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:1])
    app._app_state.selection.selected_wells = set()   # nothing explicitly selected
    # Falls back to the Scatter's visible group wells (preserve #576/#578 intent).
    assert cell_browser_source_wells(app._ctx) == wells[:1]


def test_plate_highlight_consistent_across_focus_in_split(loaded_app):
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:2])

    _focus(app, "Plotting")
    scatter_view = sorted(app._sidebar_plate.selectedWellIds())
    _focus(app, "Image Browser")
    cellbrowser_view = sorted(app._sidebar_plate.selectedWellIds())

    # The group-mode plate shows the visible group wells in BOTH focus states —
    # no focus-driven flip / apparent "loss" when clicking into the Cell Browser.
    assert scatter_view == cellbrowser_view == sorted(wells[:2])


def test_plate_click_toggles_group_in_split(loaded_app):
    # With groups defined, the plate stays group-driven in the split: clicking a
    # well toggles its GROUP's visibility, which drives BOTH the scatter and the
    # cell browser — it does NOT create a per-well selection.
    from well_viewer.core import selection_controller as sc
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_source_wells
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._sel_add(name="g1", wells=wells[:1])           # group covers wells[0]
    _focus(app, "Plotting")
    assert wells[0] in cell_browser_source_wells(app._ctx)  # group visible → in gallery

    sc.on_sidebar_plate_well_activated(app, wells[0])   # click the well → toggle group
    assert wells[0] not in cell_browser_source_wells(app._ctx)  # group hidden → gone
    assert app._app_state.selection.selected_wells == set()  # no per-well sel made


def test_gate_stays_live_as_wells_are_added_and_removed(loaded_app):
    # Combined model: the scatter gate filters cells, the well selection scopes
    # which wells, and add/remove updates the gallery live WITHOUT dropping the
    # gate. (gate admits everything so we isolate the well-scope effect.)
    from well_viewer.features.image_browser.cell_browser.collect import collect_cells
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    assert len(wells) >= 3
    _enter_split(app)
    app._ctx.runtime.cell_browser.state.scatter_gate_mode = True
    app._app_state.cell_gating.scatter_gates = [
        {"x_col": "gfp_mean_intensity", "y_col": "gfp_mean_intensity",
         "x0": -1e12, "x1": 1e12, "y0": -1e12, "y1": 1e12, "name": "G1"}]
    app._ctx.runtime.cell_browser.gate_choices = None

    def scope():
        return sorted({c.well for c in collect_cells(app._ctx, [], live_sync=True)})

    app._app_state.selection.selected_wells = {wells[0]}
    assert scope() == [wells[0]]
    app._app_state.selection.selected_wells = {wells[0], wells[1]}
    assert scope() == sorted([wells[0], wells[1]])          # added a well, gate live
    app._app_state.selection.selected_wells = {wells[0]}
    assert scope() == [wells[0]]                            # removed a well, gate live


def test_inspecting_a_cell_does_not_rescope_wells(loaded_app):
    # Clicking a gallery thumbnail to inspect a cell must NOT change the well
    # scope — otherwise looking at a cell would collapse the gated view.
    from well_viewer.features.image_browser.cell_browser import controller as ctl
    from well_viewer.features.image_browser.cell_browser.collect import collect_cells
    app = loaded_app
    wells = list(app._app_state.dataset.well_paths.keys())
    _enter_split(app)
    app._app_state.selection.selected_wells = set(wells[:2])
    cells = collect_cells(app._ctx, [], live_sync=True)
    assert cells, "expected some cells to inspect"
    before = set(app._app_state.selection.selected_wells)
    ctl.on_cell_clicked(app, cells[0])  # navigates to Segmentation; read-only for scope
    assert app._app_state.selection.selected_wells == before
