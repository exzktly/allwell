"""Each cell on a Nanowells crop in its Cell Gating size class's colour (``controller.gated_crops``,
``renderer._draw_crop_cells``).

The crops outline every counted cell, in one colour. With size classes set on Home ▸ Cell Gating, a cell in a class
is outlined (or, without an outline, dotted) in that class's colour, a cell in neither keeps the Cells colour, and
the caption names each colour and how many cells it marks. The class is read from the cell table's own area column
— the one the debris gate and the per-nanowell class counts read — and under the rule the page's counts were made
with (``GateTally.sizes``), so an outline's colour and the nanowell's count beside it cannot disagree.

The cell table lists the larger cell first and the first class is the smaller one, so a class taken from the row
order rather than the area would swap the colours (CLAUDE.md §7.8).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
from matplotlib.colors import to_hex

from review.domain.size_classes import SizeClassRule
from review.features.nanowell import cell_gate_compute as cg
from review.features.nanowell import image_compute as ic

RULE = SizeClassRule.of(("T cells", None, 100), ("DCs", 300, None))


def _crop(tmp_path, cells):
    """A tile crop of nanowell (0, 0) holding *cells*: ``(well_x_um, well_y_um, nucleus_id, area_px)``."""
    from tests.test_nanowell_crop_cells import _tile_data

    data, metrics = _tile_data(tmp_path, [])
    path = tmp_path / "cells_A01.csv"
    pd.DataFrame([{"well": "A01", "timepoint": "t0", "fov": "F0", "nucleus_id": label, "nw_q": 0, "nw_r": 0,
                   "well_x_um": x, "well_y_um": y, "area_px": area} for x, y, label, area in cells]).to_csv(path,
                                                                                                    index=False)
    data.cell_tables = {"A01": path}
    return ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]


def _gated(crop, monkeypatch, rule=RULE):
    from review.features.nanowell import controller

    monkeypatch.setattr(controller, "field_sites", lambda *_a: frozenset())
    gate = cg.GateTally(sites=pd.DataFrame(), sizes=rule)
    st = SimpleNamespace(gate=gate, metrics=pd.DataFrame({"well": ["A01"], "timepoint": ["t0"]}))
    return controller.gated_crops(st, ("A01", 0, 0), {"t0": crop})["t0"]


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
    return {c.get_gid(): c for c in ax.collections}, renderer._cells_clause(tally)


def _dot_colours(gids):
    return [to_hex(c) for c in gids["nw-crop-cells"].get_facecolors()]


def test_the_crop_reads_each_cell_s_area_from_the_table(tmp_path):
    crop = _crop(tmp_path, [(100.0, 40.0, 8, 400.0), (98.0, 41.0, 7, 50.0)])
    assert crop.cell_rows["area_px"].tolist() == [400.0, 50.0]


def test_each_cell_is_dotted_in_its_class_s_colour_and_the_caption_says_which_is_which(tmp_path, monkeypatch):
    from ui import theme
    from ui.nanowell_sites import CELL_COLOUR

    crop = _crop(tmp_path, [(100.0, 40.0, 8, 400.0), (98.0, 41.0, 7, 50.0), (99.0, 40.0, 9, 200.0)])
    gated = _gated(crop, monkeypatch)
    assert gated.size_class.tolist() == [1, 0, -1] and gated.size_labels == ("T cells", "DCs")
    gids, caption = _drawn(gated)
    (t_ink, t_name), (dc_ink, dc_name) = theme.size_class_inks()
    assert _dot_colours(gids) == [to_hex(dc_ink), to_hex(t_ink), to_hex(CELL_COLOUR)]
    assert caption.endswith(f"size classes: T cells in {t_name} (1), DCs in {dc_name} (1), white in neither (1)")


def test_an_outlined_cell_is_outlined_in_its_class_s_colour(tmp_path, monkeypatch):
    from pipeline.nanowell_outlines import outlines_path, save_outlines
    from tests.test_nanowell_crop_cells import _tile_data
    from ui import theme

    mask = np.ones((2, 3), dtype=bool)

    data, metrics = _tile_data(tmp_path, [])
    path = tmp_path / "cells_A01.csv"
    pd.DataFrame([{"well": "A01", "timepoint": "t0", "fov": "F0", "nucleus_id": 7, "nw_q": 0, "nw_r": 0,
                   "well_x_um": 98.75, "well_y_um": 38.5, "area_px": 500.0}]).to_csv(path, index=False)
    data.cell_tables = {"A01": path}
    save_outlines(outlines_path(data.out_dir / "nanowells", "A01"), [("t0", "F0", 7, 98.0, 38.0, 0.5, mask)])
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    gids, _caption = _drawn(_gated(crop, monkeypatch))
    _t, (dc_ink, _name) = theme.size_class_inks()
    assert {to_hex(c) for c in gids["nw-crop-outlines"].get_colors()} == {to_hex(dc_ink)}


def test_without_a_complete_rule_the_cells_keep_the_cells_colour_and_the_caption_is_unchanged(tmp_path, monkeypatch):
    from ui.nanowell_sites import CELL_COLOUR

    crop = _crop(tmp_path, [(100.0, 40.0, 8, 400.0), (98.0, 41.0, 7, 50.0)])
    gated = _gated(crop, monkeypatch, rule=SizeClassRule.of(("T cells", None, 100)))
    assert gated.size_class is None
    gids, caption = _drawn(gated)
    assert set(_dot_colours(gids)) == {to_hex(CELL_COLOUR)}
    assert "size classes" not in caption
