"""Tiles of different brightness and offset measure the same nanowells the same way (MEASURE_VERSION 2).

Reported: 1,472 nanowells found and 17 with a confluence value. Rendered here as a 3x3-tile well whose tiles differ
by a gain step and an additive offset, as real tiles do: before, the empty nanowells — chosen as the least textured on
raw intensity — gathered on the dimmest tiles, each tile took its texture threshold from its own empty nanowells,
and every nanowell on the others had no confluence. Now every seen nanowell has one, and every number agrees with the
same well imaged with no difference between tiles.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def well():
    rendered, record = build_well(rows=3, cols=3, timepoint="0h")
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    loaded = list(truth)[::7][:30]
    for address in loaded:
        paint_cell(rendered, truth[address], dx_um=3.0)
    base = {k: np.asarray(v, dtype=np.float32) for k, v in rendered.images.items()}
    return base, record, loaded


def _measure(base, record, gain_step=0.0, offset_step=0.0):
    """Tile k is seen through gain ``1 + gain_step * (k % 3)`` and offset ``offset_step * (k % 2)``."""
    images = {f: image * (1 + gain_step * (k % 3)) + offset_step * (k % 2)
              for k, (f, image) in enumerate(sorted(base.items()))}
    located = nr.locate_timepoint("0h", record, "A01", images, SETTINGS)
    radius, roster = nr.own_array_model(located, SETTINGS)
    measured = nr.measure_timepoint(located, images, SETTINGS, radius, roster=roster, segmenter=threshold_segmenter())
    return {(r["nw_q"], r["nw_r"]): r for r in measured.sites}, measured


@pytest.fixture(scope="module")
def even(well):
    base, record, _ = well
    return _measure(base, record)


@pytest.mark.parametrize("gain_step, offset_step", [(0.15, 0.0), (0.15, 0.3), (0.3, 0.5)])
def test_every_seen_nanowell_has_a_confluence_and_it_does_not_depend_on_the_tile(well, even, gain_step, offset_step):
    base, record, _ = well
    reference, _ = even
    sites, measured = _measure(base, record, gain_step, offset_step)
    seen = [k for k, r in sites.items() if r["state"] in ("measured", "empty")]
    assert len(seen) > 500
    assert [k for k in seen if sites[k]["confluence"] is None] == []
    assert max(abs(sites[k]["confluence"] - reference[k]["confluence"]) for k in seen) < 0.03
    assert {k: (sites[k]["state"], sites[k]["n_cells"]) for k in seen} == \
        {k: (reference[k]["state"], reference[k]["n_cells"]) for k in seen}
    block = measured.template["normalisation"]
    assert block["measure_version"] == 2 and block["offset"] == "measured" and block["tiles_without_gain"] == []


def test_empty_nanowells_read_empty_and_loaded_ones_textured_on_every_tile(well):
    base, record, loaded = well
    sites, _ = _measure(base, record, 0.3, 0.5)
    by_tile_empty = {}
    for (q, r), row in sites.items():
        if row["state"] == "empty" and (q, r) != (0, 0):                # the central square is not round: not a well
            by_tile_empty.setdefault(row["owner"], []).append(row["confluence"])
    assert len(by_tile_empty) == 9                                      # every tile has empty nanowells measured
    assert max(max(v) for v in by_tile_empty.values()) < 0.1
    assert min(sites[a]["confluence"] for a in loaded if a in sites and sites[a]["state"] == "measured") > 0.1


def test_a_tile_whose_offset_the_well_cannot_vouch_for_is_corrected_for_brightness_and_says_so(well):
    base, record, _ = well
    _sites, measured = _measure(base, record, 0.3, 0.5)
    block = measured.template["normalisation"]
    assert block["scale_only_tiles"] == sorted(set(block["offset_fallback_tiles"]) & set(block["scale_only_tiles"]))
    for tile in block["scale_only_tiles"]:
        assert any(tile in note and "brightness only" in note for note in measured.notes)
