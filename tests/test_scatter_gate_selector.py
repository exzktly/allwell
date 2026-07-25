"""Regression: the scatter gate RectangleSelector must always bind to the
CURRENT axes, not a stale one cached from a previous redraw.

Bug: the selector was created once and reused. After the first gate, the scatter
redraws (``ax.clear()`` on overlay, full ``fig.clear()`` rebuild in "Separate
axes"), leaving the cached selector bound to a dead axis — so the first gate was
correct but every later gate (and any gate in faceted mode) captured drag
coordinates against the wrong axes. The fix recreates the selector on every arm
and re-arms after a faceted redraw.
"""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from well_viewer.features.scatter.gating import (
    refresh_gate_selector,
    set_gate_draw_mode,
)


def _mk_ctx(app):
    from types import SimpleNamespace
    from well_viewer.core.app_runtime import AppRuntime
    return SimpleNamespace(
        app=app,
        state=getattr(app, "_app_state", None),
        runtime=AppRuntime(app),
        widgets=SimpleNamespace(
            get_or_none=lambda n: getattr(app, n, None),
            register=lambda n, w: (setattr(app, n, w), w)[1],
        ),
        notify=SimpleNamespace(set_status=lambda _m: None),
    )



def _app_with_axes():
    fig, ax = plt.subplots()
    app = SimpleNamespace(
        _ax_scatter=ax, _scatter_gate_selector=None,
        _scatter_gate_draw_mode=False,
    )
    app._ctx = _mk_ctx(app)
    return app, fig, ax


def test_arming_binds_selector_to_current_axes():
    app, _fig, ax = _app_with_axes()
    set_gate_draw_mode(app._ctx, True)
    assert app._scatter_gate_selector is not None
    assert app._scatter_gate_selector.ax is ax


def test_rearm_rebinds_to_new_axes_after_rebuild():
    app, _fig, _ax = _app_with_axes()
    set_gate_draw_mode(app._ctx, True)
    sel1 = app._scatter_gate_selector
    # Simulate a redraw that rebuilt the figure/axes (overlay clear / faceted).
    _fig2, ax2 = plt.subplots()
    app._ax_scatter = ax2
    refresh_gate_selector(app._ctx)  # draw mode still on
    sel2 = app._scatter_gate_selector
    assert sel2 is not sel1          # fresh selector, not the stale one
    assert sel2.ax is ax2            # bound to the live axes


def test_second_arm_creates_fresh_selector():
    # Even without changing axes, re-arming discards the cached selector — a
    # cached one may have been invalidated by an intervening ax.clear().
    app, _fig, _ax = _app_with_axes()
    set_gate_draw_mode(app._ctx, True)
    sel1 = app._scatter_gate_selector
    set_gate_draw_mode(app._ctx, False)
    set_gate_draw_mode(app._ctx, True)
    assert app._scatter_gate_selector is not sel1


def test_refresh_is_noop_when_draw_mode_off():
    app, _fig, _ax = _app_with_axes()
    refresh_gate_selector(app._ctx)
    assert app._scatter_gate_selector is None
