"""Tab-change / notebook-navigation handlers for WellViewerApp.

Holds the bodies of the centre-stack tab dispatcher (``on_tab_change``)
and the notebook page-changed listener (``on_notebook_current_changed``).
Relocated from ``runtime_app.py`` (teardown Phase 9).
"""
from __future__ import annotations
from well_viewer.features.stats.controller import stats_sync_from_app
from well_viewer.features.stats.controller import stats_update_tp_menu
from well_viewer.features.barplot.renderer import redraw_bars
from well_viewer.features.image_browser.image_table.controller import image_table_refresh_picker
from well_viewer.features.image_browser.image_table.controller import image_table_repopulate_dropdowns
from well_viewer.features.image_browser.review_image.renderer import refresh_review_image
from well_viewer.features.image_browser.review_image.renderer import update_preview
from well_viewer.features.image_browser.playground.controller import playground_repopulate_dropdowns
from well_viewer.features.scatter.controller import update_scatter_menus
import logging
from functools import partial
from PySide6.QtCore import QObject
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer.runtime_app")


def on_tab_change(app, _e=None) -> None:
    """Show/hide the sidebar and refresh whichever tab is now active."""
    if not hasattr(app, "_line_ax_mean"):
        return
    tab = app._current_centre_tab()
    prev_tab = getattr(app, "_last_tab_name", None)
    prev_selected = set(app._app_state.selection.selected_wells)
    # Single deferred import for the cell-gating refreshes used below (the tab is
    # built lazily, so this can't sit at module scope without a cycle).
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_load_cell_areas,
        cell_gating_sync_scatter_gates,
    )

    # Dismiss any open figure configurator on tab change -- the user can
    # re-open it on the new tab via the plot card's sliders IconButton.
    for sb in list((getattr(app, "_export_style_sidebars", {}) or {}).values()):
        try:
            sb._close_dock()
        except Exception:
            swallowed()  # was: pass (silent except)

    app._sidebar_main_frame.setVisible(False)
    app._sidebar_preview_frame.setVisible(False)
    if hasattr(app, "_sidebar_image_table_frame"):
        app._sidebar_image_table_frame.setVisible(False)
    app._sidebar_sample_frame.setVisible(False)
    app._sidebar_stats_frame.setVisible(False)
    if hasattr(app, "_sidebar_cell_browser_frame"):
        app._sidebar_cell_browser_frame.setVisible(False)
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
        # Cell Gating leads this tab; pick up any scatter gates drawn since the
        # last visit (refresh the summary + recompute Included if they changed).
        cell_gating_sync_scatter_gates(app)

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
            cell_gating_load_cell_areas(app)
            cell_gating_sync_scatter_gates(app)

    elif tab == "Cell Browser":
        # Show the canonical well selector (plate) above the Cell Browser's own
        # filter sidebar — its gallery is scoped to the selected wells, so it
        # needs the same well picker every other tab has.
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        if hasattr(app, "_sidebar_cell_browser_frame"):
            app._sidebar_cell_browser_frame.setVisible(True)
        for _refresh in getattr(app, "_cell_browser_refreshers", ()):
            _refresh(app)

    elif tab == "Playground":
        # Playground (Image Browser sub-tab) picks its own well/channel/tp/FOV
        # via in-tab dropdowns; show the canonical plate selector for parity
        # with the other image sub-tabs, then refresh its selectors for any
        # dataset loaded since it last built.
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        try:
            playground_repopulate_dropdowns(app)
        except Exception:
            swallowed()

    elif tab == "Saved Plots":
        # Gallery of previously saved plots — no plate selection needed; just
        # repaint from the (per-dataset) store on each visit.
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
        try:
            from well_viewer.features.saved_plots.controller import (
                refresh_saved_plots_gallery,
            )
            refresh_saved_plots_gallery(app)
        except Exception:
            swallowed()

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
        if _vs.apply_pending_tab_state(app, tab):
            # The restore lands *after* this tab's initial draw above, so that
            # draw used pre-restore defaults — e.g. the Bar timepoint popup,
            # which ``_update_bar_tp_menu`` had defaulted to a single checked
            # timepoint, so the plot showed one timepoint even though the saved
            # selection (now applied) has several. Redraw the plot tabs so they
            # reflect the restored state. (Image Table deliberately does not
            # auto-regenerate, so it is intentionally excluded.)
            if tab == "Bar Plots":
                redraw_bars(app)
            elif tab == "Scatter Plot":
                from well_viewer.features.scatter.tab_view import scatter_redraw_active
                scatter_redraw_active(app)
            elif tab == "Distribution":
                app._redraw()


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
        from well_viewer.domain.fold_change_scopes import flush_dirty_scopes
        flush_dirty_scopes(app)
    except Exception:
        _logger.exception("flush_dirty_scopes failed on tab change")
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



