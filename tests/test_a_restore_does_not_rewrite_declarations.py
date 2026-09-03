"""Restoring a saved plot reproduces a view; it must not rewrite a declaration.

**The defect.** ``restore_saved_plot`` deep-copied the record's replicate sets
over ``ctx.state.selection.selections`` and then reached
``selection_controller.rebuild_all``, whose tail queues a sample-definitions
autosave. So opening a figure saved last week replaced this week's groups —
their names, their membership, their plot order — and wrote the replacement
into ``pipeline_info.json``. There was nothing to undo it and nothing said it
had happened.

The app already knew this was destructive in the *other* place it replays a
record: ``regenerate_missing_vectors`` snapshots the live state in
``_LiveState`` and puts it back in a ``finally``, and declines the whole repair
when the snapshot fails. The Restore button had no such guard.

**The rule.** A restore may change what the app is *showing*; it must not
rewrite what the user has *declared* on Home → Sample Map. Which wells are
soloed is showing. A replicate set is declared. So the button restores the
first and reports on the second, and ``adopt_groups=True`` — the escape hatch
the export replay uses — is available only to the caller that can undo it.

The assertions read the live state and the file on disk, not the return of the
function under test: the defect's whole shape was a write nobody was watching.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from well_viewer.domain import sample_variables as sv
from well_viewer.domain.selections_model import group_drift, make_selection

pytestmark = pytest.mark.qt

WELLS = ("A01", "A02", "B01")


def _ctx(tmp_path: Path, *, groups=(("Mine", ["A01", "A02"]),)):
    """A dataset with all three declarations on it and on disk.

    All three, because the report named variables and well labels while the
    path under test replaces groups: a fixture holding only groups could not
    show that the other two come through a restore untouched, which is half of
    what is being claimed here.
    """
    from well_viewer.core.dataset_state import DatasetState
    from well_viewer.core.selection_state import SelectionState

    selections = [make_selection(name=n, wells=list(w)) for n, w in groups]
    info = {
        "schema": "x", "fluor_tokens": ["GFP"], "available_timepoints": [0.0],
        "sample_definitions": {
            "schema_version": 2, "selections": selections,
            "well_labels": {"A01": "shCtrl", "A02": "shA"},
            "notes": "my notes", "plot_groups": [],
        },
        "variables": {
            "variables": [{"id": "v1", "name": "Dose", "kind": "text",
                           "unit": "", "values": [{"id": "d1", "label": "10"}]}],
            "assignments": {"v1": {"A01": "d1"}},
        },
    }
    (tmp_path / "pipeline_info.json").write_text(json.dumps(info, indent=2))

    ds = DatasetState()
    ds.data_dir = tmp_path
    ds.well_paths = dict.fromkeys(WELLS, "x")
    ds.well_labels = {"A01": "shCtrl", "A02": "shA"}
    ds.pipeline_info = dict(info)
    sel = SelectionState()
    sel.selections = [dict(s) for s in selections]
    sel.notes_text = "my notes"
    sel.variables = sv.VariableSet(
        variables=(sv.Variable(id="v1", name="Dose",
                               values=(sv.VariableValue("d1", "10"),)),),
        assignments={"v1": {"A01": "d1"}})

    app = SimpleNamespace(_app_state=SimpleNamespace(dataset=ds, selection=sel))
    statuses: list[str] = []
    calls: list[str] = []
    ctx = SimpleNamespace(
        window=app, state=app._app_state,
        coordinator=SimpleNamespace(
            rebuild_all=lambda: calls.append("rebuild_all"),
            refresh_sidebar_map=lambda: calls.append("refresh_sidebar_map")),
        features=SimpleNamespace(call=lambda *a, **k: None),
        persistence=SimpleNamespace(
            schedule_save=lambda name: calls.append(f"save:{name}")),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
        notify=SimpleNamespace(set_status=statuses.append),
    )
    return ctx, statuses, calls


def _on_disk(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "pipeline_info.json").read_text())


# ── the comparison ──────────────────────────────────────────────────────────

def test_drift_names_the_groups_that_are_gone_and_the_ones_that_moved():
    saved = [make_selection(name="CTRL", wells=["A01", "A02"]),
             make_selection(name="DRUG", wells=["B01"]),
             make_selection(name="GONE", wells=["B02"])]
    live = [make_selection(name="CTRL", wells=["A01", "A02"]),
            make_selection(name="DRUG", wells=["B01", "B02"])]
    assert group_drift(saved, live) == (["GONE"], ["DRUG"])


def test_an_unchanged_plate_drifts_in_neither_direction():
    """The common case, and the one that must say nothing: a status line that
    speaks on every restore is a status line nobody reads."""
    saved = [make_selection(name="CTRL", wells=["A01", "A02"])]
    live = [dict(s) for s in saved]
    assert group_drift(saved, live) == ([], [])


def test_drift_matches_by_name_rather_than_by_id():
    """``validate_repair`` re-mints ids whenever a list is loaded or repaired,
    so an id-wise comparison would report every group as missing and the
    sentence would be noise on every restore."""
    saved = [make_selection(name="CTRL", wells=["A01", "A02"])]
    live = [make_selection(name="CTRL", wells=["A01", "A02"])]
    assert saved[0]["id"] != live[0]["id"]
    assert group_drift(saved, live) == ([], [])


def test_a_reordered_group_counts_as_changed():
    """The order is the plot order, so two groups holding the same wells in a
    different sequence draw a different figure. Telling the user they match
    would be wrong in exactly the way that matters to the picture."""
    saved = [make_selection(name="CTRL", wells=["A01", "A02"])]
    live = [make_selection(name="CTRL", wells=["A02", "A01"])]
    assert group_drift(saved, live) == ([], ["CTRL"])


# ── the button leaves every declaration where it is ─────────────────────────

def test_the_restore_path_touches_no_declaration_in_the_session(tmp_path):
    from well_viewer.features.saved_plots.controller import _restore_solo_wells

    ctx, _statuses, calls = _ctx(tmp_path)
    _restore_solo_wells(ctx, ["A02"])

    assert [s["name"] for s in ctx.state.selection.selections] == ["Mine"]
    assert dict(ctx.state.dataset.well_labels) == {"A01": "shCtrl", "A02": "shA"}
    assert [v.name for v in ctx.state.selection.variables.variables] == ["Dose"]
    # The view half still applies — that is what the record is for.
    assert ctx.state.selection.active_solo_wells == {"A02"}
    # Reached the screen without the fan-out whose tail writes the block.
    assert calls == ["refresh_sidebar_map"]


def test_the_restore_path_queues_no_sample_definitions_write(tmp_path):
    """``rebuild_all`` is where the disk write came from: its tail queues a
    sample-definitions autosave because every *other* caller has just edited a
    definition. A restore has not."""
    from well_viewer.features.saved_plots.controller import _restore_solo_wells

    ctx, _statuses, calls = _ctx(tmp_path)
    _restore_solo_wells(ctx, ["A01"])
    assert "rebuild_all" not in calls
    assert not [c for c in calls if c.startswith("save:")]


def test_the_file_on_disk_is_unchanged_by_a_restore(tmp_path):
    """Even if something did write the block, it must write back what is
    already there. Asserted on the bytes, because the session state agreeing
    with itself was never the thing that failed."""
    from well_viewer.persistence.sample_definitions import (
        autosave_to_pipeline_info,
    )
    from well_viewer.features.saved_plots.controller import _restore_solo_wells

    ctx, _statuses, _calls = _ctx(tmp_path)
    _restore_solo_wells(ctx, ["A02"])
    autosave_to_pipeline_info(ctx)          # what rebuild_all used to queue

    after = _on_disk(tmp_path)
    assert [s["name"] for s in after["sample_definitions"]["selections"]] == ["Mine"]
    assert after["sample_definitions"]["well_labels"] == {"A01": "shCtrl",
                                                          "A02": "shA"}
    assert [v["name"] for v in after["variables"]["variables"]] == ["Dose"]


# ── and says what it did not reproduce ──────────────────────────────────────

def _rec(title, sels):
    return SimpleNamespace(title=title, params={"selections": sels})


def test_the_drift_is_named_on_the_status_line(tmp_path):
    """A restore that quietly draws a different figure is indistinguishable
    from one that failed, and the user re-clicks Restore."""
    from well_viewer.features.saved_plots.controller import _report_group_drift

    ctx, statuses, _calls = _ctx(tmp_path)
    _report_group_drift(ctx, _rec("Fig 1", [
        make_selection(name="Old set", wells=["A01"]),
        make_selection(name="Mine", wells=["B01"]),
    ]))
    assert len(statuses) == 1
    said = statuses[0]
    assert "Fig 1" in said
    assert "Old set" in said, said          # the missing one, by name
    assert "Mine" in said, said             # the one whose wells moved
    assert "no longer defines" in said and "different wells" in said, said
    assert "left as they are" in said, said


def test_nothing_is_said_when_the_groups_still_match(tmp_path):
    from well_viewer.features.saved_plots.controller import _report_group_drift

    ctx, statuses, _calls = _ctx(tmp_path)
    _report_group_drift(ctx, _rec("Fig 1", [
        make_selection(name="Mine", wells=["A01", "A02"])]))
    assert statuses == []


def test_a_long_drift_names_three_and_counts_the_rest(tmp_path):
    from well_viewer.features.saved_plots.controller import _report_group_drift

    ctx, statuses, _calls = _ctx(tmp_path)
    _report_group_drift(ctx, _rec("Fig 1", [
        make_selection(name=f"G{i}", wells=["A01"]) for i in range(5)]))
    assert "and 2 more" in statuses[0], statuses[0]


# ── the one caller that may adopt them ──────────────────────────────────────

def test_the_export_replay_may_still_adopt_the_records_groups(tmp_path):
    """``regenerate_missing_vectors`` has to draw each record's own figure, and
    it is the only caller that can: it snapshots the live state and puts it
    back in a ``finally``. Take that hatch away and the deck's regenerated
    slides silently become pictures of today's groups."""
    from well_viewer.features.saved_plots.controller import _adopt_record_groups

    ctx, _statuses, calls = _ctx(tmp_path)
    ctx.window._invalidate_stats_cache = lambda: calls.append("invalidate")
    _adopt_record_groups(ctx, [make_selection(name="From the plot",
                                              wells=["A01"])])
    assert [s["name"] for s in ctx.state.selection.selections] == [
        "From the plot"]
    # Not through the fan-out: replaying a gallery of records would otherwise
    # have written each one's groups to disk in turn.
    assert "rebuild_all" not in calls
    assert not [c for c in calls if c.startswith("save:")]


