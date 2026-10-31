"""The selected nanowell's crops: the strip under the site detail, and **Pop out**, which shows them large in a
window of their own.

Reported: the in-page crop view (**Enlarge crops** / **Back to the plot**) and **Clear selection** misbehaved —
the crop view replaced the page's figure, and the view switch beside it went on drawing crops, so the page read as
stuck. Asked for instead: a **Pop out** button above the crops. Driven through the real builder, the real button
and real clicks on the canvas, in the minimal shell the other page tests use (``tests/test_nanowell_page.py``);
what is checked is read back from the figures' artists.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.image_compute import Crop  # noqa: E402
from tests.test_nanowell_page import _click_dot, _loaded, page  # noqa: E402,F401


@pytest.fixture
def known_crops(monkeypatch):
    """Two crops of very different brightness: a dim one (0-10) and a bright one (0-100), so one shared scale and
    each crop stretched to its own range draw differently."""
    crops = {"t_c": Crop(np.linspace(0, 10, 400).reshape(20, 20), "tile", ""),
             "t_a": Crop(np.linspace(0, 100, 400).reshape(20, 20), "tile", "")}
    monkeypatch.setattr(nwc, "site_crops", lambda data, metrics, site, **_kw: crops)
    return crops


def _press_pop_out(qtbot, ctx):
    """Press **Pop out** with the mouse, once the site detail beside the figure has been laid out (it opens at zero
    width and takes its share of the splitter on the next layout pass)."""
    button = ctx.widgets.get("_nw_popout_btn")
    qtbot.waitUntil(lambda: button.isVisible() and button.width() > 0, timeout=5000)
    qtbot.mouseClick(button, Qt.LeftButton)
    window = ctx.widgets.get("_nw_popout")
    qtbot.addWidget(window)
    return window


def _images(ctx, key):
    fig = ctx.widgets.get(key)
    return [im for ax in fig.axes for im in ax.get_images()]


def _popout_title(ctx):
    return [t.get_text() for t in ctx.widgets.get("_nw_popout_fig").texts if t.get_gid() == "nw-crop-grid-title"]


def _panel_titles(ctx):
    return [ax.get_title(loc="left") for ax in ctx.widgets.get("_nw_fig").axes if ax.get_title(loc="left")]


def test_the_strip_under_pop_out_shows_the_first_selected_nanowell_s_crops(page, qtbot, known_crops):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    qtbot.waitUntil(lambda: len(_images(page, "_nw_strip_fig")) == 2, timeout=10000)
    titles = [ax.get_title() for ax in page.widgets.get("_nw_strip_fig").axes]
    assert titles == ["t_c", "t_a"]
    assert page.widgets.get("_nw_popout_btn").isVisible()


def test_pop_out_draws_every_timepoint_large_on_one_scale_and_leaves_the_page_s_view(page, qtbot, known_crops):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    before = _panel_titles(page)
    window = _press_pop_out(qtbot, page)
    assert window.isVisible() and window.isWindow()
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 2, timeout=10000)
    assert _popout_title(page) == ["A01 · q 1, r 0 over time · phase image"]
    combined = np.concatenate([c.image.ravel() for c in known_crops.values()])
    lo, hi = np.percentile(combined, [1, 99])
    for image in _images(page, "_nw_popout_fig"):
        assert image.get_clim() == pytest.approx((lo, hi))           # one scale, not each crop's own range
    assert _panel_titles(page) == before and before                  # the page still shows its own view


def test_the_pop_out_follows_the_selection_and_says_when_there_is_none(page, qtbot, known_crops):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    _press_pop_out(qtbot, page)
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 2, timeout=10000)
    _click_dot(qtbot, page, ("A01", 0, 0))
    qtbot.waitUntil(lambda: _popout_title(page) == ["A01 · q 0, r 0 over time · phase image"], timeout=10000)
    nwc._select(page, None, False)                                   # what a click on empty space does
    said = [t.get_text() for ax in page.widgets.get("_nw_popout_fig").axes for t in ax.texts
            if t.get_gid() == "nw-popout-empty"]
    assert said == ["Select a nanowell on the plot to see its crops here."]
    assert not page.widgets.get("_nw_detail_pane").isVisible()


def test_switching_the_view_with_the_crops_popped_out_draws_the_new_view(page, qtbot, known_crops):  # noqa: F811
    """The reported stuck state: with the old in-page crop view on, the view switch redrew the crops, not the view."""
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    _press_pop_out(qtbot, page)
    nwc.set_view(page, "trajectories")
    texts = [t.get_gid() for t in page.widgets.get("_nw_fig").texts]
    assert "nw-traj-footer" in texts


def test_the_old_crop_view_and_clear_selection_buttons_are_gone(page):  # noqa: F811
    for name in ("_nw_crops_btn", "_nw_clear_btn"):
        assert page.widgets.get_or_none(name) is None
    from review.features.nanowell.state import NanowellState

    assert not hasattr(NanowellState(), "crop_view")


def _popout_texts(ctx, gid):
    fig = ctx.widgets.get("_nw_popout_fig")
    return [t.get_text() for t in list(fig.texts) + [t for ax in fig.axes for t in ax.texts] if t.get_gid() == gid] + [
        ax.get_title() for ax in fig.axes if gid == "nw-crop-title" and ax.title.get_gid() == gid and ax.get_title()]


def test_the_pop_out_has_its_own_controls_and_each_changes_what_it_draws(page, qtbot, known_crops):  # noqa: F811
    st = _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    window = _press_pop_out(qtbot, page)
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 2, timeout=10000)
    names = ("_nw_popout_image_channel_cb", "_nw_popout_range_btn", "_nw_popout_cells_box",
             "_nw_popout_metrics_box", "_nw_popout_labels_box", "_nw_popout_go_to_btn")
    for name in names:
        widget = page.widgets.get(name)
        assert widget.window() is window and widget.isVisible(), name          # in the pop-out, not the page
    # They start on the page's settings: the same channels as the strip's, the same range, the same boxes.
    strip_cb, own_cb = page.widgets.get("_nw_strip_image_channel_cb"), page.widgets.get("_nw_popout_image_channel_cb")
    assert [own_cb.itemText(i) for i in range(own_cb.count())] == [strip_cb.itemText(i) for i in range(strip_cb.count())]
    assert page.widgets.get("_nw_popout_range_btn").text() == page.widgets.get("_nw_strip_range_btn").text()
    assert page.widgets.get("_nw_popout_metrics_box").isChecked() and page.widgets.get("_nw_popout_labels_box").isChecked()
    assert sorted(_popout_texts(page, "nw-crop-title")) == ["t_a", "t_c"]
    assert len(_popout_texts(page, "nw-crop-grid-caption")) == 1
    assert _popout_texts(page, "nw-crop-grid-metrics")
    # Labels off: no timepoint over the crops and no caption; the title stays.
    page.widgets.get("_nw_popout_labels_box").click()
    assert st.popout_labels is False
    assert _popout_texts(page, "nw-crop-title") == [] and _popout_texts(page, "nw-crop-grid-caption") == []
    assert _popout_title(page) == ["A01 · q 1, r 0 over time · phase image"]
    # Metrics off: no numbers under the crops — the page's one setting, so the Extremes box follows.
    page.widgets.get("_nw_popout_metrics_box").click()
    assert st.crop_metrics is False and not page.widgets.get("_nw_extremes_metrics_box").isChecked()
    assert _popout_texts(page, "nw-crop-grid-metrics") == []
    # Cells: the strip's box follows the pop-out's.
    page.widgets.get("_nw_popout_cells_box").click()
    assert st.crop_cells is True and page.widgets.get("_nw_strip_cells_box").isChecked()
    # The display range typed from the pop-out's button reaches its crops and every Range button.
    nwc.set_display_range(page, 2.0, 50.0)
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 2, timeout=10000)
    assert [im.get_clim() for im in _images(page, "_nw_popout_fig")] == [(2.0, 50.0), (2.0, 50.0)]
    assert page.widgets.get("_nw_popout_range_btn").text() == "Range: 2–50"
    page.widgets.get("_nw_popout_labels_box").click()
    assert st.popout_labels is True and sorted(_popout_texts(page, "nw-crop-title")) == ["t_a", "t_c"]
    window.hide()


def test_the_pop_out_s_labels_setting_is_kept_with_the_page():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.popout_labels = False
    again = NanowellState()
    again.apply_config(st.to_config())
    assert again.popout_labels is False


def test_the_crop_numbers_name_nanowell_fluorescence_as_the_metric_is_named(page, qtbot, known_crops):  # noqa: F811
    """Tracker L21: the metric is *Nanowell fluorescence* everywhere on the page, so the numbers under its crops say
    ``fluor.``, not the retired "signal"."""
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    _press_pop_out(qtbot, page)
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 2, timeout=10000)
    numbers = " · ".join(_popout_texts(page, "nw-crop-grid-metrics"))
    assert "fluor. " in numbers and "bright " in numbers, numbers
    assert "signal" not in numbers, numbers
