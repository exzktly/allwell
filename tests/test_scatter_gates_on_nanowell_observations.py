"""A scatter gate drawn under Observations: Nanowells stays on the plot (ARCHITECTURE §6.2l).

The report: gates drawn on the nanowell scatter vanished the moment they were drawn. ``_on_gate_drawn`` stores the
box and redraws; the nanowell redraw cleared the axes and never painted the gates, and it left no axis columns in
the interaction cache, so the stored gate carried ``x_col = y_col = None``. These drive the real Scatter builder
and orchestrated redraw (CLAUDE.md §7.8 #3) and read the rectangle the figure holds (§7.4 #1).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.domain.observation import NANOWELLS  # noqa: E402
from tests.test_scatter_draws_nanowell_observations import _points, scatter_ctx  # noqa: E402,F401


def _gate_rects(ax):
    from matplotlib.patches import Rectangle

    return [p for p in ax.patches if isinstance(p, Rectangle) and getattr(p, "_scatter_gate_artist", False)]


def _draw_gate(ctx, box):
    from review.features.scatter.gating import _on_gate_drawn, set_gate_draw_mode

    set_gate_draw_mode(ctx, True)
    x0, y0, x1, y1 = box
    _on_gate_drawn(ctx, SimpleNamespace(xdata=x0, ydata=y0), SimpleNamespace(xdata=x1, ydata=y1))


def _box_around_half(points):
    """A box whose lower-left is below every point and whose upper-right is the median, so it holds some points
    and not others — a label of 0% or 100% could not tell a right denominator from a wrong one."""
    points = np.asarray(points, float)
    lo = points.min(axis=0) - 1.0
    hi = np.median(points, axis=0)
    return float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])


def test_a_gate_drawn_on_the_nanowell_scatter_stays_drawn(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx = scatter_ctx
    ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(ctx)
    ax = ctx.widgets.get("_ax_scatter")
    box = _box_around_half(_points(ax))
    _draw_gate(ctx, box)

    ax = ctx.widgets.get("_ax_scatter")
    rects = _gate_rects(ax)
    assert len(rects) == 1, "the gate was cleared by the redraw that follows drawing it"
    r = rects[0]
    assert (r.get_x(), r.get_y(), r.get_x() + r.get_width(), r.get_y() + r.get_height()) == pytest.approx(box)
    # Stamped with the axes it was drawn on — the nanowell metrics, as the per-cell "Nanowell ▸" pickers name them.
    x_key = ctx.widgets.get("_scatter_cells_nw_x_cb").currentData()
    y_key = ctx.widgets.get("_scatter_cells_nw_y_cb").currentData()
    (gate,) = ctx.state.cell_gating.scatter_gates
    assert (gate["x_col"], gate["y_col"]) == (f"nw::{x_key}", f"nw::{y_key}")


def test_the_gate_label_counts_each_group_s_plotted_nanowells(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx = scatter_ctx
    ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(ctx)
    ax = ctx.widgets.get("_ax_scatter")
    series = [(c.get_label(), np.asarray(c.get_offsets())) for c in ax.collections if len(c.get_offsets())]
    box = _box_around_half(np.vstack([p for _l, p in series]))
    _draw_gate(ctx, box)

    x0, y0, x1, y1 = box
    want = [f"{label}: {100.0 * np.mean((p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)):.1f}%"
            for label, p in series]
    ax = ctx.widgets.get("_ax_scatter")
    got = [t.get_text() for t in ax.texts if getattr(t, "_scatter_gate_artist", False)]
    assert got == ["Gate 1", *want]


def test_the_nanowell_gate_shows_on_the_matching_cell_axes_and_no_other(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx = scatter_ctx
    ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(ctx)
    _draw_gate(ctx, _box_around_half(_points(ctx.widgets.get("_ax_scatter"))))

    ctx.state.plot.observations = "cells"
    ctx.widgets.get("_scatter_ch_x_cb").setCurrentText("GFP")
    ctx.widgets.get("_scatter_ch_y_cb").setCurrentText("GFP")
    redraw_scatter_orchestrate(ctx)
    assert _gate_rects(ctx.widgets.get("_ax_scatter")) == []       # another pair: the box means nothing here

    ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(ctx)
    assert len(_gate_rects(ctx.widgets.get("_ax_scatter"))) == 1   # back on its own axes, it is drawn again
