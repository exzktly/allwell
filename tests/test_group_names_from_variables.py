"""A replicate group is named after what it *is*, once the plate says.

``make_replicate_pairs`` names a group after the wells in it — ``A01/A02`` —
which locates the group and describes nothing. When sample variables have been
declared, the plate already holds the description: the wells of one group carry
one cell line and one dose, and ``HeLa · 10 µM`` is the name the user would
otherwise type ninety-six times.

Two halves are worth pinning and only one of them is the happy path.

*What the name says.* Every assertion below names the **exact** string, never
"it changed" or "it is not the well tokens": the mistake this feature is one
mis-ordered loop away from is composing the right labels in the wrong order, and
"the name mentions HeLa" is satisfied by every one of those orders (§7.8 #1).
The fixture is built so a naive iteration over ``assignments`` — a dict whose
insertion order here is deliberately the reverse of the user's variable order —
produces a different string from the right one (§7.8 #2).

*What the name refuses to say.* A group whose wells disagree about the dose is
not the ``HeLa`` group, and on a plate whose only variable is `Cell line` those
two are the same six characters. So a partial description carries the wells that
produced it and a complete one does not, and the test that matters is the pair:
either alone passes against an implementation that always appends, or never
does.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from review.domain import sample_variables as sv
from review.domain.batch_models import ReplicateSet
from review.domain.selections_model import (
    active_replicate_sets, from_block, make_selection, replicate_display_label,
    to_block,
)
from review.features.home.groups.controller import (
    make_replicate_pairs, name_sets_from_variables, rep_quick_pairs,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


# ── fixtures ────────────────────────────────────────────────────────────────

def _dose(*, unit="µM", labels=(("1", 1.0), ("10", 10.0))):
    return sv.Variable(id="dose", name="Dose", kind=sv.NUMBER, unit=unit,
                       values=tuple(sv.VariableValue(f"d{i}", lab, num)
                                    for i, (lab, num) in enumerate(labels)))


def _line():
    return sv.Variable(id="line", name="Cell line", kind=sv.TEXT, unit="",
                       values=(sv.VariableValue("c0", "HeLa"),
                               sv.VariableValue("c1", "U2OS")))


def _vset(*variables, **assignments):
    """A set whose ``assignments`` dict is keyed in the **reverse** of the order
    the variables are declared in.

    Deliberate, and it is what makes the ordering assertions bite: an
    implementation that walked ``vset.assignments`` rather than
    ``vset.variables`` would compose ``10 µM · HeLa`` here, and against a fixture
    built in declaration order both spellings pass.
    """
    return sv.VariableSet(variables=tuple(variables),
                          assignments={k: dict(v) for k, v
                                       in reversed(list(assignments.items()))})


#: Four wells: one cell line throughout, two doses two wells each. So a pair
#: within a row resolves both variables, and a pair *across* the doses resolves
#: only the cell line — the partial case, on a plate where the complete case for
#: those same wells exists and is a different string.
_ASSIGN = {
    "line": {"A01": "c0", "A02": "c0", "A03": "c0", "A04": "c0"},
    "dose": {"A01": "d0", "A02": "d0", "A03": "d1", "A04": "d1"},
}


def _full():
    return _vset(_line(), _dose(), **_ASSIGN)


# ── what the name says ──────────────────────────────────────────────────────

def test_a_group_is_named_for_every_variable_its_wells_agree_on():
    assert sv.name_for_wells(_full(), ["A01", "A02"]) == "HeLa · 1 µM"


def test_the_values_come_in_the_users_variable_order_not_the_assignment_order():
    """Reverse the *declared* order and the name reverses with it.

    The companion to the fixture note above: that one proves the assignment
    order is not read, this one proves the variable order is — a hard-coded
    ``f"{line} · {dose}"`` passes the test above and fails here.
    """
    reversed_decl = _vset(_dose(), _line(), **_ASSIGN)
    assert sv.name_for_wells(reversed_decl, ["A01", "A02"]) == "1 µM · HeLa"


def test_a_numeric_value_carries_its_unit():
    no_unit = _vset(_line(), _dose(unit=""), **_ASSIGN)
    assert sv.name_for_wells(no_unit, ["A01", "A02"]) == "HeLa · 1"


def test_a_plate_with_no_variables_names_nothing():
    assert sv.name_for_wells(sv.VariableSet(), ["A01", "A02"]) == ""


def test_no_wells_names_nothing():
    assert sv.name_for_wells(_full(), []) == ""


def test_a_variable_no_well_was_ever_given_names_nothing_by_itself():
    empty = _vset(_line(), assignments={})
    assert sv.name_for_wells(empty, ["A01", "A02"]) == ""


# ── what the name refuses to say ────────────────────────────────────────────

def test_wells_that_disagree_about_a_variable_carry_their_wells():
    """``A01`` and ``A03`` are both HeLa and are two different doses.

    Not ``HeLa``: on a plate declaring only `Cell line` that is exactly what a
    completely-described group is called, and the two would be one string.
    """
    assert sv.name_for_wells(_full(), ["A01", "A03"]) == "HeLa (A01/A03)"


def test_a_completely_described_group_does_not_carry_its_wells():
    """The other half of the pair above — without it, "always append" passes."""
    assert sv.name_for_wells(_full(), ["A03", "A04"]) == "HeLa · 10 µM"


def test_an_unfilled_well_makes_the_description_partial():
    partial = _vset(_line(), _dose(),
                    line=_ASSIGN["line"],
                    dose={"A01": "d0"})            # A02 never filled
    assert sv.name_for_wells(partial, ["A01", "A02"]) == "HeLa (A01/A02)"


def test_the_mixed_sentinel_never_reaches_a_name():
    name = sv.name_for_wells(_full(), ["A01", "A03"])
    assert sv.MIXED not in name


# ── a batch of groups, kept distinct ────────────────────────────────────────

def _rows():
    """Two rows, one dose each, on a plate whose only variable is the dose.

    Both rows compose ``1 µM``, which is the collision this fixture exists for.
    """
    dose_only = sv.VariableSet(
        variables=(_dose(),),
        assignments={"dose": {"A01": "d0", "A02": "d0",
                              "B01": "d0", "B02": "d0"}})
    return dose_only, [["A01", "A02"], ["B01", "B02"]]


def test_two_groups_that_compose_one_name_each_carry_their_wells():
    vset, sets = _rows()
    assert sv.names_for_well_sets(vset, sets) == ["1 µM (A01/A02)",
                                                  "1 µM (B01/B02)"]


def test_names_that_do_not_collide_are_left_alone():
    assert sv.names_for_well_sets(_full(), [["A01", "A02"], ["A03", "A04"]]) \
        == ["HeLa · 1 µM", "HeLa · 10 µM"]


def test_a_name_that_is_both_partial_and_colliding_carries_its_wells_once():
    """One suffix, not two — ``HeLa (A01/A03) (A01/A03)`` answers nothing."""
    names = sv.names_for_well_sets(_full(), [["A01", "A03"], ["A02", "A04"]])
    assert names == ["HeLa (A01/A03)", "HeLa (A02/A04)"]


def test_a_set_the_variables_cannot_name_gets_an_empty_string():
    """So the caller can tell "no name" from a name, and keep its own."""
    assert sv.names_for_well_sets(sv.VariableSet(), [["A01"]]) == [""]


# ── the feature: naming the sets Quick Groups built ─────────────────────────

class _Coordinator:
    def __init__(self) -> None:
        self.rebuilt = 0

    def rebuild_all(self) -> None:
        self.rebuilt += 1


def _ctx(wells, vset=None, *, size=2, pair_dir="row", iter_order="row"):
    """A ctx shaped like the one ``rep_quick_pairs`` reads, and no more.

    The **real** ``SelectionState``, not a namespace carrying the four fields
    this path happens to read: ``sel_add`` reaches ``selected_wells`` two calls
    deep, and a stub shaped around what my own code touches is the one that
    cannot disagree with me (§7.8, face one).

    No ``window``: the confirm dialog is only reached when groups already exist,
    and a test that reached it would block for ever offscreen. That is why
    ``test_quick_groups_names_every_set_it_creates`` starts from an empty
    selection list.
    """
    from review.core.selection_state import SelectionState

    selection = SelectionState()
    if vset is not None:
        selection.variables = vset
    state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells},
                                well_labels={}),
        selection=selection,
    )
    return SimpleNamespace(
        state=state,
        runtime=SimpleNamespace(groups=SimpleNamespace(
            pair_dir=pair_dir, iter_order=iter_order, rep_size=size)),
        coordinator=_Coordinator(),
    )


def test_name_sets_from_variables_rewrites_the_generated_names():
    sets = make_replicate_pairs(["A01", "A02", "A03", "A04"], 2)
    assert [s.name for s in sets] == ["A01/A02", "A03/A04"]
    name_sets_from_variables(_ctx(_ASSIGN["line"], _full()), sets)
    assert [s.name for s in sets] == ["HeLa · 1 µM", "HeLa · 10 µM"]


def test_a_named_set_is_marked_verbatim():
    sets = make_replicate_pairs(["A01", "A02"], 2)
    name_sets_from_variables(_ctx(_ASSIGN["line"], _full()), sets)
    assert sets[0].name_is_verbatim is True


def test_a_plate_with_no_variables_keeps_the_well_tokens():
    sets = make_replicate_pairs(["A01", "A02", "A03", "A04"], 2)
    name_sets_from_variables(_ctx(_ASSIGN["line"], sv.VariableSet()), sets)
    assert [s.name for s in sets] == ["A01/A02", "A03/A04"]
    assert [s.name_is_verbatim for s in sets] == [False, False]


def test_a_context_with_no_variables_field_at_all_is_survivable():
    """A headless context carries only the slices its path reads.

    Built by hand rather than through :func:`_ctx`, which now uses the real
    ``SelectionState`` and therefore always has the field.
    """
    ctx = SimpleNamespace(state=SimpleNamespace(
        selection=SimpleNamespace(selections=[])))
    sets = make_replicate_pairs(["A01", "A02"], 2)
    name_sets_from_variables(ctx, sets)
    assert sets[0].name == "A01/A02"


# ── through the entry point Quick Groups actually calls (§7.8 #3) ───────────

def test_quick_groups_names_every_set_it_creates():
    ctx = _ctx(["A01", "A02", "A03", "A04"], _full())
    rep_quick_pairs(ctx)
    assert [s["name"] for s in ctx.state.selection.selections] \
        == ["HeLa · 1 µM", "HeLa · 10 µM"]


def test_quick_groups_records_the_name_as_verbatim_on_the_selection():
    ctx = _ctx(["A01", "A02", "A03", "A04"], _full())
    rep_quick_pairs(ctx)
    assert [s.get("name_is_verbatim") for s in ctx.state.selection.selections] \
        == [True, True]


def test_quick_groups_on_an_unannotated_plate_still_names_by_well():
    ctx = _ctx(["A01", "A02", "A03", "A04"], sv.VariableSet())
    rep_quick_pairs(ctx)
    assert [s["name"] for s in ctx.state.selection.selections] \
        == ["A01/A02", "A03/A04"]
    assert not any("name_is_verbatim" in s
                   for s in ctx.state.selection.selections)


def test_a_group_spanning_two_doses_is_not_named_as_one_of_them():
    """Pair size 4 over a row holding two doses: one group, both doses.

    The failure this guards is the tempting one — take the first well's value —
    which would name it ``HeLa · 1 µM`` and hide two doses inside a group
    labelled as one.
    """
    ctx = _ctx(["A01", "A02", "A03", "A04"], _full(), size=4)
    rep_quick_pairs(ctx)
    assert [s["name"] for s in ctx.state.selection.selections] \
        == ["HeLa (A01/A02/A03/A04)"]


# ── the flag has to survive to the axis, or the name does not ───────────────

def _label_ctx(wells):
    return SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells},
                                well_labels={})))


def test_a_bare_number_group_name_reaches_the_axis_intact():
    """A `Dose` of ``10`` with no unit **is** the auto-generated-name pattern.

    Without the verbatim flag travelling from the selection to the
    ``ReplicateSet``, ``replicate_display_label`` reads this name as one the app
    invented and redraws it as ``A01,A02`` — the exact tokens the feature exists
    to replace, on a plate the user has fully annotated.
    """
    ctx = _ctx(["A01", "A02"], _vset(_dose(unit=""), dose={"A01": "d1",
                                                           "A02": "d1"}))
    rep_quick_pairs(ctx)
    sels = ctx.state.selection.selections
    assert [s["name"] for s in sels] == ["10"]
    rsets = active_replicate_sets(sels, {"A01": "A01", "A02": "A02"})
    assert replicate_display_label(_label_ctx(["A01", "A02"]), rsets[0]) == "10"


def test_a_generated_group_name_is_still_relabelled():
    """The other half: nothing here weakens the heuristic for names the app made."""
    rset = ReplicateSet("3", ["A01", "A02"])
    assert replicate_display_label(_label_ctx(["A01", "A02"]), rset) == "A01,A02"


def test_the_flag_survives_a_save_and_a_load():
    sel = make_selection(name="10", wells=["A01", "A02"], name_is_verbatim=True)
    reloaded, _cur, _labels, _notes, _groups = from_block(to_block([sel]))
    assert reloaded[0]["name_is_verbatim"] is True


def test_a_selection_that_never_claimed_verbatim_carries_no_such_key():
    """Stored only when true, so every existing on-disk selection is unchanged."""
    sel = make_selection(name="Control", wells=["A01"])
    assert "name_is_verbatim" not in sel


def test_renaming_a_group_hands_the_name_back_to_the_heuristic():
    """A name the user typed is theirs, and ``3`` typed by a user is generic."""
    from review.core.plot_state import PlotState
    from review.core.selection_controller import sel_rename

    ctx = _ctx(["A01", "A02"], _vset(_dose(unit=""), dose={"A01": "d1",
                                                           "A02": "d1"}))
    rep_quick_pairs(ctx)
    sel = ctx.state.selection.selections[0]
    # The real slice, not a stub of the two attributes this path happens to
    # touch: ``sel_rename`` walks the fold-change control references first, and
    # a SimpleNamespace holding only ``rename_member`` tests a rename that no
    # build performs (§7.8, face one).
    ctx.state.plot = PlotState()
    sel_rename(ctx, sel["id"], "3")
    assert "name_is_verbatim" not in sel


# ── the New Replicate Set dialog, driven rather than read (§7.6) ────────────

def _dialog_ctx(wells, vset, *, selections=None):
    """A ctx for the dialog: it reads the same slices, plus ``window``.

    ``window`` is ``None`` — a ``QDialog`` accepts a null parent, and the
    alternative is a real window, which is a full-app construction for a test
    about a text box.
    """
    ctx = _ctx(wells, vset)
    ctx.state.selection.selections = list(selections or [])
    ctx.window = None
    ctx.widgets = SimpleNamespace(get_or_none=lambda _n: None)
    return ctx


def _press_create(dlg) -> None:
    """Click the dialog's Create button — the control, not the callback.

    Found by its text rather than held from the builder, because a test given
    the button by the thing it is testing cannot notice a button that was never
    connected.
    """
    from PySide6.QtWidgets import QPushButton

    create = next(b for b in dlg.findChildren(QPushButton)
                  if b.text() == "Create")
    create.click()


def _dialog(ctx):
    """Build the real dialog and hand back it, its name box and its plate."""
    from review.features.home.groups.controller import build_rep_add_dialog

    built = build_rep_add_dialog(ctx)
    return built.dialog, built.name_edit, built.plate


def test_the_dialog_names_the_group_from_the_wells_picked_on_its_plate(qtbot):
    """Not "the field can be set" — the field *is* set, by selecting wells.

    The whole rule lives in a closure on ``plate.selectionChanged``; calling the
    naming helper directly would pass with that connection deleted, which is the
    defect §7.6 is about.
    """
    ctx = _dialog_ctx(["A01", "A02", "A03", "A04"], _full())
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    assert name_edit.text() == "Rep 1"
    plate.setSelectedWellIds(["A01", "A02"])
    assert name_edit.text() == "HeLa · 1 µM"


def test_the_offered_name_follows_a_changed_pick(qtbot):
    ctx = _dialog_ctx(["A01", "A02", "A03", "A04"], _full())
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["A01", "A02"])
    plate.setSelectedWellIds(["A03", "A04"])
    assert name_edit.text() == "HeLa · 10 µM"


def test_a_name_the_user_typed_is_never_overwritten(qtbot):
    ctx = _dialog_ctx(["A01", "A02", "A03", "A04"], _full())
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    name_edit.setText("Vehicle")
    plate.setSelectedWellIds(["A01", "A02"])
    assert name_edit.text() == "Vehicle"


def test_an_unannotated_plate_leaves_the_fallback_name(qtbot):
    ctx = _dialog_ctx(["A01", "A02"], sv.VariableSet())
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["A01", "A02"])
    assert name_edit.text() == "Rep 1"


def test_creating_from_the_dialog_stores_the_composed_name(qtbot):
    """Press Create, and read what landed in the model.

    ``bare`` doses, so the ``name_is_verbatim`` half is load-bearing here too:
    without it the group this dialog just named ``10`` is drawn as ``A01,A02``.
    """
    vset = _vset(_dose(unit=""), dose={"A01": "d1", "A02": "d1"})
    ctx = _dialog_ctx(["A01", "A02"], vset)
    dlg, _name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["A01", "A02"])
    _press_create(dlg)
    sel = ctx.state.selection.selections[0]
    assert sel["name"] == "10"
    assert sel["name_is_verbatim"] is True


def test_a_typed_name_is_not_marked_verbatim(qtbot):
    """The user's own ``3`` goes back under the heuristic every typed name has."""
    vset = _vset(_dose(unit=""), dose={"A01": "d1", "A02": "d1"})
    ctx = _dialog_ctx(["A01", "A02"], vset)
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["A01", "A02"])
    name_edit.setText("3")
    _press_create(dlg)
    sel = ctx.state.selection.selections[0]
    assert sel["name"] == "3"
    assert "name_is_verbatim" not in sel


