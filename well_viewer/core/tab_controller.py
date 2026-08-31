"""Tab-change / notebook-navigation handlers for WellViewerApp.

Holds the bodies of the centre-stack tab dispatcher (``on_tab_change``)
and the notebook page-changed listener (``on_notebook_current_changed``).
Relocated from ``runtime_app.py`` (teardown Phase 9).
"""
from __future__ import annotations
import logging
from functools import partial
from well_viewer.core import rail_plate as _rail_plate
from well_viewer.core.safe import swallow, swallowed
from well_viewer.core.lazy import lazy as _lazy

_logger = logging.getLogger("well_viewer.runtime_app")

# Central-pane stack order, shared with runtime_app / allwell so the index↔name
# mapping lives in one place. Review is always index 0; Analyze and Datasets are
# mounted after it in this order (see WellViewerApp.mount*Pane).
PANE_MODES = ("review", "analyze", "datasets")


def pane_mode(idx: int) -> str:
    """The mode name for a central-pane stack index (out-of-range → review)."""
    return PANE_MODES[idx] if 0 <= idx < len(PANE_MODES) else "review"

# The review-image (Segmentation) panel builder is a feature-layer god-method
# (~30 app-bound method lambdas); bind it as a lazy proxy so this core module
# names no feature at import time (a ``lazy(...)`` call is not an import
# statement, so it is invisible to the deferred-feature-import ratchet).
_build_review_image_panel = _lazy(
    "well_viewer.features.preview.panel_view", "build_review_image_panel")

# Split-View extension points. ``split_view.install_split_view`` sets these; while
# they are None the resolvers below behave exactly as the single-tab original, so
# the feature is fully absent until (and unless) it is installed.
split_focus_provider = None     # fn(ctx) -> focused leaf name | None
split_visible_provider = None   # fn(ctx) -> set[str] of visible leaves | None
split_nav_intercept = None      # fn(ctx, key) -> bool (True = consume nav)


#: The per-tab sidebar sections, in the order they are stacked on the rail.
#: Hidden together at the top of every tab change and shown again by whichever
#: ``on_activate`` hooks run. ``_sidebar_cell_browser_frame`` is deliberately in
#: the list even though it is designed to show *alongside* the main frame: it
#: carries no plate, so it never competes for the picker.
SIDEBAR_SECTION_FRAMES = (
    "_sidebar_main_frame",
    "_sidebar_preview_frame",
    "_sidebar_image_table_frame",
    "_sidebar_sample_frame",
    "_sidebar_stats_frame",
    "_sidebar_cell_browser_frame",
)


def _sidebar_section_frames(ctx) -> list:
    """Every per-tab sidebar section that currently exists.

    Missing entries are skipped rather than assumed: several of these frames
    belong to removable features, and a profile build without one registers no
    widget for it.
    """
    out = []
    for name in SIDEBAR_SECTION_FRAMES:
        frame = ctx.widgets.get_or_none(name)
        if frame is not None:
            out.append(frame)
    return out


def _section_plates(ctx, frame) -> list:
    """The well pickers that serve one sidebar section.

    Every rail plate is mounted in the shared slot (``core/rail_plate.py``), so
    a section no longer contains its own picker and walking the frame finds
    nothing. The plates carry the section they serve instead, and that is what
    is matched here. The walk stays as the fallback: a frame built directly by
    a test has no slot to mount into, and its plate is still its child.
    """
    from well_viewer.core.rail_plate import rail_plates, section_of
    from widgets.well_plate_selector import WellPlateSelector
    try:
        out = [p for p in rail_plates(ctx) if section_of(p) is frame]
        return out + list(frame.findChildren(WellPlateSelector))
    except (AttributeError, RuntimeError):
        # A frame whose C++ side is already gone during teardown.
        swallowed("tab_controller: could not scan a sidebar section for plates")
        return []


