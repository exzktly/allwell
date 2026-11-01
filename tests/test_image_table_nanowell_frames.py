"""The Image Table's nanowell frames: a fluorescence montage and a per-nanowell metric map, laid out like any channel
(``review/data/nanowell_images``, ``review/features/nanowell/table_frame_compute``; ARCHITECTURE §6.2l).

The run keeps only a phase montage, so before these the Image Table could show a nanowell well only as phase and as
single tiles: no fluorescence across the array, and no computed metric at all. Every frame of a (well, timepoint) is
drawn on the stored montage's geometry, so one crop is one region of the well in every row.

The fixture (``tests/fixtures/nanowell_dataset``) has one 64-px tile per well placed at the well's origin and a
montage at the tile's own pixel size, so a montage pixel *is* a tile pixel and every expected value below is read
from the fixture's own files and tables, not from the code under test.
"""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.data.frame_load import MONTAGE_TOKEN, load_frame_array, nanowell_frame_of  # noqa: E402
from review.data.nanowell_images import (  # noqa: E402
    METRIC_MAP_COLORMAP, is_nanowell_frame, metric_label, metric_token, montage_channel, montage_token,
)
from tests.fixtures import nanowell_dataset as fx  # noqa: E402

GFP_MONTAGE = montage_token("GFP")
COUNT_MAP = metric_token("Cell count")
#: The axis spells every channel token upper-case (``ChannelSpec.axis_token``); the readers must not care.
BRIGHTNESS_MAP = metric_token("GFP Cell fluorescence (mean per cell, own-well background subtracted)").upper()


def _loaded(qtbot, tmp_path, **kw):
    from tests._minimal_shell import load_dataset, minimal_shell

    out = fx.build_nanowell_dataset(tmp_path / "nw", **kw) / "out"
    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, out)
    ctx.state.dataset.data_dir = out
    return ctx, out


def _px(q: int, r: int) -> tuple[int, int]:
    """``(row, column)`` of the montage pixel at nanowell (q, r)'s centre, from the fixture's own placement rule."""
    x, y = 100.0 + 20.0 * q, 100.0 + 20.0 * r
    return int(y / fx.PX_UM), int(x / fx.PX_UM)


def test_the_tokens_round_trip_and_name_nothing_else():
    assert montage_channel(MONTAGE_TOKEN) == "" and montage_channel(GFP_MONTAGE) == "gfp"
    assert montage_channel("GFP") is None and montage_channel("MONTAGE ") == ""
    assert metric_label(COUNT_MAP) == "Cell count" and metric_label("NW:") is None
    assert not is_nanowell_frame("GFP") and not is_nanowell_frame("GFP (binary)")
    assert all(is_nanowell_frame(t) for t in (MONTAGE_TOKEN, GFP_MONTAGE, COUNT_MAP, BRIGHTNESS_MAP))


def test_a_nanowell_run_offers_each_fluorescence_montage_and_every_metric_map(qtbot, tmp_path):
    from review.data.dataset_dims import channel_options

    ctx, _out = _loaded(qtbot, tmp_path)
    options = channel_options(ctx)
    assert options[:2] == [MONTAGE_TOKEN, GFP_MONTAGE]
    assert montage_token("PHASE") not in options          # the phase channel's montage is MONTAGE itself
    assert COUNT_MAP in options and metric_token("GFP Cell fluorescence (mean per cell, own-well background subtracted)") in options


def test_a_standard_run_offers_no_nanowell_frame(qtbot, mini_dataset):
    from review.data.dataset_dims import channel_options
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, mini_dataset)
    assert not [o for o in channel_options(ctx) if is_nanowell_frame(o)]


def test_a_fluorescence_montage_is_the_channel_s_own_tile_placed_where_the_run_put_it(qtbot, tmp_path):
    import tifffile

    ctx, _out = _loaded(qtbot, tmp_path)
    inp = tmp_path / "nw" / "in"
    with zipfile.ZipFile(inp / "A01.zip") as zf:
        gfp = tifffile.imread(io.BytesIO(zf.read("exp_GFP_A01_F0_24h.tif"))).astype(np.float32)
        phase = tifffile.imread(io.BytesIO(zf.read("exp_PHASE_A01_F0_24h.tif"))).astype(np.float32)
    cache: dict = {}
    got = load_frame_array(ctx, cache, "A01", GFP_MONTAGE, "24", "F9")      # a montage has no field: F9 is ignored
    assert got is not None and got.shape == gfp.shape
    assert np.array_equal(got, gfp) and not np.array_equal(got, phase)
    frame = nanowell_frame_of(cache, "A01", GFP_MONTAGE, "24")
    assert frame.um_per_px == fx.PX_UM and frame.note == ""


def test_a_channel_the_run_never_read_is_no_frame_and_says_why(qtbot, tmp_path):
    ctx, _out = _loaded(qtbot, tmp_path)
    cache: dict = {}
    assert load_frame_array(ctx, cache, "A01", montage_token("RFP"), "24", "F0") is None
    assert "no RFP images" in nanowell_frame_of(cache, "A01", montage_token("RFP"), "24").note


