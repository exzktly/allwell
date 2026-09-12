"""A plate edit must not delete the group members the plate cannot show.

Home's Groups rail plate rebuilds the current group's membership from what is
selected on it. A group can name wells this dataset does not hold — a subset
load, a plate re-exported with fewer wells, a definitions file carried between
runs — and the plate has no cell for those, so a membership rebuilt from the
plate alone dropped them. ``sel_set_composition`` writes the list through
verbatim and the autosave follows, so the wells were gone from the only place
they existed: adding one well to a group silently removed every member outside
the current load.

This drives the real widget. The bug is in a closure connected to
``selectionDragFinished``, and a test that called ``sel_set_composition``
directly would enter one step past it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from review.core.widget_registry import WidgetRegistry  # noqa: E402
from review.features.home.groups import panel_view  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qapp):
    """The rail plate, a group defined over the full plate, and a load that
    holds only A01 and A02."""
    group = {
        "id": "g1", "name": "Drug", "kind": "group",
        "wells": ["A01", "A03", "A04"], "replicates": None,
    }
    selection = SimpleNamespace(
        selections=[group], current_selection_id="g1",
        selected_wells=set(), active_solo_wells=set(),
    )
    dataset = SimpleNamespace(
        well_paths={"A01": 1, "A02": 2}, well_labels={}, tok_to_label={},
    )
    from review.features.home.state import HomeState
    home = HomeState(sample_map_layer="Replicates")
    registry = WidgetRegistry(SimpleNamespace())
    ctx = SimpleNamespace(
        widgets=registry,
        state=SimpleNamespace(selection=selection, dataset=dataset,
                              feature_slice=lambda name: home if name == "home" else None),
        runtime=SimpleNamespace(home=SimpleNamespace(subtabs=SimpleNamespace(
            # The Groups editor is a layer of the Sample Map tab now, and the
            # commit guard asks the sub-tab **and** the layer. The layer half is
            # answered from Home's typed slice below, not from the switch
            # widget: the guard fails closed, so a fixture that simply omitted
            # the record would refuse every commit rather than allowing them by
            # default.
            tabText=lambda index: "Sample Map", currentIndex=lambda: 0,
        ))),
        coordinator=SimpleNamespace(
            rebuild_all=lambda: None, refresh_sidebar_map=lambda: None,
        ),
    )
    parent = QWidget()
    panel_view.build_replicate_panel(ctx, parent)
    plate = registry.get_or_none("_rep_map_plate")
    plate.setEnabledWells(["A01", "A02"])
    return ctx, group, plate, parent


def test_adding_a_well_keeps_the_members_outside_this_dataset(panel):
    _ctx, group, plate, _parent = panel

    plate.setSelectedWellIds(["A01", "A02"])
    plate.selectionDragFinished.emit()

    assert group["wells"] == ["A01", "A02", "A03", "A04"], (
        "the group members this dataset does not hold were deleted by a plate "
        f"edit: {group['wells']}"
    )


def test_removing_a_loaded_well_still_removes_it(panel):
    """The carry-through must not make loaded wells un-removable."""
    _ctx, group, plate, _parent = panel

    plate.setSelectedWellIds([])          # the user clears A01 off the group
    plate.selectionDragFinished.emit()

    assert group["wells"] == ["A03", "A04"], (
        f"a loaded well could not be removed from the group: {group['wells']}"
    )


def test_a_well_id_that_has_no_plate_position_does_not_break_the_edit(panel):
    """Carrying absent members through means sorting a list that can now hold a
    token ``parse_rc`` reads as ``(None, None)`` beside real ``("A", "01")``
    keys — comparing those raises, and a group edit is not the place to find
    out. (The unreadable token is then dropped by the selections model's own
    ``_clean_wells``, which is where well-token validity is decided; what is
    pinned here is that the edit completes rather than raising.)"""
    _ctx, group, plate, _parent = panel
    group["wells"] = ["A01", "not-a-well"]

    plate.setSelectedWellIds(["A01", "A02"])
    plate.selectionDragFinished.emit()

    assert group["wells"] == ["A01", "A02"]


def test_a_drag_from_another_layer_does_not_rewrite_the_group(panel):
    """The Groups editor shares the Sample Map tab with two other layers, and
    its ``selectionDragFinished`` still fires on programmatic refresh ticks
    while one of those is showing. Without the layer half of the guard, a
    Variables fill repainting the tab rewrites whichever group happens to be
    current — an annotation nobody made, which is the defect the whole design is
    shaped around.
    """
    ctx, group, plate, _parent = panel
    before = list(group["wells"])
    ctx.state.feature_slice("home").sample_map_layer = "Variables"
    plate.setSelectedWellIds(["A01", "A02"])
    plate.selectionDragFinished.emit()
    assert group["wells"] == before, (
        "a drag while the Variables layer was showing rewrote the group")


def test_a_drag_before_any_layer_is_recorded_does_not_commit(panel):
    """The guard fails **closed**.

    ``current_layer`` falls back to the first layer for a tab that has recorded
    none, and the first layer is Groups — the one layer whose plate edits. A
    guard written on that fallback answers "yes, commit" in exactly the state
    where nothing is known, so the question is asked of the record instead.
    """
    ctx, group, plate, _parent = panel
    before = list(group["wells"])
    ctx.state.feature_slice("home").sample_map_layer = ""
    plate.setSelectedWellIds(["A01", "A02"])
    plate.selectionDragFinished.emit()
    assert group["wells"] == before
