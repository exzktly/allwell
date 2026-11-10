"""A plot whose redraw fails says so on the plot (``review/core/plot_failure.py``).

A redraw that raised — a ``MemoryError`` on a machine run short, most often — used to be logged and, at best, named
on the one-slot status line, while the figure went on showing the drawing from before: made under settings the
controls no longer show, and indistinguishable from a redraw that worked. Each test here puts a drawing on a tab's
canvas, makes that tab's redraw fail through a real catch site, and asserts the drawing is covered by the sentence —
on that tab only — and the last tests that a redraw that works afterwards shows through again, whichever way the
tab draws.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from review.core.plot_failure import FAILED_GID, failure_sentence  # noqa: E402

MEMORY = ("This plot could not be drawn (the computer ran out of memory).\n"
          "What was drawn before is not shown, because it no longer matches the settings.\n"
          "Change a setting or come back to this tab to try again.")


def _page(qtbot):
    """A tab page holding a canvas with a line drawn on it: ``(page, canvas)``."""
    from matplotlib.figure import Figure
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from widgets.canvas import make_canvas

    page = QWidget()
    qtbot.addWidget(page)
    fig = Figure()
    fig.add_subplot(111).plot([0, 1], [0, 1], gid="the-old-drawing")
    canvas = make_canvas(fig, page)
    QVBoxLayout(page).addWidget(canvas)
    return page, canvas


def _ctx(frames: dict):
    from review.core.widget_registry import WidgetRegistry

    widgets = WidgetRegistry()
    widgets.register("_tab_frames", frames)
    return SimpleNamespace(widgets=widgets)


def _drawn(canvas) -> dict:
    """``{"covered": the failure cover is on the figure, "sentence": its text}``."""
    fig = canvas.figure
    covers = [ax for ax in fig.axes if ax.get_label() == FAILED_GID]
    texts = [t.get_text() for ax in covers for t in ax.texts if t.get_gid() == FAILED_GID]
    return {"covered": bool(covers), "sentence": texts}


def test_the_sentence_names_the_failure():
    assert failure_sentence(MemoryError()) == MEMORY
    assert failure_sentence(ValueError("no column 'x'")).startswith(
        "This plot could not be drawn (ValueError: no column 'x').\n")


def test_a_failed_scope_redraw_replaces_its_tab_s_drawing_and_no_other(qtbot):
    from review.domain.fold_change_scopes import FoldChangeScope, _call_redraw

    bar_page, bar = _page(qtbot)
    line_page, line = _page(qtbot)
    statuses: list[str] = []
    app = SimpleNamespace(_ctx=_ctx({"Bar Plots": bar_page, "Line Graphs": line_page}),
                          _set_status=statuses.append)

    def boom(_app):
        raise MemoryError()

    _call_redraw(app, FoldChangeScope(name="bar", ctrl_combo_attr="", baseline_combo_attr="",
                                      redraw=boom, tab_names=("Bar Plots",)))
    assert _drawn(bar) == {"covered": True, "sentence": [MEMORY]}
    assert _drawn(line) == {"covered": False, "sentence": []}
    assert statuses == ["Plot redraw failed (bar): "]


def test_a_failed_shared_fan_out_redraw_marks_that_tab(qtbot, monkeypatch):
    """Distribution and Heat Map in ``plot_orchestrator.redraw``'s own loop, with Distribution on screen."""
    from review.core import plot_orchestrator as po
    from review.domain import fold_change_scopes as fcs

    dist_page, dist = _page(qtbot)
    heat_page, heat = _page(qtbot)
    ctx = _ctx({"Distribution": dist_page, "Heat Map": heat_page})
    ctx.widgets.register("_distribution_canvas", dist)
    ctx.widgets.register("_heatmap_canvas", heat)
    ctx.window = None
    ctx.state = SimpleNamespace(plot=SimpleNamespace(fc_dirty_scopes=set()),
                                channel=SimpleNamespace(active_metric="mean"))
    drawn: list[str] = []

    def redraw(_ctx, tab):
        drawn.append(tab)
        if tab == "Distribution":
            raise MemoryError()

    ctx.features = SimpleNamespace(redraw=redraw)
    monkeypatch.setattr(fcs, "plots_held", lambda _ctx: False)
    monkeypatch.setattr(fcs, "known_visible_scopes", lambda _win: {"distribution", "heatmap"})
    po.redraw(ctx, lineplot_redraw=None, apply_ax_style=None, all_fluor_values=None,
              all_fluor_values_filtered=None, warn="#000000")
    assert drawn == ["Distribution", "Heat Map"]
    assert _drawn(dist) == {"covered": True, "sentence": [MEMORY]}
    assert _drawn(heat) == {"covered": False, "sentence": []}


def test_a_failed_nanowell_page_redraw_marks_the_page_and_does_not_raise(qtbot, monkeypatch):
    """The Nanowells page redraws from many places, most inside a ``swallow`` that would only log the failure."""
    from review.features.nanowell import controller as nwc

    page, canvas = _page(qtbot)
    ctx = _ctx({"Nanowells": page})

    def boom(_ctx):
        raise MemoryError()

    monkeypatch.setattr(nwc, "_redraw", boom)
    nwc.redraw(ctx)                                               # does not raise
    assert _drawn(canvas) == {"covered": True, "sentence": [MEMORY]}


