"""The state a per-sample control mapping lives in, and how it survives.

Three fields carry it — a mode, a ``member key -> ControlRef`` mapping, and
what a member that is itself a control draws as — and each of the three has a
failure mode that is invisible from the plot:

* the mode is a field rather than an absence, because emptying the control to
  mean "per member" makes every untaught caller draw **raw** values under a
  fold-change axis label;
* the mapping is a ``default_factory`` dict, because ``restore_defaults``
  builds a fresh instance and copies attributes across, so a bare mutable
  default would be one dict shared by every ``PlotState`` ever made;
* a rename has to move through all three places a group is named, and moving
  through fewer is worse than moving through none.
"""
from __future__ import annotations

from types import SimpleNamespace

from well_viewer.core.plot_state import PlotState
from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.state_defaults import restore_defaults
from well_viewer.persistence import view_state as vs


def _ctx(plot=None):
    return SimpleNamespace(state=SimpleNamespace(plot=plot or PlotState()))


# ── defaults and instance isolation ──────────────────────────────────────────

def test_the_defaults_are_todays_behaviour():
    ps = PlotState()
    assert ps.fc_control_mode == "single"
    assert ps.fc_control_map == {}
    assert ps.fc_control_render == "self"


def test_the_map_is_per_instance():
    """A bare ``{}`` default would be one dict shared by every PlotState."""
    a, b = PlotState(), PlotState()
    a.fc_control_map["CTRL"] = ControlRef.for_replicate_set("DMSO")
    assert b.fc_control_map == {}


def test_a_reset_clears_all_three():
    ps = PlotState()
    ps.set_fold_change(control_mode="per_member", control_render="hidden",
                       control_map={"A": ControlRef.for_well("A01")})
    restore_defaults(ps)
    assert (ps.fc_control_mode, ps.fc_control_render, ps.fc_control_map) \
        == ("single", "self", {})


def test_a_reset_does_not_share_the_fresh_map():
    a, b = PlotState(), PlotState()
    restore_defaults(a)
    restore_defaults(b)
    a.fc_control_map["X"] = ControlRef.for_well("A01")
    assert b.fc_control_map == {}


# ── the setter ───────────────────────────────────────────────────────────────

def test_the_map_is_copied_not_aliased():
    """The editor mutates a working copy as the user types; sharing the dict
    would apply half-finished edits to the plot."""
    ps = PlotState()
    working = {"CTRL": ControlRef.for_replicate_set("DMSO")}
    ps.set_fold_change(control_map=working)
    working["TREAT"] = ControlRef.for_replicate_set("Vehicle")
    assert list(ps.fc_control_map) == ["CTRL"]


def test_the_map_replaces_wholesale():
    ps = PlotState()
    ps.set_fold_change(control_map={"A": ControlRef.for_well("A01")})
    ps.set_fold_change(control_map={"B": ControlRef.for_well("B01")})
    assert list(ps.fc_control_map) == ["B"]


def test_a_bare_string_value_is_read_as_a_legacy_reference():
    ps = PlotState()
    ps.set_fold_change(control_map={"A": "DMSO"})
    assert ps.fc_control_map == {"A": ControlRef.legacy("DMSO")}


def test_an_empty_value_is_no_entry_rather_than_an_empty_one():
    """"No control for this member" has one spelling: the key is absent."""
    ps = PlotState()
    ps.set_fold_change(control_map={"A": None, "B": "", "C": "DMSO"})
    assert list(ps.fc_control_map) == ["C"]


def test_an_unknown_mode_is_ignored_rather_than_stored():
    ps = PlotState()
    ps.set_fold_change(control_mode="sideways")
    assert ps.fc_control_mode == "single"


def test_an_unknown_render_is_ignored_rather_than_stored():
    ps = PlotState()
    ps.set_fold_change(control_render="raw")
    assert ps.fc_control_render == "self"


def test_none_leaves_each_field_alone():
    ps = PlotState()
    ps.set_fold_change(control_mode="per_member", control_render="hidden",
                       control_map={"A": ControlRef.for_well("A01")})
    ps.set_fold_change(vs_t0_on=True)
    assert ps.fc_control_mode == "per_member"
    assert ps.fc_control_render == "hidden"
    assert list(ps.fc_control_map) == ["A"]


# ── rename ───────────────────────────────────────────────────────────────────

def test_a_rename_moves_the_key_the_value_and_the_single_control():
    """All three, or the plot half-follows the group and says nothing."""
    ps = PlotState(
        fc_control=ControlRef.for_replicate_set("DMSO"),
        fc_control_map={
            "DMSO": ControlRef.for_replicate_set("DMSO"),
            "TREAT": ControlRef.for_replicate_set("DMSO"),
        },
    )
    assert ps.rename_member("DMSO", "Vehicle") is True
    assert ps.fc_control == ControlRef.for_replicate_set("Vehicle")
    assert ps.fc_control_map == {
        "Vehicle": ControlRef.for_replicate_set("Vehicle"),
        "TREAT": ControlRef.for_replicate_set("Vehicle"),
    }