# ── the flag reaches every seam that builds a set from a selection ──────────
#
# Three carry sites, and a review found two of them unguarded: deleting either
# left the whole lane green. They are not one test's worth of the same fact —
# each feeds a different half of the app, and the batch one is the half that
# would have disagreed with the tab it mirrors.

def _flagged_selection(name="10"):
    return make_selection(name=name, wells=["A01", "A02"], name_is_verbatim=True)


def test_the_hidden_and_loaded_seam_carries_the_flag_too():
    """``replicate_sets_loaded`` is what Batch Export and the Heat Map pool through.

    Guarded separately from ``active_replicate_sets`` because the two are
    different functions feeding different figures: with only the first one
    tested, a `Dose` group called ``10`` read ``10`` on the tab and ``A01,A02``
    on the batch-exported figure of the same plate — the batch-disagrees-with-
    the-tab failure, from a flag dropped in one of two near-identical lines.
    """
    from review.domain.selections_model import replicate_sets_loaded

    rsets = replicate_sets_loaded([_flagged_selection()],
                                  {"A01": "A01", "A02": "A02"})
    assert rsets[0].name_is_verbatim is True
    assert replicate_display_label(_label_ctx(["A01", "A02"]), rsets[0]) == "10"


def test_a_hidden_group_keeps_its_flag():
    """The seam's whole reason for existing is that it includes hidden groups."""
    from review.domain.selections_model import replicate_sets_loaded

    sel = _flagged_selection()
    sel["hidden"] = True
    rsets = replicate_sets_loaded([sel], {"A01": "A01", "A02": "A02"})
    assert [r.name_is_verbatim for r in rsets] == [True]


