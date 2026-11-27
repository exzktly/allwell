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
_MAX_ROUNDS = 5


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
    moved = False
    short = -min(lefts, default=0.0)
    if short > 0.5 and fig.bbox.width > 0:
        left = fig.subplotpars.left + (short + 4.0) / fig.bbox.width
        if left < fig.subplotpars.right - 0.2:
            fig.subplots_adjust(left=left)
            moved = True
    # The same at the foot: an x label (or its tick labels) under the figure's lower edge — a desk card's figure
    # short beside the open inspector cut "GFP mean intensity" off under the plot.
    bottoms = [ax.xaxis.label.get_window_extent(renderer).y0 for ax in placed
               if ax.xaxis.label.get_visible() and ax.xaxis.label.get_text().strip()]
    bottoms += [t.get_window_extent(renderer).y0 for ax in placed for t in ax.get_xticklabels()
                if t.get_visible() and t.get_text().strip()]
    low = -min(bottoms, default=0.0)
    # A note written along the figure's foot (the Observations line) is ground the labels may not take either.
    feet = [t.get_window_extent(renderer) for t in fig.texts
            if t.get_visible() and t.get_text().strip() and t.get_window_extent(renderer).y1 < fig.bbox.height * 0.2]
    if feet and bottoms:
        low = max(low, max(f.y1 for f in feet) - min(bottoms))
    if low > 0.5 and fig.bbox.height > 0:
        bottom = fig.subplotpars.bottom + (low + 4.0) / fig.bbox.height
        if bottom < fig.subplotpars.top - 0.2:
            fig.subplots_adjust(bottom=bottom)
            moved = True
    return moved


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
    """Move a key that covers a label to the place in :data:`_LEGEND_PLACES` covering fewest, staying on the figure;
    a key anchored outside its axes (``bbox_to_anchor``) keeps its anchor and is placed around it."""
    changed = False
    texts = figure_texts(fig)
    for ax in fig.axes:
        legend = ax.get_legend()
        if legend is None or not legend.get_visible():
            continue
        own = {id(x) for x in legend.get_texts()} | {id(legend.get_title())}
        others = [t.get_window_extent(renderer) for t in texts if id(t) not in own]

        def cost() -> int:
            box = legend.get_window_extent(renderer)
            return sum(_overlaps(box, o) for o in others) + (10 if _off(box, fig) else 0)

        original = legend._loc
        best_loc, best = original, cost()
        if best == 0:
            continue
        # A key anchored outside its axes (beside or under the plot) keeps its anchor and is tried at each place
        # around it: on a phone the key under the plot sat on the x label.
        # The place covering fewest labels, and the one it had when none is better: a key moved to the last place
        # tried, clear or not, covered more on Line Graphs than where its renderer put it.
        for place in _LEGEND_PLACES:
            legend.set_loc(place)
            now = cost()
            if now < best:
                best_loc, best = legend._loc, now
            if now == 0:
                break
        legend._loc = best_loc
        changed = changed or best_loc != original
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


def keep_values_apart(fig, renderer) -> bool:
    """Make the values written in one panel's cells smaller together, down to :data:`_SMALLEST` of their size, while
    they run into each other — a correlation matrix's numbers in a phone's small cells. Each keeps the size its
    renderer gave it as the size it is fitted from."""
    changed = False
    for ax in fig.axes:
        texts = [t for t in ax.texts if (t.get_visible() or getattr(t, "_aw_crowd_hidden", False))
                 and t.get_text().strip() and not hasattr(t, "_aw_source") and t.get_gid() != HIDDEN_GID]
        if len(texts) < 4:
            continue
        for t in texts:
            t.set_visible(True)
        for t in texts:
            if getattr(t, "_aw_crowd_out", None) != t.get_fontsize():
                t._aw_crowd_src = t.get_fontsize()
            t.set_fontsize(t._aw_crowd_src)
        apart = False
        for share in (1.0, 0.85, 0.7, _SMALLEST):
            for t in texts:
                t.set_fontsize(t._aw_crowd_src * share)
            boxes = [t.get_window_extent(renderer) for t in texts]
            if not any(_overlaps(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:]):
                apart = True
                break
        # Cells too small for their values at any size that can be read: the values are hidden and the panel says
        # so, never left as a blot nor dropped without a word; they come back when the panel is large enough.
        changed = _say_hidden(ax, texts, not apart) or changed
        for t in texts:
            changed = changed or getattr(t, "_aw_crowd_out", t._aw_crowd_src) != t.get_fontsize()
            t._aw_crowd_out = t.get_fontsize()
    return changed


