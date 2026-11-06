"""Drawing the composite: tiles, the missing placeholder, one brightness scale, provenance on click.

Asked for: the nanowells inside a Distribution window as an artificial hex array. It must read as what it is — not a
real array — and must never pass a tile with no image off as a picture: a tile that could not be cut is a hatched
hexagon and the caption counts it by reason. Every tile is on one brightness scale, so wells of different exposure
can be compared. A click on a tile selects the real nanowell it stands for, and nothing about the composite reaches the
page's well or the plate selection.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from matplotlib.figure import Figure

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from review.features.nanowell import _window_controller as wnc  # noqa: E402
from review.features.nanowell import composite_compute as cc  # noqa: E402
from review.features.nanowell._composite_render import render_composite  # noqa: E402
from review.features.nanowell.image_compute import Crop  # noqa: E402
from review.features.nanowell.window_compute import WindowRow  # noqa: E402
from tests.test_nanowell_page import _loaded, page  # noqa: E402,F401
from tests.test_nanowell_window_page import _drag, _first_panel  # noqa: E402


def _rows():
    """Seven nanowells: A01 holds four and B02 three."""
    return ([WindowRow(("A01", q, 0), k, 1.0, {}) for k, q in zip((1, 3, 4, 7), range(4))]
            + [WindowRow(("B02", q, 0), k, 1.0, {}) for k, q in zip((2, 5, 6), range(3))])


def _crop(low, high):
    rng = np.random.default_rng(int(low) + int(high))
    return Crop(rng.uniform(low, high, (12, 12)), "tile", "an owner tile")


def _draw(crops, **kwargs):  # one timepoint, as the composite was first drawn
    comp = cc.composite(_rows())
    fig = Figure(figsize=(9.0, 6.0))
    render_composite(fig, comp, crops, "T1", n_total=7, **kwargs)
    return fig, comp


def _artists(fig, gid):
    ax = fig.axes[0]
    return [a for a in list(ax.images) + list(ax.patches) + list(ax.texts) + list(fig.texts) if a.get_gid() == gid]


def test_every_tile_is_drawn_or_said_missing():
    comp = cc.composite(_rows())
    crops = {t.source: {"T1": _crop(0, 100)} for t in comp.tiles}
    crops[("A01", 1, 0)] = {"T1": Crop(None, "tile", "no image of EGFP in archive")}      # cut, but no image
    del crops[("B02", 2, 0)]                                                              # not cut at all
    fig, _comp = _draw(crops)
    assert len(_artists(fig, "nw-composite-tile")) == 5 and len(_artists(fig, "nw-composite-missing")) == 2
    caption = _artists(fig, "nw-composite-caption")[0].get_text()
    assert "1 no image of EGFP in archive" in caption and "1 not cut yet" in caption


def test_one_brightness_scale_across_wells():
    """A01's crops run 0-100 and B02's 1000-1100: each on a scale of its own would stretch both to the same grey."""
    crops = {t.source: {"T1": _crop(0, 100) if t.source[0] == "A01" else _crop(1000, 1100)}
             for t in cc.composite(_rows()).tiles}
    fig, _comp = _draw(crops)
    clims = {tuple(image.get_clim()) for image in _artists(fig, "nw-composite-tile")}
    assert len(clims) == 1
    (lo, hi), = clims
    assert lo < 100 and hi > 1000


def test_the_title_names_it_a_composite_not_a_well():
    fig, _comp = _draw({})
    assert _artists(fig, "nw-composite-title")[0].get_text().startswith(
        "Composite of 7 of 7 nanowells from 2 wells — not a real array")


def test_the_composite_has_no_well_id():
    comp = cc.composite(_rows())
    assert not hasattr(comp, "well") and not hasattr(comp.tiles[0], "well")
    assert [f.name for f in dataclasses.fields(cc.CompositeTile)] == ["x", "y", "source", "rank", "patch"]


# ── on the real page ─────────────────────────────────────────────────────────
def _open_hex(page, qtbot):
    st = _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    wnc.open_window_popout(page)
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(1)
    qtbot.waitUntil(lambda: bool(st.popout_tiles), timeout=10000)
    return st


def _tile_point(page, st, tile):
    canvas = page.widgets.get("_nw_popout_canvas")
    ax = page.widgets.get("_nw_popout_fig").axes[0]
    px, py = ax.transData.transform((tile[0], tile[1]))
    ratio = canvas.devicePixelRatioF()
    return canvas, QPoint(int(round(px / ratio)), int(round(canvas.height() - py / ratio)))


def test_a_click_on_a_tile_selects_its_real_nanowell(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    well, selected = st.well, set(page.state.selection.selected_wells)
    tile = st.popout_tiles[0]
    canvas, point = _tile_point(page, st, tile)
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point)
    assert st.selected_sites == [[*tile[2]]]
    assert st.well == well and set(page.state.selection.selected_wells) == selected


def test_pointing_at_a_tile_names_its_nanowell(page, qtbot, monkeypatch):  # noqa: F811
    st = _open_hex(page, qtbot)
    said = []
    monkeypatch.setattr(page.notify, "set_status", said.append)
    tile = st.popout_tiles[1]
    canvas, point = _tile_point(page, st, tile)
    # QTest.mouseMove delivers nothing under the offscreen platform, so the move is sent to the canvas as the event it is.
    QApplication.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, QPointF(point), QPointF(canvas.mapToGlobal(point)),
                                               Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    well, q, r = tile[2]
    assert said and said[-1] == f"Tile {tile[3]} · {well} {q},{r}"


def test_one_composite_per_timepoint_side_by_side_each_at_its_own_timepoint():
    """Two timepoints are two panels across, each drawing its own timepoint's crops — T1's run 0-100 and T2's
    1000-1100, so a panel drawing the other timepoint's crops would hold the other values — on one scale."""
    comp = cc.composite(_rows())
    crops = {t.source: {"T1": _crop(0, 100), "T2": _crop(1000, 1100)} for t in comp.tiles}
    fig = Figure(figsize=(9.0, 6.0))
    render_composite(fig, comp, crops, ["T1", "T2"], n_total=7)
    left, right = fig.axes
    assert left.get_position().x0 < right.get_position().x0
    assert abs(left.get_position().y0 - right.get_position().y0) < 1e-9          # side by side, one row
    assert [ax.get_title() for ax in fig.axes] == ["T1", "T2"]
    means = [[float(im.get_array().mean()) for im in ax.images] for ax in fig.axes]
    assert len(means[0]) == len(means[1]) == len(comp.tiles)
    assert max(means[0]) < 100 and min(means[1]) > 1000
    assert len({tuple(im.get_clim()) for ax in fig.axes for im in ax.images}) == 1
    title = [t.get_text() for t in fig.texts if t.get_gid() == "nw-composite-title"][0]
    assert title.endswith(" · 2 timepoints")


