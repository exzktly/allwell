"""Image Table smart layout builders — flat + nested (compound-axis) assembly.

Declare what varies down the ROWS and across the COLUMNS (Channels / Wells /
Timepoints / FOVs); one Build auto-sizes the grid to the selection and fills every
cell, holding the off-axis dimensions fixed. This replaces the manual "set rows/
cols, then assign every dropdown" workflow for the common views (channels ×
timepoints, channels × wells, wells × timepoints, …) and — with the nested picker —
compound layouts like Wells › Channels × Time.

Extracted from ``controller.py`` (the image_table monolith ratchet) to keep the
controller on event handling + orchestration. The pure per-cell / product math
lives one layer down in ``grid_layout`` (Qt-free) and is imported here; the Qt
grid-widget rebuild + per-row LUT helpers live up in ``controller`` and are
imported lazily (``_ctrl``) to avoid an import cycle. ``controller`` re-exports
every public name below, so call sites and playground importers are unchanged.
"""

from __future__ import annotations

from typing import List

from well_viewer.core.safe import swallowed
from well_viewer.features.image_browser.image_table.image_compose import _row_channel
from well_viewer.features.image_browser.image_table.grid_layout import (
    _axis_values,
    _current_axis,
    _current_dims,
    _fixed_off_axis,
    _layout_cell_values,
    _layout_cell_values_nested,
    _product_values,
)

_AXIS_LABEL = {"channel": "Channels", "well": "Wells",
               "timepoint": "Timepoints", "fov": "FOVs"}
_AXIS_MAX = 12  # non-grouped grid is capped at 12×12; longer axes are truncated
_ROW_MAX = 60   # grouped/tiled grids may stack many bands — cap total rows here


def _ctrl():
    """Lazy controller handle — breaks the controller↔build_layout import cycle.

    The grid-widget rebuild + per-row LUT rebuild are Qt/controller-side; importing
    them at module load would cycle (controller imports this module for re-export),
    so we resolve them at call time (controller is fully loaded by then)."""
    from well_viewer.features.image_browser.image_table import controller as C
    return C


def _apply_row_channel_styling(ctx, channels: List[str]) -> None:
    """When rows are channels, mirror each channel onto its row-0 channel combo
    and default every row to grayscale (Greys). The user can recolor any row
    via its LutSelector — defaulting to grayscale makes channels comparable at a
    glance and avoids implying meaning (e.g. green = GFP) that the data may not
    carry."""
    row_chan = getattr(ctx.runtime.image_table, "row_chan_cbs", None) or []
    row_lut = getattr(ctx.runtime.image_table, "row_lut_color_cbs", None) or []
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


def _rebuild_lut_row_map_by_channel(ctx) -> None:
    """Key the per-row LUT/Display editor by CHANNEL: grid rows showing the same
    channel share one LUT/colormap (per-channel display, shared across wells /
    panels — the chosen default). Recomputed from the filled cells' channels; a
    row with no resolvable channel gets its own index. Reproduces the grouped-
    movie ``group_row_span`` behaviour (bands of one channel → one LUT)."""
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    index_of: dict = {}
    row_map: List[int] = []
    for r in range(len(cells)):
        chan = (_row_channel(ctx, r) or "").strip().lower()
        key = chan or f"__row{r}"
        if key not in index_of:
            index_of[key] = len(index_of)
        row_map.append(index_of[key])
    ctx.runtime.image_table.lut_row_map = row_map


