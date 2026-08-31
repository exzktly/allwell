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

    selection = SelectionState()
    selection.selections = [dict(s) for s in selections]
    return SimpleNamespace(
        widgets=_Registry(),
        window=SimpleNamespace(),
        runtime=SimpleNamespace(home=SimpleNamespace(subtabs=None),
                                groups=SimpleNamespace(inline_edit_idx=-1)),
        state=SimpleNamespace(
            selection=selection,
            dataset=SimpleNamespace(well_paths=_wells(), well_labels={}),
        ),
        persistence=SimpleNamespace(schedule_save=lambda *a, **k: None),
    )


@pytest.fixture
def tab(qtbot):
    from well_viewer.features.home.sample_map.tab_view import build_plate_map_subtab

    ctx = _ctx()
    frame = QWidget()
    qtbot.addWidget(frame)
    frame.resize(900, 700)
    build_plate_map_subtab(ctx, frame)
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
def test_exactly_one_plate_is_on_screen(layer, tab):
    set_layer(tab.ctx, layer)
    tab.frame.show()
    assert _visible_plates(tab.ctx) == [layer], (
        "the point of the merge is that one plate is on screen and the switch "
        "says what it is")


def test_the_switch_and_the_page_agree(tab):
    from well_viewer.features.home.sample_map.controller import STACK_KEY, SWITCH_KEY

    for layer in LAYERS:
        set_layer(tab.ctx, layer)
        assert current_layer(tab.ctx) == layer
        assert tab.ctx.widgets.get_or_none(SWITCH_KEY).currentData() == layer
        assert (tab.ctx.widgets.get_or_none(STACK_KEY).currentIndex()
                == LAYERS.index(layer))


def test_pressing_a_segment_switches_the_layer(tab):
    """The control, not the function behind it. A switch wired to nothing shows
    the same three segments."""
    from well_viewer.features.home.sample_map.controller import SWITCH_KEY

    switch = tab.ctx.widgets.get_or_none(SWITCH_KEY)
    switch.setCurrentIndex(LAYERS.index("Well Labels"))
    assert current_layer(tab.ctx) == "Well Labels"
    assert _visible_plates(tab.ctx) == ["Well Labels"]


# ── the selection travels where it means the same thing ─────────────────────

def test_the_selection_carries_between_the_two_layers_that_share_it(tab):
    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A02", "B03"])
    set_layer(tab.ctx, "Well Labels")
    assert selected_on(tab.ctx, "Well Labels") == ["A02", "B03"]


def test_the_selection_is_never_carried_into_the_groups_layer(tab):
    """Its plate is a group's membership. Writing a selection into it and
    letting the commit fire is a group silently taking wells nobody put in it."""
    assert "Groups" not in SHARED_SELECTION_LAYERS
    set_layer(tab.ctx, "Variables")
    tab.ctx.widgets.get_or_none("_var_plate").setSelectedWellIds(["A02", "B03"])
    set_layer(tab.ctx, "Groups")
    assert selected_on(tab.ctx, "Groups") == []


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
    set_layer(tab.ctx, "Groups")
    tab.ctx.state.selection.selected_wells = set()
    set_layer(tab.ctx, layer)
    assert tab.ctx.state.selection.selected_wells == {"B01", "B04"}


def test_the_groups_layer_never_publishes(tab):
    """A group's membership is not "the wells I am about to act on", so it must
    not become the app-wide selection just because the layer is showing."""
    from well_viewer.features.home.sample_map.controller import (
        on_layer_selection_changed,
    )

    set_layer(tab.ctx, "Groups")
    tab.ctx.widgets.get_or_none("_rep_map_plate").setSelectedWellIds(["A01"])
    on_layer_selection_changed(tab.ctx, "Groups")
    assert tab.ctx.state.selection.selected_wells == set()


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
                                   sub_leaves=lambda _p: ("Sample map",))
    ctx.runtime.home.subtabs = None
    sync_home_sidebar(ctx)
    assert not frame.isVisible()
