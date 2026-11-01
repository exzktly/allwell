"""Phone layout's two-column rail keeps each group under its caption.

``RailNav.setColumns(2)`` lays the rows out as tiles. A caption takes a grid row
of its own across both columns, and its group's tiles start from the first
column under it, so a group never shares a line with the one before. (UI lane:
this is layout.)
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QGridLayout, QVBoxLayout

from widgets.rail_nav import RailNav


def _nav(qtbot):
    nav = RailNav()
    qtbot.addWidget(nav)
    nav.addItem("Home")
    nav.addCaption("Analysis")
    for k in ("Plotting", "Statistics", "Spatial"):
        nav.addItem(k)
    nav.addCaption("Data")
    for k in ("Review CSV", "Saved Plots"):
        nav.addItem(k)
    return nav


def test_captions_span_both_columns_and_groups_start_a_fresh_line(qtbot):
    nav = _nav(qtbot)
    nav.setColumns(2)
    grid = nav.layout()
    assert isinstance(grid, QGridLayout)

    def at(r, c):
        item = grid.itemAtPosition(r, c)
        return item.widget() if item is not None else None

    rows = nav._rows
    assert at(0, 0) is rows["Home"] and at(0, 1) is None
    assert at(1, 0).objectName() == "RailNavCaption" and at(1, 0) is at(1, 1)
    assert (at(2, 0), at(2, 1)) == (rows["Plotting"], rows["Statistics"])
    assert at(3, 0) is rows["Spatial"] and at(3, 1) is None
    assert at(4, 0).objectName() == "RailNavCaption" and at(4, 0) is at(4, 1)
    assert (at(5, 0), at(5, 1)) == (rows["Review CSV"], rows["Saved Plots"])


def test_a_hidden_tile_leaves_no_hole_inside_its_group(qtbot):
    nav = _nav(qtbot)
    nav._rows["Plotting"].hide()
    nav.setColumns(2)
    grid = nav.layout()
    assert grid.itemAtPosition(2, 0).widget() is nav._rows["Statistics"]
    assert grid.itemAtPosition(2, 1).widget() is nav._rows["Spatial"]


def test_back_to_one_column_keeps_the_order(qtbot):
    nav = _nav(qtbot)
    nav.setColumns(2)
    nav.setColumns(1)
    lay = nav.layout()
    assert isinstance(lay, QVBoxLayout)
    names = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        names.append(w._group_text if w.objectName() == "RailNavCaption" else w.key())
    assert names == ["Home", "Analysis", "Plotting", "Statistics", "Spatial",
                     "Data", "Review CSV", "Saved Plots"]
