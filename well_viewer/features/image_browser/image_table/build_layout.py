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


from well_viewer.features.image_browser.image_table.state import spec_to_state_fields
from well_viewer.features.image_browser.image_table.grid_layout import (
    PLATE_DIMS,
    _axis_values,
    _current_axis,
    _current_dims,
    _fixed_off_axis,
    _layout_cell_values,
    _layout_cell_values_nested,
    banded_indices,
    _product_values,
    _timepoint_options,
    compose_plate_well,
    plate_well_index,
)

_AXIS_LABEL = {"channel": "Channels", "well": "Wells",
               "timepoint": "Timepoints", "fov": "FOVs",
               "well_row": "Plate rows", "well_col": "Plate cols"}
_AXIS_MAX = 12  # non-grouped grid is capped at 12×12; longer axes are truncated
_ROW_MAX = 60   # grouped/tiled grids may stack many bands — cap total rows here


# Maps a layout-math dim key → the plain render-model cell key.
_DIM_TO_CELL = {"well": "well", "channel": "chan",
                "timepoint": "tp", "fov": "fov"}


def _fill_cell(cell: dict, vals: dict) -> None:
    """Write a layout-math ``{dim: value}`` result into a plain render-model cell.

    The cell is a ``{"well", "chan", "tp", "fov"}`` string dict (no Qt widgets);
    empty values are skipped so an off-axis dim already held fixed isn't cleared."""
    for dim, ckey in _DIM_TO_CELL.items():
        val = vals.get(dim)
        if val:
            cell[ckey] = val


def _ctrl():
    """Lazy controller handle — breaks the controller↔build_layout import cycle.

    The grid-widget rebuild + per-row LUT rebuild are Qt/controller-side; importing
    them at module load would cycle (controller imports this module for re-export),
    so we resolve them at call time (controller is fully loaded by then)."""
    from well_viewer.features.image_browser.image_table import controller as C
    return C


