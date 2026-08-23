"""Two header controls on ``PlotCard`` that did not hold the state they were set to.

``setThemeToggleVisible(False)`` hid the ``Publication ↔ Screen`` segmented
control and the "preview only" chip once, and then ``_sync_theme_ui`` — which
runs again on every ``setPlotTheme`` call and once more from the card's own
``changeEvent`` — put the chip straight back the first time the card switched
to the screen theme. A card asked to hide its theme UI ended up showing half
of it.

``_open_stats_popover`` built a fresh ``Popover`` on every click and assigned
it over ``self._stats_pop``. The old popover is a child of the card, so
dropping the Python reference did not destroy it: each click left another
hidden popover, with its own segmented controls, alive for the card's lifetime.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from PySide6.QtWidgets import QApplication  # noqa: E402

from widgets.plot_card import PlotCard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def card(qapp):
    c = PlotCard(figsize=(3, 2))
    yield c
    c.deleteLater()


def test_hidden_theme_toggle_stays_hidden_across_a_theme_switch(card):
    # The card starts in screen mode, so ``setPlotTheme("screen")`` below would
    # be a no-op and never reach ``_sync_theme_ui`` — the switch has to be a
    # real one for this to measure anything.
    card.setPlotTheme("publication")
    card.setThemeToggleVisible(False)
    assert not card._preview_chip.isVisibleTo(card)

    card.setPlotTheme("screen")

    assert not card._preview_chip.isVisibleTo(card), (
        "switching to the screen theme put the 'preview only' chip back on a "
        "card that was told to hide its theme UI"
    )
    if getattr(card, "_theme_sc", None) is not None:
        assert not card._theme_sc.isVisibleTo(card)


def test_showing_the_theme_toggle_again_restores_the_preview_chip(card):
    card.setPlotTheme("publication")
    card.setPlotTheme("screen")
    card.setThemeToggleVisible(False)
    assert not card._preview_chip.isVisibleTo(card)

    card.setThemeToggleVisible(True)

    assert card._preview_chip.isVisibleTo(card)


def test_a_capture_does_not_fire_the_theme_signal(card):
    """The signal is wired straight to a full tab recompute, and a capture
    flips the theme twice — so one Copy click ran two complete recomputes, the
    first of them between the flip and the savefig it wrapped."""
    seen: list[str] = []
    card.plotThemeChanged.connect(seen.append)

    card.setPlotTheme("screen")
    seen.clear()

    with card._publication_capture():
        assert card._plot_theme == "publication"
    assert card._plot_theme == "screen", "the capture left the theme flipped"
    assert seen == [], f"the round trip emitted {seen}"


def test_an_ordinary_theme_change_still_fires_the_signal(card):
    seen: list[str] = []
    card.setPlotTheme("screen")
    card.plotThemeChanged.connect(seen.append)
    card.setPlotTheme("publication")
    assert seen == ["publication"]


def test_a_capture_that_raises_still_puts_the_theme_back(card):
    card.setPlotTheme("screen")
    with pytest.raises(RuntimeError):
        with card._publication_capture():
            raise RuntimeError("savefig blew up")
    assert card._plot_theme == "screen"
    assert card._theme_signal_suppressed is False


def test_reopening_the_stats_popover_does_not_stack_popovers(card):
    from widgets.popover import Popover

    from PySide6.QtCore import QEvent

    before = len(card.findChildren(Popover))
    for _ in range(5):
        card._open_stats_popover()
        # ``deleteLater`` is deferred: without draining the queue the discarded
        # popovers are still children and the count looks identical either way.
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    after = len(card.findChildren(Popover))

    assert after - before <= 1, (
        f"five clicks left {after - before} popovers parented to the card"
    )
