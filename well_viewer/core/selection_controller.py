"""Plate/sidebar selection handlers for WellViewerApp."""

from __future__ import annotations

from util.json_write import write_json_atomic
from PySide6.QtCore import QObject
from well_viewer.core.safe import swallowed

# Selection-behaviour fallbacks for registry-less callers (e.g. lightweight test
# mocks). The real app always resolves these from the feature registry's
# ``per_well_selection`` / ``single_well_selection`` descriptor flags — see the
# helpers below; these tuples only seed the fallback.
#
# Per-well tabs keep per-well plate selection even when replicate sets / stat
# groups exist (rep-set mode otherwise makes the plate passive): Home /
# Review CSV need user-chosen wells for Cell Gating / Add Prefix /
# row-loading, and the Cell Browser draws its gallery from the active selection.
_PER_WELL_TAB_NAMES = ("Home", "Review CSV", "Cell Browser")
_SINGLE_WELL_TAB_NAMES = ("smFISH",)


def _is_per_well_tab(ctx, tab_name: str) -> bool:
    app = ctx.window  # transitional bridge (A3)
    feats = getattr(app, "_features", None)
    if feats is not None:
        d = feats.get(tab_name)
        return bool(d and d.per_well_selection)
    return tab_name in _PER_WELL_TAB_NAMES


def _is_single_well_tab(ctx, tab_name: str) -> bool:
    feats = ctx.features
    if feats is not None:
        d = feats.get(tab_name)
        return bool(d and d.single_well_selection)
    return tab_name in _SINGLE_WELL_TAB_NAMES


# Tabs that EDIT individual wells (cell-gating / add-prefix / row-load) and so
# need the plate ACTIVE as a per-well selector even when groups exist. The Cell
# Browser is deliberately NOT here: it MIRRORS the plotting layer's active wells
# (the visible groups + solo wells), so in a Scatter + Cell Browser split the
# plate stays group-driven and clicking a well toggles its group (driving both
# panes), instead of the plate flipping to a per-well selector that ignored the
# groups and stopped affecting the scatter.
#
# "Home" is here because the sample-definition editors are tabs of that page.
# This is page-level, not tab-level — ``current_centre_tab`` reports "Home" for
# every one of its tabs — so the plate stays an active per-well selector on
# Overview too. That matches Overview's own plate, whose clicks select wells.
_WELL_EDITING_TAB_NAMES = ("Home", "Review CSV")


def _is_well_editing_tab(ctx, tab_name: str) -> bool:
    return tab_name in _WELL_EDITING_TAB_NAMES


def _plate_passive_in_group_mode(ctx) -> bool:
    app = ctx.window  # transitional bridge (A3)
    """Whether the sidebar plate runs in passive (group-visibility) mode.

    Passive in rep-set (group) mode unless a *well-editing* pane (Home /
    Review CSV) is on screen — those need the plate as a per-well
    selector. The Cell Browser does NOT force the plate active: it mirrors the
    plotting layer's group wells, so a Scatter + Cell Browser split keeps the
    plate group-driven (no focus-dependent flip; clicking toggles a group and
    updates both panes).

    Outside Split View ``visible_centre_tabs`` collapses to the current leaf, so
    a single plotting tab in group mode stays passive exactly as before."""
    if not app._app_state.selection.selections:
        return False
    try:
        from well_viewer.core.tab_controller import visible_centre_tabs
        visible = set(visible_centre_tabs(ctx)) or {_active_tab(ctx)}
    except Exception:
        visible = {_active_tab(ctx)}
    return not any(_is_well_editing_tab(ctx, t) for t in visible)


def _active_tab(ctx) -> str:
    app = ctx.window  # transitional bridge (A3)
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


def _visible_tabs(ctx) -> list[str]:
    app = ctx.window  # transitional bridge (A3)
    """Leaf names of every centre tab on screen (Split View shows two).

    Prefers ``app._visible_centre_tabs``; falls back to the single active tab
    so the non-split path is unchanged.
    """
    getter = getattr(app, "_visible_centre_tabs", None)
    if callable(getter):
        try:
            tabs = list(getter())
            if tabs:
                return tabs
        except Exception:
            swallowed()  # was: pass (silent except)
    tab = _active_tab(ctx)
    return [tab] if tab else []


def _refresh_tab_view(ctx, tab: str, *, redrew: bool) -> bool:
    app = ctx.window  # transitional bridge (A3)
    """Refresh one visible tab's view after a selection change.

    ``redrew`` carries whether the generic line/distribution/heat-map redraw
    (``app._redraw``) already ran this pass; the return value updates it so two
    generic-redraw panes split side-by-side don't redraw the shared figures
    twice. Non-generic branches return ``redrew`` unchanged.
    """
    # Per-tab selection refresh is dispatched through the feature registry's
    # ``on_selection_changed`` hooks (bodies relocated from this ladder, see
    # ``features/registry.py``). A hook returns without a generic redraw (the
    # feature refreshed its own view), so ``redrew`` is unchanged; tabs with no
    # hook fall through to the shared ``app._redraw()``.
    desc = app._features.get(tab) if hasattr(app, "_features") else None
    if desc is not None and desc.on_selection_changed is not None:
        desc.on_selection_changed(ctx)
        return redrew
    if not redrew:
        app._redraw()
    return True


def _announce_selection(ctx) -> None:
    """Put the current well selection on the status bar. One writer, one place.

    Counts what the user chose, not what a tab decided to plot: a page that falls
    back to every well when nothing is picked must not make the status line claim
    those wells were selected.
    """
    from well_viewer.domain.plot_members import plot_replicate_sets

    sel = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths or {}
    n = len([w for w in sel.selected_wells if w in well_paths])
    if n:
        ctx.notify.set_status(f"{n} well{'s' if n != 1 else ''} selected")
        return
    # A visible replicate set is a selection expressed a different way, and the
    # plots draw it. Saying "No wells selected" while they do is the same
    # contradiction, one level up, that this branch set out to remove.
    n_sets = len(list(plot_replicate_sets(ctx)))
    if n_sets:
        ctx.notify.set_status(f"{n_sets} group{'s' if n_sets != 1 else ''} shown")
    else:
        ctx.notify.set_status("No wells selected")


