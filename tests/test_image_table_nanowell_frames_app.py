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
    from review.features.image_browser.image_table.builder_view import new_channel_spec
    from review.features.image_browser.image_table.image_compose import _undefined_rgb
    from review.features.image_browser.image_table.state import Axis, TableSpec

    ctx = nanowell_image_table
    montage, brightness = montage_token("GFP"), metric_token("GFP Cell fluorescence (mean per cell, own-well background subtracted)")
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
    rows = [new_channel_spec(ctx, c) for c in (montage, brightness)]
    image_table_apply_spec(ctx, TableSpec(rows=Axis(None, rows), cols=Axis("well"),
                                          filters={"timepoint": {"24h"}}), render=False)
    C.image_table_generate(ctx)

    tokens = [r.axis_token() for r in rows]
    assert set(drawn) == {(w, t) for w in ("A01", "B01") for t in tokens}
    assert all(rgb is not None for rgb in drawn.values())
    grid = ctx.runtime.image_table.render_grid
    labels = [grid.itemAt(i).widget().findChild(QLabel) for i in range(grid.count())]
    pictures = [lb for lb in labels if lb is not None and lb.objectName() != "ImageTableColourKey"
                and lb.pixmap() is not None and not lb.pixmap().isNull()]
    assert len(pictures) == 4
    # The colour key sits under the grid, in it, and has something drawn in it.
    keys = [grid.itemAt(i).widget() for i in range(grid.count())
            if grid.itemAt(i).widget().objectName() == "ImageTableColourKey"]
    assert len(keys) == 1 and not keys[0].pixmap().isNull() and keys[0].pixmap().height() > 80
    # A01's nanowell (0, 1) holds no cell at 24 h, so it has no brightness: the undefined ink, not a value.
    assert (drawn[("A01", tokens[1])] == np.array(_undefined_rgb(), dtype=np.uint8)).all(axis=-1).any()


def test_the_saved_figure_and_the_slide_carry_the_colour_key_and_the_tray_folds_the_metrics(nanowell_image_table):
    from PySide6.QtWidgets import QLabel, QToolButton

    from review.data.nanowell_images import metric_token
    from review.features.image_browser.image_table import controller as C
    from review.features.image_browser.image_table._frames import FrameCollector
    from review.features.image_browser.image_table.build_layout import image_table_apply_spec
    from review.features.image_browser.image_table.builder_view import _build_tray, builder_spec, new_channel_spec
    from review.features.image_browser.image_table.export import _build_export_figure
    from review.features.image_browser.image_table.state import Axis, TableSpec

    ctx = nanowell_image_table
    st = ctx.state.image_table
    st.active_wells = {"A01", "B01"}
    count = metric_token("Cell count")
    image_table_apply_spec(ctx, TableSpec(rows=Axis(None, [new_channel_spec(ctx, count)]), cols=Axis("well"),
                                          filters={"timepoint": {"24h"}}), render=False)
    C.image_table_generate(ctx)
    frames = FrameCollector()
    fig, _kw = _build_export_figure(ctx, frames=frames)
    assert fig is not None and any(ax.get_position().y1 <= 0.0 + 1e-9 for ax in fig.axes)    # below the grid
    names = [f.name for f in frames.grid.frames]
    assert names[-1] == "Colour key" and frames.grid.height_in > fig.get_size_inches()[1]

    body = ctx.runtime.image_table.channels_tray_body
    _build_tray(ctx, builder_spec(ctx))
    chips = [lb.text() for lb in body.findChildren(QLabel) if lb.objectName() == "ITBChanLbl"]
    toggle = next(b for b in body.findChildren(QToolButton) if "Nanowell metrics" in b.text())
    assert "NW: Cell count" not in chips and "MONTAGE GFP" in chips
    toggle.click()
    chips = [lb.text() for lb in body.findChildren(QLabel) if lb.objectName() == "ITBChanLbl"]
    assert "NW: Cell count" in chips and "NW: GFP Cell fluorescence" in chips
