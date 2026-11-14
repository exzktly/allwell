"""Show in Segmentation zooms in on the focused cell and outlines it in yellow.

Through the real app and a real field of realistic size: 1024 px across, with a
16 px nucleus. The jump used to zoom to a fixed 3x the fit-to-window view, which
on such a field is about 2 screen pixels per image pixel — the nucleus ~32 px
on screen, not zoomed in on — and outlined the cell with a one-pixel yellow line
as thin as the red round every other cell.
"""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.qt

#: Nucleus 1 is 16 px square; nucleus 2, a larger neighbour, is the one jumped to in the last test.
_BOXES = ((500, 516, 300, 316), (200, 260, 700, 760))


@pytest.fixture
def app(viewer_app, tmp_path):
    from PySide6.QtWidgets import QApplication

    from tests._page_datasets import image_plate

    viewer_app.resize(1400, 900)
    viewer_app.show()
    viewer_app._load_path(image_plate(tmp_path / "plate", size=1024, boxes=_BOXES) / "out")
    QApplication.processEvents()
    return viewer_app


def _show(app, qtbot, nucleus_id, fov="F001"):
    from PySide6.QtWidgets import QApplication

    ctx = app._ctx
    assert ctx.features.query("image_browser.show_cell", ctx, "A01", fov, "0h", str(nucleus_id),
                              default=None)
    for _ in range(4):                       # and the re-centre queued once the page is laid out
        QApplication.processEvents()
        qtbot.wait(20)
    return ctx


def _on_screen(ctx, nucleus_id):
    """The cell's extent and centre on screen, from the zoom and scroll the canvas really shows."""
    rt, ri = ctx.runtime.review_image, ctx.state.review_image
    vp = rt.canvas.viewport()
    ih, iw = rt.base_rgb.shape[:2]
    scale = min(vp.width() / iw, vp.height() / ih) * ri.zoom
    ys, xs = np.where(rt.label._mask_arr == nucleus_id)
    extent = (max(xs.max() - xs.min(), ys.max() - ys.min()) + 1) * scale
    centre = (xs.mean() * scale - rt.canvas.horizontalScrollBar().value(),
              ys.mean() * scale - rt.canvas.verticalScrollBar().value())
    return extent, centre, (vp.width(), vp.height())


def test_the_cell_fills_a_good_part_of_the_canvas_and_sits_in_its_middle(app, qtbot):
    from review.features.image_browser.review_image.renderer import _MAX_PIXMAP_SIDE

    ctx = _show(app, qtbot, 1)
    extent, (cx, cy), (w, h) = _on_screen(ctx, 1)
    # The jump aims the cell at 40% of the canvas, but the renderer scales the whole field into one
    # pixmap, so the zoom is capped where that pixmap's longest side reaches _MAX_PIXMAP_SIDE: on this
    # 1024 px field that is 8 screen px per image px, and the 16 px nucleus is drawn 128 px across —
    # zoomed in on (it was ~32 at the old fixed 3x), without the gigabyte pixmap 40% would need here.
    aimed = 0.4 * min(w, h)
    capped = 16 * _MAX_PIXMAP_SIDE / 1024
    assert extent == pytest.approx(min(aimed, capped), rel=0.05)
    assert abs(cx - w / 2) < 0.05 * w and abs(cy - h / 2) < 0.05 * h


def test_the_cell_is_outlined_in_yellow_twice_as_wide_as_its_neighbours(app, qtbot):
    from ui import theme

    ctx = _show(app, qtbot, 1)
    rgb = ctx.runtime.review_image.base_rgb
    yellow = tuple(int(theme.Colors.focused_cell[i:i + 2], 16) for i in (1, 3, 5))
    red = tuple(ctx.state.review_image.boundary_color)
    y0, y1, x0, x1 = _BOXES[0]
    row = y0 + 8                                                    # across the cell's middle
    assert [tuple(rgb[row, x]) for x in (x0 - 2, x0 - 1, x0, x0 + 1)] == [
        (0, 0, 0), yellow, yellow, tuple(rgb[row, x0 + 1])] and tuple(rgb[row, x0 + 1]) != yellow
    ny0, _ny1, nx0, _nx1 = _BOXES[1]                                # the neighbour: one red pixel
    assert [tuple(rgb[ny0 + 8, x]) for x in (nx0 - 1, nx0)] == [(0, 0, 0), red]


def test_a_cell_the_field_does_not_hold_is_said_rather_than_silently_not_shown(app, qtbot):
    statuses = []
    app._ctx.notify.set_status = statuses.append
    _show(app, qtbot, 9)
    assert statuses and statuses[-1] == ("Opened A01 · F001 · 0h, but cell #9 is not in the "
                                         "segmentation shown, so it is not outlined.")