def test_a_background_read_whose_drawing_fails_marks_its_tab(qtbot):
    from review.core import background_read

    page, canvas = _page(qtbot)
    ctx = _ctx({"Distribution": page})
    ctx.runtime = SimpleNamespace(reads=SimpleNamespace(jobs=None))
    ctx.state = SimpleNamespace(dataset=SimpleNamespace(data_dir="d"))
    drew: list = []

    def draw(_result):
        drew.append(1)
        raise MemoryError()

    assert background_read.start(ctx, "distribution", lambda: 1, on_done=draw, title="Distribution")
    qtbot.waitUntil(lambda: bool(drew), timeout=5000)
    assert _drawn(canvas) == {"covered": True, "sentence": [MEMORY]}


def test_a_page_with_no_known_title_is_left_alone(qtbot):
    from review.core.plot_failure import show_redraw_failed

    page, canvas = _page(qtbot)
    assert show_redraw_failed(_ctx({"Heat Map": page}), ("Bar Plots",), MemoryError()) == 0
    assert show_redraw_failed(SimpleNamespace(), ("Heat Map",), MemoryError()) == 0
    assert _drawn(canvas) == {"covered": False, "sentence": []}


def test_a_failed_scatter_redraw_after_a_group_edit_marks_the_scatter_tab(qtbot, monkeypatch):
    """Scatter is the one Plotting leaf with no redraw scope, so a group edit redraws it by name."""
    from review.core import selection_controller as sc
    from review.domain import fold_change_scopes as fcs

    page, canvas = _page(qtbot)
    ctx = _ctx({"Scatter Plot": page})
    ctx.window = SimpleNamespace(_invalidate_stats_cache=lambda: None, _groups_centre_refresh=lambda: None,
                                 _refresh_sidebar_map=lambda: None,
                                 _is_tab_visible=lambda name: name == "Scatter Plot")

    def redraw(_ctx, tab):
        assert tab == "Scatter Plot"
        raise MemoryError()

    ctx.features = SimpleNamespace(redraw=redraw)
    monkeypatch.setattr(fcs, "redraw_scopes_or_defer", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_schedule_sample_def_autosave", lambda _ctx: None)
    sc.rebuild_all(ctx)
    assert _drawn(canvas) == {"covered": True, "sentence": [MEMORY]}


# ── the cover comes off when the tab draws again ─────────────────────────────


def _cover(qtbot):
    from review.core.plot_failure import show_redraw_failed

    page, canvas = _page(qtbot)
    show_redraw_failed(_ctx({"Heat Map": page}), ("Heat Map",), MemoryError())
    canvas.draw()                                                 # the cover's own draw must not lift it
    assert _drawn(canvas)["covered"]
    return page, canvas                                           # the page too: dropping it deletes the canvas


def test_a_tab_that_redraws_into_the_axes_it_was_built_with_shows_through(qtbot):
    """Heat Map and Response Curves keep their Axes in the widget registry and draw into it every time. Clearing the
    figure — what the notice first did — took that Axes off the figure, and every later redraw drew into nothing."""
    _page_kept, canvas = _cover(qtbot)
    kept = [ax for ax in canvas.figure.axes if ax.get_label() != FAILED_GID][0]
    kept.clear()
    kept.imshow([[0, 1], [1, 0]], gid="the-new-drawing")
    canvas.draw()
    assert _drawn(canvas) == {"covered": False, "sentence": []}
    assert kept in canvas.figure.axes


def test_a_tab_that_rebuilds_its_figure_shows_through(qtbot):
    _page_kept, canvas = _cover(qtbot)
    canvas.figure.clear()
    canvas.figure.add_subplot(111).plot([1, 2], [2, 1])
    canvas.draw()
    assert _drawn(canvas) == {"covered": False, "sentence": []}


def test_a_failed_line_graph_is_marked_and_the_figures_after_it_are_still_drawn(qtbot, monkeypatch):
    """The line figure is drawn first in the shared fan-out; raised from there, it skipped Distribution and the Heat
    Map, and reached a caller that on a QC edit only logs it."""
    from review.core import plot_orchestrator as po
    from review.domain import fold_change_scopes as fcs

    line_page, line = _page(qtbot)
    dist_page, dist = _page(qtbot)
    ctx = _ctx({"Line Graphs": line_page, "Distribution": dist_page})
    ctx.widgets.register("_distribution_canvas", dist)
    ctx.window = None
    ctx.state = SimpleNamespace(plot=SimpleNamespace(fc_dirty_scopes=set()),
                                channel=SimpleNamespace(active_metric="mean"))
    drawn: list[str] = []
    ctx.features = SimpleNamespace(redraw=lambda _ctx, tab: drawn.append(tab))

    def line_redraw(_ctx, **_kw):
        raise MemoryError()

    monkeypatch.setattr(fcs, "plots_held", lambda _ctx: False)
    monkeypatch.setattr(fcs, "known_visible_scopes", lambda _win: {"line", "distribution"})
    po.redraw(ctx, lineplot_redraw=line_redraw, apply_ax_style=None, all_fluor_values=None,
              all_fluor_values_filtered=None, warn="#000000")
    assert _drawn(line) == {"covered": True, "sentence": [MEMORY]}
    assert drawn == ["Distribution"]
    assert _drawn(dist) == {"covered": False, "sentence": []}
