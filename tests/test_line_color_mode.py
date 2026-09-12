"""Properties → Colours on the Line tab: plate colours, or greys told apart
three ways.

The Bar tab's grey mode collapses every column onto one ink, and copying that
rule here would have been the mistake this file mostly guards against. A bar is
named by the tick label under it; a trace is not. Ten identical grey curves is
not a cleaner figure than ten coloured ones, it is an unreadable one — so grey
mode here identifies a trace by a **shade**, a **dash pattern** and a
**marker** at once, and the tests below pin all three together with the
arithmetic that says how many of each there can be and why no two of them
advance in step.

Three things the assertions are careful about:

* They sit at the **boundary** (§7.4 #1) — the colour, dash, marker and face
  that reached the drawn ``Line2D``, not what a resolver computed. A helper can
  be right while nothing reaches the screen.
* They check what survives ``apply_export_style_prefs``, which runs on **every**
  redraw and pins marker shape, marker face and dash from the Properties prefs.
  Three per-artist opt-outs stop it, one per cue; without the face one, half the
  marker cycle came back filled and six traces stopped being distinguishable
  while the legend went on naming twelve.
* Both surfaces that draw these panels — the tab and the Batch Export line
  figure — answer the question the same way (§7.5 #5).

The control itself is pressed in ``test_properties_colour_control.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import to_hex  # noqa: E402

from ui import theme  # noqa: E402
from well_viewer.core.plot_state import PlotState  # noqa: E402
from well_viewer.domain.line_colors import (  # noqa: E402
    LINE_COLOR_MODES,
    LINE_MARKERS,
    LINE_MARKER_SHAPES,
    LINE_STYLES,
    LineTraceStyle,
    grey_trace_style,
    line_color_mode,
    trace_style,
)
from well_viewer.features.lineplot.controller import (  # noqa: E402
    LineAxes,
    LinePoint,
    LineSeries,
    render_line_series,
)

_PLATE = "#6AB2FB"


@pytest.fixture(autouse=True)
def _close_figures():
    """These tests read artists off drawn figures, so they cannot close as they
    go. Closing after each keeps the file off the fast lane's figure radar."""
    yield
    plt.close("all")


# ── the resolver ────────────────────────────────────────────────────────────

def test_the_default_mode_is_the_colours_the_app_has_always_drawn():
    assert PlotState().line_color_mode == "plate"


def test_plate_mode_keeps_the_trace_s_own_colour_and_states_no_marker():
    style = trace_style(PlotState(), 3, _PLATE)
    assert style.color == _PLATE
    assert style.marker is None, (
        "plate mode must leave each panel its own marker — a trace-owned "
        "marker there would replace the circle / square / triangle that says "
        "which panel you are looking at")


def test_an_unrecognised_mode_reads_as_plate_rather_than_as_blank():
    plot = PlotState()
    plot.line_color_mode = "chartreuse"
    assert line_color_mode(plot) == "plate"
    assert trace_style(plot, 0, _PLATE).color == _PLATE


def test_the_mode_list_and_the_defaults_agree():
    assert PlotState().line_color_mode in LINE_COLOR_MODES
    assert LINE_COLOR_MODES == ("plate", "grey")


def test_grey_style_carries_exactly_the_cues_it_was_asked_for():
    """The extraction's whole contract: a plot asks for the channels it has
    free, and gets those and no others.

    A cue not asked for comes back ``None``, which every caller already reads as
    "leave whatever the panel draws" — so an omitted dash cannot overwrite one
    the figure is spending on something else.
    """
    from well_viewer.domain.line_colors import GREY_CUES, grey_style

    assert GREY_CUES == ("shade", "marker", "dash")
    for cues, wants_shade, wants_marker, wants_dash in (
        (("shade", "marker", "dash"), True, True, True),
        (("shade", "marker"), True, True, False),
        (("shade",), True, False, False),
        (("dash",), False, False, True),
        ((), False, False, False),
    ):
        style = grey_style(3, cues=cues)
        assert bool(style.color) is wants_shade, (cues, style)
        assert (style.marker is not None) is wants_marker, (cues, style)
        assert (style.linestyle is not None) is wants_dash, (cues, style)


