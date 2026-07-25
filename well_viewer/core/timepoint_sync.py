"""Shared "focus timepoint" sync across the plotting tabs (N5).

Channel / property / threshold are already shared across the plotting tabs via
the context bar; timepoint was the one axis each tab tracked on its own. This
module is the small cross-cutting seam that lets the single-timepoint tabs (the
Heat Map slider and the Distribution combo) agree on **one** timepoint:
``PlotState.focus_timepoint`` (the dataset's timepoint-hours float).

Writing it emits the existing ``plot_options_changed`` signal so visible scopes
redraw — in Split View that updates both panes live; otherwise the other tab
picks the value up on its next show (the tab-change refreshers call
:func:`apply_focus_to_*`). No new EventBus signal is added (G5); state lives on
``PlotState`` (G3/G4).

Qt-free: stdlib only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from well_viewer.core.safe import swallowed


def current_focus_tp(ctx) -> float | None:
    """The shared focus timepoint, or ``None`` when not yet chosen.

    A8-teardown T3: this leaf reads only typed state, so it takes ``ctx`` (not
    the god-object) — ``ctx.state.plot.focus_timepoint`` is the same PlotState.
    """
    return ctx.state.plot.focus_timepoint


def set_focus_tp(ctx, tp, *, emit: bool = True) -> None:
    """Set the shared focus timepoint and (by default) notify the other tabs.

    ``tp`` is coerced to ``float`` (``None`` clears it). When *emit* is True the
    ``plot_options_changed`` signal fires so visible plot scopes redraw.

    A8-teardown T3: pure state + bus, so this leaf takes ``ctx`` —
    ``ctx.state.plot`` and ``ctx.bus`` replace the god-object reach-through with
    zero residual.
    """
    try:
        val: float | None = None if tp is None else float(tp)
    except (TypeError, ValueError):
        val = None
    if val == ctx.state.plot.focus_timepoint:
        return
    ctx.state.plot.focus_timepoint = val
    if emit:
        try:
            ctx.bus.plot_options_changed.emit(ctx.state.plot)
        except Exception:
            swallowed()


def nearest_index(tps: Sequence[float], tp) -> int | None:
    """Index of the timepoint in *tps* closest to *tp*, or ``None`` when *tps*
    is empty / *tp* is unset. Tolerates float drift between tabs."""
    if not tps or tp is None:
        return None
    try:
        target = float(tp)
    except (TypeError, ValueError):
        return None
    best_i, best_d = 0, math.inf
    for i, t in enumerate(tps):
        try:
            d = abs(float(t) - target)
        except (TypeError, ValueError):
            continue
        if d < best_d:
            best_i, best_d = i, d
    return best_i
