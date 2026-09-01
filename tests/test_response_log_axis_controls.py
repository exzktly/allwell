"""The Response tab's log x axis, from the control the user actually touches.

Two reports, one root: switching the Properties panel's *X log* toggle on the
Response Curves tab did nothing, and there was no way to say what base the axis
should be drawn in.

"Did nothing" was literal, and it was three faults deep:

1. **Nothing redrew the figure.** ``_redraw_bound_figure`` dispatches on figure
   identity from a roster of six, and the Response figure was not in it, so the
   toggle fell through to ``app._redraw()`` — the *Line* tab's redraw. The
   control repainted a figure the user was not looking at and reported success.
2. **The registry could not have redrawn it either.** The descriptor's
   ``redraw`` is a lazy *factory*; Response Curves declared a ``_lazy_hook``,
   one shape short, so ``features.redraw(ctx, "Response Curves")`` raised
   ``TypeError`` before reaching the figure.
3. **The restyle path could never have done it alone.** This tab picks its own
   scale from the doses and pins the choice with ``_aw_force_xlog``, which
   ``apply_export_style_prefs`` obeys over the shared ``x_log`` pref. Only the
   tab's own draw can re-decide, so only a redraw can carry this control.

The base is the same story from the other side: a base is meaningless to a
figure that is never redrawn, and the Response tab positions its zero-dose
column one step of that base below the lowest real dose.

Everything here asserts a value — the tick positions, the recorded base, the
redraw that ran — rather than a property several implementations share
(CLAUDE.md §7.8). "The axis is log" was true before the fix and after it.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from well_viewer.core.cell_gating_state import CellGatingState
from well_viewer.core.export_prefs_state import ExportPrefsState
from well_viewer.core.plot_state import PlotState
from well_viewer.features.response.collect import (
    ResponseCurve, ResponseData, ResponsePoint,
)
from well_viewer.features.response.renderer import draw_response
from well_viewer.features.scatter.state import ScatterState

_DOSES = (0.1, 0.3, 1.0, 3.0, 10.0)


def _data(doses=_DOSES, with_zero: bool = False) -> ResponseData:
    xs = ((0.0, *doses) if with_zero else doses)
    points = [ResponsePoint(x=x, y=1.0 + i, err=float("nan"), n=3,
                            member=f"m{i}")
              for i, x in enumerate(xs)]
    return ResponseData(variable_name="Dose",
                        curves=[ResponseCurve(timepoint=0.0, series_id="",
                                              points=points)])


def _figure():
    from matplotlib.figure import Figure

    fig = Figure(figsize=(4, 3))
    return fig, fig.add_subplot(1, 1, 1)


# ── the base, where it is decided ────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("10", 10.0), ("2", 2.0), ("e", math.e), ("E", math.e), (" 2 ", 2.0),
    ("1.5", 1.5),
    # Not bases, and each would take an axis down rather than the value with it:
    # log base 1 is a division by zero and a negative base has no real log.
    ("1", 10.0), ("0", 10.0), ("-3", 10.0), ("", 10.0), ("nonsense", 10.0),
    (None, 10.0),
])
def test_the_base_field_reads_what_the_user_typed(text, expected):
    from well_viewer.core.plot_style import log_base_value

    assert log_base_value(text) == pytest.approx(expected)


def test_the_zero_dose_column_sits_one_step_of_the_chosen_base_below():
    """Not "a decade below" — one step of the axis the figure is drawn on.

    An implementation that ignored the base entirely would answer 0.01 to both
    of these, which is why both are asserted rather than only the base-2 one.
    """
    from well_viewer.features.response.response_compute import zero_position

    assert zero_position(_DOSES) == pytest.approx(0.01)
    assert zero_position(_DOSES, base=2.0) == pytest.approx(0.05)


def test_resolved_log_base_reads_this_figure_s_preference():
    from well_viewer.core.plot_style import figure_prefs
    from well_viewer.features.response.controller import resolved_log_base

    fig, _ax = _figure()
    ctx = SimpleNamespace(
        state=SimpleNamespace(export_prefs=ExportPrefsState()),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )
    assert resolved_log_base(ctx, fig) == pytest.approx(10.0)
    figure_prefs(ctx, fig)["x_log_base"] = "2"
    assert resolved_log_base(ctx, fig) == pytest.approx(2.0)


# ── the base, on the drawn axis ──────────────────────────────────────────────

def test_the_figure_is_drawn_in_the_base_it_was_given():
    """The base the renderer set, read back off the axis it set it on.

    Asserted as the recorded value rather than as "the axis is log", because
    every base makes the axis log and the whole question here is *which*.
    """
    fig, ax = _figure()
    draw_response(SimpleNamespace(), ax, _data(), log_x=True, log_base=2.0)
    assert ax.get_xscale() == "log"
    assert getattr(ax, "_aw_log_base_x") == pytest.approx(2.0)


def test_a_plain_figure_takes_its_base_from_the_properties_pref():
    """A tab that does not pin its own scale gets the base through the restyle.

    Ticked at the powers of the base, which is the visible consequence and the
    one an implementation that stored the pref and never used it fails.
    """
    from well_viewer.core.plot_style import (
        DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs,
    )

    fig, ax = _figure()
    ax.plot([1, 2, 4, 8, 16], [1, 2, 3, 4, 5])
    ax.set_xlim(1, 16)
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    prefs["x_log_base"] = "2"
    apply_export_style_prefs(fig, prefs)
    assert getattr(ax, "_aw_log_base_x") == pytest.approx(2.0)
    ticks = [t for t in ax.get_xticks() if 1 <= t <= 16]
    # Every tick a power of two. Not the exact list: matplotlib thins a
    # base-2 decade set to fit the axis, and pinning its choice would make
    # this a test of matplotlib's spacing rather than of the base. Base 10
    # over this range ticks at 10, which is not a power of two, so the
    # unfixed behaviour still fails here.
    assert len(ticks) >= 3, f"too few ticks to tell one base from another: {ticks}"
    assert all(float(math.log2(t)).is_integer() for t in ticks), ticks


def test_a_restyle_leaves_the_dose_ticks_where_the_tab_put_them():
    """The Response tab ticks one dose per concentration given. A restyle runs
    after every redraw and on every Properties edit, and ``set_xscale`` rebuilds
    the scale — taking the locator with it — even when the scale is unchanged.
    So the doses came back as matplotlib's decades: 0.001, 0.01, … 1000.
    """
    from well_viewer.core.plot_style import (
        DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs,
    )

    fig, ax = _figure()
    draw_response(SimpleNamespace(), ax, _data(), log_x=True)
    assert list(ax.get_xticks()) == pytest.approx(list(_DOSES))
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["x_log"] = True
    apply_export_style_prefs(fig, prefs)
    assert list(ax.get_xticks()) == pytest.approx(list(_DOSES))


# ── the control, pressed ─────────────────────────────────────────────────────

pytestmark_qt = pytest.mark.qt


def _stub_app(fig):
    """The smallest app the Properties sidebar builds against, holding the
    Response figure and a real feature registry — the registry is how the
    sidebar reaches this tab, so stubbing it out would step over the seam
    under test (§7.8: my stub cannot disagree with me)."""
    from well_viewer.core.app_runtime import AppRuntime
    from well_viewer.features.registry import build_feature_registry

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
    app._response_fig = fig
    app._line_redraws: list[int] = []
    app._redraw = lambda: app._line_redraws.append(1)
    app._rep_sets_active = lambda: []
    app._parse_rc = lambda lbl: (lbl, 0, 0)
    app._well_display_label = lambda w: w
    app._replicate_display_label = lambda rs: getattr(rs, "name", "")
    app._use_fov_spread_active = lambda: False
    app._toggle_sem = lambda: None
    app._toggle_fov_replicates = lambda: None
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._statuses = []
    app._set_status = app._statuses.append
    app._ctx = SimpleNamespace(
        app=app,
        state=app._app_state,
        runtime=AppRuntime(app),
        features=build_feature_registry(),
        widgets=SimpleNamespace(
            get_or_none=lambda n: getattr(app, n, None),
            register=lambda n, w: (setattr(app, n, w), w)[1],
        ),
        notify=SimpleNamespace(set_status=lambda _m: None),
    )
    return app


def _sidebar(qtbot, monkeypatch, *, drawn_log: bool = False):
    """A real ``ExportStyleSidebar`` over a real, already-drawn Response figure,
    with this tab's redraw recorded at the module the registry imports it from."""
    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig, ax = _figure()
    draw_response(SimpleNamespace(), ax, _data(), log_x=drawn_log)
    app = _stub_app(fig)
    calls: list[str] = []
    monkeypatch.setattr(
        "well_viewer.features.response.controller.redraw_response",
        lambda _ctx: calls.append("response"))
    sb = ExportStyleSidebar(app, None, fig, None, default_name="response_figure")
    qtbot.addWidget(sb)
    return app, sb, calls


