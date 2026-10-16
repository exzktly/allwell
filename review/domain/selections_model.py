"""What a saved selection means once it reaches a figure — Review's half.

The other half is :mod:`domain.selections_format`, which owns the on-disk
``sample_definitions`` block: reading it, writing it, repairing it, migrating a
legacy v1 one, and baking the ``color`` field from well rank. That half is
shared, because Review writes that block and Datasets rewrites it on a plate
merge, so two panes have to agree about the format. (The pipeline only carries
an existing block across untouched — ``pipeline.sidecar`` lists it among the
blocks that have no author on the pipeline side.)

This module is what only Review needs, and no other pane touches:

* the colour a selection or replicate set paints with on a plate
  (:func:`color_for_well`, :func:`color_for_wells`, :func:`color_for_selection`,
  :func:`color_for_replicate_set`),
* the name a figure draws for a well and where that name came from
  (:func:`figure_names_map`, :func:`well_display_label_and_source`,
  :func:`well_display_label`, :func:`replicate_display_label`,
  :func:`groups_relabelled_on_figures`, :func:`relabelled_groups_clause`),
* which selections the replicate machinery currently treats as sets
  (:func:`active_replicate_sets`, :func:`replicate_sets_loaded`),
* and the two block-shape questions a Review view asks about a loaded plate
  (:func:`group_drift`, :func:`block_is_mixed`).

It borrows exactly two helpers from the format half — :func:`well_rank`, which
is the ordering every colour here is keyed on, and ``_clean_wells``, the token
normaliser — rather than keeping a second spelling of either.

Qt-free, like the rest of ``review/domain/``.
"""

from __future__ import annotations

import re
from typing import Any
from collections.abc import Iterable

from domain.selections_format import _clean_wells, well_rank
from ui import theme

_DEFAULT_COLOR = theme.Colors.selection_default


#: Where the name a figure draws for a well came from. Returned alongside the
#: text by :func:`well_display_label_and_source`, because two callers need to
#: know *which* of the three answers they were given and cannot tell from the
#: string: the Bar tab's numeric-tick guard is about hand-typed labels only,
#: and Home reports what the figures are naming.
LABEL_FROM_CUSTOM = "custom"
LABEL_FROM_VARIABLES = "variables"
LABEL_FROM_TOKEN = "token"


#: How many substitutions the clause names before it counts the rest. Three
#: because the sentence sits under a list the user is already reading; naming
#: every one of twelve would restate the list rather than explain it.
_RELABELLED_SHOWN = 3


def color_for_wells(wells: Any) -> str:
    """Canonical group colour: the rank colour of the *lowest* well in *wells*.

    So every well in a group (and its line/bar trace) shares one colour. The
    pure implementation shared by ``core`` and feature code (A8 teardown), and the
    only one — the feature-side wrapper it was lifted from is gone, so neither
    layer reaches through the god-object for a colour. Indexes the raw
    ``WELL_COLORS`` palette directly to preserve the exact historical colours.
    """
    from review.domain.plate_layout import WELL_COLORS
    ranks = [well_rank(w) for w in (wells or [])]
    ranks = [r for r in ranks if r < (1 << 30)]
    return WELL_COLORS[(min(ranks) if ranks else 0) % len(WELL_COLORS)]


def color_for_well(token: Any) -> str:
    """Rank colour for a single well token — the pure core of the
    ``ReviewApp._rank_color_well`` god-method (A8 teardown)."""
    from review.domain.plate_layout import WELL_COLORS
    return WELL_COLORS[well_rank(token) % len(WELL_COLORS)]


def color_for_selection(s: Any) -> str:
    """Effective colour for a selection dict: explicit ``color`` override first,
    otherwise the rank colour of its wells. The pure core of ``sel_color``,
    lifted here (A8 teardown) so ``core`` and the data layer share one home
    without importing a feature."""
    if isinstance(s, dict):
        c = s.get("color")
        if isinstance(c, str) and c.strip():
            return c.strip()
        return color_for_wells(s.get("wells"))
    return color_for_wells(getattr(s, "wells", None))


