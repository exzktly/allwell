"""A Review CSV row is opened in Segmentation from a button a user can see.

Double-clicking a row shows its cell in Segmentation. The double click is a
shortcut; **Open in Segmentation** on the page's band is the visible route and
the only one where there is no double click. The page is built by its real
builder in a minimal shell; the navigation it calls is the one seam replaced,
so what is asserted is the row handed to it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTableWidgetItem, QWidget

from review.features.review_csv import tab_view as TV
from tests._minimal_shell import minimal_shell


def _page(qtbot, monkeypatch):
    ctx, window = minimal_shell(qtbot)
    opened: list = []
    monkeypatch.setattr(TV, "on_review_csv_row_double_click",
                        lambda _ctx, item: opened.append((item.row(), item.text())))
    monkeypatch.setattr(TV, "refresh_review_csv", lambda _ctx: None)
    monkeypatch.setattr(TV, "refresh_review_csv_rows", lambda _ctx: None)
    frame = QWidget()
    window.setCentralWidget(frame)
    TV.build_review_csv_tab(ctx, frame)
    table = ctx.widgets.get("_review_csv_table")
    table.setColumnCount(2)
    table.setRowCount(3)
    for r in range(3):
        for c in range(2):
            table.setItem(r, c, QTableWidgetItem(f"r{r}c{c}"))
    window.show()
    return ctx, table, opened


def test_the_button_is_disabled_until_a_row_is_selected(qtbot, monkeypatch):
    ctx, table, opened = _page(qtbot, monkeypatch)
    button = ctx.widgets.get("_review_csv_open_btn")
    assert button.text() == "Open in Segmentation"
    assert not button.isEnabled()
    table.selectRow(1)
    assert button.isEnabled()
    table.clearSelection()
    assert not button.isEnabled()
    assert opened == []


def test_pressing_it_opens_the_selected_row_as_the_double_click_does(qtbot, monkeypatch):
    ctx, table, opened = _page(qtbot, monkeypatch)
    button = ctx.widgets.get("_review_csv_open_btn")
    table.selectRow(2)                       # the last row: "always the first" opens another
    button.click()
    assert [row for row, _text in opened] == [2]
    table.itemDoubleClicked.emit(table.item(1, 0))
    assert [row for row, _text in opened] == [2, 1]
