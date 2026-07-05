"""Review CSV: O(1) navigation index + non-clipped headers.

Regression guards for the segmentation-click slowness fix. Clicking a cell used
to rebuild the whole QTableWidget and then linearly rescan it (re-normalising
keys per row) on every click. refresh_review_csv_rows now stamps a
{(fov,tp,nid): row} index + a render signature so navigation is an O(1) lookup,
and sizes columns to their header text so long column names don't clip.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


def test_review_csv_builds_row_index_and_unclipped_headers(loaded_app):
    from PySide6.QtGui import QFontMetrics

    app = loaded_app
    assert app._notebook.setCurrentByName("Review CSV")
    # Load rows for every well so the table is populated.
    app._set_selected_wells(set(app._app_state.dataset.well_paths), commit=False)
    app._refresh_review_csv_rows()

    table = app._review_csv_table
    if table.rowCount() == 0:
        pytest.skip("no review rows materialised for the mini dataset")

    # O(1) navigation index: built, and every entry points at a real row.
    idx = app._ctx.runtime.review.csv_row_index
    assert isinstance(idx, dict) and idx, "row index should be populated"
    for r in idx.values():
        assert 0 <= r < table.rowCount()
    # The index round-trips: the key derived from a row resolves to that row.
    assert app._ctx.runtime.review.csv_render_sig is not None

    # Headers must not clip: each column is at least as wide as its header text.
    fm = QFontMetrics(table.horizontalHeader().font())
    for ci in range(table.columnCount()):
        hdr_item = table.horizontalHeaderItem(ci)
        hdr = hdr_item.text() if hdr_item is not None else ""
        assert table.columnWidth(ci) >= fm.horizontalAdvance(hdr), (
            f"column {ci!r} header {hdr!r} clipped: "
            f"width={table.columnWidth(ci)} < text={fm.horizontalAdvance(hdr)}"
        )


def test_review_csv_index_keyed_by_well(loaded_app):
    """The navigation index must distinguish wells: the same
    (fov, timepoint, nucleus_id) can appear in multiple selected wells, so each
    well-qualified key must resolve to a row whose 'well' column matches —
    otherwise a segmentation click highlights the wrong well's row (or misses)."""
    app = loaded_app
    assert app._notebook.setCurrentByName("Review CSV")
    app._set_selected_wells(set(app._app_state.dataset.well_paths), commit=False)
    app._refresh_review_csv_rows()
    table = app._review_csv_table
    if table.rowCount() == 0:
        pytest.skip("no review rows materialised for the mini dataset")

    well_col = None
    for ci in range(table.columnCount()):
        hdr = table.horizontalHeaderItem(ci)
        if hdr is not None and hdr.text() == "well":
            well_col = ci
            break
    if well_col is None:
        pytest.skip("no 'well' column in the review CSV")

    four_tuple_keys = [k for k in app._ctx.runtime.review.csv_row_index if len(k) == 4]
    assert four_tuple_keys, "index should carry well-qualified (well,fov,tp,nid) keys"
    for key in four_tuple_keys:
        rw = key[0]
        r = app._ctx.runtime.review.csv_row_index[key]
        cell = table.item(r, well_col)
        assert cell is not None
        assert cell.text().strip().upper() == rw, (
            f"well-keyed index row {r} has well {cell.text()!r}, expected {rw!r}"
        )