def _log_toggle(sidebar, label: str):
    from widgets.toggle_switch import ToggleSwitch

    for w in sidebar.findChildren(ToggleSwitch):
        if w.text() == label:
            return w
    return None


def _base_field(sidebar, which: str):
    """The X or Y log-base combo, found by its tooltip — the sentence the user
    reads — rather than by position among the panel's twenty-odd combos."""
    from PySide6.QtWidgets import QComboBox

    for w in sidebar.findChildren(QComboBox):
        if w.toolTip().startswith(f"Base of the {which} log axis"):
            return w
    return None


@pytest.mark.qt
def test_the_x_log_toggle_redraws_the_response_tab(qtbot, monkeypatch):
    """The whole report, in one assertion pair.

    ``calls == ["response"]`` is the fix; ``_line_redraws == []`` is the defect,
    because falling through to ``app._redraw()`` is precisely what the old
    dispatch did — and it returned ``True``, so nothing anywhere said so.
    """
    app, sb, calls = _sidebar(qtbot, monkeypatch)
    toggle = _log_toggle(sb, "X log")
    assert toggle is not None, "no X log toggle on the Response Properties panel"
    toggle.setChecked(True)
    assert calls == ["response"], (
        f"the X log toggle did not redraw the Response tab: {calls}")
    assert app._line_redraws == [], (
        "the toggle repainted the Line tab instead of the figure it belongs to")


