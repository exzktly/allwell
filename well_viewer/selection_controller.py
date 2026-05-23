"""Plate/sidebar selection handlers for WellViewerApp."""

from __future__ import annotations

from PySide6.QtCore import QObject


def _active_tab(app) -> str:
    # Plot tabs (Line Graphs / Bar Plots / Scatter Plot) live inside a nested
    # "Plotting" QTabWidget. Use the app's centre-tab resolver so callers see
    # the leaf tab name and dispatch correctly when the nested notebook is in
    # play; fall back to the top-level notebook for older code paths.
    resolver = getattr(app, "_current_centre_tab", None)
    if callable(resolver):
        try:
            tab = resolver()
            if tab:
                return tab
        except Exception:
            pass
    if not hasattr(app, "_notebook"):
        return ""
    try:
        return app._notebook.currentName()
    except Exception:
        return ""


def apply_selection_refresh(app) -> None:
    """Refresh the sidebar and the active tab's view after a selection change.

    The subscriber body for the ``selection_changed`` bus signal: every
    selection mutator emits the signal, and ``SelectionRefreshCoordinator``
    runs this in response. It does more than a plot redraw — the sidebar
    plate-map plus a per-tab view refresh — which is why it has its own
    coordinator rather than sharing ``RedrawCoordinator``.
    """
    app._refresh_sidebar_map()
    tab = _active_tab(app)
    if tab == "Bar Plots":
        app._update_bar_tp_menu()
        app._redraw_bars()
    elif tab == "Scatter Plot":
        app._update_scatter_menus()
        from well_viewer.tabs.scatter_tab_view import scatter_redraw_active
        scatter_redraw_active(app)
    elif tab == "Review CSV":
        app._refresh_review_csv()
    elif tab == "smFISH":
        from well_viewer.tabs.smfish_tab_view import smfish_sync_from_app
        smfish_sync_from_app(app)
    elif tab == "Sample Definitions":
        # Cell Gating is a sub-tab here; refresh its CDF if the user has
        # opened it at least once.
        if hasattr(app, "_cell_gating_area_edit"):
            from well_viewer.tabs.cell_gating_tab_view import cell_gating_load_cell_areas
            cell_gating_load_cell_areas(app)
        # Don't fall through to _redraw — labels-and-groups edits don't
        # require a plot redraw.
    else:
        app._redraw()


class SelectionRefreshCoordinator(QObject):
    """Subscribe ``selection_changed`` to :func:`apply_selection_refresh`.

    A dedicated subscriber, kept separate from ``RedrawCoordinator`` because a
    selection change refreshes the sidebar and the active tab's view, not just
    the plot. Constructed as a Qt child of the ``WellViewerApp`` so Qt
    ownership keeps the coordinator — and its connection — alive without a
    ``self._*`` slot on the app.
    """

    def __init__(self, app, bus) -> None:
        super().__init__(app)
        self._app = app
        bus.selection_changed.connect(self._on_selection_changed)

    def _on_selection_changed(self, _state=None) -> None:
        apply_selection_refresh(self._app)


def on_plate_sel_change(app) -> None:
    # SelectionState diffs the new selection against its prev_sel snapshot to
    # resolve last_sel, then re-snapshots.
    sel = app._app_state.selection
    sel.commit_plate_change()
    if hasattr(app, "_notebook"):
        tab = app._notebook.currentName()
        if tab == "smFISH" and len(sel.selected_wells) > 1:
            keep = (sel.last_sel if sel.last_sel in sel.selected_wells
                    else next(iter(sel.selected_wells)))
            sel.selected_wells = {keep}
            sel.prev_sel = set(sel.selected_wells)
    app._event_bus.selection_changed.emit(app._app_state.selection)


def _well_rc(app, w):
    try:
        return app._parse_rc(w)
    except Exception:
        return ("", "")


def _set_groups_hidden(app, sels, *, target=None) -> None:
    """Set ``hidden`` on each of *sels*. ``target`` None ⇒ toggle: hide them all
    if any is currently visible, else show them all."""
    sels = [s for s in sels if isinstance(s, dict)]
    if not sels:
        return
    if target is None:
        target = any(not bool(s.get("hidden")) for s in sels)  # any visible → hide all
    target = bool(target)
    changed = False
    for s in sels:
        if bool(s.get("hidden")) != target:
            s["hidden"] = target
            changed = True
    if changed:
        app._invalidate_stats_cache()
        app._rebuild_all()


PLOTTING_TABS = frozenset({
    "Line Graphs", "Bar Plots", "Scatter Plot", "Distribution", "Heat Map",
})


def _is_plotting_tab(app) -> bool:
    return _active_tab(app) in PLOTTING_TABS


def select_row(app, row: str) -> None:
    if app._app_state.selection.selections:  # rep-mode
        crossing = [
            s for s in app._app_state.selection.selections
            if any(_well_rc(app, w)[0] == row and w in app._app_state.dataset.well_paths
                   for w in (s.get("wells") or []))
        ]
        # In rep-mode (plotting and non-plotting tabs alike), a row click
        # toggles ``hidden`` on every group crossing that row — groups
        # that don't touch the row are untouched.
        if crossing:
            _set_groups_hidden(app, crossing)
        return
    else:
        row_labels = [lbl for lbl in app._app_state.dataset.well_paths if _well_rc(app, lbl)[0] == row]
        if not row_labels:
            return
        if any(lbl not in app._app_state.selection.selected_wells for lbl in row_labels):
            app._app_state.selection.selected_wells.update(row_labels)
        else:
            app._app_state.selection.selected_wells.difference_update(row_labels)
        app._on_plate_sel_change()


