"""A dataset load draws its plots once, and a load that starts a gating pass draws them after it.

Tracker R6 (``_Docs/Markdowns/REREAD_TRACKER.md``), and the reporter's decision:
do not draw twice. Before, a reload of a dataset with a saved gate drew the
visible plot from the load's channel refresh, again from ``dataset_loaded``, and
a third time when the gating pass ended — and only the last one showed the
numbers the user asked for. On a plate of large wells each draw cost a pass
over every well.

The gating pass is held on an event so the state *during* the pass is
observable deterministically rather than raced: the visible plot must not draw,
and its placeholder must say why, until the pass ends.
"""

from __future__ import annotations

import json
import threading

import pytest

pytestmark = pytest.mark.slow


def _show_line_graphs(app) -> None:
    app._notebook.setCurrentByName("Plotting")
    app._plotting_notebook.setCurrentByName("Line Graphs")


def _count_line_draws(monkeypatch) -> list:
    from review.features.lineplot import controller

    calls: list = []
    real = controller.redraw_line_plots

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(controller, "redraw_line_plots", counted)
    return calls


def _save_gate(dataset) -> None:
    info_path = dataset / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["cell_gating"] = {"fluor_gates": {"gfp": 130.0}}
    info_path.write_text(json.dumps(info))


def _save_groups(dataset) -> None:
    """Two sample groups, as a recipe's Sample definitions scope writes them:
    with no group a plate-wide plot has nothing to collect, and an off-screen
    draw would cost nothing and be invisible to the count."""
    from domain.selections_format import make_selection

    info_path = dataset / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["sample_definitions"] = {
        "schema_version": 2, "well_labels": {}, "notes": "", "plot_groups": [],
        "selections": [make_selection(name="ctrl", wells=["A01", "A02"]),
                       make_selection(name="drug", wells=["B01", "B02"])]}
    info_path.write_text(json.dumps(info))


def _line_stack(app):
    canvas = app._ctx.widgets.get("_line_canvas")
    for stack in app._plot_empty_stacks:
        if stack.isAncestorOf(canvas):
            return stack
    raise AssertionError("the Line Graphs canvas is in no empty-state stack")


def _says_gating(stack) -> bool:
    """The gating placeholder is up. (After the pass the tab may still show a
    placeholder of its own — no wells are selected after a reload — so the test
    is for this wording, not for the figure page.)"""
    from review.core.ui_helpers import GATING_IN_PROGRESS

    return stack.currentIndex() == 0 and stack._empty_placeholder._text.text() == GATING_IN_PROGRESS.title


def _gate_the_pass(monkeypatch):
    """Make the gating pass wait on an event before it gates anything."""
    from review.features.home.cell_gating import controller as cg

    go = threading.Event()
    real = cg.GatingWorker._run_inner

    def waiting(self):
        assert go.wait(60), "the test never released the gating pass"
        return real(self)

    monkeypatch.setattr(cg.GatingWorker, "_run_inner", waiting)
    return go


def test_a_load_that_starts_gating_draws_the_visible_plot_once_after_it(
        viewer_app, mini_dataset, qtbot, monkeypatch):
    """A reload lands on Home, so the user opens Line Graphs while the pass runs:
    the tab must show the placeholder, and draw once when the pass ends."""
    app = viewer_app
    app._load_path(mini_dataset)
    _save_gate(mini_dataset)                        # what applying a recipe with gating does
    go = _gate_the_pass(monkeypatch)
    draws = _count_line_draws(monkeypatch)

    app._load_path(mini_dataset)                    # the reload after the recipe

    worker = app._ctx.runtime.cell_gating.worker
    assert worker is not None and worker.isRunning(), "the saved gate started no pass; this measures nothing"
    assert app._app_state.plot.held_for == "gating"
    _show_line_graphs(app)                          # the user opens the plot while the pass runs
    assert draws == [], f"the plot was drawn {len(draws)} time(s) before its gates were applied"
    stack = _line_stack(app)
    assert _says_gating(stack), "the plot does not say it is waiting on the gating pass"

    go.set()
    qtbot.waitUntil(lambda: app._app_state.plot.held_for == "", timeout=30_000)
    worker.wait()
    assert len(draws) == 1, f"drawn {len(draws)} times after the pass; once is the point"
    assert not _says_gating(stack), "the gating placeholder stayed up after the pass"


def test_a_load_with_no_gate_draws_the_line_plot_once_when_it_is_shown(viewer_app, mini_dataset, monkeypatch):
    """No pass to wait for: the hold lifts before ``dataset_loaded``. A load
    lands on Home, so the line plot — which ``redraw_app`` used to draw whatever
    was in front — is owed rather than drawn, and opening its tab draws it
    once. The channel refresh earlier in the load used to draw it as well."""
    app = viewer_app
    app._load_path(mini_dataset)
    draws = _count_line_draws(monkeypatch)

    app._load_path(mini_dataset)

    assert app._ctx.runtime.cell_gating.worker is None or not app._ctx.runtime.cell_gating.worker.isRunning()
    assert app._app_state.plot.held_for == ""
    assert app._notebook.currentName() == "Home"
    assert draws == [], f"a load drew the line plot {len(draws)} time(s) with Home in front"
    _show_line_graphs(app)
    assert len(draws) == 1, f"opening Line Graphs drew it {len(draws)} times"