def _import_ctx():
    from review.core.plot_state import PlotState

    ctx = _ctx(["A01", "A02"], sv.VariableSet())
    ctx.state.plot = PlotState()
    return ctx


def test_importing_a_selection_carries_the_flag_from_the_file():
    """An imported group named after a dose on its own plate keeps that name.

    Drives ``merge_imported_selections`` — the loop itself. The first version of
    this test called ``sel_add`` with the flag already extracted, which is one
    step *past* the line under review: deleting the whole loop's flag argument
    left it green, so it measured nothing about the thing the review flagged.
    That is why the loop was split out from its file dialog.
    """
    from review.core.selection_controller import merge_imported_selections

    ctx = _import_ctx()
    added = merge_imported_selections(ctx, {"selections": [
        {"name": "10", "wells": ["A01", "A02"], "name_is_verbatim": True}]})
    assert added == 1
    sel = ctx.state.selection.selections[0]
    assert sel["name_is_verbatim"] is True
    rsets = active_replicate_sets([sel], {"A01": "A01", "A02": "A02"})
    assert replicate_display_label(_label_ctx(["A01", "A02"]), rsets[0]) == "10"


def test_importing_a_selection_that_never_claimed_the_flag_does_not_gain_one():
    """The other half — without it, "always true" passes the test above."""
    from review.core.selection_controller import merge_imported_selections

    ctx = _import_ctx()
    merge_imported_selections(ctx, [{"name": "Control", "wells": ["A01"]}])
    assert "name_is_verbatim" not in ctx.state.selection.selections[0]


