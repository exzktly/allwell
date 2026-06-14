"""Plate/sidebar selection handlers for WellViewerApp."""

from __future__ import annotations

from PySide6.QtCore import QObject
from well_viewer.features.barplot.renderer import redraw_bars
from well_viewer.features.scatter.controller import update_scatter_menus
from well_viewer.features.grouping.view import rep_panel_refresh
from well_viewer.features.grouping.label_grid_view import label_grid_refresh
from well_viewer.core.safe import swallowed


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
            swallowed()  # was: pass (silent except)
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
        redraw_bars(app)
    elif tab == "Scatter Plot":
        update_scatter_menus(app)
        from well_viewer.features.scatter.tab_view import scatter_redraw_active
        scatter_redraw_active(app)
    elif tab == "Review CSV":
        app._refresh_review_csv()
    elif tab == "smFISH":
        from well_viewer.features.smfish.tab_view import smfish_sync_from_app
        smfish_sync_from_app(app)
    elif tab == "Sample Definitions":
        # Cell Gating is a sub-tab here; refresh its CDF if the user has
        # opened it at least once.
        if hasattr(app, "_cell_gating_fluor_gate_edits"):
            from well_viewer.features.grouping.cell_gating_tab_view import cell_gating_load_cell_areas
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
        on_plate_sel_change(app)


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
        on_plate_sel_change(app)


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
    on_plate_sel_change(app)


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
        on_plate_sel_change(app)
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
        select_row(app, row)
    else:
        # The widget already toggled the row and synced _selected_wells via
        # the preceding selectionChanged; do the heavy refresh once.
        on_plate_sel_change(app)


def on_sidebar_plate_col_activated(app, col: str) -> None:
    if app._app_state.selection.selections:
        select_col(app, col)
    else:
        on_plate_sel_change(app)


def on_sidebar_plate_well_dropped(app, well_id: str, token: str) -> None:
    if not token:
        return
    try:
        from well_viewer.features.heatmap.sidebar_view import _on_drop_event
        _on_drop_event(app, "palette", None, token)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Heat map well drop handling failed")


def import_selections_from_json(app) -> None:
    """Import a selections JSON file (the same schema used by Save) and
    merge it into ``app._app_state.selection.selections``. Existing names get a ``_v2``
    suffix on collision so nothing already loaded is overwritten."""
    import json
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    path, _ = QFileDialog.getOpenFileName(
        app, "Import selections from JSON", "", "JSON (*.json);;All files (*)",
    )
    if not path:
        return
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except Exception as exc:
        QMessageBox.critical(app, "Import failed", f"Could not parse JSON:\n{exc}")
        return

    # Accept either a v2 sample-definitions block (with a 'selections' key)
    # or a bare list of selection dicts.
    items = None
    if isinstance(payload, dict):
        items = payload.get("selections") or payload.get("sample_definitions", {}).get("selections")
    elif isinstance(payload, list):
        items = payload
    if not items:
        QMessageBox.warning(app, "Import", "No selections found in that file.")
        return

    added = 0
    for sel in items:
        if not isinstance(sel, dict):
            continue
        name = sel.get("name") or "Imported"
        wells = sel.get("wells") or []
        replicates = sel.get("replicates")
        source = sel.get("source") or "imported"
        try:
            app._sel_add(name=name, wells=list(wells),
                          replicates=replicates, source=source)
            added += 1
        except Exception:
            _logger = __import__("logging").getLogger("well_viewer.runtime_app")
            _logger.exception("Import selection failed for %r", name)

    if hasattr(app, "_groups_centre_refresh"):
        try:
            app._groups_centre_refresh()
        except Exception:
            swallowed()  # was: pass (silent except)
    app._toast(f"Imported {added} selection(s) from {path}.", kind="success")



