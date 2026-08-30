"""Sample variables: the roll-up that refuses to guess, and a block that lies.

The rule this file exists for is one line of §7: **a well added to a group after
the variables were filled has no value, and must not inherit one.** Everything
about the design follows from it — three roll-up outcomes rather than two, an
assignment refused rather than stored when its value does not exist, a pruned
value leaving wells unassigned rather than dangling.

The failure it prevents is not a crash. It is a bar labelled `1 µM` built partly
from wells nobody ever gave a dose to, which looks exactly like a bar that is
correct.

Pure lane — no Qt, no dataset, no filesystem.
"""

from __future__ import annotations

import pathlib

import pytest

from well_viewer.domain import sample_variables as sv


def _dose(*labels):
    return sv.Variable(
        id="dose", name="Dose", kind=sv.NUMBER, unit="µM",
        values=tuple(sv.VariableValue(id=f"d{i}", label=lab, number=float(lab))
                     for i, lab in enumerate(labels)),
    )


def _line():
    return sv.Variable(
        id="line", name="Cell line", kind=sv.TEXT,
        values=(sv.VariableValue("h", "HeLa"), sv.VariableValue("u", "U2OS")),
    )


@pytest.fixture
def plate():
    """Two variables, and A01–A04 filled with two doses two wells each."""
    vset = sv.VariableSet(variables=(_dose("0", "1"), _line()))
    return sv.fill(vset, "dose", ["A01", "A02", "A03", "A04"], repeat=2)


# ── the rule that must not be softened ──────────────────────────────────────

def test_wells_that_agree_roll_up_to_their_value(plate):
    assert plate.by_id("dose").display_label(
        sv.roll_up(plate, "dose", ["A01", "A02"])) == "0 µM"


def test_wells_that_disagree_roll_up_to_mixed(plate):
    assert sv.roll_up(plate, "dose", ["A01", "A03"]) is sv.MIXED


def test_one_unassigned_well_makes_the_whole_set_unassigned(plate):
    """Seven wells saying 1 µM and one saying nothing is not a 1 µM group.

    This is the rule. A member built from that set would put an unannotated
    measurement under a dose it was never given, and the bar would look exactly
    like a correct one. So the set answers "no value" and the caller reports the
    shortfall.
    """
    assert sv.roll_up(plate, "dose", ["A01", "A02", "A99"]) is None


def test_unassigned_is_not_the_same_answer_as_mixed(plate):
    """Two distinct facts, and only one of them is the user's mistake.

    Collapsing them loses the only signal that a group was edited after its
    variables were filled — which is the situation this whole design is built
    around.
    """
    mixed = sv.roll_up(plate, "dose", ["A01", "A03"])
    missing = sv.roll_up(plate, "dose", ["A01", "A99"])
    assert mixed is not missing
    assert missing is None and mixed is sv.MIXED


def test_an_empty_set_of_wells_has_no_value(plate):
    assert sv.roll_up(plate, "dose", []) is None


def test_unassigned_wells_are_reported_not_filled(plate):
    assert sv.unassigned_wells(plate, "dose", ["A01", "A04", "B01", "B02"]) == [
        "B01", "B02"]


# ── assignment ──────────────────────────────────────────────────────────────

def test_assigning_a_value_the_variable_does_not_define_is_refused(plate):
    """Storing it would manufacture exactly the orphan state ``orphaned_wells``
    exists to detect, from inside the editor."""
    after = sv.assign(plate, "dose", ["B01"], "no-such-value")
    assert after.value_id_for("dose", "B01") is None
    assert sv.orphaned_wells(after, "dose") == []


def test_assigning_none_clears(plate):
    after = sv.assign(plate, "dose", ["A01"], None)
    assert after.value_id_for("dose", "A01") is None
    assert after.value_id_for("dose", "A02") is not None, "it cleared its neighbour"


def test_assign_does_not_mutate_the_set_it_was_given(plate):
    before = dict(plate.assignments["dose"])
    sv.assign(plate, "dose", ["A01"], "d1")
    assert plate.assignments["dose"] == before


def test_the_two_variables_do_not_share_a_map(plate):
    after = sv.assign(plate, "line", ["A01"], "h")
    assert after.value_id_for("dose", "A01") == "d0"
    assert after.value_id_for("line", "A01") == "h"


# ── removing a value ────────────────────────────────────────────────────────

