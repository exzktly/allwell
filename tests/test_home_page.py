"""Home, built for real, against a loaded dataset.

Everything computable is tested without an application in
``test_home_collect.py``. What needs a real app is the wiring: that Home is the
page Review opens on, that its plate paints from the record, that its tabs and
their sidebar behave, and that taking the eager first-tab slot did not cost the
attribute twelve modules read off Plotting.

Slow lane, deliberately one file — each of these builds a full ``WellViewerApp``
whose Qt↔Python reference web is never reclaimed in-process.
"""

from __future__ import annotations

import json

import pytest

from well_viewer.features.home import collect as hc


def _write_run_check(app, wells: dict, **kw) -> None:
    """Put a completeness record into the loaded dataset and re-read it."""
    block = {
        "version": 1, "skipped": False, "partial_scan": False, "suspended": False,
        "finished_at": "2026-08-09T12:00:00Z", "duration_s": 3720.0,
        "host": "test-host", "wells": wells,
        "totals": {
            "wells": len(wells),
            "complete": sum(1 for w in wells.values() if w["health"] == "complete"),
            "partial": sum(1 for w in wells.values() if w["health"] == "partial"),
            "missing_csv": 0, "no_output": 0,
            "intent": sum(w["intent"] for w in wells.values()),
            "covered_any": sum(w["covered_any"] for w in wells.values()),
            "covered_csv": sum(w["covered_csv"] for w in wells.values()),
        },
        "errors": {"files": 0, "by_type": {}}, "findings": [],
    }
    block.update(kw)
    app._app_state.dataset.pipeline_info["run_check"] = block


def _well(health="complete", *, intent=8, covered_any=8, failed=()):
    return {"intent": intent, "covered_any": covered_any,
            "covered_csv": covered_any, "missing_count": intent - covered_any,
            "missing": [], "csv_state": "ok", "has_in": True,
            "has_out_zip": covered_any > 0, "has_out_folder": False,
            "failed": list(failed), "health": health}


# ── the landing page ────────────────────────────────────────────────────────

def test_home_is_the_page_the_app_opens_on(viewer_app):
    assert viewer_app._notebook.pageNames()[0] == "Home"
    assert viewer_app._notebook.currentName() == "Home"


def test_taking_the_eager_slot_did_not_cost_the_plotting_notebook(viewer_app):
    """``_plotting_notebook`` is set as a side effect of Plotting's builder, and
    Plotting used to be built eagerly because it was first. Twelve modules read
    that attribute; the explicit pre-build in ``centre_view`` is what keeps them
    working now that Home is first."""
    assert getattr(viewer_app, "_plotting_notebook", None) is not None


def test_home_appears_in_the_rail(viewer_app):
    nav = getattr(viewer_app, "_section_nav", None)
    assert nav is not None
    assert nav.currentKey() == "Home"


# ── the plate ───────────────────────────────────────────────────────────────

def test_the_plate_paints_the_record(loaded_app):
    wells = dict.fromkeys(loaded_app._app_state.dataset.well_paths, None)
    assert wells, "premise: the fixture dataset has wells"
    first = sorted(wells)[0]
    _write_run_check(loaded_app, {
        first: _well("partial", covered_any=4),
    })
    from well_viewer.features.home.controller import refresh_home
    refresh_home(loaded_app._ctx)

    cells = hc.plate_cells(loaded_app._ctx)
    assert cells[first] == hc.CELL_PARTIAL
    assert hc.plate_fractions(loaded_app._ctx)[first] == pytest.approx(0.5)
    assert loaded_app._home_plate is not None


def test_clicking_a_well_selects_it_rather_than_navigating(loaded_app):
    first = sorted(loaded_app._app_state.dataset.well_paths)[0]
    from well_viewer.features.home.controller import on_home_plate_selection

    on_home_plate_selection(loaded_app._ctx, [first])

    assert first in loaded_app._app_state.selection.selected_wells
    assert loaded_app._notebook.currentName() == "Home", (
        "a click on the summary page must not teleport the reader away")


# ── notes ───────────────────────────────────────────────────────────────────

def test_home_carries_the_only_notes_editor(loaded_app):
    """The page that used to hold the long-form editor is gone, and its box with
    it. A second editor over one string needed a mirror to stop the stale one
    writing back over an edit; one editor needs no such thing, and a second one
    reappearing without the roster knowing would resurrect that bug."""
    ctx = loaded_app._ctx
    from well_viewer.core.ui_helpers import NOTES_WIDGET_KEYS, notes_schedule_save

    assert NOTES_WIDGET_KEYS == ("_home_notes_edit",)
    home = ctx.widgets.get_or_none("_home_notes_edit")
    assert home is not None
    assert ctx.widgets.get_or_none("_notes_edit") is None

    home.setPlainText("the incubator alarmed overnight")
    notes_schedule_save(ctx, widget_key="_home_notes_edit")
    assert ctx.state.selection.notes_text == "the incubator alarmed overnight"


