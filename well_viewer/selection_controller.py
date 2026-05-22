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
