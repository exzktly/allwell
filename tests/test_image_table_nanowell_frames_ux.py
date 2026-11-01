"""The UX review of the Image Table's nanowell frames, one test per finding
(ARCHITECTURE §6.2l; the review's tracker, closed and deleted, is in this file's git history beside it).

An independent reviewer read real renders of a four-well plate and found the metric maps unreadable as numbers (no
colour key), the phase montage drawn green, "no value" mistakable for a value, the status line a cut-off wall of
text, labels too long, a chip swatch that lied, an unlabelled scale bar, stair-stepped discs after a crop, a pooled
window nobody was told about, and "FOV" on whole-well pictures. Each test below states the behaviour that answers
one finding, on the fixture's own files and tables (``tests/fixtures/nanowell_dataset``).
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.data.frame_load import MONTAGE_TOKEN, load_frame_array  # noqa: E402
from review.data.nanowell_images import display_token, metric_token, montage_token, short_label  # noqa: E402
from tests.fixtures import nanowell_dataset as fx  # noqa: E402

COUNT = metric_token("Cell count")
BRIGHTNESS = metric_token("GFP Brightness (mean per-cell intensity)")
COUNT_FC = metric_token("Count fold change")
LOG2_FC = metric_token("log₂ count fold change, (n + 1) / (n₀ + 1)")


def _loaded(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell

    out = fx.build_nanowell_dataset(tmp_path / "nw") / "out"
    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, out)
    ctx.state.dataset.data_dir = out
    return ctx


def _cells(tokens, wells=("A01", "B01"), tp="24"):
    return [[{"well": w, "chan": t, "tp": tp, "fov": "F0"} for w in wells] for t in tokens]


def _use(ctx, tokens):
    """Put *tokens* on the grid's channel axis the way the builder adds them."""
    from review.features.image_browser.image_table.builder_view import new_channel_spec

    specs = [new_channel_spec(ctx, t) for t in tokens]
    ctx.state.image_table.channel_opts = {cs.axis_token(): cs.to_dict() for cs in specs}
    return specs


def _pool(ctx, cells):
    from review.features.image_browser.image_table.controller import _compute_auto_luts

    _compute_auto_luts(ctx, cells, ctx.state.image_table.image_cache, None)


# ── H2: transmitted light is grey ───────────────────────────────────────────
def test_the_phase_montage_and_the_phase_channel_default_to_grey_and_a_fluorophore_keeps_its_colour(qtbot, tmp_path):
    from review.features.image_browser.image_table.builder_view import _default_colormap_for

    ctx = _loaded(qtbot, tmp_path)
    assert _default_colormap_for(ctx, MONTAGE_TOKEN) == "Greys"
    assert _default_colormap_for(ctx, "PHASE") == "Greys"
    assert _default_colormap_for(ctx, montage_token("GFP")) == "Greens"
    assert _default_colormap_for(ctx, "BFP") == "Cyan"            # "bf" is a word to match, not a substring


# ── H1b: a fold change diverges about 1 ─────────────────────────────────────
def test_a_fold_change_map_is_diverging_log_scaled_and_its_window_centred_on_no_change(qtbot, tmp_path):
    ctx = _loaded(qtbot, tmp_path)
    fc, count = _use(ctx, [COUNT_FC, COUNT])
    assert (fc.colormap, fc.log) == ("coolwarm", True) and (count.colormap, count.log) == ("viridis", False)
    cells = _cells([COUNT_FC, COUNT])
    _pool(ctx, cells)
    window = ctx.state.image_table.auto_lut_by_channel
    # A01 24 h over 0 h: 3/1 = 3 at (0, 0), 2/2 = 1, 4/4 = 1; B01: 2/2, 4/1 = 4, 1/1. Largest ratio 4, smallest 1.
    lo, hi = window[COUNT_FC.lower()]
    assert (lo, hi) == pytest.approx((0.25, 4.0))
    assert window[COUNT.lower()] == pytest.approx((0.0, 5.0))       # not a fold change: its own min and max


def test_a_fold_change_already_on_a_log_scale_is_centred_on_zero(qtbot, tmp_path):
    from review.features.image_browser.image_table.controller import _centred_window

    ctx = _loaded(qtbot, tmp_path)
    assert _centred_window(ctx, [-0.5, 2.0, None, LOG2_FC]) == (-2.0, 2.0)
    assert _centred_window(ctx, [0.0, 3.0, 0.5, COUNT_FC]) == pytest.approx((1 / 3.0, 3.0))


# ── L2: per cell, said out loud ─────────────────────────────────────────────
def test_per_cell_leaves_a_channel_out_of_the_pooled_window_and_the_key_says_colours_are_not_comparable(qtbot,
                                                                                                    tmp_path):
    from review.features.image_browser.image_table.image_compose import colour_keys

    ctx = _loaded(qtbot, tmp_path)
    (count,) = _use(ctx, [COUNT])
    cells = _cells([COUNT])
    _pool(ctx, cells)
    assert COUNT.lower() in ctx.state.image_table.auto_lut_by_channel
    assert colour_keys(ctx, cells)[0].scope == "one window shared by all 2 wells"
    count.auto_per_cell = True
    ctx.state.image_table.channel_opts = {count.axis_token(): count.to_dict()}
    _pool(ctx, cells)
    assert COUNT.lower() not in ctx.state.image_table.auto_lut_by_channel
    key = colour_keys(ctx, cells)[0]
    assert (key.lo, key.hi) == (None, None) and "not comparable" in key.scope


