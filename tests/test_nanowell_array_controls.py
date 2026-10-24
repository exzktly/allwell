"""The Array view's band stays put, its overlays toggle with one button, its labels carry the metric, and it zooms
from buttons as well as the wheel.

Asked for, of the Nanowells page's Array view:

- "the metric is hidden/shown as a function of the layer selection, which changes the button positions and is
  jarring. the buttons should be frozen in their position regardless of layer selection";
- "make a single button to show/hide colored overlays so they can be easily toggled back and forth";
- "currently the annotation on a well is by position; I also want it to annotate with the selected metric";
- "Nanowell array viewer needs better zoom control", and on a phone (over VNC, where a two-finger swipe arrives
  as the wheel) a swipe that zooms.

Driven through the real page and its real controls (the ``qc_page`` fixture: a run montage, four nanowells at
measured places, Array QC's layers).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_full_montage import montage_page  # noqa: E402,F401
from tests.test_nanowell_page_array_layers import _drawn, _toggle, qc_page  # noqa: E402,F401


#: Every control the page puts on its band, by its registered name.
_BAND = ("_nw_view_seg", "_nw_metric_cb", "_nw_axis_seg", "_nw_baseline_cb", "_nw_cohort_box", "_nw_well_cb",
         "_nw_image_cb", "_nw_positions_seg", "_nw_layers_btn", "_nw_overlays_btn", "_nw_zoom_out_btn",
         "_nw_zoom_lbl", "_nw_zoom_in_btn", "_nw_fit_btn", "_nw_one_btn", "_nw_anchor_btn", "_nw_clear_anchor_btn",
         "_nw_rerun_btn", "_nw_tp_slider", "_nw_tp_cb", "_nw_responding_edit", "_nw_control_cb")


def _shown_on_band(ctx) -> list:
    """The band's controls that are shown, in the band's order: what decides where each control sits."""
    return [name for name in _BAND if ctx.widgets.get(name).isVisibleTo(ctx.window)]


def _labels(ctx) -> list[str]:
    from tests.test_nanowell_page import _texts

    return _texts(ctx, "nw-array-address")


# ── frozen controls ──────────────────────────────────────────────────────────
def test_no_layer_choice_shows_or_hides_a_control_on_the_band(qc_page):
    ctx = qc_page
    before = _shown_on_band(ctx)
    assert {"_nw_metric_cb", "_nw_baseline_cb", "_nw_anchor_btn", "_nw_tp_slider"} <= set(before)
    for key in ("nanowells", "addresses", "values", "tiles", "anchor", "record"):
        _toggle(ctx, key)
        assert _shown_on_band(ctx) == before, key
        _toggle(ctx, key)
    for colour in ("metric", "state"):
        ctx.widgets.get(f"_nw_colour_{colour}").trigger()
        assert _shown_on_band(ctx) == before, colour
    for preset in ("map", "full", "qc"):
        ctx.widgets.get(f"_nw_preset_{preset}").trigger()
        assert _shown_on_band(ctx) == before, preset
    positions = ctx.widgets.get("_nw_positions_seg")
    positions.setCurrentIndex(1)
    assert _shown_on_band(ctx) == before, "lattice"
    positions.setCurrentIndex(0)
    ctx.widgets.get("_nw_overlays_btn").click()
    assert _shown_on_band(ctx) == before, "overlays"


def test_the_metric_is_greyed_exactly_when_no_layer_draws_it(qc_page):
    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    metric_cb = ctx.widgets.get("_nw_metric_cb")
    assert (st.layers.colour, st.layers.values) == ("state", False)        # Array QC: nothing draws the metric
    assert not metric_cb.isEnabled()
    _toggle(ctx, "values")
    assert metric_cb.isEnabled()                                          # written inside each nanowell
    _toggle(ctx, "values")
    ctx.widgets.get("_nw_colour_metric").trigger()
    assert metric_cb.isEnabled()                                          # the fill
    ctx.widgets.get("_nw_overlays_btn").click()
    assert not metric_cb.isEnabled()                                      # the fill is hidden with the overlays


# ── one button for the overlays ──────────────────────────────────────────────
def test_overlays_takes_every_coloured_layer_off_and_puts_back_exactly_the_ones_that_were_on(qc_page):
    from review.features.nanowell.controller import OVERLAYS_HIDDEN_NOTE
    from tests.test_nanowell_page import _texts

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    _toggle(ctx, "addresses")
    layers = st.layers.to_config()
    shown = {"image": 1, "tiles": 4, "tile labels": 4, "nanowells": 4, "addresses": 4, "record": 3}
    assert _drawn(ctx) == shown
    button = ctx.widgets.get("_nw_overlays_btn")
    assert button.isChecked()
    button.click()
    # The image, the labels and the run record stay; the nanowells and tiles go, and the figure says why.
    assert _drawn(ctx) == {**shown, "tiles": 0, "tile labels": 0, "nanowells": 0}
    assert st.hits.entries == []                                          # nothing drawn, nothing to click
    assert any(OVERLAYS_HIDDEN_NOTE in t for t in _texts(ctx, "nw-array-footer"))
    assert st.layers.to_config() == layers                                # the Layers menu's choices are kept
    assert ctx.widgets.get("_nw_layer_tiles").isChecked()
    button.click()
    assert _drawn(ctx) == shown
    assert not any(OVERLAYS_HIDDEN_NOTE in t for t in _texts(ctx, "nw-array-footer"))


def test_with_the_overlays_hidden_no_anchor_can_be_set_unseen(montage_page, qtbot):
    from tests.test_nanowell_full_montage import _full

    ctx, _in_dir = montage_page
    st = _full(qtbot, ctx)
    anchor_btn = ctx.widgets.get("_nw_anchor_btn")
    assert anchor_btn.isEnabled()
    anchor_btn.click()
    assert st.anchor_mode
    ctx.widgets.get("_nw_overlays_btn").click()
    assert not st.anchor_mode and not anchor_btn.isEnabled()
    ctx.widgets.get("_nw_overlays_btn").click()
    assert anchor_btn.isEnabled()


# ── the metric written on each nanowell ──────────────────────────────────────
def test_each_nanowell_is_labelled_with_its_address_and_its_value_of_the_chosen_metric(qc_page):
    from tests.test_nanowell_page import _texts

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    _toggle(ctx, "addresses")
    addresses = _labels(ctx)
    assert len(addresses) == 4 and all("\n" not in a for a in addresses)
    _toggle(ctx, "values")
    # The cell counts at t_a, in the nanowells' order, under each address.
    assert _labels(ctx) == [f"{a}\n{v}" for a, v in zip(addresses, ("2", "4", "3", "1"))]
    _toggle(ctx, "addresses")
    assert _labels(ctx) == ["2", "4", "3", "1"]
    # Another metric, other values: the label follows the Metric control, not a fixed column.
    rows = (st.metrics["well"] == "A01") & (st.metrics["timepoint"] == "t_a")
    st.metrics.loc[rows, "c"] = [0.125, 0.5, 1.75, np.nan]
    metric_cb = ctx.widgets.get("_nw_metric_cb")
    metric_cb.setCurrentIndex(metric_cb.findData("c"))
    assert st.metric == "c"
    # A nanowell without a value writes nothing: it is not a zero.
    assert _labels(ctx) == ["0.125", "0.5", "1.75"]
    assert not any("zoom in" in t for t in _texts(ctx, "nw-array-footer"))


def test_a_label_writes_three_significant_figures_and_never_a_missing_value_as_a_number():
    from types import SimpleNamespace

    from review.features.nanowell.renderer import site_label_texts

    sites = SimpleNamespace(labels=["0,0", "1,-1", "", ""], values=np.array([12.3456, np.nan, 0.0, np.nan]))
    assert site_label_texts(sites, addresses=True, values=True) == ["0,0\n12.3", "1,-1", "0", ""]
    assert site_label_texts(sites, addresses=False, values=True) == ["12.3", "", "0", ""]
    assert site_label_texts(sites, addresses=True, values=False) == ["0,0", "1,-1", "", ""]


def test_values_are_on_by_default_and_the_saved_layers_keep_them():
    from review.features.nanowell.state import ArrayLayers, NanowellState

    assert ArrayLayers().values
    assert not ArrayLayers.preset("map").values                 # Batch Export's figure is drawn as it was
    st = NanowellState()
    st.layers.values = False
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert restored.layers.values is False
    old = NanowellState()
    old.apply_config({"layers": {"addresses": False}})          # saved before there was a Values layer
    assert old.layers.values is True


# ── zoom ─────────────────────────────────────────────────────────────────────
def _centre(region):
    return (region[0] + region[2]) / 2, (region[1] + region[3]) / 2


def test_minus_and_plus_zoom_about_the_centre_and_the_readout_says_how_far(qc_page):
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    label = ctx.widgets.get("_nw_zoom_lbl")
    fit = st.drawn.region
    assert label.text() == "1×"
    ctx.widgets.get("_nw_zoom_in_btn").click()
    width = st.drawn.region[2] - st.drawn.region[0]
    assert width == pytest.approx(nwc.BUTTON_STEP * (fit[2] - fit[0]), rel=1e-6)
    assert _centre(st.drawn.region) == pytest.approx(_centre(fit))
    assert label.text() == f"{1 / nwc.BUTTON_STEP:.2g}×"
    ctx.widgets.get("_nw_zoom_out_btn").click()
    assert st.drawn.region == pytest.approx(fit)
    assert label.text() == "1×"


def test_zooming_out_stops_at_the_limit_and_greys_the_button(qc_page):
    from review.features.nanowell import controller as nwc

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    out_btn = ctx.widgets.get("_nw_zoom_out_btn")
    fit_width = st.drawn.fit[2] - st.drawn.fit[0]
    for _ in range(12):
        if out_btn.isEnabled():
            out_btn.click()
    assert nwc.array_zoom_factor(st.drawn) == pytest.approx(nwc.ZOOM_OUT_LIMIT)
    assert st.drawn.region[2] - st.drawn.region[0] == pytest.approx(fit_width / nwc.ZOOM_OUT_LIMIT)
    assert not out_btn.isEnabled() and ctx.widgets.get("_nw_zoom_in_btn").isEnabled()


def test_the_wheel_off_the_image_zooms_about_the_centre_rather_than_doing_nothing(qc_page):
    """Over VNC from a phone a two-finger swipe is the wheel, at wherever the remote pointer was left — often the
    colour bar or the footer, where the wheel used to be ignored."""
    from matplotlib.backend_bases import MouseEvent

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    canvas = ctx.widgets.get("_nw_canvas")
    fit = st.drawn.region
    event = MouseEvent("scroll_event", canvas, 2, 2, step=1)                 # the figure's corner, off the axes
    assert event.inaxes is None
    canvas.callbacks.process("scroll_event", event)
    width = st.drawn.region[2] - st.drawn.region[0]
    assert width == pytest.approx(0.8 * (fit[2] - fit[0]), rel=1e-6)
    assert _centre(st.drawn.region) == pytest.approx(_centre(fit))
    assert not math.isclose(width, fit[2] - fit[0])


# ── double-click a nanowell ──────────────────────────────────────────────────
def test_a_double_click_centres_a_nanowell_zooms_until_it_fills_the_view_and_opens_its_detail(qc_page, qtbot):
    """Asked for: "double click to center, focus, and maximize a specific nanowell". Driven by a real
    double-click on the canvas, off the nanowell's centre but inside its rim — wider than a single click's 6 px."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_full_montage import _widget_point

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    k = 1                                                              # the nanowell at (200, 40), radius 30
    site = list(st.drawn.sites.sites[k])
    assert (st.drawn.sites.x[k], st.drawn.sites.y[k], st.drawn.sites.radius[k]) == (200.0, 40.0, 30.0)
    canvas, point = _widget_point(ctx, st, 220.0, 50.0)                # 22 µm from its centre, inside the disc
    QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, point)
    qtbot.wait(50)
    x0, y0, x1, y1 = st.drawn.region
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == pytest.approx((200.0, 40.0), abs=0.01)
    # The nanowell fills the view's shorter side, with FOCUS_MARGIN of room.
    assert min(x1 - x0, y1 - y0) == pytest.approx(2 * 30.0 * nwc.FOCUS_MARGIN, rel=1e-6)
    assert st.selected_sites == [[site[0], int(site[1]), int(site[2])]]
    assert ctx.widgets.get("_nw_detail_pane").isVisibleTo(ctx.window)


def test_a_double_click_on_empty_space_or_with_set_anchor_on_changes_nothing(montage_page, qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tests.test_nanowell_full_montage import _full, _widget_point

    ctx, _in_dir = montage_page
    st = _full(qtbot, ctx)
    region = st.drawn.region
    sites = st.drawn.sites
    gaps = [np.hypot(sites.x - x, sites.y - y) - sites.radius for x, y in [(region[0] + 1, region[1] + 1)]]
    assert min(gaps[0]) > 50.0                                         # the view's corner is far from every nanowell
    canvas, point = _widget_point(ctx, st, region[0] + 1, region[1] + 1)
    QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, point)
    assert st.region is None and st.drawn.region == pytest.approx(region)


def test_with_the_overlays_hidden_a_double_click_still_finds_the_nanowell(qc_page, qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tests.test_nanowell_full_montage import _widget_point

    ctx = qc_page
    st = ctx.state.feature_slice("nanowell")
    ctx.widgets.get("_nw_overlays_btn").click()
    canvas, point = _widget_point(ctx, st, 40.0, 200.0)
    QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, point)
    x0, y0, x1, y1 = st.drawn.region
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == pytest.approx((40.0, 200.0), abs=0.01)