def labels_apply_affix(app, *, where: str) -> None:
    """Prompt for text and prepend/append it to the labels of every
    well currently selected.

    Selection precedence: the Well-Labels grid's selection wins when
    non-empty (rows / columns / Shift-clicked cells); otherwise the
    sidebar's plate selection is used as a fallback.
    """
    from well_viewer.core.ui_helpers import ask_name_dialog

    grid = getattr(app, "_lbl_grid", None)
    grid_sel: list[str] = []
    if grid is not None:
        try:
            grid_sel = list(grid.selectedTokens())
        except Exception:
            grid_sel = []
    if grid_sel:
        selected = sorted(grid_sel, key=app._parse_rc)
    else:
        selected = sorted(app._app_state.selection.selected_wells, key=app._parse_rc)
    if not selected:
        app._set_status(
            f"Add {where.title()}: no wells selected — click a row/column "
            "header on the Well Labels grid or select wells on the sidebar plate."
        )
        return

    title = "Add Prefix" if where == "prefix" else "Add Suffix"
    verb = "prepend to" if where == "prefix" else "append to"
    plural = "s" if len(selected) != 1 else ""
    prompt = (f"Text to {verb} the custom label of {len(selected)} "
              f"selected well{plural}:")
    text = ask_name_dialog(app, title=title, prompt=prompt, default="", strip=False)
    if not text:
        return

    for tok in selected:
        current = app._app_state.dataset.well_labels.get(tok, "")
        new = (text + current) if where == "prefix" else (current + text)
        if new:
            app._app_state.dataset.well_labels[tok] = new
        else:
            app._app_state.dataset.well_labels.pop(tok, None)

    app._invalidate_stats_cache()
    label_grid_refresh(app)
    _schedule_sample_def_autosave(app)
    app._set_status(
        f"Added {where} '{text}' to {len(selected)} well label{plural}."
    )



def update_active_group_label(app) -> None:
    """Mirror ``_current_selection_id`` into the Groups sub-tab banner.

    Shows the active group's name (bold, accent-coloured) when one is
    selected, or a "click a group below" prompt when none is — so a
    click on the sidebar plate always has a visible target.
    """
    lbl = getattr(app, "_rep_active_group_lbl", None)
    if lbl is None:
        return
    sid = app._app_state.selection.current_selection_id
    sel = app._sel_by_id(sid) if sid else None
    try:
        import theme as _theme
    except Exception:
        _theme = None
    if sel is None:
        lbl.setText("None selected — click a group below to start")
        if _theme is not None:
            lbl.setStyleSheet(
                f"QLabel#ActiveGroupValue {{ "
                f"color: {_theme.Colors.text_secondary}; "
                f"font-size: {_theme.Typography.small_size}px; "
                f"font-weight: 600; }}"
            )
        return
    name = str(sel.get("name") or "(unnamed)")
    n_wells = sum(1 for w in (sel.get("wells") or []) if w in app._app_state.dataset.well_paths)
    lbl.setText(f"{name}  ·  {n_wells} well(s)")
    if _theme is not None:
        lbl.setStyleSheet(
            f"QLabel#ActiveGroupValue {{ "
            f"color: {_theme.Colors.accent}; "
            f"font-size: {_theme.Typography.small_size}px; "
            f"font-weight: 700; }}"
        )



