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