def test_a_requested_cue_is_the_same_value_whichever_set_it_came_in():
    """The cues do not interact: asking for two must not shift the one you
    would have got asking for three, or the same series would be inked
    differently on a figure that gained a timepoint."""
    from well_viewer.domain.line_colors import grey_style

    for index in range(6):
        full = grey_style(index)
        pair = grey_style(index, cues=("shade", "marker"))
        assert (pair.color, pair.marker, pair.filled) == (
            full.color, full.marker, full.filled)


def test_the_line_tab_asks_for_all_three():
    """``grey_trace_style`` is the Line panels' answer, and they have every
    channel free — stated here because it is now a choice rather than the only
    thing the function can do."""
    from well_viewer.domain.line_colors import grey_style, grey_trace_style

    for index in range(6):
        assert grey_trace_style(index) == grey_style(index)
        assert grey_trace_style(index).linestyle is not None


def test_neighbouring_traces_differ_in_dash_as_well():
    """The third cue. Shape and shade both cost something — shape needs a
    marker drawn, shade needs contrast — while a dash pattern costs nothing and
    survives being printed in grey."""
    styles = [grey_trace_style(i) for i in range(4)]
    assert len({s.linestyle for s in styles}) == 4, (
        f"four consecutive traces share a dash: {[s.linestyle for s in styles]}")
    assert set(LINE_STYLES) == {s.linestyle for s in styles}


def test_no_two_cues_advance_in_lockstep():
    """Counts chosen coprime so a shade and a dash never track each other — two
    cues that always changed together would be one cue wearing two coats, and
    the figure would look more distinguishable than it is."""
    import math

    ramp = len(theme.Colors.plot_grey_ramp)
    assert math.gcd(ramp, len(LINE_STYLES)) == 1, (
        f"{ramp} shades and {len(LINE_STYLES)} dashes share a factor")
    assert math.gcd(ramp, len(LINE_MARKERS)) == 1, (
        f"{ramp} shades and {len(LINE_MARKERS)} markers share a factor")


def test_plate_mode_states_no_dash():
    """A dash in plate mode would be a distinction standing for nothing: the
    colour is already the identity, and every trace has always been solid."""
    assert trace_style(PlotState(), 3, _PLATE).linestyle is None


def test_neighbouring_traces_differ_in_shade_as_well_as_marker():
    """The user asked for a different shade per line, and the first draft did
    not give one: cycling the marker fastest and the shade only on a wrap bought
    a longer period nobody could see, while nine traces — a realistic selection
    — came out in nine identical greys. Looking at the rendered figure is what
    caught it; no assertion here did, so this is that assertion."""
    styles = [grey_trace_style(i) for i in range(4)]
    assert len({s.color for s in styles}) == 4, (
        f"four consecutive traces share a shade: {[s.color for s in styles]}")
    assert len({s.marker for s in styles}) == 4


def test_the_style_is_unique_across_the_legend_the_screen_names():
    """Twelve is the number that has to be distinct: it is
    ``SCREEN_LEGEND_ENTRIES``, the point past which the legend stops naming
    traces individually and says how many it left out."""
    from well_viewer.features.lineplot.controller import SCREEN_LEGEND_ENTRIES

    n = len(LINE_MARKERS)
    seen = {(s.color, s.marker, s.filled, s.linestyle)
            for s in (grey_trace_style(i) for i in range(n))}
    assert len(seen) == n, f"only {len(seen)} distinct styles in {n} traces"
    assert n >= SCREEN_LEGEND_ENTRIES, (
        f"the style cycle ({n}) is shorter than the legend names "
        f"({SCREEN_LEGEND_ENTRIES}), so two named traces would look alike")
    assert len(LINE_MARKER_SHAPES) * 2 == len(LINE_MARKERS)