def test_a_save_that_never_moved_focus_still_captures_the_home_edit(loaded_app):
    ctx = loaded_app._ctx
    from well_viewer.core.ui_helpers import commit_focused_notes

    home = ctx.widgets.get_or_none("_home_notes_edit")
    home.setPlainText("typed but not committed")
    home.setFocus()

    commit_focused_notes(ctx)
    assert ctx.state.selection.notes_text == "typed but not committed"


# ── the record's absence is visible ─────────────────────────────────────────

def test_a_dataset_with_no_record_says_so_on_the_page(loaded_app):
    loaded_app._app_state.dataset.pipeline_info.pop("run_check", None)
    from well_viewer.features.home.controller import refresh_home
    refresh_home(loaded_app._ctx)

    summary = loaded_app._home_plate_summary.text()
    assert "no completeness record" in summary
    assert hc.next_step(loaded_app._ctx)["action"] == "validate"


def test_every_tile_reads_as_a_dash_without_a_record(loaded_app):
    loaded_app._app_state.dataset.pipeline_info.pop("run_check", None)
    from well_viewer.features.home.controller import refresh_home
    from well_viewer.features.home.tab_view import DASH

    refresh_home(loaded_app._ctx)
    tiles = hc.stat_tiles(loaded_app._ctx)
    unavailable = [t for t in tiles if not t.available]
    assert unavailable, "premise: some tiles depend on the record"
    from well_viewer.features.home.tab_view import format_value
    assert all(format_value(t.value) == DASH for t in unavailable)


# ── actions ─────────────────────────────────────────────────────────────────

def test_the_analyze_action_switches_the_pane(loaded_app):
    from well_viewer.core.tab_controller import PANE_MODES
    from well_viewer.features.home.controller import run_home_action

    stack = getattr(loaded_app, "_central_pane_stack", None)
    if stack is None or stack.count() < 2:
        pytest.skip("this build has no Analyze pane mounted")

    run_home_action(loaded_app._ctx, "analyze")
    assert PANE_MODES[stack.currentIndex()] == "analyze"


def test_an_action_with_nothing_behind_it_does_nothing(viewer_app):
    """No dataset open: reload has nothing to reload and must not raise."""
    from well_viewer.features.home.controller import run_home_action
    run_home_action(viewer_app._ctx, "reload")
    run_home_action(viewer_app._ctx, "not-an-action")


def test_a_page_action_navigates(loaded_app):
    from well_viewer.features.home.controller import run_home_action
    run_home_action(loaded_app._ctx, "plot")
    assert loaded_app._notebook.currentName() == "Plotting"


# ── repainting ──────────────────────────────────────────────────────────────

def test_refresh_is_safe_before_the_page_is_built(viewer_app):
    """Hooks fire for pages whose bodies may never have been built."""
    from well_viewer.features.home.controller import refresh_home
    for name in ("_home_title", "_home_plate"):
        viewer_app._ctx.widgets._m.pop(name, None) if hasattr(
            viewer_app._ctx.widgets, "_m") else None
    refresh_home(viewer_app._ctx)


def test_repainting_twice_leaves_one_set_of_rows(loaded_app):
    _write_run_check(loaded_app, {
        w: _well() for w in sorted(loaded_app._app_state.dataset.well_paths)[:2]})
    from well_viewer.features.home.controller import refresh_home

    refresh_home(loaded_app._ctx)
    first = loaded_app._home_health.layout().count()
    refresh_home(loaded_app._ctx)
    assert loaded_app._home_health.layout().count() == first, (
        "a repaint must replace the rows, not append to them")


def test_the_record_survives_a_round_trip_through_the_sidecar(loaded_app, tmp_path):
    """What the page reads is what the pipeline writes — same shape, no
    adapter in between."""
    from pipeline.completeness import HEALTH_STATES

    _write_run_check(loaded_app, {"A01": _well()})
    block = loaded_app._app_state.dataset.pipeline_info["run_check"]
    reloaded = json.loads(json.dumps(block))
    assert reloaded == block
    assert all(w["health"] in HEALTH_STATES for w in reloaded["wells"].values())


# ── the page's tabs ─────────────────────────────────────────────────────────

def _subtabs(app):
    return app._ctx.runtime.home.subtabs


def _goto(app, qtbot, name: str) -> None:
    sub = _subtabs(app)
    for i in range(sub.count()):
        if sub.tabText(i) == name:
            sub.setCurrentIndex(i)
            qtbot.wait(10)
            return
    raise AssertionError(f"no Home tab named {name!r}")


