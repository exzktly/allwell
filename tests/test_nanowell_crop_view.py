"""The selected nanowell over time, large, in place of the Nanowells page's view.

Asked for: a larger view of a selected nanowell over time than the site detail's strip of small crops, shown
inside the page. Driven through the real builder, the real **Enlarge crops** button and real clicks on the
canvas, in the minimal shell the other page tests use (``tests/test_nanowell_page.py``); what is checked is read
back from the figure's artists.
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
    monkeypatch.setattr(nwc, "site_crops", lambda data, metrics, site: crops)
    return crops


def _press_enlarge(qtbot, ctx):
    """Press **Enlarge crops** with the mouse, once the site detail beside the figure has been laid out (it opens
    at zero width and takes its share of the splitter on the next layout pass)."""
    button = ctx.widgets.get("_nw_crops_btn")
    qtbot.waitUntil(lambda: button.width() > 0, timeout=5000)
    qtbot.mouseClick(button, Qt.LeftButton)
    return button


def _grid_images(ctx):
    fig = ctx.widgets.get("_nw_fig")
    return [im for ax in fig.axes for im in ax.get_images() if im.get_gid() == "nw-crop-grid-image"]


def _titles(ctx):
    fig = ctx.widgets.get("_nw_fig")
    return [ax.title.get_text() for ax in fig.axes if ax.title.get_gid() == "nw-crop-title"]


def test_enlarge_crops_draws_every_timepoint_large_on_one_scale_and_back_returns(page, qtbot, known_crops):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    assert page.widgets.get("_nw_crops_btn").text() == "Enlarge crops"
    button = _press_enlarge(qtbot, page)
    qtbot.waitUntil(lambda: len(_grid_images(page)) == 2, timeout=10000)
    assert _titles(page) == ["t_c", "t_a"]
    combined = np.concatenate([c.image.ravel() for c in known_crops.values()])
    lo, hi = np.percentile(combined, [1, 99])
    for image in _grid_images(page):
        assert image.get_clim() == pytest.approx((lo, hi))           # one scale, not each crop's own range
    assert button.text() == "Back to the plot"
    qtbot.mouseClick(button, Qt.LeftButton)
    assert _grid_images(page) == [] and page.state.feature_slice("nanowell").crop_view is False
    assert button.text() == "Enlarge crops"


def test_clearing_the_selection_leaves_the_crop_view(page, qtbot, known_crops):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    _press_enlarge(qtbot, page)
    qtbot.waitUntil(lambda: len(_grid_images(page)) == 2, timeout=10000)
    qtbot.mouseClick(page.widgets.get("_nw_clear_btn"), Qt.LeftButton)
    st = page.state.feature_slice("nanowell")
    assert st.selected_sites == [] and st.crop_view is False and _grid_images(page) == []
    button = page.widgets.get("_nw_crops_btn")
    assert button.text() == "Enlarge crops" and not button.isChecked()   # one press enlarges again


def test_a_new_dataset_opens_on_the_view_not_the_crop_view():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.crop_view = True
    st.reset_data()
    assert st.crop_view is False


def test_a_click_on_the_crop_view_selects_nothing(page, qtbot, known_crops):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    _press_enlarge(qtbot, page)
    qtbot.waitUntil(lambda: len(_grid_images(page)) == 2, timeout=10000)
    canvas = page.widgets.get("_nw_canvas")
    qtbot.mouseClick(canvas, Qt.LeftButton, pos=canvas.rect().center())
    st = page.state.feature_slice("nanowell")
    assert st.selected_sites == [["A01", 1, 0]] and st.crop_view is True
