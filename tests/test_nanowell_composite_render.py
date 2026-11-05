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


def _draw(crops, **kwargs):
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


def test_the_stepper_moves_the_composite_through_the_timepoints(page, qtbot):  # noqa: F811
    st = _open_hex(page, qtbot)
    title = lambda: [t.get_text() for t in page.widgets.get("_nw_popout_fig").texts  # noqa: E731
                     if t.get_gid() == "nw-composite-title"][0]
    timepoints = [str(tp) for tp in st.data.timepoints()]
    start = wnc.composite_timepoint(st, wnc.current(st)[1])
    assert title().endswith(f" · {start}")
    qtbot.mouseClick(page.widgets.get("_nw_popout_tp_prev"), Qt.LeftButton)
    expected = timepoints[max(timepoints.index(start) - 1, 0)]
    assert expected != start and title().endswith(f" · {expected}")
