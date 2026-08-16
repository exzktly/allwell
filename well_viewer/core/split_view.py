"""Split View — two different centre tabs side-by-side, both live.

**Self-contained feature module.** Everything that *does* something for Split
View lives here; the rest of the app only carries inert extension points that
default to ordinary single-tab behaviour. The whole feature is wired by a single
call:

    from well_viewer.core.split_view import install_split_view
    install_split_view(ctx)          # ← comment this out to remove the feature

With that call commented out, no toggle button is ever created, so the user can
never enter split, ``ctx.runtime.split.active`` is never set, and the provider hooks on
``tab_controller`` stay ``None`` — ``current_centre_tab`` / ``visible_centre_tabs``
/ ``is_tab_visible`` then resolve to the single current tab exactly as before, the
live-refresh chokepoints fan out to one tab, and none of the reparenting / focus
/ entry-UI code below is ever reached.

Mechanics: the centre notebook is hidden and its two chosen top-level pages are
reparented into ``ctx.runtime.split.pane`` (a horizontal ``QSplitter``). The focused
pane drives the sidebar (via the ``split_focus_provider`` hook); both panes are
reported visible (via ``split_visible_provider``) so the chokepoints refresh each.
Two instances of the *same* tab are out of scope — feature figures/state are
singletons.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QSplitter, QWidget,
)
from well_viewer.core.safe import swallowed

from well_viewer.core import tab_controller
from well_viewer.core.tab_controller import on_tab_change

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).

_logger = logging.getLogger("well_viewer.split_view")

#: Master switch. Setting this False (or commenting out the ``install_split_view``
#: call site) disables the feature with zero side effects.
ENABLED = True

#: While split, each pane's minimum width is capped to this so the divider is
#: freely draggable. A reparented centre tab (e.g. the Plotting plot area) carries
#: a large content-driven ``minimumSizeHint`` (~585px); with two such panes the
#: minimums consume the whole centre and QSplitter has no slack to move the
#: handle — the divider "sticks". An explicit ``setMinimumWidth`` overrides that
#: content minimum for the splitter, so a pane can be dragged small (its content
#: just narrows/clips). Restored to 0 on exit so single-tab view is unaffected.
_SPLIT_PANE_MIN_W = 160


# ── Reparenting (enter / exit / focus) ───────────────────────────────────────

def enter_split(ctx, tab_a: str, tab_b: str) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Show *tab_a* and *tab_b* side-by-side, both live; *tab_a* is focused.

    Both bodies are force-built if still deferred, detached from the hidden
    notebook into ``ctx.runtime.split.pane``, and the focused pane drives the sidebar.
    No-op if the names match or the centre isn't built.
    """
    if not tab_a or not tab_b or tab_a == tab_b:
        return
    nb = getattr(app, "_notebook", None)
    split = ctx.runtime.split.pane
    if nb is None or split is None:
        return
    if ctx.runtime.split.active:
        exit_split(ctx)
    # Force-build deferred bodies before detaching so panes are never blank.
    build = ctx.runtime.centre.build_pending
    if callable(build):
        for t in (tab_a, tab_b):
            try:
                build(t)
            except Exception:
                _logger.exception("Split: deferred build for %r failed", t)
    # Set state first so any notebook signal fired mid-detach resolves to the
    # focused pane rather than a stale leaf.
    ctx.runtime.split.active = True
    ctx.runtime.split.tab_a = tab_a
    ctx.runtime.split.tab_b = tab_b
    ctx.runtime.split.focused = tab_a
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
        ctx.runtime.split.active = False
        ctx.runtime.split.tab_a = ctx.runtime.split.tab_b = ctx.runtime.split.focused = None
        return
    ctx.runtime.split.pane_widgets = {tab_a: wa, tab_b: wb}
    # Both panes are detached from the notebook now, so the band's usual source
    # (``currentName()``) names neither — point it at the focused pane.
    from well_viewer.core.app_shell_view import show_page_tabs
    show_page_tabs(ctx.window, tab_a)
    split.addWidget(wa)
    split.addWidget(wb)
    # Cap each pane's minimum width so the divider has slack to move — otherwise
    # the panes' large content minimums fill the centre and the handle sticks.
    wa.setMinimumWidth(_SPLIT_PANE_MIN_W)
    wb.setMinimumWidth(_SPLIT_PANE_MIN_W)
    wa.show()
    wb.show()
    split.setVisible(True)
    # Equal LARGE targets force a ~50/50 split regardless of each pane's
    # minimumSizeHint. The old ``setSizes([1, 1])`` — set while the splitter was
    # still hidden — let QSplitter fall back to the (unequal) per-pane minimums,
    # so one side came up consistently wider. Set after ``setVisible`` so the
    # proportions apply to the real width.
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 1)
    split.setSizes([1_000_000, 1_000_000])
    nb.setVisible(False)
    _install_split_focus_filter(ctx)
    on_tab_change(ctx, None)


