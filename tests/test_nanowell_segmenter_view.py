"""What the segmenter is shown, written beside each nanowell's count.

Reported: "neighboring nanowells with similar cell counts and brightness have wildly divergent results", down to a
nanowell with about twenty clearly visible, well separated cells counted as zero. Cellpose never sees the raw tile:
each nanowell is levelled by its own ``(offset, gain)``, divided by the template, and the whole tile is then scaled
by its own 1st and 99th percentiles. Two nanowells that look alike in the raw image can therefore reach the
segmenter at very different contrast, and nothing in the output said so. These tests pin the four columns that now
do (``level_offset``, ``level_gain``, ``segmenter_floor``, ``segmenter_contrast``) and Test detection drawing its
crops on the tile's shared scale rather than stretching each crop to its own range, which made every nanowell look
alike whatever the segmenter saw.
"""

from __future__ import annotations

import csv

import numpy as np
import pytest

from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt
from pipeline.nanowell_detect import SiteGeometry

PX = 1.0


def _site(index, x, y, radius=20.0):
    return SiteGeometry(index, (x, y), radius, "fitted")


def _disk(shape, cx, cy, r):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return np.hypot(xx + 0.5 - cx, yy + 0.5 - cy) <= r


def _tile():
    """A 200 x 200 rim-suppressed tile, flat 1.0 between sites, and three sites of radius 20 px:

    * A (radius 30 px) holds a bright cell (3.0, radius 18 px, 2.5 % of the tile), which sets its 99th percentile;
    * B holds the same cell at a contrast of 1.1, as a nanowell levelled with too large a gain would;
    * C is bare floor read at 1.6, as a nanowell given a wrong offset would.

    The tile's 1st percentile is 1.0 and its 99th 3.0 (C's 1.6 covers 3.1 %, under A's 3.0), so the segmenter's scale is ``(value - 1) / 2``."""
    image = np.ones((200, 200))
    image[_disk(image.shape, 50, 50, 18)] = 3.0
    image[_disk(image.shape, 150, 50, 9)] = 1.1
    image[_disk(image.shape, 100, 150, 20)] = 1.6
    return image, [_site(0, 50, 50, radius=30.0), _site(1, 150, 50), _site(2, 100, 150)]


def test_each_site_is_read_on_the_scale_the_segmenter_normalises_the_whole_tile_to():
    image, sites = _tile()
    assert np.percentile(image, nm.SEGMENTER_PERCENTILES) == pytest.approx([1.0, 3.0])   # the fixture's premise
    view = nm.segmenter_view(image, PX, sites)
    # A: floor at the flat level between sites (0), its cell spanning the whole range (1).
    assert view[0].floor == pytest.approx(0.0) and view[0].contrast == pytest.approx(1.0)
    # B: the same floor, its cell 5 % of the range: a cell the segmenter barely sees.
    assert view[1].floor == pytest.approx(0.0) and view[1].contrast == pytest.approx(0.05)
    # C: a floor lifted to 0.3 of the range and no contrast at all.
    assert view[2].floor == pytest.approx(0.3) and view[2].contrast == pytest.approx(0.0)


def test_the_scale_is_the_tile_s_not_each_site_s():
    """A site's contrast is its spread over the tile's range: a per-site stretch would read B as 1.0 like A, which
    is exactly the view that hid the divergence."""
    image, sites = _tile()
    alone = image.copy()
    alone[_disk(image.shape, 50, 50, 18)] = 1.0                    # take A's bright cell away: B now sets the range
    assert nm.segmenter_view(image, PX, sites)[1].contrast == pytest.approx(0.05)
    assert nm.segmenter_view(alone, PX, sites)[1].contrast == pytest.approx(1 / 6)       # (1.1 - 1) / (1.6 - 1)


def test_a_site_that_cannot_be_read_is_none_never_zero():
    image, sites = _tile()
    image[150, 100] = np.nan                                      # inside C's interior
    off = _site(3, 500, 500)
    view = nm.segmenter_view(image, PX, sites + [off])
    assert view[2] == nm.SegmenterView(2, None, None) and view[3] == nm.SegmenterView(3, None, None)
    flat = nm.segmenter_view(np.ones((200, 200)), PX, sites)
    assert all(v.floor is None and v.contrast is None for v in flat.values())   # no spread: no scale


def test_the_columns_carry_the_site_s_level_and_view_and_are_blank_when_it_was_not_measured():
    got = {"level": (0.25, 1.5), "view": nm.SegmenterView(4, 0.3, 0.05)}
    assert nr._levelling_columns(got) == {"level_offset": 0.25, "level_gain": 1.5, "segmenter_floor": 0.3,
                                          "segmenter_contrast": 0.05}
    blank = {"level_offset": None, "level_gain": None, "segmenter_floor": None, "segmenter_contrast": None}
    assert nr._levelling_columns(None) == blank
    assert nr._levelling_columns({"level": None, "view": None}) == blank


