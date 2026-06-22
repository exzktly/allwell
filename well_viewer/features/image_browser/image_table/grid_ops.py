"""Image Table row/column management — add + one-click remove.

Add/remove operations on the Image Table selector grid, plus the "Add column"
dialog. Paired with ``image_table/controller.py``: the controller wires the
remove callbacks into ``build_image_table_grid`` and lazily imports the add /
remove entry points here (the controller-side import is deferred to break the
``controller`` ↔ ``grid_ops`` cycle — see ``image_table_rebuild_grid``).

Only the selector grid is (re)built here — rendering happens on the explicit
**Generate** action, so each function ends by asking the user to press it.
"""

from __future__ import annotations

from typing import Dict, List

from well_viewer.features.image_browser.image_table.controller import (
    image_table_rebuild_grid,
    image_table_rebuild_lut_row,
)
from well_viewer.features.image_browser.image_table.grid_layout import (
    _all_dim_options,
    _snapshot_cells,
    _write_cell,
)


def _restore_cells(app, snap: List[List[Dict[str, str]]]) -> None:
    """Write *snap* back into the freshly-rebuilt live cell grid."""
    for r, row in enumerate(getattr(app, "_image_table_cells", None) or []):
        if r >= len(snap):
            break
        for c, cell in enumerate(row):
            if c < len(snap[r]):
                _write_cell(cell, snap[r][c])


def _set_cols(app, new_cols: int) -> None:
    """Update the cols state + spinner (signals blocked) ahead of a rebuild."""
    app._app_state.image_table.cols = new_cols
    spin = getattr(app, "_image_table_cols_spin", None)
    if spin is not None:
        blocked = spin.blockSignals(True)
        try:
            spin.setValue(new_cols)
        finally:
            spin.blockSignals(blocked)


def _set_rows(app, new_rows: int) -> None:
    """Update the rows state + spinner (signals blocked) ahead of a rebuild."""
    app._app_state.image_table.rows = new_rows
    spin = getattr(app, "_image_table_rows_spin", None)
    if spin is not None:
        blocked = spin.blockSignals(True)
        try:
            spin.setValue(new_rows)
        finally:
            spin.blockSignals(blocked)


def image_table_remove_row(app, r: int) -> None:
    """Remove row *r* from the grid (never the only row), preserving the rest."""
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: no rows to remove.")
        return
    if len(cells) <= 1:
        app._set_status("Image Table: cannot remove the only row.")
        return
    if not (0 <= r < len(cells)):
        app._set_status(f"Image Table: row {r + 1} out of range.")
        return

    new_snap = [row for idx, row in enumerate(_snapshot_cells(app)) if idx != r]
    _set_rows(app, len(new_snap))
    image_table_rebuild_grid(app)
    _restore_cells(app, new_snap)
    image_table_rebuild_lut_row(app)
    app._set_status(f"Image Table: removed row {r + 1}. Press Generate to render.")


def image_table_remove_col(app, c: int) -> None:
    """Remove column *c* from the grid (never the only column)."""
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells or not cells[0]:
        app._set_status("Image Table: no columns to remove.")
        return
    if len(cells[0]) <= 1:
        app._set_status("Image Table: cannot remove the only column.")
        return
    if not (0 <= c < len(cells[0])):
        app._set_status(f"Image Table: column {c + 1} out of range.")
        return

    new_snap = [[cell for idx, cell in enumerate(row) if idx != c]
                for row in _snapshot_cells(app)]
    _set_cols(app, len(new_snap[0]) if new_snap else 0)
    image_table_rebuild_grid(app)
    _restore_cells(app, new_snap)
    image_table_rebuild_lut_row(app)
    app._set_status(f"Image Table: removed column {c + 1}. Press Generate to render.")


