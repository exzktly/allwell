"""Image Table tab — non-UI controller logic.

These functions are bound onto the runtime app (via ``runtime_app.py``)
and drive the Image Table tab:

- multi-select sidebar picker (ignores groupings / replicate sets)
- selector grid build / rebuild (rows × cols of dropdowns per cell)
- "Distribute Wells" — assigns active wells row-wise into cells
- Per-row channel + LUT-color selectors (column 0 of the selector grid)
- Global Options propagation (timepoint / FOV → every cell)
- Generate — loads images and lays them out below the selector
- Per-channel auto-LUT
- Export — matplotlib figure with labels above each image, transparent BG
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QVBoxLayout, QWidget,
)
from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import set_combo_values
from well_viewer.features.image_browser.image_table.grid_ops import (
    image_table_remove_row,
    image_table_remove_col,
)


# ── LUT colour palette ──────────────────────────────────────────────────────
#
# Per-row tints applied to the grayscale fluorescence image. ``Gray`` keeps
# the legacy white intensity ramp. Each tint is an (r, g, b) triple in
# [0, 1]; ``Gray`` uses ``None`` so make_fluor_thumb takes its untinted
# fast path.
# The image-composition layer (LUT palette, tint/cmap resolution, array
# loading, mask/threshold rendering, RGB composer, overlay) lives in
# ``image_compose``. Re-exported so call sites + external importers
# (playground, grid_view) are unchanged.
from well_viewer.features.image_browser.image_table.image_compose import (  # noqa: E402,F401
    LUT_COLORS,
    LUT_COLOR_NAMES,
    NUC_SEG_TOKEN,
    _compose_image_table_rgb,
    _export_binary_mask,
    _format_pixel_intensity,
    _is_nuc_seg,
    _load_array,
    _load_mask_array,
    _load_ratio_array,
    _load_well_channel,
    _norm_token,
    _numeric_token,
    _parse_lut,
    _row_display_opts,
    _render_mask_binary,
    _render_threshold_binary,
    _render_with_boundary_overlay,
    _row_cmap_name,
    _row_export_cmap,
    _row_tint,
    _tint_from_colormap,
)




# ── Virtual "Nuc+Seg" channel ───────────────────────────────────────────────
#
# The pipeline pre-renders a per-(FOV, timepoint) overlay PNG that paints
# the segmentation mask outline on top of the nuclear channel. Exposing it
# as a virtual channel here lets the Image Table double as the old Movie
# Montage view: pick "NUC+SEG" in the channel column and Generate loads
# those overlay images instead of a raw fluorescence channel.


# ── Sidebar picker ───────────────────────────────────────────────────────────


def image_table_refresh_picker(app) -> None:
    """Push the active-well set onto the sidebar plate and update the count label."""
    plate = getattr(app, "_sidebar_image_table_plate", None)
    if plate is not None:
        plate.setEnabledWells(list(app._app_state.dataset.well_paths.keys()))
        active = sorted(
            (w for w in app._app_state.image_table.active_wells if w in app._app_state.dataset.well_paths),
            key=app._parse_rc,
        )
        plate.setSelectedWellIds(active)
    lbl = getattr(app, "_image_table_count_lbl", None)
    if lbl is not None:
        n = len(app._app_state.image_table.active_wells)
        lbl.setText(f"{n} well{'s' if n != 1 else ''} active")


def image_table_select_all(app) -> None:
    app._app_state.image_table.active_wells = set(app._app_state.dataset.well_paths.keys())
    image_table_refresh_picker(app)


def image_table_clear_active(app) -> None:
    app._app_state.image_table.active_wells = set()
    image_table_refresh_picker(app)


# ── Pipeline-info-driven dropdown options ────────────────────────────────────


# The layout/dimension layer (axis math, per-dim option lists, channel
# enumeration, cell snapshot) lives in ``grid_layout``. Re-exported so call
# sites + external importers (playground) are unchanged.
from well_viewer.features.image_browser.image_table.grid_layout import (  # noqa: E402,F401
    _DIM_KEY,
    _all_dim_options,
    _axis_values,
    _cache_column_tokens,
    _channel_options,
    _current_axis,
    _find_ratio_for_channel,
    _fixed_off_axis,
    _fov_options,
    _layout_cell_values,
    _snapshot_cells,
    _timepoint_options,
    _tp_sort_key,
    _well_options,
    _write_cell,
)




















def image_table_repopulate_dropdowns(app) -> None:
    """Refresh option lists for the global row, the per-row channel combos, and every cell.

    Called when pipeline info / wells become available after the tab is
    already built. Existing selections are preserved when still valid.
    """
    chan_opts = _channel_options(app)
    tp_opts = _timepoint_options(app)
    fov_opts = _fov_options(app)
    well_opts = _well_options(app)

    if hasattr(app, "_image_table_global_chan_cb"):
        set_combo_values(app._image_table_global_chan_cb, chan_opts)
    if hasattr(app, "_image_table_global_tp_cb"):
        set_combo_values(app._image_table_global_tp_cb, tp_opts)
        set_combo_values(app._image_table_global_fov_cb, fov_opts)

    for cb in getattr(app, "_image_table_row_chan_cbs", None) or []:
        set_combo_values(cb, chan_opts)
    for cb in getattr(app, "_image_table_row_well_cbs", None) or []:
        set_combo_values(cb, well_opts)

    cells = getattr(app, "_image_table_cells", None) or []
    for row in cells:
        for cell in row:
            set_combo_values(cell["well_cb"], well_opts)
            set_combo_values(cell["chan_cb"], chan_opts)
            set_combo_values(cell["tp_cb"], tp_opts)
            set_combo_values(cell["fov_cb"], fov_opts)

    # The LUT/Display row is built once at tab-construction time, when the grid
    # may not exist yet. Rebuild it now so a per-row box appears for each grid
    # row. ``build_lut_row`` preserves prior per-row values across rebuilds.
    cells = getattr(app, "_image_table_cells", None) or []
    existing = set((getattr(app, "_image_table_lut", None) or {}).keys())
    if set(range(len(cells))) != existing:
        image_table_rebuild_lut_row(app)

    # Keep the sidebar channel multi-select in sync with the dataset's channels.
    image_table_refresh_channel_picker(app)


# ── Selector-grid build / rebuild ────────────────────────────────────────────


def image_table_apply_dimensions(app) -> None:
    """Read rows / cols spinners and rebuild the selector grid."""
    rows = int(app._image_table_rows_spin.value())
    cols = int(app._image_table_cols_spin.value())
    app._app_state.image_table.rows = rows
    app._app_state.image_table.cols = cols
    image_table_rebuild_grid(app)


def image_table_rebuild_grid(app) -> None:
    """Rebuild the selector grid (rows × cols of cell groupboxes) plus the
    per-row channel + LUT-colour column at column 0."""
    from well_viewer.features.image_browser.image_table.grid_view import build_image_table_grid

    rows = int(getattr(app._app_state.image_table, "rows", 2) or 2)
    cols = int(getattr(app._app_state.image_table, "cols", 3) or 3)

    chan_opts = _channel_options(app)
    tp_opts = _timepoint_options(app)
    fov_opts = _fov_options(app)
    well_opts = _well_options(app)

    cells, row_chan_cbs, row_well_cbs = build_image_table_grid(
        app,
        rows=rows,
        cols=cols,
        chan_opts=chan_opts,
        tp_opts=tp_opts,
        fov_opts=fov_opts,
        well_opts=well_opts,
        on_apply_row_well=image_table_apply_row_well,
        on_apply_row_channel=image_table_apply_row_channel,
        on_remove_row=image_table_remove_row,
        on_remove_col=image_table_remove_col,
    )

    app._image_table_cells = cells
    app._image_table_row_chan_cbs = row_chan_cbs
    app._image_table_row_well_cbs = row_well_cbs
    # The per-row colormap selectors live in the per-row table now and are
    # (re)created + stored on app by image_table_rebuild_lut_row -> build_lut_row.

    # Refresh the per-row levels/display table whenever any cell's channel
    # changes so the row labels + Fit pooling track the new assignment. This
    # only rebuilds the editor widgets — it does NOT render (Generate does).
    for row in cells:
        for cell in row:
            cb = cell.get("chan_cb")
            if cb is None:
                continue
            cb.currentIndexChanged.connect(
                lambda _i, a=app: image_table_rebuild_lut_row(a)
            )

    image_table_rebuild_lut_row(app)












def image_table_apply_global(app, field: str) -> None:
    """Copy a global dropdown's current value into every cell's matching combo.

    ``field`` is one of "chan", "tp", "fov". For "chan" the per-row channel
    combos are also synced and the LUT row is rebuilt so the host's per-
    channel min/max boxes track the new global channel.
    """
    if field == "chan":
        src = getattr(app, "_image_table_global_chan_cb", None)
        key = "chan_cb"
    elif field == "tp":
        src = getattr(app, "_image_table_global_tp_cb", None)
        key = "tp_cb"
    elif field == "fov":
        src = getattr(app, "_image_table_global_fov_cb", None)
        key = "fov_cb"
    else:
        return
    if src is None:
        return
    value = src.currentText()
    if not value:
        return
    cells = getattr(app, "_image_table_cells", None) or []
    for row in cells:
        for cell in row:
            cb = cell.get(key)
            if cb is None:
                continue
            cb.blockSignals(True)
            if cb.findText(value) >= 0:
                cb.setCurrentText(value)
            cb.blockSignals(False)
    if field == "chan":
        for cb in getattr(app, "_image_table_row_chan_cbs", None) or []:
            cb.blockSignals(True)
            if cb.findText(value) >= 0:
                cb.setCurrentText(value)
            cb.blockSignals(False)
        image_table_rebuild_lut_row(app)


def image_table_apply_row_well(app, row_idx: int) -> None:
    """Copy the per-row well selector's value into every cell in that row.

    Mirrors ``image_table_apply_row_channel`` for the well axis: lets the
    user pick a single well for an entire row without clicking each cell.
    """
    row_cbs = getattr(app, "_image_table_row_well_cbs", None) or []
    cells = getattr(app, "_image_table_cells", None) or []
    if not (0 <= row_idx < len(row_cbs)) or not (0 <= row_idx < len(cells)):
        return
    value = row_cbs[row_idx].currentText()
    if not value:
        return
    for cell in cells[row_idx]:
        cb = cell.get("well_cb")
        if cb is None:
            continue
        cb.blockSignals(True)
        if cb.findText(value) >= 0:
            cb.setCurrentText(value)
        cb.blockSignals(False)


def image_table_apply_row_channel(app, row_idx: int) -> None:
    """Copy the per-row channel selector's value into every cell in that row.

    Cell-level channel dropdowns remain usable for one-off overrides; this
    only fires when the per-row combo changes.
    """
    row_cbs = getattr(app, "_image_table_row_chan_cbs", None) or []
    cells = getattr(app, "_image_table_cells", None) or []
    if not (0 <= row_idx < len(row_cbs)) or not (0 <= row_idx < len(cells)):
        return
    value = row_cbs[row_idx].currentText()
    if not value:
        return
    for cell in cells[row_idx]:
        cb = cell.get("chan_cb")
        if cb is None:
            continue
        cb.blockSignals(True)
        if cb.findText(value) >= 0:
            cb.setCurrentText(value)
        cb.blockSignals(False)
    image_table_rebuild_lut_row(app)


def image_table_distribute_timepoints(app) -> None:
    """Walk the available timepoints and assign one to each column, per row.

    Each row gets the same timepoint sequence across its cells, so multi-row
    tables can compare different wells/channels at matching timepoints. Each
    row is anchored on its own first cell's well/channel/FOV (preserving any
    per-row well/channel selections made via ``Distribute Wells`` or the
    per-row dropdowns) so distinct rows keep their own well assignments.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a grid first (set rows/cols).")
        return

    tps = _timepoint_options(app)
    if not tps:
        app._set_status("Image Table: no timepoints in pipeline_info.json.")
        return

    def _set(cb, text):
        if not text:
            return
        cb.blockSignals(True)
        try:
            if cb.findText(text) >= 0:
                cb.setCurrentText(text)
        finally:
            cb.blockSignals(False)

    assignments = 0
    for row in cells:
        if not row:
            continue
        anchor = row[0]
        anchor_well = anchor["well_cb"].currentText().strip()
        anchor_chan = anchor["chan_cb"].currentText().strip()
        anchor_fov = anchor["fov_cb"].currentText().strip()
        for c, cell in enumerate(row):
            if c >= len(tps):
                break
            _set(cell["well_cb"], anchor_well)
            _set(cell["chan_cb"], anchor_chan)
            _set(cell["fov_cb"], anchor_fov)
            _set(cell["tp_cb"], tps[c])
            assignments += 1

    image_table_rebuild_lut_row(app)
    app._set_status(
        f"Image Table: distributed {min(len(tps), len(cells[0]) if cells else 0)} "
        f"timepoint(s) across {len(cells)} row(s) ({assignments} cells filled)."
    )


