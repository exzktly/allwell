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

#: The inks the two non-values take. Both neutral, so neither joins the real
#: values; and far enough apart to be two inks rather than one, which is the
#: half this got wrong. ``plot_mono`` against ``text_muted`` measured **23**
#: apart in RGB — this file's own floor for six real values is 100, and the
#: workplan records 30 as "one colour to any reader" — while the comment here
#: claimed they were "deliberately different from each other". They now measure
#: 196, and ``test_the_two_non_values_are_as_far_apart_as_two_values`` holds
#: them to the same floor the values are held to, so the claim and the pixels
#: cannot part company again.
#:
#: Mid grey for *Mixed* and a pale one for *no value*, in that direction on
#: purpose: an unassigned well is the emptier of the two states and reads that
#: way, while a member whose wells disagree is a real member with a real bar.
MIXED_COLOR = theme.Colors.plot_mono
UNASSIGNED_COLOR = theme.Colors.text_primary


#: Which entries of the plate palette a variable's values take, in order.
#:
#: Not the palette's first n. Those are far apart to a trichromat — 116 in RGB
#: at six values — and collapse for a dichromat: **28** under protanopia and
#: **43** under deuteranopia at six values, and **10** under deuteranopia at
#: eight, against the 30 this module's own tests record as "one colour to any
#: reader". Roughly one man in twelve reads a figure that way, and *Colour by*
#: has no second channel carrying the same fact, so two values sharing an ink
#: is not a degraded reading of the figure — it is a wrong one, with nothing on
#: screen to say so.
#:
#: Chosen by measuring: greedily maximising the closest pair across all four
#: views at once — trichromat, protanope, deuteranope, tritanope — under the
#: Viénot–Brettel–Mollon simulation. Six values now measure 108 / 92 / 93 / 100
#: rather than 116 / 28 / 43 / 67: a little less separation for a trichromat,
#: and three times as much for everyone else. Two and three values come out
#: *better* on every view than the palette's own head did.
#:
#: ``tests/test_colour_and_split_by_variable.py`` re-measures both halves, so
#: the numbers above cannot drift away from the picks below.
VALUE_PICKS = (0, 49, 11, 47, 41, 22, 85, 8, 3, 89, 38, 34, 78, 57, 87, 59)


def value_colors(variable) -> dict:
    """``value id -> colour``, indexed by the value's place in the variable.

    The plate palette, not a new one: the app already colours by well rank out
    of ``WELL_COLORS``, and a second categorical vocabulary would mean two
    unrelated colour languages on one screen. Indexed by **position**, so the
    colour of a dose follows the order the user put its values in rather than
    the order a dict happened to iterate — which is what makes a legend read
    monotonically down a dose series instead of shuffling on every reload.

    Which entries, and why those, is :data:`VALUE_PICKS`.
    """
    values = tuple(getattr(variable, "values", ()) or ())
    order = _pick_order()
    return {value.id: order[i % len(order)]
            for i, value in enumerate(values)}


def _pick_order() -> list:
    """The measured picks first, then the rest of the palette in its own order.

    The tail matters for a variable with more values than there are picks: a
    list that stopped at the picks would start repeating inks at seventeen
    values, and a repeated ink is the failure the picks exist to prevent. So
    the sequence continues through every remaining entry — 96 distinct colours,
    with the ones a dichromat can separate at the front.
    """
    from well_viewer.domain.plate_layout import WELL_COLORS

    picked = [WELL_COLORS[i] for i in VALUE_PICKS if i < len(WELL_COLORS)]
    taken = set(VALUE_PICKS)
    return picked + [c for i, c in enumerate(WELL_COLORS) if i not in taken]


def value_label(variable, value_id) -> str:
    """What a value id is called on an axis, a header or in a legend.

    **With the unit.** ``Variable.display_label`` is the app's one answer to
    "what is this value called", and it is what the Variables tab's chips and
    ``variable_replicate_sets``' member names already use — so a figure
    *grouped* by Dose had x labels reading ``1 µM`` while a figure *coloured* by
    the same Dose had a legend of bare numbers, and a saved PNG of the second
    left nobody able to tell µM from nM. Two spellings of one value on figures
    a reader compares side by side.
    """
    if value_id is None:
        return UNASSIGNED_LABEL
    if value_id is MIXED or value_id == MIXED:
        return MIXED_LABEL
    if variable is None:
        return UNASSIGNED_LABEL
    label = variable.display_label(value_id)
    return label or UNASSIGNED_LABEL


def _inks_for(variable, values_by_key) -> dict:
    """``member key -> colour``, from values already resolved.

    The ink rule with the ctx and the scope lifted out, so the rule itself can
    be read and tested without either. Private: the plotting tabs and Batch
    Export share one definition of what a value looks like — a batch figure
    whose colours differed from the tab's would be two answers to "what colour
    is HeLa" on one screen — but they share it through :func:`member_inks`,
    which is where the members are resolved. A caller reaching this one would
    be bringing its own resolution, which is the disagreement, not the sharing.
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


def _bands_for(variable, values_by_key) -> dict:
    """``member key -> band label``, from values already resolved.

    Private for the reason :func:`_inks_for` records: :func:`member_bands` is
    the shared answer, because it resolves the members too.
    """
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
    return _inks_for(_variable(ctx, variable_id),
                     _values(ctx, scope, variable_id, wells_by_key))


def member_bands(ctx, scope: str, variable_id: str, wells_by_key=None) -> dict:
    """``member key -> band label``, or ``{}`` when no variable is chosen.

    The label, not the id: it is drawn as a cluster header, and a reader needs
    the dose rather than ``d3``. The two non-values get their names here for the
    same reason they get their own inks — a band headed with nothing looks like
    a header that failed to draw.
    """
    return _bands_for(_variable(ctx, variable_id),
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
