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


def _stripes(ax):
    """``tile centre x -> stripe colour`` for one panel."""
    from matplotlib.colors import to_hex

    return {round(p.get_x() + p.get_width() / 2, 6): to_hex(p.get_facecolor())
            for p in ax.patches if p.get_gid() == "nw-composite-well"}


def _key(fig):
    keys = [leg for leg in fig.legends if leg.get_gid() == "nw-composite-key"]
    if not keys:
        return None
    return keys[0].get_title().get_text(), [t.get_text() for t in keys[0].get_texts()]


def test_by_well_each_well_s_tiles_share_a_stripe_and_the_key_names_the_wells():
    from matplotlib.colors import to_hex

    fig, comp = _draw({})
    stripes = _stripes(fig.axes[0])
    by_well = {}
    for tile in comp.tiles:
        by_well.setdefault(comp.patches[tile.patch][0], set()).add(stripes[round(tile.x, 6)])
    assert all(len(c) == 1 for c in by_well.values()) and by_well["A01"] != by_well["B02"]
    assert _key(fig) == ("Stripe: Source well", ["A01", "B02"])
    swatches = [to_hex(h.get_facecolor()) for h in fig.legends[0].legend_handles]
    assert swatches == [next(iter(by_well["A01"])), next(iter(by_well["B02"]))]
    assert "The stripe under a tile is its source well, keyed under the title" in _artists(
        fig, "nw-composite-caption")[0].get_text()


def test_given_inks_colour_the_stripes_and_empty_inks_draw_none_and_no_key():
    comp = cc.composite(_rows())
    fig = Figure(figsize=(9.0, 6.0))
    render_composite(fig, comp, {}, "T1", n_total=7, inks={"A01": "#ff0000", "B02": "#0000ff"},
                     key=(("U2OS", "#ff0000"), ("HeLa", "#0000ff")), key_title="Cell line")
    stripes = _stripes(fig.axes[0])
    for tile in comp.tiles:
        assert stripes[round(tile.x, 6)] == ("#ff0000" if tile.source[0] == "A01" else "#0000ff")
    assert _key(fig) == ("Stripe: Cell line", ["U2OS", "HeLa"])
    assert "its well's Cell line" in _artists(fig, "nw-composite-caption")[0].get_text()
    bare = Figure(figsize=(9.0, 6.0))
    render_composite(bare, comp, {}, "T1", n_total=7, inks={}, key=())
    assert _stripes(bare.axes[0]) == {} and _key(bare) is None
    assert "stripe" not in _artists(bare, "nw-composite-caption")[0].get_text()


def test_colouring_by_a_variable_takes_each_well_s_value_ink_from_variable_series():
    """The wells are assigned against the variable's order — A01 holds its second value — so a colouring that inked
    wells in patch order, or values in assignment order, would differ from the one every plot uses."""
    from types import SimpleNamespace

    from review.domain import variable_series as vs
    from tests.test_nanowell_page_variables import _Ctx

    ctx = _Ctx()
    st = SimpleNamespace(composite_colour="line")
    wells = ["A01", "B02", "C01"]
    inks, key, title = wnc.composite_colouring(ctx, st, wells)
    assert inks == vs.member_inks(ctx, "nanowell", "line", {w: [w] for w in wells})
    assert inks["B02"] == inks["C01"] != inks["A01"]
    assert [label for label, _c in key] == ["HeLa", "U2OS"] and title == "Cell line"
    assert dict(key)["U2OS"] == inks["A01"]
    st.composite_colour = "dose"                                   # C01 left unfilled: its own ink and name
    inks, key, _title = wnc.composite_colouring(ctx, st, wells)
    assert set(inks) == set(wells) and len(key) == 3
    st.composite_colour = "gone"                                    # a variable deleted since: by well, said so
    inks, key, title = wnc.composite_colouring(ctx, st, wells)
    assert title == "Source well" and [label for label, _c in key] == wells
    st.composite_colour = wnc.NO_COLOUR
    assert wnc.composite_colouring(ctx, st, wells) == ({}, (), "")
    labels = [label for label, _k in wnc.composite_colour_choices(ctx)]
    assert labels == ["Source well", "Cell line", "Dose", "None"]