def _build_nested_layout(ctx, row_dims: List[str], col_dims: List[str]) -> None:
    """Build a grid whose rows and/or columns are the mixed-radix Cartesian
    product of an ordered dim list, so >2 dimensions vary at once (e.g.
    rows=[well, channel] × cols=[timepoint]). The flat single-axis path
    (``image_table_build_layout``) handles the len==1 case on both axes."""
    overlap = set(row_dims) & set(col_dims)
    if overlap:
        ctx.set_status("Image Table: a dimension can't be on both the rows and "
                       "columns axes.")
        return
    row_prod = _product_values(ctx, row_dims)
    col_prod = _product_values(ctx, col_dims)
    if not row_prod:
        ctx.set_status("Image Table: nothing on the rows axis "
                       f"({' › '.join(_AXIS_LABEL[d] for d in row_dims)}).")
        return
    if not col_prod:
        ctx.set_status("Image Table: nothing on the columns axis "
                       f"({' › '.join(_AXIS_LABEL[d] for d in col_dims)}).")
        return

    n_rows_wanted, n_cols_wanted = len(row_prod), len(col_prod)
    rows = min(n_rows_wanted, _ROW_MAX)
    cols = min(n_cols_wanted, _AXIS_MAX)
    row_prod = row_prod[:rows]
    col_prod = col_prod[:cols]

    st = ctx.state.image_table
    st.group_size = 0
    st.group_row_span = 0
    st.rows = rows
    st.cols = cols
    for attr, val in (("rows_spin", rows), ("cols_spin", cols)):
        spin = getattr(ctx.runtime.image_table, attr, None)
        if spin is not None:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
    _ctrl().image_table_rebuild_grid(ctx)

    fixed = _fixed_off_axis(ctx, set(row_dims) | set(col_dims))
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

    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            vals = _layout_cell_values_nested(
                row_dims, col_dims, row_prod[r], col_prod[c], fixed)
            for dim, ckey in key_of.items():
                _set(cell.get(ckey), vals[dim])

    # Per-channel LUT keying + row styling only when channel varies down the rows
    # (each row then has exactly one channel). When channel is off-rows the map
    # stays identity (set by rebuild_grid with group_row_span=0), so we don't
    # collapse distinct rows that merely happen to share a fixed channel.
    if "channel" in row_dims:
        ci = row_dims.index("channel")
        _apply_row_channel_styling(ctx, [row_prod[r][ci] for r in range(rows)])
        _rebuild_lut_row_map_by_channel(ctx)
    _ctrl().image_table_rebuild_lut_row(ctx)

    clamped = (n_rows_wanted - rows) + (n_cols_wanted - cols)
    rlabel = " › ".join(_AXIS_LABEL[d] for d in row_dims)
    clabel = " › ".join(_AXIS_LABEL[d] for d in col_dims)
    status = (f"Image Table: built {rows}×{cols} selector grid — rows = {rlabel}, "
              f"cols = {clabel}. Press Generate to render.")
    if clamped:
        status += (f" (Clamped to the {_ROW_MAX}-row / {_AXIS_MAX}-col limit — "
                   "filter the selection to see the rest.)")
    ctx.set_status(status)