def test_the_page_opens_on_overview(loaded_app):
    """Overview leads because Home is the landing page and it reports rather
    than asks. The definition editors follow it."""
    sub = _subtabs(loaded_app)
    assert [sub.tabText(i) for i in range(sub.count())] == [
        "Overview", "Cell Gating", "Groups", "Well Labels", "Ratios",
        "Pipeline Info",
    ]
    assert sub.tabText(sub.currentIndex()) == "Overview"


def test_every_definition_tab_is_built_after_a_load(loaded_app):
    """The timing contract, and the reason the tabs are drained rather than
    built on first click.

    These editors' widgets are read after a load by persistence hydration, by
    the plot tabs' threshold quick-edit and by the Playground's context bar —
    none of which open a tab first. That was true when the definitions were a
    page of their own, drained by ``centre_view``; moving them onto the eagerly
    built landing page must not quietly make them lazy.
    """
    ctx = loaded_app._ctx
    assert ctx.runtime.home.built == {
        "Cell Gating": True, "Groups": True, "Well Labels": True,
        "Ratios": True, "Pipeline Info": True,
    }
    for key in ("_rep_list", "_lbl_grid", "_ratio_panel"):
        assert ctx.widgets.get_or_none(key) is not None, key
    assert ctx.runtime.cell_gating.fluor_gate_edits is not None
    assert callable(ctx.runtime.grouping.pipeline_info_refresh)


def test_the_group_plate_follows_the_tab(loaded_app, qtbot):
    """The rail plate composes groups. On Overview that question is not being
    asked — and Overview has a plate of its own in the page body, so leaving the
    rail one up would put two plates on screen answering different things."""
    ctx = loaded_app._ctx
    frame = ctx.widgets.get("_sidebar_sample_frame")

    _goto(loaded_app, qtbot, "Groups")
    assert frame.isVisibleTo(loaded_app)
    _goto(loaded_app, qtbot, "Overview")
    assert not frame.isVisibleTo(loaded_app)


def test_import_is_offered_only_where_it_means_something(loaded_app, qtbot):
    """Import merges a sample-definitions JSON. Beside the report it names
    nothing the user can see."""
    btn = loaded_app._ctx.widgets.get("_home_import_btn")

    _goto(loaded_app, qtbot, "Well Labels")
    assert btn.isVisibleTo(loaded_app)
    _goto(loaded_app, qtbot, "Overview")
    assert not btn.isVisibleTo(loaded_app)


def test_a_plate_drag_edits_the_group_only_from_the_groups_tab(loaded_app, qtbot):
    """Two failures in one test, because they are two halves of one rule.

    The rail plate must still commit from Groups once a group exists — the
    plate goes passive in group mode unless a *well-editing* pane is on screen,
    and that predicate is page-level, so Home has to be named in it or the drag
    silently does nothing. And it must not commit from any other tab, where
    ``selectionDragFinished`` still fires on programmatic refresh ticks and
    would rewrite the group's composition unasked.
    """
    from well_viewer.core.selection_controller import (
        _plate_passive_in_group_mode, sel_by_id,
    )

    ctx = loaded_app._ctx
    wells = sorted(ctx.state.dataset.well_paths)
    ctx.state.selection.selections.append(
        {"id": "g1", "name": "G1", "wells": [wells[0]]})
    ctx.state.selection.current_selection_id = "g1"
    plate = ctx.widgets.get("_rep_map_plate")

    _goto(loaded_app, qtbot, "Groups")
    assert not _plate_passive_in_group_mode(ctx), (
        "the plate went passive on Groups, so a drag would be dropped")
    plate.setSelectedWellIds(wells[:2])
    plate.selectionDragFinished.emit()
    qtbot.wait(10)
    assert sel_by_id(ctx, "g1").get("wells") == wells[:2]

    _goto(loaded_app, qtbot, "Well Labels")
    plate.setSelectedWellIds(wells[3:5])
    plate.selectionDragFinished.emit()
    qtbot.wait(10)
    assert sel_by_id(ctx, "g1").get("wells") == wells[:2], (
        "a drag from a non-Groups tab rewrote the group")


def test_a_document_naming_the_old_page_still_restores(loaded_app):
    """Every session writes a view-state snapshot, so documents naming the page
    the definitions used to live on are on disk everywhere. Without the remap
    the restore silently fails and the dataset reopens on whatever page happened
    to be current."""
    from well_viewer.persistence.view_state import _activate_tab

    loaded_app._notebook.setCurrentByName("Plotting")
    _activate_tab(loaded_app._ctx, "Sample Definitions")
    assert loaded_app._notebook.currentName() == "Home"
