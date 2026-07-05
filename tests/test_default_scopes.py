"""Regression: Distribution and Heat Map are registered redraw scopes, and
every default scope threads the app handle to its ctx before redrawing.

Only line + bar used to be registered, so a channel change with the
Distribution (or Heat Map) tab visible left its data stale — the redraw
coalescer only redraws *registered* scopes. And once ``redraw_app`` became
ctx-native (D-core) the scopes still had to hand it a ctx: the coalescer
invokes ``scope.redraw(app)`` with the *app*, so a scope registered with the
raw ctx-native ``redraw_app`` silently failed (it read ``app.window`` — a Qt
bound method — and the failure was swallowed). These pin both facts.
"""
from __future__ import annotations

from types import SimpleNamespace


def test_distribution_and_heatmap_are_registered_scopes():
    from well_viewer.core.scope_registry import register_default_scopes
    from well_viewer.domain.fold_change_scopes import get_scope

    register_default_scopes()

    dist = get_scope("distribution")
    assert dist is not None
    assert "Distribution" in dist.tab_names

    hm = get_scope("heatmap")
    assert hm is not None
    assert "Heat Map" in hm.tab_names


def test_default_scope_redraw_threads_app_to_ctx(monkeypatch):
    """The coalescer calls ``scope.redraw(app)`` with the app; the default
    scopes must forward ``app._ctx`` to the ctx-native ``redraw_app`` (not the
    raw app, which has no ``.window`` and would be swallowed)."""
    import well_viewer.core.plot_orchestrator as po

    seen = []
    monkeypatch.setattr(po, "redraw_app", lambda ctx: seen.append(ctx))

    from well_viewer.core.scope_registry import register_default_scopes
    from well_viewer.domain.fold_change_scopes import get_scope

    register_default_scopes()

    sentinel_ctx = object()
    fake_app = SimpleNamespace(_ctx=sentinel_ctx)
    for name in ("line", "distribution", "heatmap"):
        scope = get_scope(name)
        assert scope is not None
        scope.redraw(fake_app)  # the coalescer invokes with the APP handle

    assert seen == [sentinel_ctx, sentinel_ctx, sentinel_ctx]
