"""Colour by / Split by, applied once over a finished ``BarItem`` list.

Neither control changes what a bar *is*. ``group_by_variable`` alone decides
that, upstream in ``plot_members``; these two only decide what ink a bar takes
and which labelled band it sits in, so every number in the figure is the same
number it was before any of them were set.

**One pass over the finished list, not a change at each construction site.**
``collect_bar_items`` builds items in four branches — pooled sets, expanded
wells, plain wells, and the placeholder rows for a missing measurement — and
editing four of them is how a sweep ends up half done: the branch nobody
remembered keeps its old colour, and a figure in which some bars honour the
control and some do not is worse than one where none does, because the ones
that do make the rest look deliberate. Applied here, after the list is
complete, it cannot miss a branch.
"""

from __future__ import annotations

from review.domain.variable_series import (
    MIXED_LABEL, UNASSIGNED_LABEL, band_order, legend_entries, member_bands,
    member_inks,
)

#: Bands that are not part of the series sort after every real value, in this
#: order. A `no value` band wedged between 1 µM and 3 µM would read as a dose.
_TRAILING_BANDS = (MIXED_LABEL, UNASSIGNED_LABEL)


def decorate_bar_items(ctx, items, *, wells_by_key=None):
    """``(items, notice)`` — re-inked, re-banded, and what that cost.

    *wells_by_key* is the caller's own member key space, or ``None`` for the
    plotting tabs' (``member_wells(ctx, "bar")``). Batch Export passes a
    ``BarGroup``'s members, whose keys are the group's and not the tab's — the
    same override its fold-change path already makes. Without it the batch
    figures would be matched against keys that are not theirs, colouring some
    bars and not others, which is worse than colouring none.

    *notice* is ``""`` or the sentence the figure has to carry. Splitting by a
    variable **replaces** the band a bar already had, and the bar tab already
    uses bands for two things: one per well in multi-timepoint "Group by well"
    mode, one per timepoint in the other. Overwriting either silently would
    take the headers off a figure the user had arranged that way, with nothing
    saying where they went.
    """
    colour_id = _setting(ctx, "colour_by_variable")
    split_id = _setting(ctx, "split_by_variable")
    if not colour_id and not split_id:
        _record(ctx, "")
        return items, ""

    if colour_id:
        inks = member_inks(ctx, "bar", colour_id, wells_by_key)
        for item in items:
            # A key the map does not cover keeps the colour it was built with.
            # Re-inking it to the neutral would say "this member has no value",
            # which is a claim about the plate; not being in the map means it is
            # not a drawn member of this scope at all, which is a claim about
            # the key space and not something to paint.
            if item.key in inks:
                item.color = inks[item.key]

    notice = ""
    if split_id:
        bands = member_bands(ctx, "bar", split_id, wells_by_key)
        if bands:
            notice = _displacement_notice(items, bands)
            for item in items:
                if item.key in bands:
                    item.group_key = bands[item.key]
            items = _sorted_into_bands(ctx, items, split_id)
    _record(ctx, notice)
    return items, notice


def _record(ctx, notice: str) -> None:
    """Park the notice where the renderer can find it.

    Cleared on every pass, including the passes with nothing to say — a notice
    left behind from the previous layout is the stale-sentence failure this
    whole arrangement exists to avoid, moved from the status bar onto the
    figure where it is harder to notice.
    """
    reader = getattr(getattr(ctx, "state", None), "feature_slice", None)
    slice_ = reader("bar") if callable(reader) else None
    if slice_ is not None:
        slice_.split_notice = notice


def bar_legend_entries(ctx, items, *, wells_by_key=None):
    """``(label, colour)`` for the Colour-by legend, or ``[]``.

    Empty when nothing is coloured by a variable — the plate-colour mode needs
    no legend, because the plate *is* the legend, which is the reason that mode
    is the default.
    """
    colour_id = _setting(ctx, "colour_by_variable")
    if not colour_id:
        return []
    labels = member_bands(ctx, "bar", colour_id, wells_by_key)
    return legend_entries(ctx, colour_id,
                          {labels[item.key] for item in items
                           if item.key in labels})


def _setting(ctx, field: str) -> str:
    """One decoration setting, or ``""`` when there is no state to read it from.

    ``render_bar_items`` is called with ``ctx=None`` on purpose — the Batch
    Export panel renders these panels without one — and a caller with no ctx
    has no plot state, so there is no setting. That is a genuine absence rather
    than a computation that degraded, which is why it reads as "not chosen"
    rather than being swallowed: the figure it produces is the figure the app
    drew before these controls existed, whole and coherent, not a decorated one
    with pieces missing.
    """
    state = getattr(ctx, "state", None) if ctx is not None else None
    return str(getattr(getattr(state, "plot", None), field, "") or "")


def _displacement_notice(items, bands) -> str:
    """What splitting by a variable took away, or ``""``.

    Only when a band was actually there to lose. A single-timepoint figure has
    no bands of its own, so splitting one costs nothing and saying so anyway
    would train the reader to skip the line.
    """
    displaced = {item.group_key for item in items
                 if item.group_key and item.key in bands
                 and item.group_key != bands[item.key]}
    if not displaced:
        return ""
    return (f"Split by replaced the existing {len(displaced)} band(s) "
            f"({', '.join(sorted(displaced)[:3])}"
            f"{'…' if len(displaced) > 3 else ''}) — one layout at a time.")


def _sorted_into_bands(ctx, items, split_id: str):
    """The items re-ordered so each band is contiguous, in the user's order.

    Contiguous because ``clustered_x_positions`` gives adjacent positions to
    adjacent items sharing a band, and ``cluster_centers`` centres a header
    over the positions it saw: a band split across the axis draws its header in
    the gap between its halves, over bars belonging to something else.

    **Stable within a band**, so the drag order the user set is preserved
    inside each one. Sorting on the band alone would be a second ordering rule
    silently overriding theirs.
    """
    order = {label: i for i, label in enumerate(band_order(ctx, split_id))}
    fallback = len(order) + 1
    return sorted(items, key=lambda item: order.get(item.group_key, fallback))
