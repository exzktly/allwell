"""The Image Table's pooled Auto window: one display range per channel across the whole grid.

Moved out of ``controller`` (whose line budget it held) as one cohesive pass; ``controller`` re-exports it.
Centring a nanowell fold change's window on "no change", when its LUT row asks, is ``_colour_key.centred_window``
(ARCHITECTURE §6.2l).
"""

from __future__ import annotations

from core.safe import swallowed
from review.features.image_browser.image_table._colour_key import centred_window
from review.features.image_browser.image_table.grid_layout import _find_ratio_for_channel
from review.features.image_browser.image_table.image_compose import (
    OVERLAY_CHAN,
    _binary_base,
    _channel_opts_for,
    _is_binary_variant,
    _is_nuc_seg,
    _load_array,
    _load_ratio_array,
    _parse_lut,
    _row_display_opts,
    is_raw_fallback,
)


def _compute_auto_luts(ctx, cells, cache, crop_tool) -> None:
    """Pool ONE Auto-LUT window per channel across the whole grid.

    A channel left on Auto otherwise auto-fits each cell to its own min/max, so the
    same channel's brightness isn't comparable cell-to-cell. Here we compute a single
    (min, max) per base channel over every Auto cell of that channel and stash it on
    ``auto_lut_by_channel``; ``_parse_lut`` then returns it for those cells (live +
    export). The map is cleared first, so ``_parse_lut`` still reports the
    pre-pooling (Auto) state while we decide which cells qualify. Binary / mask /
    NUC+SEG / overlay / pre-rendered-RGB cells carry no LUT and are skipped."""
    import numpy as _np

    st = ctx.state.image_table
    st.auto_lut_by_channel = {}
    pooled: dict[str, list] = {}  # base_chan_lower -> [min, max]
    for r, row in enumerate(cells):
        for cell in row:
            chan = cell["chan"]
            well, tp, fov = cell["well"], cell["tp"], cell["fov"]
            if not (well and chan and tp):
                continue
            if chan == OVERLAY_CHAN or _is_nuc_seg(chan) or _is_binary_variant(chan):
                continue
            use_tophat, _bnd, binary_mode = _row_display_opts(ctx, r, chan)
            if binary_mode in ("mask", "threshold"):
                continue
            # Only pool channels actually on Auto (no explicit / shared window). The
            # map is empty here, so _parse_lut gives the pre-pooling answer.
            if _parse_lut(ctx, r, chan) != (None, None):
                continue
            # "per cell" on the channel's LUT row: each cell keeps its own auto-fit.
            if (_channel_opts_for(ctx, chan) or {}).get("auto_per_cell"):
                continue
            # Per cell, so one unreadable frame costs the pooling that cell's
            # contribution and nothing more — pooling the rest still beats
            # dropping every channel back to per-cell auto-fit.
            try:
                ratio = _find_ratio_for_channel(ctx, chan)
                if ratio is not None:
                    arr = _load_ratio_array(ctx, cache, well, ratio, tp, fov,
                                            use_tophat=use_tophat)
                else:
                    arr = _load_array(ctx, cache, well, chan, tp, fov,
                                      use_tophat=use_tophat)
            except Exception:
                swallowed()  # the render loop reports this cell
                continue
            if arr is None or getattr(arr, "ndim", 2) >= 3:
                continue
            # A cell served the raw frame is not on the corrected population's
            # scale — it still carries the camera offset and the background the
            # top-hat would have removed. Pooling it in stretches the window by
            # that offset and renders every genuinely corrected cell of the
            # channel near-black. Pool the corrected population only; a raw cell
            # falls through to its own auto-fit, which is the honest answer for a
            # measurement that is not comparable to the rest of the grid.
            if is_raw_fallback(ctx, cell):
                continue
            a = crop_tool.apply_to_array(arr) if crop_tool is not None else arr
            a = _np.asarray(a, dtype=_np.float32)
            a = a[_np.isfinite(a)]          # a nanowell frame's NaN is "nothing here", not a value
            if a.size == 0:
                continue
            lo, hi = float(a.min()), float(a.max())
            positive = a[a > 0]
            low_pos = float(positive.min()) if positive.size else None
            base = _binary_base(chan).strip().lower()
            cur = pooled.get(base)
            if cur is None:
                pooled[base] = [lo, hi, low_pos, chan]
            else:
                cur[0] = min(cur[0], lo)
                cur[1] = max(cur[1], hi)
                if low_pos is not None:
                    cur[2] = low_pos if cur[2] is None else min(cur[2], low_pos)
    st.auto_lut_by_channel = {
        k: centred_window(ctx, v, bool((_channel_opts_for(ctx, v[3]) or {}).get("centre_on_change")))
        for k, v in pooled.items()}