def refresh_sidebar_map_now(app) -> None:
    """Push current selection / rep-set state onto the sidebar plate widget.

    Rep-set mode: each set's wells take that set's colour (muted when the
    set is hidden); the plate runs in "passive" mode so clicks toggle a
    set's visibility. Per-well mode: selected wells take the accent colour
    and the plate runs in "select" mode (drag-to-select). Wells with no
    loaded data are disabled. The count / group-hint labels are updated
    exactly as before.
    """
    plate = getattr(app, "_sidebar_plate", None)
    if plate is None:
        app._sidebar_map_refresh_pending = False
        return

    # Some tabs need per-well selection even when groups exist:
    # Sample Definitions (Cell Gating / Add Prefix / Add Suffix all
    # need user-chosen wells), and Review CSV (the user has to pick
    # which wells' rows to load). For those tabs the plate stays in
    # ``select`` mode and ``_selected_wells`` is honoured.
    try:
        tab_name = app._current_centre_tab()
    except Exception:
        tab_name = ""
    per_well_tab = tab_name in ("Sample Definitions", "Review CSV")
    rep_mode = bool(app._app_state.selection.selections) and not per_well_tab

    colors: Dict[str, object] = {}
    if rep_mode:
        for s in app._app_state.selection.selections:
            full_c = app._sel_color(s)
            shade = app._mute_color(full_c) if s.get("hidden") else full_c
            for tok in (s.get("wells") or []):
                if tok in app._app_state.dataset.well_paths:
                    colors[tok] = shade
    else:
        for tok in app._app_state.selection.selected_wells:
            if tok in app._app_state.dataset.well_paths:
                colors[tok] = app._rank_color_well(tok)

    # smFISH suppresses row/col header selection and forces single-well.
    smfish = False
    nb = getattr(app, "_notebook", None)
    if nb is not None:
        try:
            # smFISH is now a sub-tab inside the Segmentation parent,
            # so the top-level current name reads as "Segmentation"
            # whether the user is on the Segmentation or smFISH sub-
            # tab. Descend via ``_current_centre_tab`` to recover the
            # leaf name.
            smfish = app._current_centre_tab() == "smFISH"
        except Exception:
            smfish = False

    plate.setEnabledWells(list(app._app_state.dataset.well_paths.keys()))
    plate.setSelectionMode("passive" if rep_mode else "select")
    plate.setRowColumnSelectable(not smfish)
    plate.setSingleSelectionMode(smfish and not rep_mode)
    plate.clearWellColors()
    plate.setWellColors(colors)
    # Plotting tabs in rep-mode still honour ``_selected_wells`` so a
    # click on a well in a saved group promotes that group's wells to
    # the raised + bright (``_paint_lit``) look. Other rep-mode tabs
    # (Image Table / smFISH / etc) keep the legacy "no per-well
    # selection" override.
    plotting_tab = tab_name in app._PLOTTING_TABS
    if rep_mode and not plotting_tab:
        plate.setSelectedWellIds([])
    elif rep_mode and plotting_tab:
        # In rep-mode on a plotting tab the "active" set is exactly the
        # union of every non-hidden group's wells. ``_selected_wells``
        # is not used here — clicks toggle each group's ``hidden`` flag
        # so the visual state and the plot's ``_rep_sets_active()``
        # filter stay in lock-step. Solo wells (not in any group) live
        # in ``_active_solo_wells``.
        visible_wells = {
            w for s in app._app_state.selection.selections if not s.get("hidden")
            for w in (s.get("wells") or []) if w in app._app_state.dataset.well_paths
        }
        visible_wells |= {
            w for w in (app._app_state.selection.active_solo_wells or set())
            if w in app._app_state.dataset.well_paths
        }
        plate.setSelectedWellIds(list(visible_wells))
    else:
        plate.setSelectedWellIds(list(app._app_state.selection.selected_wells))

    # Count label / hint
    loaded  = app._rep_sets_loaded() if rep_mode else []
    n_vis   = len(app._rep_sets_active()) if rep_mode else len(app._app_state.selection.selected_wells)
    n_loaded = len(loaded)
    if hasattr(app, "_sel_count_lbl"):
        if rep_mode:
            n_hid = n_loaded - n_vis
            app._sel_count_lbl.setText((f"{n_vis}/{n_loaded} set(s) visible"
                      if n_hid else f"{n_loaded} set(s) — all visible"))
        else:
            app._sel_count_lbl.setText((f"{n_vis} well{'s' if n_vis != 1 else ''} selected"
                      if n_vis else "No wells selected"))
    if hasattr(app, "_sel_count_chip"):
        total = len(app._app_state.dataset.well_paths)
        if rep_mode:
            # Count *wells* across every visible group + active solo
            # well, not groups. Earlier we showed group count here,
            # which read as "1 / 96" for a group with 4 members.
            active_wells: set = set()
            for s in app._app_state.selection.selections:
                if s.get("hidden"):
                    continue
                for w in (s.get("wells") or []):
                    if w in app._app_state.dataset.well_paths:
                        active_wells.add(w)
            for w in (app._app_state.selection.active_solo_wells or set()):
                if w in app._app_state.dataset.well_paths:
                    active_wells.add(w)
            app._sel_count_chip.setText(f"{len(active_wells)} / {total or 96}")
        else:
            app._sel_count_chip.setText(f"{n_vis} / {total or 96}")
    if hasattr(app, "_line_group_hint"):
        if rep_mode:
            app._line_group_hint.setText("Click a well to toggle its set's visibility on the plot.")
        else:
            app._line_group_hint.setText("")

    app._sidebar_map_refresh_pending = False