# ── Smart layout builder ──────────────────────────────────────────────────────
#
# Declare what varies down the ROWS and across the COLUMNS (Channels / Wells /
# Timepoints / FOVs); one Build auto-sizes the grid to the selection and fills
# every cell, holding the off-axis dimensions fixed. This replaces the manual
# "set rows/cols, then assign every dropdown" workflow for the common views
# (channels × timepoints, channels × wells, wells × timepoints, …).

_AXIS_LABEL = {"channel": "Channels", "well": "Wells",
               "timepoint": "Timepoints", "fov": "FOVs"}
_AXIS_MAX = 12  # grid is capped at 12×12; longer axes are truncated








def _apply_row_channel_styling(app, channels: List[str]) -> None:
    """When rows are channels, mirror each channel onto its row-0 channel combo
    and default every row to grayscale (Greys). The user can recolor any row
    via its LutSelector — defaulting to grayscale makes channels comparable at a
    glance and avoids implying meaning (e.g. green = GFP) that the data may not
    carry."""
    row_chan = getattr(app, "_image_table_row_chan_cbs", None) or []
    row_lut = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    for r, chan in enumerate(channels):
        if r < len(row_chan) and row_chan[r] is not None:
            cb = row_chan[r]
            cb.blockSignals(True)
            if cb.findText(chan) >= 0:
                cb.setCurrentText(chan)
            cb.blockSignals(False)
        if r < len(row_lut) and row_lut[r] is not None and hasattr(row_lut[r], "setLut"):
            sel = row_lut[r]
            sel.blockSignals(True)
            try:
                sel.setLut("Greys")
            except Exception:  # noqa: BLE001 — colormap styling is best-effort
                swallowed()  # was: pass (silent except)
            sel.blockSignals(False)




