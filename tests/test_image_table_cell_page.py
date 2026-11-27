"""One cell of the Image Table is changed from a page a user can see.

A cell's own Well / Channel / Timepoint / FOV used to be reachable only by
right-clicking the cell — the application's one context menu, which nobody
finds by looking and phone layout cannot open. The action strip's **Cell** row
opens the same choice as a page of the inspector; the right-click is a shortcut
to that page with the cell chosen.

Through the real Image Table (``image_table_app``): the page is opened by its
row, a value is chosen in it, and what is asserted is the render-model cell the
renderer reads — and that the table was re-rendered.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _table(app):
    from review.features.image_browser.image_table import controller as C

    ctx = app._ctx
    assert app._notebook.setCurrentByName("Image Browser")
    C.image_table_apply_preset(ctx, "channel", "timepoint")
    cells = ctx.runtime.image_table.cells
    assert cells and len(cells[0]) >= 1
    return ctx, cells


@pytest.mark.qt
def test_the_cell_row_opens_a_page_and_a_choice_reaches_that_cell_only(image_table_app, monkeypatch):
    from review.core import inspector_service as svc
    from review.features.image_browser.image_table import grid_ops as G

    ctx, cells = _table(image_table_app)
    generated = []
    monkeypatch.setattr(G, "image_table_generate", lambda c: generated.append(c))
    button = ctx.widgets.get("_image_table_cell_btn")
    page = ctx.widgets.get("_image_table_cell_page")
    assert button.text() == "Cell ›"
    button.click()
    assert svc.is_open(ctx) and svc.inspector(ctx).isAncestorOf(page)
    assert svc.inspector(ctx).currentTab() == "data"
    assert page.row_cb.count() == len(cells) and page.col_cb.count() == len(cells[0])

    # Choose the last cell of the first row, and give it another well.
    r, c = 0, len(cells[0]) - 1
    page.col_cb.setCurrentIndex(c)
    wells = page.value_cbs["well"]
    assert wells.currentText() == cells[r][c]["well"]
    other = next(wells.itemText(i) for i in range(wells.count())
                 if wells.itemText(i) != cells[r][c]["well"])
    before = [[dict(cell) for cell in row] for row in cells]
    wells.setCurrentIndex(wells.findText(other))

    assert cells[r][c]["well"] == other
    assert generated == [ctx], "the table was not re-rendered once for the one change"
    changed = [(i, j) for i, row in enumerate(cells) for j, cell in enumerate(row)
               if cell != before[i][j]]
    assert changed == [(r, c)], f"cells other than the chosen one changed: {changed}"
    # Choosing what the cell already shows writes and renders nothing.
    assert G.set_cell_value(ctx, r, c, "well", other) is False
    assert generated == [ctx]


@pytest.mark.qt
def test_a_right_click_on_a_cell_is_a_shortcut_to_the_page_on_that_cell(image_table_app):
    from review.core import inspector_service as svc
    from review.features.image_browser.image_table import controller as C

    ctx, cells = _table(image_table_app)
    page = ctx.widgets.get("_image_table_cell_page")
    r, c = len(cells) - 1, len(cells[0]) - 1
    C._open_cell_page(ctx, r, c)
    assert svc.inspector(ctx).isAncestorOf(page)
    assert page.selected() == (r, c)
    assert page.value_cbs["chan"].currentText() == str(cells[r][c]["chan"])
    # A second right-click, on another cell, moves the page rather than closing it.
    C._open_cell_page(ctx, 0, 0)
    assert svc.inspector(ctx).isAncestorOf(page) and page.selected() == (0, 0)
    # A cell that is not there opens nothing and moves nothing.
    C._open_cell_page(ctx, 99, 99)
    assert page.selected() == (0, 0)