def test_a_rename_leaves_a_well_reference_alone():
    """Wells are not renameable, and a group named like one must not drag a
    well reference along with it."""
    ps = PlotState(
        fc_control=ControlRef.for_well("A01"),
        fc_control_map={"TREAT": ControlRef.for_well("A01")},
    )
    assert ps.rename_member("A01", "DMSO") is False
    assert ps.fc_control == ControlRef.for_well("A01")
    assert ps.fc_control_map["TREAT"] == ControlRef.for_well("A01")


def test_a_rename_leaves_an_unresolved_legacy_reference_alone():
    """The caller resolves legacy references first; anything still legacy here
    named nothing resolvable, and guessing would be worse than leaving it."""
    ps = PlotState(fc_control=ControlRef.legacy("DMSO"))
    assert ps.rename_member("DMSO", "Vehicle") is False
    assert ps.fc_control == ControlRef.legacy("DMSO")


def test_a_rename_of_an_unrelated_group_changes_nothing():
    ps = PlotState(fc_control_map={"A": ControlRef.for_replicate_set("DMSO")})
    assert ps.rename_member("Other", "Renamed") is False
    assert ps.fc_control_map == {"A": ControlRef.for_replicate_set("DMSO")}


def test_a_no_op_rename_is_a_no_op():
    ps = PlotState(fc_control=ControlRef.for_replicate_set("DMSO"))
    assert ps.rename_member("DMSO", "DMSO") is False
    assert ps.rename_member("", "X") is False
    assert ps.rename_member("DMSO", "") is False


def test_a_rename_onto_an_existing_key_does_not_duplicate_it():
    """``_unique_name`` makes this unreachable through the UI; the state must
    still end up with one entry per member rather than two."""
    ps = PlotState(fc_control_map={
        "DMSO": ControlRef.for_well("A01"),
        "Vehicle": ControlRef.for_well("B01"),
    })
    ps.rename_member("DMSO", "Vehicle")
    assert list(ps.fc_control_map) == ["Vehicle"]


# ── persistence ──────────────────────────────────────────────────────────────

def test_the_block_round_trips():
    ps = PlotState()
    ps.set_fold_change(
        vs_control_on=True, control=ControlRef.for_replicate_set("DMSO"),
        control_mode="per_member", control_render="hidden",
        control_map={"TREAT": ControlRef.for_well("A01"),
                     "OTHER": ControlRef.for_replicate_set("Vehicle")},
    )
    blob = vs._snapshot_fold_change(_ctx(ps))

    back = PlotState()
    vs._apply_fold_change(_ctx(back), blob)
    assert back.fc_control_mode == "per_member"
    assert back.fc_control_render == "hidden"
    assert back.fc_control_map == {
        "TREAT": ControlRef.for_well("A01"),
        "OTHER": ControlRef.for_replicate_set("Vehicle"),
    }
    assert back.fc_control == ControlRef.for_replicate_set("DMSO")


def test_a_stale_entry_survives_the_round_trip():
    """Retain-don't-drop is the contract, so the save is unfiltered — a
    member that is not currently plotted keeps its pairing."""
    ps = PlotState(fc_control_map={"NOT-PLOTTED": ControlRef.for_well("Z99")})
    back = PlotState()
    vs._apply_fold_change(_ctx(back), vs._snapshot_fold_change(_ctx(ps)))
    assert back.fc_control_map == {"NOT-PLOTTED": ControlRef.for_well("Z99")}


def test_a_document_written_before_this_restores_todays_behaviour():
    ps = PlotState()
    vs._apply_fold_change(_ctx(ps), {
        "vs_control_on": True, "control_label": "DMSO", "vs_t0_on": False,
    })
    assert ps.fc_control_mode == "single"
    assert ps.fc_control_map == {}
    assert ps.fc_control_render == "self"
    assert ps.fc_control == ControlRef.legacy("DMSO")


def test_an_unreadable_map_costs_the_pairings_and_nothing_else():
    """``restore`` is one long function — a raise here loses the whole view."""
    ps = PlotState()
    vs._apply_fold_change(_ctx(ps), {
        "control_map": ["not", "a", "mapping"],
        "control_mode": "per_member",
        "vs_control_on": True,
    })
    assert ps.fc_control_map == {}
    assert ps.fc_control_mode == "per_member"
    assert ps.fc_vs_control_on is True


def test_an_unreadable_entry_costs_that_entry_and_nothing_else():
    ps = PlotState()
    vs._apply_fold_change(_ctx(ps), {
        "control_map": {"A": {"kind": "well", "name": ""}, "B": "DMSO"},
    })
    assert ps.fc_control_map == {"B": ControlRef.legacy("DMSO")}


