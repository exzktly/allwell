"""Home, built for real, against a loaded dataset.

Everything computable is tested without an application in
``test_home_collect.py``. What needs a real app is the wiring: that Home is the
page Review opens on, that its plate paints from the record, that its notes
editor and the long-form one cannot clobber each other, and that taking the
eager first-tab slot did not cost the attribute twelve modules read off Plotting.

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

def test_the_two_notes_editors_cannot_clobber_each_other(loaded_app):
    """The sequence with no other guard: type on Home, visit Sample
    Definitions, focus out there. Without the mirror the second box holds the
    previous text and writes it back over the edit."""
    ctx = loaded_app._ctx
    from well_viewer.core.ui_helpers import notes_schedule_save

    home = ctx.widgets.get_or_none("_home_notes_edit")
    other = ctx.widgets.get_or_none("_notes_edit")
    assert home is not None and other is not None, "premise: both editors exist"

    home.setPlainText("the incubator alarmed overnight")
    notes_schedule_save(ctx, widget_key="_home_notes_edit")

    assert other.toPlainText() == "the incubator alarmed overnight"

    # Now the long-form editor loses focus in its turn. It must not revert.
    notes_schedule_save(ctx, widget_key="_notes_edit")
    assert ctx.state.selection.notes_text == "the incubator alarmed overnight"
    assert home.toPlainText() == "the incubator alarmed overnight"


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


# ── the rail, across a navigation ───────────────────────────────────────────
#
# These are the only tests in this file that measure geometry, so they are the
# only ones that need the window actually shown and its events pumped: an
# unshown ``WellViewerApp`` has never laid anything out, and every height reads
# as a widget default.

_RAIL_W, _RAIL_H = 1600, 1000


def _laid_out(app, qapp):
    app.resize(_RAIL_W, _RAIL_H)
    app.show()
    for _ in range(15):
        qapp.processEvents()
    return app


def _goto(app, qapp, name: str) -> None:
    app._notebook.setCurrentByName(name)
    for _ in range(5):
        qapp.processEvents()


def test_leaving_home_and_coming_back_leaves_the_rail_links_alone(loaded_app, qapp):
    """The reported defect, in the sequence it was reported in.

    Home is the one page that shows no rail frame — its plate is on the page,
    so the rail's picker would be a second one. ``on_tab_change`` hides every
    frame before dispatching, so on Home the rail's nav was the only growable
    item left in a column stretched to the viewport, and its rows spread from
    33px to 81px.
    """
    app = _laid_out(loaded_app, qapp)
    nav = app._section_nav
    hint = nav.sizeHint().height()

    _goto(app, qapp, "Plotting")
    away = nav.height()
    _goto(app, qapp, "Home")

    assert away == hint, "premise: the nav sits at its hint on an ordinary page"
    assert nav.height() == hint, (
        f"back on Home the nav is {nav.height()}px against a hint of {hint}px — "
        "the links have spread to fill the rail")


def test_a_page_with_a_rail_body_still_gets_the_space(loaded_app, qapp):
    """The other half: the trailing spacer must not compete with the frame it
    was added to sit beside. Plotting's rail body owns the slack."""
    app = _laid_out(loaded_app, qapp)
    _goto(app, qapp, "Plotting")
    frame = app._sidebar_main_frame

    assert frame.isVisible()
    assert frame.height() > app._section_nav.height(), (
        "the rail's per-page frame should still take the column's spare height")


# ── before a dataset is open ────────────────────────────────────────────────

def test_home_shows_the_load_prompt_rather_than_an_unfilled_page(viewer_app):
    """Home is the page the app launches on, so this is the first screen."""
    from well_viewer.core.ui_helpers import NO_DATASET_TITLE

    stack = viewer_app._ctx.widgets.get_or_none("_home_empty_stack")
    assert stack is not None, "premise: Home's body is wrapped"
    assert stack.currentIndex() == 0, "an unfilled Home is showing instead"
    placeholder = stack._empty_placeholder
    assert placeholder._text.text() == NO_DATASET_TITLE
    assert placeholder._action.text() == "Open a dataset…"


def test_the_page_comes_back_once_a_dataset_is_open(loaded_app):
    stack = loaded_app._ctx.widgets.get_or_none("_home_empty_stack")
    assert stack is not None
    assert stack.currentIndex() == 1, "the placeholder is still covering Home"


def test_the_record_survives_a_round_trip_through_the_sidecar(loaded_app, tmp_path):
    """What the page reads is what the pipeline writes — same shape, no
    adapter in between."""
    from pipeline.completeness import HEALTH_STATES

    _write_run_check(loaded_app, {"A01": _well()})
    block = loaded_app._app_state.dataset.pipeline_info["run_check"]
    reloaded = json.loads(json.dumps(block))
    assert reloaded == block
    assert all(w["health"] in HEALTH_STATES for w in reloaded["wells"].values())