def test_the_composites_sit_in_one_row_however_many_timepoints():
    comp = cc.composite(_rows())
    fig = Figure(figsize=(9.0, 6.0))
    render_composite(fig, comp, {}, ["T1", "T2", "T3", "T4", "T5"], n_total=7)
    ys = {round(ax.get_position().y0, 6) for ax in fig.axes}
    xs = [ax.get_position().x0 for ax in fig.axes]
    assert len(ys) == 1 and xs == sorted(xs) and [ax.get_title() for ax in fig.axes] == ["T1", "T2", "T3", "T4", "T5"]


def test_the_hex_layout_draws_the_timepoints_menu_s_choice_and_has_no_stepper(page, qtbot):  # noqa: F811
    from review.features.nanowell import controller as nwc

    st = _open_hex(page, qtbot)
    fig = page.widgets.get("_nw_popout_fig")
    timepoints = [str(tp) for tp in st.data.timepoints()]
    assert len(timepoints) > 1 and page.widgets.get_or_none("_nw_popout_tp_prev") is None
    assert [ax.get_title() for ax in fig.axes] == timepoints              # All: one composite per timepoint
    nwc.set_popout_timepoints(page, [timepoints[-1]])
    assert len(fig.axes) == 1
    title = [t.get_text() for t in fig.texts if t.get_gid() == "nw-composite-title"][0]
    assert title.endswith(f" · {timepoints[-1]}")
    nwc.set_popout_no_timepoints(page)
    assert nwc.NO_TIMEPOINTS_SAID in [t.get_text() for ax in fig.axes for t in ax.texts] + [t.get_text() for t in fig.texts]
    assert st.popout_tiles == []