def image_table_build_layout(app) -> None:
    """Auto-size the grid to the chosen row/col axes and fill every cell."""
    row_axis = _current_axis(app, "row")
    col_axis = _current_axis(app, "col")
    if row_axis == col_axis:
        app._set_status("Image Table: choose two different axes for rows and columns.")
        return

    row_vals = _axis_values(app, row_axis)
    col_vals = _axis_values(app, col_axis)
    if not row_vals:
        app._set_status(f"Image Table: nothing on the {_AXIS_LABEL[row_axis]} axis "
                        f"(select some in the sidebar).")
        return
    if not col_vals:
        app._set_status(f"Image Table: nothing on the {_AXIS_LABEL[col_axis]} axis "
                        f"(select some in the sidebar).")
        return

    row_vals = row_vals[:_AXIS_MAX]
    col_vals = col_vals[:_AXIS_MAX]
    rows, cols = len(row_vals), len(col_vals)

    # Size the grid (sync the spinners) and rebuild the empty selector cells.
    app._app_state.image_table.rows = rows
    app._app_state.image_table.cols = cols
    for attr, val in (("_image_table_rows_spin", rows), ("_image_table_cols_spin", cols)):
        spin = getattr(app, attr, None)
        if spin is not None:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
    image_table_rebuild_grid(app)

    fixed = _fixed_off_axis(app, {row_axis, col_axis})
    key_of = {"well": "well_cb", "channel": "chan_cb",
              "timepoint": "tp_cb", "fov": "fov_cb"}

    def _set(cb, text):
        if cb is None or not text:
            return
        cb.blockSignals(True)
        try:
            if cb.findText(text) >= 0:
                cb.setCurrentText(text)
        finally:
            cb.blockSignals(False)

    cells = getattr(app, "_image_table_cells", None) or []
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            vals = _layout_cell_values(row_axis, col_axis, row_vals, col_vals, fixed, r, c)
            for dim, ckey in key_of.items():
                _set(cell.get(ckey), vals[dim])

    if row_axis == "channel":
        _apply_row_channel_styling(app, row_vals)

    image_table_rebuild_lut_row(app)
    app._set_status(
        f"Image Table: built {rows}×{cols} selector grid — rows = {_AXIS_LABEL[row_axis]}, "
        f"cols = {_AXIS_LABEL[col_axis]}. Press Generate to render."
    )


def image_table_apply_preset(app, row_axis: str, col_axis: str) -> None:
    """Set the row/col axis dropdowns to a common pairing, then Build."""
    for which, axis in (("row", row_axis), ("col", col_axis)):
        cb = getattr(app, f"_image_table_{which}_axis_cb", None)
        if cb is not None:
            idx = cb.findData(axis)
            cb.blockSignals(True)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
        setattr(app, f"_image_table_{which}_axis", axis)
    image_table_build_layout(app)


# ── Channel multi-select (sidebar) ────────────────────────────────────────────


