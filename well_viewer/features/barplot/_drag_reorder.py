"""Drag-to-reorder for the Bar plot's columns.

Five small handlers wired to the bar canvas's matplotlib mouse events by
``barplot/tab_view.py``: press records which column was grabbed, motion draws
the dashed drop guide across all three panels, release commits the new order to
``PlotState.bar_order`` and redraws. ``bar_reset_order`` is the Reset Order
button beside them.

Split out of ``barplot/controller.py``, which holds the redraw orchestration and
the tab's other controls, to keep that module under its line budget. This is a
clean seam rather than a convenient one: nothing here draws a bar or decides a
value — the handlers only read the recorded bar positions, mutate
``ctx.runtime.barplot.drag_state`` and ``ctx.state.plot.bar_order``, and ask for
a redraw. The view (canvas + axes) stays owned by the shell; these reach it only
through the widget registry.

The pure half of the interaction — ``bar_idx_at_x`` / ``bar_drag_xs`` /
``bar_guide_x``, the arithmetic that turns a data-x into a column index and a
guide position — lives in ``barplot/collect.py`` and is Qt-free, so it is
testable without a canvas.
"""

from __future__ import annotations

from well_viewer.features.barplot.collect import (
    bar_drag_xs,
    bar_guide_x,
    bar_idx_at_x,
)


def bar_event_xdata(ctx, event) -> float | None:
    """Return data-x for a matplotlib MouseEvent over any bar axis."""
    ax = getattr(event, "inaxes", None)
    if ax is not ctx.widgets.get_or_none("_ax_bar_mean") and ax is not ctx.widgets.get_or_none("_ax_bar_frac") and ax is not ctx.widgets.get_or_none("_ax_bar_n"):
        return None
    xdata = getattr(event, "xdata", None)
    if xdata is None:
        return None
    return float(xdata)


# ``bar_idx_at_x`` / ``bar_drag_xs`` / ``bar_guide_x`` / ``bar_err_settings``
# moved to ``barplot/collect.py`` (Qt-free) and re-exported above.


def bar_reset_order(ctx) -> None:
    ctx.state.plot.bar_order = None
    ctx.runtime.barplot.reset_order_btn.setProperty("variant", "toggle_muted")
    ctx.runtime.barplot.reset_order_btn.style().unpolish(ctx.runtime.barplot.reset_order_btn)
    ctx.runtime.barplot.reset_order_btn.style().polish(ctx.runtime.barplot.reset_order_btn)
    from well_viewer.features.barplot.renderer import redraw_bars
    redraw_bars(ctx)


def on_bar_drag_press(ctx, event) -> None:
    """Begin drag — record which bar was pressed."""
    if getattr(event, "button", None) != 1:
        return
    xdata = bar_event_xdata(ctx, event)
    if xdata is None:
        return
    keys = ctx.runtime.barplot.current_keys()
    n = len(keys)
    if n < 2:
        return
    idx = bar_idx_at_x(xdata, n, bar_drag_xs(ctx, keys))
    ctx.runtime.barplot.drag_state.update(active=True, src_idx=idx, cur_idx=idx)


def on_bar_drag_motion(ctx, event, *, accent_color: str) -> None:
    """Update drop-target indicator while dragging."""
    ds = ctx.runtime.barplot.drag_state
    if not ds["active"]:
        return
    xdata = bar_event_xdata(ctx, event)
    if xdata is None:
        return
    keys = ctx.runtime.barplot.current_keys()
    n = len(keys)
    if n < 2:
        return
    xs = bar_drag_xs(ctx, keys)
    tgt = bar_idx_at_x(xdata, n, xs)
    if tgt == ds["cur_idx"]:
        return
    ds["cur_idx"] = tgt

    guide_x = bar_guide_x(xs, tgt, ds["src_idx"], n)
    for ax in (ctx.widgets.get_or_none("_ax_bar_mean"), ctx.widgets.get_or_none("_ax_bar_frac"), ctx.widgets.get_or_none("_ax_bar_n")):
        if ax is None:
            continue
        for ln in list(ax.lines):
            if getattr(ln, "_bar_drag_guide", False):
                ln.remove()
        ln = ax.axvline(guide_x, color=accent_color, lw=1.5, ls="--",
                        alpha=0.8, zorder=10)
        ln._bar_drag_guide = True
    ctx.runtime.barplot.canvas.draw_idle()


def on_bar_drag_release(ctx, event) -> None:
    """Finalise drop — reorder and redraw."""
    ds = ctx.runtime.barplot.drag_state
    if not ds["active"]:
        return
    ds["active"] = False

    for ax in (ctx.widgets.get_or_none("_ax_bar_mean"), ctx.widgets.get_or_none("_ax_bar_frac"), ctx.widgets.get_or_none("_ax_bar_n")):
        if ax is None:
            continue
        for ln in list(ax.lines):
            if getattr(ln, "_bar_drag_guide", False):
                ln.remove()

    src = ds["src_idx"]
    tgt = ds["cur_idx"]
    if src == tgt:
        ctx.runtime.barplot.canvas.draw_idle()
        return

    keys = ctx.runtime.barplot.current_keys()
    if not (0 <= src < len(keys) and 0 <= tgt < len(keys)):
        ctx.runtime.barplot.canvas.draw_idle()
        return

    item = keys.pop(src)
    keys.insert(tgt, item)
    ctx.state.plot.bar_order = keys
    ctx.runtime.barplot.reset_order_btn.setProperty("variant", "toggle_accent")
    ctx.runtime.barplot.reset_order_btn.style().unpolish(ctx.runtime.barplot.reset_order_btn)
    ctx.runtime.barplot.reset_order_btn.style().polish(ctx.runtime.barplot.reset_order_btn)
    from well_viewer.features.barplot.renderer import redraw_bars
    redraw_bars(ctx)