def test_a_payload_with_no_selections_is_told_apart_from_one_that_added_none():
    """``None`` and ``0`` are different sentences for the user, not one.

    The zero case uses a non-dict entry, which the loop skips. A dict with no
    recognised keys does *not* give zero — it is added as an empty group called
    ``Imported`` — which is pre-existing behaviour this change does not touch,
    and which the first version of this test asserted away because the expected
    value was guessed rather than read off the loop.
    """
    from review.core.selection_controller import merge_imported_selections

    assert merge_imported_selections(_import_ctx(), {"selections": []}) is None
    assert merge_imported_selections(_import_ctx(), ["not a selection"]) == 0


def test_the_statistics_tabs_own_copy_of_the_rule_agrees_with_the_domains():
    """``deserialize_stats_groups`` inlines ``replicate_sets_loaded``'s rule.

    Two copies of one rule grow a difference — this is the file that says so
    about itself, in the comment directly above the line.
    """
    from review.features.stats.controller import deserialize_stats_groups

    ctx = _ctx(["A01", "A02"], sv.VariableSet())
    ctx.state.selection.selections = [_flagged_selection()]
    groups = deserialize_stats_groups(
        ctx, [{"name": "G", "members": ["10"], "solo_wells": []}])
    assert [m.name_is_verbatim for m in groups[0].members] == [True]


