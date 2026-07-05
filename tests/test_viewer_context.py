"""Unit tests for ``ViewerContext`` — the dependency-injection bundle."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from well_viewer.viewer_context import ViewerContext


def test_viewer_context_holds_its_components():
    state, bus, widgets = object(), object(), object()
    ctx = ViewerContext(state=state, bus=bus, widgets=widgets)
    assert ctx.state is state
    assert ctx.bus is bus
    assert ctx.widgets is widgets


def test_viewer_context_has_no_public_app_escape_hatch():
    # A8 T6: the generic ``app`` field/accessor is gone — the god-object is
    # reachable only through the frozen named accessors (window, persistence, …).
    ctx = ViewerContext(state=object(), bus=object(), widgets=object())
    assert not hasattr(ctx, "app")


def test_window_accessor_exposes_the_private_host():
    win = object()
    ctx = ViewerContext(state=object(), bus=object(), widgets=object(), _window=win)
    assert ctx.window is win


def test_viewer_context_is_frozen():
    ctx = ViewerContext(state=object(), bus=object(), widgets=object())
    with pytest.raises(FrozenInstanceError):
        ctx._window = object()
