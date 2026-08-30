"""Turning a sample variable into a figure's colours and its clusters.

Two band controls ask the same question of every drawn member — *Colour by*
asks "what ink", *Split by* asks "which band" — and both answers come from one
fact: which value of the chosen variable that member holds. This module is that
fact turned into the two answers, with no Qt and no ctx beyond the one call it
delegates, so both are driven by a list in the fast lane.

**The two ways a member has no value stay apart, all the way to the ink.**
``roll_up`` distinguishes a member whose wells disagree from one holding a well
that was never filled, and collapsing them here would undo the reason it does:
"you filled these inconsistently" is a mistake the user can go and fix, and
"these were never filled" is a different one with a different fix. Each gets its
own neutral ink and its own name, so a reader can tell them from each other and
from a real value — none of the three is the palette's first colour by accident.
"""

from __future__ import annotations

import theme
from well_viewer.domain.sample_variables import MIXED

#: What a member whose wells disagree is called, wherever it is named. The same
#: string ``sample_variables`` already uses on the plate, so the figure and the
#: Variables tab say it identically.
MIXED_LABEL = MIXED

#: What a member holding an unfilled well is called. Not "unknown" and not
#: blank: an empty cluster header or an empty legend entry reads as a rendering
#: fault, and this is an ordinary state of a plate someone is halfway through.
UNASSIGNED_LABEL = "no value"

#: The inks the two non-values take. Deliberately both neutral and deliberately
#: different from each other: one grey for both would say the two states are the
#: same, and a palette colour for either would put them among the real values.
MIXED_COLOR = theme.Colors.plot_mono
UNASSIGNED_COLOR = theme.Colors.text_muted


def value_colors(variable) -> dict:
    """``value id -> colour``, indexed by the value's place in the variable.

    The plate palette, not a new one: the app already colours by well rank out
    of ``WELL_COLORS``, and a second categorical vocabulary would mean two
    unrelated colour languages on one screen. Indexed by **position**, so the
    colour of a dose follows the order the user put its values in rather than
    the order a dict happened to iterate — which is what makes a legend read
    monotonically down a dose series instead of shuffling on every reload.
    """
    from well_viewer.domain.plate_layout import WELL_COLORS

    values = tuple(getattr(variable, "values", ()) or ())
    # The palette's first n entries, in order. It is already arranged so that
    # consecutive entries are far apart — measured, because the first version
    # of this assumed the opposite and spread the picks across all 96 to avoid
    # "adjacent hues". That made it worse: at three values the spread picks
    # come out 30 apart in RGB (``#E56AFB`` beside ``#DC81EA``, which is one
    # colour to any reader) against 199 for the first three, and at six values
    # 30 against 116. ``test_the_palette_entries_are_far_enough_apart`` pins
    # the measurement so the reasoning cannot drift back.
    return {value.id: WELL_COLORS[i % len(WELL_COLORS)]
            for i, value in enumerate(values)}


def value_label(variable, value_id) -> str:
    """What a value id is called on an axis, a header or in a legend."""
    if value_id is None:
        return UNASSIGNED_LABEL
    if value_id is MIXED or value_id == MIXED:
        return MIXED_LABEL
    value = variable.value_by_id(value_id) if variable is not None else None
    return value.label if value is not None else UNASSIGNED_LABEL


def inks_for(variable, values_by_key) -> dict:
    """``member key -> colour``, from values already resolved.

    The ink rule with no ctx and no scope in it, so the plotting tabs and Batch
    Export share one definition of what a value looks like while each resolves
    its own members. A batch figure whose colours differed from the tab's would
    be two answers to "what colour is HeLa" on one screen.
    """
    if variable is None:
        return {}
    palette = value_colors(variable)
    return {
        key: (UNASSIGNED_COLOR if value_id is None
              else MIXED_COLOR if value_id == MIXED
              else palette.get(value_id, UNASSIGNED_COLOR))
        for key, value_id in (values_by_key or {}).items()
    }


def bands_for(variable, values_by_key) -> dict:
    """``member key -> band label``, from values already resolved."""
    if variable is None:
        return {}
    return {key: value_label(variable, value_id)
            for key, value_id in (values_by_key or {}).items()}


def member_inks(ctx, scope: str, variable_id: str, wells_by_key=None) -> dict:
    """``member key -> colour``, or ``{}`` when no variable is chosen.

    ``{}`` rather than a dict of defaults, so a caller can tell "colour by this
    variable" from "colour the way you always did" without comparing every
    entry against the colour it would otherwise have had.

    *wells_by_key* overrides the key space. Batch Export passes a ``BarGroup``'s
    own members, whose keys are the group's and not the tab's — the same
    override its fold-change path already makes, and for the same reason.
    """
    return inks_for(_variable(ctx, variable_id),
                    _values(ctx, scope, variable_id, wells_by_key))


def member_bands(ctx, scope: str, variable_id: str, wells_by_key=None) -> dict:
    """``member key -> band label``, or ``{}`` when no variable is chosen.

    The label, not the id: it is drawn as a cluster header, and a reader needs
    the dose rather than ``d3``. The two non-values get their names here for the
    same reason they get their own inks — a band headed with nothing looks like
    a header that failed to draw.
    """
    return bands_for(_variable(ctx, variable_id),
                     _values(ctx, scope, variable_id, wells_by_key))


def _values(ctx, scope: str, variable_id: str, wells_by_key) -> dict:
    """The resolved values, over the caller's key space or the scope's."""
    from well_viewer.domain.plot_members import (
        member_variable_values, variable_values_for,
    )

    if wells_by_key is None:
        return member_variable_values(ctx, scope, variable_id)
    return variable_values_for(
        getattr(getattr(ctx, "state", None), "selection", None)
        and ctx.state.selection.variables, variable_id, wells_by_key)


def band_order(ctx, variable_id: str) -> list:
    """Every band label the variable can produce, in the user's own order.

    Used to lay the clusters out. The two non-values sort last, because they are
    not part of the series: a `no value` band wedged between 1 µM and 3 µM
    would read as a dose.
    """
    variable = _variable(ctx, variable_id)
    if variable is None:
        return []
    return [value.label for value in (variable.values or ())] + [
        MIXED_LABEL, UNASSIGNED_LABEL]


def legend_entries(ctx, variable_id: str, used_labels) -> list:
    """``(label, colour)`` for the bands actually drawn, in the user's order.

    Only the ones drawn: a legend listing six doses over a figure showing two
    is a claim about what is on screen, and the reader counts the legend.
    """
    variable = _variable(ctx, variable_id)
    if variable is None:
        return []
    used = set(used_labels or ())
    palette = value_colors(variable)
    entries = [(value.label, palette[value.id])
               for value in (variable.values or ()) if value.label in used]
    if MIXED_LABEL in used:
        entries.append((MIXED_LABEL, MIXED_COLOR))
    if UNASSIGNED_LABEL in used:
        entries.append((UNASSIGNED_LABEL, UNASSIGNED_COLOR))
    return entries


def _variable(ctx, variable_id: str) -> object | None:
    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or not variable_id:
        return None
    return vset.by_id(variable_id)
