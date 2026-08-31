"""One plate on screen, and the switch says what it is showing.

Three sibling tabs meant three plates and three interaction models for "point
at some wells" — and on Variables, two plates at once: the rail's, greyed,
behind the tab's own live one. The merge is only worth anything if that stays
fixed, so what is asserted here is the *state of the screen*, not that a
function was called.

The second half is the selection, and it is deliberately asymmetric. On
Variables and Well Labels the plate's selection is "the wells I am about to act
on", so it travels between them. On Groups the plate is the current group's
membership and a drag on it is an edit, so carrying a selection in would rewrite
a group's wells — the silent-annotation defect the whole variables design is
shaped around, arriving through a layer switch instead. A test that only checked
"the selection is shared" would pass against exactly that bug.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWidget

from well_viewer.features.home.sample_map.controller import (
    LAYER_PLATES, LAYERS, SHARED_SELECTION_LAYERS, current_layer, selected_on,
    set_layer,
)


class _Registry:
    def __init__(self):
        self._d = {}

    def register(self, name, widget):
        self._d[name] = widget
        return widget

    def get_or_none(self, name):
        return self._d.get(name)

    def has(self, name):
        return name in self._d


def _wells():
    return {f"{r}{c:02d}": f"/data/{r}{c:02d}.csv" for r in "AB" for c in (1, 2, 3, 4)}


def _ctx(selections=()):
    from well_viewer.core.selection_state import SelectionState
    from well_viewer.features.home.state import HomeState

    selection = SelectionState()
    selection.selections = [dict(s) for s in selections]
    home = HomeState()
    return SimpleNamespace(
        widgets=_Registry(),
        window=SimpleNamespace(),
        coordinator=SimpleNamespace(refresh_sidebar_map=lambda: None,
                                    rebuild_all=lambda: None),
        features=SimpleNamespace(call=lambda *a, **k: None,
                                 get=lambda _n: None,
                                 sub_leaves=lambda _p: ("Sample Map",)),
        runtime=SimpleNamespace(home=SimpleNamespace(subtabs=None),
                                groups=SimpleNamespace(inline_edit_idx=-1)),
        state=SimpleNamespace(
            # Home's **real** typed slice, not a namespace shaped to whatever
            # the code happens to read: the Sample Map tab records which layer
            # is showing there, and a stub without it answers "Replicates" to every
            # question and passes whatever the code does (§7.8, face one).
            feature_slice=lambda name: home if name == "home" else None,
            selection=selection,
            dataset=SimpleNamespace(well_paths=_wells(), well_labels={}),
        ),
        persistence=SimpleNamespace(schedule_save=lambda *a, **k: None),
    )


@pytest.fixture
def tab(qtbot):
    from well_viewer.features.home.sample_map.tab_view import build_sample_map_subtab

    ctx = _ctx()
    frame = QWidget()
    qtbot.addWidget(frame)
    frame.resize(900, 700)
    build_sample_map_subtab(ctx, frame)
    frame.show()
    qtbot.waitExposed(frame)
    return SimpleNamespace(ctx=ctx, frame=frame)


def _visible_plates(ctx) -> list[str]:
    """The layer plates a user can actually see right now."""
    out = []
    for layer, key in LAYER_PLATES.items():
        widget = ctx.widgets.get_or_none(key)
        if widget is not None and widget.isVisible():
            out.append(layer)
    return out


@pytest.mark.parametrize("layer", LAYERS)
def test_no_layer_shows_another_layers_plate(layer, tab):
    """At most one, and never a different layer's.

    Not *exactly* one: the Variables layer hides its plate until a variable
    exists, because a greyed ninety-six well grid around a two-paragraph
    explainer reads as software that has broken. What the merge guarantees is
    that no second plate is ever up — which is the state that shipped, the
    rail's greyed behind a live one.
    """
    set_layer(tab.ctx, layer)
    tab.frame.show()
    assert _visible_plates(tab.ctx) in ([layer], []), (
        f"{_visible_plates(tab.ctx)} on screen while {layer} is showing")


def test_a_layer_with_something_to_show_shows_its_plate(tab):
    """The other half, so "at most one" cannot be satisfied by showing none.

    Driven per layer with that layer given something to draw: a group for
    Groups, a variable for Variables, and the label grid, which is always there.
    """
    from well_viewer.domain.sample_variables import VariableSet, new_variable

    set_layer(tab.ctx, "Well Labels")
    assert _visible_plates(tab.ctx) == ["Well Labels"]

    tab.ctx.state.selection.selections = [
        {"id": "g1", "name": "Ctrl", "wells": ["A01"], "hidden": False,
         "color": "#5B9BF8", "replicates": None, "source": "user"}]
    set_layer(tab.ctx, "Replicates")
    assert _visible_plates(tab.ctx) == ["Replicates"]

    vset, _id = new_variable(VariableSet(), "Dose")
    tab.ctx.state.selection.variables = vset
    set_layer(tab.ctx, "Variables")
    assert _visible_plates(tab.ctx) == ["Variables"]


def test_every_roster_entry_resolves_to_something_real(tab):
    """Key-set equality is not enough: a builder name that resolves to nothing
    and a plate key nothing registers both pass it while the tab is broken."""
    import importlib

    from well_viewer.features.home.sample_map.controller import LAYER_BUILDERS

    for layer, (module, builder) in LAYER_BUILDERS.items():
        assert callable(getattr(importlib.import_module(module), builder, None)), (
            f"{layer}'s builder {module}.{builder} does not resolve")
    for layer, key in LAYER_PLATES.items():
        widget = tab.ctx.widgets.get_or_none(key)
        assert widget is not None, f"{layer}'s plate {key} is never registered"
        # ``selected_on`` and ``_apply_selection`` walk a two-name protocol and
        # answer [] for a widget carrying neither, which would be a layer whose
        # selection silently never travels.
        assert any(callable(getattr(widget, n, None))
                   for n in ("selectedWellIds", "selectedTokens")), key
        assert any(callable(getattr(widget, n, None))
                   for n in ("setSelectedWellIds", "setSelectedTokens")), key


def test_the_switch_and_the_page_agree(tab):
    from well_viewer.features.home.sample_map.controller import STACK_KEY, SWITCH_KEY

    for layer in LAYERS:
        set_layer(tab.ctx, layer)
        assert current_layer(tab.ctx) == layer
        assert tab.ctx.widgets.get_or_none(SWITCH_KEY).currentData() == layer
        assert (tab.ctx.widgets.get_or_none(STACK_KEY).currentIndex()
                == LAYERS.index(layer))


def _press(ctx, layer):
    """Press a segment the way a mouse does.

    ``QButtonGroup.idClicked`` reaches ``_on_clicked``, which is a different
    entry point from ``setCurrentIndex`` — and the difference matters, because
    both update the control's own index **before** emitting. Every test in this
    file used to call ``set_layer`` directly, one step under the control, which
    is how a switch that carried nothing on a real press passed all of them.
    """
    from well_viewer.features.home.sample_map.controller import SWITCH_KEY

    ctx.widgets.get_or_none(SWITCH_KEY)._on_clicked(LAYERS.index(layer))


def test_pressing_a_segment_switches_the_layer(tab):
    """The control, not the function behind it. A switch wired to nothing shows
    the same three segments."""
    _press(tab.ctx, "Well Labels")
    assert current_layer(tab.ctx) == "Well Labels"
    assert _visible_plates(tab.ctx) == ["Well Labels"]


def test_pressing_a_segment_carries_the_selection_too(tab):
    """The same property as the ``set_layer`` case, through the real control.

    This is the one that bit: the outgoing layer was read back off the
    ``SegmentedControl``, which had already moved to the incoming one, so the
    handler asked an empty plate what to carry and carried nothing.
    """
    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A02", "B03"])
    _press(tab.ctx, "Well Labels")
    assert selected_on(tab.ctx, "Well Labels") == ["A02", "B03"]
    assert tab.ctx.state.selection.selected_wells == {"A02", "B03"}


def test_looking_at_a_layer_never_clears_the_app_wide_selection(tab):
    """Pressing a segment is not a deselection.

    The wells a user picked on Home's Overview plate are what Cell Gating's CDFs
    and Add Prefix read. The first version published the incoming layer's empty
    plate over them, so visiting the tab emptied the selection the publishing
    exists to serve.
    """
    tab.ctx.state.selection.selected_wells = {"A01", "A02", "A03"}
    _press(tab.ctx, "Variables")
    assert tab.ctx.state.selection.selected_wells == {"A01", "A02", "A03"}
    # And the plate the user is now looking at shows them, rather than sitting
    # empty beside a selection it disagrees with.
    assert selected_on(tab.ctx, "Variables") == ["A01", "A02", "A03"]


def test_a_selection_the_incoming_plate_cannot_show_is_not_thrown_away(tab):
    """The case the refusal to publish an empty layer actually protects.

    Well labels outlive the wells they name — a plate re-run over fewer wells
    keeps them — so ``selected_wells`` can hold a token this load does not have.
    The label grid drops it (it selects only enabled cells), leaving the layer
    empty. Publishing that empty over the real selection deletes a well the user
    picked, on a plate that never had the chance to show it.
    """
    tab.ctx.state.selection.selected_wells = {"H12"}
    _press(tab.ctx, "Well Labels")
    assert selected_on(tab.ctx, "Well Labels") == [], "premise: the grid drops it"
    assert tab.ctx.state.selection.selected_wells == {"H12"}


def test_clearing_a_plate_by_hand_still_clears_the_selection(tab):
    """The other half of that rule. A click that leaves nothing selected **is**
    a deselection, and suppressing it would be the opposite bug."""
    from well_viewer.features.home.sample_map.controller import (
        on_layer_selection_changed,
    )

    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A01"])
    on_layer_selection_changed(tab.ctx, "Variables")
    assert tab.ctx.state.selection.selected_wells == {"A01"}
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds([])
    on_layer_selection_changed(tab.ctx, "Variables")
    assert tab.ctx.state.selection.selected_wells == set()


def test_arriving_on_the_groups_layer_picks_no_group(tab):
    """Otherwise the first drag on the plate edits whichever group was left
    active from the last visit. Home's sub-tab handler did this while Groups was
    a tab; the merge left it reading a tab name the registry cannot produce."""
    from well_viewer.features.home.sample_map.controller import on_tab_entered

    tab.ctx.state.selection.selections = [
        {"id": "g1", "name": "Ctrl", "wells": ["A01"], "hidden": False,
         "color": "#5B9BF8", "replicates": None, "source": "user"}]
    tab.ctx.state.selection.current_selection_id = "g1"
    set_layer(tab.ctx, "Variables")
    _press(tab.ctx, "Replicates")
    assert tab.ctx.state.selection.current_selection_id is None

    tab.ctx.state.selection.current_selection_id = "g1"
    on_tab_entered(tab.ctx)
    assert tab.ctx.state.selection.current_selection_id is None


def test_a_layer_switch_redraws_once_not_twice(tab):
    """The switch used to re-enter ``set_layer`` for the same layer, so every
    programmatic switch ran the whole selection fan-out twice — the first time
    with nothing selected."""
    seen = []
    original = tab.ctx.state.selection.selected_wells

    class _Watched(set):
        pass

    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A01", "A02"])
    import well_viewer.core.selection_controller as sc
    real = sc.set_selected_wells
    sc.set_selected_wells = lambda ctx, wells, **kw: (
        seen.append(sorted(wells)), real(ctx, wells, **kw))[1]
    try:
        _press(tab.ctx, "Well Labels")
    finally:
        sc.set_selected_wells = real
    assert seen == [], f"the switch published more than the carry needed: {seen}"
    assert tab.ctx.state.selection.selected_wells == {"A01", "A02"}
    assert isinstance(original, set) and _Watched is not None


# ── the selection travels where it means the same thing ─────────────────────

def test_the_selection_carries_between_the_two_layers_that_share_it(tab):
    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A02", "B03"])
    set_layer(tab.ctx, "Well Labels")
    assert selected_on(tab.ctx, "Well Labels") == ["A02", "B03"]


def test_the_selection_is_never_carried_into_the_groups_layer(tab):
    """Its plate is a group's membership. Writing a selection into it and
    letting the commit fire is a group silently taking wells nobody put in it."""
    assert "Replicates" not in SHARED_SELECTION_LAYERS
    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A02", "B03"])
    set_layer(tab.ctx, "Replicates")
    assert selected_on(tab.ctx, "Replicates") == []


def test_the_shared_layers_publish_to_the_app_wide_selection(tab):
    """Cell Gating's CDFs and Add Prefix's fallback read
    ``selection.selected_wells``, and Home's old rail plate never wrote it —
    measured, not assumed. Publishing here is what gives those two a picker."""
    from well_viewer.features.home.sample_map.controller import (
        on_layer_selection_changed,
    )

    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A01", "A03"])
    on_layer_selection_changed(tab.ctx, "Variables")
    assert tab.ctx.state.selection.selected_wells == {"A01", "A03"}


@pytest.mark.parametrize("layer,key,setter", [
    ("Variables", "_var_plate", "setSelectedWellIds"),
    ("Well Labels", "_lbl_grid", "setSelectedTokens"),
])
def test_switching_to_a_layer_publishes_what_that_layer_already_holds(
        layer, key, setter, tab):
    """Not only a click on the plate — the switch itself.

    Cell Gating and Add Prefix read ``selected_wells``, and a user returning to
    a layer that still holds a selection has clicked no plate since. Without the
    publish in ``set_layer`` the app-wide selection keeps whatever the previous
    layer left, so those two controls act on wells the plate in front of the
    user is not showing.

    **Both layers, because only one of them proves it.** Removing the publish
    and measuring: Variables still ends up correct, because its refresh repaints
    the plate and that emits ``selectionChanged``, which publishes by the other
    route. Well Labels does not — its refresh reloads text and emits nothing —
    so the mutation is visible on that layer alone. Written against Variables,
    this test passed against code with the feature deleted.

    The trip through Groups matters too: it neither donates nor receives, so
    coming back carries nothing and applies nothing, and the switch's own
    publish is the only thing left that can be right.
    """
    set_layer(tab.ctx, layer)
    getattr(tab.ctx.widgets.get_or_none(key), setter)(["B01", "B04"])
    set_layer(tab.ctx, "Replicates")
    tab.ctx.state.selection.selected_wells = set()
    set_layer(tab.ctx, layer)
    assert tab.ctx.state.selection.selected_wells == {"B01", "B04"}


def test_the_groups_layer_never_publishes(tab):
    """A group's membership is not "the wells I am about to act on", so it must
    not become the app-wide selection just because the layer is showing."""
    from well_viewer.features.home.sample_map.controller import (
        on_layer_selection_changed,
    )

    set_layer(tab.ctx, "Replicates")
    tab.ctx.widgets.get_or_none("_rep_map_plate").setSelectedWellIds(["A01"])
    on_layer_selection_changed(tab.ctx, "Replicates")
    assert tab.ctx.state.selection.selected_wells == set()


def test_import_follows_the_layer_not_the_tab(tab):
    """**Import** merges replicate selections and well labels, so it means
    something on two layers and nothing on the third.

    A per-*tab* rule while Variables was a tab of its own; the merge makes it a
    per-layer one, and a finer one, since Variables is now a layer of the very
    tab whose other two layers are what Import writes. The slow lane asserts it
    against the real button; this asserts the rule where a mutation can reach it.
    """
    from PySide6.QtWidgets import QPushButton

    from well_viewer.features.home.controller import sync_home_sidebar
    from well_viewer.features.home.tab_view import IMPORT_KEY

    button = tab.ctx.widgets.register(IMPORT_KEY, QPushButton(""))
    tab.ctx.runtime.home.subtabs = SimpleNamespace(
        count=lambda: 1, tabText=lambda _i: "Sample Map", currentIndex=lambda: 0)
    tab.ctx.features = SimpleNamespace(sub_leaves=lambda _p: ("Sample Map",))
    for layer, offered in (("Replicates", True), ("Well Labels", True),
                           ("Variables", False)):
        set_layer(tab.ctx, layer)
        sync_home_sidebar(tab.ctx)
        assert button.isVisibleTo(tab.frame) is offered, layer


# ── Home keeps no rail body ─────────────────────────────────────────────────

def test_home_never_shows_a_rail_plate_frame(qtbot):
    """``activate_home``'s docstring has always said Home shows no rail frame.
    The sample frame contradicted it, and carried the second plate."""
    from well_viewer.features.home.controller import sync_home_sidebar

    ctx = _ctx()
    frame = QWidget()
    qtbot.addWidget(frame)
    frame.show()
    ctx.widgets.register("_sidebar_sample_frame", frame)
    ctx.features = SimpleNamespace(get=lambda _n: None,
                                   sub_leaves=lambda _p: ("Sample Map",))
    ctx.runtime.home.subtabs = None
    sync_home_sidebar(ctx)
    assert not frame.isVisible()