def _resolve_leaf(app, top: str) -> str:
    """Resolve a top-level tab name to its current leaf.

    "Plotting" and "Image Browser" host nested sub-stacks whose current
    sub-tab is the real leaf the rest of the app pattern-matches on; descend
    one level for those. Every other top-level name is already a leaf. Works
    regardless of which page the outer notebook currently shows, so it is safe
    to call for a detached Split-View pane.
    """
    if not top:
        return ""
    if top == "Plotting":
        plotting_nb = getattr(app, "_plotting_notebook", None)
        if plotting_nb is not None and plotting_nb.count() > 0:
            try:
                return plotting_nb.currentName()
            except Exception:
                swallowed()  # was: pass (silent except)
    elif top == "Image Browser":
        sub = getattr(app, "_image_browser_subtabs", None)
        if sub is not None and sub.count() > 0:
            try:
                return sub.tabText(sub.currentIndex())
            except Exception:
                swallowed()  # was: pass (silent except)
    return top


def current_centre_tab(app) -> str:
    """Return the effective current (focused) leaf tab name.

    When the "Plotting" top-level tab is active the real content lives in a
    nested QStackedWidget; this resolves that one level of indirection so
    callers can always compare against leaf names like "Bar Plots", "Heat Map",
    etc. The "Image Browser" top-level tab is the same shape — descend into it
    too. (smFISH stays a plain top-level tab — it is not an image-review
    surface.)

    In Split View the notebook is hidden and two panes are on screen; the
    "current" tab is the *focused* pane's leaf so sidebar / per-tab dispatch
    follow whichever pane the user last clicked.
    """
    if getattr(app, "_split_active", False):
        leaf = _resolve_leaf(app, getattr(app, "_split_focused", None))
        if leaf:
            return leaf
    nb = getattr(app, "_notebook", None)
    if nb is None:
        return ""
    try:
        tab = nb.currentName()
    except Exception:
        return ""
    return _resolve_leaf(app, tab)


def visible_centre_tabs(app) -> "set[str]":
    """Leaf names of every centre tab currently on screen.

    Normally ``{current_centre_tab(app)}``. In Split View it is the resolved
    leaf of each pane, so live-refresh fan-out (selection / channel / plot
    options) updates both panes even when only one is focused.
    """
    if getattr(app, "_split_active", False):
        out = {
            leaf for top in (getattr(app, "_split_tab_a", None),
                             getattr(app, "_split_tab_b", None))
            if (leaf := _resolve_leaf(app, top))
        }
        if out:
            return out
    cur = current_centre_tab(app)
    return {cur} if cur else set()


def is_tab_visible(app, name: str) -> bool:
    """True when *name* (a leaf tab) is currently on screen in either pane."""
    return name in visible_centre_tabs(app)


# ── Split View ──────────────────────────────────────────────────────────────
# Two *different* top-level tabs side-by-side, both updating live. The notebook
# is hidden and its two pages are reparented into ``app._split_pane`` (a
# horizontal QSplitter built in centre_view). The focused pane drives the
# sidebar (``current_centre_tab``); ``visible_centre_tabs`` reports both so the
# live-refresh chokepoints fan out to each. Two instances of the *same* tab are
# out of scope (the feature figures/state are singletons).


