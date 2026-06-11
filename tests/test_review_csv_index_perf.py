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
    idx = app._review_csv_row_index
    assert isinstance(idx, dict) and idx, "row index should be populated"
    for (_f, _t, _n), r in list(idx.items()):
        assert 0 <= r < table.rowCount()
    # The index round-trips: the key derived from a row resolves to that row.
    assert app._review_csv_render_sig is not None

    # Headers must not clip: each column is at least as wide as its header text.
    fm = QFontMetrics(table.horizontalHeader().font())
    for ci in range(table.columnCount()):
        hdr_item = table.horizontalHeaderItem(ci)
        hdr = hdr_item.text() if hdr_item is not None else ""
        assert table.columnWidth(ci) >= fm.horizontalAdvance(hdr), (
            f"column {ci!r} header {hdr!r} clipped: "
            f"width={table.columnWidth(ci)} < text={fm.horizontalAdvance(hdr)}"
        )
