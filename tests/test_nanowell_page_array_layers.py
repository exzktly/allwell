"""The Array view's elements are each turned on and off, and the three views it replaced are settings of it.

Asked for: the Nanowells page's "full montage", "array QC" and "array map" were three implementations with
different behaviour — the same nanowell drawn three ways, only one of them clickable, only one zoomable — to be
made "one reusable view where elements can be turned on/off as needed". Earlier, for Array QC alone: "can the
montage be toggled on and off, and the overlay toggled on and off as well?"

Driven through the real page: its Image combo, Positions switch and Layers menu actions, each triggered as a
click triggers it. What the figure then holds is asserted — the image, the tile outlines and labels, the
nanowells, the addresses, the run record — each present or absent as the controls say. The choice is kept with
the page's other settings, and a page saved on Array map or Array QC opens on the layers that view drew.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_full_montage import montage_page  # noqa: E402,F401


@pytest.fixture
def qc_page(montage_page, qtbot):  # noqa: F811
    """The full-montage fixture with a run montage at t_a and nanowells at four measured places, on the Array view
    as Array QC drew it."""
    from PIL import Image

    from review.features.nanowell import controller as nwc

    ctx, in_dir = montage_page
    out = in_dir.parent / "out"
    buffer = io.BytesIO()
    Image.fromarray(np.full((100, 100), 128, np.uint8)).save(buffer, format="PNG")
    with zipfile.ZipFile(out / "A01_out.zip", "w") as zf:
        zf.writestr("A01_t_a_montage.png", buffer.getvalue())
    grid_path = out / "nanowells" / "A01_grid.json"
    grid = json.loads(grid_path.read_text())
    grid["timepoints"]["t_a"].update(montage_origin_um=[0.0, 0.0], montage_um_per_px=2.48)
    grid_path.write_text(json.dumps(grid))
    st = ctx.state.feature_slice("nanowell")
    qtbot.waitUntil(lambda: not st.fetching, timeout=10000)         # the fixture's own read, before the montage
    st.data.grids["A01"] = grid["timepoints"]
    rows = (st.metrics["well"] == "A01") & (st.metrics["timepoint"] == "t_a")
    for k, (x, y) in zip(st.metrics.index[rows], ((40.0, 40.0), (200.0, 40.0), (40.0, 200.0), (200.0, 200.0))):
        st.metrics.loc[k, ["well_x_um", "well_y_um", "radius_um"]] = (x, y, 30.0)
    st.montages.clear()
    ctx.widgets.get("_nw_preset_qc").trigger()
    qtbot.waitUntil(lambda: ("A01", "t_a") in st.montages, timeout=10000)
    assert st.montages[("A01", "t_a")][0] is not None, st.montages[("A01", "t_a")][1]
    nwc.redraw(ctx)
    return ctx


def _drawn(ctx):
    from tests.test_nanowell_page import _texts

    st = ctx.state.feature_slice("nanowell")
    ax = st.drawn.ax
    return {"image": len(ax.images),
            "tiles": sum(p.get_gid() == "nw-array-tile-box" for p in ax.patches),
            "tile labels": len(_texts(ctx, "nw-array-tile")),
            "nanowells": sum(len(c.get_offsets()) for c in ax.collections),
            "addresses": len(_texts(ctx, "nw-array-address")),
            "record": len(_texts(ctx, "nw-array-record"))}


def _toggle(ctx, layer):
    ctx.widgets.get(f"_nw_layer_{layer}").trigger()                  # a click on the menu item


def test_each_layer_comes_off_the_figure_and_back(qc_page):
    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    assert _drawn(ctx) == {"image": 1, "tiles": 4, "tile labels": 4, "nanowells": 4, "addresses": 0, "record": 3}
    image_cb = ctx.widgets.get("_nw_image_cb")
    framed = st.drawn.region
    image_cb.setCurrentIndex(image_cb.findData("off"))
    assert _drawn(ctx)["image"] == 0
    assert st.drawn.region == pytest.approx(framed)                    # the same frame, without the image
    _toggle(ctx, "tiles")
    assert (_drawn(ctx)["tiles"], _drawn(ctx)["tile labels"]) == (0, 0)
    _toggle(ctx, "nanowells")
    assert _drawn(ctx)["nanowells"] == 0 and st.hits.entries == []    # nothing drawn, nothing to click
    _toggle(ctx, "record")
    assert _drawn(ctx)["record"] == 0
    _toggle(ctx, "addresses")
    assert _drawn(ctx)["addresses"] == 4                              # 60 µm across: room for every address
    image_cb.setCurrentIndex(image_cb.findData("montage"))
    _toggle(ctx, "nanowells")
    assert _drawn(ctx) == {"image": 1, "tiles": 0, "tile labels": 0, "nanowells": 4, "addresses": 4, "record": 0}


def test_the_nanowells_are_filled_by_the_metric_or_outlined_by_state_in_the_shared_palette(qc_page):
    from matplotlib.colors import to_hex

    from ui.nanowell_sites import state_colour

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    states = list(st.metrics[(st.metrics["well"] == "A01") & (st.metrics["timepoint"] == "t_a")]["state"])
    # Array QC: by state. Each state is drawn as its own collection, with its own mark (``_STATE_STYLE``), so each
    # nanowell's ring colour is read back by where it is drawn.
    sites = st.drawn.sites
    ring = {}
    for coll in st.drawn.ax.collections:
        edges = coll.get_edgecolors()
        for j, (x, y) in enumerate(coll.get_offsets()):
            ring[(round(float(x), 3), round(float(y), 3))] = to_hex(edges[j % len(edges)])
    drawn = [ring[(round(float(x), 3), round(float(y), 3))] for x, y in zip(sites.x, sites.y)]
    assert drawn == [to_hex(state_colour(s)) for s in sites.state]
    assert sorted(sites.state) == sorted(states)
    ctx.widgets.get("_nw_colour_metric").trigger()
    filled = [c for c in st.drawn.ax.collections if c.get_array() is not None]
    assert len(filled) == 1 and list(filled[0].get_array()) == [2.0, 4.0, 3.0, 1.0]     # the cell counts at t_a


def test_the_presets_are_the_views_this_one_replaced(qc_page):
    from review.features.nanowell.state import ArrayLayers

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    for name in ("map", "full", "qc"):
        ctx.widgets.get(f"_nw_preset_{name}").trigger()
        assert st.layers == ArrayLayers.preset(name)
    assert (st.layers.image, st.layers.positions, st.layers.colour) == ("montage", "measured", "state")
    assert st.layers.tiles and st.layers.record and not st.layers.addresses


def test_the_choice_is_kept_with_the_page_s_settings_and_a_page_saved_on_an_old_view_opens_on_its_layers():
    from review.features.nanowell.state import ArrayLayers, NanowellState

    st = NanowellState()
    assert st.layers == ArrayLayers()
    st.view, st.layers.image, st.layers.tiles, st.layers.colour = "array", "tiles", True, "state"
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert (restored.view, restored.layers) == ("array", st.layers)
    old_map = NanowellState()
    old_map.apply_config({"view": "array_map"})
    assert (old_map.view, old_map.layers) == ("array", ArrayLayers.preset("map"))
    old_qc = NanowellState()
    old_qc.apply_config({"view": "array_qc", "qc_montage": False, "qc_overlay": False})
    assert old_qc.view == "array"
    assert (old_qc.layers.image, old_qc.layers.tiles, old_qc.layers.nanowells, old_qc.layers.record) == (
        "off", False, False, True)
    junk = NanowellState()
    junk.apply_config({"view": "array", "layers": {"image": "x-ray", "tiles": "yes", "record": True}})
    assert (junk.layers.image, junk.layers.tiles, junk.layers.record) == ("montage", False, True)


def test_the_array_controls_show_only_on_the_array_view(qc_page):
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    names = ("_nw_image_cb", "_nw_positions_seg", "_nw_layers_btn", "_nw_fit_btn")
    assert all(ctx.widgets.get(name).isVisible() for name in names)
    nwc.set_view(ctx, "distribution")
    assert not any(ctx.widgets.get(name).isVisible() for name in names)
    assert not ctx.widgets.get("_nw_anchor_btn").isVisible()
