"""Properties → Bars: bar width, and the spacing inside a timepoint group.

With more than one timepoint selected, the bars of one timepoint group — a
sample's bars across timepoints under "Group by well", a timepoint's bars
across samples under "Group by timepoint" — are drawn a sliver apart rather
than a full slot, so each group reads as one block. The sliver and the bar
width are both user settings on ``PlotState``, read by the Bar tab and by the
Batch Export bar figures.

Every position below is asserted as a number, not as "closer than before":
the untight layout is also a list of increasing positions, and a property
like "the gap shrank" is satisfied by a width change alone (§7.8 rule 1). The
width 0.5 / spacing 0.2 used throughout make the tight step 0.6, which no
untight layout produces.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.features.barplot.collect import (
    BarGeometry, bar_layout, bar_width_for, clustered_x_positions,
    collect_bar_items,
)
from tests.conftest import MockApp, make_agg
from tests.test_colour_and_split_by_variable import (
    _TWO_SETS, _WELLS, _batch_figure, _nested_ctx,
)

GEOMETRY = BarGeometry(width=0.5, group_spacing=0.2)


def _two_tp_ctx(group_mode: str, *, timepoints=(0.0, 24.0)):
    """Two pooled groups, CTRL and TREAT, at the given timepoints, no split."""
    app = MockApp(
        per_well_pts={w: [make_agg(t, 100.0 + 10 * i + t) for t in timepoints]
                      for i, w in enumerate(_WELLS)},
        selections=_TWO_SETS,
    )
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = True
    ctx.state.plot.bar_group_mode = group_mode
    return ctx


def _items(ctx, timepoints=(0.0, 24.0)):
    _use_groups, items, _band = collect_bar_items(ctx, list(timepoints))
    return items


def _xs(items, geometry=GEOMETRY):
    """Positions rounded to what a reader can see; the steps are sums of
    floats, and 0.6 + 1.5 + … accumulates last-digit noise."""
    return [round(x, 6) for x in bar_layout(items, False, geometry)[1]]


# ── which bars form a timepoint group ───────────────────────────────────────

def test_group_by_well_draws_each_sample_s_timepoints_as_one_block():
    items = _items(_two_tp_ctx("well"))

    assert [(i.key, i.display) for i in items] == [
        ("CTRL", "t=0"), ("CTRL", "t=24"), ("TREAT", "t=0"), ("TREAT", "t=24")]
    assert bar_layout(items, False, GEOMETRY)[0] == 0.5
    assert _xs(items) == [0.0, 0.6, 2.6, 3.2]


def test_group_by_timepoint_draws_each_timepoint_s_samples_as_one_block():
    items = _items(_two_tp_ctx("timepoint"))

    assert [(i.key, i.group_key) for i in items] == [
        ("CTRL", "0"), ("TREAT", "0"), ("CTRL", "24"), ("TREAT", "24")]
    assert _xs(items) == [0.0, 0.6, 2.6, 3.2]


def test_a_single_timepoint_figure_keeps_its_slots():
    """Nothing is grouped by timepoint, so nothing tightens — under "Group by
    timepoint" every bar shares the one timepoint's cluster, and tightening
    that would squeeze the whole figure into one block. The spacing setting
    must then move nothing at all."""
    for mode in ("well", "timepoint"):
        items = _items(_two_tp_ctx(mode, timepoints=(0.0,)), timepoints=(0.0,))
        assert [i.run_key for i in items] == ["", ""], mode
        untouched = _xs(items, BarGeometry(width=0.5, group_spacing=5.0))
        assert _xs(items) == untouched, mode
        assert untouched[1] - untouched[0] >= 1.0, mode


def test_the_blocks_survive_both_split_levels():
    """The reported case: Split by → Then by with two timepoints. The split
    replaces ``group_key``, so a rule keyed on it would lose the sample; the
    timepoint group is recorded before decoration and survives it."""
    items = _items(_nested_ctx(timepoints=(0.0, 24.0)))

    assert [(i.group_key, i.subgroup_key, i.display) for i in items] == [
        ("HeLa", "0 µM", "B01 t=0"), ("HeLa", "0 µM", "B01 t=24"),
        ("HeLa", "10 µM", "A01 t=0"), ("HeLa", "10 µM", "A01 t=24"),
        ("U2OS", "0 µM", "B02 t=0"), ("U2OS", "0 µM", "B02 t=24"),
        ("U2OS", "10 µM", "A02 t=0"), ("U2OS", "10 µM", "A02 t=24")]
    assert _xs(items) == [0.0, 0.6, 2.1, 2.7, 5.2, 5.8, 7.3, 7.9]


def test_two_samples_in_one_sub_band_are_two_blocks():
    """Inside one sub-band the next sample starts a new block a full slot on,
    rather than joining the previous one's."""
    ctx = _nested_ctx(timepoints=(0.0, 24.0))
    ctx.state.selection.variables.assignments["dose"] = {
        w: "d0" for w in _WELLS}
    items = _items(ctx)

    assert [(i.group_key, i.display) for i in items][:4] == [
        ("HeLa", "A01 t=0"), ("HeLa", "A01 t=24"),
        ("HeLa", "B01 t=0"), ("HeLa", "B01 t=24")]
    assert _xs(items)[:4] == [0.0, 0.6, 1.6, 2.2]


