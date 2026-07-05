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

# The Sample Definitions centre builder (``build_sample_definitions_centre``) is a
# deeply app-coupled feature-layer builder: it and its sub-builders (Groups /
# Well Labels / Ratios / Pipeline Info / Notes) stamp dozens of ``app._rep_*`` /
# ``app._sel_*`` widgets and wire the ``_clear_sample_definitions_all`` /
# ``_groups_centre_refresh`` / ``_refresh_sidebar_map`` god-methods, so it still
# takes the app handle. It is reached through the single core
# ``build_sample_definitions_app_bridge`` seam below (one god-object unwrap for
# the whole tab build) instead of being named from the feature-layer registry.
_build_sample_definitions_centre = _lazy(
    "well_viewer.features.grouping.replicate_panel_view",
    "build_sample_definitions_centre")

# Split-View extension points. ``split_view.install_split_view`` sets these; while
# they are None the resolvers below behave exactly as the single-tab original, so
# the feature is fully absent until (and unless) it is installed.
split_focus_provider = None     # fn(ctx) -> focused leaf name | None
split_visible_provider = None   # fn(ctx) -> set[str] of visible leaves | None
split_nav_intercept = None      # fn(app, key) -> bool (True = consume nav)


def on_tab_change(ctx, _e=None) -> None:
    """Show/hide the sidebar and refresh whichever tab is now active."""
    from well_viewer.core.plot_orchestrator import redraw_app
    if not ctx.widgets.has("_line_ax_mean"):
        return
    tab = current_centre_tab(ctx)
    prev_tab = ctx.runtime.tab.last_tab_name
    prev_selected = set(ctx.state.selection.selected_wells)

    # Dismiss any open figure configurator on tab change -- the user can
    # re-open it on the new tab via the plot card's sliders IconButton.
    for sb in list((ctx.widgets.get_or_none("_export_style_sidebars") or {}).values()):
        try:
            sb._close_dock()
        except Exception:
            swallowed()  # was: pass (silent except)

    ctx.widgets.get("_sidebar_main_frame").setVisible(False)
    ctx.widgets.get("_sidebar_preview_frame").setVisible(False)
    _it_frame = ctx.widgets.get_or_none("_sidebar_image_table_frame")
    if _it_frame is not None:
        _it_frame.setVisible(False)
    ctx.widgets.get("_sidebar_sample_frame").setVisible(False)
    ctx.widgets.get("_sidebar_stats_frame").setVisible(False)
    _cb_frame = ctx.widgets.get_or_none("_sidebar_cell_browser_frame")
    if _cb_frame is not None:
        _cb_frame.setVisible(False)
    # Heat-map layout configurator lives inside the standard sidebar but is
    # only relevant on the Heat Map tab.
    _hm_frame = ctx.widgets.get_or_none("_heatmap_sidebar_frame")
    if _hm_frame is not None:
        _hm_frame.setVisible(tab == "Heat Map")
    # The sidebar plate-map doubles as the heat-map drag source on the
    # Heat Map tab -- flip each WellButton into drag-source mode while
    # that tab is active.
    sync_heatmap_well_drag_mode(ctx, tab == "Heat Map")

    # Per-tab activation is dispatched through the feature registry: each
    # descriptor's ``on_activate`` hook owns its sidebar-visibility + refresh
    # (the bodies relocated from this ladder, see ``features/registry.py``). A
    # tab with no descriptor / no hook falls back to the generic plot path
    # (unified picker + redraw) — the same behaviour the old ``else`` gave Line
    # Graphs and any unknown leaf.
    desc = ctx.features.get(tab)
    if desc is not None and desc.on_activate is not None:
        desc.on_activate(ctx)
    else:
        ctx.widgets.get("_sidebar_main_frame").setVisible(True)
        ctx.refresh_sidebar_map()
        redraw_app(ctx)

    run_tab_switch_smoke_checks(ctx, prev_tab, tab, prev_selected)
    ctx.runtime.tab.last_tab_name = tab

    # Drain any restored view-state slated for this tab. Safe no-op when
    # nothing is pending; runs after the tab's own dispatch so its widgets
    # are populated and apply step can validate combo items.
    if ctx.runtime.view_state.pending:
        from well_viewer.persistence import view_state as _vs
        if _vs.apply_pending_tab_state(ctx, tab):
            # The restore lands *after* this tab's initial draw above, so that
            # draw used pre-restore defaults — e.g. the Bar timepoint popup,
            # which ``update_bar_tp_menu`` had defaulted to a single checked
            # timepoint, so the plot showed one timepoint even though the saved
            # selection (now applied) has several. Redraw the plot tabs so they
            # reflect the restored state. (Image Table deliberately does not
            # auto-regenerate, so it is intentionally excluded.)
            if tab == "Bar Plots":
                from well_viewer.features.barplot.renderer import redraw_bars
                redraw_bars(ctx)
            elif tab == "Scatter Plot":
                from well_viewer.features.scatter.tab_view import scatter_redraw_active
                scatter_redraw_active(ctx)
            elif tab == "Distribution":
                redraw_app(ctx)


