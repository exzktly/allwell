"""The settings the Analyze form exposes reach the analysis, not just the argv (tracker A2).

Both were validated, keyed and documented before the form existed and neither had been read by the code that
decides anything: a branch that is written and tested and not taken (CLAUDE.md §7.7).
"""

from __future__ import annotations

import pytest

from pipeline import nanowell_run as nr
from tests._nanowell_run_fixture import build_well, threshold_segmenter

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def tile():
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900))
    return rendered, record


def test_a_square_cut_at_another_angle_to_the_lattice_row_is_refused_unless_the_setting_says_so(tile):
    rendered, record = tile
    parallel = nr.locate_timepoint("T0", record, "A01", rendered.images, SETTINGS)
    assert parallel.status == nr.OK                       # the rendered square is parallel to a row: the default holds
    tilted = nr.locate_timepoint("T0", record, "A01", rendered.images, nr.NanowellSettings("100um", square_edge_deg=30.0))
    assert tilted.status == nr.ORIENTATION_REFUSED and "square" in tilted.reason


def test_the_visibility_threshold_decides_which_sites_carry_a_count():
    # A bubble over part of the array leaves sites between half and fully visible.
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), bubble=(1935.0 + 330.0, 1935.0, 250.0))
    load = lambda tp, tile_id: rendered.images.get(tile_id)           # noqa: E731
    default = nr.run_well("A01", record, ["T0"], load, SETTINGS, segmenter=threshold_segmenter())
    strict = nr.run_well("A01", record, ["T0"], load, nr.NanowellSettings("100um", occluded_below=0.999),
                         segmenter=threshold_segmenter())
    partly = [r for r in default.sites if r["visible_fraction"] is not None and 0.5 <= r["visible_fraction"] < 0.99
              and r["imaged_fraction"] >= 0.99 and r["state"] != "clipped"]
    assert partly, "the bubble must leave some sites partly visible for this to measure anything"
    by_address = {(r["nw_q"], r["nw_r"]): r for r in strict.sites}
    for r in partly:
        assert r["n_cells"] is not None and r["state"] in ("empty", "measured")             # counted at the default 0.5
        assert by_address[(r["nw_q"], r["nw_r"])]["state"] == "occluded"                  # not at 0.999
        assert by_address[(r["nw_q"], r["nw_r"])]["n_cells"] is None


def test_the_clipping_threshold_decides_which_edge_sites_carry_a_count():
    # The array pushed against the tile edge: its outermost nanowells are cut by it.
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), array_shift_um=(1000.0, 0.0))
    load = lambda tp, tile_id: rendered.images.get(tile_id)           # noqa: E731
    loose = nr.run_well("A01", record, ["T0"], load, SETTINGS, segmenter=threshold_segmenter())
    strict = nr.run_well("A01", record, ["T0"], load, nr.NanowellSettings("100um", clipped_below=0.999),
                         segmenter=threshold_segmenter())
    edge = [r for r in loose.sites if 0.5 <= r["imaged_fraction"] < 0.99]
    assert edge, "the array must reach the tile edge for this to measure anything"
    by_address = {(r["nw_q"], r["nw_r"]): r for r in strict.sites}
    assert all(by_address[(r["nw_q"], r["nw_r"])]["state"] == "clipped" for r in edge)
    assert all(r["state"] != "clipped" for r in edge)