def test_removing_a_value_leaves_its_wells_unassigned_rather_than_dangling(plate):
    """So removing a dose tells the user what it cost them.

    Keeping the pointer would leave a set that looks complete and resolves to
    nothing at plot time — the shortfall arriving as an empty bar three screens
    later instead of as a count on the tab that caused it.
    """
    kept = plate.by_id("dose").values[:1]
    after = sv.prune_values(plate, "dose", kept)

    assert sv.unassigned_wells(after, "dose", ["A01", "A03"]) == ["A03"]
    assert sv.orphaned_wells(after, "dose") == []
    assert after.value_id_for("dose", "A01") == "d0", "it dropped a live value too"


def test_removing_a_variable_takes_its_assignments_with_it(plate):
    after = sv.remove_variable(plate, "dose")
    assert after.by_id("dose") is None
    assert "dose" not in after.assignments


# ── the fill row ────────────────────────────────────────────────────────────

def test_fill_cycles_values_over_the_wells_in_the_order_given():
    vset = sv.VariableSet(variables=(_dose("0", "1", "10"),))
    filled = sv.fill(vset, "dose", [f"A{i:02d}" for i in range(1, 7)], repeat=2)
    labels = [filled.by_id("dose").display_label(filled.value_id_for("dose", w))
              for w in [f"A{i:02d}" for i in range(1, 7)]]
    assert labels == ["0 µM", "0 µM", "1 µM", "1 µM", "10 µM", "10 µM"]


def test_fill_wraps_rather_than_leaving_the_tail_unassigned():
    """A plate is rarely an exact multiple of the value count.

    Wrapping is visible on the plate and correctable; four unassigned columns
    with no explanation is neither.
    """
    vset = sv.VariableSet(variables=(_dose("0", "1"),))
    filled = sv.fill(vset, "dose", [f"A{i:02d}" for i in range(1, 7)], repeat=1)
    assert sv.unassigned_wells(filled, "dose",
                              [f"A{i:02d}" for i in range(1, 7)]) == []


def test_this_module_knows_nothing_about_plate_geometry_or_Qt():
    """Ordering the wells for a fill is the caller's job.

    Holding a notion of "along columns" here would put a second definition of
    it beside ``domain/plate_layout.py``'s, and the two would disagree the first
    time either changed. Asserted on the imports rather than by grepping the
    prose, which legitimately says the words while explaining the rule.
    """
    import ast

    from tests._repo_scan import parse_source

    tree = parse_source(pathlib.Path(sv.__file__))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = sorted(m for m in imported
                       if m.startswith("PySide6") or "plate_layout" in m
                       or m.startswith("well_viewer.features"))
    assert not forbidden, (
        f"{forbidden} — this module is the Qt-free, geometry-free half of the "
        "feature, and the fill's ordering is the caller's to decide"
    )


# ── the block is untrusted ──────────────────────────────────────────────────

def test_the_round_trip_is_exact(plate):
    assert sv.from_block(sv.to_block(plate)) == plate


@pytest.mark.parametrize("block", [
    None, "", 17, [], {},
    {"variables": "not a list"},
    {"variables": [{"name": "no id"}]},
    {"variables": [{"id": "v", "name": "   "}]},
])
def test_an_unreadable_block_is_an_empty_set_and_never_an_exception(block):
    assert sv.from_block(block) == sv.VariableSet()


def test_one_malformed_variable_does_not_erase_the_others():
    block = {"variables": [
        {"id": "a", "name": "A", "kind": "text", "values": []},
        {"id": None, "name": "broken"},
        {"id": "b", "name": "B", "kind": "text", "values": []},
    ]}
    assert [v.id for v in sv.from_block(block).variables] == ["a", "b"]


def test_an_unknown_kind_reads_as_text_rather_than_dropping_the_variable():
    block = {"variables": [{"id": "a", "name": "A", "kind": "categorical",
                            "values": []}]}
    assert sv.from_block(block).by_id("a").kind == sv.TEXT


def test_a_unit_on_a_text_variable_is_dropped():
    """Only a number carries a unit; ``HeLa µM`` is not a label anyone meant."""
    block = {"variables": [{"id": "a", "name": "A", "kind": "text",
                            "unit": "µM", "values": []}]}
    assert sv.from_block(block).by_id("a").unit == ""


