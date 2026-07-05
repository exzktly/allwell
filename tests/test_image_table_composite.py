"""Image Table: composite row.

The composite feature appends one extra bottom row whose cells additively merge
(LUT-colored RGB summed, clipped to white) every row above them in the same
column. The pure blend math (``composite_additive_rgb``) is tested directly; the
end-to-end row append is exercised through the Image Table controller.
"""
from __future__ import annotations

import numpy as np
import pytest

from well_viewer.features.image_browser.image_table.image_compose import (
    composite_additive_rgb,
)


def _solid(color, shape=(4, 4)):
    img = np.zeros((*shape, 3), dtype=np.uint8)
    img[:] = color
    return img


def test_additive_merge_reds_and_greens_make_yellow():
    out = composite_additive_rgb([_solid((255, 0, 0)), _solid((0, 255, 0))])
    assert out is not None
    assert out.dtype == np.uint8
    assert np.array_equal(out, _solid((255, 255, 0)))


def test_additive_merge_clips_to_white():
    # 200 + 200 = 400 → clamped to 255, never wraps around.
    out = composite_additive_rgb([_solid((200, 0, 0)), _solid((200, 0, 0))])
    assert np.array_equal(out, _solid((255, 0, 0)))


def test_mismatched_shapes_are_skipped():
    # Grid rows may target different wells/FOVs/crops → differing shapes. Only
    # tiles matching the reference (first) shape contribute.
    ref = _solid((10, 20, 30), shape=(4, 4))
    other = _solid((90, 90, 90), shape=(5, 5))
    out = composite_additive_rgb([ref, other])
    assert out.shape == (4, 4, 3)
    assert np.array_equal(out, ref)


def test_none_and_empty_inputs_return_none():
    assert composite_additive_rgb([]) is None
    assert composite_additive_rgb([None, None]) is None


@pytest.mark.qt
def test_generate_appends_composite_row(image_table_app):
    from well_viewer.features.image_browser.image_table import controller as C

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)
    C.image_table_apply_preset(ctx, "channel", "timepoint")
    cells = ctx.runtime.image_table.cells
    assert cells, "build should assemble the selector grid"
    n_rows = len(cells)
    grid = ctx.runtime.image_table.render_grid

    # Composite off: no row at index n_rows.
    ctx.state.image_table.composite = False
    C.image_table_generate(ctx)
    assert grid.itemAtPosition(n_rows, 0) is None

    # Composite on: an extra bottom row appears (does not raise).
    ctx.state.image_table.composite = True
    C.image_table_generate(ctx)
    item = grid.itemAtPosition(n_rows, 0)
    assert item is not None and item.widget() is not None


@pytest.mark.qt
def test_export_figure_includes_composite_row(image_table_app):
    from well_viewer.features.image_browser.image_table import controller as C
    from well_viewer.features.image_browser.image_table.export import _build_export_figure

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)
    C.image_table_apply_preset(ctx, "channel", "timepoint")
    cells = ctx.runtime.image_table.cells
    n_rows, n_cols = len(cells), len(cells[0])
    C.image_table_generate(ctx)

    ctx.state.image_table.composite = False
    fig, _ = _build_export_figure(ctx)
    assert len(fig.axes) == n_rows * n_cols

    ctx.state.image_table.composite = True
    fig, _ = _build_export_figure(ctx)
    assert len(fig.axes) == (n_rows + 1) * n_cols
