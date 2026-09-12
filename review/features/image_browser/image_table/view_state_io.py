"""Image Table view-state serialization — the feature owns its own snapshot /
restore so ``persistence/view_state.py`` never names (or imports) Image Table.

The registry exposes this pair via the Image Table ``FeatureDescriptor``'s
``view_state_io`` hook; ``view_state`` fans that out. A profiled-out Image Table
isn't in the registry, so this module is never imported — which is what lets a
subset binary omit it. Moved verbatim from ``view_state.py`` (T-decouple).
"""
from __future__ import annotations

import logging
from typing import Any

from core.safe import swallowed
from review.features.image_browser.image_table.builder_view import builder_refresh
# Module scope, like ``builder_refresh`` above: ``controller`` does not import this
# module, so there is no cycle to work around and no reason to spend one of the
# deferred-feature-import budget's slots (``test_deferred_imports``) on it.
from review.features.image_browser.image_table.controller import (
    image_table_refresh_picker,
)

_logger = logging.getLogger("review")


def _snapshot_axes(ctx) -> dict:
    """Row/col grid axes + nested row_dims/col_dims for the image-table snapshot."""
    axes: dict[str, Any] = {}
    for key, fld in (("row_axis", "row_axis"), ("col_axis", "col_axis")):
        v = getattr(ctx.state.image_table, fld, None)
        if isinstance(v, str) and v:
            axes[key] = v
    for key, fld in (("row_dims", "row_dims"), ("col_dims", "col_dims")):
        v = getattr(ctx.state.image_table, fld, None)
        if isinstance(v, list) and v:
            axes[key] = [str(d) for d in v]
    return axes


def _snapshot_spacing(ctx) -> dict:
    """The user-tuned rendered-table gaps + label font sizes (px)."""
    it = ctx.state.image_table
    return {k: int(getattr(it, k, d)) for k, d in
            (("cell_gap", 2), ("row_group_gap", 18), ("col_group_gap", 18),
             ("label_font_pt", 11), ("group_font_pt", 11))}


def _restore_spacing(ctx, state: dict) -> None:
    it = ctx.state.image_table
    for k, lo in (("cell_gap", 0), ("row_group_gap", 0), ("col_group_gap", 0),
                  ("label_font_pt", 6), ("group_font_pt", 6)):
        v = state.get(k)
        if isinstance(v, int):
            setattr(it, k, max(lo, v))


_DISPLAY_DEFAULTS = {
    "scale_bar_show": True,
    "pixel_size_um": 0.0, "scale_bar_um": 50.0, "scale_bar_cells": "br",
    "scale_bar_img_corner": "br", "scale_bar_color": "auto",
    "scale_bar_thickness_px": 4,
    "label_mode": "none", "label_cells": "all", "label_img_corner": "tl",
    "label_font_px": 12, "label_color": "white", "label_bg": True,
}


def _snapshot_display(ctx) -> dict:
    """Scale-bar + burned-in-label display options (the A/B feature fields)."""
    it = ctx.state.image_table
    return {k: getattr(it, k, d) for k, d in _DISPLAY_DEFAULTS.items()}


def _restore_display(ctx, state: dict) -> None:
    it = ctx.state.image_table
    for k, d in _DISPLAY_DEFAULTS.items():
        v = state.get(k)
        if v is None:
            continue
        try:
            if isinstance(d, bool):
                setattr(it, k, bool(v))
            elif isinstance(d, float):
                setattr(it, k, max(0.0, float(v)))
            elif isinstance(d, int):
                setattr(it, k, int(v))
            else:
                setattr(it, k, str(v))
        except (TypeError, ValueError):
            swallowed()


# The three selection filters that decide *what the table is of*, as opposed to
# how it is laid out or drawn. Kept together because they share one rule, stated
# once here: in ``grid_layout._axis_dim_values`` an **empty** set means "all", not
# "none" — ``sel if sel else _well_options(ctx)``. So an omitted key and an empty
# set restore to the same table, which is what lets the snapshot skip the common
# all-wells case instead of writing out ninety-six tokens.
_SCOPE_FIELDS = ("active_wells", "active_timepoints", "active_fovs")


def _snapshot_scope(ctx) -> dict:
    """Which wells / timepoints / FOVs the table covers.

    Without these the axes and the per-channel options round-trip while the
    table's *scope* does not, and a saved three-well single-timepoint grid
    restores as the whole plate across the whole time course — the channels look
    right, so it reads as "the settings did not restore" rather than as an empty
    tab.

    Stored as sorted lists: these are ``set``s, which ``json.dump`` cannot
    serialize, and sorting keeps the persisted file's diffs stable. Wells sort in
    plate order (``parse_rc``) like every other well list in the app.
    """
    from review.domain.viewer_state import parse_rc

    it = ctx.state.image_table
    out: dict = {}
    for fld in _SCOPE_FIELDS:
        vals = {str(v) for v in (getattr(it, fld, None) or set())}
        if not vals:
            continue                      # empty == all; nothing to record
        out[fld] = (sorted(vals, key=parse_rc) if fld == "active_wells"
                    else sorted(vals))
    return out


