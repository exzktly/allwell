"""Fluorescence on the Nanowells page: the page's own *Channel* control for Brightness and Nanowell fluorescence, and the
image-channel control that cuts every crop and the full-resolution montage from a fluorescence channel.

Reported: "There is no channel selector so it is not clear what is selected. EGFP shows up nowhere even though that
channel exists." The page drew its fluorescence metrics for the app's shared channel selector, which lives on the
Plotting band and is not on this page; a shared channel the tables lack (the phase channel, a ratio) greyed every
fluorescence metric with no control on the page to change it. And every picture the page drew was the phase image.

The fixtures carry two measured channels whose values differ by construction (GFP brightness 10 + q, RFP 50 + q; the
GFP tile 7, the RFP tile 9, the phase tile 1000), and a third channel the run read and did not measure, so a page
that draws the wrong channel — or the phase image under a fluorescence name — produces different numbers, not the
same ones (CLAUDE.md §7.8 #2). The page tests drive the real controls built by ``build_nanowell_tab``.
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from tifffile import imwrite

from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell.collect import _image_channels
from review.features.nanowell.image_compute import crops_for_sites
from review.features.nanowell.state import NanowellState

PX, SIDE = 0.5, 64
TILE_VALUE = {"PHASE": 1000, "GFP": 7, "RFP": 9}


def _tiles_zip(path, *, schema_has_channel=True, channels=("RFP", "GFP", "PHASE"), timepoints=("t_c", "t_a")):
    """One tile (F0) per timepoint, a constant image per channel, plus the junk real archives carry. The phase image
    is written last, so a reader that takes the first image of the tile it finds takes a fluorescence one."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(".DS_Store", b"junk")
        for tp in timepoints:
            for channel in channels:
                buffer = io.BytesIO()
                imwrite(buffer, np.full((SIDE, SIDE), TILE_VALUE[channel], dtype=np.uint16))
                # With a channel field the channel sits in it; without one, the token rides at the end of the name.
                name = (f"exp-{channel}-A01-F0-{tp}.tif" if schema_has_channel else f"exp-A01-F0-{tp}-{channel}.tif")
                zf.writestr(name, buffer.getvalue())
                zf.writestr(f"__MACOSX/._{name}", b"appledouble")


def _image_data(tmp_path, *, schema_has_channel=True, channels=("RFP", "GFP", "PHASE")):
    (tmp_path / "in").mkdir(exist_ok=True)
    _tiles_zip(tmp_path / "in" / "A01.zip", schema_has_channel=schema_has_channel, channels=channels)
    acq = {"pixel_size_um": {"x": PX, "y": PX}, "tile_size_px": {"x": SIDE, "y": SIDE}}
    block = {"status": "ok", "tiles": [{"tile": "F0", "left_um": 0.0, "top_um": 0.0, "source": "registered"}]}
    schema = "experiment:channel:well:fov:timepoint" if schema_has_channel else "experiment:well:fov:timepoint"
    return SimpleNamespace(grids={"A01": {"t_c": block, "t_a": block}}, acquisition={"t_c": acq, "t_a": acq},
                           in_dir=tmp_path / "in", out_dir=None, schema=schema, separator="-", phase_token="PHASE")


def _metrics():
    return pd.DataFrame([{"well": "A01", "nw_q": 0, "nw_r": 0, "timepoint": tp, "tp_order": k, "owner": "F0",
                          "well_x_um": 16.0, "well_y_um": 16.0, "radius_um": 5.0, "state": "measured"}
                         for k, tp in enumerate(("t_c", "t_a"))])


# ── the crops ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("schema_has_channel", [True, False], ids=["channel-field", "token-in-name"])
@pytest.mark.parametrize("channel", ["", "GFP", "RFP", "gfp"])
def test_a_crop_is_cut_from_the_chosen_channel_s_image_of_the_owner_tile(tmp_path, channel, schema_has_channel):
    data = _image_data(tmp_path, schema_has_channel=schema_has_channel)
    crops = crops_for_sites(data, _metrics(), [("A01", 0, 0)], channel=channel)[("A01", 0, 0)]
    expected = TILE_VALUE[channel.upper() or "PHASE"]
    assert sorted(crops) == ["t_a", "t_c"]
    for crop in crops.values():
        assert crop.source == "tile" and crop.image is not None
        assert set(np.unique(crop.image)) == {expected}
        assert crop.channel == channel