def _update_channel_btn_text(app) -> None:
    """Reflect the active-channel set on the layout-row dropdown button."""
    btn = getattr(app, "_image_table_channel_btn", None)
    if btn is None:
        return
    opts = _channel_options(app)
    sel = getattr(app._app_state.image_table, "active_channels", None) or set()
    ordered = [c for c in opts if c in sel]
    if not ordered or len(ordered) == len(opts):
        btn.setText("Channels: all")
    elif len(ordered) <= 2:
        btn.setText("Channels: " + ", ".join(ordered))
    else:
        btn.setText(f"Channels: {len(ordered)} selected")


def image_table_refresh_channel_picker(app) -> None:
    """Rebuild the layout-row channel dropdown menu from the dataset's
    channels, preserving valid prior selections."""
    from PySide6.QtGui import QAction

    menu = getattr(app, "_image_table_channel_menu", None)
    if menu is None:
        return
    opts = _channel_options(app)
    active = getattr(app._app_state.image_table, "active_channels", None) or set()
    app._app_state.image_table.active_channels = {c for c in active if c in opts}

    menu.clear()
    act_all = menu.addAction("Select all")
    act_all.triggered.connect(lambda _=False: image_table_set_all_channels(app, True))
    act_none = menu.addAction("Clear")
    act_none.triggered.connect(lambda _=False: image_table_set_all_channels(app, False))
    menu.addSeparator()
    for ch in opts:
        act = QAction(ch, menu)
        act.setCheckable(True)
        # Set state BEFORE wiring so building the menu doesn't fire toggled.
        act.setChecked(ch in app._app_state.image_table.active_channels)
        act.toggled.connect(lambda checked, c=ch: image_table_toggle_channel(app, c, checked))
        menu.addAction(act)
    _update_channel_btn_text(app)


def image_table_toggle_channel(app, channel: str, checked: bool) -> None:
    """Add/remove a single channel from the active set (menu item toggled)."""
    sel = set(getattr(app._app_state.image_table, "active_channels", None) or set())
    if checked:
        sel.add(channel)
    else:
        sel.discard(channel)
    app._app_state.image_table.active_channels = sel
    _update_channel_btn_text(app)


def image_table_set_all_channels(app, on: bool) -> None:
    """Select-all / clear from the dropdown header actions."""
    app._app_state.image_table.active_channels = set(_channel_options(app)) if on else set()
    image_table_refresh_channel_picker(app)  # rebuild to refresh checkmarks


# ── Add row (clone an existing row, vary one dimension) ───────────────────────










