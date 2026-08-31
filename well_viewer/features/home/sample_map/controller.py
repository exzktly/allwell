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

from well_viewer.core.safe import swallow, swallowed
from well_viewer.features.home.collect import DECLARATION_ROLES, role_for_tab
from well_viewer.features.home.groups.selections_view import rep_panel_refresh
from well_viewer.features.home.labels.tab_view import label_grid_refresh
from well_viewer.features.home.roles_view import build_role_strip
from well_viewer.features.home.variables.controller import refresh_variables

#: Registry key for the layer switch itself.
SWITCH_KEY = "_sample_map_layers"
#: Registry key for the page stack the three bodies are built into.
STACK_KEY = "_sample_map_stack"
#: Registry key for the host the current layer's role strip is painted into.
STRIP_KEY = "_sample_map_strip"

#: The layers, in the order the tabs used to appear. Derived from the one
#: declaration roster rather than restated: a layer added there and forgotten
#: here would be a declaration with no editor, and the reverse an editor with no
#: statement of what it is for.
LAYERS: tuple[str, ...] = tuple(r.tab for r in DECLARATION_ROLES if r.tab)

#: ``layer -> (module, builder)`` for the three bodies, unchanged from when each
#: was a tab of its own. Resolved by ``importlib`` at build time rather than
#: imported here: ``groups/tab_view`` imports ``groups/panel_view``, which asks
#: this module which layer is current, so a static import of the builders would
#: close that loop at import time.
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
    """The layer the tab is showing, from typed state.

    **Not from the ``SegmentedControl``.** That widget sets its own index
    *before* it emits ``currentChanged``, so a handler asking it which layer is
    current is told the **incoming** one — and every question of the form "what
    was showing a moment ago" then answers with the layer being moved to. That
    is precisely what made a layer switch driven by a mouse press carry no
    selection while the same switch driven by :func:`set_layer` carried it
    correctly, which no test could see because every test called
    :func:`set_layer`.

    Before the tab is built the slice holds ``""`` and the first layer is the
    honest answer: that is what a freshly built stack shows.
    """
    slice_ = _slice(ctx)
    stored = str(getattr(slice_, "sample_map_layer", "") or "")
    return stored if stored in LAYERS else LAYERS[0]


def layer_is_showing(ctx, layer: str) -> bool:
    """Whether *layer* is the one on screen, answered **only** from a record.

    :func:`current_layer` falls back to the first layer for a tab that has not
    recorded one yet, which is the right answer for painting — a freshly built
    stack really does show it. It is the wrong answer for a guard: the first
    layer is Groups, the one layer whose plate commits a group edit, so a guard
    written on that fallback fails *open* in exactly the state where nothing is
    known. This one says no unless the tab has said yes.
    """
    slice_ = _slice(ctx)
    return str(getattr(slice_, "sample_map_layer", "") or "") == layer


def _slice(ctx):
    """Home's typed state slice, or ``None`` on a context that has no features."""
    getter = getattr(getattr(ctx, "state", None), "feature_slice", None)
    if not callable(getter):
        return None
    try:
        return getter("home")
    except Exception:
        swallowed("sample map: reading Home's state slice")
        return None


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
            with swallow(f"sample map: reading {layer}'s selection"):
                return [str(w) for w in (getter() or ())]
    return []


def _apply_selection(ctx, layer: str, wells) -> None:
    widget = ctx.widgets.get_or_none(LAYER_PLATES.get(layer, ""))
    if widget is None:
        return
    for name in ("setSelectedWellIds", "setSelectedTokens"):
        setter = getattr(widget, name, None)
        if callable(setter):
            with swallow(f"sample map: applying a selection to {layer}"):
                setter(list(wells))
            return


def set_layer(ctx, layer: str) -> None:
    """Show *layer*: swap the page, restate the role, carry the selection.

    The order below is load-bearing and each step is here for a reason a test
    watched fail:

    1. **Record the layer first**, so everything after it — the commit guard on
       the Groups plate included — is asked about the layer being shown.
    2. **Move the switch with its signals blocked.** Left live, setting it
       re-enters this function for the same layer, and the inner call published
       an empty selection that the outer call then had to undo: two full redraw
       fan-outs per switch, the first of them with nothing selected.
    3. **Refresh before applying.** A layer's refresh is what tells its plate
       which wells the dataset holds, and both plates drop a selection of wells
       they do not yet know about. Applying first only worked because of the
       re-entrancy in step 2.
    """
    if layer not in LAYERS:
        return
    outgoing = current_layer(ctx)
    slice_ = _slice(ctx)
    if slice_ is not None:
        slice_.sample_map_layer = layer

    stack = ctx.widgets.get_or_none(STACK_KEY)
    if stack is not None:
        stack.setCurrentIndex(LAYERS.index(layer))
    switch = ctx.widgets.get_or_none(SWITCH_KEY)
    if switch is not None and switch.currentData() != layer:
        blocked = switch.blockSignals(True)
        try:
            switch.setCurrentByData(layer)
        finally:
            switch.blockSignals(blocked)

    _paint_strip(ctx, layer)
    refresh_layer(ctx, layer)
    if layer == "Groups":
        _clear_active_group(ctx)
    _carry_selection(ctx, outgoing, layer)
    with swallow("sample map: restating what the plots pool by"):
        from well_viewer.core.group_by_controller import refresh_grouping_readouts
        refresh_grouping_readouts(ctx)