def sel_export_one(app, sid) -> None:
    """Export a single saved selection (by id) to a standalone JSON file."""
    s = app._sel_by_id(sid)
    if s is None:
        return
    from PySide6.QtWidgets import QFileDialog
    import json as _json
    default_name = f"{s.get('name') or 'selection'}.json"
    path, _ = QFileDialog.getSaveFileName(
        app, "Export selection", default_name, "JSON (*.json)"
    )
    if not path:
        return
    try:
        from well_viewer.domain.selections_model import to_block
        block = to_block([s], current_id=s.get("id"))
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(block, f, indent=2)
        app._toast(f"Exported '{s.get('name')}' to {path}.", kind="success")
    except Exception as exc:
        app._toast(f"Export failed: {exc}", kind="danger", msec=6000)



def sel_strip_wells(app, wells, *, keep_id=None) -> None:
    """A well belongs to ≤1 group: remove ``wells`` from every selection but
    ``keep_id``, pruning their replicates to the survivors. Also drop the
    same wells from ``app._app_state.selection.selected_wells`` so the plotting tabs don't
    keep treating them as solo wells once they've joined a group (the
    sidebar plate becomes passive in rep_mode and the user can't update
    the set themselves)."""
    from well_viewer.domain.selections_model import _deoverlap_replicates
    wset = set(wells or [])
    if not wset:
        return
    for s in app._app_state.selection.selections:
        if s.get("id") == keep_id:
            continue
        nw = [w for w in (s.get("wells") or []) if w not in wset]
        if nw != s.get("wells"):
            s["wells"] = nw
            s["replicates"] = _deoverlap_replicates(s.get("replicates"), allowed=set(nw))
    if wset & app._app_state.selection.selected_wells:
        app._app_state.selection.selected_wells = app._app_state.selection.selected_wells - wset



def sel_set_color(app, sid, color) -> None:
    """Recolour a saved selection. ``color`` is a ``#RRGGBB`` hex string
    (or anything accepted by QColor); empty / falsy clears the override
    and lets the rank-based colour fall back through."""
    s = app._sel_by_id(sid)
    if s is None:
        return
    try:
        from PySide6.QtGui import QColor
        qc = QColor(color) if color else QColor()
        new = qc.name(QColor.HexRgb).upper() if qc.isValid() else ""
    except Exception:
        new = str(color or "").strip()
    if (s.get("color") or "") == new:
        return
    if new:
        s["color"] = new
    else:
        s.pop("color", None)
    app._rebuild_all()



def groups_centre_refresh(app) -> None:
    """Refresh all Sample Definitions panels.

    Card lists are only rebuilt when the tab is active (expensive).
    Plate maps are always updated (cheap).
    """
    app._enforce_well_exclusivity()
    tab_visible = False
    nb = getattr(app, "_notebook", None)
    if nb is not None:
        try:
            tab_visible = nb.currentName() == "Sample Definitions"
        except Exception:
            swallowed()  # was: pass (silent except)

    if tab_visible:
        rep_panel_refresh(app)
        label_grid_refresh(app)
    app._rep_refresh_map()
    update_active_group_label(app)



def set_selected_wells(app, new_set, *, commit: bool = True) -> None:
    """Single mutation point for ``_selected_wells``.

    Callers that mutated the set directly (heatmap drag, smFISH tab
    activation, batch-export panels) bypassed ``_prev_sel`` tracking
    and the redraw fan-out, so the next selection diff in
    ``selection_controller.on_plate_sel_change`` mis-attributed
    additions / removals across tabs. Funnel everything through here.
    """
    new_set = set(new_set or [])
    if new_set == app._app_state.selection.selected_wells:
        return
    # Snapshot the previous selection so on_plate_sel_change can
    # compute an accurate diff if it runs after this.
    app._app_state.selection.prev_sel = set(app._app_state.selection.selected_wells)
    app._app_state.selection.selected_wells = new_set
    if hasattr(app, "_refresh_sidebar_map"):
        app._refresh_sidebar_map()
    if commit:
        on_plate_sel_change(app)



