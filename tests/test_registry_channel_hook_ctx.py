"""Regression: the multivariate on_channel_changed hook threads app._ctx.

``recalculate_threshold`` fans a channel change out to each feature's
``on_channel_changed(app)`` hook with the *app*. ``mv_on_channel_changed`` is
ctx-native, so ``_channel_multivariate`` must forward ``app._ctx`` — passing
the raw app made it read ``app.runtime`` (AttributeError), and this hook loop
is not wrapped in try/except, so it was a hard crash on channel change whenever
the Multivariate tab had been built.
"""
from __future__ import annotations

from types import SimpleNamespace


def test_channel_multivariate_forwards_ctx(monkeypatch):
    import well_viewer.features.multivariate.controller as mv

    seen = []
    monkeypatch.setattr(mv, "mv_on_channel_changed", lambda ctx: seen.append(ctx))

    from well_viewer.features.registry import _channel_multivariate

    sentinel_ctx = object()
    app = SimpleNamespace(_ctx=sentinel_ctx, _mv_canvas=object())
    _channel_multivariate(app)

    assert seen == [sentinel_ctx]


def test_channel_multivariate_noop_without_canvas(monkeypatch):
    import well_viewer.features.multivariate.controller as mv

    seen = []
    monkeypatch.setattr(mv, "mv_on_channel_changed", lambda ctx: seen.append(ctx))

    from well_viewer.features.registry import _channel_multivariate

    app = SimpleNamespace(_ctx=object())  # no _mv_canvas → hook must not fire
    _channel_multivariate(app)

    assert seen == []
