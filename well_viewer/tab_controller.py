"""Tab-change / notebook-navigation handlers for WellViewerApp.

Holds the bodies of the centre-stack tab dispatcher (``on_tab_change``)
and the notebook page-changed listener (``on_notebook_current_changed``).
Relocated from ``runtime_app.py`` (teardown Phase 9).
"""
from __future__ import annotations
from well_viewer.stats_controller import stats_sync_from_app
from well_viewer.stats_controller import stats_update_tp_menu
from well_viewer.barplot_renderer import redraw_bars
from well_viewer.image_table_controller import image_table_refresh_picker
from well_viewer.image_table_controller import image_table_repopulate_dropdowns
from well_viewer.review_image_renderer import refresh_review_image
from well_viewer.review_image_renderer import update_preview
from well_viewer.scatter_controller import update_scatter_menus


def on_tab_change(app, _e=None) -> None:
    """Show/hide the sidebar and refresh whichever tab is now active."""
    if not hasattr(app, "_line_ax_mean"):
        return
    tab = app._current_centre_tab()
    prev_tab = getattr(app, "_last_tab_name", None)
    prev_selected = set(app._app_state.selection.selected_wells)

    # Dismiss any open figure configurator on tab change -- the user can
    # re-open it on the new tab via the plot card's sliders IconButton.
    for sb in list((getattr(app, "_export_style_sidebars", {}) or {}).values()):
        try:
            sb._close_dock()
        except Exception:
            pass

    app._sidebar_main_frame.setVisible(False)
    app._sidebar_preview_frame.setVisible(False)
    if hasattr(app, "_sidebar_image_table_frame"):
        app._sidebar_image_table_frame.setVisible(False)
    app._sidebar_sample_frame.setVisible(False)
    app._sidebar_stats_frame.setVisible(False)
    # Heat-map layout configurator lives inside the standard sidebar but is
    # only relevant on the Heat Map tab.
    if hasattr(app, "_heatmap_sidebar_frame"):
        app._heatmap_sidebar_frame.setVisible(tab == "Heat Map")
    # The sidebar plate-map doubles as the heat-map drag source on the
    # Heat Map tab -- flip each WellButton into drag-source mode while
    # that tab is active.
    app._sync_heatmap_well_drag_mode(tab == "Heat Map")

    if tab == "Segmentation":
        app._sync_preview_well_for_image_tabs()
        app._sidebar_preview_frame.setVisible(True)
        app._refresh_preview_picker()
        update_preview(app, app._app_state.preview.selected_well)
        refresh_review_image(app)

    elif tab == "Image Table":
        if hasattr(app, "_sidebar_image_table_frame"):
            app._sidebar_image_table_frame.setVisible(True)
        image_table_repopulate_dropdowns(app)
        image_table_refresh_picker(app)

    elif tab == "Sample Definitions":
        app._sidebar_sample_frame.setVisible(True)
        app._groups_centre_refresh()

    elif tab == "Statistics":
        app._sidebar_stats_frame.setVisible(True)
        # Auto-populate from rep-sets on first visit
        if not app._app_state.stats.groups and hasattr(app, "_stats_grp_inner"):
            stats_sync_from_app(app)
        if hasattr(app, "_stats_tp_cb"):
            stats_update_tp_menu(app)

    elif tab == "Batch Export":
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        if hasattr(app, "_batch_export_set_mode"):
            mode = getattr(app, "_batch_export_inline_state", {}).get("mode", "line")
            app._batch_export_set_mode(mode)

    elif tab == "Review CSV":
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        app._refresh_review_csv()

    elif tab == "smFISH":
        app._sidebar_main_frame.setVisible(True)
        if len(app._app_state.selection.selected_wells) > 1:
            keep = (
                app._app_state.selection.last_sel
                if app._app_state.selection.last_sel in app._app_state.selection.selected_wells
                else next(iter(app._app_state.selection.selected_wells))
            )
            # Route through the central mutator so _prev_sel is snapshotted
            # properly -- bypassing it leaves the next selection diff
            # mis-attributing cross-tab additions / removals.
            app._set_selected_wells({keep}, commit=False)
        app._refresh_sidebar_map()
        from well_viewer.tabs.smfish_tab_view import smfish_sync_from_app
        smfish_sync_from_app(app)

    elif tab == "Cell Gating":
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        if hasattr(app, "_cell_gating_area_edit"):
            from well_viewer.tabs.cell_gating_tab_view import cell_gating_load_cell_areas
            cell_gating_load_cell_areas(app)

    else:
        # Line Graphs, Bar Plots, or Scatter -- unified picker always shown
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        if tab == "Bar Plots":
            app._update_bar_tp_menu()
            redraw_bars(app)
        elif tab == "Scatter Plot":
            update_scatter_menus(app)
            from well_viewer.tabs.scatter_tab_view import scatter_redraw_active
            scatter_redraw_active(app)
        else:
            if tab == "Heat Map" and hasattr(app, "_heatmap_sidebar_frame"):
                try:
                    from well_viewer.views.heatmap_layout_sidebar_view import (
                        refresh_heatmap_layout_sidebar,
                    )
                    refresh_heatmap_layout_sidebar(app)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Heatmap sidebar refresh failed")
            app._redraw()

    app._run_tab_switch_smoke_checks(prev_tab, tab, prev_selected)
    app._last_tab_name = tab

    # Drain any restored view-state slated for this tab. Safe no-op when
    # nothing is pending; runs after the tab's own dispatch so its widgets
    # are populated and apply step can validate combo items.
    if getattr(app, "_view_state_pending", None):
        from well_viewer.persistence import view_state as _vs
        _vs.apply_pending_tab_state(app, tab)


def on_notebook_current_changed(app, _idx) -> None:
    nb = app._notebook
    nav = getattr(app, "_section_nav", None)
    if nav is None or nb is None:
        return
    title = nb.currentName()
    if title and nav.currentKey() != title:
        app._section_nav_building = True
        try:
            nav.setCurrentKey(title)
        finally:
            app._section_nav_building = False
    # Drain any redraws that were deferred while this tab was off-screen --
    # fold-change state mutations (and active-channel changes) now mark
    # non-visible scopes dirty rather than eagerly redrawing both.
    try:
        from well_viewer.tabs.fold_change_controls import flush_dirty_scopes
        flush_dirty_scopes(app)
    except Exception:
        import traceback
        traceback.print_exc()
    # Record the visit in the tab-history stack so cmd-left / cmd-right can
    # walk through it. _tab_history_replaying is set by the back/forward
    # shortcuts to suppress appending while they replay an entry.
    if not title:
        return
    if not hasattr(app, "_tab_history"):
        app._tab_history = []
        app._tab_history_idx = -1
    if getattr(app, "_tab_history_replaying", False):
        return
    # Drop any forward entries when the user navigates away from the
    # middle of the history (standard browser semantics).
    idx = app._tab_history_idx
    if idx < len(app._tab_history) - 1:
        del app._tab_history[idx + 1:]
    # Avoid logging consecutive duplicates.
    if app._tab_history and app._tab_history[-1] == title:
        return
    app._tab_history.append(title)
    app._tab_history_idx = len(app._tab_history) - 1
