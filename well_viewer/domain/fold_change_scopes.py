"""Scope registry + dirty-flag plumbing for fold-change tabs.

Pure logic (no Qt) so the helpers can be unit-tested without spinning
up a PySide6 environment. Each fold-change-aware tab registers a
``FoldChangeScope`` describing how to find its combo widgets and which
``WellViewerApp`` method to call when its plot needs to redraw. The
``redraw_scopes_or_defer`` / ``flush_dirty_scopes`` pair implements
the 'only redraw the visible tab; redraw the rest when the user looks
at them' optimization without coupling either tab to the other.

The Qt-facing fold-change controls module (`tabs.fold_change_controls`)
re-exports these names so existing callers don't change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FoldChangeScope:
    """Per-tab description used by the cross-tab sync + redraw glue.

    ``tab_names`` is the set of leaf tab labels (as returned by
    ``WellViewerApp._current_centre_tab``) that the scope considers
    'visible'. A scope with no matching tab name is treated as
    always-invisible — redraws targeting it are deferred until a
    matching tab becomes active.
    """
    name: str
    ctrl_combo_attr: str
    baseline_combo_attr: str
    redraw_method: str  # method name on ``WellViewerApp``
    tab_names: "tuple[str, ...]" = ()


_REGISTERED_SCOPES: "dict[str, FoldChangeScope]" = {}


def register_fold_change_scope(scope: FoldChangeScope) -> None:
    """Add a tab to the cross-tab fold-change sync set.

    Idempotent — registering the same scope twice replaces the prior
    entry. Call before any combo handler in the new tab fires.
    """
    _REGISTERED_SCOPES[scope.name] = scope


def registered_scopes() -> "tuple[FoldChangeScope, ...]":
    return tuple(_REGISTERED_SCOPES.values())


def get_scope(name: str) -> "FoldChangeScope | None":
    return _REGISTERED_SCOPES.get(name)


# Default registrations — the two scopes that exist today.
register_fold_change_scope(FoldChangeScope(
    name="bar",
    ctrl_combo_attr="_fc_ctrl_combo_bar",
    baseline_combo_attr="_fc_baseline_combo_bar",
    redraw_method="_redraw_bars",
    tab_names=("Bar Plots",),
))
register_fold_change_scope(FoldChangeScope(
    name="line",
    ctrl_combo_attr="_fc_ctrl_combo_line",
    baseline_combo_attr="_fc_baseline_combo_line",
    redraw_method="_redraw",
    tab_names=("Line Graphs",),
))


# ── Dirty-tab management ────────────────────────────────────────────────────


def _dirty_set(app) -> "set[str]":
    """The set of scope names awaiting a deferred redraw — owned by PlotState."""
    return app._app_state.plot.fc_dirty_scopes


def current_scope_name(app) -> "str | None":
    """Name of the registered scope whose tab is currently visible.

    Returns ``None`` when no fold-change tab is in focus (e.g. the
    user is on Batch Export). In that case ``redraw_scopes_or_defer``
    marks every scope dirty.
    """
    tab_getter = getattr(app, "_current_centre_tab", None)
    if not callable(tab_getter):
        return None
    try:
        tab_name = tab_getter()
    except Exception:
        return None
    for scope in registered_scopes():
        if tab_name in scope.tab_names:
            return scope.name
    return None


def _call_redraw(app, scope: FoldChangeScope) -> None:
    """Invoke the scope's redraw method, swallowing exceptions to a trace."""
    import traceback
    method = getattr(app, scope.redraw_method, None)
    if method is None:
        return
    try:
        method()
    except Exception:
        traceback.print_exc()


def redraw_scopes_or_defer(app) -> None:
    """Redraw the currently-visible scope, mark every other scope dirty.

    The single chokepoint used by every fold-change state mutation
    AND ``WellViewerApp._set_active_channel``. The companion
    ``flush_dirty_scopes`` is wired into the app's tab-change handler
    so a deferred redraw runs as soon as the user looks at the tab.
    """
    visible = current_scope_name(app)
    dirty = _dirty_set(app)
    for scope in registered_scopes():
        if scope.name == visible:
            _call_redraw(app, scope)
            dirty.discard(scope.name)
        else:
            dirty.add(scope.name)


def flush_dirty_scopes(app) -> None:
    """Redraw any dirty scope whose tab is now visible. Idempotent.

    Designed to be called from the app's tab-change handler — it walks
    the registry, finds scopes whose ``tab_names`` include the
    currently-visible leaf, and runs their redraw method if they're
    marked dirty.
    """
    visible = current_scope_name(app)
    if visible is None:
        return
    dirty = _dirty_set(app)
    if visible not in dirty:
        return
    descriptor = get_scope(visible)
    if descriptor is None:
        return
    dirty.discard(visible)
    _call_redraw(app, descriptor)
