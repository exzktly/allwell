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
    image_table_generate,
    image_table_rebuild_grid,
    image_table_rebuild_lut_row,
)
from well_viewer.features.image_browser.image_table.grid_layout import (
    _all_dim_options,
    _snapshot_cells,
    _write_cell,
)


def _restore_cells(ctx, snap: List[List[Dict[str, str]]]) -> None:
    """Write *snap* back into the freshly-rebuilt live cell grid."""
    for r, row in enumerate(getattr(ctx.runtime.image_table, "cells", None) or []):
        if r >= len(snap):
            break
        for c, cell in enumerate(row):
            if c < len(snap[r]):
                _write_cell(cell, snap[r][c])


def _snapshot_row_selectors(ctx) -> List[Dict[str, str]]:
    """Capture each row's per-row Channel + Well 'apply to whole row' selections
    so a rebuild can restore them by position (``build_image_table_grid``
    recreates these combos at their first option, losing the user's choice)."""
    it = ctx.runtime.image_table
    chans = getattr(it, "row_chan_cbs", None) or []
    wells = getattr(it, "row_well_cbs", None) or []
    out: List[Dict[str, str]] = []
    for i in range(max(len(chans), len(wells))):
        out.append({
            "channel": chans[i].currentText().strip()
            if i < len(chans) and chans[i] is not None else "",
            "well": wells[i].currentText().strip()
            if i < len(wells) and wells[i] is not None else "",
        })
    return out


def _restore_row_selectors(ctx, snap: List[Dict[str, str]]) -> None:
    """Write per-row Channel + Well selections back into the rebuilt row combos.

    Signals are blocked — the cells were already restored by ``_restore_cells``,
    so this only re-syncs the row-shortcut widgets (and the LUT-row channel
    labels that read them); it must not re-broadcast onto the cells.
    """
    it = ctx.runtime.image_table
    chans = getattr(it, "row_chan_cbs", None) or []
    wells = getattr(it, "row_well_cbs", None) or []
    for i, vals in enumerate(snap):
        for cbs, key in ((chans, "channel"), (wells, "well")):
            text = vals.get(key, "")
            if not text or i >= len(cbs) or cbs[i] is None:
                continue
            cb = cbs[i]
            blocked = cb.blockSignals(True)
            try:
                if cb.findText(text) >= 0:
                    cb.setCurrentText(text)
            finally:
                cb.blockSignals(blocked)


def _drop_row_lut_state(ctx, r: int) -> None:
    """Re-key the per-row LUT colour + levels/display state so removing grid row
    *r* shifts the rows below it up by one.

    ``build_lut_row`` preserves per-row LUT state (colour, min/max, source,
    boundaries, binary, log) across a rebuild **keyed by row index**. On a row
    removal the surviving rows below *r* shift up, so without this each of them
    would inherit the assignment of whatever row moved into its slot (colours /
    levels visibly scramble on delete). Dropping index *r* and shifting the rest
    down realigns the preserved state with the new row positions.

    Flat (non-grouped) layout only: there the LUT-editor rows map 1:1 to grid
    rows. Grouped/tiled layouts share one LUT row per channel (keyed by channel
    group, not grid row), so a grid-row removal doesn't disturb their
    per-channel assignments — leave them untouched.
    """
    if int(getattr(ctx.state.image_table, "group_size", 0) or 0) > 0:
        return
    it = ctx.runtime.image_table
    colors = getattr(it, "row_lut_color_cbs", None)
    if isinstance(colors, list) and 0 <= r < len(colors):
        del colors[r]
    lut = getattr(it, "lut", None)
    if isinstance(lut, dict):
        it.lut = {(k if k < r else k - 1): v for k, v in lut.items() if k != r}


def _set_cols(ctx, new_cols: int) -> None:
    """Update the cols state + spinner (signals blocked) ahead of a rebuild."""
    ctx.state.image_table.cols = new_cols
    spin = getattr(ctx.runtime.image_table, "cols_spin", None)
    if spin is not None:
        blocked = spin.blockSignals(True)
        try:
            spin.setValue(new_cols)
        finally:
            spin.blockSignals(blocked)


def _set_rows(ctx, new_rows: int) -> None:
    """Update the rows state + spinner (signals blocked) ahead of a rebuild."""
    ctx.state.image_table.rows = new_rows
    spin = getattr(ctx.runtime.image_table, "rows_spin", None)
    if spin is not None:
        blocked = spin.blockSignals(True)
        try:
            spin.setValue(new_rows)
        finally:
            spin.blockSignals(blocked)