def test_a_channel_the_archive_lacks_says_so_and_never_shows_the_phase_image_in_its_place(tmp_path):
    data = _image_data(tmp_path, channels=("GFP", "PHASE"))
    crops = crops_for_sites(data, _metrics(), [("A01", 0, 0)], channel="RFP")[("A01", 0, 0)]
    for crop in crops.values():
        assert crop.image is None and crop.source == ""
        assert crop.note == "No RFP crop: the archive holds no RFP image of the owner tile here."


def test_without_the_input_archive_a_fluorescence_crop_says_the_montage_is_phase_only(tmp_path):
    data = _image_data(tmp_path)
    data.in_dir = None
    crops = crops_for_sites(data, _metrics(), [("A01", 0, 0)], channel="GFP")[("A01", 0, 0)]
    for crop in crops.values():
        assert crop.image is None
        assert crop.note == ("No GFP crop: the input archive is not beside this dataset (the run's montage is phase "
                             "only).")


# ── the full-resolution montage ──────────────────────────────────────────────
@pytest.mark.parametrize("channel", ["", "GFP", "RFP"])
def test_the_full_resolution_montage_is_drawn_from_the_chosen_channel_s_tiles(tmp_path, channel):
    source, why = fmc.montage_source(_image_data(tmp_path), "A01", "t_c", channel)
    assert source is not None, why
    assert source.channel == channel
    assert [t.member for t in source.tiles] == [f"exp-{channel or 'PHASE'}-A01-F0-t_c.tif"]
    store = fmc.TileStore(source)
    store.load(["F0"], level=0)
    raw = store._read(source.tiles[0])
    assert set(np.unique(raw)) == {TILE_VALUE[channel or "PHASE"]}


def test_a_montage_of_a_channel_with_no_tiles_says_which_channel(tmp_path):
    source, why = fmc.montage_source(_image_data(tmp_path, channels=("PHASE",)), "A01", "t_c", "GFP")
    assert source is None and why == "A01.zip holds no GFP image of any tile of A01 at t_c."


# ── which channels there are ─────────────────────────────────────────────────
def test_the_image_channels_are_the_run_s_fluorescence_channels_and_a_read_but_unmeasured_one_says_why():
    sidecar = {"fluor_tokens": ["phase", "gfp", "RFP", "CY5"]}
    channels, unmeasured = _image_channels(sidecar, "phase", ["GFP", "RFP"], read_any=True)
    assert channels == ["GFP", "RFP", "CY5"]                  # spelled as the tables spell them; phase is never one
    assert list(unmeasured) == ["CY5"]
    assert "no CY5_integrated column" in unmeasured["CY5"]
    # A channel in the tables the sidecar does not name is still offered.
    assert _image_channels({}, "phase", ["GFP"], read_any=True) == (["GFP"], {})
    # Nothing read yet: no channel is called unmeasured on no evidence.
    assert _image_channels(sidecar, "phase", [], read_any=False)[1] == {}


def test_the_page_s_channel_choices_are_kept_with_its_settings_and_a_stranger_value_is_refused():
    st = NanowellState(channel="RFP", image_channel="GFP")
    again = NanowellState()
    again.apply_config(st.to_config())
    assert (again.channel, again.image_channel) == ("RFP", "GFP")
    again.apply_config({"channel": 3, "image_channel": ["GFP"]})
    assert (again.channel, again.image_channel) == ("", "")


# ── the page ─────────────────────────────────────────────────────────────────
pytest.importorskip("PySide6")


