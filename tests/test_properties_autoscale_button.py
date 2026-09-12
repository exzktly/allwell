"""The Properties → Limits & Scale → *Auto scale* button, pressed like a user.

§7.6 of CLAUDE.md: a control that *exists* is not a control that *works*. So
every test here finds the real ``QPushButton`` inside the built sidebar, clicks
it, and asserts on what came out the other side — never on the handler called
directly, and never on a property so weak that the button being wired to
nothing would satisfy it.

The button undoes a typed axis limit, and undoing one takes three steps that
each look sufficient on their own:

* blanking the four limit fields, which is the only half the user can see;
* clearing the limit out of *every* per-axis bucket, because
  ``apply_export_style_prefs`` reads the bucket first and falls back to the
  flat key only for the targeted panel — so on a multi-panel figure the fields
  are not where the drawn limit came from;
* redrawing, because the style pass only ever *sets* limits and has nothing in
  it that can re-enable autoscale.

Two of the tests below are built specifically so that an implementation missing
one of those steps still passes the others: the limit that matters sits on the
panel the fields never reach, and the end-to-end test measures the axes rather
than the preferences.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.core.cell_gating_state import CellGatingState
from review.core.export_prefs_state import ExportPrefsState
from review.core.plot_state import PlotState
from review.features.scatter.state import ScatterState

pytestmark = pytest.mark.qt

_SECTION = "Limits & Scale"
_BUTTON = "Auto scale"

#: The two panels' data, so the test can say what "scaled to its own data"
#: means in numbers rather than asserting the axes merely changed.
_PANEL_YS = ([10.0, 20.0, 30.0], [100.0, 200.0, 300.0])

#: A limit no panel's data would ever produce, so an axis still wearing it
#: cannot be mistaken for one that autoscaled.
_TYPED_LIMITS = ("0", "1")


def _mk_ctx(app):
    from review.core.app_runtime import AppRuntime
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
    # rather than discarded: the redraw is the button's only visible effect, so
    # the message is the other half of its contract.
    app._statuses = []
    app._set_status = app._statuses.append
    return app


def _two_panel_figure():
    from matplotlib.figure import Figure

    fig = Figure(figsize=(4, 3))
    for i, ys in enumerate(_PANEL_YS, start=1):
        ax = fig.add_subplot(len(_PANEL_YS), 1, i)
        ax.plot([1.0, 2.0, 3.0], ys)
    return fig


def _build_sidebar(qtbot, monkeypatch, *, redraw_ok: bool = True):
    """The built sidebar, the app, and the list of redraws it asked for."""
    from review.features.export.sidebar_view import ExportStyleSidebar

    fig = _two_panel_figure()
    app = _stub_app()
    app._line_fig = fig
    redraws: list[int] = []

    def _record(self):
        redraws.append(1)
        return redraw_ok

    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        _record, raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="line")
    qtbot.addWidget(sb)
    return app, sb, fig, redraws


def _auto_button(sidebar):
    """The Auto scale button inside the Limits & Scale section, or ``None``.

    Found through the built widget tree by the words on the section and on the
    button, so a control moved out from under this lookup is a move the test
    notices."""
    from PySide6.QtWidgets import QPushButton

    from widgets.collapsible_section import CollapsibleSection

    for sec in sidebar.findChildren(CollapsibleSection):
        if sec.titleText() != _SECTION:
            continue
        for btn in sec.findChildren(QPushButton):
            if btn.text() == _BUTTON:
                return btn
    return None


def _type_limits_everywhere(sidebar):
    """Put the same y limit on both panels, the two ways the panel stores one.

    The targeted panel gets it through the *fields*, which is how a user types
    one; the other panel gets it only in its bucket, which is where the "Axis #"
    picker leaves it and where the fields cannot reach. That asymmetry is the
    whole point of the fixture — an implementation that clears only what the
    user can see passes on panel 1 and fails on panel 2.
    """
    lo, hi = _TYPED_LIMITS
    sidebar._setters["y_lim_min"](lo)
    sidebar._setters["y_lim_max"](hi)
    for idx in ("1", "2"):
        bucket = sidebar._axis_buckets.setdefault(idx, {})
        bucket["y_lim_min"] = lo
        bucket["y_lim_max"] = hi
    sidebar._prefs["axis_buckets"] = sidebar._axis_buckets


def _limit_fields(sidebar):
    return {k: sidebar._getters[k]()
            for k in ("x_lim_min", "x_lim_max", "y_lim_min", "y_lim_max")}


def test_the_limits_section_carries_an_auto_scale_button(qtbot, monkeypatch):
    _app, sb, _fig, _r = _build_sidebar(qtbot, monkeypatch)
    assert _auto_button(sb) is not None, (
        "no Auto scale button in the Limits & Scale section")


def test_pressing_it_blanks_the_typed_limit_fields(qtbot, monkeypatch):
    _app, sb, _fig, _r = _build_sidebar(qtbot, monkeypatch)
    _type_limits_everywhere(sb)
    assert _limit_fields(sb)["y_lim_min"] == _TYPED_LIMITS[0]
    _auto_button(sb).click()
    assert _limit_fields(sb) == {
        "x_lim_min": "", "x_lim_max": "", "y_lim_min": "", "y_lim_max": ""}
    # And the same, written down: the fields are the widget, the prefs are what
    # the next redraw reads, and a handler that cleared only the widget would
    # be undone by the very next persist.
    for key in ("x_lim_min", "x_lim_max", "y_lim_min", "y_lim_max"):
        assert sb._prefs.get(key, "") == "", f"{key} survived the press"


def test_pressing_it_clears_the_limit_on_a_panel_the_fields_never_reach(
        qtbot, monkeypatch):
    """Panel 2's limit lives only in its bucket. Clearing the four fields —
    which is what "reset the limits" looks like from the outside — leaves it
    exactly where it was, and the style pass reads the bucket first."""
    _app, sb, _fig, _r = _build_sidebar(qtbot, monkeypatch)
    _type_limits_everywhere(sb)
    _auto_button(sb).click()
    for idx in ("1", "2"):
        bucket = sb._axis_buckets.get(idx) or {}
        for key in ("y_lim_min", "y_lim_max"):
            assert bucket.get(key, "") in ("", None), (
                f"axis {idx} still carries {key}={bucket.get(key)!r}")


def test_the_axes_scale_themselves_again_after_the_press(qtbot, monkeypatch):
    """The measurement, not the bookkeeping.

    The figure is styled with the typed limits first, so both panels genuinely
    wear 0…1 before the press; then the press runs with a redraw that does what
    a renderer does — clear the axes and draw the data again — followed by the
    style pass. An implementation that clears the preferences but never redraws
    leaves the axes at the limits they were last handed, and an implementation
    that redraws but leaves panel 2's bucket has the style pass put 0…1 straight
    back on it."""
    from review.core.plot_style import apply_export_style_to_current
    from review.features.export.sidebar_view import ExportStyleSidebar

    app, sb, fig, _r = _build_sidebar(qtbot, monkeypatch)
    _type_limits_everywhere(sb)
    apply_export_style_to_current(app._ctx, fig)
    lo, hi = (float(v) for v in _TYPED_LIMITS)
    for ax in fig.axes:
        assert ax.get_ylim() == (lo, hi), (
            "the fixture never put the typed limit on the axes, so this test "
            "cannot tell an autoscale from a no-op")

    def _redraw_like_a_renderer(_self):
        for ax, ys in zip(fig.axes, _PANEL_YS):
            ax.clear()
            ax.plot([1.0, 2.0, 3.0], ys)
        apply_export_style_to_current(app._ctx, fig)
        return True

    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        _redraw_like_a_renderer, raising=False)
    _auto_button(sb).click()

    for ax, ys in zip(fig.axes, _PANEL_YS):
        bot, top = ax.get_ylim()
        assert bot < min(ys) and top > max(ys), (
            f"panel drawn over {min(ys)}…{max(ys)} still shows {bot}…{top}")


def test_pressing_it_asks_for_a_redraw(qtbot, monkeypatch):
    """Clearing the preferences changes nothing on screen on its own — the
    style pass sets limits and can never take them off — so a handler that
    persists and stops is a button that does nothing until something unrelated
    repaints the tab."""
    _app, sb, _fig, redraws = _build_sidebar(qtbot, monkeypatch)
    _type_limits_everywhere(sb)
    _auto_button(sb).click()
    assert redraws, "the limits were cleared but nothing asked for a redraw"


def test_a_redraw_that_did_not_run_is_reported(qtbot, monkeypatch):
    app, sb, _fig, _r = _build_sidebar(qtbot, monkeypatch, redraw_ok=False)
    _type_limits_everywhere(sb)
    _auto_button(sb).click()
    assert app._statuses, (
        "the redraw failed and the user was told nothing, so the button reads "
        "as one that does nothing at all")
    assert "redraw" in app._statuses[-1].lower()


def test_it_leaves_the_log_scaling_alone(qtbot, monkeypatch):
    """Auto scale is about bounds, not about the scale they are drawn on. An
    axis in log stays in log — it just picks its own bounds again."""
    _app, sb, _fig, _r = _build_sidebar(qtbot, monkeypatch)
    sb._setters["y_log"](True)
    sb._setters["y_log_base"]("2")
    _type_limits_everywhere(sb)
    _auto_button(sb).click()
    assert sb._getters["y_log"]() is True
    assert sb._getters["y_log_base"]() == "2"
    assert sb._prefs.get("y_log") is True
