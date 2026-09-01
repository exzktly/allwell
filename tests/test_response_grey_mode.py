"""Response Curves' Colours mode — Colour, or greys told apart twice.

The Bar and Line tabs have carried a Properties → Colours segment for a while;
this is the Response tab's, and it is not either of theirs applied to a third
plot. A bar is named by the tick label under it, so one ink for all of them
loses nothing. A trace is not, so the Line tab's grey mode spends three cues:
shade, dash and marker. **A response curve is a trace whose dash is already
spoken for** — it carries the timepoint, which is what lets a reader see that
two curves are the same condition at two moments — so grey here takes the shade
and the marker and leaves the dash exactly where it was.

That last sentence is the design, so it is what most of this file measures. A
grey mode that let the trace cycle's dash through would look like it had added
a cue while silently removing the one the figure already had, and every
"the curves are distinguishable" assertion would still pass.

The other half is the restyle. ``apply_export_style_prefs`` runs after every
redraw and overrides the marker shape, the marker face and the dash from the
Properties prefs unless the artist says that property is data. The timepoint
dash never said so: it was applied by the renderer and removed again on the
first restyle, so a two-timepoint figure had been coming back as two solid
lines all along.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from well_viewer.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs,
)
from well_viewer.domain.line_colors import grey_trace_style
from well_viewer.domain.response_colors import (
    RESPONSE_COLOR_MODES, response_color_mode, series_style_for_mode,
)
from well_viewer.features.response.collect import (
    ResponseCurve, ResponseData, ResponsePoint,
)
from well_viewer.features.response.renderer import draw_response

_DOSES = (1.0, 3.0, 10.0)


def _points(offset: float):
    return [ResponsePoint(x=x, y=x + offset, err=float("nan"), n=3,
                          member=f"m{i}")
            for i, x in enumerate(_DOSES)]


def _data(series=("hela", "u2os"), timepoints=(24.0, 48.0)):
    """A dose series on *series* conditions at *timepoints* moments.

    Two of each on purpose: one series cannot show that colour tracks the
    condition, and one timepoint cannot show that the dash tracks the moment.
    A fixture with one of either would let a mode that confused the two pass.
    """
    curves = []
    for si, sid in enumerate(series):
        for ti, t in enumerate(timepoints):
            curves.append(ResponseCurve(
                timepoint=t, series_id=sid, series_label=sid.upper(),
                points=_points(si * 10.0 + ti)))
    return ResponseData(variable_name="Dose",
                        series_name="Cell line" if len(series) > 1 else "",
                        curves=curves)


def _drawn(data, mode: str, *, restyle: bool = False):
    """``[(series id, timepoint, colour, dash, marker, face)]`` as drawn."""
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    draw_response(SimpleNamespace(), ax, data, log_x=False, color_mode=mode)
    if restyle:
        apply_export_style_prefs(fig, dict(DEFAULT_EXPORT_STYLE_PREFS))
    drawn = []
    curves = [c for c in data.curves if c.points]
    for curve, container in zip(curves, ax.containers):
        line = container.lines[0]
        drawn.append((curve.series_id, curve.timepoint, line.get_color(),
                      line.get_linestyle(), line.get_marker(),
                      line.get_markerfacecolor()))
    assert len(drawn) == len(curves), "not every curve was drawn"
    return drawn


# ── the resolver ─────────────────────────────────────────────────────────────

def test_the_mode_defaults_to_colour_and_rejects_anything_else():
    assert response_color_mode(SimpleNamespace()) == "colour"
    assert response_color_mode(SimpleNamespace(response_color_mode="grey")) == "grey"
    assert response_color_mode(
        SimpleNamespace(response_color_mode="chartreuse")) == "colour"
    assert response_color_mode(SimpleNamespace(response_color_mode="")) == "colour"
    assert "plate" not in RESPONSE_COLOR_MODES, (
        "this tab's default is not the plate — a curve is one value of a sample "
        "variable, not one well")


def test_the_grey_style_drops_the_trace_cycle_s_dash():
    """The one line of this module that is not the Line tab's.

    Asserted against ``grey_trace_style`` rather than as "linestyle is None", so
    a resolver that returned a blank style — no shade, no marker, no dash —
    could not pass by having nothing to leak.
    """
    for index in range(6):
        line_style = grey_trace_style(index)
        ours = series_style_for_mode("grey", index, None)
        assert ours.color == line_style.color
        assert ours.marker == line_style.marker
        assert ours.filled == line_style.filled
        assert line_style.linestyle is not None, (
            "the trace cycle stopped carrying a dash, so this guard is "
            "measuring nothing")
        assert ours.linestyle is None, (
            "grey mode carried the trace cycle's dash onto a tab whose dash "
            "is the timepoint's")


# ── what reaches the axes ────────────────────────────────────────────────────

def test_grey_mode_inks_each_series_from_the_grey_ramp():
    """The value, not "they differ" — the palette mode's colours differ too."""
    drawn = _drawn(_data(), "grey")
    by_series = {sid: colour for sid, _t, colour, *_rest in drawn}
    assert by_series["hela"] == grey_trace_style(0).color
    assert by_series["u2os"] == grey_trace_style(1).color


