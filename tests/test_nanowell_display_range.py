"""The Nanowells page's **Display range**: a *min* / *max* / **Auto** brightness window for its image channel.

Asked for: "a display range LUT for the nanowell channels just like the other places in the app" — the min / max
window with an Auto that Review Image, the Image Table and the Cell Browser give a channel — and, once set anywhere on
the page, kept for all renders until changed again. Every picture of the page's image channel — the crop strip, the
pop-out, the Extremes montage, the Array view's full-resolution image — is drawn on it, and it is the page's own
(``NanowellState.display_ranges``), kept with its settings.

Driven through the real buttons and the real popover fields in the minimal shell of ``tests/test_nanowell_page.py``;
what is checked is read back from the drawn images (their ``clim`` and pixels), never from the state the figure was
drawn from. Each range is chosen so that it and the percentile scale it replaces give different numbers.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.image_compute import Crop  # noqa: E402
from review.features.nanowell.renderer import crop_scale  # noqa: E402
from tests.test_nanowell_crop_view import _images, _press_pop_out, known_crops  # noqa: E402,F401
from tests.test_nanowell_page import _click_dot, _loaded, page  # noqa: E402,F401


def _set_range(qtbot, ctx, button_name: str, low: str, high: str) -> None:
    """Open the Display range under *button_name* with a click and type *low* and *high*, as a user does."""
    button = ctx.widgets.get(button_name)
    qtbot.mouseClick(button, Qt.LeftButton)
    lo_edit, hi_edit = ctx.widgets.get("_nw_range_lo_edit"), ctx.widgets.get("_nw_range_hi_edit")
    lo_edit.setText(low)
    lo_edit.editingFinished.emit()
    hi_edit.setText(high)
    hi_edit.editingFinished.emit()


def _clims(ctx, key):
    return [tuple(round(v, 6) for v in image.get_clim()) for image in _images(ctx, key)]


def _strip_ready(qtbot, ctx):
    _loaded(qtbot, ctx)
    _click_dot(qtbot, ctx, ("A01", 1, 0))
    qtbot.waitUntil(lambda: len(_images(ctx, "_nw_strip_fig")) == 2, timeout=10000)


# ── the crops ────────────────────────────────────────────────────────────────
def test_a_range_typed_beside_the_strip_draws_the_strip_and_the_pop_out_on_it(page, qtbot, known_crops):  # noqa: F811
    _strip_ready(qtbot, page)
    combined = np.concatenate([c.image.ravel() for c in known_crops.values()])
    auto = tuple(round(float(v), 6) for v in np.percentile(combined, [1, 99]))
    assert _clims(page, "_nw_strip_fig") == [auto, auto]                  # Auto first: one percentile scale
    window = _press_pop_out(qtbot, page)
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 2, timeout=10000)

    _set_range(qtbot, page, "_nw_strip_range_btn", "2", "50")

    assert _clims(page, "_nw_strip_fig") == [(2.0, 50.0), (2.0, 50.0)]
    assert _clims(page, "_nw_popout_fig") == [(2.0, 50.0), (2.0, 50.0)]
    assert window.isVisible()
    for name in ("_nw_strip_range_btn", "_nw_extremes_range_btn", "_nw_array_range_btn"):
        assert page.widgets.get(name).text() == "Range: 2–50"
    caption = [t.get_text() for t in page.widgets.get("_nw_popout_fig").texts if t.get_gid() == "nw-crop-grid-caption"]
    assert "every panel on the display range 2 to 50" in caption[0]


def test_auto_gives_the_pictures_back_their_own_scale(page, qtbot, known_crops):  # noqa: F811
    _strip_ready(qtbot, page)
    before = _clims(page, "_nw_strip_fig")
    _set_range(qtbot, page, "_nw_strip_range_btn", "2", "50")
    assert _clims(page, "_nw_strip_fig") != before
    qtbot.mouseClick(page.widgets.get("_nw_range_auto_btn"), Qt.LeftButton)
    assert _clims(page, "_nw_strip_fig") == before
    assert page.widgets.get("_nw_strip_range_btn").text() == "Range: auto"
    assert (page.widgets.get("_nw_range_lo_edit").text(), page.widgets.get("_nw_range_hi_edit").text()) == ("", "")


def test_a_range_whose_high_is_not_above_its_low_is_not_taken(page, qtbot, known_crops):  # noqa: F811
    _strip_ready(qtbot, page)
    before = _clims(page, "_nw_strip_fig")
    _set_range(qtbot, page, "_nw_strip_range_btn", "50", "2")
    assert _clims(page, "_nw_strip_fig") == before
    assert nwc.display_range(page) is None


def test_a_range_set_on_the_page_is_kept_for_every_render_until_changed_here(page, qtbot, known_crops):  # noqa: F811
    """Asked for: once set anywhere on the page, the range stays for all renders until it is changed again. Review
    Image writes its automatic window into the app's shared per-channel store whenever it draws, so the page keeps
    its own: a write there — with a range set here, and with none — changes nothing on the page."""
    _strip_ready(qtbot, page)
    auto = _clims(page, "_nw_strip_fig")
    st = page.state.feature_slice("nanowell")
    key = nwc.display_key(st)
    assert key == (st.data.phase_token or "phase").lower()              # the phase image goes by its token
    page.state.image_view.set_lut(key, 3.0, 30.0)                       # what Review Image does on a draw
    nwc.redraw(page)
    assert _clims(page, "_nw_strip_fig") == auto                        # unset here: still Auto

    _set_range(qtbot, page, "_nw_strip_range_btn", "4", "40")
    page.state.image_view.set_lut(key, 0.0, 1.0)
    for view in ("trajectories", "extremes", "array", "distribution"):  # other renders, and back
        nwc.set_view(page, view)
    _click_dot(qtbot, page, ("A01", 1, 0))
    nwc.redraw(page)
    assert _clims(page, "_nw_strip_fig") == [(4.0, 40.0), (4.0, 40.0)]
    assert page.widgets.get("_nw_array_range_btn").text() == "Range: 4–40"


def test_the_range_is_kept_with_the_page_s_settings():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.display_ranges = {"phase": (4.0, 40.0), "egfp": (100.0, 3000.0)}
    again = NanowellState()
    again.apply_config(st.to_config())
    assert again.display_ranges == {"phase": (4.0, 40.0), "egfp": (100.0, 3000.0)}
    damaged = NanowellState()
    damaged.apply_config({"display_ranges": {"phase": [40, 4], "egfp": [1, "x"], "rfp": [1, 2], 3: [1, 2]}})
    assert damaged.display_ranges == {"rfp": (1.0, 2.0)}                # a bad entry restores as Auto


def test_a_montage_crop_keeps_a_scale_of_its_own_under_a_chosen_range():
    """The montage is an 8-bit picture: a range in the raw tiles' units would mean nothing on it, so under a chosen
    range the crops cut from it keep their own percentile scale, and the caption says so."""
    tile = Crop(np.linspace(0, 4000, 400).reshape(20, 20), "tile", "")
    montage = Crop(np.linspace(0, 255, 400).reshape(20, 20), "montage", "from the montage")
    scale = crop_scale([tile, montage], (100.0, 3000.0))
    assert scale.of(tile) == (100.0, 3000.0)
    lo, hi = np.percentile(montage.image, [1, 99])
    assert scale.of(montage) == pytest.approx((lo, hi))
    assert "the crops cut from the montage, an 8-bit picture, on a scale of their own" in scale.said()
    auto = crop_scale([tile, montage], None)                           # Auto: one scale over both, as before
    assert auto.of(tile) == auto.of(montage) and not auto.chosen


# ── the Extremes montage ─────────────────────────────────────────────────────
@pytest.fixture
def extremes_page(qtbot, tmp_path, monkeypatch):
    from tests.test_nanowell_page_extremes import page as make_page

    return make_page.__wrapped__(qtbot, tmp_path, monkeypatch)


def test_the_extremes_montage_is_drawn_on_the_range(extremes_page, qtbot):
    from tests.test_nanowell_page_extremes import _choose_extremes

    ctx = extremes_page
    _choose_extremes(qtbot, ctx)
    images = [im for ax in ctx.widgets.get("_nw_fig").axes for im in ax.get_images()]
    assert images and all(im.get_clim() != (5.0, 7.0) for im in images)
    _set_range(qtbot, ctx, "_nw_extremes_range_btn", "5", "7")
    images = [im for ax in ctx.widgets.get("_nw_fig").axes for im in ax.get_images()]
    assert images and all(im.get_clim() == (5.0, 7.0) for im in images)
    caption = [t.get_text() for t in ctx.widgets.get("_nw_fig").texts if t.get_gid() == "nw-extremes-caption"]
    assert "every crop on the display range 5 to 7." in caption[0]


# ── the Array view's full-resolution image ───────────────────────────────────
def test_the_full_resolution_image_is_drawn_on_the_range(qtbot, tmp_path):
    """The four tiles hold 1000, 2000, 3000 and 4000. The well-wide stretch (1000 to 4000) shades them 0, 85, 170
    and 255; a range of 1000 to 3000 shades them 0, 127, 255 and 255."""
    from tests.test_nanowell_full_montage import LEFT_TOP, SHADE, _footer, _full, _shade_at
    from tests.test_nanowell_full_montage import montage_page as make_page

    ctx, _in_dir = make_page.__wrapped__(qtbot, tmp_path)
    st = _full(qtbot, ctx)
    assert {t: _shade_at(st, x + 60, y + 60) for t, (x, y) in LEFT_TOP.items()} == SHADE
    _set_range(qtbot, ctx, "_nw_array_range_btn", "1000", "3000")
    assert {t: _shade_at(st, x + 60, y + 60) for t, (x, y) in LEFT_TOP.items()} == {
        "F0": 0, "F1": 127, "F2": 255, "F3": 255}
    assert "Full resolution, phase, from the raw tiles in A01.zip, on the display range 1000 to 3000." in _footer(ctx)


def test_setting_a_range_saves_the_page_s_settings(page, qtbot, known_crops, monkeypatch):  # noqa: F811
    _strip_ready(qtbot, page)
    saved = []
    monkeypatch.setattr(nwc, "_save_soon", lambda ctx: saved.append(dict(ctx.state.feature_slice("nanowell")
                                                                             .to_config()["display_ranges"])))
    _set_range(qtbot, page, "_nw_strip_range_btn", "4", "40")
    assert saved and saved[-1] == {nwc.display_key(page.state.feature_slice("nanowell")): [4.0, 40.0]}


def test_each_image_channel_keeps_its_own_range(page, qtbot, known_crops):  # noqa: F811
    _strip_ready(qtbot, page)
    st = page.state.feature_slice("nanowell")
    assert st.data.image_channels, "the fixture needs a fluorescence channel to switch to"
    _set_range(qtbot, page, "_nw_strip_range_btn", "4", "40")
    nwc.set_image_channel(page, st.data.image_channels[0])
    assert nwc.display_range(page) is None and page.widgets.get("_nw_strip_range_btn").text() == "Range: auto"
    nwc.set_image_channel(page, "")
    assert nwc.display_range(page) == (4.0, 40.0)
    qtbot.waitUntil(lambda: len(_images(page, "_nw_strip_fig")) == 2, timeout=10000)
    assert _clims(page, "_nw_strip_fig") == [(4.0, 40.0), (4.0, 40.0)]
