"""The Properties → Colours segment, pressed the way a user presses it.

§7.6 of CLAUDE.md, applied before it has to be: a control that *exists* is not
a control that *works*. A test that only asserted the "Grey" segment was
offered would pass against a segment whose ``currentChanged`` was never
connected, or connected to a closure that raises before it writes — which is
exactly how the per-sample control editor shipped unopenable.

So this file finds the real segmented control inside the built sidebar, selects
a segment the way a click does, and asserts on what came out the other side:
the state field the renderers read, and the redraw the change has to trigger to
reach the screen.

It also pins the gate. The mode changes nothing on a line, scatter or heat-map
figure, so the section must not appear on one — a Colours row on the Heat Map's
Properties would be a control that does nothing, which is the same defect wearing
the opposite face.

And it pins the re-seed. The sidebar is built once per figure and cached for the
life of the process, while ``persistence.view_state`` writes the same field on
every dataset load and on a reset. A segment left showing the previous dataset's
mode is not merely a stale label: re-selecting the option a ``SegmentedControl``
already believes is current emits nothing, so clicking the mode the user wants
does nothing until they click the other one first. An adversarial review found
that; the tests here could not, because every one of them builds a *fresh*
sidebar, which is the one case that cannot go stale.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import theme
from well_viewer.core.cell_gating_state import CellGatingState
from well_viewer.core.export_prefs_state import ExportPrefsState
from well_viewer.core.plot_state import PlotState
from well_viewer.features.scatter.state import ScatterState

pytestmark = pytest.mark.qt

_SECTION = "Colours"


def _mk_ctx(app):
    from well_viewer.core.app_runtime import AppRuntime
    return SimpleNamespace(
        app=app,
        state=app._app_state,
        runtime=AppRuntime(app),
        widgets=SimpleNamespace(
            get_or_none=lambda n: getattr(app, n, None),
            register=lambda n, w: (setattr(app, n, w), w)[1],
        ),
        notify=SimpleNamespace(set_status=lambda _m: None),
    )


def _stub_app():
    """The smallest app the sidebar will build against.

    A real ``PlotState`` rather than a ``SimpleNamespace``: the field name is
    the point, and a stand-in would accept a misspelled one just as happily."""
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(data_dir=None),
        plot=PlotState(),
        selection=SimpleNamespace(selected_wells=["A01"]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._app_state.cell_gating.scatter_gates = []
    app._ctx = _mk_ctx(app)
    app._rep_sets_active = lambda: []
    app._parse_rc = lambda lbl: (lbl, 0, 0)
    app._well_display_label = lambda w: w
    app._replicate_display_label = lambda rs: getattr(rs, "name", "")
    app._use_fov_spread_active = lambda: False
    app._toggle_sem = lambda: None
    app._toggle_fov_replicates = lambda: None
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    return app


def _build_sidebar(qtbot, monkeypatch, *, is_bar: bool):
    """The built sidebar, plus the list of redraws it asked for.

    ``_redraw_bound_figure`` is the only route from this control to the screen,
    so it is recorded rather than stubbed away — a segment that updates the
    state and never asks for a redraw leaves the figure in the old colours
    until something unrelated repaints it."""
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    if is_bar:
        app._bar_fig = fig
    redraws: list[int] = []
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        lambda self: redraws.append(1), raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="bar")
    qtbot.addWidget(sb)
    return app, sb, redraws


def _colour_segment(sidebar):
    """The SegmentedControl inside the Colours section, or ``None``.

    Found through the built widget tree by the section's own title — the words
    the user reads — so a rename that moves the control out from under this
    lookup is a rename the test should notice."""
    from widgets.collapsible_section import CollapsibleSection
    from widgets.segmented_control import SegmentedControl

    for sec in sidebar.findChildren(CollapsibleSection):
        if sec.titleText() != _SECTION:
            continue
        segs = sec.findChildren(SegmentedControl)
        if segs:
            return segs[0]
    return None


def test_the_bar_figure_offers_both_modes(qtbot, monkeypatch):
    _app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    seg = _colour_segment(sb)
    assert seg is not None, "no Colours segment on the bar figure's Properties"
    labels = [seg.segmentText(i) for i in range(seg.count())]
    assert labels == ["Plate", "Grey"], f"segments offered: {labels}"
    # The labels are what the user reads; the data is what the handler writes.
    # Both halves are asserted because a segment can carry the right words and
    # the wrong payload, and only the payload reaches the state.
    for data in ("plate", "grey"):
        seg.setCurrentByData(data)
        assert seg.currentData() == data


def test_picking_grey_reaches_the_state_the_renderers_read(qtbot, monkeypatch):
    from well_viewer.domain.bar_colors import mono_bar_color

    app, sb, redraws = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    _colour_segment(sb).setCurrentByData("grey")
    assert app._app_state.plot.bar_color_mode == "grey"
    # Not "the state changed" but "the state the draw path reads changed".
    assert mono_bar_color(app._app_state.plot) == theme.Colors.plot_mono
    assert redraws, "the colour mode changed but nothing asked for a redraw"


def test_picking_plate_again_restores_the_original_colours(qtbot, monkeypatch):
    """The mode has to be reversible from the same control. A handler that only
    ever writes "grey" (a segment wired to a constant rather than to its own
    data) passes the test above and strands the user in grey."""
    from well_viewer.domain.bar_colors import mono_bar_color

    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    seg = _colour_segment(sb)
    seg.setCurrentByData("grey")
    seg.setCurrentByData("plate")
    assert app._app_state.plot.bar_color_mode == "plate"
    assert mono_bar_color(app._app_state.plot) is None


def test_the_section_opens_on_whatever_mode_is_already_set(qtbot, monkeypatch):
    """The sidebar is rebuilt whenever it is reopened. A segment that always
    initialised to "plate" would show the wrong mode against a grey figure —
    and the next click on "grey" would then be a no-op, because the widget
    already thinks it is there."""
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    app._bar_fig = fig
    app._app_state.plot.bar_color_mode = "grey"
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        lambda self: None, raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="bar")
    qtbot.addWidget(sb)
    assert _colour_segment(sb).currentData() == "grey"


def test_a_non_bar_figure_is_not_offered_a_control_that_would_do_nothing(
        qtbot, monkeypatch):
    _app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=False)
    assert _colour_segment(sb) is None


def test_reselecting_the_current_segment_emits_nothing(qtbot, monkeypatch):
    """The mechanism behind the staleness bug, pinned so the reasoning below it
    cannot quietly stop being true. If ``SegmentedControl`` ever starts emitting
    on a no-op selection, the re-seed becomes belt-and-braces rather than
    load-bearing — and this test says so rather than leaving a comment that has
    silently become wrong."""
    _app, sb, redraws = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    seg = _colour_segment(sb)
    seg.setCurrentByData("plate")          # already current
    assert not redraws, "a no-op selection asked for a redraw"


def test_a_stale_segment_is_re_seeded_when_the_panel_is_reopened(qtbot, monkeypatch):
    """Load dataset A, pick Grey; load dataset B whose saved state says Plate.

    ``_apply_bar`` writes the field behind the cached widget's back. Without the
    re-seed the segment still reads Grey over plate-coloured bars, and the click
    that should fix it is swallowed as a no-op.
    """
    from well_viewer.persistence import view_state as vs

    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    seg = _colour_segment(sb)
    seg.setCurrentByData("grey")
    assert app._app_state.plot.bar_color_mode == "grey"

    # What landing on the Bar tab after loading another dataset does.
    vs._apply_bar(
        SimpleNamespace(
            state=SimpleNamespace(plot=app._app_state.plot,
                                  bar=SimpleNamespace(tp_selections={}),
                                  feature_slice=lambda _n: None),
            widgets=SimpleNamespace(get_or_none=lambda _n: None),
        ),
        {"bar_color_mode": "plate"},
    )
    assert app._app_state.plot.bar_color_mode == "plate"
    assert seg.currentData() == "grey", "precondition: the widget is now stale"

    sb._resync_plot_state_segments()
    assert seg.currentData() == "plate"
    # And the click that was dead before now reaches the handler.
    seg.setCurrentByData("grey")
    assert app._app_state.plot.bar_color_mode == "grey"


def test_the_re_seed_does_not_redraw_or_rewrite_the_state(qtbot, monkeypatch):
    """It runs on every panel open. Letting the widget's signal fire would run
    the handler and redraw a heavy 3-panel bar figure each time, and would write
    back the value it just read."""
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    app._app_state.plot.bar_color_mode = "grey"
    sb._resync_plot_state_segments()
    assert _colour_segment(sb).currentData() == "grey"
    assert app._app_state.plot.bar_color_mode == "grey"
    assert not redraws, "the re-seed asked for a redraw"


def test_the_re_seed_moves_the_section_summary_with_the_segment(qtbot, monkeypatch):
    """A segment that catches up while its collapsed-section header does not is
    the drift the Error bars observer already documents."""
    from PySide6.QtWidgets import QLabel

    from widgets.collapsible_section import CollapsibleSection

    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    app._app_state.plot.bar_color_mode = "grey"
    sb._resync_plot_state_segments()
    section = next(s for s in sb.findChildren(CollapsibleSection)
                   if s.titleText() == _SECTION)
    texts = {lbl.text() for lbl in section.findChildren(QLabel)}
    assert "Grey" in texts, f"section header did not follow the segment: {texts}"


def test_the_show_segment_is_re_seeded_too(qtbot, monkeypatch):
    """The Colours segment's sibling: ``bar_show_mode`` is written by the same
    two persistence paths and has no observer either, so it goes stale the same
    way. Fixing one and not the other would leave the panel holding two answers
    to one question (CLAUDE.md §7.5 #5)."""
    from widgets.segmented_control import SegmentedControl

    from widgets.collapsible_section import CollapsibleSection

    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    stats = next(s for s in sb.findChildren(CollapsibleSection)
                 if s.titleText() == "Statistics")
    show = next(s for s in stats.findChildren(SegmentedControl)
                if {s.segmentText(i) for i in range(s.count())} == {
                    "Mean", "Mean+spread", "All points"})
    assert show.currentData() == "mean_spread"
    app._app_state.plot.bar_show_mode = "all_pts"
    sb._resync_plot_state_segments()
    assert show.currentData() == "all_pts"
