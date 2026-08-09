"""A rail nav is a list of links, not a region to fill.

The reported symptom: navigating off Review's Home page and back made the
sidebar's page links "spread out to fill the entire left sidebar". Measured on
the real window, the rows went from 33px to 81px and the nav from 373px to 900.

The mechanism is not Home's. Review's rail column lives in a
``setWidgetResizable(True)`` scroll area, so it is always stretched to the
viewport, and the surplus is normally absorbed by whichever per-page frame is
visible beneath the nav. ``on_tab_change`` hides all of them on every page
change and each feature's activation hook re-shows one — except Home's, which
deliberately shows none, since Home carries its own plate. With every stretch-1
sibling hidden, the nav was the only growable item left in the column.

So the guard is on the widget, not on the page: a ``RailNav`` given more height
than it needs must not take it, whatever its host is doing. Fixing it in one
host would leave the next host to rediscover it — and there are two hosts
already, Review's rail and the Datasets pane's, which had compensated
separately.

Fast lane: a bare widget, no application.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from widgets.rail_nav import RailNav

_TALL = 900


def _nav_in_a_tall_host(parent_height: int = _TALL) -> tuple[QWidget, RailNav]:
    """A nav with nothing else in the column that could absorb slack.

    This is Review's rail on Home, reduced to the part that matters: a vertical
    layout taller than its content, and no sibling with a stretch factor.
    """
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    nav = RailNav(host)
    for name in ("Home", "Plotting", "Images", "Data"):
        nav.addItem(name)
    layout.addWidget(nav)
    host.resize(320, parent_height)
    host.show()
    return host, nav


def test_a_nav_with_room_to_spare_does_not_take_it(qtbot):
    host, nav = _nav_in_a_tall_host()
    qtbot.addWidget(host)

    assert host.height() > nav.sizeHint().height(), (
        "premise: the host must be taller than the nav needs, or nothing is "
        "being measured")
    assert nav.height() == nav.sizeHint().height(), (
        f"the nav grew to {nav.height()}px against a hint of "
        f"{nav.sizeHint().height()}px — its rows are spreading to fill the rail")


def test_the_rows_keep_their_own_height(qtbot):
    """The visible symptom, stated as the user saw it: the links spread apart."""
    host, nav = _nav_in_a_tall_host()
    qtbot.addWidget(host)

    layout = nav.layout()
    rows = [layout.itemAt(i).widget() for i in range(layout.count())]
    rows = [r for r in rows if r is not None]
    assert len(rows) == 4, "premise: four links"
    heights = {r.height() for r in rows}
    assert len(heights) <= 2, (
        f"rows took different heights ({sorted(heights)}) — surplus is being "
        "divided among them")
    assert max(heights) <= max(r.sizeHint().height() for r in rows) + 1, (
        f"rows grew past their size hint: {sorted(heights)}")


def test_a_nav_still_gets_the_height_it_asks_for(qtbot):
    """The other half of the rule — refusing to grow must not mean shrinking.

    A ``Maximum`` vertical policy caps the widget at its hint; a host shorter
    than the hint must still be able to give it what it can, and the rows must
    not collapse to nothing.
    """
    host, nav = _nav_in_a_tall_host(parent_height=40)
    qtbot.addWidget(host)

    layout = nav.layout()
    rows = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert all(r.height() > 0 for r in rows if r is not None)