def test_grey_mode_gives_each_series_its_own_marker():
    drawn = _drawn(_data(), "grey")
    markers = {sid: marker for sid, _t, _c, _d, marker, _f in drawn}
    assert markers["hela"] == grey_trace_style(0).marker
    assert markers["u2os"] == grey_trace_style(1).marker
    assert markers["hela"] != markers["u2os"]


def test_one_series_keeps_one_style_across_its_timepoints():
    """Colour and marker say *which condition*; a curve that changed either
    between two moments would break the pairing the split exists to show."""
    drawn = _drawn(_data(), "grey")
    for sid in ("hela", "u2os"):
        rows = [(c, m) for s, _t, c, _d, m, _f in drawn if s == sid]
        assert len(rows) == 2 and rows[0] == rows[1], rows


@pytest.mark.parametrize("mode", ["colour", "grey"])
def test_the_dash_is_the_timepoint_s_in_both_modes(mode):
    """The design claim: the dash names the moment, in both modes.

    What this catches is a dash keyed on anything else — the series, or a
    constant — which is what a grey mode that spent the dash on the trace would
    have had to do. It does **not** catch the resolver handing back the trace
    cycle's dash, because ``_CurveStyles.for_curve`` never reads that field;
    ``test_the_grey_style_drops_the_trace_cycle_s_dash`` is what holds that
    half, and it was mutation-checked separately for exactly this reason.
    """
    drawn = _drawn(_data(), mode)
    by_timepoint: dict[float, set] = {}
    for _sid, timepoint, _c, dash, *_rest in drawn:
        by_timepoint.setdefault(timepoint, set()).add(dash)
    assert all(len(v) == 1 for v in by_timepoint.values()), (
        f"two series at one timepoint were drawn with different dashes: "
        f"{by_timepoint}")
    dashes = {t: next(iter(v)) for t, v in by_timepoint.items()}
    assert len(set(dashes.values())) == 2, (
        f"the two timepoints were drawn with the same dash: {dashes}")


def test_a_figure_with_one_series_is_still_grey():
    """The mode's whole point is that the figure carries no colour.

    With nothing to tell apart the palette mode leaves matplotlib's own cycle
    alone, which is right there and wrong here: a lone curve left on the cycle
    comes back the cycle's first colour, which is blue.
    """
    drawn = _drawn(_data(series=("hela",)), "grey")
    assert {row[2] for row in drawn} == {grey_trace_style(0).color}


def test_colour_mode_still_draws_the_palette_it_always_did():
    """The other direction, so the mode is a choice rather than a one-way trip."""
    import matplotlib.pyplot as plt

    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color") or ["C0"]
    drawn = _drawn(_data(), "colour")
    by_series = {sid: colour for sid, _t, colour, *_rest in drawn}
    assert by_series["hela"] == cycle[0]
    assert by_series["u2os"] == cycle[1 % len(cycle)]
    assert {row[4] for row in drawn} == {"o"}, (
        "the palette mode grew a marker opinion it never had")


# ── and it all survives the pass that runs after every redraw ────────────────

@pytest.mark.parametrize("mode", ["colour", "grey"])
def test_the_encoding_survives_the_shared_restyle(mode):
    """``apply_export_style_prefs`` runs on every redraw and on every theme flip.

    It resets the dash and the marker from the Properties prefs unless the
    artist says they are data. The timepoint dash never said so, so a
    two-timepoint figure was drawn correctly and then flattened to two solid
    lines before anybody saw it.
    """
    data = _data()
    assert _drawn(data, mode, restyle=True) == _drawn(data, mode), (
        "the restyle changed how the curves were drawn")


# ── the control the user presses ─────────────────────────────────────────────
#
# §7.6: a control that exists is not a control that works. Asserting that the
# Colours section is *offered* on this figure would pass against a segment
# connected to nothing, which is how the per-sample control editor shipped
# unopenable past a lane of thousands of tests.

def _stub_app(fig, *, response=True):
    from well_viewer.core.app_runtime import AppRuntime
    from well_viewer.core.cell_gating_state import CellGatingState
    from well_viewer.core.export_prefs_state import ExportPrefsState
    from well_viewer.core.plot_state import PlotState
    from well_viewer.features.registry import build_feature_registry
    from well_viewer.features.scatter.state import ScatterState

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
    setattr(app, "_response_fig" if response else "_heatmap_fig", fig)
    app._redraw = lambda: None
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
        app=app, state=app._app_state, runtime=AppRuntime(app),
        features=build_feature_registry(),
        widgets=SimpleNamespace(
            get_or_none=lambda n: getattr(app, n, None),
            register=lambda n, w: (setattr(app, n, w), w)[1]),
        notify=SimpleNamespace(set_status=lambda _m: None),
    )
    return app


