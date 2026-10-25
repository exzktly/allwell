"""The counted cells drawn on every Nanowells crop, as the Array view's Cells layer draws them on the array.

Asked for: the Array view could draw the run's cell detections, and no other crop could. Every crop the page shows
now can — the site detail's strip, the pop-out and the Extremes montage — behind one **Cells** switch offered
beside the strip and on the Extremes view.

The cells are placed in the crop from the well frame: a crop records where its top-left pixel is, so a cell's
pixel is ``(well µm − corner µm) / µm per pixel``. The cases here are built so a wrong corner lands a cell on a
different pixel — a crop clipped at the tile's edge, where the corner is the tile's edge rather than the nanowell's
centre less the crop's half-width, and a montage crop, whose scale is the montage's. The page tests drive the real
checkboxes and read the dots back from the figures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.features.nanowell import image_compute as ic


def _with_cells(data, tmp_path, cells):
    """*data* with a cell table for A01 holding *cells* (``(well_x_um, well_y_um)`` at timepoint t0)."""
    path = tmp_path / "cells_A01.csv"
    pd.DataFrame([{"well": "A01", "timepoint": "t0", "nw_q": 0, "nw_r": 0, "well_x_um": x, "well_y_um": y}
                  for x, y in cells]).to_csv(path, index=False)
    data.cell_tables = {"A01": path}
    return data


def _tile_data(tmp_path, cells, **site):
    from tests.test_nanowell_page_views import _crop_data, _tif

    data, metrics = _crop_data(tmp_path, members=[("exp_phase_A01_F0_t0.tif",
                                                   _tif(np.zeros((120, 260), dtype=np.uint16)))])
    for key, value in site.items():
        metrics[key] = value
    return _with_cells(data, tmp_path, cells), metrics


def test_a_tile_crop_places_each_cell_at_its_pixel_and_leaves_out_the_cells_outside_it(tmp_path):
    # The nanowell at (100, 40) µm is tile pixel (200, 80) at 0.5 µm/px; ±8.4 px puts the crop's corner at (191, 71).
    data, metrics = _tile_data(tmp_path, [(100.0, 40.0), (98.0, 41.0), (150.0, 40.0)])
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert crop.source == "tile"
    assert crop.cells.tolist() == [[9.0, 9.0], [5.0, 11.0]]          # (150, 40) µm is off the crop


def test_a_crop_clipped_at_the_tile_s_edge_measures_from_the_edge(tmp_path):
    # At (2, 1) µm the nanowell is pixel (4, 2): the crop would start at (−5, −7) and is clipped to the tile's
    # corner, so a cell at (3, 2) µm — pixel (6, 4) of the tile — is pixel (6, 4) of the crop, not (11, 11).
    data, metrics = _tile_data(tmp_path, [(3.0, 2.0)], well_x_um=2.0, well_y_um=1.0)
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert crop.source == "tile" and crop.cells.tolist() == [[6.0, 4.0]]


def test_a_montage_crop_places_its_cells_at_the_montage_s_scale(tmp_path):
    from tests.test_nanowell_page_views import _crop_data

    data, metrics = _crop_data(tmp_path)                              # no input archive: cut from the montage
    data = _with_cells(data, tmp_path, [(100.0, 40.0), (101.0, 42.0)])
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    # 2 µm/px from (0, 0); the nanowell's ±4.2 µm puts the crop's corner at montage pixel (47, 17).
    assert crop.source == "montage" and crop.cells.tolist() == [[3.0, 3.0], [3.5, 4.0]]


def test_a_well_without_a_cell_table_says_none_could_be_read_not_that_it_has_none(tmp_path):
    from tests.test_nanowell_page_views import _crop_data

    data, metrics = _crop_data(tmp_path)
    data.cell_tables = {}
    assert ic.site_crops(data, metrics, ("A01", 0, 0))["t0"].cells is None
    data = _with_cells(data, tmp_path, [(500.0, 500.0)])               # a table, but no cell in this crop
    assert ic.site_crops(data, metrics, ("A01", 0, 0))["t0"].cells.shape == (0, 2)


# ── the page ─────────────────────────────────────────────────────────────────
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.image_compute import Crop  # noqa: E402
from tests.test_nanowell_page import _click_dot, _loaded, page  # noqa: E402,F401

#: Per timepoint, the cells each crop holds, in crop pixels — a different number at each, so a strip drawing one
#: timepoint's cells on every panel cannot pass.
CELLS = {"t_c": [[2.0, 3.0]], "t_a": [[1.0, 1.0], [5.0, 6.0], [7.0, 2.0]]}


@pytest.fixture
def cell_crops(monkeypatch):
    def crops(_data, _metrics, site, **_kw):
        return {tp: Crop(np.zeros((10, 10)), "tile", "F0, full resolution", np.array(xy)) for tp, xy in CELLS.items()}

    def many(data, metrics, sites, **_kw):
        return {tuple(s): crops(data, metrics, s) for s in sites}

    monkeypatch.setattr(nwc, "site_crops", crops)
    monkeypatch.setattr(nwc, "crops_for_sites", many)


def _dots(ctx, key):
    """The cell dots on figure *key*, per panel, as the panels are drawn."""
    return [c.get_offsets().tolist() for ax in ctx.widgets.get(key).axes for c in ax.collections
            if c.get_gid() == "nw-crop-cells"]


def _press(qtbot, ctx, name):
    box = ctx.widgets.get(name)
    qtbot.waitUntil(lambda: box.isVisible() and box.width() > 0, timeout=5000)
    qtbot.mouseClick(box, Qt.LeftButton)


def test_the_cells_box_draws_each_timepoint_s_cells_on_the_strip_and_the_pop_out(page, qtbot, cell_crops):  # noqa: F811
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    qtbot.waitUntil(lambda: bool(page.widgets.get("_nw_strip_fig").axes), timeout=10000)
    assert _dots(page, "_nw_strip_fig") == []                         # off until asked for
    _press(qtbot, page, "_nw_strip_cells_box")
    assert page.state.feature_slice("nanowell").crop_cells is True
    assert _dots(page, "_nw_strip_fig") == [CELLS["t_c"], CELLS["t_a"]]
    caption = [t.get_text() for t in page.widgets.get("_nw_strip_fig").texts if t.get_gid() == "nw-crop-caption"]
    assert caption[0].endswith("; dots: the 4 cells the run counted in view")
    _press(qtbot, page, "_nw_popout_btn")
    qtbot.addWidget(page.widgets.get("_nw_popout"))
    assert _dots(page, "_nw_popout_fig") == [CELLS["t_c"], CELLS["t_a"]]
    _press(qtbot, page, "_nw_strip_cells_box")                        # and off again, everywhere
    assert _dots(page, "_nw_strip_fig") == [] and _dots(page, "_nw_popout_fig") == []


def test_the_extremes_view_draws_the_cells_and_its_box_is_the_same_setting(page, qtbot, cell_crops):  # noqa: F811
    _loaded(qtbot, page)
    nwc.set_view(page, "extremes")
    assert _dots(page, "_nw_fig") == []
    _press(qtbot, page, "_nw_extremes_cells_box")
    qtbot.waitUntil(lambda: bool(_dots(page, "_nw_fig")), timeout=10000)   # the crops are cut on a worker
    dots = _dots(page, "_nw_fig")
    assert dots and set(map(str, dots)) == {str(CELLS["t_c"]), str(CELLS["t_a"])}
    assert page.widgets.get("_nw_strip_cells_box").isChecked()          # one setting, shown in both places
    assert page.state.feature_slice("nanowell").to_config()["crop_cells"] is True


def test_a_crop_of_a_well_without_a_cell_table_is_named_in_the_caption(page, qtbot, monkeypatch):  # noqa: F811
    monkeypatch.setattr(nwc, "site_crops", lambda _d, _m, _s, **_kw: {"t_c": Crop(np.zeros((10, 10)), "tile", "F0", None)})
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    _press(qtbot, page, "_nw_strip_cells_box")
    caption = [t.get_text() for t in page.widgets.get("_nw_strip_fig").texts if t.get_gid() == "nw-crop-caption"]
    assert caption[0].endswith("; 1 crop(s) of a well without a cell table, so no cells drawn there")
    assert _dots(page, "_nw_strip_fig") == []


def test_the_setting_is_restored_with_the_page_s_settings():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.apply_config({"crop_cells": True})
    assert st.crop_cells is True
    st.apply_config({"crop_cells": "yes"})                            # only a real True turns it on
    assert st.crop_cells is False