def test_the_sites_csv_writes_the_four_columns(tmp_path):
    row = {c: None for c in nt.SITE_COLUMNS}
    row.update(timepoint="T0", nw_q=1, nw_r=2, n_cells=0, level_offset=0.25, level_gain=1.5,
               segmenter_floor=0.3, segmenter_contrast=0.05)
    path = tmp_path / "A01.csv"
    nt.write_sites_csv(path, [row], [])
    with path.open(newline="", encoding="utf-8") as fh:
        written = next(csv.DictReader(fh))
    assert (written["level_offset"], written["level_gain"], written["segmenter_floor"],
            written["segmenter_contrast"]) == ("0.25", "1.5", "0.3", "0.05")


def test_review_reads_the_four_columns_as_numbers():
    from review.features.nanowell import collect

    assert {"level_offset", "level_gain", "segmenter_floor", "segmenter_contrast"} <= set(collect._NUMERIC_COLUMNS)


def test_a_test_detection_crop_is_drawn_on_its_tile_s_scale(qtbot):
    """Two crops holding the same pixels, one cut from a tile whose range they span and one from a tile with a far
    brighter cell elsewhere: drawn on their tiles' scales they differ, as the segmenter saw them differ. Each
    stretched to its own range (the old drawing) they were identical."""
    pytest.importorskip("PySide6")
    from analyze.nanowell_preview import render_crop

    rim = np.ones((21, 21), np.float32)
    rim[8:13, 8:13] = 1.1
    labels = np.zeros((21, 21), np.int64)
    centre = (10, 10)

    def grey(scale):
        image = render_crop({"rim": rim, "labels": labels, "scale": scale}, size=21).toImage()
        return image.pixelColor(*centre).red()

    assert grey((1.0, 1.1)) == 255                    # the crop spans its tile's range: its cell is white
    assert grey((1.0, 3.0)) == int(0.05 * 255)        # a brighter tile: the same cell is near black
    assert grey(None) == 255                          # no scale: stretched to its own range, as before


# ── the offset is its neighbours' (MEASURE_VERSION 5) ────────────────────────
def _hex(pitch=172.0):
    """A centre site and its six neighbours, then a site two pitches away with none of them in reach."""
    angles = np.radians(np.arange(6) * 60.0)
    return [(0.0, 0.0)] + [(pitch * np.cos(a), pitch * np.sin(a)) for a in angles] + [(0.0, 3 * pitch)]


def test_one_nanowell_s_misread_offset_is_outvoted_by_its_neighbours_and_its_gain_is_kept():
    centres = _hex()
    order = list(range(8))
    levels = {i: (0.01 * i, 1.0 + 0.1 * i) for i in order}
    levels[0] = (0.12, 0.5)                                   # the centre's surround misread
    out = nr._smoothed_offsets(levels, order, ["F0"] * 8, centres, set(order), 1.5 * 172.0)
    assert out[0] == pytest.approx((0.04, 0.5))               # median of 0.12 and 0.01 .. 0.06; its own gain
    assert out[1] == pytest.approx((0.04, 1.1))               # 0.01 with 0.12, 0.02, 0.06 and itself: median 0.04
    # Fewer than three in reach: the tile's median offset (0.045), as a gain is.
    assert out[7] == pytest.approx((0.045, 1.7))


def test_bare_floor_lends_no_offset_but_takes_its_neighbours_and_an_unlevelled_site_stays_unlevelled():
    centres = _hex()
    order = list(range(8))
    levels = {i: (0.02, 1.0) for i in order}
    bare = {3, 4, 5, 6}                                       # four of the centre's seven: they would win a vote
    levels.update({i: (5.0, 1.0) for i in bare})
    levels[7] = None                                          # no level at all
    out = nr._smoothed_offsets(levels, order, ["F0"] * 8, centres, set(order) - bare, 1.5 * 172.0)
    assert out[0] == pytest.approx((0.02, 1.0))               # the bare floor did not vote
    assert out[4] == pytest.approx((0.02, 1.0))               # and took its neighbours'
    assert out[7] is None


def test_a_tile_with_no_nanowell_offset_keeps_each_site_s_own_and_tiles_do_not_mix():
    centres = _hex()[:4]
    order = [0, 1, 2, 3]
    levels = {0: (0.3, 1.0), 1: (0.0, 1.0), 2: (0.0, 1.0), 3: (0.0, 1.0)}
    owners = ["F1", "F0", "F0", "F0"]                         # the centre alone on its tile, its neighbours elsewhere
    out = nr._smoothed_offsets(levels, order, owners, centres, {1, 2, 3}, 1.5 * 172.0)
    assert out[0] == pytest.approx((0.3, 1.0))                # F1 has no nanowell offset: its own, not F0's