def _sidebar(qtbot, monkeypatch, *, response=True):
    """A real sidebar over the figure, with this tab's redraw recorded."""
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app(fig, response=response)
    calls: list[str] = []
    monkeypatch.setattr(
        "well_viewer.features.response.controller.redraw_response",
        lambda _ctx: calls.append("response"))
    sb = ExportStyleSidebar(app, None, fig, None, default_name="response_figure")
    qtbot.addWidget(sb)
    return app, sb, calls


def _colour_segment(sidebar):
    from widgets.collapsible_section import CollapsibleSection
    from widgets.segmented_control import SegmentedControl

    for section in sidebar.findChildren(CollapsibleSection):
        if section.titleText() != "Colours":
            continue
        segments = section.findChildren(SegmentedControl)
        if segments:
            return segments[0]
    return None


@pytest.mark.qt
def test_the_response_figure_offers_colour_and_grey(qtbot, monkeypatch):
    _app, sb, _calls = _sidebar(qtbot, monkeypatch)
    segment = _colour_segment(sb)
    assert segment is not None, "no Colours section on the Response Properties"
    labels = [segment.segmentText(i) for i in range(segment.count())]
    assert labels == ["Colour", "Grey"], labels
    assert segment.currentData() == "colour"


@pytest.mark.qt
def test_picking_grey_reaches_the_state_and_repaints(qtbot, monkeypatch):
    """Both halves: the field the renderer reads, and the redraw that shows it.

    The mode is chosen while the artists are built, so the restyle path cannot
    apply it — a segment that wrote the state and asked for no redraw would
    leave the figure in the old colours until something unrelated repainted it.
    """
    app, sb, calls = _sidebar(qtbot, monkeypatch)
    _colour_segment(sb).setCurrentByData("grey")
    assert app._app_state.plot.response_color_mode == "grey"
    assert response_color_mode(app._app_state.plot) == "grey"
    assert calls == ["response"], (
        f"picking Grey repainted {calls} rather than the Response figure")


@pytest.mark.qt
def test_picking_colour_again_puts_the_palette_back(qtbot, monkeypatch):
    """A segment wired to a constant passes the test above and strands the
    user in grey."""
    app, sb, _calls = _sidebar(qtbot, monkeypatch)
    segment = _colour_segment(sb)
    segment.setCurrentByData("grey")
    segment.setCurrentByData("colour")
    assert app._app_state.plot.response_color_mode == "colour"


@pytest.mark.qt
def test_a_figure_the_mode_does_nothing_to_is_not_offered_the_section(
        qtbot, monkeypatch):
    """A Colours row on the Heat Map would be a control that does nothing —
    the same defect as one that is offered and broken, wearing the other face."""
    _app, sb, _calls = _sidebar(qtbot, monkeypatch, response=False)
    assert _colour_segment(sb) is None


@pytest.mark.qt
def test_the_segment_re_seeds_from_the_state_rather_than_from_the_bar_default(
        qtbot, monkeypatch):
    """The sidebar is built once per figure and cached for the process, so it
    re-seeds its segments on every open. That re-seed used one hardcoded
    default, ``"plate"`` — a value this segment does not offer, which would move
    it with signals blocked to a mode the figure cannot be drawn in and then
    swallow the click that would correct it."""
    app, sb, _calls = _sidebar(qtbot, monkeypatch)
    app._app_state.plot.response_color_mode = "grey"
    sb._resync_plot_state_segments()
    assert _colour_segment(sb).currentData() == "grey"

    app._app_state.plot.response_color_mode = "colour"
    sb._resync_plot_state_segments()
    assert _colour_segment(sb).currentData() == "colour"


# ── and it survives a save and a reload ──────────────────────────────────────

@pytest.mark.qt
def test_the_mode_survives_a_save_and_reload(loaded_app):
    """Through the **real** ``snapshot`` / ``restore``.

    The mode rides in the top-level plot block rather than in this tab's own,
    for the reason the Line one does: that block travels with every saved view,
    while a per-tab block is applied only when its tab is next activated, and a
    figure restored in the wrong colour mode is a different figure from the one
    that was saved. The block's restore takes a whole app, so this is a
    slow-lane test — faking the two halves would assert that a dict literal
    round-trips through itself, which passes against a key nothing writes.
    """
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.response_color_mode = "grey"
    snap = vs.snapshot(app._ctx)
    assert snap.get("response_color_mode") == "grey"

    app._app_state.plot.response_color_mode = "colour"
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
    from well_viewer.core.plot_state import PlotState

    plot = PlotState()
    plot.response_color_mode = "grey"
    plot.reset_for_new_dataset()
    assert plot.response_color_mode == "grey"