# ── a variable nobody has filled in is not this group's shortfall ───────────

def test_an_empty_variable_does_not_put_wells_on_every_group():
    """Clicking + Variable before typing its values is the normal order.

    Without this, declaring a `Timepoint` and not filling it made every group on
    the plate carry a wells suffix at once — under a manual that explains the
    suffix as "this group spans two doses".
    """
    empty = sv.Variable(id="tp", name="Timepoint", kind=sv.NUMBER, unit="h")
    vset = _vset(_dose(), empty, dose=_ASSIGN["dose"])
    assert sv.names_for_well_sets(vset, [["A01", "A02"], ["A03", "A04"]]) \
        == ["1 µM", "10 µM"]


def test_a_variable_declared_but_never_put_on_a_well_is_the_same_case():
    declared = sv.Variable(id="ln", name="Cell line", kind=sv.TEXT,
                           values=(sv.VariableValue("c0", "HeLa"),))
    vset = _vset(_dose(), declared, dose=_ASSIGN["dose"])
    assert sv.name_for_wells(vset, ["A01", "A02"]) == "1 µM"


def test_a_variable_filled_for_some_wells_still_reports_this_groups_gap():
    """The signal the rule above must not have blunted.

    `Cell line` reaches A01 and nothing else, so it *can* answer for someone —
    and this group holding an unfilled well is a real fact about this group.
    """
    partly = _vset(_dose(), _line(), dose=_ASSIGN["dose"],
                   line={"A01": "c0"})
    assert sv.name_for_wells(partly, ["A01", "A02"]) == "1 µM (A01/A02)"


