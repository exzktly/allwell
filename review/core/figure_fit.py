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


#: The most redraws in a row :func:`keep_ticks_apart` asks for before it lets a figure be.
_MAX_ROUNDS = 3


def keep_ticks_apart(canvas) -> None:
    """Thin *canvas*'s crowded tick labels after each draw (:func:`thin_crowded_ticks`), and draw once more when that
    changed anything. A draw that changes nothing asks for none, so it settles."""
    # A pass may change the layout it measured — hidden labels give a layout engine room, a wider margin narrows the
    # axes — and the next draw then judges again. Most settle in one more draw; one that does not would ask for draws
    # for ever (an Image Browser figure did, and its page never finished drawing), so a run of redraws asked for here
    # is cut at _MAX_ROUNDS, and counted afresh after a draw that changed nothing.
    rounds = {"n": 0}

    def on_draw(event) -> None:
        thinned = thin_crowded_ticks(event.canvas.figure, event.renderer)
        moved = keep_labels_on_figure(event.canvas.figure, event.renderer)
        fitted = fit_words(event.canvas.figure, event.renderer)
        if not (thinned or moved or fitted):
            rounds["n"] = 0
            return
        if rounds["n"] < _MAX_ROUNDS:
            rounds["n"] += 1
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
    # Nor a figure that also has axes placed by hand (a colour bar, a key): moving the subplots would move them into
    # it — a Heat Map's cell labels ran into its colour bar's numbers.
    if len(placed) != len(fig.axes):
        return False
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


# ── words off the figure, a key over a label, a label into the next panel ────
#: The smallest share of its own size a text is made to stay on the figure before it is broken onto lines.
_SMALLEST = 0.6
#: Where a key may go, tried in order when the one it has covers a label.
_LEGEND_PLACES = ("upper right", "upper left", "lower right", "lower left", "center right", "center left",
                  "upper center", "lower center")


def figure_texts(fig) -> list:
    """The words a reader reads off *fig*: the figure's own texts, each axes' titles, axis labels and texts. Tick
    labels and a key's entries are left out (the tick pass and the key pass judge those)."""
    out = list(fig.texts)
    for ax in fig.axes:
        out += [ax.title, ax._left_title, ax._right_title, ax.xaxis.label, ax.yaxis.label, *ax.texts]
    return [t for t in out if t.get_visible() and t.get_text().strip()]


def _off(box, fig) -> bool:
    return box.x0 < -1.0 or box.y0 < -1.0 or box.x1 > fig.bbox.width + 1.0 or box.y1 > fig.bbox.height + 1.0


def _greedy(text: str, width, room: float) -> str:
    lines, line = [], ""
    for word in text.replace("\n", " ").split(" "):
        trial = f"{line} {word}" if line else word
        if not line or width(trial) <= room:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return "\n".join(lines)


def fit_text_inside(t, fig, renderer) -> bool:
    """Keep *t* on *fig*: made smaller, down to :data:`_SMALLEST` of the size its renderer gave it, and if that is
    not enough broken at spaces to the figure's width (its height, for a label written upright). Measured from the
    text as its renderer last wrote it, never from an earlier fit. Returns whether it changed."""
    now = (t.get_text(), t.get_fontsize())
    if getattr(t, "_aw_fit_out", None) != now:            # written afresh since the last fit
        t._aw_fit_src = now
    text, size = t._aw_fit_src
    t.set_text(text)
    t.set_fontsize(size)
    if _off(t.get_window_extent(renderer), fig):
        upright = abs((t.get_rotation() or 0) % 180 - 90) < 1
        room = (fig.bbox.height if upright else fig.bbox.width) * 0.92
        fitted = False
        for share in (0.9, 0.8, 0.7, _SMALLEST):
            t.set_fontsize(size * share)
            if not _off(t.get_window_extent(renderer), fig):
                fitted = True
                break
        if not fitted:
            prop = t.get_fontproperties()
            t.set_text(_greedy(text, lambda s: renderer.get_text_width_height_descent(s, prop, ismath=False)[0],
                               room))
    t._aw_fit_out = (t.get_text(), t.get_fontsize())
    return t._aw_fit_out != now


def _overlaps(a, b) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) > 2 and min(a.y1, b.y1) - max(a.y0, b.y0) > 2


def keep_keys_off_labels(fig, renderer) -> bool:
    """Move a key that covers a label to the first place in :data:`_LEGEND_PLACES` where it covers none and stays on
    the figure; a key placed outside its axes on purpose (``bbox_to_anchor``) is left where it was put."""
    changed = False
    texts = figure_texts(fig)
    for ax in fig.axes:
        legend = ax.get_legend()
        if legend is None or not legend.get_visible() or legend._bbox_to_anchor is not None:
            continue
        own = {id(x) for x in legend.get_texts()} | {id(legend.get_title())}
        others = [t.get_window_extent(renderer) for t in texts if id(t) not in own]

        def clear() -> bool:
            box = legend.get_window_extent(renderer)
            return not _off(box, fig) and not any(_overlaps(box, o) for o in others)

        if clear():
            continue
        for place in _LEGEND_PLACES:
            legend.set_loc(place)
            if clear():
                changed = True
                break
    return changed


def keep_rows_apart(fig, renderer) -> bool:
    """Open the gap between rows of panels when a panel's x label or tick labels run into the title of the panel
    under it — only on a figure laid out by its subplot parameters, all of whose axes they place."""
    engine = fig.get_layout_engine()
    if engine is not None and not getattr(engine, "adjust_compatible", True):
        return False
    axes = list(fig.axes)
    if not axes or any(ax.get_subplotspec() is None for ax in axes):
        return False
    for upper in axes:
        below = [upper.xaxis.label.get_window_extent(renderer)] if upper.xaxis.label.get_text().strip() else []
        below += [t.get_window_extent(renderer) for t in upper.get_xticklabels() if t.get_visible() and t.get_text()]
        for lower in axes:
            # Only a panel in a row below: a neighbour beside it is a matter of wspace, which this leaves alone.
            if lower is upper or lower.get_position().y1 > upper.get_position().y0 + 1e-6:
                continue
            # Its title, or its plot itself: with the rows close enough the two pass each other, and the label is
            # then written over the panel below.
            boxes = [t.get_window_extent(renderer) for t in (lower.title, lower._left_title, lower._right_title)
                     if t.get_text().strip()] + [lower.get_window_extent(renderer)]
            if any(_overlaps(b, o) for b in below for o in boxes):
                fig.subplots_adjust(hspace=(fig.subplotpars.hspace or 0.2) + 0.2)
                return True
    return False


def fit_words(fig, renderer) -> bool:
    """Every text on the figure kept on it, keys moved off labels, rows opened apart. Returns whether anything
    changed."""
    changed = False
    for t in figure_texts(fig):
        if hasattr(t, "_aw_source"):                       # a text that fits itself as it draws (Nanowells)
            continue
        changed = fit_text_inside(t, fig, renderer) or changed
    changed = keep_rows_apart(fig, renderer) or changed
    changed = keep_keys_off_labels(fig, renderer) or changed
    return changed
