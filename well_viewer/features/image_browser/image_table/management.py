"""Image table row/column management (remove, add).

These functions handle adding and removing rows/columns from the image table grid,
including the associated dialogs and state updates.
"""

from typing import Dict, List


def image_table_remove_row(app, r: int) -> None:
    """Remove row *r* from the grid. Guard against removing the only row.

    Snapshots cells, drops row *r*, shrinks the rows count, updates the
    spinner (blocking signals), calls image_table_rebuild_grid, then
    restores the remaining rows via _write_cell.
    """
    from well_viewer.features.image_browser.image_table.controller import (
        _snapshot_cells,
        _write_cell,
        image_table_rebuild_grid,
        image_table_rebuild_lut_row,
    )

    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: no rows to remove.")
        return
    if len(cells) <= 1:
        app._set_status("Image Table: cannot remove the only row.")
        return
    if not (0 <= r < len(cells)):
        app._set_status(f"Image Table: row {r} out of range.")
        return

    snap = _snapshot_cells(app)
    new_snap = [row for idx, row in enumerate(snap) if idx != r]
    new_rows = len(new_snap)

    app._app_state.image_table.rows = new_rows
    spin = getattr(app, "_image_table_rows_spin", None)
    if spin is not None:
        spin.blockSignals(True)
        spin.setValue(new_rows)
        spin.blockSignals(False)

    image_table_rebuild_grid(app)

    for row_idx, row in enumerate(getattr(app, "_image_table_cells", None) or []):
        if row_idx >= len(new_snap):
            break
        for col_idx, cell in enumerate(row):
            if col_idx < len(new_snap[row_idx]):
                _write_cell(cell, new_snap[row_idx][col_idx])

    image_table_rebuild_lut_row(app)
    app._set_status(f"Image Table: removed row {r + 1}. Press Generate to render.")


def image_table_remove_col(app, c: int) -> None:
    """Remove column *c* from the grid. Guard against removing the only column.

    Snapshots cells, drops column *c* from each row, shrinks the cols count,
    updates the spinner (blocking signals), calls image_table_rebuild_grid,
    then restores the remaining cells.
    """
    from well_viewer.features.image_browser.image_table.controller import (
        _snapshot_cells,
        _write_cell,
        image_table_rebuild_grid,
        image_table_rebuild_lut_row,
    )

    cells = getattr(app, "_image_table_cells", None) or []
    if not cells or not cells[0]:
        app._set_status("Image Table: no columns to remove.")
        return
    if len(cells[0]) <= 1:
        app._set_status("Image Table: cannot remove the only column.")
        return
    if not (0 <= c < len(cells[0])):
        app._set_status(f"Image Table: column {c} out of range.")
        return

    snap = _snapshot_cells(app)
    new_snap = [[cell for col_idx, cell in enumerate(row) if col_idx != c]
                for row in snap]
    new_cols = len(new_snap[0]) if new_snap else 0

    app._app_state.image_table.cols = new_cols
    spin = getattr(app, "_image_table_cols_spin", None)
    if spin is not None:
        spin.blockSignals(True)
        spin.setValue(new_cols)
        spin.blockSignals(False)

    image_table_rebuild_grid(app)

    for row_idx, row in enumerate(getattr(app, "_image_table_cells", None) or []):
        if row_idx >= len(new_snap):
            break
        for col_idx, cell in enumerate(row):
            if col_idx < len(new_snap[row_idx]):
                _write_cell(cell, new_snap[row_idx][col_idx])

    image_table_rebuild_lut_row(app)
    app._set_status(f"Image Table: removed column {c + 1}. Press Generate to render.")


def image_table_add_varied_col(app, *, src_idx: int, dim: str, mode: str, value: str) -> None:
    """Append a new column cloned from col *src_idx*, varying one dimension.

    Uniform mode sets *dim* to *value* across the whole new column; Step mode
    advances each cell's value for *dim* by one (clamped to the last available
    value). Other dimensions are copied as-is. Builds the selector grid only —
    press Generate to render. Driven by :func:`image_table_open_add_col_dialog`.
    """
    from well_viewer.features.image_browser.image_table.controller import (
        _snapshot_cells,
        _write_cell,
        image_table_rebuild_grid,
        image_table_rebuild_lut_row,
        _all_dim_options,
    )

    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a table first, then add a column.")
        return
    dim = dim or "channel"
    mode = mode or "uniform"
    value = (value or "").strip()

    snap = _snapshot_cells(app)
    if not (0 <= src_idx < (len(snap[0]) if snap else 0)):
        src_idx = (len(snap[0]) - 1) if snap else 0

    new_col: List[Dict[str, str]] = []
    for row_idx, row_vals in enumerate(snap):
        if src_idx >= len(row_vals):
            src_idx = len(row_vals) - 1
        cell_vals = dict(row_vals[src_idx])
        nv = dict(cell_vals)
        if mode == "uniform":
            if value:
                nv[dim] = value
        else:  # step
            cur = cell_vals.get(dim, "")
            opts = _all_dim_options(app, dim)
            if opts:
                try:
                    i = opts.index(cur)
                except ValueError:
                    i = -1
                nv[dim] = opts[min(i + 1, len(opts) - 1)] if i >= 0 else opts[0]
        new_col.append(nv)

    new_cols = len(snap[0]) + 1 if snap else 1
    app._app_state.image_table.cols = new_cols
    spin = getattr(app, "_image_table_cols_spin", None)
    if spin is not None:
        spin.blockSignals(True)
        spin.setValue(new_cols)
        spin.blockSignals(False)
    image_table_rebuild_grid(app)

    full = [row + [new_col[r]] for r, row in enumerate(snap)]
    for r, row in enumerate(getattr(app, "_image_table_cells", None) or []):
        if r >= len(full):
            break
        for c, cell in enumerate(row):
            if c < len(full[r]):
                _write_cell(cell, full[r][c])

    image_table_rebuild_lut_row(app)
    verb = f"set {dim} = {value}" if mode == "uniform" else f"stepped {dim}"
    app._set_status(
        f"Image Table: added column {new_cols} cloned from column {src_idx + 1} "
        f"({verb}). Press Generate to render."
    )


def image_table_open_add_col_dialog(app) -> None:
    """Modal dialog to clone an existing column, varying one dimension.

    Collects source column / dimension / mode / value here (value list is
    per-dimension, enabled only in Uniform mode) and calls
    :func:`image_table_add_varied_col`.
    """
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout
    from well_viewer.features.image_browser.image_table.controller import (
        _all_dim_options,
    )

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
    for label, key in (("Channel", "channel"), ("Timepoint", "timepoint"), ("FOV", "fov"), ("Well", "well")):
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
        val_cb.blockSignals(True)
        val_cb.clear()
        opts = _all_dim_options(app, dim)
        val_cb.addItems(opts)
        if prev in opts:
            val_cb.setCurrentText(prev)
        val_cb.blockSignals(False)
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
