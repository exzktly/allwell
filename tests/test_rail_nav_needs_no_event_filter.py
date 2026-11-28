"""The rail's group captions follow their rows without an event filter (``widgets.rail_nav``).

RailNav installed itself as an event filter on every row to notice a row shown or hidden, so Python ran for every
event a row received — each touch, hover and paint. In phone layout on Windows 365 (an iPhone Duo, unfolded), a tap on
☰ crashed the app with an access violation on the filter's first line, called from the event loop. Not reproduced
off Windows; the rows now say when they are shown or hidden, and nothing filters their events.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _nav(qtbot):
    from PySide6.QtWidgets import QWidget

    from widgets.rail_nav import RailNav

    host = QWidget()
    qtbot.addWidget(host)
    nav = RailNav(host)
    nav.addItem("Home")
    caption = nav.addCaption("ASSAYS")
    rows = [nav.addItem("Nanowells"), nav.addItem("smFISH")]
    host.show()
    return host, nav, caption, rows                  # the host kept alive by the test, or the rail goes with it


def test_a_caption_hides_with_the_last_row_of_its_group_and_returns_with_one(qtbot):
    _host, nav, caption, (nanowells, smfish) = _nav(qtbot)
    assert not caption.isHidden()
    nanowells.hide()
    assert not caption.isHidden()                    # smFISH still under it
    smfish.setVisible(False)
    assert caption.isHidden()
    nanowells.show()
    assert not caption.isHidden()


def test_the_same_holds_in_the_phone_s_two_column_grid(qtbot):
    _host, nav, caption, (nanowells, smfish) = _nav(qtbot)
    nav.setColumns(2)
    nanowells.hide()
    smfish.hide()
    assert caption.isHidden()
    smfish.show()
    assert not caption.isHidden()


def test_the_rail_filters_no_events_of_its_rows():
    from PySide6.QtWidgets import QWidget

    from widgets.rail_nav import RailNav

    assert RailNav.eventFilter is QWidget.eventFilter, "RailNav overrides eventFilter again"