def select_col(app, col: str) -> None:
    if app._app_state.selection.selections:
        crossing = [
            s for s in app._app_state.selection.selections
            if any(_well_rc(app, w)[1] == col and w in app._app_state.dataset.well_paths
                   for w in (s.get("wells") or []))
        ]
        if crossing:
            _set_groups_hidden(app, crossing)
        return
    else:
        col_labels = [lbl for lbl in app._app_state.dataset.well_paths if _well_rc(app, lbl)[1] == col]
        if not col_labels:
            return
        if any(lbl not in app._app_state.selection.selected_wells for lbl in col_labels):
            app._app_state.selection.selected_wells.update(col_labels)
        else:
            app._app_state.selection.selected_wells.difference_update(col_labels)
        app._on_plate_sel_change()


def select_all(app) -> None:
    if app._app_state.selection.selections:  # rep-mode: show every group
        _set_groups_hidden(app, app._app_state.selection.selections, target=False)
    else:
        app._app_state.selection.select_wells(app._app_state.dataset.well_paths.keys())
        app._event_bus.selection_changed.emit(app._app_state.selection)


def select_none(app) -> None:
    if app._app_state.selection.selections:  # rep-mode: hide every group
        _set_groups_hidden(app, app._app_state.selection.selections, target=True)
    else:
        app._app_state.selection.clear_selection()
        app._event_bus.selection_changed.emit(app._app_state.selection)


def on_sidebar_plate_selection_changed(app, ids) -> None:
    # Fires per-cell during a drag and once for a click; also fires when
    # _refresh_sidebar_map_now calls setSelectedWellIds / setEnabledWells
    # (those corrections converge harmlessly).
    #
    # Sample Definitions / Review CSV exception: even when groups exist
    # (rep-set mode), these tabs need per-well selection so Add Prefix /
    # Add Suffix / row-filter can act on user-chosen wells. We let the
    # click through and update _selected_wells normally;
    # _refresh_sidebar_map_now mirrors the same exception so the painted
    # selection doesn't get wiped on the next refresh tick.
    from PySide6.QtCore import QTimer
    try:
        tab_name = app._current_centre_tab()
    except Exception:
        tab_name = ""
    per_well_tab = tab_name in ("Sample Definitions", "Review CSV")
    if app._app_state.selection.selections and not per_well_tab:
        return  # rep-set mode: the plate is passive; its selection is unused
    # Keep the invariant _selected_wells ⊆ _well_paths.
    new_set = {w for w in ids if w in app._app_state.dataset.well_paths}
    if new_set == app._app_state.selection.selected_wells:
        return
    app._app_state.selection.selected_wells = new_set
    app._refresh_sidebar_map()
    # Coalesce the commit. A drag through 96 cells fires this handler
    # 96 times in one event-loop tick; without coalescing each one would
    # trigger a full bar/scatter/line redraw and an export-style reapply.
    # The deferred call fires once at the end of the tick.
    if not app._app_state.selection.plate_commit_pending:
        app._app_state.selection.plate_commit_pending = True
        QTimer.singleShot(0, app._plate_commit_deferred)


def on_sidebar_plate_drag_finished(app) -> None:
    try:
        tab_name = app._current_centre_tab()
    except Exception:
        tab_name = ""
    per_well_tab = tab_name in ("Sample Definitions", "Review CSV")
    if app._app_state.selection.selections and not per_well_tab:
        return
    # Cancel any pending deferred commit from the paint events that
    # led here -- we're committing right now.
    app._app_state.selection.plate_commit_pending = False
    app._on_plate_sel_change()


def on_sidebar_plate_well_activated(app, well_id: str) -> None:
    # On a plotting tab, a single click on a well "activates" that well
    # and (if it belongs to a saved group) every other well in the same
    # group. Solo-well clicks toggle membership in active_solo_wells.
    if well_id not in app._app_state.dataset.well_paths:
        return
    try:
        tab = app._current_centre_tab()
    except Exception:
        tab = ""
    if tab in app._PLOTTING_TABS:
        sid = app._sel_id_for_well(well_id)
        if sid is not None:
            app._sel_toggle_hidden(sid)
            return
        if well_id in app._app_state.selection.active_solo_wells:
            app._app_state.selection.active_solo_wells.discard(well_id)
        else:
            app._app_state.selection.active_solo_wells.add(well_id)
        app._refresh_sidebar_map()
        app._on_plate_sel_change()
        return
    # Legacy fallback for non-plotting tabs that still receive
    # wellActivated in rep-set mode (Image Table / smFISH / etc):
    # toggle the clicked group's visibility.
    if not app._app_state.selection.selections:
        return
    sid = app._sel_id_for_well(well_id)
    if sid is None:
        return
    app._sel_toggle_hidden(sid)


def on_sidebar_plate_row_activated(app, row: str) -> None:
    if app._app_state.selection.selections:
        app._select_row(row)
    else:
        # The widget already toggled the row and synced _selected_wells via
        # the preceding selectionChanged; do the heavy refresh once.
        app._on_plate_sel_change()


def on_sidebar_plate_col_activated(app, col: str) -> None:
    if app._app_state.selection.selections:
        app._select_col(col)
    else:
        app._on_plate_sel_change()


def on_sidebar_plate_well_dropped(app, well_id: str, token: str) -> None:
    if not token:
        return
    try:
        from well_viewer.views.heatmap_layout_sidebar_view import _on_drop_event
        _on_drop_event(app, "palette", None, token)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Heat map well drop handling failed")