@pytest.mark.parametrize("mode", [*LINE_COLOR_MODES, "chartreuse", "", None])
def test_the_two_entry_points_agree(mode):
    """``trace_style`` takes a state object and ``trace_style_for_mode`` a
    string, because the renderer has no state and every other resolver in
    ``domain/`` takes one. Two doors into one question is exactly how a junk
    value ends up degrading to plate on one surface and to nothing on the
    other, so the second delegates to the first and this says so."""
    from well_viewer.domain.line_colors import trace_style_for_mode

    plot = PlotState()
    plot.line_color_mode = mode
    for index in (0, 5, 13):
        assert trace_style(plot, index, _PLATE) == trace_style_for_mode(
            line_color_mode(plot), index, _PLATE)


def test_an_open_marker_keeps_a_visible_edge():
    """matplotlib spells hollow as a face of "none"; an open marker whose edge
    also went to "none" is an invisible one."""
    open_style = LineTraceStyle(color="#686868", marker="o", filled=False)
    assert open_style.face == "none"
    assert open_style.color == "#686868"
    assert LineTraceStyle(color="#686868", marker="o").face == "#686868"


# ── the palette ─────────────────────────────────────────────────────────────

def test_the_ramp_is_a_theme_token_not_a_literal():
    from pathlib import Path

    import re

    src = (Path(__file__).resolve().parent.parent
           / "well_viewer" / "domain" / "line_colors.py").read_text()
    literals = re.findall(r"#[0-9A-Fa-f]{6}\b", src)
    assert not literals, (
        f"line_colors.py carries colour literal(s) {literals} — the ramp "
        "belongs to ui/theme.py, which is where a palette edit is looked for")


