"""Passes that keep a drawn figure's words on it, run after every draw of a plot card's canvas.

A figure's room is only certain once it is drawn — the card around it resizes, the inspector opens beside the page, a
phone turns, the style pass sets the type size — so these run on the canvas's ``draw_event`` and ask for one more draw
only when they changed something, which settles. They were written for the Nanowells page (``SURFACES_TRACKER.md`` row
S22) and run on every plot card through ``ui_helpers.apply_plot_card_decorators`` (row S31): tick labels that run into
each other are thinned, and a y label that starts left of the figure widens the left margin.

Qt-free: matplotlib only.
"""

from __future__ import annotations

def thin_crowded_ticks(fig, renderer) -> bool:
    """Hide every other tick label — every third, and so on — on any axis whose drawn labels run into each other, as
    "10 11 12" drawn "101112" on a phone's narrow figure. Measured with *renderer*, at the size the figure is drawn,
    since the room depends on it. Returns whether any label's visibility changed, so the caller can draw again."""
    changed = False
    for ax in fig.axes:
        for axis, lims in ((ax.xaxis, ax.get_xlim()), (ax.yaxis, ax.get_ylim())):
            lo, hi = sorted(lims)
            # A label hidden on purpose (``tick_params(labelleft=False)`` on a panel that shares its neighbour's
            # scale) is left alone: only the ones shown, or hidden here before, are judged.
            # Each tick's own labels, shown or not, one side at a time — label1 (left, bottom) and label2 (right, top,
            # where a colour bar writes its numbers): ``get_majorticklabels`` lists only the shown ones, so a label
            # thinned on a narrow figure would never come back when the figure widened.
            for side in ("label1", "label2"):
                changed = _thin_side(axis, side, lo, hi, renderer) or changed
    return changed


def _thin_side(axis, side: str, lo: float, hi: float, renderer) -> bool:
    """:func:`thin_crowded_ticks` for one side's labels of one axis; whether any label's visibility changed."""
    pairs = [(tick.get_loc(), getattr(tick, side)) for tick in axis.get_major_ticks()
             if getattr(tick, side).get_text().strip() and lo <= tick.get_loc() <= hi
             and (getattr(tick, side).get_visible() or getattr(getattr(tick, side), "_aw_thinned", False))]
    pairs.sort(key=lambda item: item[0])
    labels = [label for _loc, label in pairs]
    if len(labels) < 2:
        return False
    was = [label.get_visible() for label in labels]
    boxes = []
    for label in labels:
        label.set_visible(True)
        boxes.append(label.get_window_extent(renderer))
    step = 1
    while step < len(labels):
        kept = boxes[::step]
        if not any(min(a.x1, b.x1) - max(a.x0, b.x0) > 1.0 and min(a.y1, b.y1) - max(a.y0, b.y0) > 1.0
                   for a, b in zip(kept, kept[1:])):
            break
        step += 1
    for k, label in enumerate(labels):
        label.set_visible(k % step == 0)
        label._aw_thinned = k % step != 0
    return was != [label.get_visible() for label in labels]


def keep_ticks_apart(canvas) -> None:
    """Thin *canvas*'s crowded tick labels after each draw (:func:`thin_crowded_ticks`), and draw once more when that
    changed anything. A draw that changes nothing asks for none, so it settles."""
    def on_draw(event) -> None:
        thinned = thin_crowded_ticks(event.canvas.figure, event.renderer)
        if keep_labels_on_figure(event.canvas.figure, event.renderer) or thinned:
            event.canvas.draw_idle()

    canvas.mpl_connect("draw_event", on_draw)


def keep_labels_on_figure(fig, renderer) -> bool:
    """Widen the figure's left margin by what its y labels need when one starts left of the figure — the margin is a
    share of the figure's width, and with the inspector open beside the page the figure is narrower and its type the
    same size, so "Cell count" started 8 px left of it. Only where the figure is laid out by its subplot parameters
    (a layout engine places the axes itself, and keeps the labels on). Returns whether it moved anything."""
    engine = fig.get_layout_engine()
    if engine is not None and not getattr(engine, "adjust_compatible", True):
        return False
    # Only axes the subplot parameters place: one placed by hand (``add_axes``) would not move, and asking again on
    # every draw would never settle.
    placed = [ax for ax in fig.axes if ax.get_subplotspec() is not None]
    lefts = [ax.yaxis.label.get_window_extent(renderer).x0 for ax in placed
             if ax.yaxis.label.get_visible() and ax.yaxis.label.get_text().strip()]
    lefts += [t.get_window_extent(renderer).x0 for ax in placed for t in ax.get_yticklabels()
              if t.get_visible() and t.get_text().strip()]
    short = -min(lefts, default=0.0)
    if short <= 0.5 or fig.bbox.width <= 0:
        return False
    left = fig.subplotpars.left + (short + 4.0) / fig.bbox.width
    if left >= fig.subplotpars.right - 0.2:
        return False
    fig.subplots_adjust(left=left)
    return True