def enter_split(app, tab_a: str, tab_b: str) -> None:
    """Show *tab_a* and *tab_b* side-by-side, both live; *tab_a* is focused.

    Both bodies are force-built if still deferred, detached from the hidden
    notebook into ``app._split_pane``, and the focused pane drives the sidebar.
    No-op if the names match or the centre isn't built.
    """
    if not tab_a or not tab_b or tab_a == tab_b:
        return
    nb = getattr(app, "_notebook", None)
    split = getattr(app, "_split_pane", None)
    if nb is None or split is None:
        return
    if getattr(app, "_split_active", False):
        exit_split(app)
    # Force-build deferred bodies before detaching so panes are never blank.
    build = getattr(app, "_centre_build_pending", None)
    if callable(build):
        for t in (tab_a, tab_b):
            try:
                build(t)
            except Exception:
                _logger.exception("Split: deferred build for %r failed", t)
    # Set state first so any notebook signal fired mid-detach resolves to the
    # focused pane rather than a stale leaf.
    app._split_active = True
    app._split_tab_a = tab_a
    app._split_tab_b = tab_b
    app._split_focused = tab_a
    blocked = nb.blockSignals(True)
    try:
        wa = nb.detachPage(tab_a)
        wb = nb.detachPage(tab_b)
    finally:
        nb.blockSignals(blocked)
    if wa is None or wb is None:
        # Roll back a partial detach and bail.
        blocked = nb.blockSignals(True)
        try:
            if wa is not None:
                nb.reattachPage(tab_a, wa)
            if wb is not None:
                nb.reattachPage(tab_b, wb)
        finally:
            nb.blockSignals(blocked)
        app._split_active = False
        app._split_tab_a = app._split_tab_b = app._split_focused = None
        return
    app._split_pane_widgets = {tab_a: wa, tab_b: wb}
    split.addWidget(wa)
    split.addWidget(wb)
    wa.show()
    wb.show()
    split.setSizes([1, 1])
    split.setVisible(True)
    nb.setVisible(False)
    _install_split_focus_filter(app)
    on_tab_change(app, None)


def exit_split(app) -> None:
    """Tear down Split View: reattach both panes (original order) and show the
    notebook again, focused on the former pane A."""
    if not getattr(app, "_split_active", False):
        return
    nb = getattr(app, "_notebook", None)
    split = getattr(app, "_split_pane", None)
    a = app._split_tab_a
    b = app._split_tab_b
    widgets = getattr(app, "_split_pane_widgets", {}) or {}
    wa = widgets.get(a)
    wb = widgets.get(b)
    for w in (wa, wb):
        if w is not None:
            w.setParent(None)
    app._split_active = False
    app._split_tab_a = app._split_tab_b = app._split_focused = None
    app._split_pane_widgets = {}
    _remove_split_focus_filter(app)
    if split is not None:
        split.setVisible(False)
    if nb is not None:
        blocked = nb.blockSignals(True)
        try:
            if wa is not None:
                nb.reattachPage(a, wa)
            if wb is not None:
                nb.reattachPage(b, wb)
            nb.setVisible(True)
            if a:
                nb.setCurrentByName(a)
        finally:
            nb.blockSignals(blocked)
    on_tab_change(app, None)


def focus_split(app, top: str) -> None:
    """Make pane *top* the focused one (drives the sidebar). No-op off split."""
    if not getattr(app, "_split_active", False) or not top:
        return
    if getattr(app, "_split_focused", None) == top:
        return
    app._split_focused = top
    on_tab_change(app, None)


def _resolve_focused_pane(app, obj):
    """Map a clicked widget to the top-level name of the pane that contains it."""
    from PySide6.QtWidgets import QWidget
    widgets = getattr(app, "_split_pane_widgets", {}) or {}
    if not widgets:
        return None
    node = obj
    while isinstance(node, QWidget):
        for top, w in widgets.items():
            if node is w:
                return top
        node = node.parentWidget()
    return None


class _SplitFocusFilter(QObject):
    """App-wide click watcher: while Split View is on, a mouse press anywhere
    inside a pane makes that pane the focused one. Installed once and left in
    place — inert whenever ``_split_active`` is False."""

    def __init__(self, app) -> None:
        # No QObject parent: lifetime is held by ``app._split_focus_filter``.
        # (Avoids requiring ``app`` itself to be a QObject.)
        super().__init__()
        self._app = app

    def eventFilter(self, obj, event):  # noqa: N802
        from PySide6.QtCore import QEvent
        if event.type() != QEvent.MouseButtonPress:
            return False
        try:
            active = bool(getattr(self._app, "_split_active", False))
        except RuntimeError:
            return False  # underlying C++ app already deleted — nothing to do
        if active:
            top = _resolve_focused_pane(self._app, obj)
            if top is not None:
                focus_split(self._app, top)
        return False


def _install_split_focus_filter(app) -> None:
    from PySide6.QtWidgets import QApplication
    filt = getattr(app, "_split_focus_filter", None)
    if filt is None:
        filt = _SplitFocusFilter(app)
        app._split_focus_filter = filt
    qapp = QApplication.instance()
    if qapp is not None:
        qapp.installEventFilter(filt)  # idempotent: Qt re-orders an existing one