def image_table_add_varied_row(app, *, src_idx: int, dim: str, mode: str, value: str) -> None:
    """Append a new row cloned from row *src_idx*, varying one dimension.

    Uniform mode sets *dim* to *value* across the whole new row; Step mode
    advances each cell's value for *dim* by one (clamped to the last available
    value). Other dimensions are copied as-is. Builds the selector row only —
    press Generate to render. Driven by :func:`image_table_open_add_row_dialog`.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a table first, then add a row.")
        return
    dim = dim or "channel"
    mode = mode or "uniform"
    value = (value or "").strip()

    snap = _snapshot_cells(app)
    if not (0 <= src_idx < len(snap)):
        src_idx = len(snap) - 1
    src_row = snap[src_idx]
    opts = _all_dim_options(app, dim)

    new_row: List[Dict[str, str]] = []
    for cell_vals in src_row:
        nv = dict(cell_vals)
        if mode == "uniform":
            if value:
                nv[dim] = value
        else:  # step: advance this cell's value by one
            cur = cell_vals.get(dim, "")
            if opts:
                try:
                    i = opts.index(cur)
                except ValueError:
                    i = -1
                nv[dim] = opts[min(i + 1, len(opts) - 1)] if i >= 0 else opts[0]
        new_row.append(nv)

    # Grow the grid by one row, rebuild the empty cells, then restore the old
    # rows plus the freshly-built one.
    new_rows = len(snap) + 1
    app._app_state.image_table.rows = new_rows
    spin = getattr(app, "_image_table_rows_spin", None)
    if spin is not None:
        spin.blockSignals(True)
        spin.setValue(new_rows)
        spin.blockSignals(False)
    image_table_rebuild_grid(app)

    full = snap + [new_row]
    for r, row in enumerate(getattr(app, "_image_table_cells", None) or []):
        if r >= len(full):
            break
        for c, cell in enumerate(row):
            if c < len(full[r]):
                _write_cell(cell, full[r][c])

    # When the varied dimension is the channel and the whole new row shares one
    # channel (uniform), mirror it onto the row's channel combo + colormap.
    if dim == "channel" and mode == "uniform" and value:
        _apply_row_channel_styling_for_row(app, new_rows - 1, value)

    image_table_rebuild_lut_row(app)
    verb = f"set {dim} = {value}" if mode == "uniform" else f"stepped {dim}"
    app._set_status(
        f"Image Table: added row {new_rows} cloned from row {src_idx + 1} "
        f"({verb}). Press Generate to render."
    )


def image_table_open_add_row_dialog(app) -> None:
    """Modal dialog to clone an existing row, varying one dimension.

    Replaces the inline Add-row combo strip — collects source row / dimension /
    mode / value here (value list is per-dimension, enabled only in Uniform
    mode) and calls :func:`image_table_add_varied_row`.
    """
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout

    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a table first, then add a row.")
        return

    dlg = QDialog(app)
    dlg.setWindowTitle("Image Table — Add row")
    form = QFormLayout(dlg)

    src_cb = QComboBox(dlg)
    for i in range(len(cells)):
        src_cb.addItem(f"Row {i + 1}", i)
    src_cb.setCurrentIndex(src_cb.count() - 1)
    src_cb.setToolTip("Existing row to clone.")
    form.addRow("Clone row:", src_cb)

    dim_cb = QComboBox(dlg)
    for label, key in (("Channel", "channel"), ("Timepoint", "timepoint"),
                       ("FOV", "fov"), ("Well", "well")):
        dim_cb.addItem(label, key)
    form.addRow("Vary:", dim_cb)

    mode_cb = QComboBox(dlg)
    mode_cb.addItem("Uniform → one value", "uniform")
    mode_cb.addItem("Step +1 per cell", "step")
    mode_cb.setToolTip(
        "Uniform: set the chosen dimension to one value across the new row.\n"
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
    image_table_add_varied_row(
        app,
        src_idx=int(src_cb.currentData() or 0),
        dim=str(dim_cb.currentData() or "channel"),
        mode=str(mode_cb.currentData() or "uniform"),
        value=val_cb.currentText().strip(),
    )


def _apply_row_channel_styling_for_row(app, row_idx: int, channel: str) -> None:
    """Set one row's channel combo + default the LUT to grayscale (used by
    Add-row). Matches ``_apply_row_channel_styling``: rows default to Greys
    regardless of channel; user can recolor a row via its LutSelector."""
    row_chan = getattr(app, "_image_table_row_chan_cbs", None) or []
    row_lut = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    if 0 <= row_idx < len(row_chan) and row_chan[row_idx] is not None:
        cb = row_chan[row_idx]
        cb.blockSignals(True)
        if cb.findText(channel) >= 0:
            cb.setCurrentText(channel)
        cb.blockSignals(False)
    if 0 <= row_idx < len(row_lut) and row_lut[row_idx] is not None \
            and hasattr(row_lut[row_idx], "setLut"):
        sel = row_lut[row_idx]
        sel.blockSignals(True)
        try:
            sel.setLut("Greys")
        except Exception:  # noqa: BLE001
            swallowed()  # was: pass (silent except)
        sel.blockSignals(False)


def image_table_load_heatmap_layout(app) -> None:
    """Adopt the Heat Map tab's well configuration (rows, cols, per-cell well).

    Pulls the single sidebar layout maintained by the heat-map tab
    (``views.heatmap_layout_sidebar_view``), resizes the Image Table's
    selector grid to match, and writes each cell's well into the
    corresponding per-cell well dropdown. Cells that the heat map leaves
    empty are cleared on the Image Table side — otherwise they would stick
    on the first well token (the ``QComboBox.addItems`` default of A1)
    that ``image_table_rebuild_grid`` had just put there.
    """
    from well_viewer.features.heatmap.sidebar_view import (
        SIDEBAR_LAYOUT_NAME,
    )

    layouts = list(getattr(app, "_heatmap_layouts", []) or [])
    layout = next((l for l in layouts if l.name == SIDEBAR_LAYOUT_NAME), None)
    if layout is None:
        app._set_status("Image Table: no Heat Map layout to load.")
        return

    rows_spin = getattr(app, "_image_table_rows_spin", None)
    cols_spin = getattr(app, "_image_table_cols_spin", None)
    if rows_spin is None or cols_spin is None:
        return

    target_rows = min(rows_spin.maximum(), max(rows_spin.minimum(), int(layout.rows)))
    target_cols = min(cols_spin.maximum(), max(cols_spin.minimum(), int(layout.cols)))
    clamped = (target_rows != int(layout.rows)) or (target_cols != int(layout.cols))

    # Update spinners without triggering the rebuild twice; we call
    # image_table_apply_dimensions explicitly below so rows/cols + grid
    # land in sync regardless of whether the spinner values actually changed.
    for spin, val in ((rows_spin, target_rows), (cols_spin, target_cols)):
        blocked = spin.blockSignals(True)
        try:
            spin.setValue(val)
        finally:
            spin.blockSignals(blocked)
    app._app_state.image_table.rows = target_rows
    app._app_state.image_table.cols = target_cols
    image_table_rebuild_grid(app)

    cells = getattr(app, "_image_table_cells", None) or []

    # Clear every freshly-rebuilt cell's well first so heat-map blanks
    # come through as blanks instead of the A1 default.
    for row in cells:
        for cell in row:
            cb = cell.get("well_cb")
            if cb is None:
                continue
            cb.blockSignals(True)
            try:
                cb.setCurrentIndex(-1)
            finally:
                cb.blockSignals(False)

    placed = 0
    for (r, c), wells in layout.cells.items():
        if not (0 <= r < len(cells)) or not (0 <= c < len(cells[r])):
            continue
        token = (wells[0] if wells else "").strip()
        if not token:
            continue
        cb = cells[r][c].get("well_cb")
        if cb is None:
            continue
        cb.blockSignals(True)
        try:
            if cb.findText(token) >= 0:
                cb.setCurrentText(token)
                placed += 1
        finally:
            cb.blockSignals(False)

    msg = (
        f"Image Table: loaded Heat Map layout ({target_rows}×{target_cols}, "
        f"{placed} well(s) placed)."
    )
    if clamped:
        msg += (
            f" Clamped from {int(layout.rows)}×{int(layout.cols)} to fit "
            f"the Image Table grid limits."
        )
    app._set_status(msg)


def image_table_distribute_wells(app) -> None:
    """Walk active wells row-wise and assign one to each selector cell.

    Special case: when the table has exactly twice as many cells as there
    are active wells, the assignment is duplicated — each well appears in
    two cells (typically the top half then the bottom half, when the
    table is wider than tall). This is convenient for laying out two
    rows of complementary channels for the same set of wells.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not cells:
        app._set_status("Image Table: build a grid first (set rows/cols and click Apply).")
        return
    active = sorted(app._app_state.image_table.active_wells, key=app._parse_rc)
    if not active:
        app._set_status("Image Table: no active wells in the sidebar picker.")
        return

    flat_cells = [(r, c, cell) for r, row in enumerate(cells) for c, cell in enumerate(row)]
    total_cells = len(flat_cells)
    n_active = len(active)
    duplicated = total_cells == 2 * n_active

    assignments = 0
    for flat_idx, (_r, _c, cell) in enumerate(flat_cells):
        if duplicated:
            tok = active[flat_idx % n_active]
        elif flat_idx < n_active:
            tok = active[flat_idx]
        else:
            break
        cb = cell["well_cb"]
        cb.blockSignals(True)
        if cb.findText(tok) >= 0:
            cb.setCurrentText(tok)
        cb.blockSignals(False)
        assignments += 1

    if duplicated:
        app._set_status(
            f"Image Table: distributed {n_active} well(s) twice "
            f"({assignments} cells filled) for complementary-channel layout."
        )
    else:
        app._set_status(
            f"Image Table: distributed {assignments} well(s) into the selector grid."
        )


