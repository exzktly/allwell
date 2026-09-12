"""Scatter picking: what counts as "near", and what the status line owns.

Two defects in the same pair of handlers.

The nearest-point search ran in raw data space — ``hypot(x - event.xdata,
y - event.ydata)`` — across two axes holding different quantities on different
scales, either of which may be drawn on a log axis. Adding those differences
under one square root sums incommensurate units, so "nearest" was decided
almost entirely by whichever axis carried the larger numbers, and the hover's
``0.5``-data-unit cut-off was most of one axis and invisible on the other. The
search now runs through ``ax.transData``, in screen pixels, which is the
distance the user can actually see.

``on_scatter_motion`` also wrote ``""`` to the status bar on every miss. The
status bar is a single shared slot, so every mouse move that missed a point —
most of the axis area — erased whatever else was standing there: the
fold-change warnings, the load summary, an export failure. The clear is now
conditional on this handler having put the tooltip there itself.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from review.features.scatter import callbacks  # noqa: E402


class _Runtime:
    """Stand-in for ``ctx.runtime.scatter`` — arbitrary attributes, None default."""

    def __getattr__(self, name):
        return None


def _ctx():
    seen: list[str] = []
    return types.SimpleNamespace(
        notify=types.SimpleNamespace(set_status=seen.append),
        runtime=types.SimpleNamespace(scatter=_Runtime()),
        _seen=seen,
    )


@pytest.fixture()
def axes():
    """A panel whose two axes differ in scale by three orders of magnitude."""
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    ax.set_xlim(0, 4000)     # e.g. a GFP intensity
    ax.set_ylim(0, 4)        # e.g. a ratio
    fig.canvas.draw()
    yield ax
    plt.close(fig)


def _event(ax, data_x, data_y):
    """A mouse event at the *pixel* position of a data coordinate."""
    px, py = ax.transData.transform((data_x, data_y))
    return types.SimpleNamespace(xdata=data_x, ydata=data_y, x=px, y=py,
                                 inaxes=ax, button=1)


_NEAR = (2000.0, 2.0, ("A01", "near.tif", "7", 3))
_FAR_IN_X = (2400.0, 2.0, ("A01", "far_in_x.tif", "8", 4))
_FAR_IN_Y = (2000.0, 3.0, ("A01", "far_in_y.tif", "9", 5))


def test_nearest_is_decided_in_screen_space_not_data_space(axes):
    """The x candidate is 400 data units away and the y one is 1.0, so a
    data-space search picks the y one. On screen the axes are 600 px over a
    range of 4000 and 400 px over a range of 4, which puts the x candidate at
    60 px and the y candidate at 100 px — the opposite answer, and the one the
    user sees."""
    points = [_FAR_IN_X, _FAR_IN_Y]
    meta, _d = callbacks._nearest_point(_event(axes, 2000.0, 2.0), points)
    assert meta[1] == "far_in_x.tif", (
        "the data-space search picks whichever axis carries the bigger numbers"
    )


def test_the_hover_radius_is_pixels(axes, monkeypatch):
    monkeypatch.setattr(callbacks, "_points_for_event", lambda _c, _e: [_NEAR])
    ctx = _ctx()
    callbacks.on_scatter_motion(ctx, _event(axes, 2000.0, 2.0))
    assert ctx._seen == ["File: near.tif | Nuclear ID: 7"]


def test_a_miss_with_nothing_of_ours_on_the_line_writes_nothing(axes, monkeypatch):
    monkeypatch.setattr(callbacks, "_points_for_event", lambda _c, _e: [_NEAR])
    ctx = _ctx()
    # Half the x axis away — hundreds of pixels, nowhere near the point.
    callbacks.on_scatter_motion(ctx, _event(axes, 200.0, 0.2))
    assert ctx._seen == [], (
        "a hover that missed every point cleared a status line this handler "
        "had never written to"
    )


def test_a_miss_after_a_hit_clears_our_own_tooltip(axes, monkeypatch):
    monkeypatch.setattr(callbacks, "_points_for_event", lambda _c, _e: [_NEAR])
    ctx = _ctx()
    callbacks.on_scatter_motion(ctx, _event(axes, 2000.0, 2.0))
    callbacks.on_scatter_motion(ctx, _event(axes, 200.0, 0.2))
    assert ctx._seen == ["File: near.tif | Nuclear ID: 7", ""]

    # …and a second miss does not keep re-clearing.
    callbacks.on_scatter_motion(ctx, _event(axes, 200.0, 0.2))
    assert ctx._seen == ["File: near.tif | Nuclear ID: 7", ""]


def test_a_log_axis_does_not_change_which_point_is_nearest():
    """The whole reason to go through transData rather than scale the units."""
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    try:
        ax.set_xscale("log")
        ax.set_xlim(1, 10_000)
        ax.set_ylim(0, 10)
        fig.canvas.draw()
        # In data space 9000 is 8000 units from 1000 and 100 is 900 units from
        # it — so a data-space search calls 100 the nearer. On a log axis drawn
        # to the eye they are a decade either side, and 9000 is the nearer.
        points = [(100.0, 5.0, ("A01", "decade_down.tif", "1", 0)),
                  (9000.0, 5.0, ("A01", "decade_up.tif", "2", 1))]
        px, py = ax.transData.transform((1000.0, 5.0))
        ev = types.SimpleNamespace(xdata=1000.0, ydata=5.0, x=px, y=py,
                                   inaxes=ax, button=1)
        meta, _d = callbacks._nearest_point(ev, points)
        assert meta[1] == "decade_up.tif"
    finally:
        plt.close(fig)


def test_no_axes_under_the_cursor_is_not_a_hit():
    ev = types.SimpleNamespace(xdata=1.0, ydata=1.0, x=10, y=10, inaxes=None)
    assert callbacks._nearest_point(ev, [_NEAR]) == (None, float("inf"))