# ── one plate, one answer about a clashing name ────────────────────────────

def test_the_dialog_disambiguates_against_the_groups_already_there(qtbot):
    """The + Add flow has no batch to clash within, so it clashes with the plate.

    Left to ``make_selection``, this fell through to ``_unique_name`` and came
    out ``10 µM_v2`` while Quick Replicates on the same plate produced
    ``10 µM (A01/A02)`` — two flows, one plate, two different answers, and a
    manual sentence that stated only one of them.
    """
    vset = _vset(_dose(), dose={"A01": "d0", "A02": "d0",
                                "B01": "d0", "B02": "d0"})
    existing = make_selection(name="1 µM", wells=["A01", "A02"])
    ctx = _dialog_ctx(["A01", "A02", "B01", "B02"], vset,
                      selections=[existing])
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["B01", "B02"])
    assert name_edit.text() == "1 µM (B01/B02)"
    _press_create(dlg)
    assert ctx.state.selection.selections[-1]["name"] == "1 µM (B01/B02)"


def test_a_name_no_group_holds_is_left_alone(qtbot):
    """The other half — without it, "always suffix" passes the test above."""
    vset = _vset(_dose(), dose={"A01": "d0", "A02": "d0",
                                "B01": "d1", "B02": "d1"})
    existing = make_selection(name="1 µM", wells=["A01", "A02"])
    ctx = _dialog_ctx(["A01", "A02", "B01", "B02"], vset,
                      selections=[existing])
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["B01", "B02"])
    assert name_edit.text() == "10 µM"