def exit_split(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Tear down Split View: reattach both panes (original order) and show the
    notebook again, focused on the former pane A."""
    if not ctx.runtime.split.active:
        return
    nb = getattr(app, "_notebook", None)
    split = ctx.runtime.split.pane
    a = ctx.runtime.split.tab_a
    b = ctx.runtime.split.tab_b
    widgets = ctx.runtime.split.pane_widgets or {}
    wa = widgets.get(a)
    wb = widgets.get(b)
    for w in (wa, wb):
        if w is not None:
            # Undo the split-time min-width cap so the reattached single-tab page
            # sizes from its own content again.
            w.setMinimumWidth(0)
            w.setParent(None)
    ctx.runtime.split.active = False
    ctx.runtime.split.tab_a = ctx.runtime.split.tab_b = ctx.runtime.split.focused = None
    ctx.runtime.split.pane_widgets = {}
    _remove_split_focus_filter(ctx)
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
    # The reattach runs with the notebook's signals blocked, so the band's usual
    # source never fires — resync it by hand to the page we land on.
    from well_viewer.core.app_shell_view import show_page_tabs
    show_page_tabs(app, a or "")
    on_tab_change(ctx, None)


def focus_split(ctx, top: str) -> None:
    """Make pane *top* the focused one (drives the sidebar). No-op off split."""
    if not ctx.runtime.split.active or not top:
        return
    if ctx.runtime.split.focused == top:
        return
    ctx.runtime.split.focused = top
    # The shell band carries one tab strip; point it at the pane that now has
    # focus, so the split pane you are working in is the one whose sub-tabs the
    # band offers.
    from well_viewer.core.app_shell_view import show_page_tabs
    show_page_tabs(ctx.window, top)
    on_tab_change(ctx, None)


# ── Focused-pane click watcher ───────────────────────────────────────────────

def _resolve_focused_pane(ctx, obj) -> str | None:
    """Map a clicked widget to the top-level name of the pane that contains it."""
    widgets = ctx.runtime.split.pane_widgets or {}
    if not widgets:
        return None
    node = obj
    while isinstance(node, QWidget):
        for top, w in widgets.items():
            if node is w:
                return top
        try:
            node = node.parentWidget()
        except RuntimeError:
            # ``node``'s C++ object was deleted mid-walk (a pane is being
            # rebuilt / reparented). Stop rather than dereference it.
            return None
    return None


class _SplitFocusWatcher(QObject):
    """Tracks the focused split pane via ``QApplication.focusChanged`` while
    Split View is on, so clicking into a pane makes it the focused one.

    This deliberately does **not** use a raw app-global ``installEventFilter``:
    an event filter intercepts *every* ``QEvent``, and during the split-view
    redraw / reparent churn Qt handed it a freed ``QEvent`` whose ``event.type()``
    access-violated (a hard crash). ``focusChanged`` delivers only widget
    pointers — never a raw ``QEvent`` — so it can't dangle the same way.
    """

    def __init__(self, app) -> None:
        # No QObject parent: lifetime is held by ``app._split_focus_filter``.
        super().__init__()
        self._app = app
        # Whether ``on_focus_changed`` is currently connected to
        # ``QApplication.focusChanged``. Tracked so we never call ``disconnect``
        # on a connection that isn't there — PySide emits a noisy
        # ``RuntimeWarning: Failed to disconnect`` in that case (the try/except
        # only catches the exception, not the warning).
        self._connected = False

    def on_focus_changed(self, _old, new) -> None:  # Qt slot
        try:
            active = bool(self._app._ctx.runtime.split.active)
        except RuntimeError:
            return  # underlying C++ app already deleted — nothing to do
        if not active or new is None:
            return
        # Ignore focus landing on an action control (e.g. the Image Table
        # "Generate" button): clicking a button inside a pane should not swap
        # the shared sidebar away from the other pane. Only focus on pane
        # *content* (plot canvas / table) switches the focused pane.
        if isinstance(new, QAbstractButton):
            return
        try:
            top = _resolve_focused_pane(self._app._ctx, new)
        except RuntimeError:
            top = None
        if top is not None:
            # Defer the focus switch: ``focus_split`` → ``on_tab_change``
            # reparents the panes + re-runs the visible-tab refresh, which must
            # not run synchronously inside focus delivery. Idempotent — a no-op
            # when the focused pane is unchanged.
            QTimer.singleShot(0, lambda t=top: focus_split(self._app._ctx, t))


def _install_split_focus_filter(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    watcher = ctx.runtime.split.focus_filter
    if watcher is None:
        watcher = _SplitFocusWatcher(app)
        ctx.runtime.split.focus_filter = watcher
    qapp = QApplication.instance()
    if qapp is not None and not watcher._connected:
        # Idempotent via the tracked flag: connect once. We deliberately do NOT
        # disconnect-first — calling disconnect on a not-yet-connected signal is
        # exactly what triggered the spurious "Failed to disconnect" warning.
        qapp.focusChanged.connect(watcher.on_focus_changed)
        watcher._connected = True


def _remove_split_focus_filter(ctx) -> None:
    watcher = ctx.runtime.split.focus_filter
    if watcher is None:
        return
    qapp = QApplication.instance()
    if qapp is not None and watcher._connected:
        try:
            qapp.focusChanged.disconnect(watcher.on_focus_changed)
        except (RuntimeError, TypeError):
            swallowed()  # Qt already tore the connection down — fine
        watcher._connected = False


# ── Entry-UI handlers ────────────────────────────────────────────────────────

def sync_split_toggle_button(ctx, checked: bool) -> None:
    """Reflect *checked* on the top-bar split toggle without re-firing it."""
    btn = ctx.runtime.split.toggle_btn
    if btn is None:
        return
    blocked = btn.blockSignals(True)
    try:
        btn.setChecked(checked)
    finally:
        btn.blockSignals(blocked)
    combo = ctx.runtime.split.pane_combo
    if combo is not None:
        combo.setVisible(checked)


def on_split_toggle_clicked(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Top-bar split toggle: pane A = current tab, pane B = dropdown pick."""
    btn = ctx.runtime.split.toggle_btn
    combo = ctx.runtime.split.pane_combo
    if btn.isChecked():
        cur = app._notebook.currentName()
        others = [n for n in app._notebook.pageNames() if n and n != cur]
        if not others:
            sync_split_toggle_button(ctx, False)
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
        enter_split(ctx, cur, others[0])
    else:
        combo.setVisible(False)
        exit_split(ctx)


def on_split_pane_combo_changed(ctx, _idx: int) -> None:
    """Re-pair pane A with a newly chosen pane B from the dropdown."""
    if not ctx.runtime.split.active:
        return
    new_b = ctx.runtime.split.pane_combo.currentText()
    a = ctx.runtime.split.tab_a
    if not new_b or new_b == a:
        return
    exit_split(ctx)
    enter_split(ctx, a, new_b)


# ── Provider hooks registered on tab_controller (None ⇒ feature absent) ───────

def _focused_leaf(ctx) -> str | None:
    """Provider for ``current_centre_tab``: the focused pane's leaf, or None.

    A8: takes ``ctx`` — split flags via ``ctx.runtime.split``, leaf resolution
    via the now-ctx ``_resolve_leaf``."""
    if not ctx.runtime.split.active:
        return None
    return tab_controller._resolve_leaf(ctx, ctx.runtime.split.focused) or None


def _visible_leaves(ctx) -> set | None:
    """Provider for ``visible_centre_tabs``: both panes' leaves, or None.

    A8: takes ``ctx`` (split flags via ``ctx.runtime.split``)."""
    if not ctx.runtime.split.active:
        return None
    out = {
        leaf for top in (ctx.runtime.split.tab_a, ctx.runtime.split.tab_b)
        if (leaf := tab_controller._resolve_leaf(ctx, top))
    }
    return out or None


def _nav_intercept(ctx, _key) -> bool:
    """Rail navigation while split: exit split first, then let the normal
    ``setCurrentByName`` proceed (returns False = not consumed)."""
    if ctx.runtime.split.active:
        exit_split(ctx)
        sync_split_toggle_button(ctx, False)
    return False


def _exit_on_mode(ctx, mode: str = "review") -> None:
    """Leave Split View when the Review/Analyze/Datasets mode switches, and
    hide the toggle entirely outside Review — it's a Review-only feature
    (both panes it splits are Review tabs), but the top bar it lives in is
    shared across all three modes, so without this it kept sitting there in
    Analyze and Datasets with nothing for it to act on."""
    if ctx.runtime.split.active:
        exit_split(ctx)
        sync_split_toggle_button(ctx, False)
    _set_toggle_visible_for_mode(ctx, mode)


def _set_toggle_visible_for_mode(ctx, mode: str) -> None:
    is_review = mode == "review"
    btn = getattr(ctx.runtime.split, "toggle_btn", None)
    if btn is not None:
        btn.setVisible(is_review)
    combo = getattr(ctx.runtime.split, "pane_combo", None)
    if combo is not None and not is_review:
        combo.setVisible(False)


# ── The single wiring entry point ────────────────────────────────────────────

def install_split_view(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    """Wire up Split View. This is the *only* line to comment out to remove it.

    Builds ``ctx.runtime.split.pane``, the top-bar toggle + right-pane dropdown, and
    registers the provider hooks on ``tab_controller`` so the resolvers become
    split-aware. Tolerant of a missing top bar / centre layout / ``modeChanged``
    signal so it can also be driven in tests against a minimal stub app.
    """
    if not ENABLED:
        return
    nb = getattr(app, "_notebook", None)
    if nb is None:
        return

    # 1. Split pane — a hidden sibling of the notebook in the centre layout.
    split = QSplitter(Qt.Horizontal)
    split.setObjectName("CentreSplit")
    split.setChildrenCollapsible(False)
    split.setHandleWidth(6)  # a real grab target (paired with the QSS width)
    split.hide()
    parent = nb.parentWidget()
    layout = parent.layout() if parent is not None else None
    if parent is not None:
        split.setParent(parent)
    if layout is not None:
        layout.addWidget(split, 1)
    ctx.runtime.split.pane = split
    ctx.runtime.split.active = False
    ctx.runtime.split.tab_a = ctx.runtime.split.tab_b = ctx.runtime.split.focused = None
    ctx.runtime.split.pane_widgets = {}

    # 2. Entry UI — toggle button + right-pane dropdown in the top bar.
    top = getattr(app, "_top_bar", None)
    top_layout = top.layout() if top is not None else None
    if top_layout is not None:
        from widgets.icon_button import IconButton
        btn = IconButton("columns-2", tooltip="Split view", checkable=True)
        btn.clicked.connect(lambda: on_split_toggle_clicked(ctx))
        combo = QComboBox()
        combo.setToolTip("Right pane")
        combo.setVisible(False)
        combo.currentIndexChanged.connect(lambda i: on_split_pane_combo_changed(ctx, i))
        ctx.runtime.split.toggle_btn = btn
        ctx.runtime.split.pane_combo = combo
        # Sit at the right end of the top bar (after the stretch + Open).
        top_layout.addWidget(btn)
        top_layout.addWidget(combo)
        # Review-only, from the moment the app opens — install runs once,
        # before any mode switch has happened to trigger the modeChanged
        # handler below.
        current_mode = app.currentMode() if hasattr(app, "currentMode") else "review"
        _set_toggle_visible_for_mode(ctx, current_mode)

    # 3. Make the centre resolvers split-aware.
    tab_controller.split_focus_provider = _focused_leaf
    tab_controller.split_visible_provider = _visible_leaves
    tab_controller.split_nav_intercept = _nav_intercept

    # 4. Exit split (and hide the toggle) on a mode switch away from Review.
    sig = getattr(app, "modeChanged", None)
    if sig is not None:
        try:
            sig.connect(lambda mode: _exit_on_mode(ctx, mode))
        except Exception:
            _logger.exception("Split: modeChanged connect failed")
