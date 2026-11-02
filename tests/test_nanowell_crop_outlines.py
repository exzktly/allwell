"""Each counted cell's outline drawn over the Nanowells crops (``cell_outline_compute``, ``image_compute._cells_in``).

The run keeps every counted cell's pixels beside its cell table (``nanowells/<well>_outlines.npz``,
``pipeline.nanowell_outlines``). Each case here writes that file for a crop the tests already know the pixel
placement of (``test_nanowell_crop_cells``) and reads the outline back in the crop's pixels: a cell of known pixels
must trace exactly round those pixels, on a tile crop clipped at the tile's edge (whose corner is the edge, not the
nanowell less the half-width) and on a montage crop (whose scale is the montage's). A cell the file has no outline of
is a dot, and the caption says why.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.nanowell_outlines import outlines_path, save_outlines
from review.features.nanowell import image_compute as ic


def _cells(data, tmp_path, cells):
    """A cell table for A01 at t0 of ``(well_x_um, well_y_um, nucleus_id)``, every cell counted in nanowell (0, 0)
    on tile F0."""
    path = tmp_path / "cells_A01.csv"
    pd.DataFrame([{"well": "A01", "timepoint": "t0", "fov": "F0", "nucleus_id": label, "nw_q": 0, "nw_r": 0,
                   "well_x_um": x, "well_y_um": y} for x, y, label in cells]).to_csv(path, index=False)
    data.cell_tables = {"A01": path}


def _outline(data, rows):
    save_outlines(outlines_path(data.out_dir / "nanowells", "A01"), rows)


def _box(lines):
    """``(left, top, right, bottom)`` of a cell's traced boundaries."""
    points = np.concatenate(lines)
    return tuple(float(v) for v in (points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()))


def _tile_crop(tmp_path, cells, rows, **site):
    from tests.test_nanowell_crop_cells import _tile_data

    data, metrics = _tile_data(tmp_path, [])
    for key, value in site.items():
        metrics[key] = value
    _cells(data, tmp_path, cells)
    _outline(data, rows)
    return ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]


def test_a_tile_crop_traces_a_cell_round_exactly_its_own_pixels(tmp_path):
    # A 3×2-pixel cell at tile pixels x 196–198, y 76–77 (0.5 µm/px, tile at the origin): its centre is
    # (98.75, 38.5) µm. The crop's corner is tile pixel (191, 71), so the cell is crop pixels x 5–7, y 5–6 and its
    # boundary runs half a pixel outside their centres.
    mask = np.ones((2, 3), dtype=bool)
    crop = _tile_crop(tmp_path, [(98.75, 38.5, 7)], [("t0", "F0", 7, 196 * 0.5, 76 * 0.5, 0.5, mask)])
    assert crop.source == "tile" and crop.cells.tolist() == [[6.0, 5.5]]
    assert _box(crop.outlines[0]) == (4.5, 4.5, 7.5, 6.5)


def test_an_l_shaped_cell_keeps_its_shape_in_the_crop(tmp_path):
    # Stored transposed or flipped, an L is another L: its foot, at the bottom right, is what is checked.
    mask = np.array([[1, 0, 0], [1, 0, 0], [1, 1, 1]], dtype=bool)
    crop = _tile_crop(tmp_path, [(98.75, 38.75, 3)], [("t0", "F0", 3, 98.0, 38.0, 0.5, mask)])
    from matplotlib.path import Path

    outline = Path(crop.outlines[0][0])
    # Tile pixel (196, 76) is crop pixel (5, 5); the foot's far end is crop pixel (7, 7), the empty corner (7, 5).
    assert outline.contains_point((5.0, 5.0)) and outline.contains_point((7.0, 7.0))
    assert not outline.contains_point((7.0, 5.0))


def test_a_crop_clipped_at_the_tile_s_edge_traces_from_the_edge(tmp_path):
    # The crop starts at the tile's corner, so tile pixels (6, 4)–(7, 4) are crop pixels (6, 4)–(7, 4).
    mask = np.ones((1, 2), dtype=bool)
    crop = _tile_crop(tmp_path, [(3.5, 2.25, 1)], [("t0", "F0", 1, 3.0, 2.0, 0.5, mask)], well_x_um=2.0,
                      well_y_um=1.0)
    assert crop.source == "tile" and _box(crop.outlines[0]) == (5.5, 3.5, 7.5, 4.5)