@pytest.mark.qt
def test_the_log_base_field_redraws_the_response_tab(qtbot, monkeypatch):
    app, sb, calls = _sidebar(qtbot, monkeypatch, drawn_log=True)
    field = _base_field(sb, "X")
    assert field is not None, "no X log base field on the Properties panel"
    assert field.currentText() == "10"
    field.setCurrentText("2")
    assert calls == ["response"], (
        f"changing the log base did not redraw the Response tab: {calls}")
    assert app._app_state.export_prefs.axis_prefs["response"]["x_log_base"] == "2", (
        "the base was not written to this figure's own preferences")


@pytest.mark.qt
def test_the_toggle_opens_showing_the_axis_that_is_on_screen(qtbot, monkeypatch):
    """A figure with no stored answer seeds the toggle from what is drawn.

    The Response tab picks a log axis from the doses when the preference is
    unset, so a toggle seeded from that unset preference read *off* over an
    obviously logarithmic axis — and because ``_persist`` writes every field on
    any edit, changing the title size then wrote that ``False`` down and the
    next redraw obeyed it.
    """
    _app, sb, _calls = _sidebar(qtbot, monkeypatch, drawn_log=True)
    assert _log_toggle(sb, "X log").isChecked(), (
        "the panel says the axis is linear over a logarithmic axis")


@pytest.mark.qt
def test_a_linear_response_figure_still_opens_showing_linear(qtbot, monkeypatch):
    """The other direction, so the seed is reading the axis rather than
    answering "on" to everything."""
    _app, sb, _calls = _sidebar(qtbot, monkeypatch, drawn_log=False)
    assert not _log_toggle(sb, "X log").isChecked()
