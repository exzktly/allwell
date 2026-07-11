"""Playground tab — pure rendering + zoom/pan blit.

``build_playground_rgb`` is app-free: it turns ``(fluor_arr, mask_arr)`` plus LUT
/ colormap / tint / binary options into an (H, W, 3) uint8 RGB, reusing
``ui_helpers.lut_colormap_to_rgb`` for the LUT+colormap math and the same
padded-diff boundary trick the Image Table / Review Image renderers use. The
binary branch mirrors ``review_image.renderer.draw_review_image`` (labelled cells
white). ``render_playground_display`` is the only app-touching function — a
self-contained zoom/pan loop modelled on ``render_review_image_display`` that
writes to ``app._playground_image_label`` so it can never destabilise the Review
Image canvas.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from well_viewer.core.ui_helpers import lut_colormap_to_rgb


def _boundary_mask(mask_arr, shape) -> "np.ndarray | None":
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


def build_playground_rgb(fluor_arr, mask_arr, *, lo=None, hi=None,
                         cmap_name: str = "", binary: bool = False,
                         show_boundary: bool = True,
                         boundary_color=(255, 255, 255),
                         tint=(255, 255, 255), above_ids=None,
                         threshold=None) -> np.ndarray:
    """Render a corrected frame (+ optional precomputed-mask overlay) to RGB.

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
        boundary = _boundary_mask(mask_arr, arr.shape)
        if boundary is not None:
            rgb = np.ascontiguousarray(rgb)
            rgb[boundary] = np.asarray(boundary_color, dtype=np.uint8)
    return np.ascontiguousarray(rgb)


def render_playground_display(win) -> None:
    """Blit ``win._app_state.playground.base_rgb`` onto the Playground canvas,
    fit-to-viewport × zoom, honouring pan. Modelled on
    ``render_review_image_display`` but isolated to the Playground widgets."""
    label = getattr(win, "_playground_image_label", None)
    canvas = getattr(win, "_playground_image_canvas", None)
    if label is None or canvas is None:
        return
    st = win._app_state.playground
    rgb = st.base_rgb
    if rgb is None:
        label.clear()
        return
    rgb = np.ascontiguousarray(rgb)
    ih, iw = rgb.shape[:2]
    vp = canvas.viewport()
    cw, ch = max(1, vp.width()), max(1, vp.height())
    fit = min(cw / max(iw, 1), ch / max(ih, 1))
    scale = max(0.05, fit * max(0.1, float(st.zoom)))
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))

    qimg = QImage(rgb.data, iw, ih, 3 * iw, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    if (nw, nh) != (iw, ih):
        pm = pm.scaled(nw, nh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    label.setPixmap(pm)
    label.resize(max(nw, cw), max(nh, ch))
    # Geometry for the hover-tooltip pixel mapping (label space → image pixel).
    win._playground_disp = {"nw": nw, "nh": nh, "iw": iw, "ih": ih}

    cx = max(0, (max(nw, cw) - cw) // 2) - int(st.pan_x)
    cy = max(0, (max(nh, ch) - ch) // 2) - int(st.pan_y)
    hbar, vbar = canvas.horizontalScrollBar(), canvas.verticalScrollBar()
    hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), cx)))
    vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), cy)))