def enforce_well_exclusivity(app) -> None:
    """Invariant: a well belongs to **at most one group**. Keep each well in
    the first selection that has it; strip it from the rest. The edit
    helpers already enforce this (edited group wins); this is a safety net
    and cleans up pre-existing overlaps from old saved data."""
    seen: set = set()
    for s in app._app_state.selection.selections:
        if not isinstance(s, dict):
            continue
        kept = []
        for w in (s.get("wells") or []):
            if w in seen:
                continue
            seen.add(w)
            kept.append(w)
        if kept != s.get("wells"):
            from well_viewer.domain.selections_model import _deoverlap_replicates
            s["wells"] = kept
            s["replicates"] = _deoverlap_replicates(s.get("replicates"), allowed=set(kept))



def rebuild_all(app) -> None:
    """
    Single authoritative refresh called after ANY data change.
    Always synchronous — no debounce — because it is only called on
    explicit user actions (button clicks / dialog OK), never during drag.
    """
    app._invalidate_stats_cache()
    app._groups_centre_refresh()          # Sample Definitions: GROUPS panel + map
    app._refresh_sidebar_map()            # line-graph picker: rep colours
    redraw_bars(app)
    app._redraw()
    try:
        from well_viewer.features.scatter.tab_view import scatter_redraw_active
        scatter_redraw_active(app)
    except Exception:
        __import__("logging").getLogger("well_viewer.runtime_app").exception("Scatter redraw failed")
    if hasattr(app, "_notebook"):
        if app._current_centre_tab() == "Line Graphs":
            app._show_line_sidebar()
    # Sample definitions auto-persist (debounced), like notes / ratios /
    # view-state — the explicit Save button is now just "save now".
    _schedule_sample_def_autosave(app)


def _schedule_sample_def_autosave(app) -> None:
    """Queue a debounced write of the sample-definitions block.

    Safe to call from any edit path: the registry coalesces a burst into one
    write and ``autosave_to_pipeline_info`` no-ops silently without a data dir.
    """
    p = getattr(app, "_persistence", None)
    if p is not None:
        p.schedule_save("sample_definitions")


def sel_add(app, name=None, wells=None, replicates=None, source="user",
             *, make_current=True):
    from well_viewer.domain.selections_model import make_selection
    used_names = {s.get("name") for s in app._app_state.selection.selections}
    used_ids = {s.get("id") for s in app._app_state.selection.selections}
    sel = make_selection(name=name, wells=wells, replicates=replicates,
                         source=source, used_names=used_names, used_ids=used_ids,
                         fallback_color_idx=len(app._app_state.selection.selections))
    app._sel_strip_wells(sel["wells"], keep_id=sel["id"])
    app._app_state.selection.selections.append(sel)
    if make_current:
        app._app_state.selection.current_selection_id = sel["id"]
    app._rebuild_all()
    return sel["id"]



def sel_set_hidden(app, sid, hidden, *, light=False) -> None:
    s = app._sel_by_id(sid)
    if s is None:
        return
    if bool(s.get("hidden")) == bool(hidden):
        return
    s["hidden"] = bool(hidden)
    if light:
        app._refresh_sidebar_map()
        _schedule_sample_def_autosave(app)
    else:
        app._rebuild_all()



def sel_set_composition(app, sid, wells=None, replicates=None) -> None:
    from well_viewer.domain.selections_model import _clean_wells, _deoverlap_replicates
    s = app._sel_by_id(sid)
    if s is None:
        return
    if wells is not None:
        s["wells"] = _clean_wells(wells)
        app._sel_strip_wells(s["wells"], keep_id=sid)
    src = replicates if replicates is not None else s.get("replicates")
    s["replicates"] = _deoverlap_replicates(src, allowed=set(s.get("wells") or []))
    app._rebuild_all()



def select_invert(app) -> None:
    """Phase 13 (B7): flip the current selection — every loaded well that
    was selected becomes unselected, and vice versa. Only wells with data
    (``_well_paths``) participate."""
    cur = set(app._app_state.selection.selected_wells)
    new = {w for w in app._app_state.dataset.well_paths.keys() if w not in cur}
    app._app_state.selection.selected_wells = new
    # Re-use the standard refresh path the click handlers go through.
    app._refresh_sidebar_map()
    on_plate_sel_change(app)