# ── H1: the colour key ──────────────────────────────────────────────────────
def test_the_key_names_each_metric_in_full_with_the_window_its_cells_are_drawn_in(qtbot, tmp_path):
    from review.features.image_browser.image_table._colour_key import key_rgb
    from review.features.image_browser.image_table.image_compose import colour_keys

    ctx = _loaded(qtbot, tmp_path)
    _use(ctx, [MONTAGE_TOKEN, montage_token("GFP"), BRIGHTNESS, COUNT_FC])
    cells = _cells([MONTAGE_TOKEN, montage_token("GFP"), BRIGHTNESS, COUNT_FC])
    for row in cells:
        for cell in row:
            load_frame_array(ctx, ctx.state.image_table.image_cache, cell["well"], cell["chan"], "24", "F0")
    _pool(ctx, cells)
    keys = colour_keys(ctx, cells)
    # Named as the rows are, then in full: the phase picture has no quantity to key.
    assert [k.title for k in keys] == ["MONTAGE GFP", "NW: GFP Brightness (mean per-cell intensity)",
                                       "NW: Count fold change"]
    brightness = keys[1]
    measured = [g for well in ("A01", "B01")
                for g, n in zip(fx.GFP[well], fx.COUNTS[well]["24h"]) if n]
    assert (brightness.lo, brightness.hi) == pytest.approx((min(measured), max(measured)))
    assert brightness.undefined and brightness.cmap == "viridis"
    assert keys[2].centre == 1.0 and keys[2].log and not keys[0].undefined
    from review.features.image_browser.image_table._colour_key import key_ticks

    # A ratio window of (1/4, 4) on a log scale puts "no change" at the strip's middle, and nowhere else.
    assert key_ticks(keys[2]) == [(0.0, "0.25"), (1.0, "4"), (pytest.approx(0.5), "1 = no change")]
    assert key_ticks(keys[1]) == [(0.0, f"{min(measured):.3g}"), (1.0, f"{max(measured):.3g}")]
    rgb = key_rgb(keys, width_px=520)
    assert rgb.shape[1] == 520 and rgb.shape[0] > 3 * 40
    assert keys[1].scope == "one window shared by all 2 wells"


def test_a_key_says_when_its_window_was_fitted_to_a_crop(qtbot, tmp_path):
    from review.data.crop_tool import CropTool
    from review.features.image_browser.image_table.image_compose import colour_keys

    ctx = _loaded(qtbot, tmp_path)
    _use(ctx, [COUNT])
    cells = _cells([COUNT])
    _pool(ctx, cells)
    crop = CropTool(lambda: None)
    ctx.runtime.image_table.crop_tool = crop
    assert colour_keys(ctx, cells)[0].scope == "one window shared by all 2 wells"
    crop.set_crop((0, 0, 30, 30), notify=False)
    assert colour_keys(ctx, cells)[0].scope == "one window shared by all 2 wells, fitted to the cropped region"


# ── M1 is in test_image_table_nanowell_frames.py (the ring); M2 there too (the summed sentence).

# ── M3: short, case-kept labels ─────────────────────────────────────────────
def test_a_metric_is_named_short_and_as_spelt_and_its_token_keeps_its_case():
    from review.features.image_browser.image_table.state import ChannelSpec

    assert short_label("GFP Brightness (mean per-cell intensity)") == "GFP Brightness"
    assert display_token(LOG2_FC) == "NW: log₂ count fold change"
    assert display_token(montage_token("gfp")) == "MONTAGE GFP" and display_token("gfp (binary)") == "GFP (BINARY)"
    assert ChannelSpec(LOG2_FC).axis_token() == LOG2_FC
    assert ChannelSpec("gfp", "mask").axis_token() == "GFP (mask)"


def test_the_starting_table_leaves_out_the_frames_that_read_every_tile(qtbot, tmp_path):
    from review.features.image_browser.image_table.builder_view import _default_spec

    ctx = _loaded(qtbot, tmp_path)
    ctx.state.image_table.row_dims, ctx.state.image_table.col_dims = [], []
    channels = [cs.channel for cs in _default_spec(ctx).rows.channels]
    assert MONTAGE_TOKEN in channels and "GFP" in channels
    assert montage_token("GFP") not in channels and not [c for c in channels if c.startswith("NW: ")]


