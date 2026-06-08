"""Tab-change / notebook-navigation handlers for WellViewerApp.

Holds the bodies of the centre-stack tab dispatcher (``on_tab_change``)
and the notebook page-changed listener (``on_notebook_current_changed``).
Relocated from ``runtime_app.py`` (teardown Phase 9).
"""
from __future__ import annotations
from well_viewer.features.stats.controller import stats_sync_from_app
from well_viewer.features.stats.controller import stats_update_tp_menu
from well_viewer.features.barplot.renderer import redraw_bars
from well_viewer.features.image_table.controller import image_table_refresh_picker
from well_viewer.features.image_table.controller import image_table_repopulate_dropdowns
from well_viewer.features.review_image.renderer import refresh_review_image
from well_viewer.features.review_image.renderer import update_preview
from well_viewer.features.scatter.controller import update_scatter_menus
import logging
from functools import partial

_logger = logging.getLogger("well_viewer.runtime_app")


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
        from well_viewer.features.smfish.tab_view import smfish_sync_from_app
        smfish_sync_from_app(app)

    elif tab == "Cell Gating":
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        if hasattr(app, "_cell_gating_fluor_gate_edits"):
            from well_viewer.features.grouping.cell_gating_tab_view import cell_gating_load_cell_areas
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
            from well_viewer.features.scatter.tab_view import scatter_redraw_active
            scatter_redraw_active(app)
        else:
            if tab == "Heat Map" and hasattr(app, "_heatmap_sidebar_frame"):
                try:
                    from well_viewer.features.heatmap.sidebar_view import (
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
        from well_viewer.domain.fold_change_controls import flush_dirty_scopes
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


def run_tab_switch_smoke_checks(
    app,
    prev_tab: Optional[str],
    tab: str,
    prev_selected: set,
) -> None:
    """Debug guardrails for Line/Bar/Batch sidebar continuity."""
    watched = {"Line Graphs", "Bar Plots", "Batch Export"}
    if tab not in watched:
        return

    # Batch Export should share the same in-memory selection objects used by
    # the line/bar/scatter render paths.
    expected_ids = (
        id(app._app_state.selection.selected_wells),
        id(app._app_state.selection.selections),
    )
    if not hasattr(app, "_selection_model_identity"):
        app._selection_model_identity = expected_ids
    elif app._selection_model_identity != expected_ids:
        _logger.warning(
            "Selection model identity changed across tab switch: "
            "_selected_wells/_selections should be shared."
        )

    # UI smoke check: switching among Line/Bar/Batch must not mutate the
    # current per-well selection by itself.
    if prev_tab in watched and prev_selected != app._app_state.selection.selected_wells:
        _logger.warning(
            "Tab switch altered selected wells unexpectedly: %s -> %s",
            prev_tab, tab,
        )



def current_centre_tab(app) -> str:
    """Return the effective current tab name.

    When the "Plotting" top-level tab is active the real content lives in a
    nested QStackedWidget; this helper resolves that one level of indirection
    so callers can always compare against leaf names like "Bar Plots",
    "Heat Map", etc. ("Segmentation" and "smFISH" are now plain top-level
    tabs — smFISH was promoted out of the old Segmentation sub-stack.)
    """
    nb = getattr(app, "_notebook", None)
    if nb is None:
        return ""
    try:
        tab = nb.currentName()
    except Exception:
        return ""
    if tab == "Plotting":
        plotting_nb = getattr(app, "_plotting_notebook", None)
        if plotting_nb is not None and plotting_nb.count() > 0:
            try:
                return plotting_nb.currentName()
            except Exception:
                pass
    return tab



def sync_heatmap_well_drag_mode(app, enable: bool) -> None:
    """Toggle WELL_MIME drag/drop on the sidebar plate widget.

    When *enable* is True, the plate exports the well under the pointer via
    the WELL_MIME format on left-button drag (clicks stop toggling
    selection) and accepts dropped tokens as a "return to palette" action
    that unassigns the well from the active heat-map layout (handled by
    ``_on_sidebar_plate_well_dropped``). When False, drag/drop is disabled
    and the plate reverts to plain selection behaviour.
    """
    plate = getattr(app, "_sidebar_plate", None)
    if plate is None:
        return
    from well_viewer.features.heatmap.sidebar_view import WELL_MIME
    if enable:
        plate.setDragMime(WELL_MIME)
        plate.setAcceptDropMime(WELL_MIME)
    else:
        plate.setDragMime(None)
        plate.setAcceptDropMime(None)



def sync_section_nav_from_notebook(app) -> None:
    nav = getattr(app, "_section_nav", None)
    nb = getattr(app, "_notebook", None)
    if nav is None or nb is None:
        return
    app._section_nav_building = True
    try:
        existing = set(nav.items())
        for title in nb.pageNames():
            if title in existing:
                continue
            nav.addItem(title, icon=app._SECTION_ICONS.get(title), key=title)
            app._section_nav_keys.append(title)
        cur = nb.currentName() if nb.count() else None
        if cur:
            nav.setCurrentKey(cur)
    finally:
        app._section_nav_building = False



def tab_history_back(app) -> None:
    hist = getattr(app, "_tab_history", None)
    if not hist:
        return
    idx = app._tab_history_idx - 1
    if idx < 0:
        return
    app._tab_history_idx = idx
    app._tab_history_replaying = True
    try:
        app._notebook.setCurrentByName(hist[idx])
    finally:
        app._tab_history_replaying = False



def tab_history_forward(app) -> None:
    hist = getattr(app, "_tab_history", None)
    if not hist:
        return
    idx = app._tab_history_idx + 1
    if idx >= len(hist):
        return
    app._tab_history_idx = idx
    app._tab_history_replaying = True
    try:
        app._notebook.setCurrentByName(hist[idx])
    finally:
        app._tab_history_replaying = False



def on_central_pane_changed(app, idx: int) -> None:
    # Mode-seg lives in all_well's titlebar; emit modeChanged so the
    # shell can re-sync its own seg without poking widgets here.
    mode = "analyze" if idx == 1 else "review"
    # Hide the section / well-picker sidebar when Analyze is active —
    # it carries Review-only affordances. Show it again on Review.
    sidebar = getattr(app, "_sidebar_root", None)
    if sidebar is not None:
        sidebar.setVisible(mode == "review")
    app.modeChanged.emit(mode)



def build_centre_shim(app, parent) -> None:
    from well_viewer.core.centre_view import build_centre as _build_centre_view
    _build_centre_view(app, parent)
    # Phase 10 (A1): mirror the notebook's tab list into the rail nav.
    # The nav drives the (hidden-tab-bar) QTabWidget; the QTabWidget's
    # currentChanged signal keeps the nav in sync if anything else
    # changes the index (deferred builders, programmatic select_by_text).
    app._sync_section_nav_from_notebook()
    app._section_nav.currentChanged.connect(app._on_section_nav_changed)
    app._notebook.currentChanged.connect(partial(on_notebook_current_changed, app))

