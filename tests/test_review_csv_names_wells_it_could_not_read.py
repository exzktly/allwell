"""The Review CSV table says when some selected wells' rows could not be read.

Its collectors skipped a well whose rows would not read — out of memory, most often — and the table then showed the
other wells' rows and counted them as "Showing N row(s)", exactly as if they were the selection's. Driven here through
the tab's own builder and refresh on a loaded dataset, with one well's read failing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def table(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget

    from review.features.review_csv.tab_view import build_review_csv_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.mini_dataset import build_mini_dataset

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, build_mini_dataset(tmp_path / "mini"))
    parent = QWidget()
    qtbot.addWidget(parent)
    build_review_csv_tab(ctx, parent)
    ctx.state.selection.selected_wells = {"A01", "A02"}

    from review.features.image_browser.review_image import collect

    real = collect.get_rows

    def get_rows(ctx_, label):
        if label == "A02":
            raise MemoryError("simulated")
        return real(ctx_, label)

    monkeypatch.setattr(collect, "get_rows", get_rows)
    return ctx, parent


def _shown_wells(ctx) -> set:
    table = ctx.widgets.get("_review_csv_table")
    cols = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
    well = cols.index("well")
    return {table.item(r, well).text() for r in range(table.rowCount())}


def test_the_table_names_the_well_it_could_not_read(table):
    from review.features.image_browser.review_image.controller import refresh_review_csv

    ctx, _parent = table
    refresh_review_csv(ctx)
    assert _shown_wells(ctx) == {"A01"}
    msg = ctx.widgets.get("_review_csv_msg_lbl").text()
    assert msg.endswith(" Not shown: 1 selected well(s) whose rows could not be read (A02); the rows and counts "
                        "above are from the other wells. See the log for why."), msg
    assert msg.startswith("Showing "), msg


def test_every_well_unreadable_says_so_rather_than_no_rows(table, monkeypatch):
    from review.features.image_browser.review_image import collect
    from review.features.image_browser.review_image.controller import refresh_review_csv

    ctx, _parent = table

    def get_rows(_ctx, _label):
        raise MemoryError("simulated")

    monkeypatch.setattr(collect, "get_rows", get_rows)
    refresh_review_csv(ctx)
    assert ctx.widgets.get("_review_csv_msg_lbl").text() == (
        "No CSV rows loaded for selected well(s). 2 selected well(s) could not be read (A01, A02); see the log for why.")
