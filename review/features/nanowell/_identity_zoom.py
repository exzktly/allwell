"""Zoom and pan in every Identity sub-view: the wheel zooms about the pointer, a left-drag pans, a double-click goes
back to the full view.

The page's canvas events reach these through ``controller.on_click`` / ``on_motion`` / ``on_release`` /
``on_scroll`` — the one wiring the Array view's pan and zoom use (``tab_view`` connects it) — which hand them here
while the Identity view is on screen. The numbers are ``zoom_compute``'s; this module only reads the event, applies
the limits to the axes and every axes linked with it (``IdentityDrawn.links``), and repaints.

The zoom is kept on the state (``NanowellState.identity_zoom``) as each axes' limits by its name, for one
``(well, timepoint, mode)``: a redraw of the same one — the montages arriving, a setting changed — puts the limits
back (:func:`restore`), and another well, timepoint or mode, or a figure with nothing to zoom, starts at the full
view. Blink's timer only flips which frame is visible, so it never touches the limits.
"""

from __future__ import annotations

from review.features.nanowell import zoom_compute as zc


def _repaint(ctx) -> None:
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is not None:
        canvas.draw_idle()


def _panel(drawn, ax) -> str | None:
    """The zoomable axes' name *ax* is on *drawn*, or ``None``."""
    if drawn is None or ax is None:
        return None
    return next((name for name in drawn.zoomable if drawn.axes.get(name) is ax), None)


def restore(st, drawn) -> None:
    """After a draw: remember *drawn*'s full view, and put back the zoom kept for the same ``(well, timepoint,
    mode)`` — or forget it, when this draw is of another one or drew nothing to zoom."""
    st.identity_press = None
    if drawn is None or not drawn.zoomable:
        st.identity_zoom, st.identity_zoom_for = {}, None
        return
    drawn.home = {name: (drawn.axes[name].get_xlim(), drawn.axes[name].get_ylim()) for name in drawn.zoomable}
    key = (drawn.pair.well, drawn.pair.timepoint, drawn.mode)
    if key != st.identity_zoom_for:
        st.identity_zoom, st.identity_zoom_for = {}, key
        return
    for name, (xlim, ylim) in st.identity_zoom.items():
        if name in drawn.home:
            drawn.axes[name].set_xlim(*xlim)
            drawn.axes[name].set_ylim(*ylim)


def forget(st) -> None:
    """The Identity view left the screen: its zoom goes with it."""
    st.identity_zoom, st.identity_zoom_for, st.identity_press = {}, None, None


def _apply(st, drawn, name: str, xlim, ylim) -> None:
    """Set *name*'s limits and every axes linked with it, and keep them."""
    for other in zc.linked(drawn.links, name):
        ax = drawn.axes.get(other)
        if ax is None:
            continue
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        st.identity_zoom[other] = (tuple(xlim), tuple(ylim))


def on_scroll(ctx, st, event) -> None:
    """The wheel: zoom the panel under the pointer about the pointer, one ``WHEEL_STEP`` a notch. Off every panel,
    a view with one panel zooms it about its centre (over VNC a swipe arrives wherever the pointer was left)."""
    drawn = st.identity_drawn
    if drawn is None or not drawn.zoomable or not event.step:
        return
    name = _panel(drawn, event.inaxes)
    ax = drawn.axes.get(name) if name else None
    if ax is not None and event.xdata is not None:
        at = (event.xdata, event.ydata)
    elif len(drawn.zoomable) == 1:
        name = drawn.zoomable[0]
        ax = drawn.axes[name]
        (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
        at = ((x0 + x1) / 2, (y0 + y1) / 2)
    else:
        return
    home = drawn.home.get(name)
    xlim, ylim = zc.wheel_limits(ax.get_xlim(), ax.get_ylim(), at, event.step, home[0] if home else None)
    _apply(st, drawn, name, xlim, ylim)
    _repaint(ctx)


def on_press(ctx, st, event) -> None:
    """A left press on a panel starts a pan; a left double-click puts the panel (and its linked ones) back to the
    full view."""
    drawn = st.identity_drawn
    name = _panel(drawn, event.inaxes)
    if name is None or event.button != 1:
        return
    ax = drawn.axes[name]
    if event.dblclick:
        st.identity_press = None
        xlim, ylim = drawn.home[name]
        _apply(st, drawn, name, xlim, ylim)
        for other in zc.linked(drawn.links, name):
            st.identity_zoom.pop(other, None)
        _repaint(ctx)
        return
    st.identity_press = (name, event.x, event.y, ax.get_xlim(), ax.get_ylim(), ax.transData.inverted().frozen())


def on_drag(ctx, st, event) -> bool:
    """A drag after :func:`on_press`: the picture follows the pointer. Whether a pan is under way (the pointer's
    hover is then left alone)."""
    press, drawn = st.identity_press, st.identity_drawn
    if press is None or drawn is None:
        return False
    if event.x is None:
        return True
    name, x, y, xlim, ylim, inverse = press
    (px, py), (cx, cy) = inverse.transform((x, y)), inverse.transform((event.x, event.y))
    _apply(st, drawn, name, *zc.pan_limits(xlim, ylim, cx - px, cy - py))
    _repaint(ctx)
    return True


def on_release(st) -> None:
    st.identity_press = None