def test_go_to_nanowell_is_hidden_while_the_pop_out_shows_the_window(page, qtbot):  # noqa: F811
    """Go to nanowell names one nanowell; over the window's many, from several wells, it has nothing to go to."""
    st = _open_hex(page, qtbot)
    go_to = page.widgets.get("_nw_popout_go_to_btn")
    assert go_to.isHidden()
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(0)                    # Rows: still the window
    assert st.popout_layout == "rows" and go_to.isHidden()
    page.widgets.get("_nw_popout_source_cb").setCurrentIndex(0)                    # This nanowell
    assert st.popout_source == "selected" and not go_to.isHidden()


# ── the follow-ups: cells, zoom, the hover card, the controls hex has no use for, the stripe's key ──────────────────
def test_a_cell_lands_at_its_pixel_inside_its_tile():
    """A 10-pixel crop fills a tile 2 × TILE across, so pixel 4.5 (between the middle two) is the tile's centre and
    pixel 0's centre is half a pixel in from its left edge."""
    from review.features.nanowell._composite_render import TILE, _tile_cells

    tile = cc.CompositeTile(3.0, -2.0, ("A01", 0, 0), 1, 0)
    crop = Crop(np.zeros((10, 10)), "tile", "", np.array([[4.5, 4.5], [0.0, 9.0]]))
    xy = _tile_cells(tile, crop)
    assert xy[0].tolist() == pytest.approx([3.0, -2.0])
    assert xy[1].tolist() == pytest.approx([3.0 - TILE + 0.5 * 2 * TILE / 10, -2.0 - TILE + 9.5 * 2 * TILE / 10])


def test_cells_on_draws_each_tile_s_counted_cells_and_the_caption_counts_them():
    comp = cc.composite(_rows())
    crops = {t.source: {"T1": Crop(np.zeros((10, 10)), "tile", "", np.array([[1.0, 1.0], [5.0, 6.0]]))}
             for t in comp.tiles}
    crops[comp.tiles[0].source] = {"T1": Crop(np.zeros((10, 10)), "tile", "", None)}       # no cell table
    off, _comp = _draw(crops)
    on = Figure(figsize=(9.0, 6.0))
    render_composite(on, comp, crops, "T1", n_total=7, cells=True)
    assert _artists(off, "nw-composite-cells") == []
    (dots,) = [c for c in on.axes[0].collections if c.get_gid() == "nw-composite-cells"]
    assert len(dots.get_offsets()) == 2 * (len(comp.tiles) - 1)
    caption = _artists(on, "nw-composite-caption")[0].get_text()
    assert f"Dots: the {2 * (len(comp.tiles) - 1)} cells" in caption and "1 tile(s) of a well with no cell table" in caption


def test_each_patch_label_is_its_stripe_s_colour_and_the_caption_says_so():
    from matplotlib.colors import to_rgba

    fig, comp = _draw({})
    ax = fig.axes[0]
    stripes = {}
    for patch in ax.patches:
        if patch.get_gid() == "nw-composite-well":
            stripes.setdefault(round(patch.get_x() + patch.get_width() / 2, 6), to_rgba(patch.get_facecolor()))
    labels = {t.get_text(): to_rgba(t.get_color()) for t in ax.texts if t.get_gid() == "nw-composite-patch"}
    for tile in comp.tiles:
        well = comp.patches[tile.patch][0]
        assert stripes[round(tile.x, 6)] == labels[well]
    assert labels["A01"] != labels["B02"]
    assert "The stripe under a tile is its source well's colour" in _artists(fig, "nw-composite-caption")[0].get_text()