def test_an_unknown_mode_falls_back_rather_than_raising():
    ps = PlotState()
    vs._apply_fold_change(_ctx(ps), {"control_mode": "sideways",
                                     "control_render": "raw"})
    assert ps.fc_control_mode == "single"
    assert ps.fc_control_render == "self"


def test_a_block_that_is_not_a_mapping_is_ignored():
    ps = PlotState(fc_control_mode="per_member")
    vs._apply_fold_change(_ctx(ps), "nonsense")
    assert ps.fc_control_mode == "per_member"


# ── the rename hook ──────────────────────────────────────────────────────────
#
# The rename controller is where the three-way rewrite has to be *called*, and
# calling it is the half nothing else can check: ``rename_member`` can be
# correct while the group is renamed straight past it.

def _rename_ctx(plot, selections, well_paths):
    return SimpleNamespace(
        state=SimpleNamespace(
            plot=plot,
            selection=SimpleNamespace(selections=selections,
                                      current_selection_id=None),
            dataset=SimpleNamespace(well_paths=well_paths),
        ),
        coordinator=SimpleNamespace(rebuild_all=lambda: None),
    )


def test_renaming_a_group_moves_the_control_that_named_it():
    from well_viewer.core.selection_controller import sel_rename

    plot = PlotState(
        fc_control=ControlRef.for_replicate_set("DMSO"),
        fc_control_map={"TREAT": ControlRef.for_replicate_set("DMSO")},
    )
    ctx = _rename_ctx(
        plot,
        [{"id": "s1", "name": "DMSO", "wells": ["A01"]},
         {"id": "s2", "name": "TREAT", "wells": ["B01"]}],
        {"A01": "A01", "B01": "B01"},
    )
    sel_rename(ctx, "s1", "Vehicle")

    assert ctx.state.selection.selections[0]["name"] == "Vehicle"
    assert plot.fc_control == ControlRef.for_replicate_set("Vehicle")
    assert plot.fc_control_map == {"TREAT": ControlRef.for_replicate_set("Vehicle")}


def test_a_legacy_control_is_resolved_before_the_rename_not_after():
    """A legacy reference means "whatever `DMSO` resolves to today", and after
    the rename today's answer is gone. So the upgrade happens first."""
    from well_viewer.core.selection_controller import sel_rename

    plot = PlotState(fc_control=ControlRef.legacy("DMSO"))
    ctx = _rename_ctx(
        plot,
        [{"id": "s1", "name": "DMSO", "wells": ["A01"]}],
        {"A01": "A01"},
    )
    sel_rename(ctx, "s1", "Vehicle")
    assert plot.fc_control == ControlRef.for_replicate_set("Vehicle")


def test_a_legacy_control_naming_a_well_is_not_dragged_by_a_group_rename():
    """A group named ``A01`` and a loaded well ``A01``: the legacy reference
    resolves to whichever the precedence picks, and the rename must move only
    that one."""
    from well_viewer.core.selection_controller import sel_rename

    plot = PlotState(fc_control=ControlRef.legacy("A01"))
    ctx = _rename_ctx(
        plot,
        # The group's own wells are unloaded, so the precedence falls through
        # to the well token — the reference means the well.
        [{"id": "s1", "name": "A01", "wells": ["Z99"]}],
        {"A01": "A01"},
    )
    sel_rename(ctx, "s1", "Renamed")
    assert plot.fc_control == ControlRef.for_well("A01")


def test_deleting_a_group_leaves_its_pairings_behind_on_purpose():
    """Retain-don't-drop. A user who deletes a group and rebuilds it gets the
    pairing back; dropping here would make that silently unrecoverable."""
    from well_viewer.core.selection_controller import sel_delete

    plot = PlotState(fc_control_map={"TREAT": ControlRef.for_replicate_set("DMSO")})
    ctx = _rename_ctx(
        plot,
        [{"id": "s1", "name": "DMSO", "wells": ["A01"]},
         {"id": "s2", "name": "TREAT", "wells": ["B01"]}],
        {"A01": "A01", "B01": "B01"},
    )
    sel_delete(ctx, "s1")
    assert plot.fc_control_map == {"TREAT": ControlRef.for_replicate_set("DMSO")}


def test_the_saved_block_is_json_shaped():
    """It is written to ``persistence.json``, so no ``ControlRef`` may reach it."""
    import json

    ps = PlotState(fc_control=ControlRef.for_well("A01"),
                   fc_control_map={"T": ControlRef.for_replicate_set("DMSO")})
    blob = vs._snapshot_fold_change(_ctx(ps))
    assert json.loads(json.dumps(blob)) == blob