# ── the two settings ───────────────────────────────────────────────────────

def test_the_automatic_width_is_the_one_the_plot_always_had():
    assert bar_width_for(4, True, BarGeometry()) == 0.65
    assert bar_width_for(4, False, BarGeometry()) == 0.6
    assert bar_width_for(20, False, BarGeometry()) == 0.25


def test_a_set_width_replaces_the_automatic_one_within_one_slot():
    assert bar_width_for(20, False, BarGeometry(width=0.4)) == 0.4
    assert bar_width_for(4, False, BarGeometry(width=3.0)) == 1.0


def test_spacing_is_measured_against_the_drawn_width():
    items = _items(_two_tp_ctx("well"))

    assert clustered_x_positions(items, bar_w=0.3, group_spacing=1.0)[:2] == [
        0.0, 0.6]
    assert clustered_x_positions(items, bar_w=0.3, group_spacing=0.0)[:2] == [
        0.0, 0.3]


def test_a_group_step_never_exceeds_one_slot():
    """Wide bars with wide spacing would otherwise put a group's bars further
    apart than two separate groups."""
    items = _items(_two_tp_ctx("well"))

    assert clustered_x_positions(items, bar_w=1.0, group_spacing=0.5) == [
        0.0, 1.0, 3.0, 4.0]


# ── what reaches the figure ────────────────────────────────────────────────

