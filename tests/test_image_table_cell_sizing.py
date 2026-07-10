"""Rendered Image Table cells must size to their pixmap, not a fixed square.

``make_overlay_thumb`` scales KeepAspectRatio, so a non-square source image is
shorter/narrower than ``sz``. A fixed ``sz×sz`` cell would letterbox it and leave
large blank bands that read as huge gaps between rows (esp. channel rows within one
well). The cell must hug the pixmap so rows stay tight and ``cell_gap`` is the true
inter-row spacing.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

pytestmark = pytest.mark.qt


def _wide_loader(*_a, **_k):
    # 4:1 image → aspect-scaled thumbnail is ~1/4 the height of a square cell.
    return np.tile(np.linspace(0, 255, 512, dtype=np.float32), (128, 1))


def _first_image_label(grid):
    from PySide6.QtWidgets import QLabel
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if w is None:
            continue
        for lbl in w.findChildren(QLabel):
            pm = lbl.pixmap()
            if pm is not None and not pm.isNull():
                return lbl, pm
    return None, None


def test_non_square_cells_hug_the_pixmap_no_letterbox(image_table_app, monkeypatch):
    from well_viewer.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    monkeypatch.setattr(C, "_load_array", _wide_loader)
    C.image_table_apply_dims_preset(ctx, ["well", "channel"], ["timepoint"])
    C.image_table_generate(ctx)

    grid = ctx.runtime.image_table.render_grid
    lbl, pm = _first_image_label(grid)
    assert lbl is not None, "expected at least one rendered image cell"
    # The label is pinned to the pixmap's real size (short, for a 4:1 image),
    # NOT a square sz×sz that would letterbox it.
    assert lbl.height() == pm.height()
    assert lbl.width() == pm.width()
    assert lbl.height() < lbl.width(), "wide image → short cell (no square letterbox)"


def test_cell_gap_drives_inter_row_spacing(image_table_app, monkeypatch):
    from well_viewer.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    monkeypatch.setattr(C, "_load_array", _wide_loader)
    C.image_table_apply_dims_preset(ctx, ["well", "channel"], ["timepoint"])
    C.image_table_generate(ctx)
    grid = ctx.runtime.image_table.render_grid
    assert grid.spacing() == int(ctx.state.image_table.cell_gap)

    # A Properties tweak re-renders and the grid spacing follows it.
    C.image_table_set_spacing(ctx, "cell_gap", 24)
    assert ctx.state.image_table.cell_gap == 24
    assert grid.spacing() == 24