#: The artist id of a panel's note that its values are hidden.
HIDDEN_GID = "fit-values-hidden"


def _say_hidden(ax, texts, hide: bool) -> bool:
    """Hide *texts* and write the panel's note, or show them and take the note away. Whether anything changed."""
    was = any(getattr(t, "_aw_crowd_hidden", False) for t in texts)
    for t in texts:
        t.set_visible(not hide)
        t._aw_crowd_hidden = hide
    notes = [t for t in ax.texts if t.get_gid() == HIDDEN_GID]
    if hide and not notes:
        ax.text(0.0, -0.02, "Values not written: the cells are too small at this size.", transform=ax.transAxes,
                ha="left", va="top", fontsize=7, gid=HIDDEN_GID, clip_on=False, color=texts[0].get_color())
    elif not hide:
        for note in notes:
            note.remove()
    return was != hide


def fit_label_to_axis(ax, renderer) -> bool:
    """An axis label no longer than its axis: a y label taller than its panel ran past the figure's top or into the
    x label under it (Line Graphs, Scatter). Made smaller, down to :data:`_SMALLEST`, then broken at spaces to the
    axis' length; fitted from the label as its renderer wrote it. Returns whether either label changed."""
    changed = False
    box = ax.get_window_extent(renderer)
    for label, along in ((ax.xaxis.label, box.width), (ax.yaxis.label, box.height)):
        if not label.get_visible() or not label.get_text().strip() or along <= 4:
            continue
        now = (label.get_text(), label.get_fontsize())
        if getattr(label, "_aw_axis_out", None) != now:
            label._aw_axis_src = now
        text, size = label._aw_axis_src
        label.set_text(text)
        label.set_fontsize(size)

        def length() -> float:
            e = label.get_window_extent(renderer)
            return e.height if label is ax.yaxis.label else e.width

        if length() > along or _off(label.get_window_extent(renderer), ax.figure):
            for share in (0.9, 0.8, 0.7, _SMALLEST):
                label.set_fontsize(size * share)
                if length() <= along and not _off(label.get_window_extent(renderer), ax.figure):
                    break
            else:
                prop = label.get_fontproperties()
                label.set_text(_greedy(text, lambda t: renderer.get_text_width_height_descent(t, prop, ismath=False)[0],
                                       along))
        label._aw_axis_out = (label.get_text(), label.get_fontsize())
        changed = changed or label._aw_axis_out != now
    return changed


def fit_words(fig, renderer) -> bool:
    """Every text on the figure kept on it, keys moved off labels, rows opened apart. Returns whether anything
    changed."""
    changed = False
    # A panel's cell values are fitted together by ``keep_values_apart``, not one at a time.
    values = {id(t) for ax in fig.axes if len(ax.texts) >= 4 for t in ax.texts if t.get_gid() != HIDDEN_GID}
    # Axis labels are fitted to their axis by ``fit_label_to_axis`` (and kept on the figure by the margins): two
    # passes each fitting the same label would each take the other's result for the renderer's.
    values |= {id(lb) for ax in fig.axes for lb in (ax.xaxis.label, ax.yaxis.label)}
    for t in figure_texts(fig):
        if hasattr(t, "_aw_source") or id(t) in values:    # fits itself as it draws (Nanowells), or a cell value
            continue
        changed = fit_text_inside(t, fig, renderer) or changed
    for ax in fig.axes:
        changed = fit_label_to_axis(ax, renderer) or changed
    changed = keep_values_apart(fig, renderer) or changed
    changed = keep_rows_apart(fig, renderer) or changed
    changed = keep_keys_off_labels(fig, renderer) or changed
    return changed
