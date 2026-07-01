"""Unit tests for ``ViewerContext`` — the dependency-injection bundle."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from well_viewer.viewer_context import ViewerContext


def test_viewer_context_holds_its_components():
    app, state, bus, widgets = object(), object(), object(), object()
    ctx = ViewerContext(app=app, state=state, bus=bus, widgets=widgets)
    assert ctx.app is app
    assert ctx.state is state
    assert ctx.bus is bus
    assert ctx.widgets is widgets


def test_viewer_context_is_frozen():
    ctx = ViewerContext(
        app=object(), state=object(), bus=object(), widgets=object(),
    )
    with pytest.raises(FrozenInstanceError):
        ctx.app = object()
