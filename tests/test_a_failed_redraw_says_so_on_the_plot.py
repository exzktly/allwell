"""A plot whose redraw fails says so on the plot (``review/core/plot_failure.py``).

A redraw that raised — a ``MemoryError`` on a machine run short, most often — used to be logged and, at best, named
on the one-slot status line, while the figure went on showing the drawing from before: made under settings the
controls no longer show, and indistinguishable from a redraw that worked. Each test here puts a drawing on a tab's
canvas, makes that tab's redraw fail through a real catch site, and asserts the drawing is gone and the sentence is
there — on that tab only.
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
    fig = canvas.figure
    gids = [a.get_gid() for ax in fig.axes for a in [*ax.lines, *ax.texts]]
    texts = [t.get_text() for ax in fig.axes for t in ax.texts if t.get_gid() == FAILED_GID]
    return {"old": "the-old-drawing" in gids, "sentence": texts}


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
    assert _drawn(bar) == {"old": False, "sentence": [MEMORY]}
    assert _drawn(line) == {"old": True, "sentence": []}
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
    assert _drawn(dist) == {"old": False, "sentence": [MEMORY]}
    assert _drawn(heat) == {"old": True, "sentence": []}


def test_a_failed_nanowell_page_redraw_marks_the_page_and_does_not_raise(qtbot, monkeypatch):
    """The Nanowells page redraws from many places, most inside a ``swallow`` that would only log the failure."""
    from review.features.nanowell import controller as nwc

    page, canvas = _page(qtbot)
    ctx = _ctx({"Nanowells": page})

    def boom(_ctx):
        raise MemoryError()

    monkeypatch.setattr(nwc, "_redraw", boom)
    nwc.redraw(ctx)                                               # does not raise
    assert _drawn(canvas) == {"old": False, "sentence": [MEMORY]}


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
    assert _drawn(canvas) == {"old": False, "sentence": [MEMORY]}


def test_a_page_with_no_known_title_is_left_alone(qtbot):
    from review.core.plot_failure import show_redraw_failed

    page, canvas = _page(qtbot)
    assert show_redraw_failed(_ctx({"Heat Map": page}), ("Bar Plots",), MemoryError()) == 0
    assert show_redraw_failed(SimpleNamespace(), ("Heat Map",), MemoryError()) == 0
    assert _drawn(canvas) == {"old": True, "sentence": []}


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
    assert _drawn(canvas) == {"old": False, "sentence": [MEMORY]}
