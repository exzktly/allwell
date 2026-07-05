"""Split View — two different centre tabs side-by-side, both live.

**Self-contained feature module.** Everything that *does* something for Split
View lives here; the rest of the app only carries inert extension points that
default to ordinary single-tab behaviour. The whole feature is wired by a single
call:

    from well_viewer.core.split_view import install_split_view
    install_split_view(app)          # ← comment this out to remove the feature

With that call commented out, no toggle button is ever created, so the user can
never enter split, ``app._split_active`` is never set, and the provider hooks on
``tab_controller`` stay ``None`` — ``current_centre_tab`` / ``visible_centre_tabs``
/ ``is_tab_visible`` then resolve to the single current tab exactly as before, the
live-refresh chokepoints fan out to one tab, and none of the reparenting / focus
/ entry-UI code below is ever reached.

Mechanics: the centre notebook is hidden and its two chosen top-level pages are
reparented into ``app._split_pane`` (a horizontal ``QSplitter``). The focused
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

_logger = logging.getLogger("well_viewer.split_view")

#: Master switch. Setting this False (or commenting out the ``install_split_view``
#: call site) disables the feature with zero side effects.
ENABLED = True


# ── Reparenting (enter / exit / focus) ───────────────────────────────────────

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
    app._ctx.runtime.split.active = True
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
        app._ctx.runtime.split.active = False
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
    on_tab_change(app._ctx, None)


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
    app._ctx.runtime.split.active = False
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
    on_tab_change(app._ctx, None)


def focus_split(app, top: str) -> None:
    """Make pane *top* the focused one (drives the sidebar). No-op off split."""
    if not getattr(app, "_split_active", False) or not top:
        return
    if getattr(app, "_split_focused", None) == top:
        return
    app._split_focused = top
    on_tab_change(app._ctx, None)


# ── Focused-pane click watcher ───────────────────────────────────────────────

def _resolve_focused_pane(app, obj):
    """Map a clicked widget to the top-level name of the pane that contains it."""
    widgets = getattr(app, "_split_pane_widgets", {}) or {}
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

    def on_focus_changed(self, _old, new) -> None:  # noqa: N802 (Qt slot)
        try:
            active = bool(getattr(self._app, "_split_active", False))
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
            top = _resolve_focused_pane(self._app, new)
        except RuntimeError:
            top = None
        if top is not None:
            # Defer the focus switch: ``focus_split`` → ``on_tab_change``
            # reparents the panes + re-runs the visible-tab refresh, which must
            # not run synchronously inside focus delivery. Idempotent — a no-op
            # when the focused pane is unchanged.
            QTimer.singleShot(0, lambda t=top: focus_split(self._app, t))


def _install_split_focus_filter(app) -> None:
    watcher = getattr(app, "_split_focus_filter", None)
    if watcher is None:
        watcher = _SplitFocusWatcher(app)
        app._split_focus_filter = watcher
    qapp = QApplication.instance()
    if qapp is not None and not watcher._connected:
        # Idempotent via the tracked flag: connect once. We deliberately do NOT
        # disconnect-first — calling disconnect on a not-yet-connected signal is
        # exactly what triggered the spurious "Failed to disconnect" warning.
        qapp.focusChanged.connect(watcher.on_focus_changed)
        watcher._connected = True


def _remove_split_focus_filter(app) -> None:
    watcher = getattr(app, "_split_focus_filter", None)
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


# ── Provider hooks registered on tab_controller (None ⇒ feature absent) ───────

def _focused_leaf(ctx):
    """Provider for ``current_centre_tab``: the focused pane's leaf, or None.

    A8: takes ``ctx`` — split flags via ``ctx.runtime.split``, leaf resolution
    via the now-ctx ``_resolve_leaf``."""
    if not ctx.runtime.split.active:
        return None
    return tab_controller._resolve_leaf(ctx, ctx.runtime.split.focused) or None


def _visible_leaves(ctx):
    """Provider for ``visible_centre_tabs``: both panes' leaves, or None.

    A8: takes ``ctx`` (split flags via ``ctx.runtime.split``)."""
    if not ctx.runtime.split.active:
        return None
    out = {
        leaf for top in (ctx.runtime.split.tab_a, ctx.runtime.split.tab_b)
        if (leaf := tab_controller._resolve_leaf(ctx, top))
    }
    return out or None


def _nav_intercept(app, _key) -> bool:
    """Rail navigation while split: exit split first, then let the normal
    ``setCurrentByName`` proceed (returns False = not consumed)."""
    if getattr(app, "_split_active", False):
        exit_split(app)
        sync_split_toggle_button(app, False)
    return False


def _exit_on_mode(app) -> None:
    """Leave Split View when the Review/Analyze mode switches."""
    if getattr(app, "_split_active", False):
        exit_split(app)
        sync_split_toggle_button(app, False)


# ── The single wiring entry point ────────────────────────────────────────────

def install_split_view(app) -> None:
    """Wire up Split View. This is the *only* line to comment out to remove it.

    Builds ``app._split_pane``, the top-bar toggle + right-pane dropdown, and
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
    split.hide()
    parent = nb.parentWidget()
    layout = parent.layout() if parent is not None else None
    if parent is not None:
        split.setParent(parent)
    if layout is not None:
        layout.addWidget(split, 1)
    app._split_pane = split
    app._ctx.runtime.split.active = False
    app._split_tab_a = app._split_tab_b = app._split_focused = None
    app._split_pane_widgets = {}

    # 2. Entry UI — toggle button + right-pane dropdown in the top bar.
    top = getattr(app, "_top_bar", None)
    top_layout = top.layout() if top is not None else None
    if top_layout is not None:
        from widgets.icon_button import IconButton
        btn = IconButton("columns-2", tooltip="Split view", checkable=True)
        btn.clicked.connect(lambda: on_split_toggle_clicked(app))
        combo = QComboBox()
        combo.setToolTip("Right pane")
        combo.setVisible(False)
        combo.currentIndexChanged.connect(lambda i: on_split_pane_combo_changed(app, i))
        app._split_toggle_btn = btn
        app._split_pane_combo = combo
        # Sit at the right end of the top bar (after the stretch + Open).
        top_layout.addWidget(btn)
        top_layout.addWidget(combo)

    # 3. Make the centre resolvers split-aware.
    tab_controller.split_focus_provider = _focused_leaf
    tab_controller.split_visible_provider = _visible_leaves
    tab_controller.split_nav_intercept = _nav_intercept

    # 4. Exit split on a Review/Analyze mode switch.
    sig = getattr(app, "modeChanged", None)
    if sig is not None:
        try:
            sig.connect(lambda _m: _exit_on_mode(app))
        except Exception:
            _logger.exception("Split: modeChanged connect failed")
