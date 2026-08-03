"""Single-frame RGB rendering, shared by every pane that previews one frame.

``build_frame_rgb`` turns ``(image, label_mask)`` plus LUT / colormap / tint /
binary options into an (H, W, 3) uint8 RGB. It reuses
``ui_helpers.lut_colormap_to_rgb`` for the LUT+colormap math and the same
padded-diff boundary trick the Image Table and Review Image renderers use; the
binary branch mirrors ``review_image.renderer.draw_review_image`` (labelled cells
white).

It lives in ``core`` rather than in the Playground package that grew it because
the Playground is a *removable* feature (``profiles/full.toml``): nothing outside
``features/registry.py`` may import a feature package, so anything a second pane
might ever render a frame with has to sit outside it.

Qt-free: pure numpy in, numpy out. The blit that puts the result on screen is
``playground.renderer.render_playground_display``.
"""

from __future__ import annotations

import numpy as np

from well_viewer.core.ui_helpers import lut_colormap_to_rgb


def boundary_mask(mask_arr, shape) -> np.ndarray | None:
    """Bool boundary of an int label array (padded-diff), or None if absent."""
    if mask_arr is None:
        return None
    center = np.asarray(mask_arr, dtype=np.int32)
    if center.shape[:2] != tuple(shape[:2]):
        return None
    padded = np.pad(center, 1, mode="constant", constant_values=0)
    c = padded[1:-1, 1:-1]
    return (c > 0) & (
        (c != padded[:-2, 1:-1]) | (c != padded[2:, 1:-1]) |
        (c != padded[1:-1, :-2]) | (c != padded[1:-1, 2:])
    )


# Golden-ratio hue stepping. Consecutive label ids land far apart on the colour
# wheel, which is the whole point — the ids a segmenter assigns to touching
# nuclei are usually consecutive, and those are exactly the pairs that must not
# come out the same colour. Saturation and value step by their own irrationals so
# two ids whose hues happen to land close still differ.
_HUE_STEP = 0.6180339887498949
_SAT_STEP = 0.7548776662466927
_VAL_STEP = 0.5698402909980532


def label_color_lut(max_id: int) -> np.ndarray:
    """``(max_id + 1, 3)`` uint8 palette indexed by label id; row 0 is black.

    Deterministic — a pure function of the id, with no RNG anywhere. Colours must
    not change between two renders of the same frame: a cell that is teal before
    a LUT tweak and orange after it looks like a different cell, which is the
    opposite of what colouring the labels is for.
    """
    from matplotlib.colors import hsv_to_rgb
    ids = np.arange(max(int(max_id), 0) + 1, dtype=np.float64)
    hsv = np.stack([(ids * _HUE_STEP) % 1.0,
                    0.60 + 0.35 * ((ids * _SAT_STEP) % 1.0),
                    0.70 + 0.30 * ((ids * _VAL_STEP) % 1.0)], axis=-1)
    lut = np.clip(hsv_to_rgb(hsv) * 255.0, 0, 255).astype(np.uint8)
    lut[0] = 0                                   # label 0 is background
    return lut


def fill_label_rgb(rgb, center, keep, label_colors: bool) -> None:
    """Paint the kept labels into *rgb* — one colour each, or all white.

    The single place the white-or-rainbow decision is made. The Playground's
    binary view and the Image Table's cell-mask / ≥threshold cells both call it,
    so a nucleus is the same colour in both panes and there is one palette to
    keep deterministic rather than two.
    """
    if not label_colors:
        rgb[keep] = (255, 255, 255)
        return
    lut = label_color_lut(int(center.max()) if center.size else 0)
    rgb[keep] = lut[center[keep]]


def build_frame_rgb(fluor_arr, mask_arr, *, lo=None, hi=None,
                    cmap_name: str = "", binary: bool = False,
                    show_boundary: bool = True,
                    boundary_color=(255, 255, 255),
                    tint=(255, 255, 255), above_ids=None,
                    threshold=None, label_colors: bool = False) -> np.ndarray:
    """Render one frame (+ an optional label-mask overlay) to RGB.

    ``binary`` paints an above-threshold mask on black. With a label mask and
    ``above_ids`` (the label ids whose mean intensity clears the frame-local
    threshold) those cells are filled; with no mask it falls back to a pixel
    threshold (``arr > threshold``); otherwise it shows the labelled footprint.
    Non-binary shows the LUT+colormap image with an optional tint and white cell
    boundaries from ``mask_arr``.

    ``label_colors`` gives each label its own colour instead of white. All-white
    cells that touch merge into one blob on screen — two nuclei sharing a border
    are indistinguishable from one large object, which is precisely the mistake
    a segmentation preview exists to catch. It has no effect on the *per-pixel*
    fallback: with no usable mask there are no labels to colour, and that branch
    stays white rather than inventing a distinction it cannot make.
    """
    arr = np.asarray(fluor_arr, dtype=np.float32)
    h, w = arr.shape[:2]

    if binary:
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        center = None
        if mask_arr is not None:
            c = np.asarray(mask_arr, dtype=np.int32)
            if c.shape[:2] == (h, w):
                center = c
        if center is not None and above_ids is not None:
            ids = np.fromiter((int(i) for i in above_ids), dtype=center.dtype,
                              count=len(above_ids))
            if ids.size:
                keep = np.isin(center, ids)
                fill_label_rgb(rgb, center, keep, label_colors)
        elif threshold is not None:
            rgb[arr > float(threshold)] = (255, 255, 255)
        elif center is not None:
            fill_label_rgb(rgb, center, center > 0, label_colors)
        return rgb

    rgb = lut_colormap_to_rgb(arr, lo, hi, cmap_name)
    tr, tg, tb = (max(0, min(255, int(c))) for c in tint)
    if (tr, tg, tb) != (255, 255, 255):
        f = rgb.astype(np.float32)
        f[..., 0] *= tr / 255.0
        f[..., 1] *= tg / 255.0
        f[..., 2] *= tb / 255.0
        rgb = np.clip(f, 0, 255).astype(np.uint8)

    if show_boundary:
        boundary = boundary_mask(mask_arr, arr.shape)
        if boundary is not None:
            rgb = np.ascontiguousarray(rgb)
            rgb[boundary] = np.asarray(boundary_color, dtype=np.uint8)
    return np.ascontiguousarray(rgb)
