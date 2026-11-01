"""*Colour by*, *Split by* and *Then by* on the Nanowells page's Distribution view.

The three controls are the plotting band's — the same ``PlotState`` fields, the same variables, the same inks —
reached from the page's own band, because the Nanowells page is not a Plotting leaf and never shows that band. None
of them changes what a group *is* or moves a number: ``group_by_variable`` decides the groups upstream, in
``plot_members.plot_replicate_sets`` (which ``controller.page_groups`` reads), and these only decide each group's
ink and the labelled band it sits in.

**The answers come from ``domain/variable_series``, never from here.** Which value a group holds, what ink that
value takes, what a group whose wells disagree or a group holding an unfilled well is called and coloured — all of
it is :func:`variable_series.member_inks` / :func:`~variable_series.member_bands` over the page's own key space
(``group name -> wells``), which is the override Batch Export's Bar mode makes for the same reason. So "what colour
is HeLa" has one answer on the Bar tab and on this page.

:class:`Decoration` is what the model and the renderer read; it is plain data, so the layout rules
(:func:`order_groups`, :func:`band_spans`) are tested without a ctx or a figure. :func:`decoration_for` is the one
function that reads the ctx.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Decoration:
    """What *Colour by* / *Split by* / *Then by* resolved to for one figure's groups. Empty: undecorated."""

    #: ``group -> colour`` — every drawn group's ink, the two non-values included. Empty: dots keep occupancy.
    inks: dict = field(default_factory=dict)
    #: ``(label, colour)`` for the values actually drawn, in the variable's order — the dots' key.
    legend: tuple = ()
    #: The *Colour by* variable's name, the key's title.
    colour_name: str = ""
    #: ``group -> band label`` for *Split by*; empty when not splitting.
    bands: dict = field(default_factory=dict)
    #: Every band label in the variable's own order, the two non-values last (``variable_series.band_order``).
    band_order: tuple = ()
    #: ``group -> sub-band label`` for *Then by*; empty unless splitting twice.
    sub_bands: dict = field(default_factory=dict)
    sub_order: tuple = ()

    @property
    def colours(self) -> bool:
        return bool(self.inks)

    @property
    def splits(self) -> bool:
        return bool(self.bands)

    def title_prefix(self, group: str) -> str:
        """``"<band> · <sub-band> · "`` for *group*, or ``""`` — how a time-axis panel names the band it is in."""
        parts = [p for p in (self.bands.get(group, ""), self.sub_bands.get(group, "")) if p]
        return "".join(f"{p} · " for p in parts)


NONE = Decoration()


def order_groups(groups: dict, decoration: Decoration) -> dict:
    """*groups* (``name -> wells``) re-ordered so each band, and each sub-band within it, is contiguous.

    Contiguous because a band is drawn as one labelled span along the axis: a band split across it would draw its
    header over groups belonging to another. **Stable within a band**, so the page's own order (the replicate
    sets', then the solo wells') survives inside each one — sorting on the band alone would be a second ordering
    rule silently overriding the first. A group with no band (outside the map) sorts last, after the non-values.
    """
    if not decoration.splits:
        return dict(groups)
    rank = {label: i for i, label in enumerate(decoration.band_order)}
    sub_rank = {label: i for i, label in enumerate(decoration.sub_order)}
    names = sorted(groups, key=lambda name: (rank.get(decoration.bands.get(name), len(rank)),
                                             sub_rank.get(decoration.sub_bands.get(name), len(sub_rank))
                                             if decoration.sub_bands else 0))
    return {name: groups[name] for name in names}


def band_spans(names, labels: dict) -> list[tuple[int, int, str]]:
    """``(first index, last index, label)`` per contiguous run of *names* sharing a label in *labels*.

    Runs, not one entry per label: :func:`order_groups` makes each band contiguous, and a label that came back
    twice here would be drawn as two headers rather than silently as one span over both — the figure saying the
    order is wrong rather than hiding it. A name with no label ends a run and starts none.
    """
    spans: list[tuple[int, int, str]] = []
    for i, name in enumerate(names):
        label = labels.get(name, "")
        if spans and label and spans[-1][2] == label and spans[-1][1] == i - 1:
            spans[-1] = (spans[-1][0], i, label)
        elif label:
            spans.append((i, i, label))
    return spans


def sub_band_spans(names, bands: dict, sub_bands: dict) -> list[tuple[int, int, str]]:
    """:func:`band_spans` for *Then by*: runs of one sub-band **within one band**.

    Two neighbouring groups holding the same sub-band value but sitting in different bands are two spans, not one —
    a sub-band header drawn across a band boundary would claim a group from the next band.
    """
    keyed = {name: (bands.get(name, ""), sub) for name, sub in sub_bands.items() if sub}
    spans: list[tuple[int, int, str]] = []
    last = None
    for i, name in enumerate(names):
        key = keyed.get(name)
        if key is not None and key == last and spans and spans[-1][1] == i - 1:
            spans[-1] = (spans[-1][0], i, key[1])
        elif key is not None:
            spans.append((i, i, key[1]))
        last = key
    return spans


def decoration_for(ctx, groups: dict) -> Decoration:
    """The page's *Colour by* / *Split by* / *Then by* resolved over *groups* (``name -> wells``).

    Read from ``ctx.state.plot`` — the fields the plotting band writes too — with the band's own nesting rules:
    *Then by* only inside a *Split by*, and never by the same variable. A variable that no longer exists resolves to
    nothing (``variable_series`` answers ``{}``), so a stale setting draws the undecorated figure rather than a
    half-decorated one.
    """
    from review.domain.variable_series import band_order, legend_entries, member_bands, member_inks

    plot = getattr(getattr(ctx, "state", None), "plot", None)
    colour_id = str(getattr(plot, "colour_by_variable", "") or "")
    split_id = str(getattr(plot, "split_by_variable", "") or "")
    sub_id = str(getattr(plot, "sub_split_by_variable", "") or "") if split_id else ""
    if sub_id == split_id:
        sub_id = ""
    if not (colour_id or split_id) or not groups:
        return NONE
    keys = {name: list(wells) for name, wells in groups.items()}
    out = Decoration()
    if colour_id:
        out.inks = member_inks(ctx, "nanowell", colour_id, keys)
        if out.inks:
            used = member_bands(ctx, "nanowell", colour_id, keys)
            out.legend = tuple(legend_entries(ctx, colour_id, set(used.values())))
            out.colour_name = _variable_name(ctx, colour_id)
    if split_id:
        out.bands = member_bands(ctx, "nanowell", split_id, keys)
        out.band_order = tuple(band_order(ctx, split_id))
        if out.bands and sub_id:
            out.sub_bands = member_bands(ctx, "nanowell", sub_id, keys)
            out.sub_order = tuple(band_order(ctx, sub_id))
    return out


def _variable_name(ctx, variable_id: str) -> str:
    vset = getattr(getattr(ctx.state, "selection", None), "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    return str(getattr(variable, "name", "") or "")