def test_a_duplicated_value_id_keeps_the_first_and_drops_the_rest():
    """Ids address values, so a duplicate makes every assignment ambiguous."""
    block = {"variables": [{"id": "a", "name": "A", "kind": "text", "values": [
        {"id": "x", "label": "first"}, {"id": "x", "label": "second"}]}]}
    values = sv.from_block(block).by_id("a").values
    assert [(v.id, v.label) for v in values] == [("x", "first")]


def test_an_assignment_to_an_undefined_value_is_dropped_on_read():
    """The well then reads as unassigned — ringed and counted — rather than
    resolving to nothing three screens later at plot time."""
    block = {
        "variables": [{"id": "a", "name": "A", "kind": "text",
                       "values": [{"id": "x", "label": "X"}]}],
        "assignments": {"a": {"A01": "x", "A02": "gone"}},
    }
    vset = sv.from_block(block)
    assert vset.value_id_for("a", "A01") == "x"
    assert vset.value_id_for("a", "A02") is None
    assert sv.orphaned_wells(vset, "a") == []


def test_a_boolean_is_not_accepted_as_a_number():
    """``isinstance(True, int)`` is True in Python, which is how a stray flag
    becomes a dose of 1."""
    block = {"variables": [{"id": "a", "name": "A", "kind": "number", "values": [
        {"id": "x", "label": "X", "number": True}]}]}
    assert sv.from_block(block).by_id("a").values[0].number is None


def test_a_number_variable_may_carry_a_value_that_is_not_a_number():
    """A vehicle row in a dose series is a real plate. It is kept, with
    ``number=None``, and the plot decides what to do with it — reading it as
    zero would put it on the axis at a dose that means something else."""
    block = {"variables": [{"id": "a", "name": "A", "kind": "number", "values": [
        {"id": "v", "label": "vehicle"}, {"id": "x", "label": "1", "number": 1.0}]}]}
    values = sv.from_block(block).by_id("a").values
    assert [(v.label, v.number) for v in values] == [("vehicle", None), ("1", 1.0)]


def test_the_value_order_survives_the_round_trip():
    """It is the user's order, and a plot's x axis and legend read it — a dose
    series stored as a set is a curve drawn in an arbitrary direction."""
    variable = _dose("10", "0", "1")
    vset = sv.VariableSet(variables=(variable,))
    back = sv.from_block(sv.to_block(vset)).by_id("dose")
    assert [v.label for v in back.values] == ["10", "0", "1"]


def test_a_label_for_a_value_that_no_longer_exists_is_empty_not_the_raw_id(plate):
    """An id is internal; showing one tells the user nothing."""
    assert plate.by_id("dose").display_label("no-such-value") == ""


# ── the block on disk, and the dataset swap ─────────────────────────────────

def _ctx(pipeline_info=None, data_dir=None):
    import types

    from well_viewer.core.selection_state import SelectionState

    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(data_dir=data_dir,
                                          pipeline_info=pipeline_info or {}),
            selection=SelectionState(),
        ),
    )


def test_a_dataset_with_no_variables_clears_the_previous_plates(plate, tmp_path):
    """The bug ``persistence/ratios.py`` records, one layer up.

    Returning early on a missing block left the last dataset's ratios live as
    virtual channels across a swap, so every plot described work done on a plate
    that was no longer open. Variables fail the same way and worse: a plot
    grouped by `Dose` would keep drawing the old plate's doses over the new
    plate's numbers, under a masthead naming the new dataset.
    """
    from well_viewer.persistence import sample_variables as ps

    ctx = _ctx(pipeline_info={}, data_dir=tmp_path)
    ctx.state.selection.variables = plate          # the previous plate's
    ps.load_from_data_dir(ctx)

    assert ctx.state.selection.variables == sv.VariableSet(), (
        "the previous dataset's variables survived a swap to a plate that "
        "defines none"
    )


def test_a_dataset_that_has_variables_hydrates_them(plate, tmp_path):
    from well_viewer.persistence import sample_variables as ps

    ctx = _ctx(pipeline_info={ps.BLOCK_KEY: sv.to_block(plate)}, data_dir=tmp_path)
    ps.load_from_data_dir(ctx)
    assert ctx.state.selection.variables == plate