def image_table_add_varied_col(app, *, src_idx: int, dim: str, mode: str, value: str) -> None:
    """Append a column cloned from *src_idx*, varying one dimension.

    Uniform mode sets *dim* to *value* across the whole new column; Step mode
    advances each cell's value for *dim* by one (clamped to the last option).
    Mirrors ``controller.image_table_add_varied_row`` for the column axis.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a table first, then add a column.")
        return
    dim = dim or "channel"
    mode = mode or "uniform"
    value = (value or "").strip()

    snap = _snapshot_cells(app)
    last_col = (len(snap[0]) - 1) if snap and snap[0] else 0
    if not (0 <= src_idx <= last_col):
        src_idx = last_col

    new_col: List[Dict[str, str]] = []
    for row_vals in snap:
        idx = min(src_idx, len(row_vals) - 1)
        nv = dict(row_vals[idx])
        if mode == "uniform":
            if value:
                nv[dim] = value
        else:  # step +1
            opts = _all_dim_options(app, dim)
            if opts:
                try:
                    i = opts.index(nv.get(dim, ""))
                except ValueError:
                    i = -1
                nv[dim] = opts[min(i + 1, len(opts) - 1)] if i >= 0 else opts[0]
        new_col.append(nv)

    _set_cols(app, (len(snap[0]) + 1) if snap and snap[0] else 1)
    image_table_rebuild_grid(app)
    _restore_cells(app, [row + [new_col[r]] for r, row in enumerate(snap)])
    image_table_rebuild_lut_row(app)
    verb = f"set {dim} = {value}" if mode == "uniform" else f"stepped {dim}"
    app._set_status(
        f"Image Table: added column {len(snap[0]) + 1} cloned from column "
        f"{src_idx + 1} ({verb}). Press Generate to render."
    )


def image_table_open_add_col_dialog(app) -> None:
    """Modal dialog to clone an existing column, varying one dimension.

    Mirrors ``controller.image_table_open_add_row_dialog``; on accept it calls
    :func:`image_table_add_varied_col`.
    """
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout

    cells = getattr(app, "_image_table_cells", None) or []
    if not cells or not cells[0]:
        app._set_status("Image Table: build a table first, then add a column.")
        return

    dlg = QDialog(app)
    dlg.setWindowTitle("Image Table — Add column")
    form = QFormLayout(dlg)

    src_cb = QComboBox(dlg)
    for i in range(len(cells[0])):
        src_cb.addItem(f"Column {i + 1}", i)
    src_cb.setCurrentIndex(src_cb.count() - 1)
    form.addRow("Clone column:", src_cb)

    dim_cb = QComboBox(dlg)
    for label, key in (("Channel", "channel"), ("Timepoint", "timepoint"),
                       ("FOV", "fov"), ("Well", "well")):
        dim_cb.addItem(label, key)
    form.addRow("Vary:", dim_cb)

    mode_cb = QComboBox(dlg)
    mode_cb.addItem("Uniform → one value", "uniform")
    mode_cb.addItem("Step +1 per row", "step")
    mode_cb.setToolTip(
        "Uniform: set the chosen dimension to one value across the new column.\n"
        "Step +1: advance each cell's value for that dimension by one."
    )
    form.addRow("Mode:", mode_cb)

    val_cb = QComboBox(dlg)
    val_cb.setToolTip("New value (Uniform mode only).")
    form.addRow("Value:", val_cb)

    def _refresh_values() -> None:
        dim = dim_cb.currentData() or "channel"
        prev = val_cb.currentText().strip()
        blocked = val_cb.blockSignals(True)
        try:
            val_cb.clear()
            opts = _all_dim_options(app, dim)
            val_cb.addItems(opts)
            if prev in opts:
                val_cb.setCurrentText(prev)
        finally:
            val_cb.blockSignals(blocked)
        val_cb.setEnabled((mode_cb.currentData() or "uniform") == "uniform")

    dim_cb.currentIndexChanged.connect(lambda _i: _refresh_values())
    mode_cb.currentIndexChanged.connect(lambda _i: _refresh_values())
    _refresh_values()

    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    form.addRow(btns)

    if dlg.exec() != QDialog.Accepted:
        return
    image_table_add_varied_col(
        app,
        src_idx=int(src_cb.currentData() or 0),
        dim=str(dim_cb.currentData() or "channel"),
        mode=str(mode_cb.currentData() or "uniform"),
        value=val_cb.currentText().strip(),
    )
