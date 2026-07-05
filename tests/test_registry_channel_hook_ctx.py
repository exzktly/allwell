"""Regression: the multivariate on_channel_changed hook is ctx-native.

``recalculate_threshold`` fans a channel change out to each feature's
``on_channel_changed(ctx)`` hook. All seven hooks now take ``ctx`` and guard
widget existence via ``ctx.widgets.has(...)``; ``_channel_multivariate`` forwards
that ``ctx`` straight to the ctx-native ``mv_on_channel_changed`` (it must not
reach ``app.runtime`` — which briefly regressed to a hard crash when a stale
call passed the raw app before the hooks were converted).
"""
from __future__ import annotations

from types import SimpleNamespace


def _ctx(has_canvas: bool):
    return SimpleNamespace(widgets=SimpleNamespace(
        has=lambda name: has_canvas and name == "_mv_canvas"))


def test_channel_multivariate_forwards_ctx(monkeypatch):
    import well_viewer.features.multivariate.controller as mv

    seen = []
    monkeypatch.setattr(mv, "mv_on_channel_changed", lambda ctx: seen.append(ctx))

    from well_viewer.features.registry import _channel_multivariate

    ctx = _ctx(has_canvas=True)
    _channel_multivariate(ctx)

    assert seen == [ctx]


def test_channel_multivariate_noop_without_canvas(monkeypatch):
    import well_viewer.features.multivariate.controller as mv

    seen = []
    monkeypatch.setattr(mv, "mv_on_channel_changed", lambda ctx: seen.append(ctx))

    from well_viewer.features.registry import _channel_multivariate

    _channel_multivariate(_ctx(has_canvas=False))  # no _mv_canvas → hook must not fire

    assert seen == []