def test_a_metric_map_fills_each_nanowell_with_its_value_and_leaves_the_rest_empty(qtbot, tmp_path):
    ctx, _out = _loaded(qtbot, tmp_path)
    cache: dict = {}
    got = load_frame_array(ctx, cache, "B01", COUNT_MAP, "24h", "F0")
    for (q, r), n in zip(fx.SITES, fx.COUNTS["B01"]["24h"]):
        assert got[_px(q, r)] == n, (q, r)           # an empty nanowell is a count of 0, drawn as 0
    assert np.isnan(got[0, 0]) and np.isnan(got[-1, -1])
    frame = nanowell_frame_of(cache, "B01", COUNT_MAP, "24h")
    assert not frame.undefined.any() and frame.um_per_px == fx.PX_UM


def test_a_kept_metric_frame_names_the_same_metric_whatever_background_its_dataset_used(qtbot, tmp_path):
    """A frame kept in a view carries its metric's label, which names the background the fluorescence was measured
    above. The same view on a dataset measured above another background still draws the same metric, and a label
    naming another metric is not taken for it."""
    ctx, _out = _loaded(qtbot, tmp_path)               # measured above each well's own floor
    own = load_frame_array(ctx, {}, "A01", BRIGHTNESS_MAP, "24", "F0")
    other = metric_token("GFP Cell fluorescence (mean per cell, empty-well background subtracted)")
    kept = load_frame_array(ctx, {}, "A01", other, "24", "F0")
    assert own is not None and kept is not None and np.array_equal(own, kept, equal_nan=True)
    signal = metric_token("GFP Nanowell fluorescence (total, empty-well background subtracted)")
    assert not np.array_equal(load_frame_array(ctx, {}, "A01", signal, "24", "F0"), own, equal_nan=True)


def test_a_nanowell_with_no_value_is_an_empty_ring_never_background_a_disc_or_zero(qtbot, tmp_path):
    ctx, _out = _loaded(qtbot, tmp_path)
    cache: dict = {}
    got = load_frame_array(ctx, cache, "A01", BRIGHTNESS_MAP, "24", "F0")
    frame = nanowell_frame_of(cache, "A01", BRIGHTNESS_MAP, "24")
    for (q, r), n, gfp in zip(fx.SITES, fx.COUNTS["A01"]["24h"], fx.GFP["A01"]):
        y, x = _px(q, r)
        around = frame.undefined[y - 2:y + 3, x - 2:x + 3]
        if n:
            assert got[y, x] == pytest.approx(gfp) and not around.any()
        else:
            # Its rim is marked and its middle is empty: a ring, which no value of any colormap can look like.
            assert np.isnan(got[y, x]) and not frame.undefined[y, x] and around.any()
    assert not frame.undefined[0, 0]
    assert frame.missing == {"no cells": 1} and frame.total == 5


def test_the_ring_is_the_rim_of_the_nanowell_and_a_value_fills_its_disc():
    import pandas as pd

    from review.features.nanowell.table_frame_compute import metric_map

    rows = pd.DataFrame({"well_x_um": [20.0, 60.0], "well_y_um": [20.0, 20.0], "radius_um": [10.0, 10.0],
                         "v": [7.0, float("nan")]})
    frame = metric_map(rows, "v", "V", (0.0, 0.0, 1.0, 40, 80), pd.Series(["", "occluded"], index=rows.index))
    assert frame.values[20, 20] == 7.0 and frame.values[20, 11] == 7.0 and not frame.undefined[10:31, 10:31].any()
    assert np.isnan(frame.values[20, 60]) and not frame.undefined[20, 60]           # the middle is empty
    assert frame.undefined[20, 51] and frame.undefined[11, 60]                      # the rim is the ring
    assert frame.missing == {"occluded": 1} and frame.total == 2


def test_a_metric_map_follows_the_nanowells_baseline_rather_than_a_cached_copy(qtbot, tmp_path):
    ctx, _out = _loaded(qtbot, tmp_path)
    cache: dict = {}
    token = metric_token("Count fold change")
    at = _px(*fx.SITES[0])
    st = ctx.state.feature_slice("nanowell")
    first = float(load_frame_array(ctx, cache, "A01", token, "24", "F0")[at])
    st.baseline = "tp:24h"                                   # 24 h over itself
    second = float(load_frame_array(ctx, cache, "A01", token, "24", "F0")[at])
    counts = fx.COUNTS["A01"]
    assert first == pytest.approx(counts["24h"][0] / counts["0h"][0]) and second == pytest.approx(1.0)