def apply_selection_refresh(ctx) -> None:
    """Refresh the sidebar and every visible tab's view after a selection change.

    The subscriber body for the ``selection_changed`` bus signal: every
    selection mutator emits the signal, and ``SelectionRefreshCoordinator``
    runs this in response. It does more than a plot redraw — the sidebar
    plate-map plus a per-tab view refresh — which is why it has its own
    coordinator rather than sharing ``RedrawCoordinator``.

    With Split View a single change can touch two panes, so the per-tab
    refresh runs for each visible leaf; off split this is exactly one tab and
    the behaviour is unchanged.

    The empty-state pass runs first and covers **every** registered stack, not
    just the visible tabs. It has to: the loop below only touches
    ``_visible_tabs``, and a feature carrying an ``on_selection_changed`` hook
    (Review CSV, smFISH, Multivariate, Spatial, Bar Plots, Scatter) returns
    without ever reaching ``plot_orchestrator.redraw_app``, which is where the
    other empty-state refresh lives. Anything less leaves a background page
    showing whatever state it held when the user last looked at it.

    The status line is written from here for the same reason. It used to be
    echoed by whichever tab happened to redraw, so it reported the selection on
    the pages that bothered and kept a stale sentence everywhere else — the
    Segmentation page read "No wells selected." over a lit A01 while its own rail
    said "Selected: A01", because a tab the user had left wrote it. One writer on
    the one signal that means the selection changed.
    """
    app = ctx.window  # transitional bridge (A3)
    from well_viewer.core.ui_helpers import refresh_empty_state_pages
    refresh_empty_state_pages(app)
    _announce_selection(ctx)
    app._refresh_sidebar_map()
    redrew = False
    for tab in _visible_tabs(ctx):
        redrew = _refresh_tab_view(ctx, tab, redrew=redrew)


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
        apply_selection_refresh(self._app._ctx)


def on_plate_sel_change(ctx) -> None:
    # SelectionState diffs the new selection against its prev_sel snapshot to
    # resolve last_sel, then re-snapshots.
    sel = ctx.state.selection
    sel.commit_plate_change()
    nb = getattr(ctx.window, "_notebook", None)
    if nb is not None:
        tab = nb.currentName()
        if _is_single_well_tab(ctx, tab) and len(sel.selected_wells) > 1:
            keep = (sel.last_sel if sel.last_sel in sel.selected_wells
                    else next(iter(sel.selected_wells)))
            sel.selected_wells = {keep}
            sel.prev_sel = set(sel.selected_wells)
    ctx.bus.selection_changed.emit(ctx.state.selection)


def _well_rc(ctx, w):
    app = ctx.window  # transitional bridge (A3)
    try:
        return app._parse_rc(w)
    except Exception:
        return ("", "")


def _set_groups_hidden(ctx, sels, *, target=None) -> None:
    app = ctx.window  # transitional bridge (A3)
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
        # Visibility, not definition: the statistics on this plate are still
        # correct, so they are kept.
        #
        # This is ONE call, not two. An earlier version notified and then called
        # ``_rebuild_all()`` beside it — whose first act is to invalidate the
        # stats cache — so the whole plate's statistics were discarded a line
        # after being deliberately preserved, and the change bought nothing at
        # all. The refresh has to know not to invalidate; being told separately
        # is not the same thing.
        app._rebuild_all(invalidate=False)


PLOTTING_TABS = frozenset({
    "Line Graphs", "Bar Plots", "Scatter Plot", "Distribution", "Heat Map",
})

# Per-well tabs that consume the replicate-mode *active set* (visible groups +
# solo wells) like the plotting tabs — so in rep-mode the plate highlights their
# active wells and a well click toggles that group. Spatial / Multivariate read
# ``rep_sets_active`` (not ``selected_wells``, which is empty in passive
# rep-mode); without this they fall back to first/all wells.
_REP_MIRROR_TABS = PLOTTING_TABS | frozenset({"Spatial", "Multivariate"})


def _select_plate_line(ctx, value: str, rc_index: int) -> None:
    """Toggle a whole plate row or column. *rc_index* picks which half of
    ``_well_rc`` to match on — 0 for the row letter, 1 for the column number.

    Two modes, and which one applies depends on whether replicate groups exist:

    * **rep-mode** (groups defined) — a click toggles ``hidden`` on every group
      crossing the line. Groups that don't touch it are untouched. This holds on
      plotting and non-plotting tabs alike.
    * **plain mode** — the line's wells are added to the selection unless all of
      them are already in it, in which case they are removed. That asymmetry is
      what makes a second click on a fully-selected row clear it.
    """
    app = ctx.window  # transitional bridge (A3)
    if app._app_state.selection.selections:  # rep-mode
        crossing = [
            s for s in app._app_state.selection.selections
            if any(_well_rc(ctx, w)[rc_index] == value
                   and w in app._app_state.dataset.well_paths
                   for w in (s.get("wells") or []))
        ]
        if crossing:
            _set_groups_hidden(ctx, crossing)
        return

    labels = [lbl for lbl in app._app_state.dataset.well_paths
              if _well_rc(ctx, lbl)[rc_index] == value]
    if not labels:
        return
    selected = app._app_state.selection.selected_wells
    if any(lbl not in selected for lbl in labels):
        selected.update(labels)
    else:
        selected.difference_update(labels)
    on_plate_sel_change(ctx)