# ── LUT row ──────────────────────────────────────────────────────────────────


def image_table_rebuild_lut_row(app) -> None:
    """Rebuild the per-row LUT + display editors (one box per grid row)."""
    from well_viewer.features.image_browser.image_table.grid_view import build_lut_row

    build_lut_row(
        app,
        on_auto_lut=image_table_auto_lut,
        lut_color_names=LUT_COLOR_NAMES,
    )




# ── Image loading + caching ──────────────────────────────────────────────────












_ZOOM_MIN = 0.33
_ZOOM_MAX = 4.0


def image_table_set_zoom(app, factor: float, *, relative: bool = False) -> None:
    """Set (or step) the rendered-table zoom factor and re-render.

    ``relative=True`` multiplies the current zoom by *factor* (zoom in/out
    buttons); otherwise *factor* is the absolute level (1.0 = 240 px cells).
    """
    cur = float(getattr(app._app_state.image_table, "zoom", 1.0) or 1.0)
    new = cur * factor if relative else factor
    new = max(_ZOOM_MIN, min(_ZOOM_MAX, new))
    app._app_state.image_table.zoom = new
    lbl = getattr(app, "_image_table_zoom_lbl", None)
    if lbl is not None:
        lbl.setText(f"{new * 100:.0f}%")
    # Only re-render if a table has already been generated; otherwise just
    # store the level for the next Generate.
    if getattr(app._app_state.image_table, "last_render", None):
        image_table_generate(app)


def image_table_set_thresh_frac(app, value) -> None:
    """Edit the active channel's ON/OFF intensity threshold (ThreshFracOn) from
    the Image Table controls row, then re-composite so the per-row "≥ Thr"
    binary overlay reflects it.

    Writes through ``set_thresh_frac_on`` (the single source of truth shared with
    the Cell Gating tab and the other quick-edits). Re-renders only when a table
    has already been generated — the cached images are reused, so it's the same
    cheap re-composite the zoom control does; before the first Generate it just
    stores the value.
    """
    app._set_thresh_frac_on(value)
    if getattr(app._app_state.image_table, "last_render", None):
        image_table_generate(app)


# ── Per-cell mouse handling: hover pixel readout + crop drag ────────────────




def _install_cell_events(
    app, label, crop_tool, well: str, chan: str, tp: str, fov: str,
) -> None:
    """Wire press/move/release on a cell's image label.

    Replaces ``CropTool.install_events`` so the same label can both serve
    as the crop-tool's drag target and report the intensity of the pixel
    under the cursor on every move.
    """
    from PySide6.QtCore import QObject, QEvent
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QToolTip

    label.setMouseTracking(True)
    if crop_tool is not None:
        label.setCursor(_Qt.CrossCursor)

    def _show_tip(_lbl, ev, text: str) -> None:
        if not text:
            QToolTip.hideText()
            return
        try:
            gp = ev.globalPosition().toPoint()
        except Exception:
            try:
                gp = _lbl.mapToGlobal(ev.position().toPoint())
            except Exception:
                QToolTip.hideText()
                return
        QToolTip.showText(gp, text, _lbl)

    def _label_to_image_xy(_lbl, lx: int, ly: int):
        """Map label-local coords to source-image (x, y). Returns None when
        cursor is outside the rendered pixmap area."""
        try:
            import numpy as _np
        except Exception:
            return None
        pm = _lbl.pixmap()
        arr = getattr(_lbl, "_raw_arr", None)
        if arr is None or pm is None:
            return None
        if hasattr(pm, "isNull") and pm.isNull():
            return None
        pw, ph = pm.width(), pm.height()
        if pw <= 0 or ph <= 0:
            return None
        a = _np.asarray(arr)
        full_h, full_w = a.shape[:2]
        crop = getattr(_lbl, "_crop", None)
        if crop is not None:
            y0, x0, y1, x1 = crop
            view_h = max(1, int(y1) - int(y0))
            view_w = max(1, int(x1) - int(x0))
        else:
            y0 = x0 = 0
            view_h, view_w = full_h, full_w
        lw, lh = _lbl.width(), _lbl.height()
        offset_x = (lw - pw) // 2
        offset_y = (lh - ph) // 2
        if not (offset_x <= lx < offset_x + pw and offset_y <= ly < offset_y + ph):
            return None
        px = lx - offset_x
        py = ly - offset_y
        img_x = int(x0 + px * view_w / pw)
        img_y = int(y0 + py * view_h / ph)
        img_x = max(0, min(full_w - 1, img_x))
        img_y = max(0, min(full_h - 1, img_y))
        return (img_x, img_y)

    def _update_hover(_lbl, ev) -> None:
        try:
            arr = getattr(_lbl, "_raw_arr", None)
            if arr is None:
                _show_tip(_lbl, ev, "")
                return
            pos = ev.position()
            xy = _label_to_image_xy(_lbl, int(pos.x()), int(pos.y()))
            if xy is None:
                _show_tip(_lbl, ev, "")
                return
            x, y = xy
            intensity = _format_pixel_intensity(arr, y, x)
            if not intensity:
                _show_tip(_lbl, ev, "")
                return
            prefix = f"{well} {chan.upper()} T:{tp}"
            if fov:
                prefix += f" FOV:{fov}"
            _show_tip(_lbl, ev, f"{prefix}  ({x}, {y}) = {intensity}")
        except Exception:
            QToolTip.hideText()

    class _CellEventFilter(QObject):
        def eventFilter(self, obj, ev):
            t = ev.type()
            if t == QEvent.MouseMove:
                _update_hover(obj, ev)
                if crop_tool is not None:
                    crop_tool.update_drag(ev)
            elif t == QEvent.MouseButtonPress:
                if crop_tool is not None:
                    crop_tool.begin_drag(obj, ev)
            elif t == QEvent.MouseButtonRelease:
                if crop_tool is not None:
                    crop_tool.end_drag(ev)
            elif t == QEvent.Leave:
                QToolTip.hideText()
            return False

    filt = _CellEventFilter(label)
    label.installEventFilter(filt)
    # Keep a reference so the filter isn't garbage-collected.
    label._image_table_event_filter = filt  # type: ignore[attr-defined]


