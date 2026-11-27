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


def test_values_too_crowded_to_read_are_hidden_and_the_panel_says_so_and_they_return():
    from review.core import figure_fit

    fig = _agg(0.6, 0.6)
    ax = fig.add_axes((0.1, 0.1, 0.8, 0.8))
    for i in range(5):
        for j in range(5):
            ax.text(i / 5 + 0.1, j / 5 + 0.1, "1.00", fontsize=12, ha="center", va="center", transform=ax.transAxes)
    values = list(ax.texts)
    fig.canvas.draw()
    assert figure_fit.keep_values_apart(fig, fig.canvas.get_renderer())
    assert not any(t.get_visible() for t in values)
    assert [t.get_gid() for t in ax.texts].count(figure_fit.HIDDEN_GID) == 1
    fig.set_size_inches(8.0, 8.0)                   # large enough again
    fig.canvas.draw()
    figure_fit.keep_values_apart(fig, fig.canvas.get_renderer())
    assert all(t.get_visible() for t in values)
    assert figure_fit.HIDDEN_GID not in [t.get_gid() for t in ax.texts]


def test_a_y_label_taller_than_its_panel_is_fitted_to_it():
    from review.core import figure_fit

    fig = _agg(4.0, 1.2)
    ax = fig.add_subplot(1, 1, 1)
    ax.set_ylabel("GFP Mean Intensity (log10, +1)", fontsize=12)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    assert figure_fit.fit_label_to_axis(ax, r)
    assert ax.yaxis.label.get_window_extent(r).height <= ax.get_window_extent(r).height + 1
    assert ax.yaxis.label.get_text().split() == "GFP Mean Intensity (log10, +1)".split()


def test_an_x_label_under_the_figure_raises_the_bottom_margin():
    from review.core import figure_fit

    fig = _agg(3.0, 2.0)
    ax = fig.add_subplot(1, 1, 1)
    fig.subplots_adjust(bottom=0.02)
    ax.set_xlabel("GFP mean intensity", fontsize=12)
    fig.canvas.draw()
    assert figure_fit.keep_labels_on_figure(fig, fig.canvas.get_renderer())
    fig.canvas.draw()
    assert ax.xaxis.label.get_window_extent(fig.canvas.get_renderer()).y0 >= -0.5


def test_the_tick_check_pairs_each_tick_with_its_own_label(qtbot):
    """The tour's tick check zipped the ticks with ``get_majorticklabels`` — the shown labels only — so once a label
    was thinned it paired a tick with another's label and reported overlaps that were not drawn."""
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from review.core import figure_fit
    from tests._layout_checks import figure_tick_overlap

    window = QWidget()
    qtbot.addWidget(window)
    canvas = FigureCanvasQTAgg(Figure(figsize=(2.0, 1.0)))
    QVBoxLayout(window).addWidget(canvas)
    window.resize(220, 120)
    window.show()
    ax = canvas.figure.add_subplot(1, 1, 1)
    ax.set_xticks(range(-10, 41))                  # ticks beyond the limits too: matplotlib keeps their labels
    ax.set_xlim(0, 30)
    canvas.draw()
    assert figure_tick_overlap(window), "crowded labels not found"
    figure_fit.thin_crowded_ticks(canvas.figure, canvas.get_renderer())
    assert figure_tick_overlap(window) == []
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()                   # the canvas's pending idle draw, while it is still alive


def test_a_note_along_the_foot_raises_the_margin_above_it():
    from review.core import figure_fit

    fig = _agg(3.0, 2.0)
    ax = fig.add_subplot(1, 1, 1)
    fig.subplots_adjust(bottom=0.15)
    ax.set_xlabel("GFP mean intensity", fontsize=10)
    note = fig.text(0.01, 0.002, "Observations: cells · pooled from 3 nanowells", fontsize=9, va="bottom")
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    assert figure_fit.keep_labels_on_figure(fig, r)
    fig.canvas.draw()
    assert not figure_fit._overlaps(ax.xaxis.label.get_window_extent(r), note.get_window_extent(r))


def test_a_colour_bar_label_past_the_right_edge_is_made_to_fit():
    from review.core import figure_fit

    fig = _agg(3.0, 3.0)
    bar = fig.add_axes((0.87, 0.1, 0.05, 0.8))     # a short label, no longer than the bar, just past the edge
    bar.yaxis.set_label_position("right")
    bar.set_ylabel("Mean intensity", fontsize=20)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    assert figure_fit._off(bar.yaxis.label.get_window_extent(r), fig)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    figure_fit.fit_label_to_axis(bar, r)
    assert not figure_fit._off(bar.yaxis.label.get_window_extent(r), fig)


def test_a_key_anchored_outside_its_axes_moves_off_a_label_it_covers():
    from review.core import figure_fit

    fig = _agg(3.0, 3.0)
    ax = fig.add_axes((0.15, 0.3, 0.8, 0.6))
    ax.plot([0, 1], [0, 1], label="A01")
    ax.set_xlabel("Time (hours)", fontsize=12)
    # Anchored to a point on the x label itself: every place around that point covers it.
    legend = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12))
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    assert figure_fit._overlaps(legend.get_window_extent(r), ax.xaxis.label.get_window_extent(r))
    assert figure_fit.keep_keys_off_labels(fig, r)
    assert not figure_fit._overlaps(legend.get_window_extent(r), ax.xaxis.label.get_window_extent(r))


def test_a_key_too_large_to_clear_a_label_anywhere_is_made_smaller():
    from review.core import figure_fit

    fig = _agg(2.0, 2.0)
    ax = fig.add_axes((0.2, 0.25, 0.7, 0.6))
    for name in ("A01", "A02", "B01", "B02"):
        ax.plot([0, 1], [0, 1], label=name)
    # A label at every place a key can go: at its size the key covers one wherever it is put.
    for x, y in ((0.1, 0.95), (0.9, 0.95), (0.1, 0.05), (0.9, 0.05), (0.9, 0.5), (0.1, 0.5), (0.5, 0.95), (0.5, 0.05)):
        ax.text(x, y, "n", transform=ax.transAxes, ha="center", va="center", fontsize=7)
    legend = ax.legend(loc="upper left", fontsize=13)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()

    def covered() -> int:
        box = legend.get_window_extent(r)
        return sum(figure_fit._overlaps(box, t.get_window_extent(r)) for t in ax.texts)

    before = covered()
    assert before > 0
    figure_fit.keep_keys_off_labels(fig, r)
    assert all(t.get_fontsize() < 13 for t in legend.get_texts())
    assert covered() < before
