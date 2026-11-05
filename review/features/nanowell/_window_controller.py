"""The Distribution window's controller: the drag that draws it, the controls that edit it, and what it shows.

A press on the Distribution figure starts either a click (select one nanowell, as it always did) or a window; the
release tells them apart by how far the pointer moved (``controller.CLICK_SLOP_PX``). A window is kept per metric
and axis on ``NanowellState.windows`` and read against the model the figure was last drawn from
(``NanowellState.window_model``), so its members are the model's, not the drawing's (``window_compute``).

Reaches ``controller`` the way ``_qc_controller`` does, through :func:`_nwc`, because the controller imports this
module.
"""

from __future__ import annotations

import math
from dataclasses import replace

from review.features.nanowell import controller as _controller
from review.features.nanowell import window_compute as wc
from review.features.nanowell._window_render import DRAG_GID, draw_drag, draw_window
from review.features.nanowell.renderer import DENSITY_ABOVE, MAX_PANELS


def _nwc():
    return _controller


def _state(ctx):
    return ctx.state.feature_slice("nanowell")


def current(st):
    """``(window, result)`` for what the figure draws now — the window kept for its metric and axis and its members
    on the model — or ``(None, None)``."""
    model = st.window_model
    if model is None:
        return None, None
    window = st.windows.get(wc.window_key(model.column, model.axis))
    return (window, wc.members(model, window)) if window is not None else (None, None)


def after_draw(ctx, st, model) -> None:
    """Called by ``controller.redraw`` after a view is drawn; on the Distribution view, keep the model the figure was
    drawn from and draw the window on it. (The check is here, not in ``redraw``, which is at the complexity cap.)"""
    if st.view != "distribution":
        return
    st.window_model = model
    window, result = current(st)
    if window is not None and st.hits is not None:
        draw_window(st.hits, window, result)
    _nwc()._call(ctx, "_nw_window_refresh")


def on_press(ctx, st, event) -> None:
    """A press on a Distribution panel: remember where, until the release says whether it was a click or a drag."""
    if event.inaxes is None or event.button != 1:
        return
    st.window_press = (event.inaxes, float(event.x), float(event.y))


def on_motion(ctx, st, event) -> None:
    """A drag in progress: the rectangle follows the pointer, drawn on the axes the press began on."""
    press = st.window_press
    if press is None or event.x is None:
        return
    ax, x, y = press
    if abs(event.x - x) + abs(event.y - y) <= _nwc().CLICK_SLOP_PX:
        return
    _remove_drag(ax)
    inverse = ax.transData.inverted()
    (dx0, dy0), (dx1, dy1) = inverse.transform((x, y)), inverse.transform((event.x, event.y))
    draw_drag(ax, dx0, dy0, dx1, dy1)
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is not None:
        canvas.draw_idle()


def on_release(ctx, st, event) -> None:
    """The end of a press: within the slop it is a click and selects the nanowell under it (or clears the selection on
    empty space); beyond it the drag is a window, its columns snapped to the ones it overlaps."""
    press, st.window_press = st.window_press, None
    if press is None:
        return
    ax, x, y = press
    _remove_drag(ax)
    ex, ey = (x, y) if event.x is None else (float(event.x), float(event.y))
    nwc = _nwc()
    if abs(ex - x) + abs(ey - y) <= nwc.CLICK_SLOP_PX:
        site = st.hits.nearest(ax, x, y, nwc.CLICK_RADIUS_PX) if st.hits is not None else None
        nwc._select(ctx, site)
        return
    model = st.window_model
    index = dict(st.hits.panels).get(ax) if st.hits is not None else None
    if model is None or index is None:
        return
    inverse = ax.transData.inverted()
    (x0, y0), (x1, y1) = inverse.transform((x, y)), inverse.transform((ex, ey))
    keys = wc.snap(model, index, x0, x1)
    if not keys:
        ctx.notify.set_status("The window covers no column: drag across the dots you want.")
        nwc.redraw(ctx)
        return
    draft = wc.DistributionWindow(model.column, model.axis, keys, float(min(y0, y1)), float(max(y0, y1)),
                                  panel=model.panels[index].title, drawn_under=wc.settings_key(st))
    _store(ctx, st, replace(draft, n_at_drawing=len(wc.members(model, draft).rows)))


def _remove_drag(ax) -> None:
    for patch in [p for p in ax.patches if p.get_gid() == DRAG_GID]:
        patch.remove()


def _store(ctx, st, window) -> None:
    """Keep *window* for the metric and axis drawn (``None`` removes it), start the pop-out's pages over, save, and
    draw again."""
    model = st.window_model
    if model is None:
        return
    key = wc.window_key(model.column, model.axis)
    if window is None:
        st.windows.pop(key, None)
    else:
        st.windows[key] = window
    st.window_page = 0
    nwc = _nwc()
    nwc._save_soon(ctx)
    nwc.redraw(ctx)
    nwc.draw_popout(ctx)


def clear_window(ctx) -> None:
    """**✕**: remove the window drawn for this metric and axis."""
    st = _state(ctx)
    if st is not None and current(st)[0] is not None:
        _store(ctx, st, None)


def set_window_range(ctx, lo: float, hi: float) -> None:
    """The Low and High fields: the window's range, swapped if given the wrong way round; keys, rule and panel stay."""
    st = _state(ctx)
    window = current(st)[0] if st is not None else None
    if window is None or not (math.isfinite(lo) and math.isfinite(hi)):
        return
    lo, hi = min(lo, hi), max(lo, hi)
    _store(ctx, st, replace(window, lo=lo, hi=hi))


def set_window_rule(ctx, rule: str) -> None:
    """*every* or *any* timepoint (``window_compute.RULES``); anything else is ignored."""
    st = _state(ctx)
    window = current(st)[0] if st is not None else None
    if window is not None and rule in wc.RULES and rule != window.rule:
        _store(ctx, st, replace(window, rule=rule))


def set_window_panel_only(ctx, on: bool) -> None:
    """**This panel only**: cover the panel the window was drawn in, or every panel."""
    st = _state(ctx)
    window = current(st)[0] if st is not None else None
    if window is not None and bool(on) != window.panel_only:
        _store(ctx, st, replace(window, panel_only=bool(on)))


def strip_text(st) -> str:
    """The sentence the strip and the pop-out write about the window — range, counts, members with no dot on the
    figure and why, and what changed since it was drawn; ``""`` with no window."""
    window, result = current(st)
    if window is None:
        return ""
    model = st.window_model
    hidden = (wc.not_drawn(result, model, st.hits, max_panels=MAX_PANELS, density_above=DENSITY_ABOVE)
              if st.hits is not None else {})
    said = wc.label_text(window, result, hidden, model.axis)
    changed = wc.changed_sentence(window, wc.settings_key(st), len(result.rows))
    return f"{said} {changed}".strip()
