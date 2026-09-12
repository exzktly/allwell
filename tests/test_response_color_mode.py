"""Properties → Colours on Response Curves: series colours, or greys.

The Line tab's grey mode identifies a trace three ways — a shade, a dash and a
marker. Copying that rule here would have been the mistake this file guards
against, because a response figure has already spent its dash: the dash says
which **timepoint** a curve was measured at, and the colour says which series
it belongs to. Taking the dash for the series would overwrite one encoding with
another, silently, on a figure whose legend goes on naming both. So grey mode
here is the Line tab's minus the dash — shade and marker — and the tests below
pin the dash staying with the timepoint as hard as they pin the greys.

Three things the assertions are careful about:

* They sit at the **boundary** (§7.4 #1) — the colour, dash, marker and face
  that reached the drawn artist, not what a resolver computed.
* The fixtures are built so the right and wrong answers differ (§7.8 #2): two
  series at two timepoints, so a rule that put the series in the dash and a
  rule that put the timepoint there produce different figures, and a mode that
  changed nothing produces a third.
* They check what survives ``apply_export_style_prefs``, which runs on every
  redraw and pins marker shape, marker face and dash from the Properties prefs.

The control itself is pressed in ``test_properties_colour_control.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_hex  # noqa: E402

from ui import theme  # noqa: E402
from well_viewer.core.plot_state import PlotState  # noqa: E402
from well_viewer.domain.line_colors import (  # noqa: E402
    LINE_MARKERS, LINE_STYLES, grey_trace_style,
)
from well_viewer.domain.response_colors import (  # noqa: E402
    RESPONSE_COLOR_MODES, grey_series_style, response_color_mode,
    response_grey_cues, series_styles,
)
from well_viewer.features.response import collect as rc  # noqa: E402
from well_viewer.features.response.renderer import draw_response  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


def _ctx(mode: str = "series"):
    plot = PlotState()
    plot.response_color_mode = mode
    return SimpleNamespace(state=SimpleNamespace(plot=plot))


def _points(offset: float = 0.0):
    return tuple(rc.ResponsePoint(f"m{i}", x, x * 2 + offset, 0.5, 3, f"{x:g}")
                 for i, x in enumerate((1.0, 2.0, 3.0)))


def _two_by_two() -> rc.ResponseData:
    """Two series at two timepoints — four curves.

    Both encodings vary, so a rule that inks by timepoint and a rule that inks
    by series draw different figures. With one series or one timepoint the two
    are indistinguishable, and every assertion below would hold for the wrong
    implementation.
    """
    curves = [
        rc.ResponseCurve(t, _points(i), series_id=sid, series_label=lab)
        for i, (t, sid, lab) in enumerate((
            (24.0, "s1", "HeLa"),
            (24.0, "s2", "U2OS"),
            (48.0, "s1", "HeLa"),
            (48.0, "s2", "U2OS"),
        ))
    ]
    return rc.ResponseData(variable_name="Dose", unit="µM", curves=curves,
                           series_name="Cell line", placed=len(curves))


def _drawn(ax):
    """``[(label, Line2D)]`` for the curves, in draw order.

    ``ax.containers`` rather than ``ax.lines``: every curve is an
    ``errorbar``, whose container holds the data line, the caps and the bars,
    and reading ``ax.lines`` would interleave the three.
    """
    return [(c.get_label(), c.lines[0]) for c in ax.containers
            if getattr(c, "lines", None) and c.lines[0] is not None]


# ── the resolver ───────────────────────────────────────────────────────────

def test_the_mode_defaults_to_series_and_rejects_junk():
    assert response_color_mode(PlotState()) == "series"
    assert response_color_mode(SimpleNamespace()) == "series"
    assert response_color_mode(SimpleNamespace(response_color_mode="teal")) == "series"
    assert response_color_mode(SimpleNamespace(response_color_mode="grey")) == "grey"
    assert RESPONSE_COLOR_MODES == ("series", "grey")


def test_the_grey_series_style_keeps_the_line_shade_and_marker_but_drops_the_dash():
    """Not "it has no dash" but "it is the Line tab's style with the dash
    removed" — the shade and marker have to be the *same* cycle, or the app
    grows a second answer to which greys stay apart."""
    for index in range(len(LINE_MARKERS) + 2):
        line = grey_trace_style(index)
        here = grey_series_style(index)
        assert here.color == line.color
        assert here.marker == line.marker
        assert here.filled == line.filled
        assert line.linestyle in LINE_STYLES, "the Line style should carry a dash"
        assert here.linestyle is None, (
            f"series {index} took a dash, which on this figure means the "
            "timepoint")


def test_grey_mode_styles_a_lone_series_where_the_series_mode_leaves_it_alone():
    """The whole request is "take the colour out of this figure". A single
    curve left in C0 blue is that request refused on the one figure where it is
    most visible."""
    palette = ["#111111", "#222222"]
    assert series_styles("series", ["only"], palette)["only"].color == "#111111"
    assert (series_styles("grey", ["only"], palette)["only"].color
            == grey_series_style(0).color)


# ── what reaches the figure ────────────────────────────────────────────────

def test_series_mode_draws_the_palette_and_the_timepoint_dashes():
    """The behaviour before the mode existed, pinned so grey mode cannot become
    the only tested one."""
    fig, ax = plt.subplots()
    draw_response(_ctx("series"), ax, _two_by_two())
    drawn = _drawn(ax)
    assert len(drawn) == 4, [d[0] for d in drawn]

    cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    by_label = {label: line for label, line in drawn}
    # One colour per series across both of its timepoints, and the two series
    # take the first two entries of the cycle in the order collect placed them.
    hela = [to_hex(ln.get_color()) for lab, ln in drawn if "HeLa" in lab]
    u2os = [to_hex(ln.get_color()) for lab, ln in drawn if "U2OS" in lab]
    assert hela == [to_hex(cycle[0])] * 2, hela
    assert u2os == [to_hex(cycle[1])] * 2, u2os
    assert hela[0] != u2os[0]
    # …and one dash per timepoint across both of its series.
    t24 = {ln.get_linestyle() for lab, ln in drawn if "t=24" in lab}
    t48 = {ln.get_linestyle() for lab, ln in drawn if "t=48" in lab}
    assert len(t24) == 1 and len(t48) == 1, (t24, t48)
    assert t24 != t48
    assert by_label


def test_grey_mode_inks_the_series_in_greys_and_leaves_the_dash_to_the_timepoint():
    """The reported request, and the trap inside it.

    A grey mode that took the dash would pass "no curve is coloured" and still
    have destroyed the timepoint encoding, so both halves are asserted on one
    figure where the two vary independently.
    """
    fig, ax = plt.subplots()
    draw_response(_ctx("grey"), ax, _two_by_two())
    drawn = _drawn(ax)
    assert len(drawn) == 4, [d[0] for d in drawn]

    ramp = {to_hex(c) for c in theme.Colors.plot_grey_ramp}
    for label, line in drawn:
        assert to_hex(line.get_color()) in ramp, (
            f"{label} was drawn in {to_hex(line.get_color())}, which is not a "
            "shade of the grey ramp")

    # The series still differ from each other…
    hela = {to_hex(ln.get_color()) for lab, ln in drawn if "HeLa" in lab}
    u2os = {to_hex(ln.get_color()) for lab, ln in drawn if "U2OS" in lab}
    assert len(hela) == 1 and len(u2os) == 1, (hela, u2os)
    assert hela != u2os, "both series came out the same shade"
    hela_mk = {ln.get_marker() for lab, ln in drawn if "HeLa" in lab}
    u2os_mk = {ln.get_marker() for lab, ln in drawn if "U2OS" in lab}
    assert len(hela_mk) == 1 and len(u2os_mk) == 1, (hela_mk, u2os_mk)
    assert hela_mk != u2os_mk, "both series took the same marker"
    assert hela_mk == {grey_series_style(0).marker}
    assert u2os_mk == {grey_series_style(1).marker}

    # …and the dash is still the timepoint's, not the series'.
    t24 = {ln.get_linestyle() for lab, ln in drawn if "t=24" in lab}
    t48 = {ln.get_linestyle() for lab, ln in drawn if "t=48" in lab}
    assert len(t24) == 1 and len(t48) == 1, (t24, t48)
    assert t24 != t48, "the two timepoints came out with the same dash"


def test_grey_mode_greys_a_figure_with_no_series_split():
    """One curve per timepoint and no Series variable. The ``"series"`` mode
    hands those to matplotlib's cycle, which is right; grey mode cannot, or the
    most common response figure of all is the one the mode does not reach."""
    data = rc.ResponseData(
        variable_name="Dose", unit="µM", placed=2,
        curves=[rc.ResponseCurve(24.0, _points()),
                rc.ResponseCurve(48.0, _points(1.0))])
    fig, ax = plt.subplots()
    draw_response(_ctx("grey"), ax, data)
    ramp = {to_hex(c) for c in theme.Colors.plot_grey_ramp}
    colours = [to_hex(ln.get_color()) for _lab, ln in _drawn(ax)]
    assert colours and all(c in ramp for c in colours), colours
    # Both curves are the one series, so they share its shade; the dash is what
    # tells the timepoints apart, exactly as in the coloured mode.
    assert len(set(colours)) == 1, colours
    dashes = {ln.get_linestyle() for _lab, ln in _drawn(ax)}
    assert len(dashes) == 2, dashes


def test_the_export_style_pass_does_not_stamp_over_the_encodings():
    """``apply_export_style_prefs`` runs on every redraw and pins the marker
    shape, the marker face and the dash from the Properties prefs. Three
    per-artist opt-outs stop it, one per cue — and the dash one is needed in
    **both** modes, because the timepoint owns the dash whatever the colour
    mode is. Without it one pref pattern replaced four timepoints' worth while
    the legend went on naming all four.
    """
    from well_viewer.core.plot_style import apply_export_style_prefs

    prefs = {"marker_style": "s", "marker_open": True, "line_style": "-"}
    for mode in RESPONSE_COLOR_MODES:
        fig, ax = plt.subplots()
        draw_response(_ctx(mode), ax, _two_by_two())
        before = [(ln.get_linestyle(), ln.get_marker())
                  for _lab, ln in _drawn(ax)]
        apply_export_style_prefs(fig, prefs)
        after = [(ln.get_linestyle(), ln.get_marker())
                 for _lab, ln in _drawn(ax)]
        assert [b[0] for b in before] == [a[0] for a in after], (
            f"{mode}: the export pass overwrote the timepoint dashes "
            f"{before} -> {after}")
        if mode == "grey":
            assert [b[1] for b in before] == [a[1] for a in after], (
                f"grey: the export pass overwrote the series markers "
                f"{before} -> {after}")


# ── and it survives a save and a reload ──────────────────────────────────────
#
# The mode rides in the top-level plot block rather than in this tab's own, and
# nothing was reading that back: the snapshot and the restore were both written
# and neither had a test, so a key that no longer round-tripped would have
# looked exactly like one that did.

@pytest.mark.qt
def test_the_mode_survives_a_save_and_reload(loaded_app):
    """Through the **real** ``snapshot`` / ``restore``.

    That block's restore takes a whole app, so this is a slow-lane test —
    faking the two halves would assert that a dict literal round-trips through
    itself, which passes against a key nothing writes.
    """
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.response_color_mode = "grey"
    snap = vs.snapshot(app._ctx)
    assert snap.get("response_color_mode") == "grey"

    app._app_state.plot.response_color_mode = "series"
    vs.restore(app._ctx, snap)
    assert app._app_state.plot.response_color_mode == "grey"


@pytest.mark.qt
def test_a_junk_value_on_disk_does_not_reach_the_state(loaded_app):
    """An edited ``persistence.json`` is dropped rather than stored, so the
    resolver's degrade-to-default is not the only thing between a typo and a
    figure drawn in no mode at all."""
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.response_color_mode = "grey"
    vs.restore(app._ctx, {"response_color_mode": "chartreuse"})
    assert app._app_state.plot.response_color_mode == "grey", (
        "a junk value on disk overwrote a valid mode")


def test_the_mode_survives_a_new_dataset():
    """A presentation preference, not a statement about this plate's wells."""
    plot = PlotState()
    plot.response_color_mode = "grey"
    plot.reset_for_new_dataset()
    assert plot.response_color_mode == "grey"


