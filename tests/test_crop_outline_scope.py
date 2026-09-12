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

from review.features.image_browser.review_image.renderer import (
    _draw_crop_outline,
)

_IN_SCOPE = np.array([0, 220, 255], dtype=np.uint8)
_OUT_OF_SCOPE = np.array([0, 110, 128], dtype=np.uint8)


class _Combo:
    def __init__(self, text):
        self._text = text

    def currentText(self):
        return self._text


def _rows():
    """Two cells in each of the well's two frames — (fov 1, t=0) and (fov 1,
    t=24). The scope is judged against these, so a ctx without them would send
    every test down the no-rows fallback and prove nothing about the path the
    outline actually takes."""
    import pandas as pd
    return pd.DataFrame({
        "shape_centroid_row": [20, 80, 20, 80],
        "shape_centroid_col": [20, 80, 20, 80],
        "fov": ["1", "1", "1", "1"],
        "timepoint": ["0", "0", "24", "24"],
    })


def _ctx(*, well="A01", fov="1", tp="0", region=(10, 10, 90, 90), **scope):
    return SimpleNamespace(
        state=SimpleNamespace(
            preview=SimpleNamespace(selected_well=well),
            dataset=SimpleNamespace(
                cache={"A01": _rows()},
                well_paths={"A01": "/tmp/A01.csv"},
                fov_integrity={},
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


# ── the outline agrees with the verdict, not with a second token comparison ──
def _ctx_with_rows(rows, **scope):
    """A ctx whose well carries real rows, so the outline is judged the way the
    ``Crop_Pass`` verdict judges them."""
    import pandas as pd
    df = pd.DataFrame(rows)
    ctx = _ctx(**scope)
    ctx.state.dataset.cache = {"A01": df}
    return ctx


def test_a_scope_holding_raw_tokens_still_draws_solid():
    """The scope came off disk holding ``T24``/``001`` where the combos read
    ``24``/``1``. The cells *are* being excluded, so a dashed outline would
    understate a filter that is running."""
    rows = {"shape_centroid_row": [1, 50], "shape_centroid_col": [1, 50],
            "fov": ["1", "1"], "timepoint": ["24", "24"]}
    ctx = _ctx_with_rows(rows, fovs={"001"}, tps={"T24"})
    ctx.runtime.review_image.fov_menu = _Combo("1")
    ctx.runtime.review_image.tp_cb = _Combo("24")
    rgb = _rgb()
    _draw_crop_outline(ctx, rgb)
    assert _count(rgb, _IN_SCOPE) > 0
    assert _count(rgb, _OUT_OF_SCOPE) == 0


def test_a_frame_whose_rows_cannot_match_the_armed_timepoint_is_dashed():
    """The timepoint picker offers a value from ``pipeline_info`` that the rows
    do not carry, so no row of this frame can match it and the crop reaches
    nothing here — a solid outline would state a filter that is not running."""
    rows = {"shape_centroid_row": [1, 50], "shape_centroid_col": [1, 50],
            "fov": ["1", "1"]}                      # no timepoint column
    ctx = _ctx_with_rows(rows, tps={"0"})
    rgb = _rgb()
    _draw_crop_outline(ctx, rgb)
    assert _count(rgb, _IN_SCOPE) == 0
    assert _count(rgb, _OUT_OF_SCOPE) > 0