def select_row(ctx, row: str) -> None:
    _select_plate_line(ctx, row, 0)


def select_col(ctx, col: str) -> None:
    _select_plate_line(ctx, col, 1)


def select_all(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    if app._app_state.selection.selections:  # rep-mode: show every group
        _set_groups_hidden(ctx, app._app_state.selection.selections, target=False)
    else:
        app._app_state.selection.select_wells(app._app_state.dataset.well_paths.keys())
        app._event_bus.selection_changed.emit(app._app_state.selection)


def select_none(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    sel = app._app_state.selection
    # Clear any individually-activated (solo) wells regardless of mode, so
    # "Deselect all" empties everything the plate is showing as active — not
    # just the groups.
    solo = getattr(sel, "active_solo_wells", None)
    cleared_solo = bool(solo)
    if cleared_solo:
        solo.clear()
    # Mirror the sidebar's mode decision: group ("rep") mode applies only when
    # saved selections exist AND the current tab isn't a per-well tab. On a
    # per-well tab (Cell Browser / Home / Review CSV) the plate
    # selects individual wells, so "Deselect all" must clear ``selected_wells``
    # — even when saved groups also exist. Branching on ``sel.selections`` alone
    # (the old behaviour) only hid the groups and left the per-well selection,
    # so the button looked broken on those tabs.
    try:
        _tab = app._current_centre_tab()
    except Exception:
        _tab = ""
    rep_mode = bool(sel.selections) and not _is_per_well_tab(ctx, _tab)
    if rep_mode:  # group mode: hide every group
        # _set_groups_hidden only refreshes when a group's hidden flag actually
        # flips; if every group was already hidden (or there were none to flip)
        # but we cleared solo wells, repaint + redraw explicitly so the button
        # visibly does something instead of silently no-op'ing.
        any_visible = any(not s.get("hidden") for s in sel.selections)
        _set_groups_hidden(ctx, sel.selections, target=True)
        if cleared_solo and not any_visible:
            app._refresh_sidebar_map()
            app._event_bus.selection_changed.emit(sel)
    else:
        sel.clear_selection()
        app._event_bus.selection_changed.emit(sel)


def on_sidebar_plate_selection_changed(ctx, ids) -> None:
    app = ctx.window  # transitional bridge (A3)
    # Fires per-cell during a drag and once for a click; also fires when
    # _refresh_sidebar_map_now calls setSelectedWellIds / setEnabledWells
    # (those corrections converge harmlessly).
    #
    # Home / Review CSV exception: even when groups exist
    # (rep-set mode), these tabs need per-well selection so Add Prefix /
    # Add Suffix / row-filter can act on user-chosen wells. We let the
    # click through and update _selected_wells normally;
    # _refresh_sidebar_map_now mirrors the same exception so the painted
    # selection doesn't get wiped on the next refresh tick.
    from PySide6.QtCore import QTimer
    if _plate_passive_in_group_mode(ctx):
        return  # rep-set mode, no per-well pane visible: the plate is passive
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


def on_sidebar_plate_drag_finished(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    if _plate_passive_in_group_mode(ctx):
        return
    # Cancel any pending deferred commit from the paint events that
    # led here -- we're committing right now.
    app._app_state.selection.plate_commit_pending = False
    on_plate_sel_change(ctx)


def on_sidebar_plate_corner_toggled(ctx, select_all_target: bool) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Plate corner all/none toggle while the plate is passive (group mode).

    In group mode the plate doesn't own per-well selection, so the corner acts
    on group visibility: if any group is currently visible, hide them all;
    otherwise show them all. (Falls back to the plain select/clear when no
    groups are defined, though the corner only emits this in passive mode.)
    """
    sels = app._app_state.selection.selections
    if sels:
        any_visible = any(not s.get("hidden") for s in sels)
        select_none(ctx) if any_visible else select_all(ctx)
        return
    select_all(ctx) if select_all_target else select_none(ctx)


def on_sidebar_plate_well_activated(ctx, well_id: str) -> None:
    app = ctx.window  # transitional bridge (A3)
    # On a plotting tab, a single click on a well "activates" that well
    # and (if it belongs to a saved group) every other well in the same
    # group. Solo-well clicks toggle membership in active_solo_wells.
    if well_id not in app._app_state.dataset.well_paths:
        return
    try:
        tab = app._current_centre_tab()
    except Exception:
        tab = ""
    if tab in _REP_MIRROR_TABS:
        sid = app._sel_id_for_well(well_id)
        if sid is not None:
            app._sel_toggle_hidden(sid)
            return
        if well_id in app._app_state.selection.active_solo_wells:
            app._app_state.selection.active_solo_wells.discard(well_id)
        else:
            app._app_state.selection.active_solo_wells.add(well_id)
        app._refresh_sidebar_map()
        on_plate_sel_change(ctx)
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


def on_sidebar_plate_row_activated(ctx, row: str) -> None:
    app = ctx.window  # transitional bridge (A3)
    if app._app_state.selection.selections:
        select_row(ctx, row)
    else:
        # The widget already toggled the row and synced _selected_wells via
        # the preceding selectionChanged; do the heavy refresh once.
        on_plate_sel_change(ctx)


def on_sidebar_plate_col_activated(ctx, col: str) -> None:
    app = ctx.window  # transitional bridge (A3)
    if app._app_state.selection.selections:
        select_col(ctx, col)
    else:
        on_plate_sel_change(ctx)


def on_sidebar_plate_well_dropped(ctx, well_id: str, token: str) -> None:
    if not token:
        return
    try:
        ctx.features.call("heatmap.well_dropped", ctx, "palette", None, token)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Heat map well drop handling failed")


def import_selections_from_json(ctx) -> None:
    """Import a selections JSON file (the same schema used by Save) and
    merge it into ``ctx.state.selection.selections``. Existing names get a ``_v2``
    suffix on collision so nothing already loaded is overwritten."""
    import json
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    path, _ = QFileDialog.getOpenFileName(
        ctx.window, "Import selections from JSON", "", "JSON (*.json);;All files (*)",
    )
    if not path:
        return
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except Exception as exc:
        QMessageBox.critical(ctx.window, "Import failed", f"Could not parse JSON:\n{exc}")
        return

    # Accept either a v2 sample-definitions block (with a 'selections' key)
    # or a bare list of selection dicts.
    items = None
    if isinstance(payload, dict):
        items = payload.get("selections") or payload.get("sample_definitions", {}).get("selections")
    elif isinstance(payload, list):
        items = payload
    if not items:
        QMessageBox.warning(ctx.window, "Import", "No selections found in that file.")
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
            sel_add(ctx, name=name, wells=list(wells),
                    replicates=replicates, source=source)
            added += 1
        except Exception:
            _logger = __import__("logging").getLogger("well_viewer.runtime_app")
            _logger.exception("Import selection failed for %r", name)

    try:
        groups_centre_refresh(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    ctx.notify.toast(f"Imported {added} selection(s) from {path}.", kind="success")



def labels_apply_affix(ctx, *, where: str) -> None:
    """Prompt for text and prepend/append it to the labels of every
    well currently selected.

    Selection precedence: the Well-Labels grid's selection wins when
    non-empty (rows / columns / Shift-clicked cells); otherwise the
    sidebar's plate selection is used as a fallback.
    """
    from well_viewer.core.ui_helpers import ask_name_dialog
    from well_viewer.domain.viewer_state import parse_rc

    grid = ctx.widgets.get_or_none("_lbl_grid")
    grid_sel: list[str] = []
    if grid is not None:
        try:
            grid_sel = list(grid.selectedTokens())
        except Exception:
            grid_sel = []
    if grid_sel:
        selected = sorted(grid_sel, key=parse_rc)
    else:
        selected = sorted(ctx.state.selection.selected_wells, key=parse_rc)
    if not selected:
        ctx.notify.set_status(
            f"Add {where.title()}: no wells selected — click a row/column "
            "header on the Well Labels grid or select wells on the sidebar plate."
        )
        return

    title = "Add Prefix" if where == "prefix" else "Add Suffix"
    verb = "prepend to" if where == "prefix" else "append to"
    plural = "s" if len(selected) != 1 else ""
    prompt = (f"Text to {verb} the custom label of {len(selected)} "
              f"selected well{plural}:")
    text = ask_name_dialog(ctx.window, title=title, prompt=prompt, default="", strip=False)
    if not text:
        return

    labels = ctx.state.dataset.well_labels
    for tok in selected:
        current = labels.get(tok, "")
        new = (text + current) if where == "prefix" else (current + text)
        if new:
            labels[tok] = new
        else:
            labels.pop(tok, None)

    invalidate_stats_cache(ctx)
    ctx.features.call("labels.grid_refresh", ctx)
    _schedule_sample_def_autosave(ctx)
    ctx.notify.set_status(
        f"Added {where} '{text}' to {len(selected)} well label{plural}."
    )



def update_active_group_label(ctx) -> None:
    """Mirror ``_current_selection_id`` into the Groups sub-tab banner.

    Shows the active group's name (bold, accent-coloured) when one is
    selected, or a "click a group below" prompt when none is — so a
    click on the sidebar plate always has a visible target.
    """
    lbl = ctx.widgets.get_or_none("_rep_active_group_lbl")
    if lbl is None:
        return
    sel_state = ctx.state.selection
    sid = sel_state.current_selection_id
    sel = next((s for s in sel_state.selections if s.get("id") == sid), None) if sid else None
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
    n_wells = sum(1 for w in (sel.get("wells") or []) if w in ctx.state.dataset.well_paths)
    lbl.setText(f"{name}  ·  {n_wells} well(s)")
    if _theme is not None:
        lbl.setStyleSheet(
            f"QLabel#ActiveGroupValue {{ "
            f"color: {_theme.Colors.accent}; "
            f"font-size: {_theme.Typography.small_size}px; "
            f"font-weight: 700; }}"
        )



def refresh_sidebar_map_now(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
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
    # Home's definition tabs (Cell Gating / Add Prefix / Add Suffix all
    # need user-chosen wells), and Review CSV (the user has to pick
    # which wells' rows to load). For those tabs the plate stays in
    # ``select`` mode and ``_selected_wells`` is honoured.
    try:
        tab_name = app._current_centre_tab()
    except Exception:
        tab_name = ""
    # Passive (group-visibility) only when no per-well pane is on screen — must
    # stay in lock-step with the plate handlers, or the next refresh tick would
    # repaint passive and wipe the per-well selection the handler just committed.
    rep_mode = _plate_passive_in_group_mode(ctx)

    colors: dict[str, object] = {}
    if rep_mode:
        for s in app._app_state.selection.selections:
            full_c = app._sel_color(s)
            shade = app._mute_color(full_c) if s.get("hidden") else full_c
            for tok in (s.get("wells") or []):
                if tok in app._app_state.dataset.well_paths:
                    colors[tok] = shade
    else:
        # Rank colours tint each selected well by its absolute plate position
        # ("the plate is the legend") — the SAME colour every plot renderer uses
        # for that well (bar/line/scatter/multivariate all call ``color_for_well``).
        # Pushed on EVERY per-well tab so a well shows one consistent colour in the
        # picker regardless of which tab is active (and so the picker matches the
        # plotted point/series colours). Selection is additionally shown by the
        # accent highlight (``setSelectedWellIds`` below).
        for tok in app._app_state.selection.selected_wells:
            if tok in app._app_state.dataset.well_paths:
                colors[tok] = app._rank_color_well(tok)

    # smFISH suppresses row/col header selection and forces single-well.
    #
    # N11 — in Split View the one sidebar plate serves BOTH visible panes, so the
    # single-well constraint is reconciled **least-restrictively**: force it only
    # when EVERY visible leaf wants single-well selection. Otherwise focusing a
    # single-well pane (smFISH / Review CSV) next to a multi-well pane (Heat Map /
    # plots) would collapse the shared selection to one well. ``_is_single_well_tab``
    # reads each feature's registry descriptor; outside split ``visible`` is just
    # the current leaf, so behaviour is unchanged.
    try:
        from well_viewer.core.tab_controller import visible_centre_tabs
        visible = set(visible_centre_tabs(ctx)) or {tab_name}
    except Exception:
        visible = {tab_name}
    smfish = bool(visible) and all(_is_single_well_tab(ctx, t) for t in visible)

    plate.setEnabledWells(list(app._app_state.dataset.well_paths.keys()))
    plate.setSelectionMode("passive" if rep_mode else "select")
    plate.setRowColumnSelectable(not smfish)
    plate.setSingleSelectionMode(smfish and not rep_mode)
    plate.clearWellColors()
    plate.setWellColors(colors)
    # Plotting tabs in rep-mode paint the visible group wells (clicks toggle a
    # group's ``hidden`` flag). The Cell Browser MIRRORS that group set — it must
    # paint the same wells (not the legacy empty plate), so the highlight is
    # identical whether the Scatter or the Cell Browser pane is focused (no
    # focus-driven flip / apparent "loss"). Other rep-mode tabs (Image Table /
    # smFISH / etc) keep the legacy "no per-well selection" override.
    plotting_tab = tab_name in _REP_MIRROR_TABS
    mirror_tab = plotting_tab or tab_name == "Cell Browser"
    if rep_mode and not mirror_tab:
        plate.setSelectedWellIds([])
    elif rep_mode and mirror_tab:
        # In rep-mode the "active" set is exactly the union of every non-hidden
        # group's wells (+ solo wells). ``_selected_wells`` is not used here —
        # clicks toggle each group's ``hidden`` flag so the visual state and the
        # plot's ``_rep_sets_active()`` filter stay in lock-step.
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



def sel_export_one(ctx, sid) -> None:
    """Export a single saved selection (by id) to a standalone JSON file."""
    s = sel_by_id(ctx, sid)
    if s is None:
        return
    from PySide6.QtWidgets import QFileDialog
    default_name = f"{s.get('name') or 'selection'}.json"
    path, _ = QFileDialog.getSaveFileName(
        ctx.window, "Export selection", default_name, "JSON (*.json)"
    )
    if not path:
        return
    try:
        from well_viewer.domain.selections_model import to_block
        block = to_block([s], current_id=s.get("id"))
        write_json_atomic(path, block)
        ctx.notify.toast(f"Exported '{s.get('name')}' to {path}.", kind="success")
    except Exception as exc:
        ctx.notify.toast(f"Export failed: {exc}", kind="danger", msec=6000)



def sel_strip_wells(ctx, wells, *, keep_id=None) -> None:
    """A well belongs to ≤1 group: remove ``wells`` from every selection but
    ``keep_id``, pruning their replicates to the survivors. Also drop the
    same wells from ``ctx.state.selection.selected_wells`` so the plotting tabs don't
    keep treating them as solo wells once they've joined a group (the
    sidebar plate becomes passive in rep_mode and the user can't update
    the set themselves)."""
    sel_state = ctx.state.selection
    wset = set(wells or [])
    if not wset:
        return
    for s in sel_state.selections:
        if s.get("id") == keep_id:
            continue
        nw = [w for w in (s.get("wells") or []) if w not in wset]
        if nw != s.get("wells"):
            s["wells"] = nw
            s["replicates"] = None  # a group's wells are its implicit replicate set
    if wset & sel_state.selected_wells:
        sel_state.selected_wells = sel_state.selected_wells - wset



def sel_set_color(ctx, sid, color) -> None:
    """Recolour a saved selection. ``color`` is a ``#RRGGBB`` hex string
    (or anything accepted by QColor); empty / falsy clears the override
    and lets the rank-based colour fall back through."""
    s = sel_by_id(ctx, sid)
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
    ctx.coordinator.rebuild_all()



def groups_centre_refresh(ctx) -> None:
    """Refresh all sample-definition panels.

    Card lists are only rebuilt when one of the definition tabs is showing
    (expensive). Plate maps are always updated (cheap).

    The test is the *tab*, not the page. The definitions are tabs of Home, and
    Home is what the app opens on — so asking whether the page is current would
    be true from startup and would rebuild both card lists on every selection
    change, whatever the user is actually looking at.
    """
    enforce_well_exclusivity(ctx)
    tab_visible = False
    nb = getattr(ctx.window, "_notebook", None)
    sub_tabs = ctx.runtime.home.subtabs
    if nb is not None and sub_tabs is not None:
        try:
            tab_visible = (nb.currentName() == "Home"
                           and sub_tabs.tabText(sub_tabs.currentIndex()) != "Overview")
        except Exception:
            swallowed()  # was: pass (silent except)

    if tab_visible:
        ctx.features.call("groups.panel_refresh", ctx)
        ctx.features.call("labels.grid_refresh", ctx)
    ctx.features.call("groups.map_refresh", ctx)
    update_active_group_label(ctx)
    # The readouts count the user's groups, so adding or hiding one changes what
    # they say. Core to core: the sentence is not a feature's, because the
    # pooling decision it reports belongs to every tab at once.
    from well_viewer.core.group_by_controller import refresh_grouping_readouts
    refresh_grouping_readouts(ctx)



def set_selected_wells(ctx, new_set, *, commit: bool = True) -> None:
    """Single mutation point for ``_selected_wells``.

    Callers that mutated the set directly (heatmap drag, smFISH tab
    activation, batch-export panels) bypassed ``_prev_sel`` tracking
    and the redraw fan-out, so the next selection diff in
    ``selection_controller.on_plate_sel_change`` mis-attributed
    additions / removals across tabs. Funnel everything through here.

    Takes ``ctx`` so feature callers (heatmap drag, the Plot Groups drawer)
    thread the context instead of the god-object. The prev-sel snapshot + set
    mutation are pure typed state; only the two downstream fan-outs
    (``_refresh_sidebar_map`` debounce and ``on_plate_sel_change``) are still
    app-native, so they are reached through a single ``app`` handle bridge.
    """
    sel = ctx.state.selection
    new_set = set(new_set or [])
    if new_set == sel.selected_wells:
        return
    # Snapshot the previous selection so on_plate_sel_change can
    # compute an accurate diff if it runs after this.
    sel.prev_sel = set(sel.selected_wells)
    sel.selected_wells = new_set
    # The sidebar-map debounce and the plate-change commit are named on the
    # context (genuine app-orchestration service + ctx-native commit).
    ctx.coordinator.refresh_sidebar_map()
    if commit:
        on_plate_sel_change(ctx)


def has_plottable_selection(ctx) -> bool:
    """Is there anything for a selection-driven tab to draw?

    True when the user has picked wells on the plate **or** has at least one
    visible replicate set — a group is a selection expressed a different way, and
    a tab plotting groups is not empty just because no individual well is lit.

    The four renderers that decide whether to draw a placeholder —
    ``barplot/renderer``, ``lineplot/controller`` and ``scatter/controller``
    twice — call this rather than each spelling out ``if not selected and not
    active_rsets``, so the placeholder and the plot cannot come to different
    conclusions about whether the page has anything on it. The docstring used
    to describe that consolidation as done while all four copies were still
    there and this function had no renderer among its callers;
    ``tests/test_one_plottable_selection_predicate.py`` is what makes the claim
    true rather than aspirational.

    Deliberately *not* routed through ``data_loading.iter_plot_groups``: that one
    also yields solo wells and defaults to falling back to every loaded well, so
    it answers "what should I draw?" rather than "is there anything to draw?".
    """

    from well_viewer.domain.plot_members import plot_replicate_sets

    sel = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths or {}
    # Filtered by ``well_paths``, as all four call sites are. A label the dataset does not have plots nothing, so counting
    # it as "plottable" hides the placeholder over a figure with no data in it.
    # That state is reachable and outlives a frame: a saved heat-map layout can
    # push a stale label through ``on_heatmap_click`` -> ``set_selected_wells``,
    # and the plate widget only prunes it on the *debounced* sidebar refresh,
    # which lands after this has already decided what to show.
    if any(w in well_paths for w in sel.selected_wells):
        return True
    return bool(list(plot_replicate_sets(ctx)))


def enforce_well_exclusivity(ctx) -> None:
    """Invariant: a well belongs to **at most one group**. Keep each well in
    the first selection that has it; strip it from the rest. The edit
    helpers already enforce this (edited group wins); this is a safety net
    and cleans up pre-existing overlaps from old saved data."""
    seen: set = set()
    for s in ctx.state.selection.selections:
        if not isinstance(s, dict):
            continue
        kept = []
        for w in (s.get("wells") or []):
            if w in seen:
                continue
            seen.add(w)
            kept.append(w)
        if kept != s.get("wells"):
            s["wells"] = kept
            s["replicates"] = None  # a group's wells are its implicit replicate set



def rebuild_all(ctx, *, invalidate: bool = True) -> None:
    app = ctx.window  # transitional bridge (A3)
    """
    Single authoritative refresh called after ANY data change.
    Always synchronous — no debounce — because it is only called on
    explicit user actions (button clicks / dialog OK), never during drag.

    ``invalidate=False`` is for the one caller whose change cannot move a
    computed value: toggling a group's *visibility*. Everything else here — the
    Groups panel, the plate map, the visible redraws — still has to happen, so
    that caller needs this function rather than a smaller one; what it does not
    need is the plate's statistics thrown away. See
    :func:`note_group_visibility_changed`, which measured that.
    """
    if invalidate:
        app._invalidate_stats_cache()
    else:
        note_group_visibility_changed(ctx)
    app._groups_centre_refresh()          # Home: GROUPS panel + map
    app._refresh_sidebar_map()            # line-graph picker: rep colours
    # Only redraw plots that are actually on screen. An off-screen plot is fully
    # re-rendered from current state by its on_activate hook the moment the user
    # switches to it, so redrawing it here — a synchronous matplotlib cla()+draw
    # — is pure waste. This mirrors the visibility guard the Cell Browser /
    # Spatial / Multivariate block below already uses. Without it, editing a
    # sample group (done on Home → Groups, where NO plot is visible)
    # paid 3–5 off-screen figure re-renders — and a full stats recompute to feed
    # them — on every single well click: the "group edits are ridiculously slow"
    # bug. ``_is_tab_visible`` is true in either split-view pane, so a plot shown
    # side-by-side still updates live.
    if app._is_tab_visible("Bar Plots"):
        ctx.features.redraw(ctx, "Bar Plots")
    if any(app._is_tab_visible(_n) for _n in ("Line Graphs", "Distribution", "Heat Map")):
        app._redraw()
    if app._is_tab_visible("Scatter Plot"):
        try:
            ctx.features.redraw(ctx, "Scatter Plot")
        except Exception:
            __import__("logging").getLogger("well_viewer.runtime_app").exception("Scatter redraw failed")
    # Group edits change which wells the plots draw from, and several per-well
    # features follow the same active set (rep_sets_active): the Cell Browser
    # gallery, the Spatial facets, and the Multivariate projection. Selection-
    # change events already fan out to them, but group mutations come through
    # here and never emit selection_changed — so without this a side-by-side
    # (or focused) Cell Browser / Spatial / Multivariate pane goes stale while
    # the Scatter pane updates. Routed through the feature registry so core
    # stays decoupled from these removable features; each hook self-guards
    # (visibility + Split View), so this is a no-op when the tab isn't shown.
    for _name in ("Cell Browser", "Spatial", "Multivariate"):
        desc = app._features.get(_name) if hasattr(app, "_features") else None
        if (desc is not None and desc.on_selection_changed is not None
                and app._is_tab_visible(_name)):
            try:
                desc.on_selection_changed(ctx)
            except Exception:
                __import__("logging").getLogger("well_viewer.runtime_app").exception(
                    "%s refresh after group edit failed", _name)
    if hasattr(app, "_notebook"):
        if app._current_centre_tab() == "Line Graphs":
            app._show_line_sidebar()
    # Sample definitions auto-persist (debounced), like notes / ratios /
    # view-state — the explicit Save button is now just "save now".
    _schedule_sample_def_autosave(ctx)


def _schedule_sample_def_autosave(ctx) -> None:
    """Queue a debounced write of the sample-definitions block.

    Safe to call from any edit path: the registry coalesces a burst into one
    write and ``autosave_to_pipeline_info`` no-ops silently without a data dir.
    """
    p = ctx.persistence
    if p is not None:
        p.schedule_save("sample_definitions")


def sel_add(ctx, name=None, wells=None, replicates=None, source="user",
             *, make_current=True):
    from well_viewer.domain.selections_model import make_selection
    sel_state = ctx.state.selection
    used_names = {s.get("name") for s in sel_state.selections}
    used_ids = {s.get("id") for s in sel_state.selections}
    sel = make_selection(name=name, wells=wells, replicates=replicates,
                         source=source, used_names=used_names, used_ids=used_ids,
                         fallback_color_idx=len(sel_state.selections))
    sel_strip_wells(ctx, sel["wells"], keep_id=sel["id"])
    sel_state.selections.append(sel)
    if make_current:
        sel_state.current_selection_id = sel["id"]
    ctx.coordinator.rebuild_all()
    return sel["id"]



def sel_set_hidden(ctx, sid, hidden, *, light=False) -> None:
    s = sel_by_id(ctx, sid)
    if s is None:
        return
    if bool(s.get("hidden")) == bool(hidden):
        return
    s["hidden"] = bool(hidden)
    if light:
        ctx.coordinator.refresh_sidebar_map()
        _schedule_sample_def_autosave(ctx)
    else:
        ctx.coordinator.rebuild_all()



def sel_set_composition(ctx, sid, wells=None, replicates=None) -> None:
    from well_viewer.domain.selections_model import _clean_wells
    s = sel_by_id(ctx, sid)
    if s is None:
        return
    if wells is not None:
        s["wells"] = _clean_wells(wells)
        sel_strip_wells(ctx, s["wells"], keep_id=sid)
    # A group is one condition; its wells are implicitly its replicate set, so
    # no sub-partition is stored (the ``replicates`` arg is accepted for
    # signature compatibility but the field is always ``None``).
    s["replicates"] = None
    ctx.coordinator.rebuild_all()



def note_group_visibility_changed(ctx) -> None:
    """A group was shown or hidden. Nothing computed has changed.

    Showing and hiding groups used to go through :func:`invalidate_stats_cache`,
    which throws away every cached statistic on the plate. It never needed to.
    Hiding a group changes which of the already-computed results are *drawn*,
    not what any of them is: ``active_replicate_sets`` filters hidden sets out
    before anything is computed, and no cache key carries ``hidden`` — the keys
    are the set's name, its wells, the timepoint, the threshold and the gate and
    ratio signatures. So a plate of twelve groups paid for twelve groups'
    statistics every time one of them was toggled off.

    Measured rather than reasoned: a full collect over twelve groups of eight
    wells fills 312 entries; toggling ``hidden`` on one group and recomputing
    every one of them from scratch gives 312 comparable keys and zero value
    differences.

    What a toggle does still need is the per-FOV button refresh, which syncs the
    button bank's label and variant from ``plot.use_fov_replicates`` and calls
    its observers. It is kept here and nothing else is: the other two things
    ``invalidate_stats_cache`` does — bumping the Review Image override version
    and cueing the Image Table as stale — both describe a change in *inclusion*,
    and a visibility toggle does not change which cells are included.
    """
    from well_viewer.core.plot_toggles import refresh_fov_btn_state
    refresh_fov_btn_state(ctx)


def invalidate_stats_cache(ctx) -> None:
    """Discard cached group statistics. Call whenever group definitions change.

    For a change in group *visibility* rather than definition, call
    :func:`note_group_visibility_changed` — it costs nothing and keeps the
    plate's statistics.
    """
    ctx.state.stats_cache.invalidate()
    # The per-FOV toggle's label and variant are synced from plot state here so
    # every path that disturbs the groups leaves the button bank consistent.
    note_group_visibility_changed(ctx)
    # Cell gating writes new ``Included`` flags onto cached rows, which the
    # Review Image include map mirrors. This is a CACHE KEY, not a repaint cue:
    # ``data/inclusion.py`` memoises the per-well excluded-nucleus map on this
    # version, and names this function as its bump site. Bumping forces the next
    # refresh to recompute the include map (cheap) while reusing the cached
    # image arrays and boundary mask (expensive). Anything that moves or drops
    # this bump makes that map outlive the edit it describes.
    # No-op when Image Browser is disabled (no review_image slice).
    _review_image = ctx.state.feature_slice("review_image")
    if _review_image is not None:
        _review_image.override_version += 1
    # The Image Table draws only the cells the analysis counts, but it draws
    # them once — an already-rendered grid would keep the outlines of cells this
    # edit just dropped, agreeing with the plots only if the user happened to
    # press Generate. The stamp cues that instead; re-rendering a plate-wide
    # grid here would be seconds of decoding per edit. Unstamped means the lazy
    # tab was never built, so there is nothing to cue.
    _mark_stale = ctx.runtime.image_table.inclusion_changed_cb
    if _mark_stale is not None:
        try:
            _mark_stale()
        except Exception:
            swallowed()  # a failed cue must not abort the edit that caused it



def sel_clear_all(ctx) -> None:
    """Drop every saved selection and clear the current-selection pointer."""
    sel_state = ctx.state.selection
    sel_state.selections = []
    sel_state.current_selection_id = None
    ctx.coordinator.rebuild_all()


def rep_clear_all(ctx) -> None:
    from PySide6.QtWidgets import QMessageBox
    sel_state = ctx.state.selection
    if not sel_state.selections:
        return
    resp = QMessageBox.question(
        ctx.window, "Clear all groups?",
        f"Remove all {len(sel_state.selections)} group(s)?",
        QMessageBox.Yes | QMessageBox.No,
    )
    if resp == QMessageBox.Yes:
        sel_clear_all(ctx)



def sel_rename(ctx, sid, name) -> None:
    from well_viewer.domain.selections_model import _unique_name as _uq
    s = sel_by_id(ctx, sid)
    if s is None:
        return
    new = _uq(name, {x.get("name") for x in ctx.state.selection.selections if x.get("id") != sid})
    old = s.get("name")
    if new == old:
        return
    # Resolve any still-ambiguous control *before* the group changes name: a
    # legacy reference means "whatever `A01` resolves to today", and after the
    # rename today's answer is gone. Then follow the rename through the
    # fold-change state, which names groups in three places.
    _upgrade_fc_controls(ctx)
    s["name"] = new
    ctx.state.plot.rename_member(old, new)
    ctx.coordinator.rebuild_all()


def _upgrade_fc_controls(ctx) -> None:
    """Give every fold-change control reference a concrete kind.

    A reference that resolves to nothing is kept exactly as it was, never
    dropped — the retain-don't-drop contract is what lets a user reload the
    missing group and get their pairing back. The single control needs that
    rule as much as the map does: ``fc_vs_control_on`` is a separate flag, so
    clearing the reference under it leaves the plot claiming a control it no
    longer names, every bar NaN, and the loss written to disk on the next save
    with nothing on screen saying it happened.
    """
    from well_viewer.domain.fold_change import concrete_control

    def _keep(ref):
        concrete = concrete_control(ctx, ref)
        return concrete if concrete is not None else ref

    plot = ctx.state.plot
    if plot.fc_control is not None:
        plot.fc_control = _keep(plot.fc_control)
    if plot.fc_control_map:
        plot.fc_control_map = {
            key: _keep(ref) for key, ref in plot.fc_control_map.items()
        }



def sel_delete(ctx, sid) -> None:
    sel_state = ctx.state.selection
    i = sel_index_of(ctx, sid)
    if i < 0:
        return
    sel_state.selections.pop(i)
    if sel_state.current_selection_id == sid:
        sel_state.current_selection_id = (
            sel_state.selections[min(i, len(sel_state.selections) - 1)].get("id")
            if sel_state.selections else None)
    ctx.coordinator.rebuild_all()



def sel_select(ctx, sid) -> None:
    sel_state = ctx.state.selection
    if sid not in {s.get("id") for s in sel_state.selections}:
        return
    if sid == sel_state.current_selection_id:
        return
    sel_state.current_selection_id = sid
    groups_centre_refresh(ctx)
    ctx.coordinator.refresh_sidebar_map()



def sel_reorder(ctx, ids) -> None:
    sel_state = ctx.state.selection
    by_id = {s.get("id"): s for s in sel_state.selections}
    new = [by_id[i] for i in ids if i in by_id]
    for s in sel_state.selections:
        if s not in new:
            new.append(s)
    if new != sel_state.selections:
        sel_state.selections = new
        ctx.coordinator.rebuild_all()



def sel_duplicate(ctx, sid) -> dict | None:
    s = sel_by_id(ctx, sid)
    if s is None:
        return None
    return sel_add(ctx, name=f"{s.get('name', 'Selection')} copy",
                   wells=[], replicates=None, source="user")



def sel_by_id(ctx, sid) -> dict | None:
    for s in ctx.state.selection.selections:
        if s.get("id") == sid:
            return s
    return None


def sel_index_of(ctx, sid) -> int:
    for i, s in enumerate(ctx.state.selection.selections):
        if s.get("id") == sid:
            return i
    return -1



def sel_color(ctx, s) -> str:
    """Effective colour for a selection dict: explicit ``color`` override
    first, otherwise the rank-based fallback so groups without a user-set
    colour stay consistent with their wells.

    A8 teardown: takes ``ctx`` for uniformity with the colour graph; delegates
    to the pure domain resolver (no god-object)."""
    from well_viewer.domain.selections_model import color_for_selection
    return color_for_selection(s)



def plate_commit_deferred(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Run the deferred commit scheduled by a paint event. No-op if
    a drag_finished handler already cleared the pending flag (which
    means it already committed in the same tick)."""
    if not app._app_state.selection.plate_commit_pending:
        return
    app._app_state.selection.plate_commit_pending = False
    on_plate_sel_change(ctx)



def labels_clear_all(ctx) -> None:
    if ctx.state.dataset.well_labels:
        ctx.state.dataset.well_labels.clear()
        ctx.features.call("labels.grid_refresh", ctx)
        invalidate_stats_cache(ctx)
        _schedule_sample_def_autosave(ctx)



