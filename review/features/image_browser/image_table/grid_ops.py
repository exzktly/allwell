"""Image Table — right-click per-cell override popover.

The drag-and-drop builder shelf owns the table's structure (which dimensions vary
on the Rows / Columns axes, the nested channels, the held Fixed values). This is
the one-off tweak for an irregular cell: right-clicking a rendered cell offers a
new Well / Channel / Timepoint / FOV chosen from that dimension's available
values. The choice is written straight into the plain render-model cell dict
(``ctx.runtime.image_table.cells[r][c]``) — the same dict the renderer reads —
and the grid re-renders.
"""

from __future__ import annotations
from widgets import menu_exec

from review.features.image_browser.image_table.controller import (
    image_table_generate,
)
from review.features.image_browser.image_table.grid_layout import (
    _all_dim_options,
)

# (menu label, dimension name, render-model cell key)
_OVERRIDE_DIMS = (
    ("Well", "well", "well"),
    ("Channel", "channel", "chan"),
    ("Timepoint", "timepoint", "tp"),
    ("FOV", "fov", "fov"),
)


def image_table_cell_override_menu(ctx, r: int, c: int, global_pos) -> None:
    """Right-click override popover for a single rendered cell.

    Progressive disclosure for the odd irregular cell: pick a new Well / Channel
    / Timepoint / FOV for just this cell from that dimension's available values,
    write it into the cell's render-model dict, and re-render. The bulk shape
    still comes from the builder shelf; this is the one-off tweak.
    """
    from PySide6.QtWidgets import QMenu

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not (0 <= r < len(cells) and 0 <= c < len(cells[r])):
        return
    cell = cells[r][c]
    menu = QMenu()

    def _set(cell_key: str, val: str) -> None:
        cell[cell_key] = val
        image_table_generate(ctx)

    for label, dim, cell_key in _OVERRIDE_DIMS:
        cur = cell.get(cell_key, "")
        sub = menu.addMenu(f"{label}: {cur or '—'}")
        for val in _all_dim_options(ctx, dim):
            act = sub.addAction(val)
            act.setCheckable(True)
            act.setChecked(val == cur)
            act.triggered.connect(lambda _c=False, k=cell_key, v=val: _set(k, v))
    menu_exec.exec_menu(menu, global_pos)
