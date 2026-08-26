"""The Properties → Overlays value-label controls, driven the way a user drives
them.

CLAUDE.md §7.6: a control that *exists* is not a control that *works*. A test
that finds a "Show value labels" switch in the sidebar passes just as happily
against one whose ``toggled`` signal was never connected, or connected to a
closure that raises before it writes — which is how the per-sample control
editor shipped unopenable behind eighteen green test files.

So these press the switch and type in the field, then assert on what came out
the other side: the state field the Bar and Line renderers read, and the redraw
without which the change never reaches the screen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QGridLayout, QLabel, QLineEdit

from well_viewer.core.cell_gating_state import CellGatingState
from well_viewer.core.export_prefs_state import ExportPrefsState
from well_viewer.core.plot_state import PlotState
from well_viewer.features.scatter.state import ScatterState
from widgets.toggle_switch import ToggleSwitch

pytestmark = pytest.mark.qt


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

    A real ``PlotState``, deliberately: a ``SimpleNamespace`` stand-in would
    accept whatever attribute name the sidebar wrote to, misspellings included,
    and the whole point of these tests is that the name the sidebar writes is
    the name the renderers read.
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
    """The built sidebar, plus the redraws it asked for.

    ``_redraw_active_plot`` is the only route from these controls to the
    screen, so it is recorded rather than stubbed away: a control that writes
    the state and never asks for a redraw leaves the plot bare until something
    unrelated repaints it.
    """
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    redraws: list[int] = []
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_active_plot",
                        lambda self: redraws.append(1), raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="bar")
    qtbot.addWidget(sb)
    return app, sb, redraws


def _overlays_section(sidebar):
    from widgets.collapsible_section import CollapsibleSection

    for sec in sidebar.findChildren(CollapsibleSection):
        if sec.titleText() == "Overlays":
            return sec
    return None


def _value_switch(sidebar) -> ToggleSwitch | None:
    sec = _overlays_section(sidebar)
    if sec is None:
        return None
    for tog in sec.findChildren(ToggleSwitch):
        if tog.text() == "Show value labels":
            return tog
    return None


def _format_field(sidebar) -> QLineEdit | None:
    """The "Value format" row's edit, found by the label the user reads."""
    sec = _overlays_section(sidebar)
    if sec is None:
        return None
    for grid in sec.findChildren(QGridLayout):
        for row in range(grid.rowCount()):
            lbl_item = grid.itemAtPosition(row, 0)
            ctl_item = grid.itemAtPosition(row, 1)
            if lbl_item is None or ctl_item is None:
                continue
            lbl, ctl = lbl_item.widget(), ctl_item.widget()
            if (isinstance(lbl, QLabel) and isinstance(ctl, QLineEdit)
                    and lbl.text() == "Value format"):
                return ctl
    return None


def test_the_overlays_section_offers_both_controls(qtbot, monkeypatch):
    _app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    assert _value_switch(sb) is not None, "no 'Show value labels' switch"
    assert _format_field(sb) is not None, "no 'Value format' field"


def test_the_switch_starts_from_the_state(qtbot, monkeypatch):
    _app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    assert _value_switch(sb).isChecked() is False


def test_flipping_the_switch_reaches_the_state_a_renderer_reads(qtbot, monkeypatch):
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)
    _value_switch(sb).setChecked(True)
    assert app._app_state.plot.show_value_labels is True
    assert redraws, "the overlay was switched on but nothing asked for a redraw"


def test_flipping_it_back_turns_the_overlay_off(qtbot, monkeypatch):
    app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    sw = _value_switch(sb)
    sw.setChecked(True)
    sw.setChecked(False)
    assert app._app_state.plot.show_value_labels is False


def test_typing_a_format_reaches_the_state_a_renderer_reads(qtbot, monkeypatch):
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)
    _value_switch(sb).setChecked(True)
    before = len(redraws)
    fld = _format_field(sb)
    fld.setText("%.1f")
    fld.editingFinished.emit()
    assert app._app_state.plot.value_label_format == "%.1f"
    assert len(redraws) > before, (
        "the format changed while the labels were on and nothing repainted them"
    )


def test_the_format_is_stored_even_while_the_overlay_is_off(qtbot, monkeypatch):
    """Typing a format with the labels off must still be remembered — the
    alternative is a field that silently discards what the user typed until
    they happen to flip the switch first."""
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)
    fld = _format_field(sb)
    fld.setText("%d")
    fld.editingFinished.emit()
    assert app._app_state.plot.value_label_format == "%d"
    assert not redraws, "nothing is labelled, so nothing needed repainting"


def test_the_switch_does_not_disturb_the_threshold_marker(qtbot, monkeypatch):
    """The two Overlays toggles are independent. A copy-pasted handler still
    writing its neighbour's field would make turning one on clear the other."""
    app, sb, _redraws = _build_sidebar(qtbot, monkeypatch)
    app._app_state.plot.show_threshold_line = True
    _value_switch(sb).setChecked(True)
    assert app._app_state.plot.show_threshold_line is True
