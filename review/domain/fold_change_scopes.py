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
from typing import Any
from collections.abc import Callable


@dataclass(frozen=True)
class FoldChangeScope:
    """Per-tab description used by the cross-tab sync + redraw glue.

    ``redraw`` is invoked with the running ``ReviewApp`` whenever
    the scope's tab is visible and a redraw is requested.

    ``tab_names`` is the set of leaf tab labels (as returned by
    ``ReviewApp._current_centre_tab``) that the scope considers
    'visible'. A scope with no matching tab name is treated as
    always-invisible — redraws targeting it are deferred until a
    matching tab becomes active.
    """
    name: str
    ctrl_combo_attr: str
    baseline_combo_attr: str
    redraw: Callable[[Any], None]
    tab_names: tuple[str, ...] = ()


_REGISTERED_SCOPES: dict[str, FoldChangeScope] = {}


def register_fold_change_scope(scope: FoldChangeScope) -> None:
    """Add a tab to the cross-tab fold-change sync set.

    Idempotent — registering the same scope twice replaces the prior
    entry. Call before any combo handler in the new tab fires.
    """
    _REGISTERED_SCOPES[scope.name] = scope


def registered_scopes() -> tuple[FoldChangeScope, ...]:
    return tuple(_REGISTERED_SCOPES.values())


def get_scope(name: str) -> FoldChangeScope | None:
    return _REGISTERED_SCOPES.get(name)


# ── Dirty-tab management ────────────────────────────────────────────────────


def _dirty_set(app) -> set[str]:
    """The set of scope names awaiting a deferred redraw — owned by PlotState."""
    return app._app_state.plot.fc_dirty_scopes


def plots_held(owner) -> bool:
    """True while every plot is held (``PlotState.held_for``; see ``core/plot_hold.py``).

    *owner* is the window (``app._app_state``) or a context (``ctx.state``);
    anything with neither — a test's stub — is never held.
    """
    state = getattr(owner, "_app_state", None) or getattr(owner, "state", None)
    return bool(getattr(getattr(state, "plot", None), "held_for", ""))


def defer_every_scope(app) -> None:
    """Mark every registered scope dirty without drawing any of them."""
    _dirty_set(app).update(scope.name for scope in registered_scopes())


def known_visible_scopes(app) -> set[str] | None:
    """The registered scopes on screen, or ``None`` when the tab on screen is unknown.

    :func:`visible_scope_names` answers an empty set both when no plot tab is in
    front (Home, Statistics, an image tab) and when the lookup itself failed,
    and a caller that must draw on "unknown" cannot defer on that answer. This
    one separates them: an empty set means the visible tab is known and draws
    no registered scope, so every plot can wait until its tab is shown.
    """
    tabs = _visible_tab_names(app)
    if not tabs:
        return None
    return {s.name for s in registered_scopes() if tabs.intersection(s.tab_names)}


def _visible_tab_names(app) -> frozenset[str]:
    """Leaf tab names currently on screen.

    Prefers ``app._visible_centre_tabs`` (Split View shows two leaves at
    once); falls back to the single ``app._current_centre_tab`` so non-split
    behaviour — and the pure unit tests that only stub the latter — are
    byte-identical.
    """
    multi = getattr(app, "_visible_centre_tabs", None)
    if callable(multi):
        try:
            tabs = multi()
            if tabs:
                return frozenset(tabs)
        except Exception:
            from core.safe import swallowed
            swallowed("fold_change_scopes: _visible_centre_tabs failed")
    single = getattr(app, "_current_centre_tab", None)
    if callable(single):
        try:
            tab = single()
            if tab:
                return frozenset((tab,))
        except Exception:
            # Same treatment as the ``_visible_centre_tabs`` arm above. An
            # empty set here is not neutral: ``visible_scope_names`` returns
            # nothing, so ``redraw_scopes_or_defer`` defers every scope and the
            # fold-change redraw never happens. Returning it silently made a
            # failed lookup indistinguishable from a window with no visible
            # tab, which is a legitimate state.
            from core.safe import swallowed
            swallowed("fold_change_scopes: _current_centre_tab failed")
    return frozenset()


