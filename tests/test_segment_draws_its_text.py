"""A segment given text and no icon draws its text (``widgets.segmented_control``).

QToolButton's default style is icon-only, which the segment's own ``paintEvent`` reads as "no text", so wherever no
style sheet set the style — the inspector's Data / Display / Style / Detail tabs — the buttons were drawn blank.
Measured by pixels: the button's picture with its text against the same button with none.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_a_text_segment_paints_its_text(qtbot):
    from PySide6.QtWidgets import QApplication

    from ui import theme
    
    app = QApplication.instance()
    before = app.styleSheet()
    app.setStyleSheet(theme.qss())                  # the app's sheet: without it the platform style drew the text

    from widgets.inspector import Inspector

    worded, blank = Inspector(), Inspector()
    worded.setTabs([("data", "Data"), ("display", "Display")])
    blank.setTabs([("data", ""), ("display", "")])
    for host in (worded, blank):
        qtbot.addWidget(host)
        host.resize(320, 120)
        host.show()
    qtbot.waitExposed(worded)
    worded, blank = worded._tab_bar, blank._tab_bar
    try:
        assert worded.grab().toImage() != blank.grab().toImage(), "the segment's text was not drawn"
    finally:
        app.setStyleSheet(before)                   # app-wide: left applied, it changes later tests' layout