def test_loading_without_a_dataset_leaves_the_state_alone(plate):
    """No data directory is "nothing has been opened", not "it is empty"."""
    from well_viewer.persistence import sample_variables as ps

    ctx = _ctx(data_dir=None)
    ctx.state.selection.variables = plate
    ps.load_from_data_dir(ctx)
    assert ctx.state.selection.variables == plate


def test_saving_preserves_every_other_key_in_pipeline_info(plate, tmp_path):
    """The pipeline writes this file too — often from another process on another
    host — and a block-level rewrite that dropped its keys would take the run's
    own record of channels and timepoints with it."""
    import json

    from well_viewer.persistence import sample_variables as ps

    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({
        "schema": "plate1", "fluor_tokens": ["gfp", "dapi"],
        "available_timepoints": [0.0, 24.0],
        "sample_definitions": {"schema_version": 2, "selections": []},
    }))

    ctx = _ctx(data_dir=tmp_path)
    ctx.state.selection.variables = plate
    ps.save_to_data_dir(ctx)

    written = json.loads(info.read_text())
    assert written["fluor_tokens"] == ["gfp", "dapi"]
    assert written["available_timepoints"] == [0.0, 24.0]
    assert written["sample_definitions"] == {"schema_version": 2, "selections": []}
    assert sv.from_block(written[ps.BLOCK_KEY]) == plate


def test_saving_without_a_dataset_is_a_silent_no_op(plate):
    from well_viewer.persistence import sample_variables as ps

    ctx = _ctx(data_dir=None)
    ctx.state.selection.variables = plate
    ps.save_to_data_dir(ctx)  # must not raise


def test_saving_where_there_is_no_pipeline_info_costs_the_write_and_nothing_else(
        plate, tmp_path):
    """A dataset folder the pipeline never wrote to. The variables stay in
    memory; taking the window down over it would be the worse trade."""
    from well_viewer.persistence import sample_variables as ps

    ctx = _ctx(data_dir=tmp_path)
    ctx.state.selection.variables = plate
    ps.save_to_data_dir(ctx)
    assert ctx.state.selection.variables == plate


def test_the_domain_is_registered_so_a_load_actually_hydrates_it():
    """A store nothing calls is a store that is always empty (§7.6).

    Asserted on the registry and on the load path together: registering without
    the call in ``load_controller`` leaves it dead, and the call without the
    registration raises.
    """
    import ast

    from tests._repo_scan import parse_source
    from well_viewer.persistence.registry import build_persistence_registry

    registry = build_persistence_registry(_ctx())
    assert "sample_variables" in registry._save
    assert "sample_variables" in registry._load

    source = ast.dump(parse_source(
        pathlib.Path("well_viewer/core/load_controller.py")))
    assert "'sample_variables'" in source, (
        "load_controller never asks the registry to load this domain, so the "
        "variable set is empty on every dataset the user opens"
    )


# ── the definition editor's semantics ───────────────────────────────────────
#
# In the domain module rather than in the widgets, so every rule below is driven
# by a test rather than by clicking. §7.6: a control that is offered is not a
# control that works, and the way to keep the two together is to put the
# semantics where a fast-lane test can reach them.

@pytest.mark.parametrize("label, expected", [
    ("1", 1.0), ("0", 0.0), ("0.5", 0.5), ("  10  ", 10.0),
    ("1 µM", 1.0), ("10%", 10.0), ("1e-3", 0.001), ("-2", -2.0),
    ("0,5", 0.5),                       # a comma decimal, as typed in much of Europe
    ("vehicle", None), ("", None), ("µM 1", None), ("n/a", None),
])
def test_a_dose_is_read_the_way_it_gets_typed(label, expected):
    assert sv.parse_number(label) == expected


def test_an_unreadable_label_is_not_read_as_zero():
    """Zero is a dose. A vehicle row read as 0.0 is plotted at the origin as
    though it were a measured concentration — a fabricated number on a real
    measurement, which is the one thing this module must never do."""
    assert sv.parse_number("vehicle") is None
    assert sv.parse_number("0") == 0.0


def test_a_second_variable_of_the_same_name_is_renamed_not_refused():
    """Two `Dose` variables make ``by_name`` ambiguous and "Group by: Dose"
    meaningless, and the user cannot tell the chips apart. Renaming keeps the
    user's word; refusing mid-thought does not."""
    vset, _ = sv.new_variable(sv.VariableSet(), "Dose")
    vset, second = sv.new_variable(vset, "Dose")
    assert vset.by_id(second).name == "Dose 2"
    assert vset.by_name("Dose").id != second


