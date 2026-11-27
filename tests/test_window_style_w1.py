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
    win.close()


def _pixel(widget, x: int, y: int) -> str:
    return widget.window().grab().toImage().pixelColor(widget.mapTo(widget.window(), QPoint(x, y))).name()


def test_the_shell_sits_on_the_frame_and_the_page_is_a_rounded_panel(window):
    from ui import shell_frame, theme

    review = window._review
    rail, band, split, page = review._sidebar_root, review._top_bar, review._h_pane, review._central_pane_stack
    # The regions on the frame, read where nothing is drawn over them: the rail's foot, the band's far right.
    assert _pixel(rail, rail.width() // 2, rail.height() - 3) == theme.Colors.frame.lower()
    assert _pixel(band, band.width() - 3, band.height() // 2) == theme.Colors.frame.lower()
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
