"""The button beside Analyze's input Browse that offers a queued job's output.

``analyze.run_spec.offer_queued_outputs`` adds it when the Job Queue injects the
offer (``datasets.tab._offer_queued_outputs``). Bare ``QApplication``, never the
full-app fixtures (CLAUDE.md §2).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory, QToolButton  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _tab_with_offer(provider=lambda: [], choose=lambda _k: None):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    tab.offer_queued_outputs("Output of a queued job", provider, choose,
                             "No queued job writes a folder", "tip")
    button = tab.findChild(QToolButton, "QueuedOutputButton")
    assert button is not None
    return tab, button


def _render_button(style_name: str) -> QImage:
    from ui import theme

    tab, button = _tab_with_offer()
    button.setStyle(QStyleFactory.create(style_name))
    button.setStyleSheet(theme.qss())
    image = button.grab().toImage()
    tab.deleteLater()
    return image


def test_the_queued_output_button_draws_one_arrow_and_no_platform_part(qapp):
    """One caret, the theme's, and nothing the platform style paints.

    The button used to carry a "▾" label as well as Qt's own menu indicator,
    which the platform style drew beside it: two arrows of different sizes.
    The same button over two base styles must render pixel-identical, which
    holds only when the stylesheet draws every part of it.
    """
    tab, button = _tab_with_offer()
    assert button.text() == ""
    assert button.property("hasMenu") is True
    tab.deleteLater()

    fusion = _render_button("Fusion")
    windows = _render_button("Windows")
    assert fusion.size() == windows.size()
    assert fusion == windows


def test_the_queued_output_button_is_level_with_browse(qapp):
    tab, button = _tab_with_offer()
    side = tab._browse_btn.sizeHint().height()
    assert (button.width(), button.height()) == (side, side)
    tab.deleteLater()


def test_picking_a_queued_job_calls_choose_with_its_key(qapp):
    """The menu does something: opening the submenu lists the provider's jobs,
    and triggering one hands its key to *choose*."""
    chosen: list[str] = []
    tab, button = _tab_with_offer(
        provider=lambda: [("job-1", "Concatenate → /out/a"), ("job-2", "Subset → /out/b")],
        choose=chosen.append)
    sub = button.menu().actions()[0].menu()
    sub.aboutToShow.emit()
    labels = [a.text() for a in sub.actions()]
    assert labels == ["Concatenate → /out/a", "Subset → /out/b"]
    sub.actions()[1].trigger()
    assert chosen == ["job-2"]
    tab.deleteLater()


def test_an_empty_queue_shows_the_empty_text_disabled(qapp):
    tab, button = _tab_with_offer(provider=lambda: [])
    sub = button.menu().actions()[0].menu()
    sub.aboutToShow.emit()
    [only] = sub.actions()
    assert (only.text(), only.isEnabled()) == ("No queued job writes a folder", False)
    tab.deleteLater()
