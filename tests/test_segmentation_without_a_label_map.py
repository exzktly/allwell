"""Segmentation draws a frame that has no label map, says why, and turns off the tools that need outlines
(NANOWELL_OBSERVATIONS_TRACKER #4, #5).

A nanowell run writes no label map, and Segmentation used to abort on a missing one — "Missing fluorescence image
or label map", nothing drawn, Delete / Crop / Edit still enabled. Now the image is drawn without outlines, the
status line says why, and on a nanowell run the Nanowells feature's ``segmentation.nanowell_overlay`` service adds
each counted cell as a point and each nanowell the tile owns as a rim. Without that feature the line says the
overlays are not in this build.

Through the real Segmentation panel builder and update / refresh path in a minimal shell, reading the pixmap
buffer and the status label (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.features.image_browser.review_image import renderer as rr  # noqa: E402


@pytest.fixture
def seg_ctx(qtbot, tmp_path):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from review.features.preview.panel_view import build_review_image_panel
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    ctx.state.dataset.in_dir = root / "in"            # the raw tiles are the only frames a nanowell run has
    frame = QWidget()
    QVBoxLayout(frame)
    window.setCentralWidget(frame)
    build_review_image_panel(ctx, frame)
    ctx.state.channel.active_image_channel = "phase"
    ctx.state.preview.selected_well = "A01"
    return ctx


def _draw(ctx):
    rr.update_preview_ctx(ctx, "A01")
    rr.refresh_review_image_ctx(ctx)
    return ctx.widgets.get("_review_image_status").text(), ctx.runtime.review_image.base_rgb


def _buttons(ctx):
    return [ctx.widgets.get(n) for n in ("_review_image_delete_btn", "_review_image_crop_btn",
                                         "_review_image_edit_btn")]


def test_the_tile_is_drawn_with_its_cells_as_points_and_the_tools_are_off(seg_ctx):
    status, rgb = _draw(seg_ctx)
    assert status.startswith(rr.NO_LABEL_MAP_NANOWELL.format(overlays=""))
    assert rgb is not None and rgb.shape[:2] == (64, 64)
    assert not any(b.isEnabled() for b in _buttons(seg_ctx))
    assert all("no label map" in b.toolTip() for b in _buttons(seg_ctx))
    # The overlay is painted: the cells' dots and the rims are in the accent and border inks.
    from matplotlib.colors import to_rgb

    from ui import theme
    dot = np.array([round(255 * c) for c in to_rgb(theme.Colors.accent)])
    assert (rgb.reshape(-1, 3) == dot).all(axis=1).sum() > 0


def test_the_overlay_puts_each_cell_where_the_grid_record_places_its_tile(seg_ctx):
    from review.features.nanowell.overlay_compute import nanowell_overlay
    from tests.fixtures.nanowell_dataset import COUNTS, PX_UM, SITES

    overlay = nanowell_overlay(seg_ctx, "A01", "F0", "0")
    # Tile F0 sits at the well frame's origin, so a cell at (100 + 20q, 100 + 20r) µm is at that over 4.3 µm/px.
    want = sorted(((100.0 + 20.0 * q) / PX_UM, (100.0 + 20.0 * r) / PX_UM)
                  for (q, r), n in zip(SITES, COUNTS["A01"]["0h"]) for _ in range(n))
    assert np.allclose(sorted(map(tuple, overlay.points)), want)
    assert len(overlay.rims) == len(SITES)


def test_without_the_nanowells_feature_it_says_the_overlays_are_not_in_this_build(seg_ctx, monkeypatch):
    monkeypatch.setattr(seg_ctx.features, "service", lambda key: None)
    status, rgb = _draw(seg_ctx)
    assert status == rr.NO_LABEL_MAP_NANOWELL.format(overlays=rr.OVERLAYS_NOT_IN_BUILD)
    assert rgb is not None