def test_batch_exports_scope_swap_keeps_the_flag_on_its_rebuilt_selections():
    """Batch Export re-runs the tab collectors against selections it rebuilds.

    ``_app_group_scope`` swaps ``ctx.state.selection.selections`` for one
    ``make_selection`` per live member so "a batch group behaves exactly like
    the tab" — its own words. The collectors then read those rebuilt dicts back
    through ``active_replicate_sets``, so a flag left behind in the rebuild
    relabelled a variable-named group on the exported figure and not on the
    tab's. Within one export, even: the line and bar panels read ``grp.members``
    directly and kept the name, so a `Dose` group came out ``10`` on one panel
    and ``A01,A02`` on the next.

    Driven through ``make_selection`` the way the scope does rather than through
    a built Batch Export panel, and pinned to the *label* — the outbound value
    (§7.4 #1) — so it fails on the substitution rather than on the field.
    """
    member = ReplicateSet("10", ["A01", "A02"], name_is_verbatim=True)
    used_names: set = set()
    used_ids: set = set()
    rebuilt = [make_selection(
        name=member.name, wells=list(member.wells), source="rep_set",
        name_is_verbatim=getattr(member, "name_is_verbatim", False),
        used_names=used_names, used_ids=used_ids, fallback_color_idx=0)]
    rsets = active_replicate_sets(rebuilt, {"A01": "A01", "A02": "A02"})
    ctx = _label_ctx(["A01", "A02"])
    assert replicate_display_label(ctx, rsets[0]) == "10"
    assert replicate_display_label(ctx, member) == "10"


def test_the_batch_scope_swap_really_passes_the_flag():
    """The source line itself, so the test above cannot pass a fixture-only fix.

    The test above rebuilds the selection the way the panel does; this one reads
    the panel's own call, because a rule reproduced in a test and absent from the
    code is the shape §7.8 is about.
    """
    import ast

    from tests._repo_scan import parse_source

    tree = parse_source(
        REPO_ROOT / "review/features/batch_export/base_panel.py")
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "make_selection"]
    assert calls, "make_selection is no longer called here — re-point this guard"
    for call in calls:
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        source = getattr(kwargs.get("source"), "value", None)
        if source == "well":
            # A solo well is named after the well token itself, so there is no
            # composed name to protect and the relabelling heuristic — which
            # matches ``1`` / ``Rep 2`` / ``Group 3`` and never ``A01`` — cannot
            # touch it. Exempted by what it builds rather than by its line
            # number, so moving it keeps the exemption and adding a *fourth*
            # call that rebuilds a group does not inherit one.
            continue
        assert "name_is_verbatim" in kwargs, (
            "a make_selection in base_panel.py rebuilds a group without "
            "name_is_verbatim; the collectors re-read these dicts through "
            "active_replicate_sets, so the exported figure would relabel a "
            "variable-named group that the tab draws correctly")


def test_clearing_the_name_box_does_not_switch_the_offer_off(qtbot):
    """Deleting the text is "not that one", not a name the user chose.

    Treating the empty string as typed meant no later pick refilled the box, it
    sat blank while wells were being selected, and Create stored ``Rep 1`` on a
    fully annotated plate.
    """
    ctx = _dialog_ctx(["A01", "A02", "A03", "A04"], _full())
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["A01", "A02"])
    name_edit.clear()
    plate.setSelectedWellIds(["A03", "A04"])
    assert name_edit.text() == "HeLa · 10 µM"


def test_creating_with_the_box_cleared_stores_the_composed_name(qtbot):
    ctx = _dialog_ctx(["A01", "A02"], _full())
    dlg, name_edit, plate = _dialog(ctx)
    qtbot.addWidget(dlg)
    plate.setSelectedWellIds(["A01", "A02"])
    name_edit.clear()
    _press_create(dlg)
    sel = ctx.state.selection.selections[0]
    assert sel["name"] == "HeLa · 1 µM"
    assert sel["name_is_verbatim"] is True


def test_rep_add_execs_the_dialog_the_builder_built(monkeypatch):
    """The split is this branch's structural change; nothing pinned it.

    Mutating ``rep_add``'s body to ``pass`` left the whole lane green — the
    dialog would simply never open, which no test could see.
    """
    from review.features.home.groups import controller as mod

    opened = []

    class _Spy:
        def exec(self):
            opened.append(True)
            return 0

    spy = _Spy()
    monkeypatch.setattr(
        mod, "build_rep_add_dialog",
        lambda ctx: mod.RepAddDialog(spy, object(), object()))
    mod.rep_add(_dialog_ctx(["A01"], _full()))
    assert opened == [True]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
