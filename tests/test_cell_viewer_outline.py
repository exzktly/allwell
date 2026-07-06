"""Cell-viewer boundary outline: the red cell outline must survive a
channel/mask frame-size mismatch.

Regression: ``_make_magnified_thumb`` painted the outline only when
``outline.shape == disp.shape`` and silently dropped it otherwise, so the red
"boundary circle" vanished whenever the displayed channel frame differed in
size from the segmentation-mask frame. The outline is now resampled to the
display buffer instead of dropped.

Exercises the pure static helpers directly — no QApplication / full app.
"""

from __future__ import annotations

import numpy as np

from well_viewer.features.scatter.cell_viewer_view import ScatterCellViewer


def _rgb(h, w):
    disp = np.zeros((h, w), dtype=np.uint8)
    return np.stack([disp, disp, disp], axis=-1)


def test_paint_outline_exact_shape_draws_red():
    rgb = _rgb(10, 10)
    outline = np.zeros((10, 10), dtype=bool)
    outline[0, :] = True
    ScatterCellViewer._paint_outline(rgb, outline)
    assert (rgb[0, :] == [255, 0, 0]).all()
    assert (rgb[5, 5] == 0).all()  # untouched interior stays black


def test_paint_outline_resamples_on_shape_mismatch():
    # disp is 20x20, outline is 10x10 — must resample rather than drop.
    rgb = _rgb(20, 20)
    outline = np.zeros((10, 10), dtype=bool)
    outline[0, :] = True  # top row
    ScatterCellViewer._paint_outline(rgb, outline)
    red = (rgb == [255, 0, 0]).all(axis=-1)
    assert red.any(), "outline was dropped on a shape mismatch"
    assert red[0].all(), "resampled top-row outline should cover the top rows"


def test_paint_outline_none_is_noop():
    rgb = _rgb(4, 4)
    ScatterCellViewer._paint_outline(rgb, None)
    assert (rgb == 0).all()


def test_resize_mask_nearest_shape_and_content():
    m = np.zeros((4, 4), dtype=bool)
    m[0, 0] = True
    out = ScatterCellViewer._resize_mask_nearest(m, (8, 8))
    assert out.shape == (8, 8)
    assert out[0, 0]
    assert not out[7, 7]
