"""The Sample map tab's layer switch: which annotation the plate is showing.

Groups, sample variables and well labels are three ways of saying something
about a well, and each used to have a tab of its own. Three tabs meant three
plates, three interaction models for "point at some wells", and — on Variables
— two plates on screen at once, the rail's greyed behind the tab's own live one.
A user could not tell from the page which control would change a number.

One tab, one plate, and a switch that names what the plate is showing. The three
bodies are unchanged: each layer is still built by its own package's builder,
into its own page of a ``QStackedWidget``, so exactly one plate is on screen and
each keeps the painter it already had. A single shared selector painting three
different things is the "two plates meaning different things" hazard turned
inward, and a well label cell takes free text, which a ``WellPlateSelector``
cannot.

**The selection travels between the two layers where it means the same thing.**
On Variables and Well Labels the plate's selection is *the wells I am about to
act on* — assign a value, add a prefix — so switching between them carries it
across and writes it to ``ctx.state.selection.selected_wells``, which is what
Cell Gating's CDFs and Add Prefix's fallback read. On Groups it is not that: the
plate shows **the current group's membership**, pushed by ``rep_refresh_map``,
and a drag on it rewrites that group. Carrying a selection into it would
silently rewrite a group's wells, so the Groups layer neither receives nor
donates. The asymmetry is real and the switch is what makes it legible: the
strip above the plate says which of the two the plate currently means.
"""

from __future__ import annotations

from well_viewer.core.safe import swallow
from well_viewer.features.home.collect import DECLARATION_ROLES

#: Registry key for the layer switch itself.
SWITCH_KEY = "_plate_map_layers"
#: Registry key for the page stack the three bodies are built into.
STACK_KEY = "_plate_map_stack"
#: Registry key for the host the current layer's role strip is painted into.
STRIP_KEY = "_plate_map_strip"

#: The layers, in the order the tabs used to appear. Derived from the one
#: declaration roster rather than restated: a layer added there and forgotten
#: here would be a declaration with no editor, and the reverse an editor with no
#: statement of what it is for.
LAYERS: tuple[str, ...] = tuple(r.tab for r in DECLARATION_ROLES if r.tab)

#: ``layer -> (module, builder)`` for the three bodies, unchanged from when each
#: was a tab of its own. Function-local imports at build time keep the
#: sub-packages removable.
LAYER_BUILDERS: dict[str, tuple[str, str]] = {
    "Groups": ("well_viewer.features.home.groups.tab_view",
               "build_replicate_groups_centre"),
    "Variables": ("well_viewer.features.home.variables.tab_view",
                  "build_variables_subtab"),
    "Well Labels": ("well_viewer.features.home.labels.tab_view",
                    "build_label_grid"),
}

#: ``layer -> the registry key of the widget that holds that layer's selection``.
#: Three widgets rather than one, because they are not the same control: two are
#: ``WellPlateSelector``s and the third is the 96-cell text grid, which exists
#: precisely because a label cell takes free text.
LAYER_PLATES: dict[str, str] = {
    "Groups": "_rep_map_plate",
    "Variables": "_var_plate",
    "Well Labels": "_lbl_grid",
}

#: The layers whose plate selection means "the wells I am about to act on", and
#: which therefore share it. Groups is absent on purpose — see the module
#: docstring; its plate shows a group's membership and a drag on it is an edit.
SHARED_SELECTION_LAYERS: frozenset[str] = frozenset({"Variables", "Well Labels"})


def current_layer(ctx) -> str:
    """The layer the tab is showing, or the first one before it is built."""
    switch = ctx.widgets.get_or_none(SWITCH_KEY)
    if switch is None:
        return LAYERS[0]
    data = switch.currentData()
    return str(data) if data in LAYERS else LAYERS[0]


def selected_on(ctx, layer: str) -> list[str]:
    """The wells selected on *layer*'s own control, or ``[]``.

    Two vocabularies, because the controls are two different classes and
    neither is going to grow the other's method name for this: a
    ``WellPlateSelector`` answers ``selectedWellIds`` and the label grid
    ``selectedTokens``.
    """
    widget = ctx.widgets.get_or_none(LAYER_PLATES.get(layer, ""))
    if widget is None:
        return []
    for name in ("selectedWellIds", "selectedTokens"):
        getter = getattr(widget, name, None)
        if callable(getter):
            with swallow(f"plate map: reading {layer}'s selection"):
                return [str(w) for w in (getter() or ())]
    return []