# ── M4: the swatch is the ramp ──────────────────────────────────────────────
def test_a_ramp_s_swatch_is_its_gradient_and_the_lut_editor_offers_it():
    from review.features.image_browser.image_table.builder_view import _cmap_swatch
    from review.features.image_browser.image_table.image_compose import LUT_COLOR_NAMES

    assert _cmap_swatch("viridis").startswith("qlineargradient(") and "rgb(68,1,84)" in _cmap_swatch("viridis")
    assert _cmap_swatch("Greens") == "#4ADE80"
    assert "viridis" in LUT_COLOR_NAMES and "coolwarm" in LUT_COLOR_NAMES


# ── M5: the bar says its length ─────────────────────────────────────────────
def test_the_scale_bar_carries_its_length_and_can_be_told_not_to(qtbot, tmp_path):
    from review.features.image_browser.image_table.image_compose import (
        bar_length_text, bar_rect_px, cell_chrome_kwargs, draw_cell_chrome,
    )

    ctx = _loaded(qtbot, tmp_path)
    st = ctx.state.image_table
    st.scale_bar_um, st.pixel_size_um = 43.0, 0.5
    cell = {"well": "A01", "chan": "GFP", "tp": "24", "fov": "F0"}
    assert cell_chrome_kwargs(ctx, 0, 0, cell, (86, {(0, 0)}, set()))["bar_text"] == "43 µm"
    st.scale_bar_label = False
    assert cell_chrome_kwargs(ctx, 0, 0, cell, (86, {(0, 0)}, set()))["bar_text"] == ""
    assert bar_length_text(1500) == "1.5 mm"
    blank = np.zeros((240, 240, 3), np.uint8)
    plain = draw_cell_chrome(blank, bar_px=86, bar_color="white")
    labelled = draw_cell_chrome(blank, bar_px=86, bar_color="white", bar_text="43 µm")
    x0, y0, x1, _y1 = bar_rect_px(240, 240, bar_px=86)
    above = np.s_[max(0, y0 - 30):y0 - 2, x0:x1]
    assert not plain[above].any() and labelled[above].max() == 255
    # A labelled bar sits on a plate that darkens the data under it, so it is not read off a bright disc.
    bright = np.full((240, 240, 3), 200, np.uint8)
    plated = draw_cell_chrome(bright, bar_px=86, bar_color="white", bar_text="43 µm")
    unplated = draw_cell_chrome(bright, bar_px=86, bar_color="white")
    beside = np.s_[y0 - 2, x0 - 1]
    assert plated[beside].max() < 150 and unplated[beside].min() == 200


# ── L1: a cropped metric map is re-painted, and its bar measured on what was painted ──
def test_a_cropped_metric_map_is_redrawn_finer_and_its_scale_bar_follows(qtbot, tmp_path):
    from review.data.crop_tool import CropTool
    from review.features.image_browser.image_table.image_compose import (
        _MAX_OVERSAMPLE, _finer_metric_map, cell_chrome_kwargs, cell_display_rgb,
    )

    ctx = _loaded(qtbot, tmp_path)
    _use(ctx, [COUNT])
    st = ctx.state.image_table
    cell = {"well": "A01", "chan": COUNT, "tp": "24", "fov": "F0"}
    arr = load_frame_array(ctx, st.image_cache, "A01", COUNT, "24", "F0")
    crop = CropTool(lambda: None)
    crop.set_crop((16, 16, 32, 32), notify=False)
    finer, k = _finer_metric_map(ctx, cell, crop, arr)
    assert k == _MAX_OVERSAMPLE and finer.values.shape == (16 * k, 16 * k)
    rgb = cell_display_rgb(ctx, st.image_cache, cell, 0, crop, arr)
    assert rgb.shape[:2] == (16 * k, 16 * k)
    # Nanowell (0, 0) is centred at 100 µm: pixel 23.26 of the frame, 7.26 of the crop, so 58 of the finer one.
    centre = int(((100.0 / fx.PX_UM) - 16) * k)
    assert finer.values[centre, centre] == fx.COUNTS["A01"]["24h"][0]
    st.scale_bar_um = 43.0
    assert cell_chrome_kwargs(ctx, 0, 0, cell, (86, {(0, 0)}, set()))["bar_px"] == round(43.0 / (fx.PX_UM / k))
    crop.set_crop(None, notify=False)
    assert _finer_metric_map(ctx, cell, crop, arr) is None


# ── L3: no field on a whole well ────────────────────────────────────────────
def test_a_grid_of_whole_wells_names_no_field(qtbot, tmp_path):
    from review.features.image_browser.image_table.facet_view import plan_facets
    from review.features.image_browser.image_table.image_compose import cell_identity_label

    ctx = _loaded(qtbot, tmp_path)
    st = ctx.state.image_table
    st.row_dims, st.col_dims = ["channel"], ["well"]
    frames = _cells([MONTAGE_TOKEN, COUNT])
    assert "FOV" not in plan_facets(ctx, frames).corner and "T:24" in plan_facets(ctx, frames).corner
    assert "FOV" in plan_facets(ctx, _cells(["GFP"])).corner
    assert "FOV" not in cell_identity_label(ctx, frames[1][0]) and "NW: Cell count" in cell_identity_label(
        ctx, frames[1][0])
    assert "FOV:F0" in cell_identity_label(ctx, _cells(["GFP"])[0][0])