def _two_channel_dataset(tmp_path):
    """The page tests' dataset with RFP measured beside GFP (brightness 50 + q against GFP's 10 + q, nanowell fluorescence
    3× GFP's), CY5 read and not measured, and the input archive holding a phase, GFP and RFP tile."""
    from tests.test_nanowell_page import _write_nanowell_dataset

    out = _write_nanowell_dataset(tmp_path, counts={"A01": {"t_c": [1, 2, 0, 1], "t_a": [2, 4, 3, 1]}})
    sites = pd.read_csv(out / "nanowells" / "A01.csv")
    sites["RFP_integrated"] = sites["GFP_integrated"] * 3
    sites["RFP_mean_bg"] = sites["GFP_mean_bg"] * 3
    sites.to_csv(out / "nanowells" / "A01.csv", index=False)
    cells = pd.read_csv(out / "measurements_A01.csv")
    cells["RFP_mean_intensity"] = cells["GFP_mean_intensity"] + 40.0
    cells.to_csv(out / "measurements_A01.csv", index=False)
    _tiles_zip(out.parent / "in" / "A01.zip")
    info = json.loads((out / "pipeline_info.json").read_text())
    acq = {"pixel_size_um": {"x": PX, "y": PX}, "tile_size_px": {"x": SIDE, "y": SIDE}}
    info.update(acquisition={"t_c": acq, "t_a": acq}, schema="experiment:channel:well:fov:timepoint", separator="-",
                phase_token="PHASE", fluor_tokens=["PHASE", "GFP", "RFP", "CY5"])
    (out / "pipeline_info.json").write_text(json.dumps(info))
    for path in (out / "nanowells").glob("*_grid.json"):
        grid = json.loads(path.read_text())
        for block in grid["timepoints"].values():
            block["tiles"] = [{"tile": "F0", "left_um": 0.0, "top_um": 0.0, "source": "registered"}]
        path.write_text(json.dumps(grid))
    return out


