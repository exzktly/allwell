"""Playground tab — the zoom/pan blit onto the Playground canvas.

The RGB build itself lives in ``well_viewer.core.frame_render.build_frame_rgb``,
outside this package, because the Playground is a *removable* feature and only
``features/registry.py`` may import a feature at all — anything a second pane
could want to reuse has to live in ``core``. ``build_playground_rgb`` stays here
as the name this package's callers and tests use.

``render_playground_display`` is the only app-touching function — a
self-contained zoom/pan loop modelled on ``render_review_image_display`` that
writes to ``app._playground_image_label`` so it can never destabilise the Review
Image canvas.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from well_viewer.core.frame_render import (  # noqa: F401 — re-exported
    boundary_mask as _boundary_mask,
    build_frame_rgb as build_playground_rgb,
)


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