def _restore_scope(ctx, state: dict) -> None:
    """Put the scope back, and leave it alone when the block does not carry it.

    Deliberately unlike the ``row_dims`` / ``col_dims`` loop in
    :func:`apply_image_table`, which writes ``[]`` for a missing key: a plot
    saved before the scope was captured has none of these keys, and clearing the
    user's live plate selection because an old record is silent about it would
    be a second bug wearing the first one's clothes.
    """
    it = ctx.state.image_table
    for fld in _SCOPE_FIELDS:
        vals = state.get(fld)
        if not isinstance(vals, (list, tuple, set)):
            continue                      # absent (or malformed) → keep current
        try:
            setattr(it, fld, {str(v) for v in vals})
        except Exception:
            swallowed()


def _relight_sidebar_plate(ctx) -> None:
    """Push the restored wells onto the Image Table's own sidebar plate.

    ``state.image_table.active_wells`` is that plate's selection, separate from
    ``selection.selected_wells``. Restoring the state without the widget leaves
    the grid built from three wells while the plate shows none selected — the
    state and the control disagreeing about the same thing.

    State is written before this runs, so ``picker_view._on_plate_changed``
    short-circuits on ``new == active_wells`` and this cannot provoke a second
    layout rebuild.
    """
    if getattr(ctx.runtime.image_table, "sidebar_plate", None) is None:
        return                            # sidebar not built yet (lazy tab)
    try:
        image_table_refresh_picker(ctx)
    except Exception:
        swallowed()


def _snapshot_builder(ctx) -> dict:
    """Builder tree state: per-channel render options + Fixed panel pinned values."""
    out: dict = {}
    copts = dict(getattr(ctx.state.image_table, "channel_opts", None) or {})
    if copts:
        out["channel_opts"] = copts
    fvals = dict(getattr(ctx.state.image_table, "fixed_vals", None) or {})
    if fvals:
        out["fixed_vals"] = fvals
    # The Separate↔Overlay switch. The persisted cells carry the OVERLAY_CHAN
    # sentinel when it's on, so this flag must round-trip for the blend path to
    # re-engage on restore (else the sentinel cells render as "(no image)").
    if bool(getattr(ctx.state.image_table, "composite", False)):
        out["composite"] = True
    return out


def snapshot_image_table(ctx) -> dict:
    """Capture the Image Table grid: dimensions, per-cell well/chan/tp/fov,
    overlay toggles, and per-channel LUT min/max edits."""
    cells = ctx.runtime.image_table.cells
    if not cells:
        return {}
    out: dict[str, Any] = {
        "rows": int(getattr(ctx.state.image_table, "rows", len(cells))),
        "cols": int(getattr(ctx.state.image_table, "cols",
                             len(cells[0]) if cells else 0)),
        "group_size": int(getattr(ctx.state.image_table, "group_size", 0)),
        "group_row_span": int(getattr(ctx.state.image_table, "group_row_span", 0)),
    }
    grid: list[list[dict[str, str]]] = []
    for row in cells:
        row_out: list[dict[str, str]] = []
        for cell in row:
            row_out.append({k: str(cell.get(k, ""))
                            for k in ("well", "chan", "tp", "fov")
                            if cell.get(k)})
        grid.append(row_out)
    out["cells"] = grid
    out.update(_snapshot_axes(ctx))
    ct = ctx.runtime.image_table.crop_tool
    if ct is not None:
        try:
            out["crop"] = list(ct.crop) if ct.crop else None
            out["crop_mode"] = bool(ct.mode)
        except Exception:
            swallowed()
    try:
        out["zoom"] = float(ctx.state.image_table.zoom)
        out.update(_snapshot_spacing(ctx))
    except Exception:
        swallowed()
    out.update(_snapshot_builder(ctx))
    out.update(_snapshot_display(ctx))
    out.update(_snapshot_scope(ctx))
    return out