@pytest.fixture
def page(qtbot, tmp_path):
    from tests.test_nanowell_page import _loaded, _page

    ctx = _page(qtbot, _two_channel_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    _loaded(qtbot, ctx)
    return ctx


def _choose(combo, data) -> None:
    index = combo.findData(data)
    assert index >= 0, (data, [combo.itemData(i) for i in range(combo.count())])
    combo.setCurrentIndex(index)


def _drawn_values(ctx) -> list:
    st = ctx.state.feature_slice("nanowell")
    return sorted(float(v) for panel in st.model.panels for swarm in panel.swarms for v in swarm.values)


def test_the_channel_control_lists_the_measured_channels_and_greys_the_unmeasured_one_with_why(page):
    combo = page.widgets.get("_nw_channel_cb")
    assert combo.isVisible()
    assert [combo.itemText(i) for i in range(combo.count())] == ["GFP", "RFP", "CY5 (not measured)"]
    cy5 = combo.model().item(2)
    assert not cy5.isEnabled() and "no CY5_integrated column" in cy5.toolTip()
    assert combo.currentData() == "GFP"


def test_choosing_a_channel_on_the_page_draws_that_channel_s_brightness(page, qtbot):
    _choose(page.widgets.get("_nw_metric_cb"), "brightness")
    gfp = _drawn_values(page)
    assert gfp and max(gfp) < 20                                  # GFP's cells read 10 + q
    _choose(page.widgets.get("_nw_channel_cb"), "RFP")
    assert page.widgets.get("_nw_fig").axes[0].get_ylabel() == "Cell brightness (mean per cell, own-well background subtracted) · RFP"
    assert _drawn_values(page) == pytest.approx([v + 40 for v in gfp])
    assert page.state.feature_slice("nanowell").channel == "RFP"


def test_a_fold_change_of_a_fluorescence_channel_is_drawn_for_the_page_s_channel(page):
    _choose(page.widgets.get("_nw_channel_cb"), "RFP")
    _choose(page.widgets.get("_nw_metric_cb"), "signal_fc")
    assert page.widgets.get("_nw_fig").axes[0].get_ylabel() == "Nanowell fluorescence fold change (own-well background subtracted) · RFP"
    from review.features.nanowell import controller as nwc

    assert nwc.drawn_metric_column(page) == "signal_fc_RFP"


def test_a_shared_channel_the_tables_lack_no_longer_greys_every_fluorescence_metric(page):
    """The reported symptom: the shared selector (not on this page) named the phase channel, and every fluorescence
    metric was greyed with no control here to change it. The page now draws its first measured channel."""
    from review.features.nanowell import controller as nwc

    page.state.channel.active_channel = "phase"
    nwc.nw_on_channel_changed(page)
    metric_cb = page.widgets.get("_nw_metric_cb")
    assert metric_cb.model().item(metric_cb.findData("brightness")).isEnabled()
    assert page.widgets.get("_nw_channel_cb").currentText() == "GFP"


def _image_combos(ctx):
    return [ctx.widgets.get(name) for name in ("_nw_strip_image_channel_cb", "_nw_extremes_image_channel_cb",
                                               "_nw_array_image_channel_cb")]


def test_the_image_channel_controls_offer_phase_and_every_channel_the_run_read(page):
    for combo in _image_combos(page):
        assert [combo.itemText(i) for i in range(combo.count())] == ["Phase (PHASE)", "GFP", "RFP", "CY5"]
        assert combo.isEnabled() and combo.currentData() == ""


def test_choosing_gfp_beside_the_strip_cuts_the_crops_from_the_gfp_tiles_and_says_so(page, qtbot):
    from tests.test_nanowell_crop_view import _images
    from tests.test_nanowell_page import _click_dot

    _click_dot(qtbot, page, ("A01", 1, 0))
    qtbot.waitUntil(lambda: len(_images(page, "_nw_strip_fig")) == 2, timeout=10000)
    assert {int(v) for im in _images(page, "_nw_strip_fig") for v in np.unique(im.get_array())} == {1000}
    strip_combo, extremes_combo, array_combo = _image_combos(page)
    _choose(strip_combo, "GFP")
    qtbot.waitUntil(lambda: {int(v) for im in _images(page, "_nw_strip_fig")
                             for v in np.unique(im.get_array())} == {7}, timeout=10000)
    caption = [t.get_text() for t in page.widgets.get("_nw_strip_fig").texts if t.get_gid() == "nw-crop-caption"]
    assert caption and caption[0].startswith("Crops (GFP image): A01 · q 1, r 0")
    assert extremes_combo.currentData() == array_combo.currentData() == "GFP"      # one setting, every control
    assert page.state.feature_slice("nanowell").image_channel == "GFP"


def test_the_extremes_montage_is_cut_from_the_chosen_channel(page, qtbot):
    from review.features.nanowell import controller as nwc

    nwc.set_view(page, "extremes")
    _choose(_image_combos(page)[1], "RFP")
    fig = page.widgets.get("_nw_fig")

    def shown():
        return {int(v) for ax in fig.axes for im in ax.get_images() for v in np.unique(im.get_array())}

    qtbot.waitUntil(lambda: shown() == {9}, timeout=10000)
    caption = [t.get_text() for t in fig.texts if t.get_gid() == "nw-extremes-caption"]
    assert caption and "Crops of the RFP image" in caption[0]


def test_full_resolution_reads_the_chosen_channel_s_tiles_and_the_run_montage_says_it_is_phase(page, qtbot):
    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_full_montage import _footer

    nwc.set_view(page, "array")
    image_cb = page.widgets.get("_nw_image_cb")
    _choose(image_cb, "tiles")
    _choose(_image_combos(page)[2], "RFP")
    st = page.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: st.tile_store is not None and st.tile_store.loaded() == 1 and not st.tile_pending,
                    timeout=20000)
    assert st.tile_store.source.channel == "RFP"
    assert [t.member for t in st.tile_store.source.tiles][0].startswith("exp-RFP-")
    assert any("Full resolution, RFP, from the raw tiles in A01.zip." in t for t in _footer(page))
    _choose(image_cb, "montage")
    assert any("The run montage is the phase image; choose Full resolution to see RFP." in t for t in _footer(page))
