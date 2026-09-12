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

from ui import theme
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
    # What the handler tells the user when the redraw does not run. Recorded
    # rather than discarded: the message is half the control's contract, and
    # the half that was wrong.
    app._statuses = []
    app._set_status = app._statuses.append
    return app


def _build_sidebar(qtbot, monkeypatch, *, is_bar: bool = False,
                   is_line: bool = False, is_response: bool = False,
                   redraw_ok: bool = True):
    """The built sidebar, plus the list of redraws it asked for.

    ``_redraw_bound_figure`` is the only route from this control to the screen,
    so it is recorded rather than stubbed away — a segment that updates the
    state and never asks for a redraw leaves the figure in the old colours
    until something unrelated repaints it. The stub returns *redraw_ok*, which
    is the value the real one returns and the value the handler branches on."""
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    if is_bar:
        app._bar_fig = fig
    if is_line:
        app._line_fig = fig
    if is_response:
        app._response_fig = fig
    redraws: list[int] = []

    def _record(self):
        redraws.append(1)
        return redraw_ok
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        _record, raising=False)
    sb = ExportStyleSidebar(
        app, None, fig, None,
        default_name=("response" if is_response
                      else "line" if is_line else "bar"))
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


def test_a_figure_the_mode_does_not_reach_is_not_offered_a_control(
        qtbot, monkeypatch):
    """Scatter, Distribution and the Heat Map draw neither bars nor traces, so
    a Colours row on their Properties would be a control that does nothing —
    the same defect as a control that is offered and broken, wearing the
    opposite face."""
    _app, sb, _r = _build_sidebar(qtbot, monkeypatch)
    assert _colour_segment(sb) is None


# ── the Line tab's own mode ─────────────────────────────────────────────────

def test_the_line_figure_offers_both_modes(qtbot, monkeypatch):
    _app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_line=True)
    seg = _colour_segment(sb)
    assert seg is not None, "no Colours segment on the line figure's Properties"
    assert [seg.segmentText(i) for i in range(seg.count())] == ["Plate", "Grey"]


def test_picking_grey_on_the_line_figure_writes_the_line_field(qtbot, monkeypatch):
    """Its **own** field. One ``_color_seg`` attribute now holds whichever of
    the two tabs' segments the panel was built for, so a handler still writing
    ``bar_color_mode`` would grey the Bar tab from the Line tab's control and
    leave the traces coloured."""
    from well_viewer.domain.line_colors import line_color_mode

    app, sb, redraws = _build_sidebar(qtbot, monkeypatch, is_line=True)
    _colour_segment(sb).setCurrentByData("grey")
    assert app._app_state.plot.line_color_mode == "grey"
    assert app._app_state.plot.bar_color_mode == "plate", (
        "the Line control wrote the Bar tab's field")
    assert line_color_mode(app._app_state.plot) == "grey"
    assert redraws, "the mode changed but nothing asked for a redraw"


def test_the_line_segment_is_re_seeded_from_the_line_field(qtbot, monkeypatch):
    """The re-seed reads whichever field this panel's segment writes. Reading
    the bar field on a line panel would move the widget with signals blocked —
    it then shows a mode the figure is not drawn in, and the click that should
    fix it is swallowed as a no-op, which is the exact bug the re-seed exists
    for."""
    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_line=True)
    app._app_state.plot.line_color_mode = "grey"
    app._app_state.plot.bar_color_mode = "plate"
    sb._resync_plot_state_segments()
    assert _colour_segment(sb).currentData() == "grey"


def test_a_successful_redraw_does_not_report_a_failure(qtbot, monkeypatch):
    """``_redraw_bound_figure`` returned ``None`` on every path while the
    handler tested its truthiness, so a redraw that worked told the user it had
    not: "…but the plot could not be redrawn — switch tabs and back". A message
    that is wrong whenever things work is worse than none, because the one time
    it is right reads the same as all the others."""
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch, is_bar=True,
                                      redraw_ok=True)
    _colour_segment(sb).setCurrentByData("grey")
    assert redraws, "no redraw was attempted"
    assert app._statuses == [], f"a working redraw reported a failure: {app._statuses}"


def test_a_failed_redraw_still_says_so(qtbot, monkeypatch):
    """The other direction, which is the whole point of the message."""
    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=True, redraw_ok=False)
    _colour_segment(sb).setCurrentByData("grey")
    assert any("could not be redrawn" in m for m in app._statuses), app._statuses


def test_the_real_redraw_reports_whether_it_ran(qtbot, monkeypatch):
    """The stub above returns a bool because the real one does. Pinned here so
    the two cannot drift — a stub that is more honest than the code it stands
    in for tests nothing."""
    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig_app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_bar=True)
    monkeypatch.undo()
    calls: list[str] = []
    monkeypatch.setattr("well_viewer.features.export.sidebar_view.redraw_bars",
                        lambda _ctx: calls.append("bars"))
    assert ExportStyleSidebar._redraw_bound_figure(sb) is True
    assert calls == ["bars"]


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