# ── Binary render helpers (mask vs. above-threshold) ─────────────────────────






def _refresh_lut_fields_from_render(app, rendered) -> None:
    """Pool the actual min/max per ROW from the just-rendered arrays and write
    the numbers into that row's LUT box, except rows the user has overridden.

    Each row's entry carries an ``auto`` flag (True until the user edits its
    min/max); only auto rows get refreshed, so manual overrides stick.
    """
    luts = getattr(app, "_image_table_lut", None) or {}
    cells = getattr(app, "_image_table_cells", None) or []
    if not luts or not cells:
        return
    try:
        import numpy as _np
    except Exception:
        return

    for r, row in enumerate(cells):
        entry = luts.get(r)
        if not isinstance(entry, dict) or not entry.get("auto", True):
            continue  # missing or user-edited — leave alone
        lo: Optional[float] = None
        hi: Optional[float] = None
        for c, _cell in enumerate(row):
            arr = rendered.get((r, c))
            if arr is None or getattr(arr, "ndim", 2) >= 3:
                continue  # skip empty cells and pre-rendered RGB overlays
            a = _np.asarray(arr, dtype=_np.float32)
            mn, mx = float(a.min()), float(a.max())
            lo = mn if lo is None else min(lo, mn)
            hi = mx if hi is None else max(hi, mx)
        if lo is None or hi is None:
            continue
        if hi <= lo:
            hi = lo + 1.0
        for key, val in (("min", lo), ("max", hi)):
            le = entry.get(key)
            if le is None:
                continue
            blocked = le.blockSignals(True)
            try:
                le.setText(f"{val:.4g}")
            finally:
                le.blockSignals(blocked)


# ── Generate (render image table below the selector) ─────────────────────────


def image_table_generate(app) -> None:
    """Load images for every cell and lay out the rendered image table."""
    from well_viewer.core.ui_helpers import make_fluor_thumb, make_overlay_thumb
    from well_viewer.core.ui_helpers import clear_layout

    cells = getattr(app, "_image_table_cells", None) or []
    grid = app._image_table_render_grid
    clear_layout(grid)
    if not cells:
        app._set_status("Image Table: build a grid first (set rows/cols and click Apply).")
        return

    # Once the user has triggered a Generate, hide the EmptyState placeholder.
    empty = getattr(app, "_image_table_empty_state", None)
    if empty is not None:
        empty.setVisible(False)

    cache: Dict[Tuple[str, str, str], Dict] = {}
    rendered: Dict[Tuple[int, int], Any] = {}
    crop_tool = getattr(app, "_image_table_crop_tool", None)
    # Per-cell thumbnail size, scaled by the zoom factor (240 px = 1.0×).
    zoom = float(getattr(app._app_state.image_table, "zoom", 1.0) or 1.0)
    sz = max(80, int(round(240 * zoom)))

    for r, row in enumerate(cells):
        # Display options (top-hat / boundaries / binary) + LUT are per ROW —
        # read from this row's box so two rows on the same channel stay
        # independent (see grid_view.build_lut_row).
        use_tophat, show_boundaries, binary_mode = _row_display_opts(app, r)
        for c, cell in enumerate(row):
            well = cell["well_cb"].currentText().strip()
            chan = cell["chan_cb"].currentText().strip()
            tp = cell["tp_cb"].currentText().strip()
            fov = cell["fov_cb"].currentText().strip()

            arr = None
            ratio = _find_ratio_for_channel(app, chan) if chan else None
            if well and chan and tp:
                if ratio is not None:
                    arr = _load_ratio_array(app, cache, well, ratio, tp, fov, use_tophat=use_tophat)
                else:
                    arr = _load_array(app, cache, well, chan, tp, fov, use_tophat=use_tophat)
            rendered[(r, c)] = arr

            lo, hi = _parse_lut(app, r)
            cell_widget = QWidget()
            cl = QVBoxLayout(cell_widget)
            cl.setContentsMargins(2, 2, 2, 2)
            cl.setSpacing(2)

            if well and chan and tp:
                header_text = f"{well}  {chan.upper()}  T:{tp}"
                if fov:
                    header_text += f"  FOV:{fov}"
            else:
                header_text = "(unset)"
            header = QLabel(header_text, cell_widget)
            header.setAlignment(Qt.AlignCenter)
            cl.addWidget(header)

            img_label = QLabel(cell_widget)
            img_label.setAlignment(Qt.AlignCenter)
            # Pin to the rendered pixmap size so the surrounding QLabel doesn't
            # expand and leave large gaps around the image.
            img_label.setFixedSize(sz, sz)
            if arr is not None:
                # Apply the shared crop (no-op when not set) and render.
                cropped = crop_tool.apply_to_array(arr) if crop_tool else arr
                is_overlay = _is_nuc_seg(chan) or getattr(arr, "ndim", 2) >= 3
                if is_overlay:
                    # Pre-rendered RGB overlay: tint and per-channel LUT
                    # don't apply, so feed it straight to the overlay
                    # thumbnailer (which still respects lo/hi when set).
                    pix = make_overlay_thumb(cropped, sz, sz, lo, hi)
                elif binary_mode == "mask":
                    # Every segmented cell white on black.
                    pix = _render_mask_binary(app, cache, well, tp, fov, crop_tool, sz)
                    if pix is None:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(app, r))
                elif binary_mode == "threshold":
                    # Only cells above the active ON threshold white on black.
                    pix = _render_threshold_binary(app, cache, well, chan, tp, fov, crop_tool, sz)
                    if pix is None:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(app, r))
                elif show_boundaries:
                    # Overlay white cell-boundary outlines on the fluorescence image.
                    mask_arr = _load_mask_array(app, cache, well, tp, fov)
                    if mask_arr is not None:
                        cm = crop_tool.apply_to_array(mask_arr) if crop_tool else mask_arr
                        pix = _render_with_boundary_overlay(
                            cropped, sz, sz, lo, hi,
                            _row_cmap_name(app, r), cm,
                        )
                    else:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(app, r))
                else:
                    pix = make_fluor_thumb(
                        cropped, sz, sz, lo, hi,
                        cmap=_row_cmap_name(app, r),
                    )
                # CropTool needs the FULL source array + the active crop on
                # the label so label-pixel → image-pixel coord conversion
                # works correctly even when the label already shows a crop.
                img_label._raw_arr = arr  # type: ignore[attr-defined]
                img_label._crop = crop_tool.crop if crop_tool else None  # type: ignore[attr-defined]
                _install_cell_events(app, img_label, crop_tool, well, chan, tp, fov)
                if pix is not None:
                    img_label.setPixmap(pix)
                else:
                    img_label.setText("(render failed)")
            else:
                img_label.setText("(no image)")
            cl.addWidget(img_label)

            grid.addWidget(cell_widget, r, c)

    app._app_state.image_table.last_render = rendered
    app._app_state.image_table.image_cache = cache
    # Fill the per-channel LUT min/max boxes with the actual pooled numbers so
    # the user sees real values instead of "auto" (skips manually-edited rows).
    _refresh_lut_fields_from_render(app, rendered)
    n = sum(1 for v in rendered.values() if v is not None)
    app._set_status(f"Image Table: generated {n} of {len(rendered)} cell(s).")
    if n == 0 and rendered:
        # Zero hits across the whole grid means key drift somewhere — log
        # both the (tp, fov) values the user picked and a sample of the
        # available cache keys for the first cached well so the format
        # mismatch is visible.
        try:
            import logging as _logging
            _log = _logging.getLogger("well_viewer.image_table")
            requested = []
            for r, row in enumerate(cells):
                for c, cell in enumerate(row):
                    requested.append((
                        r, c,
                        cell["well_cb"].currentText().strip(),
                        cell["chan_cb"].currentText().strip(),
                        cell["tp_cb"].currentText().strip(),
                        cell["fov_cb"].currentText().strip(),
                    ))
            for key, fluor in cache.items():
                sample_keys = list(fluor.keys())[:8]
                _log.warning(
                    "Image Table generated 0/%d; well=%r channel=%r "
                    "requested cells (r, c, well, chan, tp, fov)=%r "
                    "available keys (sample, up to 8): %r",
                    len(rendered), key[0], key[1], requested[:4], sample_keys,
                )
                break
        except Exception:
            swallowed()  # was: pass (silent except)


