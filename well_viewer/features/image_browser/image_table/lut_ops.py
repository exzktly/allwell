"""Image Table — per-row LUT min/max pooling (Fit + post-render auto-refresh).

The two "pool the loaded arrays' intensity range into the LUT boxes" helpers,
split out of ``controller.py`` to keep it under its line budget. Both operate on
LUT *groups*: normally one grid row = one group, but a grouped/tiled layout
shares one LUT row across all bands of a channel (``lut_row_map``), so the pooled
range spans every band — the shared-per-channel LUT. ``controller`` re-exports
these names, so its call sites (``image_table_rebuild_lut_row`` passes
``image_table_auto_lut`` as ``on_auto_lut``; ``image_table_generate`` calls
``_refresh_lut_fields_from_render``) are unchanged.

Depends only on the ``image_compose`` layer (``_load_array`` / ``_row_display_opts``)
and ``ctx`` — never on ``controller`` — so importing it introduces no cycle.
"""

from __future__ import annotations

from typing import Optional

from well_viewer.features.image_browser.image_table.image_compose import (
    _load_array,
    _row_display_opts,
)


def _refresh_lut_fields_from_render(ctx, rendered) -> None:
    """Pool the actual min/max per LUT GROUP from the just-rendered arrays and
    write the numbers into that group's LUT box, except boxes the user overrode.

    Each entry carries an ``auto`` flag (True until the user edits its min/max);
    only auto boxes get refreshed, so manual overrides stick. In a grouped/tiled
    layout one LUT box is shared across all band-rows of a channel, so the pooled
    range spans every band (matching the shared-per-channel LUT).
    """
    luts = getattr(ctx.runtime.image_table, "lut", None) or {}
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not luts or not cells:
        return
    try:
        import numpy as _np
    except Exception:
        return

    lut_map = getattr(ctx.runtime.image_table, "lut_row_map", None) or list(range(len(cells)))
    n_lut = (max(lut_map) + 1) if lut_map else 0
    for li in range(n_lut):
        entry = luts.get(li)
        if not isinstance(entry, dict) or not entry.get("auto", True):
            continue  # missing or user-edited — leave alone
        group_rows = [r for r in range(len(cells)) if r < len(lut_map) and lut_map[r] == li]
        lo: Optional[float] = None
        hi: Optional[float] = None
        for r in group_rows:
            for c in range(len(cells[r])):
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


def image_table_auto_lut(ctx, lut_idx: int) -> None:
    """Pool min/max across LUT-group *lut_idx*'s loaded cells; write the box.

    Fit pools the image range of every cell in the group's rows. Normally one
    grid row = one group, but a grouped/tiled layout shares one LUT row across
    all bands of a channel, so this pools across *all* those band-rows — the
    shared min/max then covers every band. Honours the group's top-hat toggle.
    """
    cells = getattr(ctx.runtime.image_table, "cells", None) or []
    if not cells:
        return
    lut_map = getattr(ctx.runtime.image_table, "lut_row_map", None) or list(range(len(cells)))
    group_rows = [r for r in range(len(cells))
                  if r < len(lut_map) and lut_map[r] == lut_idx]
    if not group_rows:
        if 0 <= lut_idx < len(cells):
            group_rows = [lut_idx]
        else:
            return
    rendered = getattr(ctx.state.image_table, "last_render", None) or {}
    cache = getattr(ctx.state.image_table, "image_cache", None)
    if cache is None:
        cache = {}
        ctx.state.image_table.image_cache = cache
    # Display opts are shared across the group; read from a representative row.
    use_tophat, _show_b, _bm = _row_display_opts(ctx, group_rows[0])

    try:
        import numpy as _np
    except Exception:
        ctx.set_status("Image Table: numpy unavailable; cannot compute auto LUT.")
        return

    pooled_min: Optional[float] = None
    pooled_max: Optional[float] = None
    for r in group_rows:
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
                    arr = _load_array(ctx, cache, well, chan.upper(), tp, fov, use_tophat=use_tophat)
            if arr is None:
                continue
            a = _np.asarray(arr, dtype=_np.float32)
            mn = float(a.min())
            mx = float(a.max())
            pooled_min = mn if pooled_min is None else min(pooled_min, mn)
            pooled_max = mx if pooled_max is None else max(pooled_max, mx)

    if pooled_min is None or pooled_max is None:
        ctx.set_status(
            f"Image Table: no loaded images for row {lut_idx + 1}; cannot auto-LUT."
        )
        return
    if pooled_max <= pooled_min:
        pooled_max = pooled_min + 1.0

    luts = getattr(ctx.runtime.image_table, "lut", None) or {}
    entry = luts.get(lut_idx)
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
