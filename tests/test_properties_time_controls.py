"""The Properties → Time controls, driven the way a user drives them.

§7.6 of CLAUDE.md, applied before it had to be: a control that *exists* is not a
control that *works*. Asserting that the Time section offers an "Offset" row
would pass against a stepper whose ``valueChanged`` was never connected, or
connected to a closure that raises before it writes — which is exactly how the
per-sample control editor shipped unopenable.

So these press the controls. Each finds the real widget inside the built
sidebar, calls ``setValue`` on it the way the user's click does, and asserts on
what came out the other side: the state field a renderer will read, and the
redraw the change has to trigger to reach the screen.

The two controls are checked independently and together, because the failure
this feature can produce is one of them silently doing nothing — an axis
corrected by half the user's setting, carrying a marker that says it was
corrected in full.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QGridLayout, QLabel

from well_viewer.core.cell_gating_state import CellGatingState
from well_viewer.core.export_prefs_state import ExportPrefsState
from well_viewer.core.plot_state import PlotState
from well_viewer.features.scatter.state import ScatterState
from widgets.stepper import Stepper

pytestmark = pytest.mark.qt


def _mk_ctx(app):
    """The same shape the other sidebar tests build: state/runtime/widgets all
    resolve through the fake app's own attributes."""
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

    A real ``PlotState`` rather than a ``SimpleNamespace``, because the two
    fields under test are the point: a stand-in would accept any attribute name
    the sidebar wrote to, including a misspelled one.
    """
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


def _build_sidebar(qtbot, monkeypatch):
    """The built sidebar, plus the list of redraws it asked for.

    ``_redraw_active_plot`` is the only route from either control to the screen,
    so it is recorded rather than stubbed away — a control that updates the
    state and never asks for a redraw leaves the figure showing the old axis
    until something unrelated happens to repaint it.
    """
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    redraws: list[int] = []
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_active_plot",
                        lambda self: redraws.append(1), raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="line")
    qtbot.addWidget(sb)
    return app, sb, redraws


def _time_steppers(sidebar) -> dict[str, Stepper]:
    """Every ``Stepper`` in the Time section, keyed by its own row label.

    Found through the built widget tree rather than by tooltip text or by
    construction order: the row label is what the user reads, so a rename that
    moves the control out from under this lookup is a rename the test should
    notice.
    """
    from widgets.collapsible_section import CollapsibleSection

    for sec in sidebar.findChildren(CollapsibleSection):
        if sec.titleText() != "Time":
            continue
        grid = next((g for g in sec.findChildren(QGridLayout)), None)
        if grid is None:
            continue
        found: dict[str, Stepper] = {}
        for row in range(grid.rowCount()):
            lbl_item = grid.itemAtPosition(row, 0)
            ctl_item = grid.itemAtPosition(row, 1)
            if lbl_item is None or ctl_item is None:
                continue
            lbl, ctl = lbl_item.widget(), ctl_item.widget()
            if isinstance(lbl, QLabel) and isinstance(ctl, Stepper):
                found[lbl.text()] = ctl
        if "Interval per timepoint" in found:
            return found
    return {}


def test_the_time_section_offers_both_controls(qtbot, monkeypatch):
    _app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    steppers = _time_steppers(sb)
    assert set(steppers) >= {"Interval per timepoint", "Offset"}, (
        f"Time section rows found: {sorted(steppers)}")


def test_typing_an_offset_reaches_the_state_a_renderer_reads(qtbot, monkeypatch):
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)
    _time_steppers(sb)["Offset"].setValue(-2.0)
    assert app._app_state.plot.time_offset == -2.0
    assert redraws, "the offset changed but nothing asked for a redraw"


def test_typing_an_interval_reaches_the_state_a_renderer_reads(qtbot, monkeypatch):
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)
    _time_steppers(sb)["Interval per timepoint"].setValue(4.0)
    assert app._app_state.plot.time_scale == 4.0
    assert redraws, "the interval changed but nothing asked for a redraw"


def test_each_control_leaves_the_other_alone(qtbot, monkeypatch):
    """They are two independent statements about the plate. A shared handler,
    or a copy-pasted one still writing the other's field, would make setting
    either silently clear the other."""
    app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    steppers = _time_steppers(sb)

    steppers["Interval per timepoint"].setValue(4.0)
    assert app._app_state.plot.time_offset == 0.0

    steppers["Offset"].setValue(-2.0)
    assert app._app_state.plot.time_scale == 4.0


def test_what_the_controls_write_is_what_the_renderers_read(qtbot, monkeypatch):
    """The seam that matters: the two fields the sidebar writes are the two
    ``rescale_for`` reads, and the correction that reaches a figure is the one
    the user typed."""
    from well_viewer.domain.time_rescale import TimeRescale, format_scaled, rescale_for

    app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    steppers = _time_steppers(sb)
    steppers["Interval per timepoint"].setValue(4.0)
    steppers["Offset"].setValue(-2.0)

    rescale = rescale_for(app._app_state.plot)
    assert rescale == TimeRescale(factor=4.0, offset=-2.0)
    assert format_scaled(1, rescale) == "2"


def test_a_negative_offset_is_accepted_by_the_control(qtbot, monkeypatch):
    """The ordinary case for this control, and the one a range copied from the
    interval stepper (which refuses anything ≤ 0) would silently clamp away."""
    app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    _time_steppers(sb)["Offset"].setValue(-6.5)
    assert app._app_state.plot.time_offset == -6.5


def test_the_section_summary_names_both_halves(qtbot, monkeypatch):
    """The collapsed section shows one line. Built from the live state, so
    editing either control redraws a summary describing both — one built from
    the value just changed would read "Off" on an axis still being shifted."""
    from PySide6.QtWidgets import QLabel as _QLabel
    from widgets.collapsible_section import CollapsibleSection

    app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    steppers = _time_steppers(sb)

    sec = next(s for s in sb.findChildren(CollapsibleSection)
               if s.titleText() == "Time")
    preview = next(w for w in sec.findChildren(_QLabel)
                   if w.objectName() == "Muted")

    assert preview.text() == "Off"
    steppers["Offset"].setValue(-2.0)
    assert preview.text() == "−2"
    steppers["Interval per timepoint"].setValue(4.0)
    assert preview.text() == "×4 −2"
    assert app._app_state.plot.time_scale == 4.0
