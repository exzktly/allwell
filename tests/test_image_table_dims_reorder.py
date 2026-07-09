"""Image Table Rows/Columns dimension picker — include, drag-reorder, exclusion.

The picker is a non-grabbing frameless dropdown hosting a drag-reorderable
``QListWidget``: ticking a dimension includes it on the axis, and the checked
items' top→bottom order sets the nesting (outer › inner). Order is set by dragging
(commit happens in the list's ``dropEvent``); a dimension already on the *other*
axis is greyed-out and un-checkable so it can't live on both. A real mouse drag
isn't drivable headless, so the reorder check moves the item and fires the same
commit hook ``dropEvent`` calls.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QListWidget  # noqa: E402


def _open_row_picker(app, *, row_dims=None, row_axis="channel"):
    st = app._app_state.image_table
    st.row_dims = list(row_dims or [])
    st.row_axis = row_axis
    btn = app._image_table_row_dims_btn
    btn.click()  # _open_popup → rebuilds the list from state
    listw = btn.findChild(QListWidget)
    assert listw is not None and listw.count() == 4
    return btn, listw


def _row_of(listw, key):
    for i in range(listw.count()):
        if listw.item(i).data(Qt.UserRole) == key:
            return i, listw.item(i)
    raise AssertionError(f"no list item for {key}")


def test_checking_dims_sets_row_dims_in_list_order(image_table_app):
    app = image_table_app
    _btn, listw = _open_row_picker(app)
    # Fresh popup shows the current row axis (channel) checked; tick Wells too.
    _row_of(listw, "well")[1].setCheckState(Qt.Checked)
    # Both checked → row_dims = checked dims in top→bottom list order.
    assert app._app_state.image_table.row_dims == ["channel", "well"]


def test_drag_reorder_flips_nesting(image_table_app):
    app = image_table_app
    btn, listw = _open_row_picker(app)
    _row_of(listw, "well")[1].setCheckState(Qt.Checked)
    assert app._app_state.image_table.row_dims == ["channel", "well"]

    # Simulate a drag-drop of Wells above Channels, then fire the same commit hook
    # the list's dropEvent invokes.
    well_row, _ = _row_of(listw, "well")
    it = listw.takeItem(well_row)
    listw.insertItem(0, it)
    listw._on_reordered()

    assert app._app_state.image_table.row_dims == ["well", "channel"]
    assert app._app_state.image_table.row_axis == "well"   # outer-dim mirror follows
    assert "Wells › Channels" in btn.text()


def test_dim_on_other_axis_is_greyed_and_uncheckable(image_table_app):
    app = image_table_app
    # Timepoints live on the Columns axis (the default col axis), so the Rows
    # picker must show it disabled — a dim can't be on both axes.
    app._app_state.image_table.col_dims = ["timepoint"]
    app._app_state.image_table.col_axis = "timepoint"
    _btn, listw = _open_row_picker(app)

    _idx, tp_item = _row_of(listw, "timepoint")
    assert not (tp_item.flags() & Qt.ItemIsUserCheckable)
    assert not (tp_item.flags() & Qt.ItemIsEnabled)
    assert "(on Columns)" in tp_item.text()

    # Trying to check it is a no-op → it never joins the Rows axis.
    tp_item.setCheckState(Qt.Checked)
    assert "timepoint" not in app._app_state.image_table.row_dims