def image_table_auto_lut(app, row_idx: int) -> None:
    """Pool min/max across grid row *row_idx*'s loaded cells; write + redraw.

    Per-row Fit: pools the image range of the cells in this row (whatever
    channel each shows — normally the one row channel), honouring the row's
    own top-hat toggle, and writes the result into the row's LUT box.
    """
    cells = getattr(app, "_image_table_cells", None) or []
    if not (0 <= row_idx < len(cells)):
        return
    rendered = getattr(app._app_state.image_table, "last_render", None) or {}
    cache = getattr(app._app_state.image_table, "image_cache", None)
    if cache is None:
        cache = {}
        app._app_state.image_table.image_cache = cache
    use_tophat, _show_b, _bm = _row_display_opts(app, row_idx)

    try:
        import numpy as _np
    except Exception:
        app._set_status("Image Table: numpy unavailable; cannot compute auto LUT.")
        return

    pooled_min: Optional[float] = None
    pooled_max: Optional[float] = None
    r = row_idx
    for c, cell in enumerate(cells[r]):
        chan = cell["chan_cb"].currentText().strip()
        if not chan:
            continue
        arr = rendered.get((r, c))
        if arr is None:
            # Lazy-load if Generate hasn't been clicked yet.
            well = cell["well_cb"].currentText().strip()
            tp = cell["tp_cb"].currentText().strip()
            fov = cell["fov_cb"].currentText().strip()
            if well and tp and fov:
                arr = _load_array(app, cache, well, chan.upper(), tp, fov, use_tophat=use_tophat)
        if arr is None:
            continue
        a = _np.asarray(arr, dtype=_np.float32)
        mn = float(a.min())
        mx = float(a.max())
        pooled_min = mn if pooled_min is None else min(pooled_min, mn)
        pooled_max = mx if pooled_max is None else max(pooled_max, mx)

    if pooled_min is None or pooled_max is None:
        app._set_status(
            f"Image Table: no loaded images for row {row_idx + 1}; cannot auto-LUT."
        )
        return
    if pooled_max <= pooled_min:
        pooled_max = pooled_min + 1.0

    luts = getattr(app, "_image_table_lut", None) or {}
    entry = luts.get(row_idx)
    if entry is None:
        return
    # "Fit" re-enables auto-tracking so the boxes follow the data again.
    entry["auto"] = True
    for key, val in (("min", pooled_min), ("max", pooled_max)):
        le = entry.get(key)
        if le is None:
            continue
        blocked = le.blockSignals(True)
        try:
            le.setText(f"{val:.4g}")
        finally:
            le.blockSignals(blocked)
    # No auto-render: Fit only fills the min/max numbers; the explicit Generate
    # button applies them (matches the rest of the per-row table).


# ── Export (transparent background, labels above each image) ─────────────────


# The export cluster (settings dialog, figure builder, PNG/SVG save+copy)
# lives in ``export``. Re-exported so the tab_view toolbar wiring is unchanged.
from well_viewer.features.image_browser.image_table.export import (  # noqa: E402,F401
    _export_opts,
    image_table_open_export_settings,
    _build_export_figure,
    image_table_export,
    image_table_copy_png,
    image_table_copy_svg,
)




# ── Export (transparent background, labels above each image) ─────────────────








