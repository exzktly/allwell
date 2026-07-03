"""Cell Browser image loading — composes per-cell thumbnails off the UI thread.

Given a page of :class:`~well_viewer.features.image_browser.cell_browser.collect.CellDescriptor`,
loads each cell's FOV channel images + segmentation mask (reusing the Image Table
loaders, so the zipfile cache is shared), crops to the cell, and builds a
multi-channel composite RGB. Runs on a daemon thread and pushes results back to
the GUI through a queued ``Signal`` (the smFISH / Playground bridge pattern) so
the grid fills incrementally and the UI never blocks. Composition is numpy-only.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

import numpy as np
from PySide6.QtCore import QObject, Signal

from well_viewer.features.image_browser.cell_browser.collect import CellDescriptor
from well_viewer.features.image_browser.image_table.image_compose import _load_array, _load_mask_array

_logger = logging.getLogger("well_viewer")

# Additive tints for the multi-channel composite (black background → hue), in
# channel order. Falls back to white for channels beyond the list.
_CHANNEL_TINTS = (
    (0.0, 1.0, 0.0),  # green
    (1.0, 0.0, 0.0),  # red / magenta-ish
    (0.0, 0.6, 1.0),  # blue
    (1.0, 0.0, 1.0),  # magenta
    (0.0, 1.0, 1.0),  # cyan
    (1.0, 1.0, 0.0),  # yellow
)
_CROP_PAD_PX = 18


def resolve_channel_tint(channel: str, index: int,
                         tint_map: Optional[Dict[str, tuple]] = None) -> tuple:
    """Additive tint for a channel as an ``(r, g, b)`` 0..1 float tuple.

    An explicit per-channel override (the shared ``tint_by_channel``, 0–255)
    wins; otherwise fall back to the fixed palette by channel index. Pure (no
    Qt / ctx), so it's unit-testable on its own.
    """
    override = (tint_map or {}).get(str(channel).strip().lower())
    if override is not None:
        return tuple(max(0.0, min(1.0, float(c) / 255.0)) for c in override)
    return _CHANNEL_TINTS[index % len(_CHANNEL_TINTS)]


def _bbox_for(mask_arr, nucleus_id: int, pad: int) -> Optional[tuple]:
    """Padded (r0, c0, r1, c1) bounding box of ``mask == nucleus_id``; None if absent."""
    ys, xs = np.where(mask_arr == nucleus_id)
    if ys.size == 0:
        return None
    r0, r1 = int(ys.min()), int(ys.max()) + 1
    c0, c1 = int(xs.min()), int(xs.max()) + 1
    r0 = max(0, r0 - pad); c0 = max(0, c0 - pad)
    r1 = min(mask_arr.shape[0], r1 + pad); c1 = min(mask_arr.shape[1], c1 + pad)
    return r0, c0, r1, c1


def _norm01(crop, bounds: Optional[tuple] = None) -> np.ndarray:
    """Scale a 2-D float crop to 0..1.

    With *bounds* ``(lo, hi)`` — the shared per-channel display LUT (S4) — clip
    to that fixed window so the Cell Browser matches the brightness tuned in the
    other image viewers. Without it, fall back to a robust 1–99 percentile of
    the crop (auto).
    """
    a = np.asarray(crop, dtype=np.float32)
    if bounds is not None:
        lo, hi = float(bounds[0]), float(bounds[1])
        if hi <= lo:
            hi = lo + 1.0
        return np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return np.zeros(a.shape, dtype=np.float32)
    lo = float(np.percentile(finite, 1.0))
    hi = float(np.percentile(finite, 99.0))
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0)


def compose_cell_rgb(ctx, desc: CellDescriptor, channels: List[str],
                     show_boundaries: bool, cache: Dict,
                     lut_by_channel: Optional[Dict[str, tuple]] = None,
                     tint_by_channel: Optional[Dict[str, tuple]] = None,
                     use_tophat: bool = False) -> Optional[np.ndarray]:
    """Multi-channel composite RGB (uint8) of one cell, or None if unrenderable.

    *cache* is a per-page dict so each FOV's mask + channel arrays load once.
    *lut_by_channel* maps a lower-cased channel token to its shared ``(lo, hi)``
    display window (S4); a channel with an entry uses that fixed brightness,
    others auto-scale per crop. *tint_by_channel* overrides the additive display
    colour per channel (else the fixed per-index palette). *use_tophat* selects
    the corrected (top-hat) image instead of raw.
    """
    lut_by_channel = lut_by_channel or {}
    tint_by_channel = tint_by_channel or {}
    try:
        nid = int(float(desc.nucleus_id))
    except (TypeError, ValueError):
        return None
    # Load the channel arrays FIRST: _load_array (→ _load_well_channel) populates
    # the (well, "__mask__", "raw") cache entry as a side effect, and
    # _load_mask_array depends on it. Calling _load_mask_array before any
    # _load_array always missed → every cell rendered as "—".
    chan_arrays = [
        _load_array(ctx, cache, desc.well, ch, desc.tp_token, desc.fov,
                    use_tophat=use_tophat)
        for ch in channels
    ]
    mask_full = _load_mask_array(ctx, cache, desc.well, desc.tp_token, desc.fov)
    if mask_full is None:
        return None
    mask_full = np.asarray(mask_full)
    box = _bbox_for(mask_full, nid, _CROP_PAD_PX)
    if box is None:
        return None
    r0, c0, r1, c1 = box
    mask_crop = mask_full[r0:r1, c0:c1]

    rgb = np.zeros((r1 - r0, c1 - c0, 3), dtype=np.float32)
    painted = False
    for i, arr in enumerate(chan_arrays):
        if arr is None:
            continue
        arr = np.asarray(arr)
        if arr.ndim != 2 or arr.shape[:2] != mask_full.shape[:2]:
            continue
        bounds = lut_by_channel.get(str(channels[i]).strip().lower())
        norm = _norm01(arr[r0:r1, c0:c1], bounds=bounds)
        tint = resolve_channel_tint(channels[i], i, tint_by_channel)
        rgb = np.maximum(rgb, norm[..., None] * np.asarray(tint, dtype=np.float32))
        painted = True
    if not painted:
        return None

    out = (np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    if show_boundaries:
        m = mask_crop.astype(np.int32)
        padded = np.pad(m, 1, mode="constant", constant_values=0)
        center = padded[1:-1, 1:-1]
        boundary = (center == nid) & (
            (center != padded[:-2, 1:-1]) | (center != padded[2:, 1:-1]) |
            (center != padded[1:-1, :-2]) | (center != padded[1:-1, 2:])
        )
        out[boundary] = (255, 255, 0)
    return out


class CellImageLoader(QObject):
    """Loads a page of cell thumbnails on a daemon thread.

    Emits ``cell_ready`` per composed cell and ``page_finished`` at the end. Every
    payload carries the ``gen`` token captured at launch so the controller can
    drop results from a superseded filter run.
    """

    cell_ready = Signal(object)      # {"gen", "index", "rgb", "desc"}
    page_finished = Signal(object)   # {"gen", "count"}

    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        # The most recent generation token handed to load_page. A daemon
        # loader checks this before every compose and bails the instant a
        # newer page supersedes it — otherwise a burst of rapid paging left a
        # pile of unbounded daemon threads all still reading the shared
        # zipfile cache (which a dataset swap can invalidate mid-read),
        # producing noisy exceptions and wasted work (audit E22).
        self._latest_gen: int = -1

    def cancel(self) -> None:
        """Supersede any in-flight loader so it stops before its next compose.

        Called on dataset swap / teardown: bumping ``_latest_gen`` past every
        live page token makes each running thread's ``gen != self._latest_gen``
        check trip, so it stops reading the (soon-to-be-invalidated) cache.
        """
        self._latest_gen += 1

    def load_page(self, descriptors: List[CellDescriptor], start_index: int,
                  channels: List[str], show_boundaries: bool, gen: int,
                  use_tophat: bool = False) -> None:
        """Spawn a daemon thread that composes *descriptors* and emits each."""
        self._latest_gen = gen
        # Snapshot the shared per-channel LUT + tint (S4) on the GUI thread so
        # the daemon thread doesn't read ctx state concurrently; a copy is cheap
        # and the page reloads whenever the LUT/tint/source could matter.
        _iv = getattr(self._ctx.state, "image_view", None)
        lut_map = dict(_iv.lut_by_channel) if _iv is not None else {}
        tint_map = dict(_iv.tint_by_channel) if _iv is not None else {}

        def _run() -> None:
            cache: Dict[str, Any] = {}
            n = 0
            for offset, desc in enumerate(descriptors):
                # A newer page (or a dataset swap that bumped the gen) has
                # superseded this run — stop compositing immediately instead
                # of grinding through the rest of the page against a cache
                # that may already be invalidated (audit E22).
                if gen != self._latest_gen:
                    return
                try:
                    rgb = compose_cell_rgb(self._ctx, desc, channels,
                                           show_boundaries, cache, lut_map,
                                           tint_map, use_tophat)
                except Exception:
                    _logger.debug("cell_browser: compose failed for %r",
                                  desc, exc_info=True)
                    rgb = None
                self.cell_ready.emit({
                    "gen": gen, "index": start_index + offset,
                    "rgb": rgb, "desc": desc,
                })
                n += 1
            self.page_finished.emit({"gen": gen, "count": n})

        t = threading.Thread(target=_run, name="cell-browser-loader", daemon=True)
        t.start()