def apply_image_table(ctx, state: dict) -> None:
    """Restore the Image Table grid (dimensions, per-cell combos, toggles,
    per-channel LUTs) and re-render via image_table_generate."""
    try:
        from review.features.image_browser.image_table.controller import (
            image_table_rebuild_grid,
            image_table_set_zoom,
        )
    except Exception:  # pragma: no cover - import failure leaves these no-ops
        image_table_set_zoom = None
        image_table_rebuild_grid = None

    for key, which in (("row_axis", "row"), ("col_axis", "col")):
        axis = state.get(key)
        if not isinstance(axis, str) or not axis:
            continue
        try:
            setattr(ctx.state.image_table, f"{which}_axis", axis)
            setattr(ctx.runtime.image_table, f"{which}_axis", axis)
        except Exception:
            swallowed()

    for key, fld in (("row_dims", "row_dims"), ("col_dims", "col_dims")):
        dims = state.get(key)
        try:
            setattr(ctx.state.image_table, fld,
                    [str(d) for d in dims] if isinstance(dims, list) else [])
        except Exception:
            swallowed()

    try:
        copts = dict(state.get("channel_opts") or {})
        ctx.state.image_table.channel_opts = copts
        ctx.state.image_table.fixed_vals = dict(state.get("fixed_vals") or {})
        ctx.runtime.image_table.builder_spec = None
        # Overlay: restore the switch + the selected-channel set the blend
        # iterates. active_channels == the channel_opts keys (spec_to_state_fields
        # keys each channel's opts by its axis token), so recover it from there.
        ctx.state.image_table.composite = bool(state.get("composite", False))
        if copts:
            ctx.state.image_table.active_channels = set(copts)
    except Exception:
        swallowed()

    grp = state.get("group_size")
    span = state.get("group_row_span")
    if isinstance(grp, int):
        ctx.state.image_table.group_size = int(grp)
    if isinstance(span, int):
        ctx.state.image_table.group_row_span = int(span)

    rows = state.get("rows")
    cols = state.get("cols")
    if isinstance(rows, int):
        ctx.state.image_table.rows = int(rows)
    if isinstance(cols, int):
        ctx.state.image_table.cols = int(cols)
    if (isinstance(rows, int) or isinstance(cols, int)) and image_table_rebuild_grid is not None:
        try:
            image_table_rebuild_grid(ctx)
        except Exception:
            _logger.debug("image_table: rebuild_grid failed", exc_info=True)

    cells = state.get("cells") or []
    live = ctx.runtime.image_table.cells or []
    if isinstance(cells, list) and live:
        for r, row_state in enumerate(cells):
            if r >= len(live) or not isinstance(row_state, list):
                continue
            for c, cell_state in enumerate(row_state):
                if c >= len(live[r]) or not isinstance(cell_state, dict):
                    continue
                cell = live[r][c]
                if not isinstance(cell, dict):
                    continue
                for key in ("well", "chan", "tp", "fov"):
                    val = cell_state.get(key)
                    if isinstance(val, str) and val:
                        cell[key] = val

    ct = ctx.runtime.image_table.crop_tool
    if ct is not None and "crop" in state:
        try:
            crop = state.get("crop")
            if isinstance(crop, (list, tuple)) and len(crop) == 4:
                ct.set_crop(tuple(int(v) for v in crop), notify=False)
            else:
                ct.clear(notify=False)
            if bool(state.get("crop_mode", False)) != bool(ct.mode):
                ct.toggle_mode(notify=False)
        except Exception:
            swallowed()

    if isinstance(state.get("zoom"), (int, float)) and image_table_set_zoom is not None:
        try:
            image_table_set_zoom(ctx, float(state["zoom"]))
        except Exception:
            _logger.debug("image_table: zoom restore failed", exc_info=True)

    _restore_spacing(ctx, state)
    _restore_display(ctx, state)
    # What the table is *of* — before the shelf refresh below, which reads these
    # through ``spec_from_state``'s filters.
    _restore_scope(ctx, state)
    _relight_sidebar_plate(ctx)
    # Intentionally NOT calling image_table_generate here — restore only pre-fills
    # the dropdowns; the user clicks Generate to load images off disk. That holds
    # for Saved Plots' Restore too: reading a whole grid back off disk is the
    # user's call to make, not something a restore takes on their behalf.
    #
    # It does mean the shelf now describes a table the pictures below it are not
    # of, so the restored table is marked stale by the same flag a shelf edit
    # sets. The header then carries its standing "edited — press Generate" cue,
    # which is what turns "these controls do not match the grid" from something
    # the reader has to notice into something the page says.
    ctx.runtime.image_table.builder_dirty = True

    # Redraw the shelf (rows/cols trees, Fixed panel, tray) so it visually
    # reflects what was just restored instead of the stale spec it was built
    # with before this restore ran. Safe pre-build: no-ops if the shelf's body
    # handles don't exist yet.
    try:
        builder_refresh(ctx)
    except Exception:
        swallowed()

    # Tell the on_dataset_loaded reset hook (registry._loaded_image_table) that
    # a saved grid was just correctly restored this load, so it must not wipe
    # it back to fresh-tab defaults. Reset to False once per dataset load in
    # load_controller.load_directory.
    ctx.runtime.image_table.restored_this_load = True
