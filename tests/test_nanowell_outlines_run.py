"""Every cell a real nanowell run counts has its outline kept beside the cell table, filed under the table's own
identity and centred where the table places the cell (``pipeline.nanowell_outlines``).

``process_microscopy.main`` over the four-well plate of ``test_nanowell_manual_anchor_run``, with the deterministic
stand-in segmenter of ``test_nanowell_mask_reuse_run`` so there are many cells. Only the real entry point exercises
the whole path: the outline rides in the run's cell columns through each tile, the seam renumbering, the timepoint
merge and the well's writing, and a row shifted anywhere along it would centre an outline on another cell.
"""

from __future__ import annotations

import gzip

import numpy as np
import pandas as pd
import pytest

import tests.test_nanowell_manual_anchor_run as anchor_run
from pipeline.nanowell_outlines import outlines_path, read_outlines
from tests.test_nanowell_mask_reuse_run import CELLS, _counting_segmenter
from tests.test_nanowell_reanchor import _write_plate


def _cells(out, well):
    path = out / f"measurements_{well}.csv"
    return pd.read_csv(gzip.open(f"{path}.gz", "rt") if not path.is_file() else path,
                       dtype={"timepoint": str, "fov": str})


def test_every_counted_cell_has_its_outline_centred_on_its_row(tmp_path, monkeypatch):
    monkeypatch.setattr(anchor_run, "threshold_segmenter", lambda: _counting_segmenter([]))
    plate, _truth = _write_plate(tmp_path, cells=CELLS)
    out = anchor_run._run(monkeypatch, plate, blind=frozenset())
    checked = 0
    for well in ("A01", "A02", "B01", "B02"):
        cells = _cells(out, well)
        outlines = read_outlines(outlines_path(out / "nanowells", well))
        assert outlines is not None and len(outlines) == len(cells), well
        for row in cells.itertuples():
            x0, y0, px, mask = outlines.get(row.timepoint, row.fov, row.nucleus_id)
            ys, xs = np.nonzero(mask)
            assert x0 + (xs.mean() + 0.5) * px == pytest.approx(row.well_x_um, abs=1e-6)
            assert y0 + (ys.mean() + 0.5) * px == pytest.approx(row.well_y_um, abs=1e-6)
            assert mask.sum() == pytest.approx(row.area_px)
            checked += 1
    assert checked > 100
