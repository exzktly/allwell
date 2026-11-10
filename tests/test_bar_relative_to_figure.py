"""What *Then relative to* puts on the Bar figure: the bars drawn, the line, the
p labels, the note, the axis label, and the brackets it withholds.

Drives the real ``draw_grouped_bar_mode`` on Agg axes over the reported design
(``tests/test_bar_relative_to.py``), so what is asserted is what a saved figure
carries — not the collector's return value.
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from review.features.barplot import renderer as r  # noqa: E402
from tests.test_bar_relative_to import _app, _on  # noqa: E402


def _draw(ctx, *, show_significance=False):
    fig = plt.figure()
    ax_mean, ax_frac, ax_n = (fig.add_subplot(3, 1, i) for i in (1, 2, 3))
    ctx.runtime.barplot.fig = fig
    ctx.runtime.barplot.canvas = SimpleNamespace(draw_idle=lambda: None)
    ctx.state.plot.show_significance = show_significance
    status: list[str] = []
    ctx.notify = SimpleNamespace(set_status=lambda m, *a, **k: status.append(m))
    r.draw_grouped_bar_mode(ctx, ax_mean=ax_mean, ax_frac=ax_frac, ax_n=ax_n,
                            active_rsets=[], target_ts=[0.0], tp_str="0",
                            threshold=0.0, band_lbl="SEM", use_sem=True)
    return fig, ax_mean, ax_frac, ax_n


def _texts(ax):
    return [t.get_text() for t in ax.texts]


def test_only_the_compared_bar_is_drawn_and_the_references_are_a_dotted_line():
    ctx, ids = _app()
    _on(ctx, ids)
    fig, ax_mean, ax_frac, ax_n = _draw(ctx)
    heights = [p.get_height() for p in ax_mean.patches if p.get_height() == p.get_height()]
    assert heights == pytest.approx([1.5])
    for ax in (ax_mean, ax_frac, ax_n):
        dotted = [ln for ln in ax.lines if ln.get_linestyle() == ":"
                  and list(ln.get_ydata()) == [1.0, 1.0]]
        assert dotted, ax
    plt.close(fig)


def test_the_tested_bar_carries_its_adjusted_p_and_the_note_names_the_test():
    ctx, ids = _app()
    _on(ctx, ids)
    fig, ax_mean, _f, _n = _draw(ctx)
    texts = _texts(ax_mean)
    assert any(t.startswith("p_BH") for t in texts), texts
    assert any(t.startswith("Relative to Treatment = Vehicle within each RNA.")
               for t in texts), texts
    assert "÷ (Treatment = Vehicle)" in ax_mean.get_ylabel()
    plt.close(fig)


def test_the_stored_brackets_are_withheld_and_say_why():
    ctx, ids = _app()
    _on(ctx, ids)
    fig, ax_mean, _f, _n = _draw(ctx, show_significance=True)
    assert any("brackets withheld" in t for t in _texts(ax_mean))
    plt.close(fig)


def test_a_declined_rule_adds_no_label_and_draws_stage_one_with_its_reason():
    ctx, ids = _app()
    _on(ctx, ids)
    ctx.state.plot.fc_vs_t0_on = True
    fig, ax_mean, _f, _n = _draw(ctx)
    assert "÷ (Treatment" not in ax_mean.get_ylabel()
    assert any("Baseline (vs t0) is set" in t for t in _texts(ax_mean))
    assert not [ln for ln in ax_mean.lines if ln.get_linestyle() == ":"]
    plt.close(fig)


def test_a_per_cell_mode_says_it_is_not_applied():
    """Violin / swarm / all points never reach the collector's stage 2."""
    ctx, ids = _app()
    bar = _on(ctx, ids)
    bar.rel_applied = True               # left over from the last bar redraw
    fig, ax = plt.subplots()
    r._decline_relative_for_cells(ctx, ax)
    assert bar.rel_applied is False
    assert any("not applied" in t and "draws each column's cells" in t for t in _texts(ax))
    plt.close(fig)


def test_split_and_then_by_headers_survive_a_single_remaining_bar():
    """The reported failure: on a 2×2, *Then relative to* leaves one bar, the
    layout inserts no gap, and both header rows used to vanish with it."""
    ctx, ids = _app()
    _on(ctx, ids)
    ctx.state.plot.split_by_variable = ids["treat"]
    ctx.state.plot.sub_split_by_variable = ids["rna"]
    fig, ax_mean, _f, _n = _draw(ctx)
    texts = _texts(ax_mean)
    assert "Drug" in texts and "Mut" in texts, texts
    plt.close(fig)


def test_without_split_by_a_lone_bar_still_has_no_header():
    """The rule the gap test existed for: no band, no redundant bold label."""
    ctx, ids = _app()
    _on(ctx, ids)
    fig, ax_mean, _f, _n = _draw(ctx)
    assert "Drug" not in _texts(ax_mean) and "Mut" not in _texts(ax_mean)
    plt.close(fig)


def test_the_tick_label_names_both_layers_of_normalisation():
    """Reported: with two layers the tick named only the first. One bar is left,
    so one control is in play and the 'name controls only when they differ'
    rule printed neither layer."""
    ctx, ids = _app()
    _on(ctx, ids)
    fig, ax_mean, _f, _n = _draw(ctx)
    ticks = [t.get_text() for t in ax_mean.get_xticklabels()]
    assert [t.replace("\u00a0", " ") for t in ticks] == ["Mut-drug\n÷ WT-drug ÷ Vehicle"], ticks
    plt.close(fig)