def _clear_active_group(ctx) -> None:
    """Entering the Groups layer picks no group, so a plate click has no effect.

    A drag on that plate rewrites whichever group is current, and arriving on
    the layer with one still active from last time means the first click edits a
    group the user has not looked at since. Home's sub-tab handler did this while
    Groups was a tab of its own; the merge left the branch reading a tab name the
    registry can no longer produce, so it silently stopped running.
    """
    if ctx.state.selection.current_selection_id is None:
        return
    with swallow("sample map: clearing the active group on entry"):
        from well_viewer.core.selection_controller import groups_centre_refresh

        # Not ``sel_select(ctx, "")``: that refuses an id no selection carries,
        # so it is a no-op here. The three steps are the ones Home's sub-tab
        # handler ran while Groups was a tab of its own.
        ctx.state.selection.current_selection_id = None
        groups_centre_refresh(ctx)
        refresher = getattr(getattr(ctx, "coordinator", None),
                            "refresh_sidebar_map", None)
        if callable(refresher):
            refresher()


def _carry_selection(ctx, outgoing: str, layer: str) -> None:
    """Bring the wells the user had picked onto the layer they moved to.

    Only between the two layers where a plate selection means the same thing —
    *the wells I am about to act on*. The Groups plate is a group's membership,
    so it neither donates nor receives.

    **Coming from Groups, the app-wide selection is what is carried.** It is the
    selection Cell Gating and Add Prefix read, so adopting it is what makes the
    plate on screen agree with the controls that use it. Publishing an *empty*
    layer over a non-empty app-wide selection is the opposite move and is
    refused: looking at a tab is not a deselection, and the version that did it
    wiped the wells a user had picked on Overview the moment they pressed a
    segment.
    """
    if layer not in SHARED_SELECTION_LAYERS:
        return
    if outgoing in SHARED_SELECTION_LAYERS:
        carried = selected_on(ctx, outgoing)
    else:
        carried = sorted(ctx.state.selection.selected_wells or ())
    if carried:
        _apply_selection(ctx, layer, carried)
    _publish_selection(ctx, layer, allow_clear=False)


def _publish_selection(ctx, layer: str, *, allow_clear: bool) -> None:
    """Push the layer's selection to the app-wide one, where that is what it is.

    Cell Gating draws its CDFs from ``selected_wells`` and Add Prefix falls back
    to it, and Home's rail plate — the only other plate the page used to carry —
    never wrote it: it composed a group and nothing else, which was measured
    rather than assumed. So those two controls ran off a selection that could
    only be made on Overview. Publishing here is what makes the plate a user is
    looking at the plate those controls read.

    ``allow_clear`` separates the two callers, and the distinction is the whole
    of finding 2. A click on the plate that leaves nothing selected **is** a
    deselection and must clear. A layer *switch* that arrives at an empty plate
    is not: the first version cleared there too, so pressing a segment threw
    away the wells the user had picked on Overview — the exact controls this
    publishing exists to serve, emptied by looking at them.
    """
    if layer not in SHARED_SELECTION_LAYERS:
        return
    from well_viewer.core.selection_controller import set_selected_wells

    wells = set(selected_on(ctx, layer))
    if not wells and not allow_clear:
        return
    if wells == set(ctx.state.selection.selected_wells or ()):
        return
    with swallow("sample map: publishing the layer's selection"):
        set_selected_wells(ctx, wells)


def _paint_strip(ctx, layer: str) -> None:
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
    """Repaint *layer* through its own package's refresh.

    Three painters, one per layer, and the dispatch is here because which layer
    is showing is a fact about the tab rather than about any one of them. It is
    **not** an isolation rule: ``selection_controller.groups_centre_refresh``
    repaints the group list, the label grid and the plate together on every load
    and every group edit, whichever layer is showing, and that is fine — each
    paints into its own page. What this gives is that entering a layer leaves it
    current, which is what tells a plate which wells the dataset holds before
    anything tries to select on it.
    """
    layer = layer or current_layer(ctx)
    if layer == "Groups":
        with swallow("sample map: refreshing the Groups layer"):
            rep_panel_refresh(ctx)
        return
    if layer == "Variables":
        with swallow("sample map: refreshing the Variables layer"):
            refresh_variables(ctx)
        return
    with swallow("sample map: refreshing the Well Labels layer"):
        # The feature's own refresh, not ``grid.refresh()``. That one reloads
        # the text and leaves the enabled set alone, so a grid that had not yet
        # been told which wells the dataset holds stayed inert: every cell
        # disabled, nothing selectable, and a selection carried in from the
        # Variables layer silently dropped on the way. Caught by the layer-carry
        # test, which is the reason it drives the switch rather than the setter.
        label_grid_refresh(ctx)


def on_layer_selection_changed(ctx, layer: str) -> None:
    """A click on the layer's own control reached the app-wide selection.

    ``allow_clear=True``: this is the user acting on the plate, so clearing it
    is a deselection and must travel.
    """
    if layer != current_layer(ctx):
        return
    _publish_selection(ctx, layer, allow_clear=True)


def on_tab_entered(ctx) -> None:
    """Home switched to this tab: restate the layer it is showing.

    Repaints the current layer and re-runs the entry rules for it — including
    clearing the active group, which is what stops the first click on the Groups
    plate editing a group the user has not looked at since their last visit.
    """
    set_layer(ctx, current_layer(ctx))
