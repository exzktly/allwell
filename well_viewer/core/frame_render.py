"""Single-frame RGB rendering, shared by every pane that previews one frame.

``build_frame_rgb`` turns ``(image, label_mask)`` plus LUT / colormap / tint /
binary options into an (H, W, 3) uint8 RGB. It reuses
``ui_helpers.lut_colormap_to_rgb`` for the LUT+colormap math and the same
padded-diff boundary trick the Image Table and Review Image renderers use; the
binary branch mirrors ``review_image.renderer.draw_review_image`` (labelled cells
white).

It lives in ``core`` rather than in the Playground package that grew it because
the Analyze pane's Preview page renders the same thing, and the Playground is a
*removable* feature (``profiles/full.toml``) — a build that drops it must not
take the Analyze preview down with it, and nothing outside
``features/registry.py`` may import a feature package anyway.

Qt-free: pure numpy in, numpy out. The blit that puts the result on screen is
``widgets.frame_preview.FramePreview`` (Analyze) or
``playground.renderer.render_playground_display`` (Review).
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


def build_frame_rgb(fluor_arr, mask_arr, *, lo=None, hi=None,
                    cmap_name: str = "", binary: bool = False,
                    show_boundary: bool = True,
                    boundary_color=(255, 255, 255),
                    tint=(255, 255, 255), above_ids=None,
                    threshold=None) -> np.ndarray:
    """Render one frame (+ an optional label-mask overlay) to RGB.

    ``binary`` paints an above-threshold mask white on black. With a label mask
    and ``above_ids`` (the label ids whose mean intensity clears the frame-local
    threshold) those cells are filled; with no mask it falls back to a pixel
    threshold (``arr > threshold``); otherwise it shows the labelled footprint.
    Non-binary shows the LUT+colormap image with an optional tint and white cell
    boundaries from ``mask_arr``.
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
                rgb[np.isin(center, ids)] = (255, 255, 255)
        elif threshold is not None:
            rgb[arr > float(threshold)] = (255, 255, 255)
        elif center is not None:
            rgb[center > 0] = (255, 255, 255)
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