def color_for_replicate_set(rset: Any, selections: Iterable[Any]) -> str:
    """Colour for a replicate set / bar group: honours the ``color`` of a
    selection sharing its ``name``, else the rank colour of its wells. The A8
    teardown left this the single implementation; the ctx-shaped adapter that
    used to wrap it is gone and callers reach it here directly."""
    name = getattr(rset, "name", None)
    if name:
        for s in selections:
            if isinstance(s, dict) and s.get("name") == name:
                return color_for_selection(s)
    return color_for_wells(getattr(rset, "wells", None))


def figure_names_map(ctx) -> dict:
    """``token -> what a figure calls this well``, for every well that is called
    something other than its own token.

    The map form of :func:`well_display_label_and_source`, for the callers
    that name a whole plate at once rather than one well at a time: every
    ``well_name`` column in every exported CSV, and the Spatial tab's summary
    axis. Same two rungs in the same order — a name typed on the **Well
    Labels** tab, and, for a loaded well that has none, the values it holds on
    the **Variables** tab, composed as ``HeLa · 10 µM`` — so the rows behind a
    figure and the figure itself cannot disagree about what a well is.

    Typed labels for wells the dataset no longer holds are kept. They are what
    ``well_name_for``'s ``strict`` mode consults to tell a well token it does
    not recognise from a group name, and dropping them would blank the column
    for a plate re-run over fewer wells. Composed names are only ever added for
    loaded wells: there is no figure to name an absent one on.
    """
    from review.domain.sample_variables import label_for_well

    out = dict(ctx.state.dataset.well_labels or {})
    vset = getattr(getattr(ctx.state, "selection", None), "variables", None)
    if vset is None:
        return out
    for well in (ctx.state.dataset.well_paths or {}):
        if str(out.get(well, "") or "").strip():
            continue
        composed = label_for_well(vset, well)
        if composed:
            out[well] = composed
    return out


def well_display_label_and_source(ctx, lbl: str) -> tuple[str, str]:
    """``(what a figure calls this well, which of the three said so)``.

    The three, in the order they win:

    * a label typed on the **Well Labels** tab — the user's explicit override,
      and the only one of the three they wrote for this well alone;
    * the **sample variables** the well holds, composed by
      :func:`sample_variables.label_for_well` — ``HeLa · 10 µM``. A plate
      annotated on the Variables tab and nowhere else drew its bare well
      tokens on every tick and in every legend before this rung existed: the
      annotation was read for pooling, for colour and for splitting, and never
      for the one thing the user could see;
    * the well token itself, for a well nothing describes.

    A blank custom label counts as no label rather than as an empty name. The
    persisted map cannot hold one (``_clean_labels`` drops it), so this is
    about a label being cleared in the session that typed it — and a tick with
    no text at all is not what clearing a label asks for.
    """
    from review.domain.sample_variables import label_for_well
    from review.domain.viewer_state import extract_well_token
    tok = extract_well_token(lbl) or lbl
    custom = str((getattr(ctx.state.dataset, "well_labels", None) or {})
                 .get(tok, "") or "").strip()
    if custom:
        return custom, LABEL_FROM_CUSTOM
    vset = getattr(getattr(ctx.state, "selection", None), "variables", None)
    if vset is not None:
        composed = label_for_well(vset, tok)
        if composed:
            return composed, LABEL_FROM_VARIABLES
    return tok, LABEL_FROM_TOKEN


def well_display_label(ctx, lbl: str) -> str:
    """What a figure calls this well — see :func:`well_display_label_and_source`.

    A8 teardown: the pure body of ``ReviewApp._well_display_label``, lifted to
    this low layer so ``core`` / feature renderers share it without a feature
    import (extract_well_token is itself domain)."""
    return well_display_label_and_source(ctx, lbl)[0]