def test_the_button_and_the_replay_disagree_on_purpose(tmp_path):
    """``adopt_groups`` defaults to off. A default that flipped would restore
    the original defect with no test noticing, since every other assertion here
    calls the two halves directly."""
    import inspect

    from well_viewer.features.saved_plots.controller import restore_saved_plot

    default = inspect.signature(restore_saved_plot).parameters["adopt_groups"].default
    assert default is False


def test_the_restore_button_itself_leaves_the_groups_alone(tmp_path, monkeypatch):
    """Driven through ``restore_saved_plot``, the function the button calls.

    Every other assertion in this file enters one step past it, at
    ``_restore_solo_wells`` or ``_adopt_record_groups`` — so deleting the
    ``if adopt_groups:`` guard and adopting unconditionally left the whole file
    green. That is §7.8 #3 exactly: the seam being right is not the claim, the
    claim is that the button does not eat your groups.

    The blocks after the ones under test — the gating reset, the stats re-run,
    ``view_state.restore``, the tab reapply — each catch their own failures, so
    a ctx this thin reaches the end and the assertions are about what ran, not
    about what happened to be reachable.
    """
    from well_viewer.features.saved_plots import controller as sp

    ctx, statuses, _calls = _ctx(tmp_path)
    ctx.runtime = SimpleNamespace(
        view_state=SimpleNamespace(pending=None, absent_wells=set(),
                                   absent_solo=set()),
        saved_plots=SimpleNamespace(cards_by_tab={}))
    ctx.features = SimpleNamespace(call=lambda *a, **k: None,
                                   redraw=lambda *a, **k: None)

    record = SimpleNamespace(
        id="p1", title="Fig 1", tab="Bar Plots", thresholds={},
        params={"selections": [make_selection(name="From the plot",
                                              wells=["A01"])],
                "active_solo_wells": ["A02"],
                "active_centre_tab": "Bar Plots"})
    monkeypatch.setattr(
        sp, "get_store",
        lambda _ctx: SimpleNamespace(get=lambda pid: record if pid == "p1" else None),
        raising=True)

    sp.restore_saved_plot(ctx, "p1")

    assert [s["name"] for s in ctx.state.selection.selections] == ["Mine"]
    assert dict(ctx.state.dataset.well_labels) == {"A01": "shCtrl", "A02": "shA"}
    assert [v.name for v in ctx.state.selection.variables.variables] == ["Dose"]
    # …and it said what it could not reproduce.
    assert any("From the plot" in s for s in statuses), statuses