# ── the dash is a cue when nobody else is using it ──────────────────────────
#
# Reserving it unconditionally was the defect: ``_timepoint_styles`` draws no
# dashes at all below two timepoints, which is the commoner figure, and grey
# mode gave the dash up there anyway — so three series came out solid, told
# apart by one step of grey and a small marker. The reservation is a fact about
# a figure's timepoints, not about the tab.

def _one_timepoint_three_series() -> rc.ResponseData:
    """Three series at one timepoint — the figure that was drawn all-solid.

    Three rather than two, so a rule that varies the dash and a rule that
    alternates between two of them draw different figures.
    """
    curves = [
        rc.ResponseCurve(24.0, _points(i), series_id=sid, series_label=lab)
        for i, (sid, lab) in enumerate(
            (("s1", "HeLa"), ("s2", "U2OS"), ("s3", "RPE1")))
    ]
    return rc.ResponseData(variable_name="Dose", unit="µM", curves=curves,
                           series_name="Cell line", placed=len(curves))


def test_the_cue_set_is_a_question_about_the_figure():
    assert response_grey_cues(dash_is_spent=True) == ("shade", "marker")
    assert response_grey_cues(dash_is_spent=False) == ("shade", "marker", "dash")


def test_a_caller_that_does_not_say_gets_the_conservative_answer():
    """Handing a series the dash the timepoint is also using is the failure
    worth defaulting away from, so an unsaid caller keeps two cues."""
    assert grey_series_style(0).linestyle is None
    assert grey_series_style(0, dash_is_spent=True).linestyle is None
    assert grey_series_style(0, dash_is_spent=False).linestyle is not None


