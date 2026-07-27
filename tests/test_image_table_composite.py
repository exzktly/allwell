"""Image Table: channel overlay (Separate ↔ Overlay switch).

The shelf's per-root Separate↔Overlay switch (``state.composite``) collapses the
channel axis to a single entry and blends every selected channel into each cell —
"blend this root's channels into one cell". The pure additive blend math
(``composite_additive_rgb``) is tested directly; ``overlay_cell_rgb`` wires it to
the per-channel render path; and the collapse + blend are exercised end-to-end
through the Image Table controller / export.
"""
from __future__ import annotations

import numpy as np
import pytest

from well_viewer.features.image_browser.image_table.image_compose import (
    OVERLAY_CHAN,
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


def test_overlay_cell_rgb_blends_every_channel(monkeypatch):
    """overlay_cell_rgb loads + renders each channel through the per-channel path
    and additively merges them — so a red + green channel merge to yellow."""
    from well_viewer.features.image_browser.image_table import image_compose as IC

    colors = {"R": _solid((255, 0, 0)), "G": _solid((0, 255, 0))}
    # Stub the two leaf calls so the test needs no real dataset: _load_array
    # returns a marker per channel, cell_display_rgb turns it into that channel's
    # solid RGB (mirroring how the real per-channel LUT/colormap would tint it).
    monkeypatch.setattr(IC, "_load_array", lambda ctx, cache, well, ch, tp, fov, **k: ch)
    monkeypatch.setattr(IC, "_row_display_opts", lambda ctx, r, ch=None: (False, False, "raw"))
    monkeypatch.setattr(IC, "cell_display_rgb",
                        lambda ctx, cache, cell, r, crop, arr: colors.get(arr))

    class _Chan:
        ratio_metrics = []

    class _State:
        channel = _Chan()

    class _Ctx:
        state = _State()

    cell = {"well": "A01", "chan": OVERLAY_CHAN, "tp": "0h", "fov": "1"}
    out = IC.overlay_cell_rgb(_Ctx(), {}, cell, 0, None, ["R", "G"])
    assert out is not None
    assert np.array_equal(out, _solid((255, 255, 0)))


@pytest.mark.qt
def test_overlay_collapses_channel_axis_and_marks_cells(image_table_app):
    from well_viewer.features.image_browser.image_table import controller as C
    from well_viewer.features.image_browser.image_table import builder_view as B

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)

    spec = B.builder_spec(ctx)
    ax_name = spec.channel_axis() or "rows"
    ax = getattr(spec, ax_name)
    n_channels = len(ax.channels)
    assert n_channels >= 2, "need multiple channels to exercise the overlay"

    # Separate: one row per channel, no sentinel cells.
    ax.overlay = False
    B._apply_now(ctx)
    sep = ctx.runtime.image_table.cells
    assert len(sep) == n_channels
    assert all(cell["chan"] != OVERLAY_CHAN for row in sep for cell in row)
    assert ctx.state.image_table.composite is False

    # Overlay: the channel axis collapses to ONE blended row of sentinel cells.
    ax.overlay = True
    B._apply_now(ctx)
    ovl = ctx.runtime.image_table.cells
    assert len(ovl) == 1
    assert all(cell["chan"] == OVERLAY_CHAN for row in ovl for cell in row)
    assert ctx.state.image_table.composite is True

    # Generate must not raise on the collapsed grid.
    C.image_table_generate(ctx)


@pytest.mark.qt
def test_export_figure_reflects_collapsed_overlay(image_table_app):
    from well_viewer.features.image_browser.image_table import controller as C
    from well_viewer.features.image_browser.image_table import builder_view as B
    from well_viewer.features.image_browser.image_table.export import _build_export_figure
    from well_viewer.features.image_browser.image_table.renderer import FACET_GID

    def _cell_axes(fig):
        # The figure also carries the facet label strips; count only image cells.
        return [a for a in fig.axes if a.get_gid() != FACET_GID]

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)

    spec = B.builder_spec(ctx)
    ax_name = spec.channel_axis() or "rows"
    ax = getattr(spec, ax_name)

    # Separate: N channel rows × M timepoint columns.
    ax.overlay = False
    B._apply_now(ctx)
    C.image_table_generate(ctx)
    sep_cells = ctx.runtime.image_table.cells
    n_rows, n_cols = len(sep_cells), len(sep_cells[0])
    fig, _ = _build_export_figure(ctx)
    assert len(_cell_axes(fig)) == n_rows * n_cols

    # Overlay: the channel axis collapses to a single blended row.
    ax.overlay = True
    B._apply_now(ctx)
    C.image_table_generate(ctx)
    fig, _ = _build_export_figure(ctx)
    assert len(_cell_axes(fig)) == 1 * n_cols
