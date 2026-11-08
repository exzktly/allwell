"""A capture keeps the plot's font sizes, in the saved image and on screen afterwards.

Reported: on the Bar tab, Save to Saved Plots reset the graph's font sizes. Every capture (Save to Saved Plots, Save
figure, Copy) flips the card to the publication theme and back around its ``savefig`` (``_publication_capture``), and
the theme pass set every tick label to the theme's caption size — overwriting the size Properties → Axes had set, in
the saved image and on the screen after it, until the tab next redrew. The Line tab hid it because its capture path
redraws around the save; Bar does not.

Measured through the real ``PlotCard`` capture, at the ``savefig`` the saved image is drawn from and after it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _card(qtbot):
    from widgets.plot_card import PlotCard

    card = PlotCard()
    qtbot.addWidget(card)
    ax = card.figure.add_subplot(1, 1, 1)
    ax.bar([0, 1, 2], [3, 1, 2])
    ax.set_xlabel("Well")
    ax.set_title("Mean")
    ax.tick_params(labelsize=18)            # what Properties → Tick label size set
    return card, ax


def _tick_sizes(ax) -> set[float]:
    return {t.get_fontsize() for t in ax.get_xticklabels() + ax.get_yticklabels()}


@pytest.mark.qt
def test_save_to_saved_plots_keeps_the_tick_label_size(qtbot):
    card, ax = _card(qtbot)
    at_capture: list[set[float]] = []
    real = card.figure.savefig

    def _spy(*a, **k):
        at_capture.append(_tick_sizes(ax))
        return real(*a, **k)

    card.figure.savefig = _spy
    card._save_handler = lambda *a: "Saved plot"
    card.saveFigureToGallery()
    assert at_capture and at_capture[0] == {18.0}, "the saved image must carry the size the user chose"
    assert _tick_sizes(ax) == {18.0}, "and the plot on screen must keep it after the save"


@pytest.mark.qt
def test_a_theme_switch_recolours_without_resizing(qtbot):
    card, ax = _card(qtbot)
    card.setPlotTheme("publication")
    assert _tick_sizes(ax) == {18.0}
    card.setPlotTheme("screen")
    assert _tick_sizes(ax) == {18.0}
