"""The Image Table's nanowell frames through the real app: offered by the real dropdown, laid out by the real spec,
drawn by the real Generate (CLAUDE.md §7.8 #3).

Every other test of them (``test_image_table_nanowell_frames.py``) calls the loader and the composer directly, in a
minimal shell. This one builds ``ReviewApp`` — so the slow lane (``conftest.py`` marks ``viewer_app`` users
``slow``) — and checks the one thing those cannot: that a grid of a fluorescence montage row and a metric-map row,
across two wells, comes out of Generate as pictures, with the metric map's undefined nanowell in the undefined ink.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def nanowell_image_table(viewer_app, tmp_path):
    from review.features.image_browser.controller import build_image_browser_subtab
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    viewer_app._load_path(build_nanowell_dataset(tmp_path / "nw") / "out")
    ctx = viewer_app._ctx
    build_pending = ctx.runtime.centre.build_pending
    if build_pending is not None:
        build_pending("Image Browser")
    sub = ctx.runtime.image_browser.subtabs
    build_image_browser_subtab(ctx, next(i for i in range(sub.count()) if sub.tabText(i) == "Image Table"))
    return ctx


def test_a_montage_row_and_a_metric_row_are_drawn_for_every_well(nanowell_image_table, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from review.data.dataset_dims import channel_options
    from review.data.nanowell_images import metric_token, montage_token
    from review.features.image_browser.image_table import controller as C
    from review.features.image_browser.image_table.build_layout import image_table_apply_spec
    from review.features.image_browser.image_table.builder_view import _default_colormap_for
    from review.features.image_browser.image_table.image_compose import _undefined_rgb
    from review.features.image_browser.image_table.state import Axis, ChannelSpec, TableSpec

    ctx = nanowell_image_table
    montage, brightness = montage_token("GFP"), metric_token("GFP Brightness (mean per-cell intensity)")
    offered = channel_options(ctx)
    assert montage in offered and brightness in offered

    drawn: dict = {}
    real = C.cell_display_rgb

    def spy(ctx_, cache, cell, r, crop_tool, arr):
        rgb = real(ctx_, cache, cell, r, crop_tool, arr)
        drawn[(cell["well"], cell["chan"])] = rgb
        return rgb

    monkeypatch.setattr(C, "cell_display_rgb", spy)
    st = ctx.state.image_table
    st.active_wells = {"A01", "B01"}
    rows = [ChannelSpec(c, "corrected", colormap=_default_colormap_for(ctx, c)) for c in (montage, brightness)]
    image_table_apply_spec(ctx, TableSpec(rows=Axis(None, rows), cols=Axis("well"),
                                          filters={"timepoint": {"24h"}}), render=False)
    C.image_table_generate(ctx)

    tokens = [r.axis_token() for r in rows]
    assert set(drawn) == {(w, t) for w in ("A01", "B01") for t in tokens}
    assert all(rgb is not None for rgb in drawn.values())
    grid = ctx.runtime.image_table.render_grid
    labels = [grid.itemAt(i).widget().findChild(QLabel) for i in range(grid.count())]
    pictures = [lb for lb in labels if lb is not None and lb.pixmap() is not None and not lb.pixmap().isNull()]
    assert len(pictures) == 4
    # A01's nanowell (0, 1) holds no cell at 24 h, so it has no brightness: the undefined ink, not a value.
    assert (drawn[("A01", tokens[1])] == np.array(_undefined_rgb(), dtype=np.uint8)).all(axis=-1).any()