def _draw_tab(ctx, *, width=0.0, spacing=0.2):
    """The Bar tab's own renderer on Agg axes, with the Bars settings set."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.barplot.renderer import draw_grouped_bar_mode

    ctx.widgets = SimpleNamespace(get_or_none=lambda _name: None)
    ctx.state.plot.bar_width = width
    ctx.state.plot.bar_group_spacing = spacing
    fig, (ax_mean, ax_frac, ax_n) = plt.subplots(3)
    ctx.runtime.barplot.fig = fig
    ctx.runtime.barplot.canvas = SimpleNamespace(draw_idle=lambda: None)
    try:
        draw_grouped_bar_mode(
            ctx, ax_mean=ax_mean, ax_frac=ax_frac, ax_n=ax_n, active_rsets=[],
            target_ts=[0.0, 24.0], tp_str="0", threshold=0.5, band_lbl="SEM",
            use_sem=True)
        bars = [(round(p.get_x() + p.get_width() / 2, 3), round(p.get_width(), 3))
                for p in ax_mean.patches]
        footers = [(t.get_text(), round(float(t.xy[0]), 3)) for t in ax_mean.texts
                   if tuple(getattr(t, "xyann", (0, 0))) == (0, -32)]
    finally:
        plt.close(fig)
    return SimpleNamespace(bars=bars, footers=footers)


def test_the_tab_draws_the_set_width_at_the_tight_positions():
    drawn = _draw_tab(_two_tp_ctx("well"), width=0.5, spacing=0.2)

    assert drawn.bars == [(0.0, 0.5), (0.6, 0.5), (2.6, 0.5), (3.2, 0.5)]


def test_the_footers_sit_under_their_own_blocks():
    """The footers are laid out from the positions the bars were drawn at.
    Computed afresh from the default geometry, they would sit under a layout
    that is not on screen."""
    drawn = _draw_tab(_two_tp_ctx("well"), width=0.5, spacing=0.2)

    assert drawn.footers == [("CTRL", 0.3), ("TREAT", 2.9)]


def test_batch_export_draws_the_same_geometry():
    """Batch Export renders with ctx=None, so the settings have to be handed
    over — a batch figure at a different width is a different figure."""
    ctx = _nested_ctx(timepoints=(0.0, 24.0))
    ctx.state.plot.bar_width = 0.5
    ctx.state.plot.bar_group_spacing = 0.2

    fig = _batch_figure(ctx)
    ax = fig.axes[0]
    bars = [(round(p.get_x() + p.get_width() / 2, 3), round(p.get_width(), 3))
            for p in ax.patches]

    assert bars == [(x, 0.5) for x in (0.0, 0.6, 2.1, 2.7, 5.2, 5.8, 7.3, 7.9)]


# ── persistence ────────────────────────────────────────────────────────────

def test_the_settings_round_trip_through_a_saved_view():
    from review.persistence import view_state

    ctx = _two_tp_ctx("well")
    ctx.widgets = SimpleNamespace(get_or_none=lambda _name: None)
    ctx.state.plot.bar_width = 0.4
    ctx.state.plot.bar_group_spacing = 0.35
    snap = view_state._snapshot_bar(ctx)
    ctx.state.plot.bar_width = 0.0
    ctx.state.plot.bar_group_spacing = 0.2

    view_state._apply_bar(ctx, snap)

    assert (ctx.state.plot.bar_width, ctx.state.plot.bar_group_spacing) == (
        0.4, 0.35)


@pytest.mark.parametrize("state", [
    {"bar_width": 7.0, "bar_group_spacing": 0.35},
    {"bar_width": "wide", "bar_group_spacing": 0.35},
])
def test_a_damaged_width_is_dropped_without_taking_the_spacing(state):
    from review.persistence import view_state

    plot = SimpleNamespace(bar_width=0.0, bar_group_spacing=0.2)
    view_state._restore_bar_geometry(plot, state)

    assert (plot.bar_width, plot.bar_group_spacing) == (0.0, 0.35)


# ── the controls, pressed ──────────────────────────────────────────────────

def _build_sidebar(qtbot, monkeypatch, *, bar_fig=True):
    """The Properties sidebar over a figure the app knows as its Bar plot."""
    from matplotlib.figure import Figure

    from review.features.export.sidebar_view import ExportStyleSidebar
    from tests.test_properties_value_labels import _stub_app

    fig = Figure(figsize=(4, 3))
    fig.add_subplot(1, 1, 1)
    app = _stub_app()
    if bar_fig:
        app._bar_fig = fig
    redraws: list[int] = []
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_active_plot",
                        lambda self: redraws.append(1), raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="bar")
    qtbot.addWidget(sb)
    return app, sb, redraws


def _bars_section(sidebar):
    from widgets.collapsible_section import CollapsibleSection

    for sec in sidebar.findChildren(CollapsibleSection):
        if sec.titleText() == "Bars":
            return sec
    return None


@pytest.mark.qt
def test_the_bars_section_is_offered_only_on_the_bar_plot(qtbot, monkeypatch):
    _app, sb, _ = _build_sidebar(qtbot, monkeypatch)
    assert _bars_section(sb) is not None
    _app, other, _ = _build_sidebar(qtbot, monkeypatch, bar_fig=False)
    assert _bars_section(other) is None


@pytest.mark.qt
def test_the_width_controls_reach_the_state_and_redraw(qtbot, monkeypatch):
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)
    plot = app._app_state.plot
    assert sb._bars_auto_tog.isChecked()
    assert not sb._bars_width_step.isEnabled()

    sb._bars_auto_tog.setChecked(False)
    assert plot.bar_width == 0.6
    assert sb._bars_width_step.isEnabled()
    sb._bars_width_step.setValue(0.4)
    assert plot.bar_width == 0.4

    sb._bars_auto_tog.setChecked(True)
    assert plot.bar_width == 0.0
    assert len(redraws) == 3, "each change has to repaint the plot"


@pytest.mark.qt
def test_the_spacing_control_reaches_the_state_and_redraws(qtbot, monkeypatch):
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)

    sb._bars_spacing_step.setValue(0.5)

    assert app._app_state.plot.bar_group_spacing == 0.5
    assert app._app_state.plot.bar_width == 0.0
    assert redraws


@pytest.mark.qt
def test_a_reopened_panel_shows_the_state_it_finds(qtbot, monkeypatch):
    """The panel is cached per figure. A view restore that moved the width
    behind its back must not leave it showing — and later writing back — the
    old one."""
    app, sb, redraws = _build_sidebar(qtbot, monkeypatch)
    app._app_state.plot.bar_width = 0.35
    app._app_state.plot.bar_group_spacing = 0.1

    sb._resync_plot_state_segments()

    assert not sb._bars_auto_tog.isChecked()
    assert sb._bars_width_step.value() == 0.35
    assert sb._bars_spacing_step.value() == 0.1
    assert not redraws, "catching up with the state is not a user edit"