def test_the_replay_asking_for_them_still_gets_them(tmp_path, monkeypatch):
    """The other half of the same call: without this, "the button leaves them
    alone" is satisfied by a ``restore_saved_plot`` that can never adopt at
    all, and the deck's regenerated slides quietly become today's figures."""
    from well_viewer.features.saved_plots import controller as sp

    ctx, _statuses, _calls = _ctx(tmp_path)
    ctx.runtime = SimpleNamespace(
        view_state=SimpleNamespace(pending=None, absent_wells=set(),
                                   absent_solo=set()),
        saved_plots=SimpleNamespace(cards_by_tab={}))
    ctx.features = SimpleNamespace(call=lambda *a, **k: None,
                                   redraw=lambda *a, **k: None)
    ctx.window._invalidate_stats_cache = lambda: None

    record = SimpleNamespace(
        id="p1", title="Fig 1", tab="Bar Plots", thresholds={},
        params={"selections": [make_selection(name="From the plot",
                                              wells=["A01"])],
                "active_centre_tab": "Bar Plots"})
    monkeypatch.setattr(
        sp, "get_store",
        lambda _ctx: SimpleNamespace(get=lambda pid: record if pid == "p1" else None),
        raising=True)

    sp.restore_saved_plot(ctx, "p1", adopt_groups=True)
    assert [s["name"] for s in ctx.state.selection.selections] == [
        "From the plot"]