def test_grey_mode_varies_the_dash_when_the_timepoint_is_not_using_it():
    """The reported symptom, as the values on the axes.

    Asserted as three *different* dashes rather than as "not all solid": a rule
    that alternated two patterns would satisfy the looser claim, and so would
    the broken behaviour on a two-series figure.
    """
    fig, ax = plt.subplots()
    draw_response(_ctx("grey"), ax, _one_timepoint_three_series(), log_x=False)
    dashes = [line.get_linestyle() for _label, line in _drawn(ax)]
    assert len(dashes) == 3, dashes
    assert len(set(dashes)) == 3, f"the series were not told apart by dash: {dashes}"


def test_no_series_style_carries_a_dash_the_timepoint_is_using():
    """Asserted on the styles, not on the drawn lines.

    ``for_curve`` applies the timepoint's dash *after* the series', so a series
    that wrongly claimed one is overwritten and the picture comes out right
    anyway — which means no assertion on the drawn lines can see it. That is
    why ``_curve_styles`` derives the two halves together instead of taking the
    answer as an argument, and why this reads them off that one function:
    exactly one of the two owns the dash, on either shape of figure.
    """
    from well_viewer.features.response.renderer import _curve_styles

    for data, dashed_timepoints in ((_two_by_two(), True),
                                    (_one_timepoint_three_series(), False)):
        marks = _curve_styles(_ctx("grey"), data)
        assert (marks.dashes is not None) is dashed_timepoints
        carried = [st.linestyle for st in (marks.styles or {}).values()]
        assert carried, marks
        # Exactly one of the two owns the dash, on either figure.
        assert all((ls is None) is dashed_timepoints for ls in carried), (
            f"the series and the timepoint both claim the dash: {marks}")