def _contrast(a: str, b: str) -> float:
    def lum(hexs: str) -> float:
        r, g, b_ = (int(hexs[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


_GROUNDS = ("#FFFFFF", "#131A24", "#0A0C10")


@pytest.mark.parametrize("shade", tuple(theme.Colors.plot_grey_ramp))
def test_every_shade_clears_the_floor_on_some_ground(shade):
    """A trace is data ink on a figure that goes to paper *and* stays on a dark
    card, and the same hex has to do both — but not equally well, and requiring
    3:1 on *both* is what capped the ramp at a span too narrow to read.

    The two grounds fail at opposite ends, so the rule that buys real
    separation is this one: every shade clears the 3:1 non-text floor on at
    least one ground, and the shade that softens on white is the one the dark
    card renders strongest."""
    best = max(_contrast(shade, g) for g in _GROUNDS)
    assert best >= 3.0, (
        f"{shade} clears 3:1 on no ground at all — "
        + ", ".join(f"{g}={_contrast(shade, g):.2f}" for g in _GROUNDS))


@pytest.mark.parametrize("shade", tuple(theme.Colors.plot_grey_ramp))
@pytest.mark.parametrize("ground", _GROUNDS)
def test_no_shade_disappears_on_any_ground(shade, ground):
    """The other half of the trade, and the one that stops "span more of the
    grey space" turning into a trace nobody can see. 2.3:1 is well above the
    ~1.5 at which a two-point line stops being locatable, and the ends that sit
    near it are strong on the opposite ground."""
    assert _contrast(shade, ground) >= 2.3, (
        f"{shade} measures {_contrast(shade, ground):.2f}:1 on {ground}")


def test_the_ramp_spans_more_than_a_both_grounds_rule_would_allow():
    """The measurement behind widening it. Greys clearing 3:1 on *both* grounds
    occupy #686868…#949494 — 44 levels — so a fifth shade inside that band
    takes the step from 14 down to 11 and makes the ramp harder to read, not
    easier. This pins that the ramp escaped that box, since the box is where a
    future well-meaning edit would put it back."""
    levels = [int(s[1:3], 16) for s in theme.Colors.plot_grey_ramp]
    assert max(levels) - min(levels) > 0x94 - 0x68, (
        "the ramp is back inside the both-grounds band, where a fifth shade "
        "is closer together than four were")


def test_the_shades_stay_apart_from_each_other():
    """A ramp whose steps are indistinguishable is one shade with extra
    entries, and would make the count in ``grey_trace_style`` a lie."""
    ramp = tuple(theme.Colors.plot_grey_ramp)
    levels = [int(s[1:3], 16) for s in ramp]
    gaps = [b - a for a, b in zip(levels, levels[1:])]
    assert all(g >= 16 for g in gaps), f"shades too close: {ramp} (gaps {gaps})"
    assert max(gaps) - min(gaps) <= 2, (
        f"the ramp spends its narrow band unevenly: {ramp} (gaps {gaps}) — "
        "the closest pair is the one a reader cannot separate")


def test_the_ramp_is_not_overridden_by_any_theme_bundle():
    """Theme-independent for the reason ``plot_mono`` is: a trace must not
    change colour when the card flips to publication."""
    for name, bundle in getattr(theme, "THEMES", {}).items():
        assert "plot_grey_ramp" not in bundle, (
            f"theme bundle {name!r} overrides the trace ramp, which would "
            "restore exactly the screen/publication divergence it avoids")


# ── what reaches the axes ───────────────────────────────────────────────────

def _series(n: int):
    out = []
    for i in range(n):
        pts = [LinePoint(t=float(t), mean=10.0 + i + t, spread=0.5, frac=0.2,
                         n_above=5.0 + i, n_total=100, frac_spread=0.02,
                         n_above_spread=0.4)
               for t in (0, 4, 8)]
        out.append(LineSeries(key=f"A{i:02d}", display=f"A{i:02d}", color=_PLATE,
                              kind="well", wells=[f"A{i:02d}"], points=pts,
                              cdf_vals=np.array([1.0, 2.0, 3.0, 4.0])))
    return out


def _render(mode: str, n: int = 14):
    fig, (axm, axf, axn, axc) = plt.subplots(4, 1, figsize=(6, 12))
    render_line_series(
        LineAxes(axm, axf, axn, axc), _series(n), fov_active=False,
        fc_active=False, threshold=1.0, legend_kw={"fontsize": 7},
        warn_color="#ff0000", muted_fg="#333333", color_mode=mode,
    )
    return fig, (axm, axf, axn, axc)


def _traces(ax):
    """The data traces on *ax*, and nothing else.

    The CDF panel also carries the threshold marker, which ``_finalize_line_axes``
    labels so it can appear in the legend. It is chrome, it has no marker and no
    shade from the ramp, and counting it as a trace made the first version of
    the CDF test fail against correct code — the harness measuring itself."""
    return [
        ln for ln in ax.lines
        if not str(ln.get_label()).startswith(("_", "threshold="))
    ]


def test_plate_mode_draws_what_it_always_drew():
    _fig, (axm, axf, axn, axc) = _render("plate", n=3)
    for ax, marker in ((axm, "o"), (axf, "s"), (axn, "^")):
        for ln in _traces(ax):
            assert to_hex(ln.get_color()) == _PLATE.lower()
            assert ln.get_marker() == marker, (
                "the panel's own marker was replaced in plate mode")
    assert all(ln.get_marker() in ("None", None) for ln in _traces(axc)), (
        "the CDF grew markers in plate mode, where the colour already names "
        "each curve and a marker would only add ink")


def test_grey_mode_gives_every_trace_its_own_shade_and_marker():
    # Twelve, the length of the cycle and the number the legend names. Drawing
    # more than that repeats a style by construction, which the cycle test
    # above states outright rather than leaving for this one to trip over.
    _fig, (axm, _f, _n, _c) = _render("grey", n=12)
    drawn = [(to_hex(ln.get_color()), ln.get_marker(),
              str(ln.get_markerfacecolor())) for ln in _traces(axm)]
    ramp = {c.lower() for c in theme.Colors.plot_grey_ramp}
    assert {c for c, _m, _f2 in drawn} <= ramp, (
        f"a trace was drawn in a colour outside the ramp: {drawn}")
    assert len(set(drawn)) == len(drawn), (
        f"two traces share a (shade, marker, face): {drawn}")


def test_a_trace_keeps_one_marker_across_every_panel():
    """The marker is the trace's identity in grey mode, so a reader matching a
    curve from the Value panel to the Fraction panel is matching the shape. A
    shape that changed between them would break exactly that."""
    _fig, (axm, axf, axn, axc) = _render("grey", n=6)
    by_panel = [
        {str(ln.get_label()): (ln.get_marker(), str(ln.get_markerfacecolor()))
         for ln in _traces(ax)}
        for ax in (axm, axf, axn)
    ]
    for label in by_panel[0]:
        marks = {panel[label] for panel in by_panel if label in panel}
        assert len(marks) == 1, f"{label} is marked {marks} across the panels"
    # The CDF labels carry the cell count, so match on the leading token.
    cdf = {str(ln.get_label()).split(" ")[0]: ln.get_marker()
           for ln in _traces(axc)}
    for label, (marker, _face) in by_panel[0].items():
        assert cdf.get(label) == marker, (
            f"{label} is a {marker} on the time panels and a "
            f"{cdf.get(label)} on the CDF")


def test_the_cdf_is_marked_sparsely_rather_than_at_every_vertex():
    """It is the one panel with thousands of points and no error band. Without
    a marker a grey CDF of ten wells is ten curves nobody can separate; with a
    marker at every vertex it is a solid bar."""
    _fig, (_m, _f, _n, axc) = _render("grey", n=3)
    for ln in _traces(axc):
        assert ln.get_marker() not in ("None", None)
        assert ln.get_markevery() is not None, (
            "the CDF marks every vertex, which buries the curve it annotates")


def test_grey_mode_does_not_recolour_the_error_band():
    """The band is the spread, drawn from the trace's colour at low alpha — it
    follows the trace, and the mode has no separate opinion about it."""
    _fig, (axm, _f, _n, _c) = _render("grey", n=2)
    ramp = {c.lower() for c in theme.Colors.plot_grey_ramp}
    for coll in axm.collections:
        for rgba in coll.get_facecolor():
            assert to_hex(rgba) in ramp, (
                "an error band was left in a colour the mode did not choose")


@pytest.mark.parametrize("pref_marker", ["o", "s_open", "D", "keep", "none"])
def test_the_markers_survive_the_export_style_pass(pref_marker):
    """``apply_export_style_prefs`` runs on every redraw and pins marker shape
    and face from the Properties prefs. Both per-artist opt-outs have to hold,
    and the face one is the easy one to forget: without it every open marker
    came back filled, collapsing twelve distinct traces onto six while the
    legend went on advertising twelve."""
    from well_viewer.core.plot_style import (
        DEFAULT_EXPORT_STYLE_PREFS,
        apply_export_style_prefs,
    )

    fig, (axm, _f, _n, _c) = _render("grey", n=12)

    def _cues():
        return [(ln.get_marker(), str(ln.get_markerfacecolor()),
                 ln.get_linestyle()) for ln in _traces(axm)]

    before = _cues()
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["marker_style"] = pref_marker
    # A pref that would flatten the dash cue too, since the pass stamps that
    # from its own row and the third opt-out is what stops it.
    prefs["line_style"] = "-"
    apply_export_style_prefs(fig, prefs)
    after = _cues()
    assert after == before, (
        f"marker_style={pref_marker!r} rewrote the trace cues: {after}")
    assert len(set(after)) == len(after), "traces stopped being distinguishable"


def test_the_line_width_pref_still_reaches_a_grey_figure():
    """The opt-outs are scoped to the three cues that carry identity. Width
    carries none, so the Properties row must still govern it — a fix for the
    dash that also froze the width would disable a control nobody asked about."""
    from well_viewer.core.plot_style import (
        DEFAULT_EXPORT_STYLE_PREFS,
        apply_export_style_prefs,
    )

    fig, (axm, _f, _n, _c) = _render("grey", n=6)
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["line_width"] = 5.0
    apply_export_style_prefs(fig, prefs)
    assert {ln.get_linewidth() for ln in _traces(axm)} == {5.0}


def test_the_cdf_curve_stays_solid_in_grey_mode():
    """It is read for where it crosses, so a dash there reads as a gap in the
    distribution rather than as an identity — and the marker already names the
    curve on that panel."""
    _fig, (_m, _f, _n, axc) = _render("grey", n=6)
    assert {ln.get_linestyle() for ln in _traces(axc)} == {"-"}


def test_the_pref_still_reaches_a_plate_mode_figure():
    """The opt-outs are scoped to the mode that needs them. In plate mode the
    marker carries nothing, so the Properties pref must still govern it —
    otherwise the fix for one tab silently disables a control on the other."""
    from well_viewer.core.plot_style import (
        DEFAULT_EXPORT_STYLE_PREFS,
        apply_export_style_prefs,
    )

    fig, (axm, _f, _n, _c) = _render("plate", n=3)
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["marker_style"] = "D"
    apply_export_style_prefs(fig, prefs)
    assert all(ln.get_marker() == "D" for ln in _traces(axm))


# ── the other surface ───────────────────────────────────────────────────────

def test_the_batch_line_figure_resolves_the_mode_from_the_same_state():
    """Source-level, like the guard beside it: building a batch panel wants a
    loaded plate and a Qt panel, while the question here is only whether that
    call site reads the mode at all. A batch export in plate colours beside a
    screen figure in greys is one setting drawn two ways."""
    import ast

    from tests._repo_scan import REPO_ROOT, parse_source

    path = (REPO_ROOT / "well_viewer" / "features" / "batch_export"
            / "base_panel.py")
    # Through ``_repo_scan`` rather than ``ast.parse(path.read_text())``: the
    # tree is parsed once per process and shared with the sixty-odd other
    # guards that want it, and ``test_the_fast_lane_stays_fast`` fails any test
    # that re-parses one for itself.
    tree = parse_source(path)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None))
        in ("_render_line_series", "render_line_series")
    ]
    assert calls, "the batch line call site moved — update this guard"
    for call in calls:
        kw = next((k for k in call.keywords if k.arg == "color_mode"), None)
        assert kw is not None, (
            f"{path.name}:{call.lineno} renders the line panels without "
            "passing color_mode")
        assert isinstance(kw.value, ast.Call), (
            "color_mode is a literal here, so the batch figure is pinned to "
            "one mode whatever the user picked")