def image_table_build_layout(ctx) -> None:
    """Auto-size the grid to the chosen row/col axes and fill every cell."""
    # Nested / compound axes: when either axis carries more than one dimension,
    # build the mixed-radix product grid. The single-axis path below is unchanged.
    _row_dims = _current_dims(ctx, "row")
    _col_dims = _current_dims(ctx, "col")
    if len(_row_dims) > 1 or len(_col_dims) > 1:
        _build_nested_layout(ctx, _row_dims, _col_dims)
        return

    row_axis = _current_axis(ctx, "row")
    col_axis = _current_axis(ctx, "col")
    if row_axis == col_axis:
        ctx.set_status("Image Table: choose two different axes for rows and columns.")
        return

    row_vals = _axis_values(ctx, row_axis)
    col_vals = _axis_values(ctx, col_axis)
    if not row_vals:
        ctx.set_status(f"Image Table: nothing on the {_AXIS_LABEL[row_axis]} axis "
                        f"(select some in the sidebar).")
        return
    if not col_vals:
        ctx.set_status(f"Image Table: nothing on the {_AXIS_LABEL[col_axis]} axis "
                        f"(select some in the sidebar).")
        return

    # Grouped/tiled layout: wrap the COLUMNS axis into vertically-stacked bands
    # of ``G`` columns, repeating the ROWS axis per band. 0 / ≥col-count = off.
    group = int(getattr(ctx.state.image_table, "group_size", 0) or 0)
    n_row_vals = len(row_vals[:_AXIS_MAX])
    grouped = 0 < group < len(col_vals)
    clamped_bands = 0
    if grouped:
        # Cap band width at the column max and total rows at the row max; drop
        # whole overflow bands (reporting the truncation) rather than silently.
        G = min(group, _AXIS_MAX)
        row_vals = row_vals[:_AXIS_MAX]
        n_row_vals = len(row_vals)
        n_bands_wanted = (len(col_vals) + G - 1) // G
        max_bands = max(1, _ROW_MAX // max(1, n_row_vals))
        n_bands = min(n_bands_wanted, max_bands)
        clamped_bands = n_bands_wanted - n_bands
        cols = G
        rows = n_row_vals * n_bands
        ctx.state.image_table.group_size = G
        ctx.state.image_table.group_row_span = n_row_vals
    else:
        row_vals = row_vals[:_AXIS_MAX]
        col_vals = col_vals[:_AXIS_MAX]
        rows, cols = len(row_vals), len(col_vals)
        ctx.state.image_table.group_size = 0
        ctx.state.image_table.group_row_span = 0

    # Size the grid (sync the spinners) and rebuild the empty selector cells.
    # group state is set above so rebuild_grid builds the LUT row→group map.
    ctx.state.image_table.rows = rows
    ctx.state.image_table.cols = cols
    for attr, val in (("rows_spin", rows), ("cols_spin", cols)):
        spin = getattr(ctx.runtime.image_table, attr, None)
        if spin is not None:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
    _ctrl().image_table_rebuild_grid(ctx)

    fixed = _fixed_off_axis(ctx, {row_axis, col_axis})
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

    tiled = {"n_row_vals": n_row_vals, "group_size": cols} if grouped else {}
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            vals = _layout_cell_values(row_axis, col_axis, row_vals, col_vals,
                                       fixed, r, c, **tiled)
            for dim, ckey in key_of.items():
                _set(cell.get(ckey), vals[dim])

    if row_axis == "channel":
        # Repeat the channel per band so every grid row's selector-col-0 combo
        # shows its band channel.
        grid_channels = (
            [row_vals[r % n_row_vals] for r in range(rows)] if grouped else row_vals
        )
        _apply_row_channel_styling(ctx, grid_channels)

    _ctrl().image_table_rebuild_lut_row(ctx)
    status = (
        f"Image Table: built {rows}×{cols} selector grid — rows = {_AXIS_LABEL[row_axis]}, "
        f"cols = {_AXIS_LABEL[col_axis]}"
    )
    if grouped:
        status += f", grouped {cols}/band"
    status += ". Press Generate to render."
    if clamped_bands:
        status += (
            f" (Clamped {clamped_bands} band(s) that exceed the {_ROW_MAX}-row grid limit.)"
        )
    ctx.set_status(status)


def image_table_apply_preset(ctx, row_axis: str, col_axis: str) -> None:
    """Set the row/col axis dropdowns to a common (single-dim) pairing, then Build.

    Clears any nested row_dims/col_dims so a flat preset resets nesting."""
    ctx.state.image_table.row_dims = []
    ctx.state.image_table.col_dims = []
    for which, axis in (("row", row_axis), ("col", col_axis)):
        cb = getattr(ctx.runtime.image_table, f"{which}_axis_cb", None)
        if cb is not None:
            idx = cb.findData(axis)
            cb.blockSignals(True)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
        setattr(ctx.runtime.image_table, f"{which}_axis", axis)
    _refresh_dims_pickers(ctx)
    image_table_build_layout(ctx)


def _refresh_dims_pickers(ctx) -> None:
    """Nudge the Rows/Columns picker buttons to redraw from state (no-op headless
    or before the tab is built — the refresh hooks are optional)."""
    for which in ("row", "col"):
        fn = getattr(ctx.runtime.image_table, f"{which}_dims_refresh", None)
        if callable(fn):
            fn()


def image_table_apply_dims_preset(ctx, row_dims, col_dims) -> None:
    """Set the row/col dimension LISTS (nested-capable), then Build.

    Single-element lists reduce to the flat single-axis layout; multi-element
    lists build the mixed-radix product grid. Keeps the legacy single-axis fields
    + combos synced to each axis's outer dim so the flat path / saved state stay
    coherent."""
    st = ctx.state.image_table
    st.row_dims = list(row_dims)
    st.col_dims = list(col_dims)
    for which, dims in (("row", row_dims), ("col", col_dims)):
        axis = dims[0] if dims else ("channel" if which == "row" else "timepoint")
        cb = getattr(ctx.runtime.image_table, f"{which}_axis_cb", None)
        if cb is not None:
            idx = cb.findData(axis)
            cb.blockSignals(True)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
        setattr(ctx.runtime.image_table, f"{which}_axis", axis)
        setattr(st, f"{which}_axis", axis)
    _refresh_dims_pickers(ctx)
    image_table_build_layout(ctx)
