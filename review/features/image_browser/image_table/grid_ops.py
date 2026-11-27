"""Image Table — the per-cell override: the **Cell** page of the inspector.

The drag-and-drop builder shelf owns the table's structure (which dimensions vary
on the Rows / Columns axes, the nested channels, the held Fixed values). This is
the one-off tweak for an irregular cell: a new Well / Channel / Timepoint / FOV
for one cell, chosen from that dimension's available values. The choice is
written straight into the plain render-model cell dict
(``ctx.runtime.image_table.cells[r][c]``) — the same dict the renderer reads —
and the grid re-renders.

The action strip's **Cell** row opens the page: which cell (its row and
column), then one list per dimension. Right-clicking a rendered cell is a
shortcut to the same page with that cell chosen; it was once the only way in,
through the application's one context menu, which a user had to stumble on and
phone layout cannot open at all.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QWidget

from review.core import inspector_panels
from review.features.image_browser.image_table.controller import (
    image_table_generate,
)
from review.features.image_browser.image_table.grid_layout import (
    _all_dim_options,
)

#: The action strip's row, and its page's title.
CELL_TITLE = "Cell"

# (label, dimension name, render-model cell key)
_OVERRIDE_DIMS = (
    ("Well", "well", "well"),
    ("Channel", "channel", "chan"),
    ("Timepoint", "timepoint", "tp"),
    ("FOV", "fov", "fov"),
)


def _cells(ctx) -> list:
    return getattr(ctx.runtime.image_table, "cells", None) or []


def cell_at(ctx, r: int, c: int) -> dict | None:
    cells = _cells(ctx)
    if 0 <= r < len(cells) and 0 <= c < len(cells[r]):
        return cells[r][c]
    return None


def set_cell_value(ctx, r: int, c: int, cell_key: str, value: str) -> bool:
    """Give cell (*r*, *c*) *value* for *cell_key* and re-render. ``False``
    when there is no such cell or it already holds the value."""
    cell = cell_at(ctx, r, c)
    if cell is None or cell.get(cell_key, "") == value:
        return False
    cell[cell_key] = value
    image_table_generate(ctx)
    return True


class _CellPage(QWidget):
    """Which cell, and that cell's four values."""

    def __init__(self, ctx, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.note = QLabel("", self)
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        form.addRow(self.note)
        self.row_cb, self.col_cb = QComboBox(self), QComboBox(self)
        self.row_cb.setObjectName("ImageTableCellRow")
        self.col_cb.setObjectName("ImageTableCellColumn")
        form.addRow("Row", self.row_cb)
        form.addRow("Column", self.col_cb)
        self.row_cb.currentIndexChanged.connect(lambda _i: self._cell_chosen())
        self.col_cb.currentIndexChanged.connect(lambda _i: self._show_values())
        self.value_cbs: dict[str, QComboBox] = {}
        for label, dim, key in _OVERRIDE_DIMS:
            combo = QComboBox(self)
            combo.setObjectName(f"ImageTableCell_{key}")
            combo.currentIndexChanged.connect(
                lambda _i, k=key, cb=combo: self._value_chosen(k, cb))
            self.value_cbs[key] = combo
            form.addRow(label, combo)

    # ── which cell ───────────────────────────────────────────────────────────
    def selected(self) -> tuple[int, int]:
        return max(self.row_cb.currentIndex(), 0), max(self.col_cb.currentIndex(), 0)

    def refill(self, r: int | None = None, c: int | None = None) -> None:
        """Rebuild the row and column lists from the grid as it stands, keeping
        (or moving to) cell (*r*, *c*)."""
        cells = _cells(self._ctx)
        keep_r, keep_c = self.selected()
        r = keep_r if r is None else r
        c = keep_c if c is None else c
        r = min(max(r, 0), max(len(cells) - 1, 0))
        self._fill(self.row_cb, len(cells), r)
        width = len(cells[r]) if cells else 0
        self._fill(self.col_cb, width, min(max(c, 0), max(width - 1, 0)))
        self.note.setText(
            "Change what one cell of the table shows. The table's shape comes "
            "from the builder above; this is for the odd cell out."
            if cells else "Build a table first: there is no cell to change yet.")
        for combo in (self.row_cb, self.col_cb, *self.value_cbs.values()):
            combo.setEnabled(bool(cells))
        self._show_values()

    @staticmethod
    def _fill(combo: QComboBox, count: int, current: int) -> None:
        blocked = combo.blockSignals(True)
        combo.clear()
        combo.addItems([str(i + 1) for i in range(count)])
        if count:
            combo.setCurrentIndex(current)
        combo.blockSignals(blocked)

    def _cell_chosen(self) -> None:
        r, c = self.selected()
        cells = _cells(self._ctx)
        width = len(cells[r]) if r < len(cells) else 0
        self._fill(self.col_cb, width, min(c, max(width - 1, 0)))
        self._show_values()

    # ── its values ───────────────────────────────────────────────────────────
    def _show_values(self) -> None:
        cell = cell_at(self._ctx, *self.selected()) or {}
        for _label, dim, key in _OVERRIDE_DIMS:
            combo = self.value_cbs[key]
            current = str(cell.get(key, "") or "")
            options = [str(v) for v in _all_dim_options(self._ctx, dim)]
            if current and current not in options:
                options.append(current)       # what the cell holds is always listed
            blocked = combo.blockSignals(True)
            combo.clear()
            combo.addItems(options)
            combo.setCurrentIndex(options.index(current) if current in options else -1)
            combo.blockSignals(blocked)

    def _value_chosen(self, key: str, combo: QComboBox) -> None:
        if combo.currentIndex() < 0:
            return
        r, c = self.selected()
        set_cell_value(self._ctx, r, c, key, combo.currentText())


def build_cell_row(ctx, parent: QWidget) -> QWidget:
    """The action strip's **Cell** row; pressing it shows the page."""
    button, content, content_layout = inspector_panels.make_popover_button(
        CELL_TITLE, parent, ctx=ctx, tab="data")
    button.setToolTip("Change what one cell of the table shows "
                      "(right-clicking a cell opens this for that cell)")
    page = _CellPage(ctx, content)
    content_layout.addWidget(page)
    inspector_panels.on_open(button, page.refill)
    ctx.widgets.register("_image_table_cell_btn", button)
    ctx.widgets.register("_image_table_cell_page", page)
    return button


def open_cell(ctx, r: int, c: int) -> None:
    """Show the Cell page on cell (*r*, *c*) — the right-click's shortcut."""
    from review.core import inspector_service
    button = ctx.widgets.get_or_none("_image_table_cell_btn")
    page = ctx.widgets.get_or_none("_image_table_cell_page")
    if button is None or page is None or cell_at(ctx, r, c) is None:
        return
    panel = inspector_panels.panel_of(button)
    if panel is None or not inspector_service.is_showing(ctx, panel.content):
        inspector_panels.press(button)
    page.refill(r, c)