def test_the_cell_draws_its_background_grey_and_an_undefined_nanowell_as_a_ring(qtbot, tmp_path):
    from matplotlib import colormaps

    from review.features.image_browser.image_table.builder_view import _default_colormap_for
    from review.features.image_browser.image_table.image_compose import _undefined_rgb, cell_display_rgb
    from review.features.image_browser.image_table.state import ChannelSpec

    ctx, _out = _loaded(qtbot, tmp_path)
    assert _default_colormap_for(ctx, BRIGHTNESS_MAP) == METRIC_MAP_COLORMAP     # not GFP's Greens
    spec = ChannelSpec(BRIGHTNESS_MAP, colormap=_default_colormap_for(ctx, BRIGHTNESS_MAP))
    ctx.state.image_table.channel_opts = {spec.axis_token(): spec.to_dict()}
    cache: dict = {}
    cell = {"well": "A01", "chan": spec.axis_token(), "tp": "24", "fov": "F0"}
    arr = load_frame_array(ctx, cache, "A01", cell["chan"], "24", "F0")
    rgb = cell_display_rgb(ctx, cache, cell, 0, None, arr)
    from review.features.image_browser.image_table.image_compose import metric_map_background_rgb

    # Not black: viridis' darkest end all but vanishes on black, and the lowest values with it.
    background = metric_map_background_rgb()
    assert tuple(rgb[0, 0]) == background and background != (0, 0, 0)
    empty = next(s for s, n in zip(fx.SITES, fx.COUNTS["A01"]["24h"]) if not n)
    y, x = _px(*empty)
    assert tuple(rgb[y, x]) == background                                 # the ring's empty middle
    assert (rgb[y - 2:y + 3, x - 2:x + 3] == np.array(_undefined_rgb(), dtype=np.uint8)).all(axis=-1).any()
    # The window is the defined values' own (10 to 50 here), not NaN: the brightest nanowell is the colormap's top.
    brightest = fx.SITES[int(np.argmax([g if n else -1 for g, n in zip(fx.GFP["A01"], fx.COUNTS["A01"]["24h"])]))]
    top = tuple(int(v) for v in (np.asarray(colormaps[METRIC_MAP_COLORMAP](1.0)[:3]) * 255).astype(np.uint8))
    assert tuple(rgb[_px(*brightest)]) == top


def test_a_scale_bar_on_a_frame_is_measured_at_the_frame_s_scale_not_the_tiles(qtbot, tmp_path):
    from review.features.image_browser.image_table.image_compose import cell_chrome_kwargs

    ctx, _out = _loaded(qtbot, tmp_path)
    st = ctx.state.image_table
    st.scale_bar_um, st.pixel_size_um = 43.0, 0.5           # 86 px at the tiles' size; 10 px on the frame
    cell = {"well": "A01", "chan": GFP_MONTAGE, "tp": "24", "fov": "F0"}
    load_frame_array(ctx, st.image_cache, "A01", GFP_MONTAGE, "24", "F0")
    plan = (86, {(0, 0)}, set())
    assert cell_chrome_kwargs(ctx, 0, 0, cell, plan)["bar_px"] == round(43.0 / fx.PX_UM)
    fluor = {"well": "A01", "chan": "GFP", "tp": "24", "fov": "F0"}
    assert cell_chrome_kwargs(ctx, 0, 0, fluor, plan)["bar_px"] == 86


def test_what_the_frames_leave_out_is_one_sentence_per_metric_summed_over_the_wells(qtbot, tmp_path):
    from review.features.image_browser.image_table.image_compose import (
        _load_array, partial_scan_clause, reset_scan_misses,
    )

    ctx, _out = _loaded(qtbot, tmp_path)
    reset_scan_misses(ctx)
    for well in ("A01", "B01"):           # the token as the axis now carries it: the metric's own spelling
        _load_array(ctx, {}, well, metric_token("GFP Cell fluorescence (mean per cell, own-well background subtracted)"), "24", "F0")
    empty = sum(1 for well in ("A01", "B01") for n in fx.COUNTS[well]["24h"] if not n)
    clause = partial_scan_clause(ctx)
    assert (f"NW: GFP Cell fluorescence: {empty} of 10 nanowells in 2 wells have no value ({empty} no cells), "
            "drawn as empty rings") in clause
    assert "A01" not in clause and "B01" not in clause              # summed, not listed well by well
    reset_scan_misses(ctx)
    assert partial_scan_clause(ctx) == ""


def test_without_the_nanowells_feature_the_phase_montage_still_draws_and_nothing_else_is_offered(qtbot, tmp_path,
                                                                                                 monkeypatch):
    from review.data.dataset_dims import channel_options

    ctx, out = _loaded(qtbot, tmp_path)
    real = ctx.features.service
    monkeypatch.setattr(ctx.features, "service",
                        lambda key: None if key == "image_table.nanowell_frame" else real(key))
    assert not [o for o in channel_options(ctx) if is_nanowell_frame(o) and o != MONTAGE_TOKEN]
    cache: dict = {}
    got = load_frame_array(ctx, cache, "A01", MONTAGE_TOKEN, "24", "F0")
    from PIL import Image

    with zipfile.ZipFile(out / "A01_out.zip") as zf:
        want = np.asarray(Image.open(io.BytesIO(zf.read("A01_24h_montage.png"))).convert("L"), dtype=np.float32)
    assert np.array_equal(got, want)
    assert nanowell_frame_of(cache, "A01", MONTAGE_TOKEN, "24").um_per_px is None     # so it carries no bar
    assert load_frame_array(ctx, {}, "A01", COUNT_MAP, "24", "F0") is None
