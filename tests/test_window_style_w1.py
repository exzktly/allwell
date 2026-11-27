"""The window in style W1, "Panels" (design language §3.1, row D20), read off the real window.

The header, the mode band and the sidebar sit on the window's ground (``Colors.frame``) with no dividing line; the
page is a rounded panel in the canvas colour with an 8-pixel gap to the sidebar and to the window's right and
bottom edges; the mode switch's current segment is in the selected look; the sidebar's selected row carries no
accent bar. Builds the whole window (``allwell.AllWellApp``), so it is in the slow lane:
``scripts/test.sh tests/test_window_style_w1.py -m ""``.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.slow


@pytest.fixture
def window(qapp):
    from tests._page_render_case import start_window
    win = start_window(qapp)
    win.resize(1400, 900)
    win.show()
    for _ in range(10):
        QApplication.processEvents()
    yield win
    # Closed and deleted, not only closed: a window left alive answers the next test's ReviewApp (its signals
    # reach a live shell), and the closed-app test then counts what that shell built as a leak.
    win.close()
    win.deleteLater()
    for _ in range(5):
        QApplication.processEvents()


def _pixel(widget, x: int, y: int) -> str:
    return widget.window().grab().toImage().pixelColor(widget.mapTo(widget.window(), QPoint(x, y))).name()


def test_the_shell_sits_on_the_frame_and_the_page_is_a_rounded_panel(window):
    from ui import shell_frame, theme

    review = window._review
    rail, split, page = review._sidebar_root, review._h_pane, review._central_pane_stack
    header = window._header
    # The regions on the frame, read where nothing is drawn over them: the rail's foot, the header's middle.
    assert _pixel(rail, rail.width() // 2, rail.height() - 3) == theme.Colors.frame.lower()
    assert _pixel(header, header.width() // 2 + 40, 3) == theme.Colors.frame.lower()
    # The page: the gap round it, and its corner clipped to the frame behind it.
    assert split.handleWidth() == shell_frame.PANEL_GAP
    assert split.contentsMargins().right() == split.contentsMargins().bottom() == shell_frame.PANEL_GAP
    assert not page.mask().isEmpty()
    assert not page.mask().contains(QPoint(0, 0)) and page.mask().contains(QPoint(page.width() // 2, 20))


def test_the_mode_switch_and_the_rail_mark_the_current_one_with_the_selected_fill(window):
    from PySide6.QtWidgets import QWidget

    segments = window._mode_seg.findChildren(QWidget, "ModeSegment")
    assert [s.property("active") for s in segments] == [True, False, False]
    nav = window._review._section_nav
    row = nav._rows[nav.currentKey()]
    strip = row._strip
    # No accent bar: the strip a bar was drawn in is left empty, so the frame shows through it, and the body
    # carries the selected fill.
    from ui import theme
    assert _pixel(strip, strip.width() // 2, strip.height() // 2) == theme.Colors.frame.lower()
    assert row.findChild(QWidget, "RailNavRowBody").property("active") is True


def test_one_top_bar_and_the_page_tabs_head_the_page(window):
    """The mock-up's structure: the mode switch and the ‹ › arrows beside the name in the header, the band they sat
    in for phone layout hidden at the desk, and the page tabs the first row of the page panel."""
    review = window._review
    header = window._header
    assert header.isAncestorOf(window._mode_seg)
    assert header.isAncestorOf(window._history_back_btn) and header.isAncestorOf(window._history_forward_btn)
    assert review._top_bar.isHidden()
    assert review._central_pane_stack.isAncestorOf(review._page_tabs_host)


def test_phone_layout_takes_the_switch_and_arrows_back_into_its_bar(window):
    from core import phone_layout
    review = window._review
    phone_layout.set_on(True)
    try:
        QApplication.processEvents()
        band = review._top_bar
        assert not band.isHidden()
        assert band.isAncestorOf(window._mode_seg) and band.isAncestorOf(window._history_back_btn)
    finally:
        phone_layout.set_on(False)
    QApplication.processEvents()
    assert window._header.isAncestorOf(window._mode_seg) and review._top_bar.isHidden()
