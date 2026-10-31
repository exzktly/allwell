"""A well re-run after an anchor was set by hand takes its cell masks from the run before, through the real run.

``process_microscopy.main`` over the four-well plate of ``test_nanowell_manual_anchor_run``: B02's central square is
never found, so the first run leaves it unaddressed at both timepoints. An anchor is then set at 0h and B02 re-run
forced, as *Re-run anchored wells…* runs it. The anchor decides which nanowell is which, not where a crop is cut or
what it holds, so every crop the segmenter is given is one it was given before — and the re-run must segment none of
them, and still write exactly the rows a run that segmented everything again writes.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys

import numpy as np
import pytest

import process_microscopy as pm
from pipeline import nanowell_measure as nm
import tests.test_nanowell_manual_anchor_run as anchor_run
from pipeline.nanowell_masks import MaskStore, masks_path
from tests._nanowell_run_fixture import build_well
from tests.test_nanowell_reanchor import TILE, _write_plate
from util.nanowell_anchors import anchors_path, set_anchor


#: Cells drawn in every well, so the masks the re-run takes back hold something a wrong mask would count differently.
CELLS = 120


def _counting_segmenter(calls: list):
    """A deterministic stand-in that finds many objects on any input (each connected patch brighter than the input's
    own 70th percentile), counted, and with an identity so its masks are kept. The run fixture's threshold finds a
    handful on these tiles, too few for two equal label images to say anything."""
    from scipy import ndimage

    def segment(image, **_kw):
        calls.append(1)
        smoothed = ndimage.gaussian_filter(np.nan_to_num(np.asarray(image, dtype=float)), 1.0)
        labels, _n = ndimage.label(smoothed > np.percentile(smoothed, 70))
        return labels
    segment.cache_identity = "percentile stand-in"
    return segment


def _rows(out, well="B02") -> dict:
    """The well's two tables as text: its sites and, gzipped or not, its cells."""
    cells = out / f"measurements_{well}.csv"
    text = gzip.open(f"{cells}.gz", "rt").read() if not cells.is_file() else cells.read_text()
    return {"sites": (out / "nanowells" / f"{well}.csv").read_text(), "cells": text}


def _forced_b02(monkeypatch):
    real_main = pm.main

    def main():
        sys.argv += ["--only_wells", "B02", "--force_wells", "B02"]
        return real_main()
    monkeypatch.setattr(pm, "main", main)


@pytest.mark.parametrize("flags", [[], ["--nanowell_segment_on", "raw", "--nanowell_segment_scale", "tile"]],
                         ids=["defaults", "raw-tile-scale"])
def test_a_re_run_after_a_click_segments_nothing_again_and_writes_the_same_rows(tmp_path, monkeypatch, flags):
    calls: list = []
    monkeypatch.setattr(anchor_run, "threshold_segmenter", lambda: _counting_segmenter(calls))
    real_main = pm.main

    def with_flags():
        sys.argv += flags
        return real_main()

    plate, _truth = _write_plate(tmp_path, cells=CELLS)
    monkeypatch.setattr(pm, "main", with_flags)
    out = anchor_run._run(monkeypatch, plate)
    monkeypatch.undo()
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())["timepoints"]
    assert [grid[tp]["status"] for tp in ("0h", "24h")] == ["unaddressed", "unaddressed"]
    kept = masks_path(out / "nanowells", "B02")
    assert kept.is_file() and calls, "the first run segmented nothing, or kept no masks"

    rendered, _ = build_well(rows=1, cols=1, tile=TILE, well="B02", timepoint="0h", seed=11, radius_fraction=0.26,
                             cells=CELLS)
    square = rendered.sites_um[rendered.array.addresses.index((0, 0))]
    set_anchor(anchors_path(plate / "in"), "B02", "0h", np.add(square, anchor_run.CLICK_ERROR_UM))
    saved = tmp_path / "kept_masks.npz"
    shutil.copy(kept, saved)

    labels: list = []
    real_sites, real_tile = nm.segment_sites, nm.segment_tile

    def rerun():
        calls.clear()
        labels.clear()
        monkeypatch.setattr(anchor_run, "threshold_segmenter", lambda: _counting_segmenter(calls))
        # Every tile's label image as the count reads it: what a wrong mask would change before any row does.
        monkeypatch.setattr(nm, "segment_sites", lambda *a, **k: labels.append(real_sites(*a, **k)) or labels[-1])
        monkeypatch.setattr(nm, "segment_tile", lambda *a, **k: labels.append(real_tile(*a, **k)) or labels[-1])
        _forced_b02(monkeypatch)
        real = pm.main
        monkeypatch.setattr(pm, "main", lambda: (sys.argv.extend(flags), real())[1])
        result = anchor_run._run(monkeypatch, plate)
        monkeypatch.undo()
        return result

    out = rerun()
    reused_calls, reused, reused_labels = len(calls), _rows(out), list(labels)
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())["timepoints"]
    assert (grid["0h"]["status"], grid["0h"]["origin_method"]) == ("ok", "manual")
    assert (grid["24h"]["status"], grid["24h"]["origin_method"]) == ("ok", "plate_rigid")
    assert reused_calls == 0, f"the re-run after the click segmented {reused_calls} crop(s) it had segmented before"

    # The control: the same re-run with no store at all segments every crop, and must give the same label images and
    # write the same rows. No store, not merely no file: a store keyed on too little hands one crop's mask to another
    # within a run too, so a control that still had one would be wrong the same way.
    kept.unlink()
    monkeypatch.setattr(MaskStore, "wrap", lambda self, segment, timepoint: segment)
    out = rerun()
    assert calls, "with no masks kept the re-run segmented nothing: the comparison measures nothing"
    assert len(labels) == len(reused_labels) >= 2
    for fresh, taken in zip(labels, reused_labels):
        np.testing.assert_array_equal(taken, fresh)
    objects = sum(len(np.unique(t)) - 1 for t in reused_labels)
    assert objects > 100, f"the label images hold {objects} object(s): too few for equal images to mean much"
    assert _rows(out) == reused
