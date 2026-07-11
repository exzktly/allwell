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

from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")


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


def _snapshot_builder(ctx) -> dict:
    """Builder tree state: per-channel render options + Fixed panel pinned values."""
    out: dict = {}
    copts = dict(getattr(ctx.state.image_table, "channel_opts", None) or {})
    if copts:
        out["channel_opts"] = copts
    fvals = dict(getattr(ctx.state.image_table, "fixed_vals", None) or {})
    if fvals:
        out["fixed_vals"] = fvals
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
    return out


def apply_image_table(ctx, state: dict) -> None:
    """Restore the Image Table grid (dimensions, per-cell combos, toggles,
    per-channel LUTs) and re-render via image_table_generate."""
    try:
        from well_viewer.features.image_browser.image_table.controller import (
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
        ctx.state.image_table.channel_opts = dict(state.get("channel_opts") or {})
        ctx.state.image_table.fixed_vals = dict(state.get("fixed_vals") or {})
        ctx.runtime.image_table.builder_spec = None
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
    # Intentionally NOT calling image_table_generate here — restore only pre-fills
    # the dropdowns; the user clicks Generate to load images off disk.