def test_the_timepoint_still_owns_the_dash_when_it_is_using_it():
    """The half that must not regress: with two timepoints drawn, the dash goes
    back to meaning *which moment*, and two series at one moment share it.

    True by the overwrite in ``for_curve`` as well as by the cue set, so this
    holds even against a series style that claimed a dash — see the test above
    for the half that does not.
    """
    fig, ax = plt.subplots()
    draw_response(_ctx("grey"), ax, _two_by_two(), log_x=False)
    by_timepoint: dict[float, set] = {}
    curves = [c for c in _two_by_two().curves if c.points]
    for curve, (_label, line) in zip(curves, _drawn(ax)):
        by_timepoint.setdefault(curve.timepoint, set()).add(line.get_linestyle())
    assert all(len(v) == 1 for v in by_timepoint.values()), by_timepoint
    assert len({next(iter(v)) for v in by_timepoint.values()}) == 2, by_timepoint


def test_the_series_dash_is_tagged_as_data_too():
    """Whoever owns the dash, the export-style pass must be told.

    ``apply_export_style_prefs`` stamps one pref pattern over every artist
    unless it says the dash is data — so a dash that now belongs to the series
    needs the flag exactly as the timepoint's did. Without this the new cue
    would be applied and then removed again on the restyle that follows every
    redraw, which is how the timepoint's own dashes were lost before.
    """
    fig, ax = plt.subplots()
    draw_response(_ctx("grey"), ax, _one_timepoint_three_series(), log_x=False)
    lines = [line for _label, line in _drawn(ax)]
    assert lines
    assert all(getattr(line, "_aw_linestyle_is_data", False) for line in lines)


def test_the_series_mode_is_left_solid_either_way():
    """The dash is grey mode's to spend, not the palette mode's: a coloured
    figure identifies a series by its colour, and a dash there would be a
    distinction standing for nothing."""
    fig, ax = plt.subplots()
    draw_response(_ctx("series"), ax, _one_timepoint_three_series(), log_x=False)
    assert {line.get_linestyle() for _label, line in _drawn(ax)} == {"-"}