def test_renaming_a_variable_keeps_its_id_so_nothing_is_disturbed(plate):
    after = sv.rename_variable(plate, "dose", "Concentration")
    assert after.by_id("dose").name == "Concentration"
    assert after.assignments["dose"] == plate.assignments["dose"]


def test_renaming_a_value_rewrites_every_well_holding_it(plate):
    """The whole reason values carry ids: correcting `1uM` to `1 µM` after a
    plate is filled is a rename, not a re-fill."""
    value_id = plate.by_id("dose").values[1].id
    after = sv.rename_value(plate, "dose", value_id, "1 µM")

    assert after.by_id("dose").value_by_id(value_id).label == "1 µM"
    assert after.value_id_for("dose", "A03") == value_id, "a well was re-pointed"
    assert after.by_id("dose").value_by_id(value_id).number == 1.0


def test_a_value_that_duplicates_an_existing_label_is_not_added(plate):
    """Two `1` rows split one dose into two series holding half the wells each,
    and neither is visibly wrong."""
    before = len(plate.by_id("dose").values)
    after = sv.add_value(plate, "dose", "1")
    assert len(after.by_id("dose").values) == before
    assert len(sv.add_value(plate, "dose", " 1 ").by_id("dose").values) == before


def test_renaming_a_value_onto_another_values_label_is_refused(plate):
    values = plate.by_id("dose").values
    after = sv.rename_value(plate, "dose", values[0].id, values[1].label)
    assert after.by_id("dose").value_by_id(values[0].id).label == values[0].label


def test_switching_a_text_variable_to_number_reparses_its_values():
    """A list typed before the kind was switched becomes usable as an axis
    without being retyped."""
    vset, vid = sv.new_variable(sv.VariableSet(), "Dose")
    for label in ("0", "vehicle", "10"):
        vset = sv.add_value(vset, vid, label)
    assert all(v.number is None for v in vset.by_id(vid).values)

    vset = sv.set_kind(vset, vid, sv.NUMBER, unit="µM")
    numbers = [v.number for v in vset.by_id(vid).values]
    assert numbers == [0.0, None, 10.0], (
        "the unreadable label was dropped or read as a number"
    )
    assert vset.by_id(vid).unit == "µM"


def test_switching_a_number_variable_to_text_drops_the_unit(plate):
    """`HeLa µM` is not a label anyone meant."""
    after = sv.set_kind(plate, "dose", sv.TEXT)
    assert after.by_id("dose").unit == ""
    assert all(v.number is None for v in after.by_id("dose").values)
    assert len(after.by_id("dose").values) == len(plate.by_id("dose").values)


def test_a_text_variable_cannot_be_given_a_unit(plate):
    assert sv.set_unit(plate, "line", "µM").by_id("line").unit == ""


def test_moving_a_value_clamps_rather_than_wraps(plate):
    """A value that jumped from the top of the list to the bottom because a
    button was pressed once too often is a reorder nobody asked for."""
    first = plate.by_id("dose").values[0].id
    assert [v.id for v in sv.move_value(plate, "dose", first, -1)
            .by_id("dose").values] == [v.id for v in plate.by_id("dose").values]

    moved = sv.move_value(plate, "dose", first, +1)
    assert [v.id for v in moved.by_id("dose").values][1] == first


def test_removing_a_value_through_the_editor_unassigns_its_wells(plate):
    value_id = plate.by_id("dose").values[0].id
    after = sv.remove_value(plate, "dose", value_id)
    assert after.by_id("dose").value_by_id(value_id) is None
    assert sv.unassigned_wells(after, "dose", ["A01", "A03"]) == ["A01"]
    assert sv.orphaned_wells(after, "dose") == []


def test_every_editing_call_leaves_the_set_it_was_given_alone(plate):
    """Pure functions, so an editor can build a candidate and discard it."""
    import copy

    before = copy.deepcopy(plate)
    value_id = plate.by_id("dose").values[0].id
    sv.rename_variable(plate, "dose", "X")
    sv.set_kind(plate, "dose", sv.TEXT)
    sv.add_value(plate, "dose", "99")
    sv.rename_value(plate, "dose", value_id, "99")
    sv.move_value(plate, "dose", value_id, 1)
    sv.remove_value(plate, "dose", value_id)
    assert plate == before