def on_tab_change(ctx, _e=None) -> None:
    """Show/hide the sidebar and refresh whichever tab is now active.

    The early return asks whether the centre notebook exists yet, because a tab
    change delivered mid-construction has no tab to dispatch on. It used to ask
    whether ``_line_ax_mean`` was registered — a Line Graphs axis, registered
    only by ``features/lineplot/tab_view``. Every profile shipped today keeps
    Line Graphs, so that probe was satisfied in practice; but the feature is
    one of the removable units ``test_feature_removal`` exists for, and in a
    build that dropped it the probe would never be satisfied and this function
    would return for the life of the process — no tab change reaching any tab.
    Nothing below this line touches the line feature.
    """
    from well_viewer.core.plot_orchestrator import redraw_app
    tab = current_centre_tab(ctx)
    if not tab:
        return
    prev_tab = ctx.runtime.tab.last_tab_name
    prev_selected = set(ctx.state.selection.selected_wells)

    # The Decorate ▾ popover is page-level band furniture, so it is not hidden
    # by the per-leaf slot machinery — but only the tabs in
    # ``group_by_controller.DECORATED_TABS`` read Colour by / Split by. Offering
    # it on the other five Plotting leaves is a control that writes state,
    # redraws every tab and changes nothing on screen. Re-evaluated here rather
    # than at build time because the answer is a property of the tab on screen.
    from well_viewer.core.group_by_controller import refresh_decoration_combos
    with swallow("re-evaluate the Decorate button for the new tab"):
        refresh_decoration_combos(ctx)

    # Dismiss any open figure configurator on tab change -- the user can
    # re-open it on the new tab via the plot card's sliders IconButton.
    for sb in list((ctx.widgets.get_or_none("_export_style_sidebars") or {}).values()):
        try:
            sb._close_dock()
        except Exception:
            swallowed()  # was: pass (silent except)

    _frames = _sidebar_section_frames(ctx)
    for _frame in _frames:
        _frame.setVisible(False)
    # Every pane that is on screen gets its sidebar section, not just the
    # focused one. In Split View two leaves are visible at once, and the sidebar
    # used to follow the focus alone: opening Plots beside Statistics left the
    # Statistics group-definition editor hidden under the well picker until the
    # user clicked into the Statistics pane to make it focused — a click that
    # bought nothing, on a panel they were already looking at the tab for.
    _visible_tabs = visible_centre_tabs(ctx)
    _also_visible = [t for t in sorted(_visible_tabs) if t and t != tab]

    # Heat-map layout configurator lives inside the standard sidebar but is
    # only relevant on the Heat Map tab — in either pane.
    _hm_frame = ctx.widgets.get_or_none("_heatmap_sidebar_frame")
    if _hm_frame is not None:
        _hm_frame.setVisible("Heat Map" in _visible_tabs)
    # The sidebar plate-map doubles as the heat-map drag source on the
    # Heat Map tab -- flip each WellButton into drag-source mode while
    # that tab is active. Focused tab only, unlike the frame above: drag mode
    # changes what a plate *click* does, and the plate is shared, so following
    # an unfocused Heat Map would rewire the clicks of the pane the user is
    # actually working in.
    sync_heatmap_well_drag_mode(ctx, tab == "Heat Map")

    # Per-tab activation is dispatched through the feature registry: each
    # descriptor's ``on_activate`` hook owns its sidebar-visibility + refresh
    # (the bodies relocated from this ladder, see ``features/registry.py``). A
    # tab with no descriptor / no hook falls back to the generic plot path
    # (unified picker + redraw) — the same behaviour the old ``else`` gave Line
    # Graphs and any unknown leaf.
    # The other visible pane first, so anything the two hooks both write —
    # the plate's mode, the rep-colour map — settles on the focused pane's
    # terms. Only the hook runs for it: the descriptor-less fallback below
    # ends in a full ``redraw_app``, which belongs to the tab being switched
    # *to*, and the unfocused pane's own redraw is already handled by the
    # scope coalescer (``fold_change_scopes.redraw_scopes_or_defer``, which is
    # Split-View-aware). A pane with no descriptor shares the main frame, so
    # showing that is all it needs here.
    for _other in _also_visible:
        _other_desc = ctx.features.get(_other)
        if _other_desc is not None and _other_desc.on_activate is not None:
            try:
                _other_desc.on_activate(ctx)
            except Exception:
                swallowed(f"tab_controller: on_activate failed for {_other!r}")
        else:
            ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    # Which frames the unfocused panes turned on. Everything was hidden a few
    # lines above, so "visible now" is exactly "shown by one of those hooks".
    _other_frames = [f for f in _frames if not f.isHidden()]

    desc = ctx.features.get(tab)
    if desc is not None and desc.on_activate is not None:
        desc.on_activate(ctx)
    else:
        ctx.widgets.get("_sidebar_main_frame").setVisible(True)
        ctx.coordinator.refresh_sidebar_map()
        redraw_app(ctx)

    # **One well picker on screen, and it belongs to the focused pane.** Four
    # sidebar sections offer a ``WellPlateSelector`` — the shared one for
    # `_sidebar_main_frame`, plus Statistics, Segmentation and Image Table — and
    # they were mutually exclusive for as long as only one section showed at a
    # time. Showing every visible pane's section (above) put two of them on the
    # rail at once, stacked and identical-looking while meaning different things:
    # the Statistics plate paints wells into comparison groups, the shared one
    # selects wells to plot. Two pickers with different consequences and no way
    # to tell them apart is worse than the click this change removed.
    #
    # So the sections all stay and the picker follows the focus — the rule the
    # heat-map drag mode already uses a few lines above: frames follow
    # visibility, interaction follows focus, and a plate is interaction. All
    # four plates live in one slot above the sections (``core/rail_plate.py``),
    # so choosing whose picker shows is a visibility decision and not a change
    # of anyone's height: the rail does not move when the focus does.
    #
    # Every plate is set, not only the ones whose section is on screen. A plate
    # in the shared slot is not hidden by its section being hidden — that is
    # what having one slot means — so a pass that only spoke for the visible
    # sections would leave a hidden section's picker on the rail.
    _focused_frames = [f for f in _frames if not f.isHidden()]
    # The frames the focused hook turned on are the ones it did not inherit.
    # When it inherits everything — two plot tabs sharing ``_sidebar_main_frame``
    # — there is one frame and therefore one plate, and the main frame is the
    # right thing to keep.
    _newly = [f for f in _focused_frames if f not in _other_frames]
    _keep = _newly or [ctx.widgets.get_or_none("_sidebar_main_frame")]
    for _frame in _frames:
        _show = _frame in _keep and _frame in _focused_frames
        for _plate in _section_plates(ctx, _frame):
            _plate.setVisible(_show)
    _rail_plate.settle_slot(ctx)

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
            # reflect the restored state. The Image Table is deliberately
            # excluded — it reads images off disk, and it renders only when the
            # user presses Generate. That holds on every restore path, this one
            # and Saved Plots' Restore alike; the table is marked stale instead,
            # so the shelf header asks for the Generate rather than taking it.
            if tab == "Bar Plots":
                ctx.features.redraw(ctx, "Bar Plots")
            elif tab == "Scatter Plot":
                ctx.features.redraw(ctx, "Scatter Plot")
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