def test_the_colour_choice_is_kept_with_the_page():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.composite_colour = "line"
    again = NanowellState()
    again.apply_config(st.to_config())
    assert again.composite_colour == "line"
    again.apply_config({"composite_colour": 3})
    assert again.composite_colour == ""


def test_colour_is_offered_only_for_the_hex_and_none_takes_the_stripes_away(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    combo = page.widgets.get("_nw_popout_colour_cb")
    fig = page.widgets.get("_nw_popout_fig")
    assert not combo.isHidden() and combo.currentData() == wnc.BY_WELL
    assert _stripes(fig.axes[0]) and _key(fig)[0] == "Stripe: Source well"
    combo.setCurrentIndex(combo.findData(wnc.NO_COLOUR))
    assert st.composite_colour == wnc.NO_COLOUR and _stripes(fig.axes[0]) == {} and _key(fig) is None
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(0)                    # Rows: no stripes to colour
    assert combo.isHidden()


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
    # The crops are cut a timepoint per pass: wait for every panel, not the first, or the count below is of
    # whichever timepoints happen to have arrived (CI's slower runner caught one of two).
    qtbot.waitUntil(lambda: fig.axes and all(len(ax.images) == len(st.popout_tiles) for ax in fig.axes),
                    timeout=10000)
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
    page.widgets.get("_nw_popout_hover_tip").setDelay(0)               # the card, not its wait
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
    qtbot.waitUntil(lambda: bool(shown), timeout=2000)
    assert shown[-1] == f"Tile {tile[3]} of the composite\n{expected}"


def test_labels_off_leaves_out_each_panel_s_timepoint_and_the_caption_but_not_the_wells_names():
    comp = cc.composite(_rows())
    on, off = Figure(figsize=(9.0, 6.0)), Figure(figsize=(9.0, 6.0))
    render_composite(on, comp, {}, ["T1", "T2"], n_total=7)
    render_composite(off, comp, {}, ["T1", "T2"], n_total=7, labels=False)
    assert [ax.get_title() for ax in on.axes] == ["T1", "T2"] and [ax.get_title() for ax in off.axes] == ["", ""]
    gids = lambda fig: [t.get_gid() for t in fig.texts]  # noqa: E731
    assert "nw-composite-caption" in gids(on) and "nw-composite-caption" not in gids(off)
    assert "nw-composite-title" in gids(off)
    assert sorted(t.get_text() for t in off.axes[0].texts if t.get_gid() == "nw-composite-patch") == ["A01", "B02"]


def test_the_labels_box_reaches_the_hex_composite_and_metrics_is_not_offered_there(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    fig = page.widgets.get("_nw_popout_fig")
    metrics = page.widgets.get("_nw_popout_metrics_box")
    assert metrics.isHidden()
    assert any(t.get_gid() == "nw-composite-caption" for t in fig.texts)
    page.widgets.get("_nw_popout_labels_box").click()
    assert st.popout_labels is False and not any(t.get_gid() == "nw-composite-caption" for t in fig.texts)
    assert all(ax.get_title() == "" for ax in fig.axes)
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(0)                    # Rows: Metrics is back
    assert not metrics.isHidden()


# ── cutting the window's crops a timepoint at a time ─────────────────────────────────────────────────────────────
def _recording_cutter(monkeypatch):
    """``crops_for_sites`` replaced by one that records the timepoints each pass asked for and cuts only those."""
    from review.features.nanowell import controller as nwc

    asked = []

    def cut(data, _metrics, sites, *, timepoints=None, **_kw):
        asked.append(list(timepoints) if timepoints is not None else None)        # None: every timepoint at once
        tps = timepoints if timepoints is not None else [str(tp) for tp in data.timepoints()]
        return {tuple(s): {tp: _crop(0, 100) for tp in tps} for s in sites}

    monkeypatch.setattr(nwc, "crops_for_sites", cut)
    return asked


def test_the_hex_cuts_only_the_timepoints_it_shows_one_pass_each_in_their_order(page, qtbot, monkeypatch):  # noqa: F811
    """A page of the composite is 91 nanowells. Cut over every timepoint in one pass, nothing showed until every tile
    of every timepoint was decoded; now the timepoints shown are cut one pass each, in the order shown, and a
    timepoint nobody shows is never cut."""
    from review.features.nanowell import controller as nwc

    asked = _recording_cutter(monkeypatch)
    st = _loaded(qtbot, page)
    timepoints = [str(tp) for tp in st.data.timepoints()]
    nwc.set_popout_timepoints(page, [timepoints[-1]])
    _drag(page, _first_panel(page), (-0.4, 1.5), (1.4, 4.5))
    wnc.open_window_popout(page)
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(1)
    fig = page.widgets.get("_nw_popout_fig")
    qtbot.waitUntil(lambda: bool([im for ax in fig.axes for im in ax.images]), timeout=10000)
    assert asked == [[timepoints[-1]]]                                  # only the timepoint shown
    nwc.set_popout_timepoints(page, [])                                 # All: the others, one pass each, in order
    qtbot.waitUntil(lambda: len(asked) == len(timepoints), timeout=10000)
    assert asked == [[timepoints[-1]]] + [[tp] for tp in timepoints if tp != timepoints[-1]]
    qtbot.waitUntil(lambda: all(ax.images for ax in fig.axes), timeout=10000)
    assert all(len(ax.images) == len(st.popout_tiles) for ax in fig.axes)


def test_a_timepoint_still_being_cut_says_so_and_one_cut_with_nothing_there_says_no_image():
    from types import SimpleNamespace

    from review.features.nanowell.crop_cache_compute import CropCache, TimepointCropCache

    st = SimpleNamespace(crops=CropCache(), window_crops=TimepointCropCache(), gate=None, metrics=None)
    a, b = ("A01", 0, 0), ("B02", 1, 0)
    st.window_crops.add({a: {"T1": _crop(0, 100)}, b: {}}, ["T1"])             # T1 cut; B02 has no row there
    crops = wnc._page_crops(st, [a, b], ["T1", "T2"])
    assert crops[a]["T1"].image is not None
    assert "T1" not in crops[b]                                                 # cut, nothing there: "no image"
    assert crops[a]["T2"].note == crops[b]["T2"].note == wnc.NOT_CUT_YET         # still to be cut
    fig = Figure(figsize=(9.0, 6.0))
    comp = cc.composite([WindowRow(a, 1, 1.0, {}), WindowRow(b, 2, 1.0, {})])
    render_composite(fig, comp, crops, ["T1", "T2"], n_total=2)
    caption = [t.get_text() for t in fig.texts if t.get_gid() == "nw-composite-caption"][0]
    assert "2 not cut yet" in caption and "1 no image at T1" in caption


def test_crops_for_sites_cuts_only_the_timepoints_asked_for(monkeypatch):
    import pandas as pd

    from review.features.nanowell import image_compute as ic

    cut_rows = []
    monkeypatch.setattr(ic, "_cut_well", lambda _d, well, records, *_a, **_k: cut_rows.extend(
        (well, r["timepoint"]) for r in records))
    metrics = pd.DataFrame({"well": ["A01"] * 3, "nw_q": [0] * 3, "nw_r": [0] * 3,
                            "timepoint": ["T0", "T1", "T2"], "tp_order": [0, 1, 2]})
    ic.crops_for_sites(None, metrics, [("A01", 0, 0)], timepoints=["T1"])
    assert cut_rows == [("A01", "T1")]
    cut_rows.clear()
    ic.crops_for_sites(None, metrics, [("A01", 0, 0)])
    assert cut_rows == [("A01", "T0"), ("A01", "T1"), ("A01", "T2")]


# ── a trackpad's scroll, panning by a drag, the cells' outlines ──────────────────────────────────────────────────
def test_a_trackpad_swipe_zooms_one_notch_not_one_notch_per_pixel(page, qtbot):  # noqa: F811
    """A Mac trackpad sends a pixel delta beside the angle delta, and matplotlib hands the pixels on as the step: a
    20-pixel swipe event zoomed by 0.8 ** 20, about 87 times at once. One notch's angle must zoom one notch."""
    from PySide6.QtGui import QWheelEvent

    from review.features.nanowell.zoom_compute import WHEEL_STEP

    st = _open_hex(page, qtbot)
    fig = page.widgets.get("_nw_popout_fig")
    home = fig.axes[0].get_xlim()
    canvas, point = _tile_point(page, st, st.popout_tiles[0])
    event = QWheelEvent(QPointF(point), QPointF(canvas.mapToGlobal(point)), QPoint(0, 20), QPoint(0, 120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(canvas, event)
    width = abs(fig.axes[0].get_xlim()[1] - fig.axes[0].get_xlim()[0])
    assert width == pytest.approx(abs(home[1] - home[0]) * WHEEL_STEP, rel=1e-6)


def test_notches_reads_the_angle_and_falls_back_to_the_step():
    from types import SimpleNamespace

    from review.features.nanowell.zoom_compute import notches

    angle = lambda y: SimpleNamespace(angleDelta=lambda: SimpleNamespace(y=lambda: y))  # noqa: E731
    assert notches(SimpleNamespace(step=20.0, guiEvent=angle(60))) == 0.5
    assert notches(SimpleNamespace(step=-1.0, guiEvent=angle(-120))) == -1.0
    assert notches(SimpleNamespace(step=2.0, guiEvent=None)) == 2.0


def _drag_canvas(canvas, start, end):
    from PySide6.QtTest import QTest

    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
    middle = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    for at in (middle, end):
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, QPointF(at), QPointF(canvas.mapToGlobal(at)),
                                                   Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, end)


def test_a_drag_pans_every_panel_and_selects_nothing_and_a_click_still_selects(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    fig = page.widgets.get("_nw_popout_fig")
    selected = list(st.selected_sites)
    home_x, home_y = fig.axes[0].get_xlim(), fig.axes[0].get_ylim()
    canvas, point = _tile_point(page, st, st.popout_tiles[0])
    _drag_canvas(canvas, point, QPoint(point.x() + 40, point.y() + 30))           # right and down on screen
    (x0, x1), (y0, y1) = fig.axes[0].get_xlim(), fig.axes[0].get_ylim()
    assert x0 < home_x[0] and x1 < home_x[1]                 # the picture moved right: the view went left
    assert y0 < home_y[0] and y1 < home_y[1]                 # moved down (y runs down): the view went up
    assert x1 - x0 == pytest.approx(home_x[1] - home_x[0])   # panned, not zoomed
    assert {ax.get_xlim() for ax in fig.axes} == {fig.axes[0].get_xlim()}
    assert st.selected_sites == selected and st.composite_view is not None
    from review.features.nanowell import controller as nwc
    nwc.draw_popout(page)                                     # crops arriving keep the pan
    assert page.widgets.get("_nw_popout_fig").axes[0].get_xlim() == pytest.approx((x0, x1))
    tile = st.popout_tiles[0]
    canvas, point = _tile_point(page, st, tile)
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, point)
    assert st.selected_sites == [[*tile[2]]]


def test_cells_are_drawn_as_their_traced_outlines_and_a_cell_without_one_as_a_dot():
    from matplotlib.collections import LineCollection

    from review.features.nanowell._composite_render import TILE

    comp = cc.composite(_rows())
    square = [np.array([[1.0, 1.0], [3.0, 1.0], [3.0, 3.0], [1.0, 3.0], [1.0, 1.0]])]
    crop = Crop(np.zeros((10, 10)), "tile", "", np.array([[2.0, 2.0], [7.0, 7.0]]), outlines=[square, None])
    crops = {t.source: {"T1": crop} for t in comp.tiles}
    fig = Figure(figsize=(9.0, 6.0))
    render_composite(fig, comp, crops, "T1", n_total=7, cells=True)
    ax = fig.axes[0]
    fills = [c for c in ax.collections if c.get_gid() == "nw-composite-outlines"]
    assert len(fills) == 1, "the cells' outlines were not drawn"
    fill = fills[0]
    assert isinstance(fill, LineCollection) and len(fill.get_segments()) == len(comp.tiles)
    tile = comp.tiles[0]
    first = fill.get_segments()[0]
    assert first[0].tolist() == pytest.approx([tile.x - TILE + 1.5 * 2 * TILE / 10, tile.y - TILE + 1.5 * 2 * TILE / 10])
    (dots,) = [c for c in ax.collections if c.get_gid() == "nw-composite-cells"]
    assert len(dots.get_offsets()) == len(comp.tiles)                         # only the cell with no outline
    caption = _artists(fig, "nw-composite-caption")[0].get_text()
    assert f"Outlines and dots: the {2 * len(comp.tiles)} cells" in caption
    assert f"({len(comp.tiles)} a dot: the run kept no outline of it)" in caption


# ── Sort ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_sort_places_the_tiles_by_the_metric_from_the_top_left_in_the_order_chosen(page, qtbot):  # noqa: F811
    """Through the real combos: sorted by cell count at the first timepoint shown, each well's tiles read from its
    patch's top left in that order — the counts are read back from the page's own table, not the test's. The window
    takes nearly every nanowell, so a well holds several whose counts at the first timepoint run against their rank
    (A01's ranks by its latest count are q1, q2, q0, with first counts 2, 0, 1): a sort that kept the rank order fails."""
    st = _loaded(qtbot, page)
    _drag(page, _first_panel(page), (-0.4, -0.5), (1.4, 4.5))
    wnc.open_window_popout(page)
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(1)
    qtbot.waitUntil(lambda: bool(st.popout_tiles), timeout=10000)
    timepoint = str(st.data.timepoints()[0])
    sort, order = page.widgets.get("_nw_popout_sort_cb"), page.widgets.get("_nw_popout_order_cb")
    assert not sort.isHidden() and sort.currentData() == wnc.BY_RANK
    sort.setCurrentIndex(sort.findData("n"))
    assert st.composite_sort == "n"
    rows = st.metrics[st.metrics["timepoint"].astype(str) == timepoint]
    counts = {(str(w), int(q), int(r)): float(v) for w, q, r, v in zip(rows["well"], rows["nw_q"], rows["nw_r"],
                                                                       rows["n"])}

    def reading(well):
        mine = [t for t in st.popout_tiles if t[2][0] == well]
        return [t[2] for t in sorted(mine, key=lambda t: (round(t[1], 9), t[0]))]

    wells = {t[2][0] for t in st.popout_tiles}
    assert max(len(reading(w)) for w in wells) >= 3
    by_rank = sorted((t for t in st.popout_tiles if t[2][0] == "A01"), key=lambda t: t[3])
    assert [counts[t[2]] for t in by_rank] != sorted((counts[t[2]] for t in by_rank), reverse=True)
    for well in wells:
        got = [counts[s] for s in reading(well)]
        assert got == sorted(got, reverse=True)
    order.setCurrentIndex(order.findData(False))
    assert st.composite_desc is False
    for well in wells:
        got = [counts[s] for s in reading(well)]
        assert got == sorted(got)
    title = [t.get_text() for t in page.widgets.get("_nw_popout_fig").texts if t.get_gid() == "nw-composite-title"][0]
    assert f"placed by Cell count at {timepoint}, smallest first" in title
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(0)                    # Rows: not offered
    assert sort.isHidden() and order.isHidden()


def test_a_metric_that_cannot_be_read_places_by_rank_and_says_why():
    from types import SimpleNamespace

    import pandas as pd

    metrics = pd.DataFrame({"well": ["A01"], "nw_q": [0.0], "nw_r": [0.0], "timepoint": ["T1"], "tp_order": [0],
                            "n": [3.0]})
    st = SimpleNamespace(composite_sort="nope", metrics=metrics, data=SimpleNamespace(channels=[]),
                         composite_desc=True)
    assert wnc.composite_order(None, st, [("A01", 0, 0)], "T1") == (None, "rank")
    labels = [label for label, _k in wnc.composite_sort_choices()]
    assert labels[0] == "Window rank" and "Cell count" in labels


def test_the_dashed_frame_stays_on_each_panel_s_edge_when_zoomed_and_panned(page, qtbot):  # noqa: F811
    """Reported: on zoom the dashed edge outlining each timepoint's composite disappeared — it was drawn around the
    data, so zooming in moved it out of view. It is the panel's own edge now, whatever the zoom or pan."""
    st = _open_hex(page, qtbot)
    fig = page.widgets.get("_nw_popout_fig")
    canvas, point = _tile_point(page, st, st.popout_tiles[0])

    def frames_on_edge():
        for ax in fig.axes:
            (frame,) = [p for p in ax.patches if p.get_gid() == "nw-composite-frame"]
            corners = frame.get_window_extent()
            box = ax.get_window_extent()
            assert corners.bounds == pytest.approx(box.bounds, abs=1.0)
        return True

    assert frames_on_edge()
    for _ in range(4):
        _wheel(canvas, point)
    assert abs(fig.axes[0].get_xlim()[1] - fig.axes[0].get_xlim()[0]) < abs(
        fig.axes[0]._aw_composite_home[0][1] - fig.axes[0]._aw_composite_home[0][0]) * 0.5
    assert frames_on_edge()
    _drag_canvas(canvas, point, QPoint(point.x() + 30, point.y() + 20))
    assert frames_on_edge()


# ── Corner: a metric in each tile's corner ───────────────────────────────────────────────────────────────────────
def test_corner_writes_each_tile_s_value_at_its_panel_s_timepoint():
    from review.features.nanowell._composite_render import TILE, corner_text

    comp = cc.composite(_rows())
    sites = [t.source for t in comp.tiles]
    corner = {"T1": {s: float(i) for i, s in enumerate(sites)}, "T2": {s: 2.5 * i for i, s in enumerate(sites[:-1])}}
    fig = Figure(figsize=(9.0, 6.0))
    render_composite(fig, comp, {}, ["T1", "T2"], n_total=7, corner=corner, corner_said="Corner: each tile's n.")
    for ax, tp in zip(fig.axes, ("T1", "T2")):
        written = {}
        for text in (t for t in ax.texts if t.get_gid() == "nw-composite-value"):
            x, y = text.get_position()                                       # each tile's own top-left corner
            tile = min(comp.tiles, key=lambda t: abs(t.x - TILE - x) + abs(t.y - TILE - y))
            assert abs(tile.x - TILE - x) < 0.1 and abs(tile.y - TILE - y) < 0.1
            written[tile.source] = text.get_text()
        assert written == {s: corner_text(corner[tp].get(s)) for s in sites}
    assert corner_text(3.0) == "3" and corner_text(2.5) == "2.5" and corner_text(1.23456) == "1.23"
    assert corner_text(float("nan")) == corner_text(None) == "—"
    assert "Corner: each tile's n." in _artists(fig, "nw-composite-caption")[0].get_text()
    bare = Figure(figsize=(9.0, 6.0))
    render_composite(bare, comp, {}, "T1", n_total=7)
    assert not [t for t in bare.axes[0].texts if t.get_gid() == "nw-composite-value"]


def test_the_corner_combo_writes_the_page_s_values_at_each_panel_s_timepoint(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    combo = page.widgets.get("_nw_popout_label_cb")
    assert not combo.isHidden() and combo.currentData() == wnc.NO_LABEL
    combo.setCurrentIndex(combo.findData("n"))
    assert st.composite_label == "n"
    fig = page.widgets.get("_nw_popout_fig")
    assert len(fig.axes) > 1
    for ax in fig.axes:
        tp = ax._aw_composite_tp
        rows = st.metrics[st.metrics["timepoint"].astype(str) == tp]
        counts = {(str(w), int(q), int(r)): v for w, q, r, v in zip(rows["well"], rows["nw_q"], rows["nw_r"], rows["n"])}
        texts = sorted(t.get_text() for t in ax.texts if t.get_gid() == "nw-composite-value")
        expected = sorted(f"{counts[t[2]]:.0f}" for t in st.popout_tiles)
        assert texts == expected
    caption = [t.get_text() for t in fig.texts if t.get_gid() == "nw-composite-caption"][0]
    assert "Corner: each tile's Cell count at its panel's timepoint." in caption
    combo.setCurrentIndex(combo.findData(wnc.NO_LABEL))
    assert not [t for ax in fig.axes for t in ax.texts if t.get_gid() == "nw-composite-value"]


def test_the_tile_s_card_waits_for_the_pointer_to_rest(page, qtbot, monkeypatch):  # noqa: F811
    """Asked for: a one-second delay before the hover card, on the real pop-out canvas at its real delay."""
    from PySide6.QtWidgets import QToolTip

    import time

    st = _open_hex(page, qtbot)
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, *_a: shown.append((time.monotonic(), text)))
    canvas, point = _tile_point(page, st, st.popout_tiles[0])
    # Timed, not slept: on a busy runner a "half-second" wait can itself run past the second, so the test measures
    # when the card showed against when the pointer arrived.
    arrived = time.monotonic()
    QApplication.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, QPointF(point), QPointF(canvas.mapToGlobal(point)),
                                               Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    assert shown == []                                               # not on the move itself
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    at, text = shown[-1]
    assert at - arrived >= 0.9, at - arrived                         # it waited the second (Qt's timer: ±5%)
    assert text.startswith(f"Tile {st.popout_tiles[0][3]} of the composite")


# ── the pop-out's chrome: Reset zoom, and hiding the control rows ────────────────────────────────────────────────
def test_reset_zoom_returns_the_whole_composite_and_is_greyed_while_it_is_whole(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    reset = page.widgets.get("_nw_popout_reset_zoom_btn")
    fig = page.widgets.get("_nw_popout_fig")
    home = fig.axes[0].get_xlim()
    assert not reset.isHidden() and not reset.isEnabled()                     # whole: nothing to reset
    canvas, point = _tile_point(page, st, st.popout_tiles[0])
    _wheel(canvas, point)
    assert st.composite_view is not None and reset.isEnabled()
    assert abs(fig.axes[0].get_xlim()[1] - fig.axes[0].get_xlim()[0]) < abs(home[1] - home[0]) * 0.9
    reset.click()
    assert st.composite_view is None and not reset.isEnabled()
    assert page.widgets.get("_nw_popout_fig").axes[0].get_xlim() == pytest.approx(home)
    _drag_canvas(canvas, point, QPoint(point.x() + 30, point.y() + 20))       # a pan counts as moved too
    assert reset.isEnabled()
    reset.click()
    assert page.widgets.get("_nw_popout_fig").axes[0].get_xlim() == pytest.approx(home)
    page.widgets.get("_nw_popout_layout_cb").setCurrentIndex(0)               # Rows: no zoom to reset
    assert reset.isHidden()


def test_hide_controls_folds_every_control_row_and_show_controls_brings_them_back(page, qtbot):
    from PySide6.QtWidgets import QApplication

    _open_hex(page, qtbot)
    window = page.widgets.get("_nw_popout")
    window.show()
    toggle = page.widgets.get("_nw_popout_controls_btn")
    rows = ("_nw_popout_image_channel_cb", "_nw_popout_tps_btn", "_nw_popout_layout_cb", "_nw_popout_sort_cb")
    card = page.widgets.get("_nw_popout_card")
    assert toggle.text() == "Hide controls ▾" and all(page.widgets.get(n).isVisibleTo(window) for n in rows)
    shown_height = card.height()
    toggle.click()
    QApplication.processEvents()
    assert toggle.text() == "Show controls ▸" and not any(page.widgets.get(n).isVisibleTo(window) for n in rows)
    assert toggle.isVisibleTo(window) and page.widgets.get("_nw_popout_reset_zoom_btn").isVisibleTo(window)
    assert card.height() > shown_height                                         # the figure took the room
    toggle.click()
    QApplication.processEvents()
    assert all(page.widgets.get(n).isVisibleTo(window) for n in rows) and card.height() == shown_height