# ── Response Curves' own mode ───────────────────────────────────────────────

def test_the_response_figure_offers_series_and_grey(qtbot, monkeypatch):
    """"Series", not "Plate". A response curve is a *series* pooled across
    however many wells hold that value, so it has no plate-picker colour to
    take — the other two tabs' label would name a thing this figure does not
    do. The data values are asserted as well as the words, because a segment
    can carry the right label and the wrong payload and only the payload
    reaches the state."""
    _app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_response=True)
    seg = _colour_segment(sb)
    assert seg is not None, "no Colours segment on the response figure"
    assert [seg.segmentText(i) for i in range(seg.count())] == ["Series", "Grey"]
    for data in ("series", "grey"):
        seg.setCurrentByData(data)
        assert seg.currentData() == data


def test_picking_grey_on_the_response_figure_writes_its_own_field(
        qtbot, monkeypatch):
    from well_viewer.domain.response_colors import response_color_mode

    app, sb, redraws = _build_sidebar(qtbot, monkeypatch, is_response=True)
    _colour_segment(sb).setCurrentByData("grey")
    assert app._app_state.plot.response_color_mode == "grey"
    assert response_color_mode(app._app_state.plot) == "grey"
    assert app._app_state.plot.bar_color_mode == "plate", (
        "the Response control wrote the Bar tab's field")
    assert app._app_state.plot.line_color_mode == "plate", (
        "the Response control wrote the Line tab's field")
    assert redraws, "the mode changed but nothing asked for a redraw"


def test_picking_series_again_is_reachable_from_the_same_control(
        qtbot, monkeypatch):
    """Reversible, and reversible to the value this tab's default actually is.
    A handler that fell back to the two other tabs' ``"plate"`` would write a
    mode this segment does not offer, leaving the control unable to show what
    the state says."""
    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_response=True)
    seg = _colour_segment(sb)
    seg.setCurrentByData("grey")
    seg.setCurrentByData("series")
    assert app._app_state.plot.response_color_mode == "series"


def test_the_response_segment_is_re_seeded_from_its_own_field(qtbot, monkeypatch):
    """The re-seed reads the field this panel's segment writes, not the bar
    one. Reading the bar field on a response panel would move the widget with
    signals blocked — it then shows a mode the figure is not drawn in, and the
    click that should fix it is swallowed as a no-op."""
    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_response=True)
    app._app_state.plot.response_color_mode = "grey"
    app._app_state.plot.bar_color_mode = "plate"
    sb._resync_plot_state_segments()
    assert _colour_segment(sb).currentData() == "grey"

    app._app_state.plot.response_color_mode = "series"
    sb._resync_plot_state_segments()
    assert _colour_segment(sb).currentData() == "series"


def test_an_empty_mode_re_seeds_to_this_tab_s_default_not_the_others(
        qtbot, monkeypatch):
    """The one case the recorded default is reachable in.

    ``_resync_plot_state_segments`` reads ``getattr(plot, field, default) or
    default``, and the field is always present on a real ``PlotState`` — so the
    default is only consulted for a *falsy* value, which is what an emptied or
    hand-edited ``persistence.json`` leaves behind. Three tabs now and only two
    of them default to "plate": with the old hardcoded one, this asks
    ``setCurrentByData`` for a value the Response segment does not carry, and
    the control keeps showing whatever it showed before while the figure draws
    something else.

    Written after watching the first version of this test pass against the
    hardcoded default — it seeded the field with a real mode, which never
    reaches the fallback at all.
    """
    app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_response=True)
    seg = _colour_segment(sb)
    seg.setCurrentByData("grey")
    app._app_state.plot.response_color_mode = ""
    sb._resync_plot_state_segments()
    assert seg.currentData() == "series", (
        f"re-seeded to {seg.currentData()!r}; the Response segment offers "
        f"{[seg.segmentText(i) for i in range(seg.count())]}")


def test_the_real_redraw_reaches_the_response_figure(qtbot, monkeypatch):
    """Not any figure — *this* one.

    ``_redraw_bound_figure`` falls through to the Line tab's redraw for a figure
    it cannot place, so a Response figure it did not reach meant the control set
    the mode, repainted the Line plot, and reported success while Response
    stayed exactly as it was. It is placed through ``_FIGURE_OWNING_FEATURES``,
    which asks the feature registry for the redraw rather than naming a function
    here, so this drives the real dispatch and asserts the fall-through was
    **not** what ran — the half a bare "did something redraw?" assertion would
    miss, since the fall-through redraws too.
    """
    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    _app, sb, _r = _build_sidebar(qtbot, monkeypatch, is_response=True)
    monkeypatch.undo()
    calls: list[str] = []
    sb._app._ctx.features = SimpleNamespace(
        redraw=lambda _ctx, feature: (calls.append(feature), True)[1])
    sb._app._redraw = lambda: calls.append("the Line tab's fall-through")
    assert ExportStyleSidebar._redraw_bound_figure(sb) is True
    assert calls == ["Response Curves"], calls
