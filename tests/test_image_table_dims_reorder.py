"""Image Table Rows/Columns dimension picker — include + reorder behaviour.

The picker is a frameless popup hosting a drag-reorderable ``QListWidget``: ticking
a dimension includes it on the axis, and the checked items' top→bottom order sets
the nesting (outer › inner). Order is editable by dragging (not simulable
headless) or the ▲/▼ buttons, which share the same commit path as a drag. These
checks drive the real tab (``image_table_app`` fixture) through the list + the
Move-up button and assert ``row_dims`` tracks the list order.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QListWidget  # noqa: E402

from widgets.icon_button import IconButton  # noqa: E402


def _open_row_picker(app):
    btn = app._image_table_row_dims_btn
    app._app_state.image_table.row_dims = []
    app._app_state.image_table.row_axis = "channel"
    btn.click()  # _open_popup → rebuilds the list from state
    listw = btn.findChild(QListWidget)
    assert listw is not None and listw.count() == 4
    return btn, listw


def _item(listw, key):
    for i in range(listw.count()):
        if listw.item(i).data(Qt.UserRole) == key:
            return i, listw.item(i)
    raise AssertionError(f"no list item for {key}")


def test_checking_dims_sets_row_dims_in_list_order(image_table_app):
    app = image_table_app
    _btn, listw = _open_row_picker(app)
    # Fresh popup lists dims in canonical order: channel, well, timepoint, fov.
    _item(listw, "well")[1].setCheckState(Qt.Checked)
    _item(listw, "channel")[1].setCheckState(Qt.Checked)
    # Both checked → row_dims = checked dims in their top→bottom list order
    # (channel sits above well), independent of which was ticked first.
    assert app._app_state.image_table.row_dims == ["channel", "well"]


def test_move_up_button_reorders_nesting(image_table_app):
    app = image_table_app
    btn, listw = _open_row_picker(app)
    _item(listw, "well")[1].setCheckState(Qt.Checked)
    _item(listw, "channel")[1].setCheckState(Qt.Checked)
    assert app._app_state.image_table.row_dims == ["channel", "well"]

    # Select the Wells row and click Move up → Wells becomes the outer dim.
    well_row, _ = _item(listw, "well")
    listw.setCurrentRow(well_row)
    popup = btn.findChild(QListWidget).parent()
    ups = [b for b in popup.findChildren(IconButton)
           if b.toolTip().startswith("Move up")]
    assert ups, "the popup should carry a Move-up button"
    ups[0].click()

    assert app._app_state.image_table.row_dims == ["well", "channel"]
    # legacy outer-dim mirror follows dims[0]
    assert app._app_state.image_table.row_axis == "well"
    assert "Wells › Channels" in btn.text()
