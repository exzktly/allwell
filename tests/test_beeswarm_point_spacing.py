"""Properties → Beeswarm → Point spacing: how far apart beeswarm dots are drawn.

``PlotState.swarm_spacing`` is a multiple of one dot's width, or 0.0 for Auto.
Two figures read it: the Bar plot's Beeswarm / All points dots (through
``barplot.renderer.swarm_pitch`` → ``_beeswarm_jitter(pitch=…)``) and the
Nanowells Distribution view (through ``nanowell.controller.draw_view`` →
``render_distribution(spacing=…)``).

Every test asserts a position or a value, never "narrower than before": a
column stretched to fill its slot and a column drawn at a fixed spacing are
both lists of offsets inside the slot, so a property like "it got denser" is
satisfied by the Auto layout too (CLAUDE.md §7.8 rule 1). The fixtures are
chosen so Auto and the set spacing put dots in different places.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from review.core.plot_state import PlotState
from review.data.data_loading import _beeswarm_jitter
from tests.test_bar_color_mode_per_cell import stub_ctx  # noqa: F401 — the fixture


# ── the packing ────────────────────────────────────────────────────────────

def test_auto_stretches_a_row_to_fill_the_column():
    # Nine ties make one row at 0, ±d, ±2d, … — Auto scales the widest to the slot.
    xs, _ = _beeswarm_jitter([5.0] * 9, max_spread=0.35, n_bins=40)
    assert sorted(round(x, 6) for x in xs) == [round(k * 0.35 / 4, 6) for k in range(-4, 5)]


def test_a_set_pitch_draws_the_row_at_that_spacing():
    xs, _ = _beeswarm_jitter([5.0] * 9, max_spread=0.35, n_bins=40, pitch=0.01)
    assert sorted(round(x, 6) for x in xs) == [round(k * 0.01, 6) for k in range(-4, 5)]


def test_a_pitch_too_wide_for_the_column_is_held_to_the_column():
    auto, _ = _beeswarm_jitter([5.0] * 9, max_spread=0.35, n_bins=40)
    wide, _ = _beeswarm_jitter([5.0] * 9, max_spread=0.35, n_bins=40, pitch=0.1)
    assert sorted(round(x, 6) for x in wide) == sorted(round(x, 6) for x in auto)


# ── the Bar plot ───────────────────────────────────────────────────────────

def _ctx(spacing: float):
    plot = PlotState()
    plot.swarm_spacing = spacing
    return SimpleNamespace(state=SimpleNamespace(plot=plot))


def _ax():
    from matplotlib.figure import Figure

    fig = Figure(figsize=(6, 4), dpi=100)
    return fig.add_subplot(1, 1, 1)


def test_swarm_pitch_is_none_on_auto():
    from review.features.barplot.renderer import swarm_pitch

    assert swarm_pitch(_ctx(0.0), _ax(), 3.2) is None


def test_swarm_pitch_is_the_spacing_times_the_marker_width_in_data_units(monkeypatch):
    from review.features.barplot import renderer as rnd

    monkeypatch.setattr(rnd, "figure_prefs", lambda _ctx, _fig: {"marker_size": 4.0})
    ax = _ax()
    width_px = ax.get_position().width * 6 * 100
    # 0.5 × a 4 pt dot = 2 pt = 2·100/72 px, over an axes 3.2 data units wide.
    expected = 0.5 * 4.0 * 100 / 72 * 3.2 / width_px
    assert rnd.swarm_pitch(_ctx(0.5), ax, 3.2) == pytest.approx(expected)


def test_the_beeswarm_renderer_hands_the_pitch_to_the_packing(stub_ctx, monkeypatch):
    """At the boundary: what ``draw_beeswarm`` passes the packing, and where the dots it draws are."""
    from review.features.barplot import renderer as rnd
    from tests.test_bar_color_mode_per_cell import _PLATE, _WELLS, _axes

    seen: list = []
    real = rnd._beeswarm_jitter

    def _spy(values, **kw):
        out = real(values, **kw)
        seen.append((kw.get("pitch"), np.asarray(out[0]) - kw["x_center"]))
        return out

    monkeypatch.setattr(rnd, "_beeswarm_jitter", _spy)
    monkeypatch.setattr(rnd, "swarm_pitch", lambda ctx, ax, span: 0.002 if ctx.state.plot.swarm_spacing else None)

    def _drawn(spacing):
        stub_ctx.state.plot.swarm_spacing = spacing
        seen.clear()
        axm, axf, _ = _axes()
        rnd.draw_beeswarm(stub_ctx, axm, axf, _WELLS, list(_PLATE), list(_WELLS), [0.0, 0.0], "0", 10.0)
        dots = [c for c in axm.collections if getattr(c, "_aw_marker_collection", False)]
        return [np.asarray(c.get_offsets())[:, 0] - i for i, c in enumerate(dots)], list(seen)

    auto, calls = _drawn(0.0)
    assert [pitch for pitch, _ in calls] == [None, None]
    for offsets in auto:
        assert np.max(np.abs(offsets)) == pytest.approx(0.35)          # Auto stretches to the column
    fixed, calls = _drawn(1.0)
    # Each column packed as it is drawn, then once more on the page once the y range is known (``_pack_on_page``):
    # the dots drawn are the second packing's.
    assert [pitch for pitch, _ in calls] == [0.002] * 4
    for offsets, (_pitch, packed) in zip(fixed, calls[2:]):
        assert np.allclose(offsets, packed)                            # the dots are the packing's
        assert np.max(np.abs(offsets)) < 0.35                          # not stretched back to the column


def test_a_set_spacing_holds_on_screen_in_a_tight_column_and_a_spread_one(stub_ctx, monkeypatch):
    """At spacing 1.0 no two dots of a column overlap on the page, whatever the column's own range.

    The packing used to measure a dot's height as 1/40 of *each column's own* value range, and only its width in
    page units. A column whose cells sit in a narrow band of the shared axis was then packed as though its cells
    were spread over the whole height, and drawn as a narrow swarm of dots piled onto each other. The fixture gives
    the two columns ranges five times apart, so a per-column height cannot be right for both.
    """
    import pandas as pd

    from review.features.barplot import renderer as rnd
    from tests.test_bar_color_mode_per_cell import _PLATE, _VAL_COL, _WELLS, _axes

    spreads = {"A01": 20.0, "A02": 4.0}

    def _frame(_ctx, well):
        rng = np.random.default_rng(1)
        n = 60
        return pd.DataFrame({"timepoint_hours": np.zeros(n), _VAL_COL: rng.normal(50.0, spreads[well], n),
                             "fov": np.repeat(["f1", "f2", "f3"], n // 3), "area_px": np.full(n, 200.0)})

    monkeypatch.setattr(rnd, "get_rows", _frame)
    stub_ctx.state.plot.swarm_spacing = 1.0
    axm, axf, _ = _axes()
    rnd.draw_beeswarm(stub_ctx, axm, axf, _WELLS, list(_PLATE), list(_WELLS), [0.0, 0.0], "0", 10.0)
    dot_px = 5.0 * axm.figure.dpi / 72.0          # the stub has no style store: the default 5 pt marker
    dots = [c for c in axm.collections if getattr(c, "_aw_marker_collection", False)]
    assert len(dots) == 2
    closest = []
    for c in dots:
        px = axm.transData.transform(np.asarray(c.get_offsets()))
        gaps = np.sqrt(((px[:, None] - px[None]) ** 2).sum(-1))
        np.fill_diagonal(gaps, np.inf)
        closest.append(round(float(gaps.min() / dot_px), 2))
    assert min(closest) >= 0.99, closest


# ── the Nanowells Distribution view ───────────────────────────────────────

def _distribution(spacing: float):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.features.nanowell import distribution_compute as dc
    from review.features.nanowell.renderer import _dot_units, render_distribution

    # Forty ties on one continuous value: one row, so the slots used are 0, ±w, ±2w, … at the pitch.
    values = np.full(40, 2.5)
    swarm = dc.Swarm("t", "A01", 0.0, values, [("A01", j, 0) for j in range(40)], ["1"] * 40, 40)
    model = dc.DistributionModel("v", "v", "Value", dc.TIME, False, [dc.Panel("A01", [swarm], [(0.0, "t")])])
    fig = Figure(figsize=(6, 4))
    render_distribution(fig, model, spacing=spacing)
    ax = fig.axes[0]
    dots = next(c for c in ax.collections if c.get_gid() == "nw-dots")
    xs = np.asarray(dots.get_offsets())[:, 0]
    return np.unique(np.round(np.abs(xs), 9)), _dot_units(ax, fig)[0]


@pytest.mark.parametrize("spacing", [1.0, 2.0, 0.5])
def test_the_distribution_view_lays_dots_out_at_the_spacing(spacing):
    offsets, dot_w = _distribution(spacing)
    nonzero = offsets[offsets > 0]
    assert len(nonzero)
    assert nonzero[0] == pytest.approx(spacing * dot_w)


def test_draw_view_hands_the_page_s_spacing_to_the_distribution(monkeypatch):
    from review.features.nanowell import controller

    got: list = []
    monkeypatch.setattr(controller, "render_distribution",
                        lambda fig, model, *, selected=(), spacing=1.0, **_kw: got.append(spacing))
    model = SimpleNamespace(empty="", axis="wells", notes=[], starting=None)
    monkeypatch.setattr(controller, "distribution_model", lambda *a, **k: model)
    controller.draw_view(None, "distribution", None, SimpleNamespace(channels=[], fluor_backgrounds={},
                                                                     time_source=None),
                         groups={}, metric="n", channel=None, axis="wells", timepoint="", control="",
                         baseline="earliest", cohort=False, well="", well_timepoint="", swarm_spacing=0.7)
    assert got == [0.7]


def test_every_app_call_of_draw_view_passes_the_spacing():
    """The page and Batch Export's Nanowells mode each call ``draw_view``; one that leaves ``swarm_spacing`` out
    draws at Auto whatever Properties says, and nothing else would notice. The same holds for the page's **Show** and
    **Smoothing** (``dist_style`` / ``violin_smooth``): left out, a batch figure draws Auto's dots or violins whatever
    the page shows. The Identity view has no swarm, so its call (view given as the literal ``"identity"``) is
    exempt."""
    import ast

    from tests._repo_scan import parse_source

    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    sites, missing = 0, []
    for rel in ("review/features/nanowell/controller.py", "review/features/batch_export/nanowell_panel.py"):
        for node in ast.walk(parse_source(root / rel)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("draw_view", "_draw_view")):
                continue
            view = node.args[1] if len(node.args) > 1 else None
            if isinstance(view, ast.Constant) and view.value == "identity":
                continue
            sites += 1
            given = {k.arg for k in node.keywords}
            missing += [f"{rel}:{node.lineno} {name}" for name in ("swarm_spacing", "dist_style", "violin_smooth",
                                                                       "hist_bins")
                        if name not in given]
    assert sites == 2, f"expected the page's and Batch Export's calls, found {sites}"
    assert not missing, f"draw_view called without the Distribution's drawing settings at {missing}"


@pytest.mark.parametrize("stored, read", [(0.0, 0.0), (0.7, 0.7), (-1.0, 0.0)])
def test_swarm_spacing_of_reads_the_plot_state(stored, read):
    from review.features.nanowell.controller import swarm_spacing_of

    assert swarm_spacing_of(_ctx(stored)) == read


# ── the Properties control, pressed ────────────────────────────────────────

def _sidebar(qtbot, monkeypatch, attr):
    from matplotlib.figure import Figure

    from review.features.export.properties_view import ExportStyleSidebar
    from tests.test_properties_value_labels import _stub_app

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    if attr:
        setattr(app, attr, fig)
    redraws: list[int] = []
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_active_plot",
                        lambda self: redraws.append(1), raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="plot")
    qtbot.addWidget(sb)
    return app, sb, redraws


def _section(sidebar):
    from widgets.collapsible_section import CollapsibleSection

    return next((s for s in sidebar.findChildren(CollapsibleSection) if s.titleText() == "Beeswarm"), None)


@pytest.mark.qt
@pytest.mark.parametrize("attr, offered", [("_bar_fig", True), ("_nw_fig", True), ("_line_fig", False)])
def test_the_section_is_offered_only_where_a_beeswarm_is_drawn(qtbot, monkeypatch, attr, offered):
    _app, sb, _ = _sidebar(qtbot, monkeypatch, attr)
    assert (_section(sb) is not None) == offered


@pytest.mark.qt
def test_the_spacing_controls_reach_the_state_and_redraw(qtbot, monkeypatch):
    app, sb, redraws = _sidebar(qtbot, monkeypatch, "_bar_fig")
    plot = app._app_state.plot
    assert sb._swarm_auto_tog.isChecked() and not sb._swarm_spacing_step.isEnabled()

    sb._swarm_auto_tog.setChecked(False)
    assert plot.swarm_spacing == 1.0 and sb._swarm_spacing_step.isEnabled()
    sb._swarm_spacing_step.setValue(0.4)
    assert plot.swarm_spacing == pytest.approx(0.4)

    sb._swarm_auto_tog.setChecked(True)
    assert plot.swarm_spacing == 0.0
    assert len(redraws) == 3, "each change has to repaint the plot"


@pytest.mark.qt
def test_a_panel_reopened_after_a_restore_shows_the_restored_spacing(qtbot, monkeypatch):
    app, sb, _ = _sidebar(qtbot, monkeypatch, "_bar_fig")
    app._app_state.plot.swarm_spacing = 2.5
    sb._resync_swarm_spacing()
    assert not sb._swarm_auto_tog.isChecked()
    assert sb._swarm_spacing_step.value() == pytest.approx(2.5)


# ── persistence ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state, kept", [
    ({"swarm_spacing": 0.6}, 0.6),
    ({"swarm_spacing": 9.0}, 0.0),
    ({"swarm_spacing": "dense"}, 0.0),
])
def test_a_saved_spacing_is_restored_only_when_it_can_be_drawn(state, kept):
    from review.persistence import view_state

    plot = SimpleNamespace(bar_width=0.0, bar_group_spacing=0.2, swarm_spacing=0.0)
    view_state._restore_bar_geometry(plot, state)
    assert plot.swarm_spacing == kept