def _remove_split_focus_filter(app) -> None:
    """Detach the click watcher from the app — called on exit so it never fires
    against a torn-down app (the cause of a teardown crash under the full suite)."""
    filt = getattr(app, "_split_focus_filter", None)
    if filt is None:
        return
    from PySide6.QtWidgets import QApplication
    qapp = QApplication.instance()
    if qapp is not None:
        qapp.removeEventFilter(filt)


def sync_split_toggle_button(app, checked: bool) -> None:
    """Reflect *checked* on the top-bar split toggle without re-firing it."""
    btn = getattr(app, "_split_toggle_btn", None)
    if btn is None:
        return
    blocked = btn.blockSignals(True)
    try:
        btn.setChecked(checked)
    finally:
        btn.blockSignals(blocked)
    combo = getattr(app, "_split_pane_combo", None)
    if combo is not None:
        combo.setVisible(checked)


def on_split_toggle_clicked(app) -> None:
    """Top-bar split toggle: pane A = current tab, pane B = dropdown pick."""
    btn = app._split_toggle_btn
    combo = app._split_pane_combo
    if btn.isChecked():
        cur = app._notebook.currentName()
        others = [n for n in app._notebook.pageNames() if n and n != cur]
        if not others:
            sync_split_toggle_button(app, False)
            app._toast("Split view needs a second tab to show.", kind="warning")
            return
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(others)
            combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(blocked)
        combo.setVisible(True)
        enter_split(app, cur, others[0])
    else:
        combo.setVisible(False)
        exit_split(app)


def on_split_pane_combo_changed(app, _idx: int) -> None:
    """Re-pair pane A with a newly chosen pane B from the dropdown."""
    if not getattr(app, "_split_active", False):
        return
    new_b = app._split_pane_combo.currentText()
    a = getattr(app, "_split_tab_a", None)
    if not new_b or new_b == a:
        return
    exit_split(app)
    enter_split(app, a, new_b)



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



def apply_sidebar_visibility(app) -> None:
    """Apply the effective left-rail visibility.

    The rail is shown only when **both** conditions hold: the app is in
    Review mode (Analyze carries no Review-only affordances) and the user
    hasn't collapsed it via the toggle (``Ctrl+B`` / top-bar button). Routing
    both the mode switch and the user toggle through this single helper keeps
    them from fighting over ``setVisible``.
    """
    sidebar = getattr(app, "_sidebar_root", None)
    if sidebar is None:
        return
    mode = app.currentMode() if hasattr(app, "currentMode") else "review"
    visible = (mode == "review") and not getattr(app, "_sidebar_collapsed", False)
    sidebar.setVisible(visible)


def toggle_sidebar(app) -> None:
    """User-driven collapse/expand of the left rail.

    Flips the persistent collapse flag, re-applies the effective visibility,
    and syncs the top-bar toggle button's checked state / glyph.
    """
    app._sidebar_collapsed = not getattr(app, "_sidebar_collapsed", False)
    apply_sidebar_visibility(app)
    btn = getattr(app, "_sidebar_toggle_btn", None)
    if btn is not None:
        collapsed = app._sidebar_collapsed
        blocked = btn.blockSignals(True)
        try:
            btn.setChecked(collapsed)
        finally:
            btn.blockSignals(blocked)
        btn.setIconName("panel-right-open" if collapsed else "panel-right-close")
        btn.setToolTip("Show sidebar" if collapsed else "Hide sidebar")


def on_central_pane_changed(app, idx: int) -> None:
    # Switching the Review/Analyze mode tears down Split View — the panes are
    # Review-mode centre tabs and the toggle button lives in the Review chrome.
    if getattr(app, "_split_active", False):
        exit_split(app)
        app._sync_split_toggle_button(False)
    # Mode-seg lives in allwell's titlebar; emit modeChanged so the
    # shell can re-sync its own seg without poking widgets here.
    mode = "analyze" if idx == 1 else "review"
    # Hide the section / well-picker sidebar when Analyze is active —
    # it carries Review-only affordances. Show it again on Review (unless
    # the user has collapsed it). Both rules live in apply_sidebar_visibility.
    apply_sidebar_visibility(app)
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