def on_notebook_current_changed(ctx, _idx) -> None:
    app = ctx.window  # transitional bridge (A3)
    nb = app._notebook
    nav = getattr(app, "_section_nav", None)
    if nav is None or nb is None:
        return
    title = nb.currentName()
    from well_viewer.core.app_shell_view import show_page_tabs
    show_page_tabs(app, title)
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
    prev_tab: str | None,
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


def visible_centre_tabs(ctx) -> set[str]:
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
    from well_viewer.core.dnd import WELL_MIME
    if enable:
        plate.setDragMime(WELL_MIME)
        plate.setAcceptDropMime(WELL_MIME)
    else:
        plate.setDragMime(None)
        plate.setAcceptDropMime(None)



def sync_section_nav_from_notebook(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
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



def tab_history_back(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
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



def tab_history_forward(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
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



def apply_sidebar_visibility(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Apply the effective left-rail visibility for the active mode.

    The collapse toggle (``Ctrl+B`` / the button floating on the rail's
    edge) drives whichever left sidebar belongs to the current mode,
    symmetrically:

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
    # The collapse toggle floats over the visible panel's right edge, so it has
    # to move when the panel changes width, goes away, or is replaced by another
    # mode's. The page tab strip is Review's, and rides the same switch.
    from well_viewer.core.app_shell_view import position_rail_toggle, show_page_tabs
    position_rail_toggle(app)
    # …and again once the incoming pane's splitter has laid out. A mode switch
    # runs this while the pane being shown still carries the geometry it had
    # before it was laid out into the stack, so the first call parks the toggle
    # against a width that is about to change. Queued on the GUI thread, so the
    # second call sees the settled layout.
    from PySide6.QtCore import QTimer

    def _repark_when_laid_out() -> None:
        try:
            position_rail_toggle(app)
        except RuntimeError:
            # The window can be torn down between the switch and this callback
            # (a test's app going out of scope), leaving a deleted C++ widget.
            swallowed()  # window gone before the queued reposition ran

    QTimer.singleShot(0, _repark_when_laid_out)
    notebook = getattr(app, "_notebook", None)
    if notebook is not None:
        show_page_tabs(app, notebook.currentName())
    # The Analyze and Datasets panes each host their own left sidebar (only ever
    # visible when their pane is the current stack page); the same toggle hides
    # whichever is showing.
    for attr in ("_analyze_pane", "_datasets_pane"):
        pane = getattr(app, attr, None)
        pane_sb = getattr(pane, "_left_sidebar", None)
        if pane_sb is not None:
            pane_sb.setVisible(not collapsed)


def toggle_sidebar(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """User-driven collapse/expand of the left rail.

    Flips the persistent collapse flag, re-applies the effective visibility,
    and syncs the floating toggle button's checked state / glyph.
    """
    app._sidebar_collapsed = not getattr(app, "_sidebar_collapsed", False)
    apply_sidebar_visibility(ctx)
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


def on_central_pane_changed(ctx, idx: int) -> None:
    app = ctx.window  # transitional bridge (A3)
    # Mode-seg lives in allwell's titlebar; emit modeChanged so the
    # shell can re-sync its own seg without poking widgets here. (Split View
    # listens to modeChanged to tear itself down — see split_view.install.)
    mode = pane_mode(idx)
    # Hide the section / well-picker sidebar when Analyze is active —
    # it carries Review-only affordances. Show it again on Review (unless
    # the user has collapsed it). Both rules live in apply_sidebar_visibility.
    apply_sidebar_visibility(ctx)
    app.modeChanged.emit(mode)



def build_centre_shim(ctx, parent) -> None:
    app = ctx.window  # transitional bridge (A3)
    from well_viewer.core.centre_view import build_centre as _build_centre_view
    _build_centre_view(app, parent)
    # Phase 10 (A1): mirror the notebook's tab list into the rail nav.
    # The nav drives the (hidden-tab-bar) QTabWidget; the QTabWidget's
    # currentChanged signal keeps the nav in sync if anything else
    # changes the index (deferred builders, programmatic select_by_text).
    app._sync_section_nav_from_notebook()
    app._section_nav.currentChanged.connect(app._on_section_nav_changed)
    app._notebook.currentChanged.connect(partial(on_notebook_current_changed, ctx))