def invalidate_stats_cache(app) -> None:
    """Discard cached group statistics. Call whenever group definitions change."""
    app._app_state.stats.cache: dict = {}
    # Replicate-set visibility may have shifted, so re-check whether the
    # per-FOV toggle is still applicable. Cheap; only walks _fov_btns.
    if hasattr(app, "_refresh_fov_btn_state"):
        app._refresh_fov_btn_state()
    # Cell gating writes new ``Included`` flags onto cached rows, which
    # the Review Image include map mirrors. Bumping the version key
    # forces the next refresh to recompute the include map (cheap)
    # while reusing the cached image arrays + boundary mask (expensive).
    app._app_state.review_image.override_version += 1



def rep_clear_all(app) -> None:
    from PySide6.QtWidgets import QMessageBox
    if not app._app_state.selection.selections:
        return
    resp = QMessageBox.question(
        app, "Clear all groups?",
        f"Remove all {len(app._app_state.selection.selections)} group(s)?",
        QMessageBox.Yes | QMessageBox.No,
    )
    if resp == QMessageBox.Yes:
        app._sel_clear_all()



def sel_rename(app, sid, name) -> None:
    from well_viewer.domain.selections_model import _unique_name as _uq
    s = app._sel_by_id(sid)
    if s is None:
        return
    new = _uq(name, {x.get("name") for x in app._app_state.selection.selections if x.get("id") != sid})
    if new == s.get("name"):
        return
    s["name"] = new
    app._rebuild_all()



def sel_delete(app, sid) -> None:
    i = app._sel_index_of(sid)
    if i < 0:
        return
    app._app_state.selection.selections.pop(i)
    if app._app_state.selection.current_selection_id == sid:
        app._app_state.selection.current_selection_id = (
            app._app_state.selection.selections[min(i, len(app._app_state.selection.selections) - 1)].get("id")
            if app._app_state.selection.selections else None)
    app._rebuild_all()



def sel_select(app, sid) -> None:
    if sid not in {s.get("id") for s in app._app_state.selection.selections}:
        return
    if sid == app._app_state.selection.current_selection_id:
        return
    app._app_state.selection.current_selection_id = sid
    app._groups_centre_refresh()
    app._refresh_sidebar_map()



def sel_reorder(app, ids) -> None:
    by_id = {s.get("id"): s for s in app._app_state.selection.selections}
    new = [by_id[i] for i in ids if i in by_id]
    for s in app._app_state.selection.selections:
        if s not in new:
            new.append(s)
    if new != app._app_state.selection.selections:
        app._app_state.selection.selections = new
        app._rebuild_all()



def sel_duplicate(app, sid):
    s = app._sel_by_id(sid)
    if s is None:
        return None
    return app._sel_add(name=f"{s.get('name', 'Selection')} copy",
                         wells=[], replicates=None, source="user")



def sel_by_id(app, sid):
    for s in app._app_state.selection.selections:
        if s.get("id") == sid:
            return s
    return None



def sel_color(app, s) -> str:
    """Effective colour for a selection dict: explicit ``color`` override
    first, otherwise the rank-based fallback so groups without a user-set
    colour stay consistent with their wells."""
    if not isinstance(s, dict):
        return app._rank_color_wells(getattr(s, "wells", None))
    c = s.get("color")
    if isinstance(c, str) and c.strip():
        return c.strip()
    return app._rank_color_wells(s.get("wells"))



def plate_commit_deferred(app) -> None:
    """Run the deferred commit scheduled by a paint event. No-op if
    a drag_finished handler already cleared the pending flag (which
    means it already committed in the same tick)."""
    if not app._app_state.selection.plate_commit_pending:
        return
    app._app_state.selection.plate_commit_pending = False
    on_plate_sel_change(app)



def labels_clear_all(app) -> None:
    if app._app_state.dataset.well_labels:
        app._app_state.dataset.well_labels.clear()
        label_grid_refresh(app)
        app._invalidate_stats_cache()
        _schedule_sample_def_autosave(app)



def rep_select(app, idx: int) -> None:
    # Legacy bridge: select the idx-th rep_set-source selection.
    sels = [s for s in app._app_state.selection.selections if s.get("source") == "rep_set"]
    if 0 <= idx < len(sels):
        app._sel_select(sels[idx].get("id"))