# ── persistence ─────────────────────────────────────────────────────────────

@pytest.mark.qt
def test_the_mode_survives_a_save_and_reload(loaded_app):
    """Through the **real** ``snapshot`` / ``restore``.

    The line mode rides in the top-level plot block rather than a per-tab one,
    and that block's restore takes a whole app — so this is the slow-lane test
    the bar mode did not need. Faking the two halves here would assert that a
    dict literal round-trips through itself, which is the shape of test this
    repository has been burned by: it passes against a key nothing writes.
    """
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.line_color_mode = "grey"
    snap = vs.snapshot(app._ctx)
    assert snap.get("line_color_mode") == "grey"

    app._app_state.plot.line_color_mode = "plate"
    vs.restore(app._ctx, snap)
    assert app._app_state.plot.line_color_mode == "grey"


@pytest.mark.qt
def test_a_junk_value_on_disk_does_not_reach_the_state(loaded_app):
    """An edited ``persistence.json`` is dropped, not stored — otherwise the
    resolver's degrade-to-plate becomes the only thing standing between a typo
    and a figure drawn in no mode at all."""
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.line_color_mode = "grey"
    vs.restore(app._ctx, {"line_color_mode": "chartreuse"})
    assert app._app_state.plot.line_color_mode == "grey", (
        "a junk value on disk overwrote a valid mode")


def test_the_mode_survives_a_new_dataset():
    """A presentation preference, not a statement about this plate's wells."""
    plot = PlotState()
    plot.line_color_mode = "grey"
    plot.reset_for_new_dataset()
    assert plot.line_color_mode == "grey"
