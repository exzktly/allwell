"""Every plot card thins its crowded tick labels as it draws (``review.core.figure_fit``, tracker row S31).

The passes were written for the Nanowells page; ``ui_helpers.apply_plot_card_decorators`` — which every plot tab calls
on its card — now installs them on any card, so a Bar Plot's "10 11 12" on a phone is thinned the same way.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


def test_a_card_given_the_decorators_thins_its_crowded_tick_labels(qtbot):
    from review.core.ui_helpers import apply_plot_card_decorators
    from widgets.plot_card import PlotCard

    card = PlotCard(None, figsize=(2.0, 2.0), constrained=False, header=False)
    qtbot.addWidget(card)
    ctx = SimpleNamespace(features=SimpleNamespace(with_hook=lambda _name: []))
    apply_plot_card_decorators(ctx, card, "Bar Plots")
    ax = card.figure.add_subplot(1, 1, 1)
    ax.set_xlim(0, 30)
    ax.set_xticks(range(31))
    card.canvas.draw()
    shown = [t for t in ax.xaxis.get_major_ticks() if t.label1.get_visible()]
    assert 1 < len(shown) < 31


def test_a_figure_whose_passes_never_settle_stops_asking_for_draws(qtbot, monkeypatch):
    """A pass that changes what the next draw measures could ask for draws for ever: an Image Browser page in the tour
    never finished drawing. The run of redraws asked for is cut."""
    from review.core import figure_fit
    from widgets.plot_card import PlotCard

    card = PlotCard(None, figsize=(2.0, 2.0), constrained=False, header=False)
    qtbot.addWidget(card)
    monkeypatch.setattr(figure_fit, "thin_crowded_ticks", lambda fig, renderer: True)     # never settles
    asked = []
    monkeypatch.setattr(card.canvas, "draw_idle", lambda: asked.append(1))
    figure_fit.keep_ticks_apart(card.canvas)
    for _ in range(10):
        card.canvas.draw()
    assert len(asked) == figure_fit._MAX_ROUNDS


def _agg(w_in: float, h_in: float):
    import matplotlib

    matplotlib.use("agg")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(w_in, h_in), dpi=100)
    FigureCanvasAgg(fig)
    return fig


def test_a_title_too_wide_is_made_smaller_then_broken_and_every_word_kept():
    from review.core import figure_fit

    fig = _agg(2.0, 2.0)
    ax = fig.add_subplot(1, 1, 1)
    title = "GFP Mean Intensity (above threshold) ± SEM across replicate wells"
    ax.set_title(title, fontsize=14)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    assert figure_fit.fit_text_inside(ax.title, fig, r)
    box = ax.title.get_window_extent(r)
    assert box.x0 >= -1 and box.x1 <= fig.bbox.width + 1
    assert ax.title.get_text().split() == title.split()
    assert not figure_fit.fit_text_inside(ax.title, fig, r), "a second fit changed what the first settled"


def test_a_key_over_a_label_moves_to_where_it_covers_none():
    from review.core import figure_fit

    fig = _agg(3.0, 2.0)
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([0, 1], [0, 1], label="A01")
    ax.text(0.95, 0.95, "a note in the corner", transform=ax.transAxes, ha="right", va="top")
    legend = ax.legend(loc="upper right")
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    assert figure_fit.keep_keys_off_labels(fig, r)
    note = ax.texts[0].get_window_extent(r)
    assert not figure_fit._overlaps(legend.get_window_extent(r), note)


def test_rows_of_panels_open_apart_when_a_label_runs_into_the_title_below():
    from review.core import figure_fit

    fig = _agg(3.0, 3.0)
    top, bottom = fig.add_subplot(2, 1, 1), fig.add_subplot(2, 1, 2)
    fig.subplots_adjust(hspace=0.1)                # close enough that the label passes the title, onto the panel
    top.set_xlabel("Time (hours)", fontsize=12)
    bottom.set_title("Events above threshold", fontsize=12)
    fig.canvas.draw()
    before = fig.subplotpars.hspace
    assert figure_fit.keep_rows_apart(fig, fig.canvas.get_renderer())
    assert fig.subplotpars.hspace > before


def test_a_title_a_little_too_wide_is_only_made_smaller():
    from review.core import figure_fit

    probe = _agg(10.0, 2.0)
    wide = probe.text(0, 0, "Fraction of cells above threshold", fontsize=14)
    probe.canvas.draw()
    width_in = wide.get_window_extent(probe.canvas.get_renderer()).width / 100.0
    fig = _agg(width_in / 1.2, 2.0)                # the title a fifth wider than the figure
    ax = fig.add_axes((0, 0.1, 1, 0.7))
    ax.set_title("Fraction of cells above threshold", fontsize=14, loc="left")
    fig.canvas.draw()
    assert figure_fit.fit_text_inside(ax._left_title, fig, fig.canvas.get_renderer())
    assert "\n" not in ax._left_title.get_text() and ax._left_title.get_fontsize() < 14


def test_crowded_cell_values_are_made_smaller_together():
    from review.core import figure_fit

    fig = _agg(1.5, 1.5)
    ax = fig.add_axes((0.1, 0.1, 0.8, 0.8))
    for i in range(4):
        for j in range(4):
            ax.text(i / 4 + 0.125, j / 4 + 0.125, "1.00", fontsize=14, ha="center", va="center", transform=ax.transAxes)
    fig.canvas.draw()
    assert figure_fit.keep_values_apart(fig, fig.canvas.get_renderer())
    sizes = {t.get_fontsize() for t in ax.texts}
    assert len(sizes) == 1 and sizes.pop() < 14


def test_a_key_with_nowhere_clear_stays_where_its_renderer_put_it():
    from review.core import figure_fit

    fig = _agg(1.2, 1.2)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.plot([0, 1], [0, 1], label="A long key entry that covers everything")
    # A label under every place a key can go, and two under the last place tried: nowhere is clear, and the last
    # place is worse than where the key started.
    for x, y in ((0.15, 0.93), (0.85, 0.93), (0.15, 0.07), (0.85, 0.07), (0.85, 0.5), (0.15, 0.5), (0.5, 0.93),
                 (0.4, 0.07), (0.6, 0.07)):
        ax.text(x, y, "label", transform=ax.transAxes, ha="center", va="center")
    legend = ax.legend(loc="upper left")
    fig.canvas.draw()
    before = legend._loc
    figure_fit.keep_keys_off_labels(fig, fig.canvas.get_renderer())
    r = fig.canvas.get_renderer()
    box = legend.get_window_extent(r)
    covered = sum(figure_fit._overlaps(box, t.get_window_extent(r)) for t in ax.texts)
    legend._loc = before
    fig.canvas.draw()
    box0 = legend.get_window_extent(r)
    assert covered <= sum(figure_fit._overlaps(box0, t.get_window_extent(r)) for t in ax.texts)