def _build_nested_layout(ctx, row_dims: list[str], col_dims: list[str],
                         wrap: int = 0) -> None:
    """Build a grid whose rows and/or columns are the mixed-radix Cartesian
    product of an ordered dim list, so >2 dimensions vary at once (e.g.
    rows=[well, channel] × cols=[timepoint]). The flat single-axis path
    (``image_table_build_layout``) handles the len==1 case on both axes.

    *wrap* folds the columns into vertically-stacked bands of that many columns,
    repeating the whole row product per band — the same banding the flat path
    does, applied to the column *product* rather than a single dim's values, so
    a table with channels nested under an axis wraps like any other. 0 / a value
    at or past the column count = off."""
    overlap = set(row_dims) & set(col_dims)
    if overlap:
        ctx.notify.set_status("Image Table: a dimension can't be on both the rows and "
                       "columns axes.")
        return
    row_prod = _product_values(ctx, row_dims)
    col_prod = _product_values(ctx, col_dims)
    if not row_prod:
        ctx.notify.set_status("Image Table: nothing on the rows axis "
                       f"({' › '.join(_AXIS_LABEL[d] for d in row_dims)}).")
        return
    if not col_prod:
        ctx.notify.set_status("Image Table: nothing on the columns axis "
                       f"({' › '.join(_AXIS_LABEL[d] for d in col_dims)}).")
        return

    n_rows_wanted, n_cols_wanted = len(row_prod), len(col_prod)
    group = int(wrap or 0)
    grouped = 0 < group < n_cols_wanted
    st = ctx.state.image_table
    clamped_bands = 0
    if grouped:
        # Cap band width at the column max and total rows at the row max; drop
        # whole overflow bands (reported below) rather than silently.
        cols = min(group, _AXIS_MAX)
        n_row_prod = min(n_rows_wanted, _ROW_MAX)
        row_prod = row_prod[:n_row_prod]
        n_bands_wanted = (n_cols_wanted + cols - 1) // cols
        max_bands = max(1, _ROW_MAX // max(1, n_row_prod))
        n_bands = min(n_bands_wanted, max_bands)
        clamped_bands = n_bands_wanted - n_bands
        rows = n_row_prod * n_bands
        st.group_size = cols
        st.group_row_span = n_row_prod
    else:
        rows = min(n_rows_wanted, _ROW_MAX)
        cols = min(n_cols_wanted, _AXIS_MAX)
        row_prod = row_prod[:rows]
        col_prod = col_prod[:cols]
        n_row_prod = rows
        st.group_size = 0
        st.group_row_span = 0
    st.rows = rows
    st.cols = cols
    _ctrl().image_table_rebuild_grid(ctx)

    fixed = _fixed_off_axis(ctx, set(row_dims) | set(col_dims))
    plate = bool((set(row_dims) | set(col_dims)) & set(PLATE_DIMS))
    rc_index = plate_well_index(ctx) if plate else {}
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            if grouped:
                ri, ci = banded_indices(r, c, n_row_prod, cols)
            else:
                ri, ci = r, c
            # A ragged final band runs past the last column value: blank the cell
            # (None) instead of indexing, so it stays empty rather than repeating.
            rt = row_prod[ri] if 0 <= ri < len(row_prod) else None
            ct = col_prod[ci] if 0 <= ci < len(col_prod) else None
            vals = _layout_cell_values_nested(row_dims, col_dims, rt, ct, fixed)
            if plate:
                compose_plate_well(vals, rc_index)
            _fill_cell(cell, vals)

    if grouped:
        clamped = max(0, n_rows_wanted - n_row_prod)
    else:
        clamped = (n_rows_wanted - rows) + (n_cols_wanted - cols)
    rlabel = " › ".join(_AXIS_LABEL[d] for d in row_dims)
    clabel = " › ".join(_AXIS_LABEL[d] for d in col_dims)
    status = (f"Image Table: built {rows}×{cols} selector grid — rows = {rlabel}, "
              f"cols = {clabel}")
    if grouped:
        status += f", grouped {cols}/band"
    status += ". Press Generate to render."
    if clamped_bands:
        status += (f" (Clamped {clamped_bands} band(s) that exceed the "
                   f"{_ROW_MAX}-row grid limit.)")
    if clamped:
        status += (f" (Clamped to the {_ROW_MAX}-row / {_AXIS_MAX}-col limit — "
                   "filter the selection to see the rest.)")
    ctx.notify.set_status(status)


def image_table_build_layout(ctx) -> None:
    """Auto-size the grid to the chosen row/col axes and fill every cell."""
    # Nested / compound axes: when either axis carries more than one dimension,
    # build the mixed-radix product grid. The single-axis path below is unchanged.
    _row_dims = _current_dims(ctx, "row")
    _col_dims = _current_dims(ctx, "col")
    if len(_row_dims) > 1 or len(_col_dims) > 1:
        # Carry the wrap through: banding is the columns axis folding into bands,
        # which is as meaningful for a column product as for a single dim.
        _build_nested_layout(ctx, _row_dims, _col_dims,
                             int(getattr(ctx.state.image_table, "group_size", 0) or 0))
        return

    row_axis = _current_axis(ctx, "row")
    col_axis = _current_axis(ctx, "col")
    if row_axis == col_axis:
        ctx.notify.set_status("Image Table: choose two different axes for rows and columns.")
        return

    row_vals = _axis_values(ctx, row_axis)
    col_vals = _axis_values(ctx, col_axis)
    if not row_vals:
        ctx.notify.set_status(f"Image Table: nothing on the {_AXIS_LABEL[row_axis]} axis "
                        f"(select some in the sidebar).")
        return
    if not col_vals:
        ctx.notify.set_status(f"Image Table: nothing on the {_AXIS_LABEL[col_axis]} axis "
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
    tiled = {"n_row_vals": n_row_vals, "group_size": cols} if grouped else {}
    plate = bool({row_axis, col_axis} & set(PLATE_DIMS))
    rc_index = plate_well_index(ctx) if plate else {}
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            vals = _layout_cell_values(row_axis, col_axis, row_vals, col_vals,
                                       fixed, r, c, **tiled)
            if plate:
                compose_plate_well(vals, rc_index)
            _fill_cell(cell, vals)

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
    ctx.notify.set_status(status)


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


# ── The builder shelf entry point ───────────────────────────────────────────
# ``builder_view`` (the root + nested-channels drag-and-drop tree) is the sole
# authoring surface. It edits a Qt-free ``TableSpec`` and calls this to flatten
# it onto the existing state fields + build + render. The tree is the single
# source of truth; the per-cell render model (plain value dicts) is a derived
# cache the render path reads. Off-axis held values live on ``state.fixed_vals``
# (read directly by ``grid_layout._fixed_off_axis``) — no mirror widgets.


def image_table_apply_spec(ctx, spec, *, render: bool = True) -> None:
    """Author the Image Table from a builder ``TableSpec`` (root + nested channels).

    Flattens the tree onto the existing state fields
    (``row_dims``/``col_dims``/``active_channels``/``active_timepoints``/
    ``active_fovs``/``composite``/``group_size``/``channel_opts``/``fixed_vals``),
    mirrors the Fixed values onto the off-axis combos, builds the selector grid
    with the existing layout math, then (optionally) renders. ``active_wells``
    stays owned by the sidebar plate. A threshold channel is carried by its
    ``"<CHAN> (binary)"`` token (already in ``active_channels`` via
    ``spec_to_state_fields``), so the tested binary-variant plumbing renders it on
    either axis; every other per-channel render option is resolved from
    ``channel_opts`` by the render seam.
    """
    st = ctx.state.image_table
    fields = spec_to_state_fields(spec)
    st.row_dims = fields["row_dims"]
    st.col_dims = fields["col_dims"]
    st.active_channels = fields["active_channels"]
    st.active_timepoints = fields["active_timepoints"]
    st.active_fovs = fields["active_fovs"]
    st.composite = fields["composite"]
    st.channel_opts = fields["channel_opts"]
    st.fixed_vals = fields["fixed_vals"]

    row_dims = list(st.row_dims)
    col_dims = list(st.col_dims)
    wrap = int(fields["group_size"] or 0)

    if wrap and len(row_dims) <= 1 and len(col_dims) <= 1:
        # Grouped/tiled columns — reuse the flat grouped path (single-dim axes).
        st.group_size = wrap
        rt = ctx.runtime.image_table
        rt.row_axis = row_dims[0] if row_dims else "channel"
        rt.col_axis = col_dims[0] if col_dims else "timepoint"
        st.row_axis = rt.row_axis
        st.col_axis = rt.col_axis
        image_table_build_layout(ctx)
    else:
        # Nested axes band too — the column *product* folds into bands — so a
        # layout with channels nested under a root still honours Wrap cols.
        _build_nested_layout(ctx, row_dims, col_dims, wrap)

    if render:
        _ctrl().image_table_generate(ctx)


# Build Layout defaults — the fresh-tab state the "Reset table" action returns to.
_DEFAULT_ROW_AXIS = "channel"
_DEFAULT_COL_AXIS = "timepoint"
_DEFAULT_ROWS = 2
_DEFAULT_COLS = 3


def _last_timepoint_set(ctx) -> set:
    """``{last timepoint}``, or empty when the dataset has none (which the
    filters read as "all" — correct, since there is nothing to narrow).

    ``_timepoint_options`` returns the run's timepoints already ordered by the
    shared ``util.timepoint`` sort key, so the last element is the latest.
    """
    opts = _timepoint_options(ctx)
    return {opts[-1]} if opts else set()


def _reset_layout_state(ctx) -> None:
    """Restore the builder/layout state to fresh-tab defaults — no widget side
    effects (no picker refresh / grid rebuild / status). Shared by the "Reset
    table" button and the dataset-load reset so both agree on "defaults".

    Matches the historical "Reset table" scope exactly: it does NOT touch the
    per-channel render options (``channel_opts``) or the cached ``builder_spec`` —
    the dataset-load reset clears those separately (new dataset ⇒ new channels)."""
    st = ctx.state.image_table
    st.row_dims = []
    st.col_dims = []
    st.row_axis = _DEFAULT_ROW_AXIS
    st.col_axis = _DEFAULT_COL_AXIS
    st.rows = _DEFAULT_ROWS
    st.cols = _DEFAULT_COLS
    st.group_size = 0
    st.group_row_span = 0
    st.active_wells = set()
    st.active_channels = set()
    # An empty filter set means "all" (grid_layout._filtered_values), and
    # timepoint is the *column* axis by default — so leaving it empty opens the
    # tab with one column per timepoint in the run, multiplying the grid by the
    # length of the experiment before the user has chosen anything. Start on the
    # last timepoint; the picker adds the rest.
    st.active_timepoints = _last_timepoint_set(ctx)
    st.active_fovs = set()
    st.fixed_vals = {}

    rt = ctx.runtime.image_table
    rt.row_axis = _DEFAULT_ROW_AXIS
    rt.col_axis = _DEFAULT_COL_AXIS


def _clear_rendered_grid(ctx) -> None:
    """Tear down the rendered image widgets + re-show the empty-state placeholder,
    with no status message. Shared by "Clear images" and the dataset-load reset."""
    from well_viewer.core.ui_helpers import clear_layout
    rt = ctx.runtime.image_table
    grid = getattr(rt, "render_grid", None)
    if grid is not None:
        clear_layout(grid)
    ctx.state.image_table.last_render = {}
    empty = getattr(rt, "empty_state", None)
    if empty is not None:
        empty.setVisible(True)


def image_table_reset_layout(ctx) -> None:
    """Reset the builder to defaults ("Reset table").

    Empties the render model and restores the fresh-tab state: Channels ×
    Timepoints axes, a 2×3 grid, no grouping, and every dimension selection +
    nesting cleared (wells / channels / timepoints / fovs, ``row_dims`` /
    ``col_dims`` / ``fixed_vals``). The rendered images below are left untouched —
    ``Clear images`` (``image_table_clear_render``) is the separate action."""
    _reset_layout_state(ctx)
    C = _ctrl()
    C.image_table_refresh_picker(ctx)
    C.image_table_rebuild_grid(ctx)
    ctx.notify.set_status("Image Table: layout reset to defaults.")


def image_table_reset_on_dataset_load(ctx) -> None:
    """Reset the whole Image Table to fresh-tab state on a dataset (re)load.

    A new / reloaded dataset must never inherit the previous grid (its wells /
    channels differ), and the "Reset all saved state" trash-can relies on this:
    it deletes ``persistence.json`` then reloads, so the reset lands here. Clears
    the builder/layout defaults *and* wipes the rendered image widgets, then
    refreshes the picker + rebuilds the empty grid. No status message — loads are
    frequent and the reset is implicit. A dataset that *has* a saved grid still
    gets it back: ``view_state`` restore re-applies its ``Image Table`` block when
    the tab is (re)activated, overwriting this default."""
    _reset_layout_state(ctx)
    # New dataset ⇒ its channels differ, so drop per-channel render options + the
    # cached builder spec too (beyond the "Reset table" scope).
    ctx.state.image_table.channel_opts = {}
    ctx.runtime.image_table.builder_spec = None
    _clear_rendered_grid(ctx)
    C = _ctrl()
    C.image_table_refresh_picker(ctx)
    C.image_table_rebuild_grid(ctx)


def image_table_clear_render(ctx) -> None:
    """Wipe the rendered image grid ("Clear images").

    Blanks the Generate output and re-shows the empty-state placeholder, leaving
    the selector layout and the decoded-image cache intact so a re-Generate is one
    click away (and cheap — no zip re-read). Independent of ``Reset table``."""
    _clear_rendered_grid(ctx)
    ctx.notify.set_status("Image Table: cleared rendered images. Press Generate to re-render.")
