"""Bar ticks under both fold-change stages lie flat and clear of the next panel.

Reported: with a per-sample control and *Then relative to* both on, the bar
plot's x tick labels overlapped and could not be read. Each tick spelled out
both denominators on three lines — ``siKRAS Erlotinib`` / ``÷ WT Erlotinib`` /
``÷ (siKRAS Vehicle ÷ WT Vehicle)`` — as wide as two bars, so even four bars
could not lie flat; the tick fit stood them on end, and the figure's fixed
panel spacing put them through the next panel's title.

Drawn by the Bar tab's renderer and styled by its own style step, on a 3 × 3
plate of realistic names, at the bar canvas's height and a width a desk with a
dock open gives it.
"""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from review.domain import sample_variables as sv  # noqa: E402
from review.domain import variable_pairing as vp  # noqa: E402
from review.domain.control_ref import ControlRef  # noqa: E402
from tests.conftest import MockApp, make_agg  # noqa: E402

_RNAS = ("WT", "siKRAS", "siMYC")
_TREATS = ("Vehicle", "Erlotinib", "Trametinib")
_NB = " "


def _ctx(*, relative: bool = True):
    pts, sels, wells_of = {}, [], {}
    vset, rna = sv.new_variable(sv.VariableSet(), "RNA")
    vset, treat = sv.new_variable(vset, "Treatment")
    for vid, labels in ((rna, _RNAS), (treat, _TREATS)):
        for label in labels:
            vset = sv.add_value(vset, vid, label)

    def value(vid, label):
        return next(v.id for v in vset.by_id(vid).values if v.label == label)

    for row, (r, t) in enumerate((r, t) for r in _RNAS for t in _TREATS):
        name, wells = f"{r} {t}", [f"{'ABCDEFGHI'[row]}0{i + 1}" for i in range(3)]
        wells_of[name] = wells
        for i, w in enumerate(wells):
            pts[w] = [make_agg(0.0, 100.0 * (1 + 0.1 * i) * (1 + row / 5))]
        sels.append({"name": name, "wells": wells, "hidden": False})
        vset = sv.assign(vset, rna, wells, value(rna, r))
        vset = sv.assign(vset, treat, wells, value(treat, t))
    ctx = MockApp(per_well_pts=pts, selections=sels)._ctx
    ctx.state.selection.variables = vset
    plot = ctx.state.plot
    plot.fc_vs_control_on, plot.fc_control_mode = True, "per_member"
    plot.fc_control_map = vp.pair_by_variable(
        vset, wells_of, {n: ControlRef.for_replicate_set(n) for n in wells_of},
        variable_id=rna, value_id=value(rna, "WT"), within=[treat]).mapping
    if relative:
        bar = ctx.state.feature_slice("bar")
        bar.rel_variable_id, bar.rel_value_id, bar.rel_within = treat, value(treat, "Vehicle"), [rna]
    return ctx


def _drawn(ctx, width_in: float):
    from review.features.barplot import renderer as R
    from review.features.barplot.tab_view import _FIG_H

    ctx.widgets = SimpleNamespace(get_or_none=lambda _name: None)
    fig = ctx.runtime.barplot.fig or plt.figure(figsize=(width_in, _FIG_H), dpi=100)
    if not fig.axes:
        for i in (1, 2, 3):
            fig.add_subplot(3, 1, i)
        fig.subplots_adjust(**R.BAR_FIGURE_MARGINS)
    ctx.runtime.barplot.fig = fig
    ctx.runtime.barplot.canvas = SimpleNamespace(draw_idle=lambda: None)
    for ax in fig.axes:
        ax.cla()
    mean, frac, n = fig.axes[:3]
    R.draw_grouped_bar_mode(ctx, ax_mean=mean, ax_frac=frac, ax_n=n, active_rsets=[],
                            target_ts=[0.0], tp_str="0", threshold=0.5, band_lbl="SEM",
                            use_sem=True)
    R._style_bar_figure(ctx)
    fig.canvas.draw()
    return fig


def test_the_tick_names_both_stages_on_two_lines_each_denominator_whole():
    fig = _drawn(_ctx(), 9.0)
    ticks = [t.get_text() for t in fig.axes[0].get_xticklabels()]
    assert ticks[0].replace("\n", "|") in (
        f"siKRAS Erlotinib|÷{_NB}WT{_NB}Erlotinib ÷{_NB}Vehicle",      # as written
        f"siKRAS Erlotinib|÷{_NB}WT{_NB}Erlotinib|÷{_NB}Vehicle")      # wrapped between the terms
    plt.close(fig)


@pytest.mark.parametrize("width_in", [9.0, 8.0])
def test_the_ticks_lie_flat_apart_and_clear_of_the_panel_below(width_in):
    """At 8 in the labels wrap to four lines: flat, but taller than the fixed
    margins leave above the next panel, so the figure must make room."""
    fig = _drawn(_ctx(), width_in)
    renderer = fig.canvas.get_renderer()
    for upper, lower in zip(fig.axes, fig.axes[1:]):
        labels = [t for t in upper.get_xticklabels() if t.get_text()]
        boxes = [t.get_window_extent(renderer) for t in labels]
        assert len(boxes) == 4 and {t.get_rotation() for t in labels} == {0.0}
        for a, b in zip(boxes, boxes[1:]):
            assert a.x1 <= b.x0 + 1.5, "neighbouring ticks overlap"
        below = lower.title.get_window_extent(renderer)
        assert min(b.y0 for b in boxes) >= below.y1 - 1.5, "ticks run into the next panel's title"
    plt.close(fig)


def test_one_line_ticks_get_the_fixed_margins_back():
    """Tight layout only while a tick runs to more lines than the margins hold;
    a later redraw with one-line ticks returns to the bar figure's own margins."""
    from review.features.barplot import renderer as R

    ctx = _ctx()
    fig = _drawn(ctx, 9.0)
    assert fig._aw_tight_layout and fig.get_layout_engine() is not None
    ctx.state.plot.fc_vs_control_on = False
    ctx.state.feature_slice("bar").rel_variable_id = ""
    _drawn(ctx, 9.0)
    assert not fig._aw_tight_layout and fig.get_layout_engine() is None
    sp = fig.subplotpars
    assert {k: round(getattr(sp, k), 6) for k in R.BAR_FIGURE_MARGINS} == R.BAR_FIGURE_MARGINS
    plt.close(fig)
