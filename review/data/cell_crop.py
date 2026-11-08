"""One cell cut out of a field: its box, its crop, its brightness, its outline.

The Scatter tab's cell popup and the Cell Browser's tiles both show one
segmented cell from a field image. They used to carry a copy each of the same
three steps — the padded bounding box of ``mask == nucleus_id``, a crop scaled to
0..1, the one-pixel outline — and the copies disagreed about brightness: the
Cell Browser used the shared per-channel display window
(``ImageViewState.lut_by_channel``), else the crop's 1st–99th percentile, while
the popup stretched each crop to its own minimum and maximum, so the same cell
looked brighter in one place than the other. Both now come here, and the
brightness rule is :func:`display_window`.

Qt-free and ctx-free: the popup and the Cell Browser call it from a worker
thread.
"""

from __future__ import annotations

import numpy as np

#: The two places this rule is stated to the user, so a caption cannot drift
#: from the rule that drew the pixels.
SHARED_WINDOW = "shared"
AUTO_WINDOW = "auto"


def bbox_for(mask: np.ndarray, nucleus_id: int, pad: int) -> tuple[int, int, int, int] | None:
    """Padded ``(r0, c0, r1, c1)`` box of ``mask == nucleus_id``, clipped to the
    field; ``None`` when the label is not in *mask*."""
    ys, xs = np.where(np.asarray(mask) == nucleus_id)
    if ys.size == 0:
        return None
    r0, r1 = int(ys.min()), int(ys.max()) + 1
    c0, c1 = int(xs.min()), int(xs.max()) + 1
    r0 = max(0, r0 - pad)
    c0 = max(0, c0 - pad)
    r1 = min(mask.shape[0], r1 + pad)
    c1 = min(mask.shape[1], c1 + pad)
    return r0, c0, r1, c1


def display_window(crop: np.ndarray, shared: tuple[float, float] | None
                   ) -> tuple[tuple[float, float], str]:
    """The ``(lo, hi)`` a crop is shown with, and which rule chose it.

    The shared per-channel window when there is one (tuned in any image
    viewer), else the crop's own 1st–99th percentile. A window whose top does
    not exceed its bottom is widened by one so a flat crop still draws.
    """
    if shared is not None:
        lo, hi = float(shared[0]), float(shared[1])
        rule = SHARED_WINDOW
    else:
        a = np.asarray(crop, dtype=np.float32)
        finite = a[np.isfinite(a)]
        if finite.size == 0:
            return (0.0, 1.0), AUTO_WINDOW
        lo = float(np.percentile(finite, 1.0))
        hi = float(np.percentile(finite, 99.0))
        rule = AUTO_WINDOW
    if hi <= lo:
        hi = lo + 1.0
    return (lo, hi), rule


def normalise(crop: np.ndarray, window: tuple[float, float]) -> np.ndarray:
    """*crop* scaled to 0..1 against *window*, clipped."""
    lo, hi = float(window[0]), float(window[1])
    if hi <= lo:
        hi = lo + 1.0
    a = np.asarray(crop, dtype=np.float32)
    # A pixel with no value draws black, never NaN into the composite.
    return np.nan_to_num(np.clip((a - lo) / (hi - lo), 0.0, 1.0), nan=0.0)


def outline(mask_crop: np.ndarray, nucleus_id: int) -> np.ndarray:
    """The one-pixel boundary of label *nucleus_id* inside *mask_crop* (bool)."""
    m = np.asarray(mask_crop).astype(np.int32)
    padded = np.pad(m, 1, mode="constant", constant_values=0)
    center = padded[1:-1, 1:-1]
    return (center == nucleus_id) & (
        (center != padded[:-2, 1:-1]) | (center != padded[2:, 1:-1])
        | (center != padded[1:-1, :-2]) | (center != padded[1:-1, 2:])
    )


#: Padding around a nucleus's box, in pixels. One value for every surface that
#: crops a single cell, so a cell is framed the same in the popup and a tile.
CELL_PAD_PX = 18


class CellCrops:
    """One cell's crops, loaded once for every channel asked for.

    ``crops`` maps each channel that could be shown to its float crop;
    ``missing`` maps each that could not to the sentence saying why. ``outline``
    is the cell's one-pixel boundary in crop coordinates. ``problem`` is set,
    and everything else empty, when the cell itself could not be found — no
    mask for the field, or no such nucleus in it — so a caller shows that
    sentence instead of an empty frame.
    """

    __slots__ = ("crops", "missing", "outline", "problem")

    def __init__(self, crops=None, missing=None, outline=None, problem: str = "") -> None:
        self.crops: dict[str, np.ndarray] = dict(crops or {})
        self.missing: dict[str, str] = dict(missing or {})
        self.outline = outline
        self.problem = problem


def load_cell(ctx, *, well: str, fov: str, tp: str, nucleus_id: int,
              channels: list[str], use_tophat: bool = False) -> CellCrops:
    """Crop *nucleus_id* out of its field for each of *channels*.

    Every frame comes through :mod:`review.data.frame_load`, addressed by well,
    field and timepoint — the resolver the Image Table and the Cell Browser use,
    which refuses an ambiguous match rather than guessing a file. Safe to run off
    the GUI thread, as the Cell Browser's loader does.
    """
    from review.data.frame_load import load_frame_array, load_mask_array

    cache: dict = {}
    scan_misses: set = set()
    arrays = {ch: load_frame_array(ctx, cache, well, str(ch).upper(), tp, fov,
                                   use_tophat=use_tophat, scan_misses=scan_misses)
              for ch in channels}
    mask = load_mask_array(ctx, cache, well, tp, fov)
    if mask is None:
        return CellCrops(problem=f"No segmentation mask was found for {well}, field {fov}, "
                                 f"timepoint {tp}, so the cell cannot be located.")
    box = bbox_for(mask, int(nucleus_id), CELL_PAD_PX)
    if box is None:
        return CellCrops(problem=f"Nucleus {nucleus_id} is not in the segmentation mask of "
                                 f"{well}, field {fov}, timepoint {tp}.")
    r0, c0, r1, c1 = box
    crops: dict[str, np.ndarray] = {}
    missing: dict[str, str] = {}
    for ch, arr in arrays.items():
        if arr is None:
            missing[ch] = f"no {ch} image for this field and timepoint"
            continue
        arr = np.asarray(arr)
        if arr.ndim != 2 or arr.shape[:2] != mask.shape[:2]:
            missing[ch] = (f"the {ch} image is {arr.shape[1]}×{arr.shape[0]} and the mask "
                           f"{mask.shape[1]}×{mask.shape[0]}, so the cell cannot be cut from it")
            continue
        crops[ch] = arr[r0:r1, c0:c1].astype(np.float32)
    return CellCrops(crops=crops, missing=missing,
                     outline=outline(mask[r0:r1, c0:c1], int(nucleus_id)))