def visible_scope_names(app) -> set[str]:
    """Names of every registered scope whose tab is currently visible.

    Normally a 0- or 1-element set; in Split View it can hold both a plot
    scope and another pane's scope when each pane shows a fold-change tab.
    """
    tabs = _visible_tab_names(app)
    if not tabs:
        return set()
    return {s.name for s in registered_scopes() if tabs.intersection(s.tab_names)}


def current_scope_name(app) -> str | None:
    """Name of a registered scope whose tab is currently visible (or ``None``).

    Retained for callers/tests that want a single scope; Split-View-aware
    fan-out uses :func:`visible_scope_names` instead.
    """
    names = visible_scope_names(app)
    if not names:
        return None
    # Deterministic pick: registry order, so single-tab behaviour is stable.
    for scope in registered_scopes():
        if scope.name in names:
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
    from core.safe import swallowed
    try:
        scope.redraw(app)
    except Exception as exc:
        logging.getLogger("review").exception(
            "Fold-change redraw failed for scope %r", scope.name,
        )
        setter = getattr(app, "_set_status", None)
        if callable(setter):
            try:
                setter(f"Plot redraw failed ({scope.name}): {exc}")
            except Exception:
                swallowed("fold_change_scopes: status-bar update failed")


def redraw_scopes_or_defer(app, *, skip=None) -> None:
    """Redraw the currently-visible scope, mark every other scope dirty.

    The single chokepoint used by every fold-change state mutation
    AND ``ReviewApp._set_active_channel``. The companion
    ``flush_dirty_scopes`` is wired into the app's tab-change handler
    so a deferred redraw runs as soon as the user looks at the tab.

    *skip* is a redraw callable the caller has **already run**. Scopes using it
    are left alone and left clean, rather than drawn a second time: several
    scopes share one callable (line, distribution and heat map all redraw
    through ``scope_registry.redraw_app_scope``), and a caller that ran it
    itself would otherwise repaint a 96-series figure twice per click.
    Compared by identity on the callable, which is why that one is a module
    function rather than a closure made per registration.
    """
    if plots_held(app):
        # Held (``core/plot_hold.py``): nothing draws, everything is owed.
        defer_every_scope(app)
        return
    visible = visible_scope_names(app)
    dirty = _dirty_set(app)
    for scope in registered_scopes():
        if skip is not None and scope.redraw is skip:
            dirty.discard(scope.name)
        elif scope.name in visible:
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
    if plots_held(app):
        # Held (``core/plot_hold.py``): nothing draws, everything is owed.
        defer_every_scope(app)
        return
    visible = visible_scope_names(app)
    dirty = _dirty_set(app)
    # Identity on the *scope*, not on its redraw callable. ``line``,
    # ``distribution`` and ``heatmap`` are all registered with the same
    # ``redraw_app_scope`` function object, so ``scope.redraw is
    # default_redraw`` matched all three — and cleared the dirty marks that
    # ``plot_orchestrator.redraw`` had just set for the two hidden ones, leaving
    # them neither drawn nor owed. The default scope is one scope; name it.
    default_name = next(
        (s.name for s in registered_scopes() if s.redraw is default_redraw),
        None) if default_redraw is not None else None
    for scope in registered_scopes():
        if default_name is not None and scope.name == default_name:
            # ``_redraw`` ran this scope's redraw already and left its mark as it
            # should be: clean when it drew it, dirty when its tab was known to
            # be off screen (``plot_orchestrator.redraw``).
            continue
        elif scope.name in visible:
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
    if plots_held(app):
        # Held (``core/plot_hold.py``): nothing draws, everything is owed.
        defer_every_scope(app)
        return
    visible = visible_scope_names(app)
    if not visible:
        return
    dirty = _dirty_set(app)
    for name in list(visible):
        if name not in dirty:
            continue
        descriptor = get_scope(name)
        if descriptor is None:
            continue
        dirty.discard(name)
        _call_redraw(app, descriptor)