def image_browser_subtab_app_bridge(
    ctx, *, segmentation_frame=None, refresh_active_tab: bool = False,
) -> None:
    """The residual ``WellViewerApp`` behaviour the Image Browser sub-tab
    lifecycle still needs from the god-object, bundled behind one ctx→app seam.

    The Image Browser sub-tab controller (``features/image_browser/controller``)
    is ctx-native — it reads ``ctx.runtime`` / ``ctx.widgets`` and dispatches the
    ctx-native sub-tab builders. ``on_tab_change``, ``update_channel_selector``,
    and the whole ``build_review_image_panel`` callback web (crop / interaction /
    LUT / zoom / colour) are all ctx-native now, so this bridge names no
    god-object — it is a thin lifecycle seam kept for the Image Browser sub-tab
    controller to call.
    """
    if segmentation_frame is not None:
        from well_viewer.core.channel_state_controller import update_channel_selector
        _build_review_image_panel(ctx, segmentation_frame)
        # The review-image channel combo is born empty (a "GFP" placeholder);
        # when the panel builds *after* a dataset is already loaded (it builds
        # lazily now), nothing has populated it yet, so fill it from the current
        # channel state. Harmless before any load.
        update_channel_selector(ctx)
    if refresh_active_tab:
        on_tab_change(ctx, None)


def build_sample_definitions_app_bridge(ctx, frame) -> None:
    """Build the Sample Definitions centre tab.

    The centre builder + its whole sub-builder tree (Groups / Well Labels /
    Ratios / Pipeline Info / Notes) are now ctx-native — they register widgets
    through ``ctx.widgets``, read typed ``ctx.state``, wire buttons directly to
    the ctx-native ``sel_*`` / ``rep_*`` / ``clear_all`` / ``labels_*`` mutators,
    and reach the remaining orchestration services through the frozen accessors
    (``ctx.rebuild_all`` / ``ctx.refresh_sidebar_map`` / ``ctx.toast``). The
    registry calls this core bridge (reached via a ``lazy(...)`` proxy) so the
    feature builder is not named at this core module's import time.
    """
    _build_sample_definitions_centre(ctx, frame)


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
    ctx,
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
        id(ctx.state.selection.selected_wells),
        id(ctx.state.selection.selections),
    )
    if ctx.runtime.tab.selection_model_identity is None:
        ctx.runtime.tab.selection_model_identity = expected_ids
    elif ctx.runtime.tab.selection_model_identity != expected_ids:
        _logger.warning(
            "Selection model identity changed across tab switch: "
            "_selected_wells/_selections should be shared."
        )

    # UI smoke check: switching among Line/Bar/Batch must not mutate the
    # current per-well selection by itself.
    if prev_tab in watched and prev_selected != ctx.state.selection.selected_wells:
        _logger.warning(
            "Tab switch altered selected wells unexpectedly: %s -> %s",
            prev_tab, tab,
        )



def _resolve_leaf(ctx, top: str) -> str:
    """Resolve a top-level tab name to its current leaf.

    "Plotting" and "Image Browser" host nested sub-stacks whose current
    sub-tab is the real leaf the rest of the app pattern-matches on; descend
    one level for those. Every other top-level name is already a leaf. Works
    regardless of which page the outer notebook currently shows, so it is safe
    to call for a detached Split-View pane.

    A8: takes ``ctx``; the plotting notebook is a window widget (``ctx.window``),
    the Image Browser sub-tab bar is a runtime handle (``ctx.runtime.image_browser``).
    """
    if not top:
        return ""
    if top == "Plotting":
        plotting_nb = getattr(ctx.window, "_plotting_notebook", None)
        if plotting_nb is not None and plotting_nb.count() > 0:
            try:
                return plotting_nb.currentName()
            except Exception:
                swallowed()  # was: pass (silent except)
    elif top == "Image Browser":
        sub = ctx.runtime.image_browser.subtabs
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

    A8: fully ctx-native — the split provider takes ``ctx``, and the centre
    notebook is a window widget reached through the blessed ``ctx.window``
    accessor (Qt widget-nav, not god-object behaviour).
    """
    if split_focus_provider is not None:
        leaf = split_focus_provider(ctx)
        if leaf:
            return leaf
    nb = getattr(ctx.window, "_notebook", None)
    if nb is None:
        return ""
    try:
        tab = nb.currentName()
    except Exception:
        return ""
    return _resolve_leaf(ctx, tab)


def visible_centre_tabs(ctx) -> "set[str]":
    """Leaf names of every centre tab currently on screen.

    Normally ``{current_centre_tab(ctx)}``. When ``split_visible_provider`` is
    installed (Split View) it returns both panes' leaves, so live-refresh fan-out
    (selection / channel / plot options) updates both panes even when only one is
    focused.

    A8: the split provider takes ``ctx`` — no god-object handle needed.
    """
    if split_visible_provider is not None:
        tabs = split_visible_provider(ctx)
        if tabs:
            return set(tabs)
    cur = current_centre_tab(ctx)
    return {cur} if cur else set()


def is_tab_visible(ctx, name: str) -> bool:
    """True when *name* (a leaf tab) is currently on screen in either pane."""
    return name in visible_centre_tabs(ctx)


def sync_heatmap_well_drag_mode(ctx, enable: bool) -> None:
    """Toggle WELL_MIME drag/drop on the sidebar plate widget.

    When *enable* is True, the plate exports the well under the pointer via
    the WELL_MIME format on left-button drag (clicks stop toggling
    selection) and accepts dropped tokens as a "return to palette" action
    that unassigns the well from the active heat-map layout (handled by
    ``_on_sidebar_plate_well_dropped``). When False, drag/drop is disabled
    and the plate reverts to plain selection behaviour.
    """
    plate = ctx.widgets.get_or_none("_sidebar_plate")
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

