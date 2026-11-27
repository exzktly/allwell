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
