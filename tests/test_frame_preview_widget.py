"""The shared frame canvas: blit, zoom, pan, and the hover pixel mapping.

``widgets.frame_preview.FramePreview`` is handed an RGB array and reports image
coordinates. The hover mapping is the only arithmetic in it, and it is the piece
that goes subtly wrong — the pixmap is centred inside a label that may be larger
than it, so the centring offset has to come out before scaling back to image
space. Both corners are asserted, because an off-by-one there reads a neighbour's
intensity and nothing else notices.

Pure ``qtbot`` — no app fixture.
"""

from __future__ import annotations

import numpy as np
import pytest

from widgets.frame_preview import FramePreview


@pytest.fixture
def preview(qtbot):
    w = FramePreview()
    qtbot.addWidget(w)
    w.resize(200, 100)
    w.show()
    qtbot.waitExposed(w)
    return w


def _rgb(h: int = 40, w: int = 80):
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[..., 0] = 255
    return a


def test_starts_empty(preview):
    assert preview._disp is None
    assert preview.image_xy_at(5, 5) is None


def test_blit_preserves_the_aspect_ratio(preview):
    preview.set_rgb(_rgb())
    nw, nh, iw, ih = preview._disp
    assert (iw, ih) == (80, 40)
    assert round(nw / nh, 3) == round(iw / ih, 3)


def test_clearing_drops_the_image_and_the_geometry(preview):
    preview.set_rgb(_rgb())
    preview.clear()
    assert preview._disp is None
    assert preview.image_xy_at(5, 5) is None


# ── zoom ─────────────────────────────────────────────────────────────────────

def test_zoom_is_clamped_both_ways(preview):
    preview.set_zoom(1e6)
    assert preview.zoom() == 8.0
    preview.set_zoom(1e-6)
    assert preview.zoom() == 0.1


def test_zoom_in_out_are_reciprocal(preview):
    preview.set_rgb(_rgb())
    preview.set_zoom(2.0)
    preview.zoom_in()
    preview.zoom_out()
    assert preview.zoom() == pytest.approx(2.0)


def test_fit_resets_zoom_and_pan(preview):
    preview.set_rgb(_rgb())
    preview.set_zoom(4.0)
    preview._pan = (33.0, 44.0)
    preview.fit()
    assert preview.zoom() == 1.0
    assert preview._pan == (0.0, 0.0)


# ── pan ──────────────────────────────────────────────────────────────────────

class _Ev:
    def __init__(self, x, y, buttons=None):
        from PySide6.QtCore import Qt
        self._x, self._y = x, y
        self._b = buttons if buttons is not None else Qt.NoButton

    def position(self):
        from PySide6.QtCore import QPointF
        return QPointF(self._x, self._y)

    def buttons(self):
        return self._b


def test_drag_pans_by_the_delta(preview):
    from PySide6.QtCore import Qt
    preview.set_rgb(_rgb())
    preview._on_press(_Ev(50, 50))
    preview._on_move(_Ev(70, 65, Qt.LeftButton))
    assert preview._pan == (20.0, 15.0)


def test_a_drag_move_without_a_press_is_ignored(preview):
    from PySide6.QtCore import Qt
    preview.set_rgb(_rgb())
    preview._on_move(_Ev(70, 65, Qt.LeftButton))   # must not raise
    assert preview._pan == (0.0, 0.0)


# ── hover mapping ────────────────────────────────────────────────────────────

def _corner_positions(preview):
    nw, nh, _iw, _ih = preview._disp
    off_x = max(0, (preview._label.width() - nw) // 2)
    off_y = max(0, (preview._label.height() - nh) // 2)
    return (off_x + 0.5, off_y + 0.5), (off_x + nw - 1, off_y + nh - 1)


def test_hover_maps_both_corners_exactly(preview, qtbot):
    preview.set_rgb(_rgb())
    seen = []
    preview.pixelHovered.connect(lambda x, y: seen.append((x, y)))
    top_left, bottom_right = _corner_positions(preview)
    preview._on_move(_Ev(*top_left))
    preview._on_move(_Ev(*bottom_right))
    _nw, _nh, iw, ih = preview._disp
    assert seen == [(0, 0), (iw - 1, ih - 1)]


def test_hover_outside_the_image_emits_nothing(preview):
    preview.set_rgb(_rgb())
    seen = []
    preview.pixelHovered.connect(lambda x, y: seen.append((x, y)))
    preview._on_move(_Ev(-500, -500))
    preview._on_move(_Ev(50_000, 50_000))
    assert seen == []


def test_hover_still_maps_after_a_zoom(preview):
    """The mapping reads the geometry of the *current* blit, so it must survive a
    scale change — a stale scale silently reports the wrong pixel."""
    preview.set_rgb(_rgb())
    preview.set_zoom(3.0)
    top_left, bottom_right = _corner_positions(preview)
    assert preview.image_xy_at(*top_left) == (0, 0)
    _nw, _nh, iw, ih = preview._disp
    assert preview.image_xy_at(*bottom_right) == (iw - 1, ih - 1)
