"""Hovering a scatter axis must not wipe the status line it does not own.

``on_scatter_motion`` wrote a "File: … | Nuclear ID: …" tooltip into the status
bar when the cursor landed near a point and wrote ``""`` when it did not. The
status bar is a single shared slot, so every mouse move that missed a point —
which is most of the axis area — erased whatever else was standing there: the
fold-change control warnings, the load summary, an export failure. The clear is
now conditional on the handler having put the tooltip there itself.
"""

from __future__ import annotations

import types

import pytest

from well_viewer.features.scatter import callbacks  # noqa: E402


class _Runtime:
    """Stand-in for ``ctx.runtime.scatter`` — arbitrary attributes, None default."""

    def __getattr__(self, name):
        return None


def _ctx(points):
    seen: list[str] = []
    return types.SimpleNamespace(
        notify=types.SimpleNamespace(set_status=seen.append),
        runtime=types.SimpleNamespace(scatter=_Runtime()),
        _seen=seen,
        _points=points,
    )


def _event(x, y):
    return types.SimpleNamespace(xdata=x, ydata=y)


@pytest.fixture()
def one_point(monkeypatch):
    monkeypatch.setattr(
        callbacks, "_points_for_event",
        lambda ctx, _ev: [(0.0, 0.0, ("A01", "img.tif", "7", 3))],
    )


def test_a_miss_with_nothing_of_ours_on_the_line_writes_nothing(one_point):
    ctx = _ctx(None)
    callbacks.on_scatter_motion(ctx, _event(50.0, 50.0))
    assert ctx._seen == [], (
        "a hover that missed every point cleared a status line this handler "
        "had never written to"
    )


def test_a_hit_writes_the_tooltip(one_point):
    ctx = _ctx(None)
    callbacks.on_scatter_motion(ctx, _event(0.01, 0.01))
    assert ctx._seen == ["File: img.tif | Nuclear ID: 7"]


def test_a_miss_after_a_hit_clears_our_own_tooltip(one_point):
    ctx = _ctx(None)
    callbacks.on_scatter_motion(ctx, _event(0.01, 0.01))
    callbacks.on_scatter_motion(ctx, _event(50.0, 50.0))
    assert ctx._seen == ["File: img.tif | Nuclear ID: 7", ""]

    # …and a second miss does not keep re-clearing.
    callbacks.on_scatter_motion(ctx, _event(50.0, 50.0))
    assert ctx._seen == ["File: img.tif | Nuclear ID: 7", ""]