def test_a_montage_crop_traces_at_the_montage_s_scale(tmp_path):
    from tests.test_nanowell_page_views import _crop_data

    data, metrics = _crop_data(tmp_path)                              # no input archive: cut from the montage
    # Four 0.5 µm tile pixels square from (100, 40) µm: 2 µm, one montage pixel. The crop's corner is montage pixel
    # (47, 17), so the cell is the whole of crop pixel (3, 3), and its boundary that pixel's edges.
    _cells(data, tmp_path, [(101.0, 41.0, 5)])
    _outline(data, [("t0", "F0", 5, 100.0, 40.0, 0.5, np.ones((4, 4), dtype=bool))])
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert crop.source == "montage"
    assert _box(crop.outlines[0]) == pytest.approx((2.5, 2.5, 3.5, 3.5))


def test_a_cell_the_run_kept_no_outline_of_is_a_dot_and_a_well_with_no_file_has_none(tmp_path):
    mask = np.ones((2, 3), dtype=bool)
    crop = _tile_crop(tmp_path, [(98.75, 38.5, 7), (100.0, 40.0, 8)], [("t0", "F0", 7, 98.0, 38.0, 0.5, mask)])
    assert crop.outlines[0] is not None and crop.outlines[1] is None
    (tmp_path / "out" / "nanowells" / "A01_outlines.npz").unlink()
    from review.features.nanowell.cell_outline_compute import _read

    _read.cache_clear()
    from tests.test_nanowell_crop_cells import _tile_data

    (tmp_path / "again").mkdir()
    data, metrics = _tile_data(tmp_path / "again", [])
    _cells(data, tmp_path / "again", [(98.75, 38.5, 7)])
    assert ic.site_crops(data, metrics, ("A01", 0, 0))["t0"].outlines is None


def _drawn(crop):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from review.features.nanowell import renderer

    fig = Figure()
    ax = fig.add_subplot(111)
    ax.imshow(crop.image)
    tally: dict = {}
    renderer._draw_crop_cells(ax, crop, tally)
    gids = {c.get_gid(): c for c in ax.collections}
    return gids, renderer._cells_clause(tally)


def test_the_crop_draws_outlines_and_a_dot_only_for_the_cell_without_one(tmp_path):
    mask = np.ones((2, 3), dtype=bool)
    crop = _tile_crop(tmp_path, [(98.75, 38.5, 7), (100.0, 40.0, 8)], [("t0", "F0", 7, 98.0, 38.0, 0.5, mask)])
    gids, caption = _drawn(crop)
    assert len(gids["nw-crop-outlines"].get_segments()) == 1
    assert gids["nw-crop-cells"].get_offsets().tolist() == [crop.cells[1].tolist()]
    assert caption == ("outlines and dots: the 2 cells the run counted in this nanowell; 1 of them dots, the run "
                       "kept no outline of them")


def test_gating_leaves_out_a_cell_s_outline_with_its_dot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from review.features.nanowell import controller

    mask = np.ones((2, 3), dtype=bool)
    crop = _tile_crop(tmp_path, [(100.0, 40.0, 8), (98.75, 38.5, 7)], [("t0", "F0", 7, 98.0, 38.0, 0.5, mask)])
    monkeypatch.setattr(controller.cg, "hidden_cells", lambda rows, *_a: (np.array([True, False]), 1, 0))
    monkeypatch.setattr(controller, "field_sites", lambda *_a: None)
    st = SimpleNamespace(gate=object(), metrics=pd.DataFrame({"well": ["A01"], "timepoint": ["t0"]}))
    gated = controller.gated_crops(st, ("A01", 0, 0), {"t0": crop})["t0"]
    assert gated.cells.tolist() == [crop.cells[1].tolist()]
    assert len(gated.outlines) == 1 and _box(gated.outlines[0]) == _box(crop.outlines[1])
