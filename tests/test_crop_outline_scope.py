"""The crop outline drawn on the Segmentation canvas, against a scoped crop.

The crop's scope is a property of the dataset, not of the view, so the box can
be armed for wells and timepoints the canvas is not showing. Drawing the same
solid cyan rectangle on a frame the crop does not filter would state that those
cells are being excluded when they are not — the silent-substitution failure
CLAUDE.md §7 is about. Out of scope the outline is dashed and dimmed instead.

Asserted on the pixels the renderer writes, not on the decision behind them: a
test that reads the scope back would pass happily while the canvas stayed wrong.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PySide6")

from well_viewer.features.image_browser.review_image.renderer import (
    _draw_crop_outline,
)

_IN_SCOPE = np.array([0, 220, 255], dtype=np.uint8)
_OUT_OF_SCOPE = np.array([0, 110, 128], dtype=np.uint8)


class _Combo:
    def __init__(self, text):
        self._text = text

    def currentText(self):
        return self._text


def _ctx(*, well="A01", fov="1", tp="0", region=(10, 10, 90, 90), **scope):
    return SimpleNamespace(
        state=SimpleNamespace(
            preview=SimpleNamespace(selected_well=well),
            dataset=SimpleNamespace(
                crop_region=region,
                crop_scope_wells=scope.get("wells"),
                crop_scope_fovs=scope.get("fovs"),
                crop_scope_tps=scope.get("tps"),
            ),
        ),
        runtime=SimpleNamespace(
            review_image=SimpleNamespace(fov_menu=_Combo(fov), tp_cb=_Combo(tp)),
        ),
        widgets=SimpleNamespace(get_or_none=lambda _name: None),
    )


def _rgb():
    return np.zeros((100, 100, 3), dtype=np.uint8)


def _count(rgb, color):
    return int(np.all(rgb == color, axis=-1).sum())


def test_unscoped_crop_draws_the_solid_outline():
    rgb = _rgb()
    _draw_crop_outline(_ctx(), rgb)
    assert _count(rgb, _IN_SCOPE) > 0
    assert _count(rgb, _OUT_OF_SCOPE) == 0


def test_displayed_frame_inside_the_scope_draws_the_solid_outline():
    rgb = _rgb()
    _draw_crop_outline(_ctx(wells={"A01"}, tps={"0"}), rgb)
    assert _count(rgb, _IN_SCOPE) > 0
    assert _count(rgb, _OUT_OF_SCOPE) == 0


def test_frame_outside_the_scope_is_dashed_and_dimmed():
    solid = _rgb()
    _draw_crop_outline(_ctx(), solid)
    dashed = _rgb()
    _draw_crop_outline(_ctx(wells={"B02"}), dashed)
    assert _count(dashed, _IN_SCOPE) == 0
    painted = _count(dashed, _OUT_OF_SCOPE)
    # Dashed means one dash painted in every two, so roughly half the solid
    # outline's ink — bounded loosely, because the four bands overlap at the
    # corners and the last dash of each is clipped to the band's end.
    solid_ink = _count(solid, _IN_SCOPE)
    assert 0.3 * solid_ink < painted < 0.7 * solid_ink


def test_a_timepoint_outside_the_scope_dashes_the_outline_too():
    rgb = _rgb()
    _draw_crop_outline(_ctx(tp="24", tps={"0"}), rgb)
    assert _count(rgb, _IN_SCOPE) == 0
    assert _count(rgb, _OUT_OF_SCOPE) > 0


def test_no_region_draws_nothing():
    rgb = _rgb()
    _draw_crop_outline(_ctx(region=None), rgb)
    assert rgb.any() == False  # noqa: E712 — asserting the array is untouched