def test_the_gating_block_is_still_re_applied(tmp_path, monkeypatch):
    """The documented exception, pinned so the rule is not over-applied later.

    A gate is a threshold, not a statement about what a well is — which is why
    ``home/collect.DECLARATION_ROLES`` leaves Cell Gating out of the four ways
    of annotating a well in the first place. It also changes the *numbers* the
    saved figure was drawn from, so a restore that left today's gates in place
    would draw a different measurement under the saved figure's title. It stays
    a session change: nothing on this path writes ``pipeline_info.json``.
    """
    from well_viewer.core.cell_gating_state import CellGatingState
    from well_viewer.features.saved_plots import controller as sp

    ctx, _statuses, _calls = _ctx(tmp_path)
    ctx.state.cell_gating = CellGatingState()
    ctx.state.cell_gating.fluor_gates = {"gfp": 500.0}   # the user's own gate
    ctx.runtime = SimpleNamespace(
        view_state=SimpleNamespace(pending=None, absent_wells=set(),
                                   absent_solo=set()),
        saved_plots=SimpleNamespace(cards_by_tab={}))
    ctx.features = SimpleNamespace(call=lambda *a, **k: None,
                                   redraw=lambda *a, **k: None)
    record = SimpleNamespace(
        id="p1", title="Fig 1", tab="Bar Plots",
        thresholds={"fluor_gates": {"gfp": 120.0}},
        params={"active_centre_tab": "Bar Plots"})
    monkeypatch.setattr(
        sp, "get_store",
        lambda _ctx: SimpleNamespace(get=lambda pid: record if pid == "p1" else None),
        raising=True)

    sp.restore_saved_plot(ctx, "p1")
    assert ctx.state.cell_gating.fluor_gates == {"gfp": 120.0}
    # …and the file is still the user's.
    assert _on_disk(tmp_path)["sample_definitions"]["well_labels"] == {
        "A01": "shCtrl", "A02": "shA"}