def test_a_load_draws_no_plot_that_is_not_on_screen(viewer_app, mini_dataset, monkeypatch):
    """The channel refresh inside the load used to draw Scatter, Scatter
    Aggregate, Response and the Heat Map whatever tab was in front, and the
    load's own end then owed them again anyway. With Home in front, a reload
    collects no Scatter Aggregate data at all; the tab draws when it is shown."""
    from review.features.scatter import controller as scatter

    app = viewer_app
    app._load_path(mini_dataset)
    app._drain_pending_centre_builders()           # every tab built, so every hook can fire
    assert app._ctx.widgets.has("_scatter_ch_x_cb"), "Scatter is not built; its hook cannot fire"
    collected: list = []
    real = scatter.collect_scatter_agg_data

    def counted(*a, **k):
        collected.append(1)
        return real(*a, **k)

    monkeypatch.setattr(scatter, "collect_scatter_agg_data", counted)
    _save_groups(mini_dataset)                     # the recipe's sample definitions
    app._load_path(mini_dataset)
    assert app._notebook.currentName() == "Home"
    assert collected == [], f"a reload collected Scatter Aggregate data {len(collected)} time(s) off screen"


def test_a_failed_pass_still_lifts_the_hold_and_draws(viewer_app, mini_dataset, qtbot, monkeypatch):
    from review.features.home.cell_gating import controller as cg

    app = viewer_app
    app._load_path(mini_dataset)
    _save_gate(mini_dataset)

    def broken(self):
        raise RuntimeError("disk vanished")

    monkeypatch.setattr(cg.GatingWorker, "_run_inner", broken)
    app._load_path(mini_dataset)
    qtbot.waitUntil(lambda: app._app_state.plot.held_for == "", timeout=30_000)
    draws = _count_line_draws(monkeypatch)
    _show_line_graphs(app)
    assert len(draws) == 1
    assert not _says_gating(_line_stack(app))


def _gate_the_threaded_pass(monkeypatch):
    """Like :func:`_gate_the_pass`, but only the worker thread waits: a pass run
    synchronously on the GUI thread (``cell_gating_recompute_now``) goes straight
    through, as it does in the app."""
    from review.features.home.cell_gating import controller as cg

    go = threading.Event()
    real = cg.GatingWorker._run_inner

    def waiting(self):
        if threading.current_thread() is not threading.main_thread():
            assert go.wait(60), "the test never released the gating pass"
        return real(self)

    monkeypatch.setattr(cg.GatingWorker, "_run_inner", waiting)
    return go


def test_a_synchronous_recompute_that_cancels_the_load_s_pass_lifts_its_hold(
        viewer_app, mini_dataset, qtbot, monkeypatch):
    """The third way a load's pass can end: cancelled, with no async pass to
    replace it. ``cell_gating_recompute_now`` (the Saved Plots export and the
    headless report) cancels the running worker — which then emits nothing —
    and runs the pass itself. The hold belonged to the cancelled pass, so the
    synchronous one must lift it and draw the visible plot; otherwise every
    redraw stays a no-op for the rest of the session."""
    from review.features.home.cell_gating.controller import cell_gating_recompute_now

    app = viewer_app
    app._load_path(mini_dataset)
    _save_gate(mini_dataset)
    go = _gate_the_threaded_pass(monkeypatch)
    app._load_path(mini_dataset)
    worker = app._ctx.runtime.cell_gating.worker
    assert worker is not None and worker.isRunning(), "the saved gate started no pass; this measures nothing"
    assert app._app_state.plot.held_for == "gating"
    _show_line_graphs(app)
    draws = _count_line_draws(monkeypatch)
    try:
        assert cell_gating_recompute_now(app._ctx) is True
        assert app._app_state.plot.held_for == "", (
            "the synchronous recompute cancelled the pass that owned the 'gating' hold "
            "and left the hold in place: every redraw is now a no-op")
        assert len(draws) == 1, f"the visible plot was drawn {len(draws)} times by lifting the hold"
        assert not _says_gating(_line_stack(app)), "the gating placeholder stayed up"
    finally:
        go.set()
        worker.wait()
    for _ in range(20):                              # the cancelled worker emits nothing
        qtbot.wait(10)
    assert app._app_state.plot.held_for == ""


def test_the_report_does_not_call_a_figure_drawn_while_the_plots_are_held(
        viewer_app, mini_dataset, monkeypatch):
    """``render_saved_plot`` captures ``card.figure`` after asking for a redraw.
    While a hold is in force every redraw body returns early, so the capture is
    of whatever the figure last held — a non-empty PNG of the wrong thing, which
    the report's manifest would list as ``drawn``. A held capture must come back
    with a reason instead."""
    from review.core import plot_hold
    from review.features.saved_plots import _render
    from review.features.saved_plots.state import get_store

    app = viewer_app
    ctx = app._ctx
    app._load_path(mini_dataset)
    assert app._sel_add(name="P", wells=["A01", "A02"], source="user")
    app._notebook.setCurrentByName("Bar Plots")
    card = ctx.runtime.saved_plots.cards_by_tab["Bar Plots"]
    card.saveFigureToGallery()
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    rec = store.records[-1]

    def recompute_that_leaves_the_hold(c):
        plot_hold.hold(c, plot_hold.HOLD_GATING)
        return True

    monkeypatch.setattr(_render, "_gating_recompute_now", recompute_that_leaves_the_hold)
    out = _render.render_saved_plot(ctx, rec)
    assert out.png == b"" and "held" in out.reason, (
        f"a figure captured while every redraw was a no-op was reported as drawn "
        f"(png {len(out.png)} bytes, reason {out.reason!r})")

    plot_hold.release(ctx)
    monkeypatch.setattr(_render, "_gating_recompute_now", lambda c: True)
    drawn = _render.render_saved_plot(ctx, rec)
    assert drawn.png and drawn.reason == "", f"an unheld render was refused: {drawn.reason!r}"