def replicate_display_label(ctx, rset: Any) -> str:
    """Human-friendly x-axis label for a replicate set: the group name, or —
    when that name looks auto-generated (``"1"`` / ``"Replicate 2"`` / ``"R3"``) —
    its member well labels so the axis stays interpretable. A8 teardown: pure
    ``ctx.state`` + ``well_display_label``, with no feature-side wrapper."""
    if getattr(rset, "solo", False) and getattr(rset, "wells", None):
        # A solo well stands for itself, so it is named the way the per-well
        # plots name it — its Well Labels name or its variable values — rather
        # than by its bare token.
        return well_display_label(ctx, rset.wells[0])
    name = str(getattr(rset, "name", "") or "").strip()
    if not name:
        name = "Replicate"
    if getattr(rset, "name_is_verbatim", False):
        # A label the user typed. The heuristic below is for group names the
        # app generated, and a sample-variable value that happens to read "10"
        # is not one of those — substituting its wells put ``A01,A03`` on a dose
        # axis. §7.1: a domain predicate applied outside its domain.
        return name
    generic_name = bool(re.match(
        r"^(?:\d+|r\s*\d+|rep(?:licate)?\s*\d+|group\s*\d+|set\s*\d+|batch\s*\d+)$",
        name, re.I))
    if not generic_name:
        return name
    wells = [well_display_label(ctx, w) for w in getattr(rset, "wells", [])
             if w in ctx.state.dataset.well_paths]
    if not wells:
        return name
    if len(wells) <= 3:
        return ",".join(wells)
    return f"{','.join(wells[:3])} +{len(wells)-3}"


def groups_relabelled_on_figures(ctx) -> list[tuple[str, str]]:
    """``(the group's name, what a figure calls it)`` for every substitution.

    :func:`replicate_display_label` silently replaces a group's name with its
    member well labels whenever the name matches the auto-generated pattern
    (``1``, ``Rep 2``, ``Group 3``). That is the right default — an axis reading
    ``1, 2, 3`` says nothing — but it means whatever the wells are *called*
    becomes the group's identity on every figure, decided by a rule the user
    never saw and cannot find. That name is a label typed on the **Well
    Labels** tab, or, failing one, the values the well holds on the
    **Variables** tab, so all three ways of annotating a well are wired
    together in exactly this one place, and nothing said so.

    Visible groups only: a hidden one is not drawn, so nothing relabels it.
    **And none of them when a sample variable is pooling the plots**, for the
    same reason one step out: the groups are not drawn at all then, so a note
    saying figures label them with their wells describes a figure that is not on
    screen — and it appeared on the same tab as, and directly contradicted, the
    line saying those groups were not pooling. The dependency list this rule
    was first written against — a group's name, its membership, and the labels
    on another tab — was missing the fourth: whether the group is drawn.

    Returns the pairs rather than a sentence so the count, the wording and the
    order are the caller's, and so the rule is drivable without a widget.
    """
    # Deferred: ``plot_members`` imports this module at module scope, so the
    # seam has to be reached from inside the function. It is the one answer to
    # "is a variable pooling", and re-deriving it here would be a second one.
    from review.domain.plot_members import variable_grouping_in_force

    if variable_grouping_in_force(ctx):
        return []
    out: list[tuple[str, str]] = []
    for rset in active_replicate_sets(
            ctx.state.selection.selections or [],
            ctx.state.dataset.well_paths or {}):
        drawn = replicate_display_label(ctx, rset)
        if drawn != rset.name:
            out.append((rset.name, drawn))
    return out


def relabelled_groups_clause(ctx) -> str:
    """The sentence naming those substitutions, or ``""`` when there are none.

    Empty rather than "no groups are relabelled": a reader who has not hit the
    rule does not need to be taught it, and a line that is always present is a
    line nobody reads on the day it matters.
    """
    pairs = groups_relabelled_on_figures(ctx)
    if not pairs:
        return ""
    shown = ", ".join(f"\u201c{name}\u201d \u2192 \u201c{drawn}\u201d"
                      for name, drawn in pairs[:_RELABELLED_SHOWN])
    rest = len(pairs) - _RELABELLED_SHOWN
    if rest > 0:
        shown += f", and {rest} more"
    subject = ("1 group has a name that looks" if len(pairs) == 1
               else f"{len(pairs)} groups have names that look")
    return (f"{subject} auto-generated, so figures label them with what their "
            f"wells are called instead: {shown}. Rename a group to choose its "
            f"own label.")


