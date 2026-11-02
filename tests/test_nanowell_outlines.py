"""Each counted cell's outline, kept beside a nanowell run's cell table (``pipeline.nanowell_outlines``).

The outline is what Review draws over a crop, so it has to be the pixels the run counted the cell on, placed in the
well frame by the same rule as the cell's centre, and filed under the identity the cell table writes. The cases here
are built so a wrong rule lands on a different answer: tiles placed away from the origin and pixel sizes other than
one (a corner or a scale left out moves the box), an L-shaped cell (a mask stored transposed or flipped is another
shape), and merges in an order where a plain list extend shifts every outline onto another cell's row.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from pipeline import nanowell_outlines as no


def _labels():
    labels = np.zeros((12, 16), dtype=np.int64)
    labels[2:6, 3:5] = 4                      # an L: a 4×2 bar with a foot to the right
    labels[5, 5:8] = 4
    labels[8:11, 10:15] = 9                   # a 3×5 block
    return labels


def test_an_outline_is_the_cell_s_own_pixels_placed_by_the_tile_s_corner_and_scale():
    labels = _labels()
    texts = no.encode_outlines(labels, [4, 9], 0.5, (100.0, 30.0))
    x0, y0, px, mask = no.decode_outline(texts[0])
    assert (x0, y0, px) == (100.0 + 3 * 0.5, 30.0 + 2 * 0.5, 0.5)
    assert mask.tolist() == (labels[2:6, 3:8] == 4).tolist()
    x0, y0, px, mask = no.decode_outline(texts[1])
    assert (x0, y0) == (105.0, 34.0) and mask.shape == (3, 5) and mask.all()


def test_a_label_with_no_pixel_has_no_outline():
    assert no.encode_outlines(_labels(), [4, 7, 40], 1.0, (0.0, 0.0))[1:] == [None, None]


def test_rows_without_outlines_keep_every_later_outline_on_its_own_row():
    """A timepoint resumed from a checkpoint written before outlines were kept has rows and no outline column."""
    into: dict = {}
    no.extend_aligned(into, {"label": [1, 2]})                                       # an old checkpoint's rows
    no.extend_aligned(into, {"label": [3], no.OUTLINE_COLUMN: ["c"]})
    assert into[no.OUTLINE_COLUMN] == [None, None, "c"]
    into = {}
    no.extend_aligned(into, {"label": [1], no.OUTLINE_COLUMN: ["a"]})
    no.extend_aligned(into, {"label": [2, 3]})
    no.extend_aligned(into, {"label": [4], no.OUTLINE_COLUMN: ["d"]})
    assert into[no.OUTLINE_COLUMN] == ["a", None, None, "d"] and into["label"] == [1, 2, 3, 4]


def _row(tp, fov, label, shift=0.0):
    return (tp, fov, label, 10.0 + shift, 20.0, 0.5, _labels()[2:6, 3:8] == 4)


def test_the_file_reads_back_what_was_written_under_the_cell_table_s_identity(tmp_path):
    path = tmp_path / "A01_outlines.npz"
    assert no.save_outlines(path, [_row("0h", "F0", 4), _row("24h", "F1", 4, shift=3.0)]) == 2
    got = no.read_outlines(path)
    x0, y0, px, mask = got.get("24h", "F1", 4.0)                  # a label read back from a CSV is a float
    assert (x0, y0, px) == (13.0, 20.0, 0.5) and mask.tolist() == _row("0h", "F0", 4)[6].tolist()
    assert got.get("24h", "F0", 4) is None and got.get("0h", "F0", 5) is None


def test_no_rows_removes_the_file_rather_than_keep_one_describing_cells_the_table_no_longer_holds(tmp_path):
    path = tmp_path / "A01_outlines.npz"
    no.save_outlines(path, [_row("0h", "F0", 4)])
    assert no.save_outlines(path, []) == 0 and not path.exists()


def test_a_file_of_another_layout_reads_as_no_outlines(tmp_path):
    path = tmp_path / "A01_outlines.npz"
    no.save_outlines(path, [_row("0h", "F0", 4)])
    with np.load(path) as data:
        arrays = {k: data[k] for k in data.files}
    arrays["version"] = np.array(no.OUTLINES_VERSION + 1)
    np.savez(path, **arrays)
    assert len(no.read_outlines(path)) == 0


def test_an_incremental_run_keeps_the_kept_timepoints_outlines_and_replaces_the_rest(tmp_path):
    prior = tmp_path / "prior.npz"
    no.save_outlines(prior, [_row("0h", "F0", 4), _row("24h", "F0", 4, shift=5.0)])
    labels = _labels()
    cells = {"timepoint": ["24h"], "fov": ["F0"], "label": [9],
             no.OUTLINE_COLUMN: no.encode_outlines(labels, [9], 1.0, (0.0, 0.0))}
    target = tmp_path / "A01_outlines.npz"
    assert no.write_well_outlines(target, cells, prior, kept={"0h"}) == 2
    got = no.read_outlines(target)
    assert got.get("0h", "F0", 4)[0] == 10.0                     # the kept timepoint's, from the prior file
    assert got.get("24h", "F0", 4) is None                       # the prior run's cell of a re-measured timepoint
    assert got.get("24h", "F0", 9)[:2] == (10.0, 8.0)


def test_the_fetcher_s_restated_suffix_is_the_pipeline_s():
    from services.remote_fetch_nanowells import OUTLINES_SUFFIX

    assert OUTLINES_SUFFIX == no.OUTLINES_SUFFIX


# ── through the run's own tile measurement ───────────────────────────────────
def test_each_counted_cell_s_outline_is_centred_where_the_table_places_the_cell():
    """``nanowell_run._tile_contents``, the run's own per-tile step, on the confluence scene with a segmenter that
    finds an irregular blob in every nanowell, on a tile placed away from the well frame's origin: every row of the
    cell table carries an outline, and the outline's own centre of mass, in well µm, is the row's ``well_x_um`` /
    ``well_y_um``. An outline placed without the tile's corner, or one filed against another row, is elsewhere."""
    from pipeline import nanowell_run as nr
    from tests import test_nanowell_confluence_reference as scene
    from tests.test_nanowell_segmenter_reference import RIM, _norm, _paint

    from types import SimpleNamespace

    image, _edge = _paint(0, 3)
    images, template, norm, _out = _norm(image, None)
    # The scene's tile sits at the well frame's origin; move it (and its nanowells with it) so a corner left out shows.
    origin = (513.0, 217.0)
    located = SimpleNamespace(**{**vars(scene.LOCATED), "positions": {scene.TILE: origin}})
    geoms = {i: replace(g, centre_um=(g.centre_um[0] + origin[0], g.centre_um[1] + origin[1]))
             for i, g in scene.GEOMS.items()}

    def blobs(img, **_kw):
        labels = np.zeros(np.shape(img), dtype=np.int64)
        for k, (x, y) in enumerate(scene.CENTRES, start=1):
            cx, cy = int(x) + (k % 3), int(y) - (k % 2)            # off the centre, by a different step per site
            labels[cy - 4:cy + 5, cx - 3:cx + 3] = k
            labels[cy + 5:cy + 7, cx - 3:cx + 7] = k               # a foot, so the blob is not symmetric
        return labels

    _got, table, _notes = nr._tile_contents(scene.TILE, scene.BY_TILE[scene.TILE], located, images,
                                            replace(RIM, segment_on="raw"), geoms, [(i, 0) for i in geoms],
                                            template, blobs, None, None, norm)
    assert len(table["label"]) == len(scene.CENTRES)
    assert len(table[no.OUTLINE_COLUMN]) == len(table["label"])
    for x_um, y_um, text in zip(table["well_x_um"], table["well_y_um"], table[no.OUTLINE_COLUMN]):
        x0, y0, px, mask = no.decode_outline(text)
        rows, cols = np.nonzero(mask)
        assert x0 + (cols.mean() + 0.5) * px == pytest.approx(x_um, abs=1e-9)
        assert y0 + (rows.mean() + 0.5) * px == pytest.approx(y_um, abs=1e-9)
