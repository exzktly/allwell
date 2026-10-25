"""Only the selected rail entry is drawn at the active text colour.

Reported: "the first entry in the left sidebar under Review and Datasets is a
different color than the rest of the entries." It was. ``addItem`` makes the
first row active, and ``setActive`` repolished the row, its body and its strip
but not its label, whose colour came from a descendant rule on the row's
``active`` property — resolved only when the label itself is polished. So the
first label kept the active colour whatever was selected, and the label of the
row actually selected never took it. Measured before the fix, with Statistics
selected: ``['#e6e9ef', '#98a2b3', '#98a2b3']``.

Asserted on the colour each label is drawn in, against the two theme tokens,
for every row after each selection — so a fix that brightened the selected row
but left the first one bright too would still fail.
"""

from __future__ import annotations

from PySide6.QtGui import QPalette

from ui import theme
from widgets.rail_nav import RailNav

_KEYS = ("Home", "Plotting", "Statistics")


def _label_colours(nav) -> list[str]:
    return [nav._rows[k]._label.palette().color(QPalette.WindowText).name().lower()
            for k in nav.items()]


def _expected(selected: str) -> list[str]:
    return [(theme.Colors.text_primary if k == selected else theme.Colors.text_secondary).lower()
            for k in _KEYS]


def test_only_the_selected_entry_is_bright(qtbot):
    nav = RailNav()
    qtbot.addWidget(nav)
    for key in _KEYS:
        nav.addItem(key)
    nav.show()
    qtbot.waitExposed(nav)

    assert _label_colours(nav) == _expected("Home"), "premise: the first row starts selected"
    for key in ("Statistics", "Plotting", "Home"):
        nav.setCurrentKey(key)
        assert _label_colours(nav) == _expected(key), f"after selecting {key}"


def test_a_click_moves_the_colour_too(qtbot):
    """Through the row's own click, the way a user selects an entry."""
    from PySide6.QtCore import Qt

    nav = RailNav()
    qtbot.addWidget(nav)
    for key in _KEYS:
        nav.addItem(key)
    nav.show()
    qtbot.waitExposed(nav)
    qtbot.mouseClick(nav._rows["Statistics"], Qt.LeftButton)
    assert _label_colours(nav) == _expected("Statistics")