def active_replicate_sets(selections: Iterable[Any], well_paths: Any):
    """One ``ReplicateSet`` per visible (non-hidden) selection with ≥1 loaded
    well, in order — the traces drawn on the plots. Pure core of
    ``barplot.controller.rep_sets_active`` (A8 teardown), so the data layer can
    enumerate groups without importing a feature.

    Carries each selection's ``name_is_verbatim`` onto the set it builds. That
    flag is the only thing standing between a group named from a sample variable
    and :func:`replicate_display_label`'s auto-generated-name heuristic, and a
    flag dropped in the one function every plot reaches the groups through is a
    flag nothing reads."""
    from review.domain.batch_models import ReplicateSet
    out = []
    for s in selections:
        if s.get("hidden"):
            continue
        wells = s.get("wells") or []
        if any(w in well_paths for w in wells):
            out.append(ReplicateSet(s.get("name") or "", list(wells),
                                    name_is_verbatim=bool(s.get("name_is_verbatim"))))
    return out


def replicate_sets_loaded(selections: Iterable[Any], well_paths: Any):
    """One ``ReplicateSet`` per selection with ≥1 loaded well, **including
    hidden ones**, in order — the full pool the Heat Map's rep-set-average
    expansion walks (it colours a cell by the rep-set its well belongs to
    regardless of trace visibility). Pure core of the legacy
    ``ReviewApp._rep_sets_loaded`` (A8 R-leaf), so the heatmap reads it
    without a feature import. Carries ``name_is_verbatim`` for the reason
    :func:`active_replicate_sets` does."""
    from review.domain.batch_models import ReplicateSet
    out = []
    for s in selections:
        wells = s.get("wells") or []
        if any(w in well_paths for w in wells):
            out.append(ReplicateSet(s.get("name") or "", list(wells),
                                    name_is_verbatim=bool(s.get("name_is_verbatim"))))
    return out


def group_drift(saved: Any, live: Any) -> tuple[list[str], list[str]]:
    """``(groups the record had that are gone, groups whose wells have changed)``.

    What a saved plot's own groups no longer say about this dataset. A saved
    plot records the replicate sets its figure was drawn from, and those are
    **declarations** — the user built their names, their membership and their
    order by hand on Home → Sample Map → Replicates. Restoring the record used
    to overwrite them with the plot's copy and autosave the result, so opening
    a figure from last week deleted this week's groups off disk. Reproducing a
    *view* must not rewrite the *data* it was drawn from, so the restore now
    reports the difference instead of imposing it, and this is the comparison
    behind that sentence.

    Matched by **name**, not by id. Ids are re-minted by :func:`validate_repair`
    whenever a list is loaded or repaired, so a group that has survived
    untouched since the plot was saved routinely carries a different id — an
    id-wise comparison would report every group on the plate as missing and the
    sentence would be noise on every restore. The name is what the user typed,
    what the axis draws and what a saved fold-change ``ControlRef`` is keyed on,
    which makes it the identity everywhere else too.

    Membership is compared as an **ordered** list, because the order is the
    plot order: two groups holding the same wells in a different sequence draw
    a different figure, and telling the user their groups match would be wrong
    in exactly the way that matters to the picture in front of them.

    Both lists come back in the record's own order, and both empty when nothing
    has drifted — which is the common case and the one that must say nothing.
    """
    live_wells: dict[str, list[str]] = {}
    for sel in (live or []):
        if isinstance(sel, dict):
            name = str(sel.get("name") or "")
            if name and name not in live_wells:
                live_wells[name] = _clean_wells(sel.get("wells"))
    missing: list[str] = []
    changed: list[str] = []
    for sel in (saved or []):
        if not isinstance(sel, dict):
            continue
        name = str(sel.get("name") or "")
        if not name:
            continue
        if name not in live_wells:
            missing.append(name)
        elif live_wells[name] != _clean_wells(sel.get("wells")):
            changed.append(name)
    return missing, changed


def block_is_mixed(block: Any) -> bool:
    """``True`` when *block* carries both v1 and v2 keys with content.

    Lets callers distinguish "use v1 path" from "refuse to migrate"
    after ``block_is_v2`` returns ``False``.
    """
    if not isinstance(block, dict):
        return False
    has_legacy = bool(block.get("rep_sets")) or bool(block.get("groups"))
    try:
        is_v2_marker = int(block.get("schema_version", 1)) >= 2
    except (TypeError, ValueError):
        is_v2_marker = False
    if not is_v2_marker:
        is_v2_marker = isinstance(block.get("selections"), list)
    return is_v2_marker and has_legacy