def image_table_remove_row(ctx, r: int) -> None:
    """Remove row *r* from the grid (never the only row), preserving the rest.

    "The rest" includes every surviving row's per-cell selections, its per-row
    Channel/Well shortcut, and its LUT colour + levels/display assignment — all
    of which are keyed by row index and would otherwise scramble when the rows
    below *r* shift up (see ``_drop_row_lut_state`` / ``_restore_row_selectors``).
    """
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        ctx.set_status("Image Table: no rows to remove.")
        return
    if len(cells) <= 1:
        ctx.set_status("Image Table: cannot remove the only row.")
        return
    if not (0 <= r < len(cells)):
        ctx.set_status(f"Image Table: row {r + 1} out of range.")
        return

    new_snap = [row for idx, row in enumerate(_snapshot_cells(ctx)) if idx != r]
    row_selectors = [s for idx, s in enumerate(_snapshot_row_selectors(ctx))
                     if idx != r]
    _drop_row_lut_state(ctx, r)  # before the rebuild's index-keyed LUT restore
    _set_rows(ctx, len(new_snap))
    image_table_rebuild_grid(ctx)
    _restore_cells(ctx, new_snap)
    _restore_row_selectors(ctx, row_selectors)
    image_table_rebuild_lut_row(ctx)  # relabel now the restored channels are set
    ctx.set_status(f"Image Table: removed row {r + 1}. Press Generate to render.")


def image_table_remove_col(ctx, c: int) -> None:
    """Remove column *c* from the grid (never the only column)."""
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells or not cells[0]:
        ctx.set_status("Image Table: no columns to remove.")
        return
    if len(cells[0]) <= 1:
        ctx.set_status("Image Table: cannot remove the only column.")
        return
    if not (0 <= c < len(cells[0])):
        ctx.set_status(f"Image Table: column {c + 1} out of range.")
        return

    new_snap = [[cell for idx, cell in enumerate(row) if idx != c]
                for row in _snapshot_cells(ctx)]
    _set_cols(ctx, len(new_snap[0]) if new_snap else 0)
    image_table_rebuild_grid(ctx)
    _restore_cells(ctx, new_snap)
    image_table_rebuild_lut_row(ctx)
    ctx.set_status(f"Image Table: removed column {c + 1}. Press Generate to render.")


def image_table_add_varied_col(ctx, *, src_idx: int, dim: str, mode: str, value: str) -> None:
    """Append a column cloned from *src_idx*, varying one dimension.

    Uniform mode sets *dim* to *value* across the whole new column; Step mode
    advances each cell's value for *dim* by one (clamped to the last option).
    Mirrors ``controller.image_table_add_varied_row`` for the column axis.
    """
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        ctx.set_status("Image Table: build a table first, then add a column.")
        return
    dim = dim or "channel"
    mode = mode or "uniform"
    value = (value or "").strip()

    snap = _snapshot_cells(ctx)
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
            opts = _all_dim_options(ctx, dim)
            if opts:
                try:
                    i = opts.index(nv.get(dim, ""))
                except ValueError:
                    i = -1
                nv[dim] = opts[min(i + 1, len(opts) - 1)] if i >= 0 else opts[0]
        new_col.append(nv)

    _set_cols(ctx, (len(snap[0]) + 1) if snap and snap[0] else 1)
    image_table_rebuild_grid(ctx)
    _restore_cells(ctx, [row + [new_col[r]] for r, row in enumerate(snap)])
    image_table_rebuild_lut_row(ctx)
    verb = f"set {dim} = {value}" if mode == "uniform" else f"stepped {dim}"
    ctx.set_status(
        f"Image Table: added column {len(snap[0]) + 1} cloned from column "
        f"{src_idx + 1} ({verb}). Press Generate to render."
    )


def image_table_open_add_col_dialog(ctx) -> None:
    """Modal dialog to clone an existing column, varying one dimension.

    Mirrors ``controller.image_table_open_add_row_dialog``; on accept it calls
    :func:`image_table_add_varied_col`.
    """
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells or not cells[0]:
        ctx.set_status("Image Table: build a table first, then add a column.")
        return

    dlg = QDialog(ctx.window)
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
            opts = _all_dim_options(ctx, dim)
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
        ctx,
        src_idx=int(src_cb.currentData() or 0),
        dim=str(dim_cb.currentData() or "channel"),
        mode=str(mode_cb.currentData() or "uniform"),
        value=val_cb.currentText().strip(),
    )


def image_table_cell_override_menu(ctx, r: int, c: int, global_pos) -> None:
    """Right-click override popover for a single rendered cell.

    Progressive disclosure for the odd irregular cell: pick a new Well / Channel
    / Timepoint / FOV for just this cell from its own combo's values, write it
    into the selector combo (the same widget the renderer reads), and re-render.
    The bulk shape still comes from the builder shelf; this is the one-off tweak.
    """
    from PySide6.QtWidgets import QMenu

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not (0 <= r < len(cells) and 0 <= c < len(cells[r])):
        return
    cell = cells[r][c]
    menu = QMenu()

    def _set(cb, val):
        cb.blockSignals(True)
        if cb.findText(val) >= 0:
            cb.setCurrentText(val)
        cb.blockSignals(False)
        image_table_generate(ctx)

    for label, key in (("Well", "well_cb"), ("Channel", "chan_cb"),
                       ("Timepoint", "tp_cb"), ("FOV", "fov_cb")):
        cb = cell.get(key)
        if cb is None:
            continue
        sub = menu.addMenu(f"{label}: {cb.currentText() or '—'}")
        for i in range(cb.count()):
            val = cb.itemText(i)
            act = sub.addAction(val)
            act.setCheckable(True)
            act.setChecked(i == cb.currentIndex())
            act.triggered.connect(lambda _c=False, _cb=cb, _v=val: _set(_cb, _v))
    menu.exec(global_pos)