def _apply_selection(ctx, layer: str, wells) -> None:
    widget = ctx.widgets.get_or_none(LAYER_PLATES.get(layer, ""))
    if widget is None:
        return
    for name in ("setSelectedWellIds", "setSelectedTokens"):
        setter = getattr(widget, name, None)
        if callable(setter):
            with swallow(f"plate map: applying a selection to {layer}"):
                setter(list(wells))
            return


def set_layer(ctx, layer: str) -> None:
    """Show *layer*: swap the page, restate the role, carry the selection."""
    if layer not in LAYERS:
        return
    outgoing = current_layer(ctx)
    carried = (selected_on(ctx, outgoing)
               if outgoing in SHARED_SELECTION_LAYERS else [])

    stack = ctx.widgets.get_or_none(STACK_KEY)
    if stack is not None:
        stack.setCurrentIndex(LAYERS.index(layer))
    switch = ctx.widgets.get_or_none(SWITCH_KEY)
    if switch is not None and switch.currentData() != layer:
        switch.setCurrentByData(layer)

    _paint_strip(ctx, layer)
    if carried and layer in SHARED_SELECTION_LAYERS:
        _apply_selection(ctx, layer, carried)
    _publish_selection(ctx, layer)
    refresh_layer(ctx, layer)
    with swallow("sample map: restating what the plots pool by"):
        from well_viewer.core.group_by_controller import refresh_grouping_readouts
        refresh_grouping_readouts(ctx)


def _publish_selection(ctx, layer: str) -> None:
    """Push the layer's selection to the app-wide one, where that is what it is.

    Cell Gating draws its CDFs from ``selected_wells`` and Add Prefix falls back
    to it, and Home's rail plate — the only other plate the page used to carry —
    never wrote it: it composed a group and nothing else, which was measured
    rather than assumed. So those two controls ran off a selection that could
    only be made on Overview. Publishing here is what makes the plate a user is
    looking at the plate those controls read.
    """
    if layer not in SHARED_SELECTION_LAYERS:
        return
    from well_viewer.core.selection_controller import set_selected_wells

    wells = set(selected_on(ctx, layer))
    if wells == set(ctx.state.selection.selected_wells or ()):
        return
    with swallow("plate map: publishing the layer's selection"):
        set_selected_wells(ctx, wells)


def _paint_strip(ctx, layer: str) -> None:
    from well_viewer.features.home.collect import role_for_tab
    from well_viewer.features.home.roles_view import build_role_strip

    host = ctx.widgets.get_or_none(STRIP_KEY)
    role = role_for_tab(layer)
    if host is None or role is None:
        return
    layout = host.layout()
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
    layout.addWidget(build_role_strip(role, host))


def refresh_layer(ctx, layer: str | None = None) -> None:
    """Repaint the current layer, and only it.

    Each layer's painter is its own — ``rep_panel_refresh``, the Variables
    refresh, the label grid's — and painting one while another is showing would
    put a dose legend under a group list. The dispatch is here rather than in
    each painter because it is a fact about the tab, not about any one of them.
    """
    layer = layer or current_layer(ctx)
    if layer == "Groups":
        with swallow("plate map: refreshing the Groups layer"):
            from well_viewer.features.home.groups.selections_view import (
                rep_panel_refresh,
            )
            rep_panel_refresh(ctx)
        return
    if layer == "Variables":
        with swallow("plate map: refreshing the Variables layer"):
            from well_viewer.features.home.variables.controller import (
                refresh_variables,
            )
            refresh_variables(ctx)
        return
    with swallow("plate map: refreshing the Well Labels layer"):
        from well_viewer.features.home.labels.tab_view import label_grid_refresh
        # The feature's own refresh, not ``grid.refresh()``. That one reloads
        # the text and leaves the enabled set alone, so a grid that had not yet
        # been told which wells the dataset holds stayed inert: every cell
        # disabled, nothing selectable, and a selection carried in from the
        # Variables layer silently dropped on the way. Caught by the layer-carry
        # test, which is the reason it drives the switch rather than the setter.
        label_grid_refresh(ctx)


def on_layer_selection_changed(ctx, layer: str) -> None:
    """A click on the layer's own control reached the app-wide selection."""
    if layer != current_layer(ctx):
        return
    _publish_selection(ctx, layer)