def test_the_cells_box_draws_the_cells_on_the_hex_composite(page, qtbot, monkeypatch):  # noqa: F811
    from tests.test_nanowell_crop_cells import CELLS
    from review.features.nanowell import controller as nwc

    def crops(_data, _metrics, site, **_kw):
        return {tp: Crop(np.zeros((10, 10)), "tile", "F0", np.array(xy)) for tp, xy in CELLS.items()}

    monkeypatch.setattr(nwc, "site_crops", crops)
    monkeypatch.setattr(nwc, "crops_for_sites", lambda d, m, sites, **kw: {tuple(s): crops(d, m, s) for s in sites})
    st = _open_hex(page, qtbot)
    fig = page.widgets.get("_nw_popout_fig")
    dots = lambda: sum(len(c.get_offsets()) for ax in fig.axes for c in ax.collections  # noqa: E731
                       if c.get_gid() == "nw-composite-cells")
    qtbot.waitUntil(lambda: bool([im for ax in fig.axes for im in ax.images]), timeout=10000)
    assert dots() == 0
    page.widgets.get("_nw_popout_cells_box").click()
    n_tiles = len(st.popout_tiles)
    assert st.crop_cells and dots() == n_tiles * sum(len(v) for v in CELLS.values())


def test_grid_and_channels_are_not_offered_for_the_hex_layout(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    grid, channels = page.widgets.get("_nw_popout_grid_btn"), page.widgets.get("_nw_popout_channels_btn")
    assert grid.isHidden() and channels.isHidden()
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(0)                    # Rows
    assert st.popout_layout == "rows" and not grid.isHidden() and not channels.isHidden()


def _wheel(canvas, point, notches=1):
    from PySide6.QtGui import QWheelEvent

    event = QWheelEvent(QPointF(point), QPointF(canvas.mapToGlobal(point)), QPoint(0, 0), QPoint(0, 120 * notches),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(canvas, event)


def test_the_wheel_zooms_every_panel_alike_and_the_zoom_outlasts_a_redraw(page, qtbot):  # noqa: F811
    from review.features.nanowell import controller as nwc

    st = _open_hex(page, qtbot)
    fig = page.widgets.get("_nw_popout_fig")
    assert len(fig.axes) > 1
    home = fig.axes[0].get_xlim()
    canvas, point = _tile_point(page, st, st.popout_tiles[0])
    _wheel(canvas, point)
    width = lambda ax: abs(ax.get_xlim()[1] - ax.get_xlim()[0])  # noqa: E731
    assert width(fig.axes[0]) < abs(home[1] - home[0]) * 0.9
    assert {round(width(ax), 6) for ax in fig.axes} == {round(width(fig.axes[0]), 6)}
    zoomed = fig.axes[0].get_xlim()
    nwc.draw_popout(page)                                                         # crops arriving redraw it
    assert page.widgets.get("_nw_popout_fig").axes[0].get_xlim() == pytest.approx(zoomed)
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(0)
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(1)                   # another layout: unzoomed again
    assert st.composite_view is None and page.widgets.get("_nw_popout_fig").axes[0].get_xlim() == pytest.approx(home)


def test_pointing_at_a_tile_shows_the_array_view_s_card_at_that_panel_s_timepoint(page, qtbot, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QToolTip

    from review.features.nanowell import array_hover_compute as ahc

    st = _open_hex(page, qtbot)
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, *_a: shown.append(text))
    fig = page.widgets.get("_nw_popout_fig")
    second = fig.axes[1]
    tile = st.popout_tiles[0]
    canvas = page.widgets.get("_nw_popout_canvas")
    px, py = second.transData.transform((tile[0], tile[1]))
    ratio = canvas.devicePixelRatioF()
    point = QPoint(int(round(px / ratio)), int(round(canvas.height() - py / ratio)))
    QApplication.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, QPointF(point), QPointF(canvas.mapToGlobal(point)),
                                               Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    well, q, r = tile[2]
    timepoint = second._aw_composite_tp
    rows = ahc.rows_by_address(st.metrics, well, timepoint)
    expected = ahc.hover_text(rows.get((q, r)), tile[2], timepoint=timepoint, channels=list(st.data.channels),
                              rule=st.rim_focus_rule())
    assert timepoint != fig.axes[0]._aw_composite_tp
    assert shown and shown[-1] == f"Tile {tile[3]} of the composite\n{expected}"
