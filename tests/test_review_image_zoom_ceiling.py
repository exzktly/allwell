"""The Segmentation view never scales a field into a pixmap larger than it can hold.

The renderer scales the whole field into one pixmap, not only the part in view,
so a zoom's cost is the scaled field's size. Show in Segmentation sizes a cell to
the canvas, up to 40x the fit view, and the zoom buttons go on to 40x past their
stops: on a 2048 px field in a 1000 x 800 canvas that was a 21,845 px square
(1.9 GB) for a 30 px nucleus and a 32,000 px square (4.1 GB) at 40x, built on
the GUI thread. These tests drive the real renderer and the real jump to a cell
on a real QLabel host inside a QScrollArea, and measure the pixmap it built.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _ctx(qapp, *, side=2048, nucleus_px=30):
    from PySide6.QtWidgets import QLabel, QScrollArea

    from review.features.image_browser.review_image.state import ReviewImageState

    canvas = QScrollArea()
    label = QLabel()
    canvas.setWidget(label)
    canvas.resize(1000, 800)
    canvas.show()
    qapp.processEvents()
    rgb = np.zeros((side, side, 3), dtype=np.uint8)
    mask = np.zeros((side, side), dtype=np.int32)
    c = side // 2
    mask[c:c + nucleus_px, c:c + nucleus_px] = 7
    label._mask_arr = mask
    runtime = SimpleNamespace(review_image=SimpleNamespace(
        label=label, canvas=canvas, base_rgb=rgb, scale=None))
    ri = ReviewImageState()
    ri.base_pil = None
    state = SimpleNamespace(review_image=ri)
    widgets = SimpleNamespace(get_or_none=lambda _name: None)
    return SimpleNamespace(runtime=runtime, state=state, widgets=widgets), canvas, label


def _limit():
    from review.features.image_browser.review_image import renderer
    return renderer._MAX_PIXMAP_SIDE


def test_the_zoom_buttons_furthest_zoom_builds_no_pixmap_beyond_the_limit(qapp):
    from review.features.image_browser.review_image import renderer

    ctx, canvas, label = _ctx(qapp)
    try:
        for _ in range(30):  # every press past the stops, to the very top
            renderer.review_image_zoom_step(ctx, +1)
        pm = label.pixmap()
        assert max(pm.width(), pm.height()) <= _limit(), (
            f"a {pm.width()} x {pm.height()} pixmap was built at zoom "
            f"{ctx.state.review_image.zoom:.1f}x")
        # The readout and the state say the zoom actually drawn, not one beyond it.
        vp = canvas.viewport()
        fit = min(vp.width() / 2048, vp.height() / 2048)
        assert ctx.state.review_image.zoom == pytest.approx(
            renderer.zoom_ceiling(2048, 2048, fit))
    finally:
        canvas.deleteLater()


def test_a_jump_to_a_small_cell_builds_no_pixmap_beyond_the_limit(qapp, monkeypatch):
    from review.features.image_browser.review_image import renderer

    ctx, canvas, label = _ctx(qapp, nucleus_px=15)
    monkeypatch.setattr(renderer, "_focused_nucleus_here", lambda _ctx: 7)
    try:
        assert renderer.zoom_review_image_to_selected_nucleus(ctx) is True
        pm = label.pixmap()
        assert max(pm.width(), pm.height()) <= _limit(), (
            f"a {pm.width()} x {pm.height()} pixmap was built for a 15 px cell")
        # The pan was computed for the zoom that was drawn: the cell's centre
        # sits in the middle of the viewport.
        vp = canvas.viewport()
        scale = renderer.zoom_ceiling(2048, 2048, min(vp.width() / 2048, vp.height() / 2048)) * \
            min(vp.width() / 2048, vp.height() / 2048)
        cx = (1024 + 7) * scale
        left = canvas.horizontalScrollBar().value()
        assert left <= cx <= left + vp.width()
    finally:
        canvas.deleteLater()


def test_the_ceiling_leaves_a_small_field_its_full_zoom():
    from review.features.image_browser.review_image import renderer

    # A 100 px field fitted to 200 px may go the whole way to 40x (8,000 px);
    # a 2048 px field fitted to 800 px is held to 8192 / 800.
    assert renderer.zoom_ceiling(100, 100, 2.0) == renderer._MAX_CELL_ZOOM
    assert renderer.zoom_ceiling(2048, 2048, 800 / 2048) == pytest.approx(8192 / 800)
    # Never below the fit view itself, however large the field.
    assert renderer.zoom_ceiling(100_000, 100_000, 1.0) == 1.0
