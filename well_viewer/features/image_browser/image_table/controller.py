"""Image Table tab — non-UI controller logic.

These functions are bound onto the runtime ctx (via ``runtime_app.py``)
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

from typing import Any, Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QVBoxLayout, QWidget,
)
from well_viewer.core.safe import swallowed
from well_viewer.core.ui_helpers import set_combo_values
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.core.channel_state_controller import set_thresh_frac_on


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
    _row_channel,
    _row_display_opts,
    _render_mask_binary,
    _render_threshold_binary,
    _render_with_boundary_overlay,
    _row_cmap_name,
    _row_log_scale,
    _row_export_cmap,
    _row_tint,
    _tint_from_colormap,
    cell_display_rgb,
    composite_additive_rgb,
    composite_column_rgb,
)
# LUT min/max pooling (Fit + post-render refresh) lives in ``lut_ops``; re-exported
# so ``image_table_rebuild_lut_row`` (on_auto_lut) and ``image_table_generate``
# call sites are unchanged.
from well_viewer.features.image_browser.image_table.lut_ops import (  # noqa: E402,F401
    _refresh_lut_fields_from_render,
    image_table_auto_lut,
)




# ── Virtual "Nuc+Seg" channel ───────────────────────────────────────────────
#
# The pipeline pre-renders a per-(FOV, timepoint) overlay PNG that paints
# the segmentation mask outline on top of the nuclear channel. Exposing it
# as a virtual channel here lets the Image Table double as the old Movie
# Montage view: pick "NUC+SEG" in the channel column and Generate loads
# those overlay images instead of a raw fluorescence channel.


# ── Sidebar picker ───────────────────────────────────────────────────────────


def image_table_refresh_picker(ctx) -> None:
    """Push the active-well set onto the sidebar plate and update the count label."""
    plate = ctx.runtime.image_table.sidebar_plate
    if plate is not None:
        plate.setEnabledWells(list(ctx.state.dataset.well_paths.keys()))
        active = sorted(
            (w for w in ctx.state.image_table.active_wells if w in ctx.state.dataset.well_paths),
            key=parse_rc,
        )
        # Tint each well its canonical plate-rank colour so the picker matches
        # the main sidebar / plots ("the plate is the legend") instead of the
        # widget's selection-order fallback palette.
        from well_viewer.domain.selections_model import color_for_well
        plate.clearWellColors()
        plate.setWellColors({tok: color_for_well(tok) for tok in active})
        plate.setSelectedWellIds(active)
    lbl = getattr(ctx.runtime.image_table, "count_lbl", None)
    if lbl is not None:
        n = len(ctx.state.image_table.active_wells)
        lbl.setText(f"{n} well{'s' if n != 1 else ''} active")


def image_table_select_all(ctx) -> None:
    ctx.state.image_table.active_wells = set(ctx.state.dataset.well_paths.keys())
    image_table_refresh_picker(ctx)


def image_table_clear_active(ctx) -> None:
    ctx.state.image_table.active_wells = set()
    image_table_refresh_picker(ctx)


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
    _current_dims,
    _find_ratio_for_channel,
    _fixed_off_axis,
    _fov_options,
    _layout_cell_values,
    _layout_cell_values_nested,
    _product_values,
    _snapshot_cells,
    _timepoint_options,
    _tp_sort_key,
    _well_options,
    _write_cell,
)




















def image_table_repopulate_dropdowns(ctx) -> None:
    """Refresh option lists for the global row, the per-row channel combos, and every cell.

    Called when pipeline info / wells become available after the tab is
    already built. Existing selections are preserved when still valid.
    """
    chan_opts = _channel_options(ctx)
    tp_opts = _timepoint_options(ctx)
    fov_opts = _fov_options(ctx)
    well_opts = _well_options(ctx)

    if ctx.runtime.image_table.global_chan_cb is not None:
        set_combo_values(ctx.runtime.image_table.global_chan_cb, chan_opts)
    if ctx.runtime.image_table.global_tp_cb is not None:
        set_combo_values(ctx.runtime.image_table.global_tp_cb, tp_opts)
        set_combo_values(ctx.runtime.image_table.global_fov_cb, fov_opts)

    for cb in getattr(ctx.runtime.image_table, "row_chan_cbs", None) or []:
        set_combo_values(cb, chan_opts)
    for cb in getattr(ctx.runtime.image_table, "row_well_cbs", None) or []:
        set_combo_values(cb, well_opts)

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    for row in cells:
        for cell in row:
            set_combo_values(cell["well_cb"], well_opts)
            set_combo_values(cell["chan_cb"], chan_opts)
            set_combo_values(cell["tp_cb"], tp_opts)
            set_combo_values(cell["fov_cb"], fov_opts)

    # The LUT/Display row is built once at tab-construction time, when the grid
    # may not exist yet. Rebuild it now so a per-row box appears for each grid
    # row. ``build_lut_row`` preserves prior per-row values across rebuilds.
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    existing = set((getattr(ctx.runtime.image_table, "lut", None) or {}).keys())
    if set(range(len(cells))) != existing:
        image_table_rebuild_lut_row(ctx)

    # Keep the sidebar channel multi-select in sync with the dataset's channels.
    image_table_refresh_channel_picker(ctx)


# ── Selector-grid build / rebuild ────────────────────────────────────────────


def image_table_apply_dimensions(ctx) -> None:
    """Read rows / cols spinners and rebuild the selector grid.

    A manual grid resize exits grouped/tiled mode: the banded LUT-sharing map is
    derived from the Build step's group sizing, so hand-editing rows/cols clears
    it (one LUT row per grid row again) to keep the mapping consistent.
    """
    rows = int(ctx.runtime.image_table.rows_spin.value())
    cols = int(ctx.runtime.image_table.cols_spin.value())
    ctx.state.image_table.rows = rows
    ctx.state.image_table.cols = cols
    ctx.state.image_table.group_size = 0
    ctx.state.image_table.group_row_span = 0
    image_table_rebuild_grid(ctx)


def image_table_rebuild_grid(ctx) -> None:
    """Rebuild the selector grid (rows × cols of cell groupboxes) plus the
    per-row channel + LUT-colour column at column 0."""
    from well_viewer.features.image_browser.image_table.grid_view import build_image_table_grid
    # Deferred to break the controller ↔ grid_ops cycle (grid_ops imports the
    # rebuild helpers from this module); counted by test_deferred_imports.
    from well_viewer.features.image_browser.image_table.grid_ops import (
        image_table_remove_col,
        image_table_remove_row,
    )

    rows = int(getattr(ctx.state.image_table, "rows", 2) or 2)
    cols = int(getattr(ctx.state.image_table, "cols", 3) or 3)

    chan_opts = _channel_options(ctx)
    tp_opts = _timepoint_options(ctx)
    fov_opts = _fov_options(ctx)
    well_opts = _well_options(ctx)

    cells, row_chan_cbs, row_well_cbs = build_image_table_grid(
        ctx,
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

    ctx.runtime.image_table.cells = cells
    ctx.runtime.image_table.row_chan_cbs = row_chan_cbs
    ctx.runtime.image_table.row_well_cbs = row_well_cbs

    # Grid-row → LUT-group map. In a grouped/tiled layout the row axis repeats
    # every ``group_row_span`` grid rows (band by band), so all band-rows of the
    # same channel share one LUT/Display row (shared-per-channel LUT). Non-grouped
    # → identity, one LUT row per grid row. build_lut_row reads this to decide how
    # many editor rows to build, so it must be set BEFORE rebuild_lut_row below.
    span = int(getattr(ctx.state.image_table, "group_row_span", 0) or 0)
    ctx.runtime.image_table.lut_row_map = (
        [r % span for r in range(rows)] if span > 0 else list(range(rows))
    )
    # The per-row colormap selectors live in the per-row table now and are
    # (re)created + stored on ctx by image_table_rebuild_lut_row -> build_lut_row.

    # Refresh the per-row levels/display table whenever any cell's channel
    # changes so the row labels + Fit pooling track the new assignment. This
    # only rebuilds the editor widgets — it does NOT render (Generate does).
    for row in cells:
        for cell in row:
            cb = cell.get("chan_cb")
            if cb is None:
                continue
            cb.currentIndexChanged.connect(
                lambda _i, a=ctx: image_table_rebuild_lut_row(a)
            )

    image_table_rebuild_lut_row(ctx)












def image_table_apply_global(ctx, field: str) -> None:
    """Copy a global dropdown's current value into every cell's matching combo.

    ``field`` is one of "chan", "tp", "fov". For "chan" the per-row channel
    combos are also synced and the LUT row is rebuilt so the host's per-
    channel min/max boxes track the new global channel.
    """
    if field == "chan":
        src = getattr(ctx.runtime.image_table, "global_chan_cb", None)
        key = "chan_cb"
    elif field == "tp":
        src = getattr(ctx.runtime.image_table, "global_tp_cb", None)
        key = "tp_cb"
    elif field == "fov":
        src = getattr(ctx.runtime.image_table, "global_fov_cb", None)
        key = "fov_cb"
    else:
        return
    if src is None:
        return
    value = src.currentText()
    if not value:
        return
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
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
        for cb in getattr(ctx.runtime.image_table, "row_chan_cbs", None) or []:
            cb.blockSignals(True)
            if cb.findText(value) >= 0:
                cb.setCurrentText(value)
            cb.blockSignals(False)
        image_table_rebuild_lut_row(ctx)


def image_table_apply_row_well(ctx, row_idx: int) -> None:
    """Copy the per-row well selector's value into every cell in that row.

    Mirrors ``image_table_apply_row_channel`` for the well axis: lets the
    user pick a single well for an entire row without clicking each cell.
    """
    row_cbs = getattr(ctx.runtime.image_table, "row_well_cbs", None) or []
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
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


def image_table_apply_row_channel(ctx, row_idx: int) -> None:
    """Copy the per-row channel selector's value into every cell in that row.

    Cell-level channel dropdowns remain usable for one-off overrides; this
    only fires when the per-row combo changes.
    """
    row_cbs = getattr(ctx.runtime.image_table, "row_chan_cbs", None) or []
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
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
    image_table_rebuild_lut_row(ctx)


def image_table_distribute_timepoints(ctx) -> None:
    """Walk the available timepoints and assign one to each column, per row.

    Each row gets the same timepoint sequence across its cells, so multi-row
    tables can compare different wells/channels at matching timepoints. Each
    row is anchored on its own first cell's well/channel/FOV (preserving any
    per-row well/channel selections made via ``Distribute Wells`` or the
    per-row dropdowns) so distinct rows keep their own well assignments.
    """
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        ctx.set_status("Image Table: build a grid first (set rows/cols).")
        return

    tps = _timepoint_options(ctx)
    if not tps:
        ctx.set_status("Image Table: no timepoints in pipeline_info.json.")
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

    image_table_rebuild_lut_row(ctx)
    ctx.set_status(
        f"Image Table: distributed {min(len(tps), len(cells[0]) if cells else 0)} "
        f"timepoint(s) across {len(cells)} row(s) ({assignments} cells filled)."
    )


# ── Smart layout builder ──────────────────────────────────────────────────────
#
# The flat + nested grid assembly (auto-size the grid to the chosen row/col axes
# and fill every cell, holding off-axis dims fixed) lives in ``build_layout.py``.
# Re-exported here so call sites (tab_view, view_state restore, presets) and the
# playground importer resolve the same names unchanged.
from well_viewer.features.image_browser.image_table.build_layout import (  # noqa: E402,F401
    _AXIS_LABEL,
    _AXIS_MAX,
    _ROW_MAX,
    _apply_row_channel_styling,
    _build_nested_layout,
    _rebuild_lut_row_map_by_channel,
    _refresh_dims_pickers,
    image_table_apply_dims_preset,
    image_table_apply_preset,
    image_table_build_layout,
)


# ── Channel multi-select (sidebar) ────────────────────────────────────────────


def _update_channel_btn_text(ctx) -> None:
    """Reflect the active-channel set on the layout-row dropdown button."""
    btn = getattr(ctx.runtime.image_table, "channel_btn", None)
    if btn is None:
        return
    opts = _channel_options(ctx)
    sel = getattr(ctx.state.image_table, "active_channels", None) or set()
    ordered = [c for c in opts if c in sel]
    if not ordered or len(ordered) == len(opts):
        btn.setText("Channels: all")
    elif len(ordered) <= 2:
        btn.setText("Channels: " + ", ".join(ordered))
    else:
        btn.setText(f"Channels: {len(ordered)} selected")


def image_table_refresh_channel_picker(ctx) -> None:
    """Rebuild the layout-row channel dropdown menu from the dataset's
    channels, preserving valid prior selections."""
    from PySide6.QtGui import QAction

    menu = getattr(ctx.runtime.image_table, "channel_menu", None)
    if menu is None:
        return
    opts = _channel_options(ctx)
    active = getattr(ctx.state.image_table, "active_channels", None) or set()
    ctx.state.image_table.active_channels = {c for c in active if c in opts}

    menu.clear()
    act_all = menu.addAction("Select all")
    act_all.triggered.connect(lambda _=False: image_table_set_all_channels(ctx, True))
    act_none = menu.addAction("Clear")
    act_none.triggered.connect(lambda _=False: image_table_set_all_channels(ctx, False))
    menu.addSeparator()
    for ch in opts:
        act = QAction(ch, menu)
        act.setCheckable(True)
        # Set state BEFORE wiring so building the menu doesn't fire toggled.
        act.setChecked(ch in ctx.state.image_table.active_channels)
        act.toggled.connect(lambda checked, c=ch: image_table_toggle_channel(ctx, c, checked))
        menu.addAction(act)
    _update_channel_btn_text(ctx)


def image_table_toggle_channel(ctx, channel: str, checked: bool) -> None:
    """Add/remove a single channel from the active set (menu item toggled)."""
    sel = set(getattr(ctx.state.image_table, "active_channels", None) or set())
    if checked:
        sel.add(channel)
    else:
        sel.discard(channel)
    ctx.state.image_table.active_channels = sel
    _update_channel_btn_text(ctx)


def image_table_set_all_channels(ctx, on: bool) -> None:
    """Select-all / clear from the dropdown header actions."""
    ctx.state.image_table.active_channels = set(_channel_options(ctx)) if on else set()
    image_table_refresh_channel_picker(ctx)  # rebuild to refresh checkmarks


# ── Add row (clone an existing row, vary one dimension) ───────────────────────










def image_table_add_varied_row(ctx, *, src_idx: int, dim: str, mode: str, value: str) -> None:
    """Append a new row cloned from row *src_idx*, varying one dimension.

    Uniform mode sets *dim* to *value* across the whole new row; Step mode
    advances each cell's value for *dim* by one (clamped to the last available
    value). Other dimensions are copied as-is. Builds the selector row only —
    press Generate to render. Driven by :func:`image_table_open_add_row_dialog`.
    """
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        ctx.set_status("Image Table: build a table first, then add a row.")
        return
    dim = dim or "channel"
    mode = mode or "uniform"
    value = (value or "").strip()

    snap = _snapshot_cells(ctx)
    if not (0 <= src_idx < len(snap)):
        src_idx = len(snap) - 1
    src_row = snap[src_idx]
    opts = _all_dim_options(ctx, dim)

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
    ctx.state.image_table.rows = new_rows
    spin = getattr(ctx.runtime.image_table, "rows_spin", None)
    if spin is not None:
        spin.blockSignals(True)
        spin.setValue(new_rows)
        spin.blockSignals(False)
    image_table_rebuild_grid(ctx)

    full = snap + [new_row]
    for r, row in enumerate(getattr(ctx.runtime.image_table, "cells", None) or []):
        if r >= len(full):
            break
        for c, cell in enumerate(row):
            if c < len(full[r]):
                _write_cell(cell, full[r][c])

    # When the varied dimension is the channel and the whole new row shares one
    # channel (uniform), mirror it onto the row's channel combo + colormap.
    if dim == "channel" and mode == "uniform" and value:
        _apply_row_channel_styling_for_row(ctx, new_rows - 1, value)

    image_table_rebuild_lut_row(ctx)
    verb = f"set {dim} = {value}" if mode == "uniform" else f"stepped {dim}"
    ctx.set_status(
        f"Image Table: added row {new_rows} cloned from row {src_idx + 1} "
        f"({verb}). Press Generate to render."
    )


def image_table_open_add_row_dialog(ctx) -> None:
    """Modal dialog to clone an existing row, varying one dimension.

    Replaces the inline Add-row combo strip — collects source row / dimension /
    mode / value here (value list is per-dimension, enabled only in Uniform
    mode) and calls :func:`image_table_add_varied_row`.
    """
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        ctx.set_status("Image Table: build a table first, then add a row.")
        return

    dlg = QDialog(ctx)
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
        opts = _all_dim_options(ctx, dim)
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
        ctx,
        src_idx=int(src_cb.currentData() or 0),
        dim=str(dim_cb.currentData() or "channel"),
        mode=str(mode_cb.currentData() or "uniform"),
        value=val_cb.currentText().strip(),
    )


def _apply_row_channel_styling_for_row(ctx, row_idx: int, channel: str) -> None:
    """Set one row's channel combo + default the LUT to grayscale (used by
    Add-row). Matches ``_apply_row_channel_styling``: rows default to Greys
    regardless of channel; user can recolor a row via its LutSelector."""
    row_chan = getattr(ctx.runtime.image_table, "row_chan_cbs", None) or []
    row_lut = getattr(ctx.runtime.image_table, "row_lut_color_cbs", None) or []
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




def image_table_load_heatmap_layout(ctx) -> None:
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

    layouts = list(ctx.runtime.heatmap.layouts or [])
    layout = next((l for l in layouts if l.name == SIDEBAR_LAYOUT_NAME), None)
    if layout is None:
        ctx.set_status("Image Table: no Heat Map layout to load.")
        return

    rows_spin = getattr(ctx.runtime.image_table, "rows_spin", None)
    cols_spin = getattr(ctx.runtime.image_table, "cols_spin", None)
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
    ctx.state.image_table.rows = target_rows
    ctx.state.image_table.cols = target_cols
    image_table_rebuild_grid(ctx)

    cells = getattr(ctx.runtime.image_table, "cells", None) or []

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
    ctx.set_status(msg)


def image_table_distribute_wells(ctx) -> None:
    """Walk active wells row-wise and assign one to each selector cell.

    Special case: when the table has exactly twice as many cells as there
    are active wells, the assignment is duplicated — each well appears in
    two cells (typically the top half then the bottom half, when the
    table is wider than tall). This is convenient for laying out two
    rows of complementary channels for the same set of wells.
    """
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        ctx.set_status("Image Table: build a grid first (set rows/cols and click Apply).")
        return
    active = sorted(ctx.state.image_table.active_wells, key=parse_rc)
    if not active:
        ctx.set_status("Image Table: no active wells in the sidebar picker.")
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
        ctx.set_status(
            f"Image Table: distributed {n_active} well(s) twice "
            f"({assignments} cells filled) for complementary-channel layout."
        )
    else:
        ctx.set_status(
            f"Image Table: distributed {assignments} well(s) into the selector grid."
        )


# ── LUT row ──────────────────────────────────────────────────────────────────


def image_table_rebuild_lut_row(ctx) -> None:
    """Rebuild the per-row LUT + display editors (one box per grid row)."""
    from well_viewer.features.image_browser.image_table.grid_view import build_lut_row

    build_lut_row(
        ctx,
        on_auto_lut=image_table_auto_lut,
        lut_color_names=LUT_COLOR_NAMES,
    )




# ── Image loading + caching ──────────────────────────────────────────────────












_ZOOM_MIN = 0.33
_ZOOM_MAX = 4.0


def image_table_set_zoom(ctx, factor: float, *, relative: bool = False) -> None:
    """Set (or step) the rendered-table zoom factor and re-render.

    ``relative=True`` multiplies the current zoom by *factor* (zoom in/out
    buttons); otherwise *factor* is the absolute level (1.0 = 240 px cells).
    """
    cur = float(getattr(ctx.state.image_table, "zoom", 1.0) or 1.0)
    new = cur * factor if relative else factor
    new = max(_ZOOM_MIN, min(_ZOOM_MAX, new))
    ctx.state.image_table.zoom = new
    lbl = getattr(ctx.runtime.image_table, "zoom_lbl", None)
    if lbl is not None:
        lbl.setText(f"{new * 100:.0f}%")
    # Only re-render if a table has already been generated; otherwise just
    # store the level for the next Generate.
    if getattr(ctx.state.image_table, "last_render", None):
        image_table_generate(ctx)


def image_table_set_thresh_frac(ctx, value) -> None:
    """Edit the active channel's ON/OFF intensity threshold (ThreshFracOn) from
    the Image Table controls row, then re-composite so the per-row "≥ Thr"
    binary overlay reflects it.

    Writes through ``set_thresh_frac_on`` (the single source of truth shared with
    the Cell Gating tab and the other quick-edits). Re-renders only when a table
    has already been generated — the cached images are reused, so it's the same
    cheap re-composite the zoom control does; before the first Generate it just
    stores the value.
    """
    set_thresh_frac_on(ctx, value)
    if getattr(ctx.state.image_table, "last_render", None):
        image_table_generate(ctx)


# ── Per-cell mouse handling: hover pixel readout + crop drag ────────────────




def _install_cell_events(
    ctx, label, crop_tool, well: str, chan: str, tp: str, fov: str,
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






# ── Generate (render image table below the selector) ─────────────────────────


def _prefetch_cell_arrays(ctx, cells, cache) -> None:
    """Decode the grid's unique (well, channel, variant) images in parallel.

    The first Generate after a dataset load (or after the cache is invalidated)
    has to read every cell's image off disk; doing that sequentially on the GUI
    thread is the slow part. One ``_load_array`` call loads (and caches) all
    FOVs/timepoints for a well+channel, so the unique work is far smaller than
    the cell count. Threads help because ``zipfile``/``tifffile``/``PIL`` release
    the GIL for the C-level inflate+decode, and the per-zip-handle serialization
    in ``data/zipfile_cache.py`` keeps concurrent reads safe (same well → one
    handle, serialized; different wells → parallel). Ratio channels are skipped
    (they compose from per-channel arrays, which this still warms). Best-effort:
    any failure leaves the sequential in-loop load as the fallback.
    """
    todo: dict = {}
    for r, row in enumerate(cells):
        use_tophat, _b, _bm = _row_display_opts(ctx, r)
        for cell in row:
            well = cell["well_cb"].currentText().strip()
            chan = cell["chan_cb"].currentText().strip()
            tp = cell["tp_cb"].currentText().strip()
            fov = cell["fov_cb"].currentText().strip()
            if not (well and chan and tp):
                continue
            if _find_ratio_for_channel(ctx, chan) is not None:
                continue
            th = bool(use_tophat) and not _is_nuc_seg(chan)
            variant = "tophat" if th else "raw"
            key = (well, chan.lower(), variant)
            if key in cache:
                continue
            todo.setdefault(key, (well, chan, tp, fov, th))
    if len(todo) < 2:
        return  # nothing to gain from a pool for 0–1 reads
    import os
    from concurrent.futures import ThreadPoolExecutor

    def _warm(spec) -> None:
        well, chan, tp, fov, th = spec
        try:
            _load_array(ctx, cache, well, chan, tp, fov, use_tophat=th)
        except Exception:
            swallowed()  # in-loop load (with its own handling) is the fallback

    try:
        with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as ex:
            list(ex.map(_warm, list(todo.values())))
    except Exception:
        import logging as _logging
        _logging.getLogger("well_viewer.image_table").debug(
            "image_table: parallel prefetch failed", exc_info=True)


def _append_composite_row(ctx, cells, grid, cache, crop_tool, sz, rendered) -> None:
    """Append one extra bottom grid row whose cells additively merge the
    LUT-colored RGB of every row above them in the same column.

    Derives entirely from the rows above (no selector cell, no ``lut[r]`` entry),
    so the grid's row-indexed lookups are unaffected. Extracted from
    ``image_table_generate`` to keep that function's complexity in budget.
    """
    from well_viewer.core.ui_helpers import make_overlay_thumb

    comp_row = len(cells)
    n_cols = max(len(row) for row in cells)
    for c in range(n_cols):
        comp = composite_column_rgb(ctx, cache, cells, crop_tool, c, rendered)

        cell_widget = QWidget()
        cl = QVBoxLayout(cell_widget)
        cl.setContentsMargins(2, 2, 2, 2)
        cl.setSpacing(2)
        header = QLabel("Composite", cell_widget)
        header.setAlignment(Qt.AlignCenter)
        cl.addWidget(header)

        img_label = QLabel(cell_widget)
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setFixedSize(sz, sz)
        pix = make_overlay_thumb(comp, sz, sz, 0.0, 255.0) if comp is not None else None
        if pix is not None:
            img_label.setPixmap(pix)
        else:
            img_label.setText("(no image)")
        cl.addWidget(img_label)

        grid.addWidget(cell_widget, comp_row, c)


def image_table_generate(ctx) -> None:
    """Load images for every cell and lay out the rendered image table."""
    from well_viewer.core.ui_helpers import make_fluor_thumb, make_overlay_thumb
    from well_viewer.core.ui_helpers import clear_layout

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    grid = ctx.runtime.image_table.render_grid
    clear_layout(grid)
    if not cells:
        ctx.set_status("Image Table: build a grid first (set rows/cols and click Apply).")
        return

    # Once the user has triggered a Generate, hide the EmptyState placeholder.
    empty = getattr(ctx.runtime.image_table, "empty_state", None)
    if empty is not None:
        empty.setVisible(False)

    # Persist the decoded-image cache across Generates so crop / zoom / LUT /
    # threshold re-renders re-composite from memory instead of re-reading and
    # re-decoding every cell from its zip. Invalidated on dataset load
    # (load_controller) and keyed by variant, so Raw/Corrected stays correct.
    cache = ctx.state.image_table.image_cache
    if not isinstance(cache, dict):
        cache = {}
        ctx.state.image_table.image_cache = cache
    rendered: Dict[Tuple[int, int], Any] = {}
    crop_tool = getattr(ctx.runtime.image_table, "crop_tool", None)
    # Per-cell thumbnail size, scaled by the zoom factor (240 px = 1.0×).
    zoom = float(getattr(ctx.state.image_table, "zoom", 1.0) or 1.0)
    sz = max(80, int(round(240 * zoom)))

    _prefetch_cell_arrays(ctx, cells, cache)

    for r, row in enumerate(cells):
        # Display options (top-hat / boundaries / binary) + LUT are per ROW —
        # read from this row's box so two rows on the same channel stay
        # independent (see grid_view.build_lut_row).
        use_tophat, show_boundaries, binary_mode = _row_display_opts(ctx, r)
        log_scale = _row_log_scale(ctx, r)
        for c, cell in enumerate(row):
            well = cell["well_cb"].currentText().strip()
            chan = cell["chan_cb"].currentText().strip()
            tp = cell["tp_cb"].currentText().strip()
            fov = cell["fov_cb"].currentText().strip()

            arr = None
            ratio = _find_ratio_for_channel(ctx, chan) if chan else None
            if well and chan and tp:
                if ratio is not None:
                    arr = _load_ratio_array(ctx, cache, well, ratio, tp, fov, use_tophat=use_tophat)
                else:
                    arr = _load_array(ctx, cache, well, chan, tp, fov, use_tophat=use_tophat)
            rendered[(r, c)] = arr

            lo, hi = _parse_lut(ctx, r)
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
                    pix = _render_mask_binary(ctx, cache, well, tp, fov, crop_tool, sz)
                    if pix is None:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(ctx, r), log_scale=log_scale)
                elif binary_mode == "threshold":
                    # Only cells above the active ON threshold white on black.
                    pix = _render_threshold_binary(ctx, cache, well, chan, tp, fov, crop_tool, sz)
                    if pix is None:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(ctx, r), log_scale=log_scale)
                elif show_boundaries:
                    # Overlay white cell-boundary outlines on the fluorescence image.
                    mask_arr = _load_mask_array(ctx, cache, well, tp, fov)
                    if mask_arr is not None:
                        cm = crop_tool.apply_to_array(mask_arr) if crop_tool else mask_arr
                        pix = _render_with_boundary_overlay(
                            cropped, sz, sz, lo, hi,
                            _row_cmap_name(ctx, r), cm, log_scale=log_scale,
                        )
                    else:
                        pix = make_fluor_thumb(cropped, sz, sz, lo, hi, cmap=_row_cmap_name(ctx, r), log_scale=log_scale)
                else:
                    pix = make_fluor_thumb(
                        cropped, sz, sz, lo, hi,
                        cmap=_row_cmap_name(ctx, r), log_scale=log_scale,
                    )
                # CropTool needs the FULL source array + the active crop on
                # the label so label-pixel → image-pixel coord conversion
                # works correctly even when the label already shows a crop.
                img_label._raw_arr = arr  # type: ignore[attr-defined]
                img_label._crop = crop_tool.crop if crop_tool else None  # type: ignore[attr-defined]
                _install_cell_events(ctx, img_label, crop_tool, well, chan, tp, fov)
                if pix is not None:
                    img_label.setPixmap(pix)
                else:
                    img_label.setText("(render failed)")
            else:
                img_label.setText("(no image)")
            cl.addWidget(img_label)

            grid.addWidget(cell_widget, r, c)

    # Composite row: append one extra bottom row whose cells additively merge
    # the LUT-colored RGB of every row above them in the same column.
    if getattr(ctx.state.image_table, "composite", False) and cells:
        _append_composite_row(ctx, cells, grid, cache, crop_tool, sz, rendered)

    ctx.state.image_table.last_render = rendered
    ctx.state.image_table.image_cache = cache
    # Fill the per-channel LUT min/max boxes with the actual pooled numbers so
    # the user sees real values instead of "auto" (skips manually-edited rows).
    _refresh_lut_fields_from_render(ctx, rendered)
    n = sum(1 for v in rendered.values() if v is not None)
    ctx.set_status(f"Image Table: generated {n} of {len(rendered)} cell(s).")
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
    image_table_save_to_gallery,
)




# ── Export (transparent background, labels above each image) ─────────────────








