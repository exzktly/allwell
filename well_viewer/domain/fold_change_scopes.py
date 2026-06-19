"""Scope registry + dirty-flag plumbing for fold-change tabs.

Pure logic (no Qt) so the helpers can be unit-tested without spinning
up a PySide6 environment. Each fold-change-aware tab registers a
``FoldChangeScope`` describing how to find its combo widgets and which
callable to invoke when its plot needs to redraw. The
``redraw_scopes_or_defer`` / ``flush_dirty_scopes`` pair implements
the 'only redraw the visible tab; redraw the rest when the user looks
at them' optimization without coupling either tab to the other.

Registrations live in ``core/scope_registry.register_default_scopes``,
which the app calls once during ``__init__``. Domain stays at the
bottom of the import graph — it does not reach up into ``features/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class FoldChangeScope:
    """Per-tab description used by the cross-tab sync + redraw glue.

    ``redraw`` is invoked with the running ``WellViewerApp`` whenever
    the scope's tab is visible and a redraw is requested.

    ``tab_names`` is the set of leaf tab labels (as returned by
    ``WellViewerApp._current_centre_tab``) that the scope considers
    'visible'. A scope with no matching tab name is treated as
    always-invisible — redraws targeting it are deferred until a
    matching tab becomes active.
    """
    name: str
    ctrl_combo_attr: str
    baseline_combo_attr: str
    redraw: Callable[[Any], None]
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
    """Invoke the scope's redraw callable.

    Logs and surfaces failures via the status bar so a broken redraw doesn't
    look identical to "no change was made" — the previous ``print_exc``-only
    handling left the user with a frozen-looking plot and a traceback in
    stderr they'd never see when running the .app bundle. We still don't
    let one scope's failure crash the app, but we make sure it's visible.
    """
    import logging
    from well_viewer.core.safe import swallowed
    try:
        scope.redraw(app)
    except Exception as exc:
        logging.getLogger("well_viewer").exception(
            "Fold-change redraw failed for scope %r", scope.name,
        )
        setter = getattr(app, "_set_status", None)
        if callable(setter):
            try:
                setter(f"Plot redraw failed ({scope.name}): {exc}")
            except Exception:
                swallowed("fold_change_scopes: status-bar update failed")


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


def redraw_scopes_after_load(app, *, default_redraw=None) -> None:
    """Post-load scope refresh that composes with a prior ``_redraw``.

    A freshly loaded dataset invalidates every plot. ``RedrawCoordinator`` runs
    ``app._redraw`` first (``redraw_app`` — the line / distribution / heat-map
    plots), then calls this to cover the Bar / Scatter scopes that ``_redraw``
    does not touch:

    * The scope whose ``redraw`` IS ``default_redraw`` was already drawn by
      ``_redraw`` — leave it clean (don't redraw it again, avoiding a fan-out
      when the line tab is the visible one).
    * The visible non-default scope is drawn now so its tab isn't blank.
    * Every other scope is marked dirty, so it flushes the moment its tab is
      next viewed — the same defer-until-viewed contract as any other action.
    """
    visible = current_scope_name(app)
    dirty = _dirty_set(app)
    for scope in registered_scopes():
        if default_redraw is not None and scope.redraw is default_redraw:
            dirty.discard(scope.name)
        elif scope.name == visible:
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
