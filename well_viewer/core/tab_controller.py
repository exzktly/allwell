"""Tab-change / notebook-navigation handlers for WellViewerApp.

Holds the bodies of the centre-stack tab dispatcher (``on_tab_change``)
and the notebook page-changed listener (``on_notebook_current_changed``).
Relocated from ``runtime_app.py`` (teardown Phase 9).
"""
from __future__ import annotations
import logging
from functools import partial
from well_viewer.core.safe import swallowed
from well_viewer.core.lazy import lazy as _lazy

_logger = logging.getLogger("well_viewer.runtime_app")

# The review-image (Segmentation) panel builder is a feature-layer god-method
# (~30 app-bound method lambdas); bind it as a lazy proxy so this core module
# names no feature at import time (a ``lazy(...)`` call is not an import
# statement, so it is invisible to the deferred-feature-import ratchet).
_build_review_image_panel = _lazy(
    "well_viewer.features.preview.panel_view", "build_review_image_panel")

# Split-View extension points. ``split_view.install_split_view`` sets these; while
# they are None the resolvers below behave exactly as the single-tab original, so
# the feature is fully absent until (and unless) it is installed.
split_focus_provider = None     # fn(app) -> focused leaf name | None
split_visible_provider = None   # fn(app) -> set[str] of visible leaves | None
split_nav_intercept = None      # fn(app, key) -> bool (True = consume nav)


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

    # Per-tab activation is dispatched through the feature registry: each
    # descriptor's ``on_activate`` hook owns its sidebar-visibility + refresh
    # (the bodies relocated from this ladder, see ``features/registry.py``). A
    # tab with no descriptor / no hook falls back to the generic plot path
    # (unified picker + redraw) — the same behaviour the old ``else`` gave Line
    # Graphs and any unknown leaf.
    desc = app._features.get(tab)
    if desc is not None and desc.on_activate is not None:
        desc.on_activate(app)
    else:
        app._sidebar_main_frame.setVisible(True)
        app._refresh_sidebar_map()
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
                from well_viewer.features.barplot.renderer import redraw_bars
                redraw_bars(app._ctx)
            elif tab == "Scatter Plot":
                from well_viewer.features.scatter.tab_view import scatter_redraw_active
                scatter_redraw_active(app._ctx)
            elif tab == "Distribution":
                app._redraw()


def image_browser_subtab_app_bridge(
    ctx, *, segmentation_frame=None, refresh_active_tab: bool = False,
) -> None:
    """The residual ``WellViewerApp`` behaviour the Image Browser sub-tab
    lifecycle still needs from the god-object, bundled behind one ctx→app seam.

    The Image Browser sub-tab controller (``features/image_browser/controller``)
    is ctx-native — it reads ``ctx.runtime`` / ``ctx.widgets`` and dispatches the
    ctx-native sub-tab builders. Two leaves in the lifecycle still bottom into
    un-migrated god-method clusters, so they can only be reached through the app
    handle:

    * **Segmentation build** — ``build_review_image_panel`` (a feature-layer
      builder with ~30 app-bound method lambdas) and its channel-combo populate
      ``update_channel_selector`` (the whole channel-state cluster).
    * **Active-tab refresh** — ``on_tab_change`` (the sidebar-swap + per-leaf
      feature-registry dispatch).

    Converting any of these piecemeal would net-*add* god-object unwraps; routing
    both through this single core bridge keeps exactly one such unwrap for the
    whole sub-tab lifecycle, so the feature layer names no god-object. This is the
    sanctioned ctx→app boundary until those clusters migrate centrally.
    """
    app = ctx.app
    if segmentation_frame is not None:
        from well_viewer.core.channel_state_controller import update_channel_selector
        _build_review_image_panel(app, segmentation_frame)
        # The review-image channel combo is born empty (a "GFP" placeholder);
        # when the panel builds *after* a dataset is already loaded (it builds
        # lazily now), nothing has populated it yet, so fill it from the current
        # channel state. Harmless before any load.
        update_channel_selector(app)
    if refresh_active_tab:
        on_tab_change(app, None)


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


def current_centre_tab(ctx) -> str:
    """Return the effective current (focused) leaf tab name.

    When the "Plotting" top-level tab is active the real content lives in a
    nested QStackedWidget; this resolves that one level of indirection so
    callers can always compare against leaf names like "Bar Plots", "Heat Map",
    etc. The "Image Browser" top-level tab is the same shape — descend into it
    too. (smFISH stays a plain top-level tab — it is not an image-review
    surface.)

    In Split View the notebook is hidden and two panes are on screen; the
    "current" tab is the *focused* pane's leaf so sidebar / per-tab dispatch
    follow whichever pane the user last clicked — supplied by
    ``split_focus_provider`` (installed by ``split_view``; ``None`` otherwise).

    A8: takes ``ctx`` so features name the tab state without the god-object; the
    body drives the app's notebook widget, so it unwraps the handle once here.
    """
    app = ctx.app
    if split_focus_provider is not None:
        leaf = split_focus_provider(app)
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


def visible_centre_tabs(ctx) -> "set[str]":
    """Leaf names of every centre tab currently on screen.

    Normally ``{current_centre_tab(ctx)}``. When ``split_visible_provider`` is
    installed (Split View) it returns both panes' leaves, so live-refresh fan-out
    (selection / channel / plot options) updates both panes even when only one is
    focused.
    """
    app = ctx.app
    if split_visible_provider is not None:
        tabs = split_visible_provider(app)
        if tabs:
            return set(tabs)
    cur = current_centre_tab(ctx)
    return {cur} if cur else set()


def is_tab_visible(ctx, name: str) -> bool:
    """True when *name* (a leaf tab) is currently on screen in either pane."""
    return name in visible_centre_tabs(ctx)


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
    """Apply the effective left-rail visibility for the active mode.

    The collapse toggle (``Ctrl+B`` / top-bar button) drives whichever left
    sidebar belongs to the current mode, symmetrically:

    * Review mode → the Review rail (``_sidebar_root``): shown unless collapsed.
      The rail carries Review-only affordances, so it is always hidden in
      Analyze regardless of the toggle.
    * Analyze mode → the Analyze tab's own form sidebar (``_analyze_pane``):
      shown unless collapsed.

    Routing the mode switch and the user toggle through this one helper keeps
    them from fighting over ``setVisible``.
    """
    collapsed = bool(getattr(app, "_sidebar_collapsed", False))
    mode = app.currentMode() if hasattr(app, "currentMode") else "review"
    sidebar = getattr(app, "_sidebar_root", None)
    if sidebar is not None:
        sidebar.setVisible(mode == "review" and not collapsed)
    # The Analyze tab hosts its own left form sidebar; the same toggle hides it.
    analyze = getattr(app, "_analyze_pane", None)
    analyze_sb = getattr(analyze, "_left_sidebar", None)
    if analyze_sb is not None:
        analyze_sb.setVisible(not collapsed)


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
    # Mode-seg lives in allwell's titlebar; emit modeChanged so the
    # shell can re-sync its own seg without poking widgets here. (Split View
    # listens to modeChanged to tear itself down — see split_view.install.)
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

