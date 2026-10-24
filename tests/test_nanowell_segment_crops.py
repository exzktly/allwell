"""The nanowell count's segmenter is given a crop around each nanowell, not the whole tile (``segment_scope``).

Asked for, to make Cellpose-SAM cheaper on a T4: "Nanowell crops". The segmenter's work is the pixels it is given,
and on a nanowell tile much of them are the lattice between nanowells, which holds no cell the count keeps. Each crop
is scaled by the whole tile's 1st-99th percentiles, never its own, so an empty nanowell's noise is not stretched
across the segmenter's range; labels are pasted back into a whole-tile image, so counting is unchanged. A run's
options that name no scope mean the whole tile, which every earlier run segmented.
"""

from __future__ import annotations

import numpy as np
import pytest

import process_microscopy as pm
from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from pipeline.nanowell_detect import FITTED, SiteGeometry
from pipeline.nanowell_preview import preview_timepoint
from tests._nanowell_run_fixture import threshold_segmenter
from tests.test_nanowell_preview import SCHEMA, WELL, plate  # noqa: F401


def _sites(centres, radius=10.0):
    return [SiteGeometry(i, c, radius, FITTED, 0.2, 64) for i, c in enumerate(centres)]


def test_each_nanowell_is_one_call_on_its_own_crop_scaled_as_the_tile_and_pasted_back_in_place():
    rng = np.random.default_rng(0)
    image = rng.normal(100.0, 5.0, (120, 160))
    image[0, 0] = 1000.0                                        # one bright pixel far from any nanowell
    geoms = _sites([(30.5, 30.5), (90.5, 60.5), (140.5, 90.5)])
    calls = []

    def recording(crop, lowhigh=None):
        calls.append((crop.shape, lowhigh))
        labels = np.zeros(crop.shape, dtype=np.int32)
        cy, cx = crop.shape[0] // 2, crop.shape[1] // 2
        labels[cy - 1:cy + 2, cx - 1:cx + 2] = 1                # one cell at the crop's centre
        return labels
    recording.accepts_range = True

    labels = nm.segment_sites(image, 1.0, geoms, recording)
    side = int(np.ceil(2 * nm.CROP_REACH * 10.0)) + 1
    assert [shape for shape, _ in calls] == [(side, side)] * 3              # a crop each, never the whole tile
    expected = tuple(np.percentile(image, nm.SEGMENTER_PERCENTILES))
    assert all(lowhigh == pytest.approx(expected) for _, lowhigh in calls)  # the tile's scale, not the crop's
    assert sorted(set(np.unique(labels)) - {0}) == [1, 2, 3]                # renumbered: no two crops share a label
    for g in geoms:
        assert labels[int(g.centre_um[1]), int(g.centre_um[0])] > 0         # each cell back where its nanowell is


def test_a_segmenter_without_a_range_is_called_as_before():
    seen = []
    geoms = _sites([(30.5, 30.5)])
    nm.segment_sites(np.ones((60, 60)), 1.0, geoms, lambda crop: seen.append(crop.shape) or np.zeros(crop.shape))
    assert len(seen) == 1


def _preview(plate, tmp_path, scope, segmenter):  # noqa: F811
    zip_path, _ = plate
    scratch = tmp_path / f"scratch_{scope}"
    scratch.mkdir()
    return preview_timepoint(zip_path, WELL, "0h", nr.NanowellSettings("100um", segment_scope=scope),
                             grouper=pm.find_image_groups_in_zip, parse=pm.parse_filename, schema=SCHEMA, sep="_",
                             phase_token="PHASE", scratch=scratch, segmenter=segmenter, n_crops=2)


def test_crops_count_what_the_whole_tile_counts_and_give_the_segmenter_less(plate, tmp_path):  # noqa: F811
    """On the rim-divided image with a local segmenter, cropping changes nothing a nanowell holds: every site's
    count is the same, while every call is a crop much smaller than a tile."""
    shapes = {"tile": [], "nanowells": []}
    results = {}
    for scope in ("tile", "nanowells"):
        inner = threshold_segmenter()

        def recording(image, _scope=scope, _inner=inner):
            shapes[_scope].append(np.shape(image))
            return _inner(image)
        results[scope] = _preview(plate, tmp_path, scope, recording)
    tile_px = max(h * w for h, w in shapes["tile"])
    assert shapes["nanowells"] and max(h * w for h, w in shapes["nanowells"]) < tile_px / 50
    counts = {scope: {s["address"]: s["n_cells"] for s in r.sites} for scope, r in results.items()}
    assert counts["tile"] == counts["nanowells"]
    assert sum(n or 0 for n in counts["tile"].values()) > 0              # the painted cells were found at all
    # The pixels the segmenter was given, all crops together, are a fraction of the tiles'.
    assert sum(h * w for h, w in shapes["nanowells"]) < sum(h * w for h, w in shapes["tile"])


def test_a_new_run_crops_and_an_earlier_run_s_options_mean_the_whole_tile():
    from pipeline.cli_args import build_parser, nanowell_options_from_args
    from pipeline.nanowell_task import settings_from_kwargs

    parser = build_parser()

    def options(*extra):
        args = parser.parse_args(["--input_dir", "in", "--output_dir", "out", "--nanowell_format", "100um",
                                  "--phase_token", "PHASE", *extra])
        return nanowell_options_from_args(args, parser.error)

    new = settings_from_kwargs({"_nanowell": options()})
    assert new.segment_scope == "nanowells" and new.key()["segment_scope"] == "nanowells"
    for legacy in (options("--nanowell_segment_scope", "tile"),
                   {k: v for k, v in options().items() if k != "segment_scope"}):
        settings = settings_from_kwargs({"_nanowell": legacy})
        assert settings.segment_scope == "tile" and "segment_scope" not in settings.key()
    from analyze.nanowell_section import settings_from_opts

    assert settings_from_opts({"nanowell_format": "100um"}).segment_scope == "nanowells"     # Test detection's


@pytest.mark.parametrize("scope", ["nanowells", "tile"])
def test_a_measured_timepoint_reports_its_segmented_nanowells_and_where_the_count_stopped(plate, tmp_path,  # noqa: F811
                                                                                          monkeypatch, scope):
    """The progress a run prints inside a measure step (``_SiteCount``), through the real measurement: it starts at
    none of the timepoint's imaged nanowells, climbs, and its last word is the count reached — here one short of the
    total, a nanowell the first tile leaves unsegmented — rather than going silent at the last tenth it crossed."""
    said = []
    real_measure, real_tile = nr.measure_timepoint, nr._tile_contents
    first = []

    def measure(*a, **kw):
        return real_measure(*a, on_segmented=lambda done, total: said.append((done, total)), **kw)

    def tile(*a, on_segmented=None, **kw):
        if not first:                                                 # the first tile segments one nanowell fewer
            first.append(True)
            skipped = []

            def short(n, _skipped=skipped):
                if not _skipped:
                    _skipped.append(True)
                    n -= 1
                on_segmented(n)
            return real_tile(*a, on_segmented=short, **kw)
        return real_tile(*a, on_segmented=on_segmented, **kw)

    monkeypatch.setattr(nr, "measure_timepoint", measure)
    monkeypatch.setattr(nr, "_tile_contents", tile)
    _preview(plate, tmp_path, scope, threshold_segmenter())
    total = said[0][1]
    assert said[0] == (0, total) and total > 0 and {t for _d, t in said} == {total}
    assert [d for d, _t in said] == sorted(d for d, _t in said)
    assert said[-1] == (total - 1, total)


def test_a_segmenter_that_takes_several_crops_gets_them_in_batches_and_labels_them_as_one_at_a_time():
    """``segment_many`` (Cellpose 4's batch) is given ``CROP_BATCH`` crops a call, each with the tile's range, and
    the tile's labels are exactly what one call per crop gives: same crops, same renumbering, same pasting."""
    rng = np.random.default_rng(3)
    image = rng.random((200, 200)) * 10
    centres = [(15.5 + 30 * (i % 6), 15.5 + 30 * (i // 6)) for i in range(nm.CROP_BATCH + 4)]
    for x, y in centres[::2]:                                               # a cell in every other nanowell
        image[int(y) - 3:int(y) + 3, int(x) - 3:int(x) + 3] = 100.0
    geoms = _sites(centres, radius=10.0)
    one = threshold_segmenter(level=0.5)

    def single(crop, lowhigh=None):
        return one((np.asarray(crop) - lowhigh[0]) / (lowhigh[1] - lowhigh[0]))
    single.accepts_range = True
    batches = []

    def batched(crop, lowhigh=None):
        raise AssertionError("a crop went to the one-at-a-time call")

    def many(crops, lowhigh):
        batches.append((len(crops), lowhigh))
        return [single(c, lowhigh=lowhigh) for c in crops]
    batched.accepts_range, batched.segment_many = True, many
    ticks = []
    want = nm.segment_sites(image, 1.0, geoms, single)
    got = nm.segment_sites(image, 1.0, geoms, batched, on_site=lambda: ticks.append(1))
    assert np.array_equal(got, want) and got.max() > 0
    lowhigh = tuple(np.percentile(image, nm.SEGMENTER_PERCENTILES))
    assert [n for n, _ in batches] == [nm.CROP_BATCH, 4] and all(np.allclose(lh, lowhigh) for _, lh in batches)
    assert len(ticks) == len(geoms)


def _rimmed_tile():
    """A raw phase-like tile: nanowells with a dark-then-bright rim (the extremes of the tile), a mid-grey interior
    and lattice, and faint cells inside — a few per cent of the rims' swing, as cells are on such an image."""
    px = 1.0
    tile = np.full((200, 200), 100.0)
    yy, xx = np.mgrid[:200, :200]
    centres = [(50.5, 50.5), (150.5, 50.5), (50.5, 150.5), (150.5, 150.5)]
    for cx, cy in [(x - 0.5, y - 0.5) for x, y in centres]:              # a site at x um is pixel x - 0.5
        d = np.hypot(xx - cx, yy - cy)
        tile[(d > 20) & (d <= 22)] = 20.0                                    # dark rim
        tile[(d > 22) & (d <= 24)] = 250.0                                   # bright halo
        tile[np.hypot(xx - (cx + 5), yy - cy) <= 4] = 112.0                  # a faint cell
    return tile, px, _sites(centres, radius=22.0)


def test_the_nanowell_scale_gives_cells_the_range_the_rims_took():
    """Reported: on the image as imaged Cellpose-SAM counted almost every nanowell empty, except at the array's edge.
    Scaled by the tile's own percentiles (``tile``) the rims are 0 and 1 and a cell is a sliver between; scaled by the
    nanowells' interiors (``nanowells``), with rims and lattice set to the interiors' median, the cell spans the range
    and nothing outside the nanowells is left to segment."""
    tile, px, geoms = _rimmed_tile()
    same, none = nm.segmenter_scale(tile, px, geoms, "tile")
    assert same is tile and none is None
    seen, (lo, hi) = nm.segmenter_scale(tile, px, geoms, "nanowells")
    cell = (112.0 - lo) / (hi - lo)
    background = (100.0 - lo) / (hi - lo)
    tile_lo, tile_hi = np.percentile(tile, nm.SEGMENTER_PERCENTILES)
    assert (112.0 - 100.0) / (tile_hi - tile_lo) < 0.06                  # a sliver of the tile's range
    assert cell - background > 0.9                                        # most of the range under the new scale
    assert np.all(seen[np.isin(tile, (20.0, 250.0))] == 100.0)           # no rim left for the segmenter
    assert seen[0, 0] == 100.0 and seen[50, 55] == 112.0                 # lattice blanked, the cell kept
    view = nm.segmenter_view(seen, px, geoms, lowhigh=(lo, hi))
    assert all(v.contrast > 0.9 for v in view.values())                  # the column says what the model saw


def test_a_run_s_count_is_scaled_by_its_nanowells_interiors_and_an_earlier_run_s_by_its_tile():
    from pipeline.cli_args import build_parser, nanowell_options_from_args
    from pipeline.nanowell_task import settings_from_kwargs

    parser = build_parser()

    def options(*extra):
        args = parser.parse_args(["--input_dir", "in", "--output_dir", "out", "--nanowell_format", "100um",
                                  "--phase_token", "PHASE", *extra])
        return nanowell_options_from_args(args, parser.error)

    new = settings_from_kwargs({"_nanowell": options()})
    assert new.segment_scale == "levelled" and new.key()["segment_scale"] == "levelled"
    assert settings_from_kwargs({"_nanowell": options("--nanowell_segment_scale", "nanowells")}).segment_scale \
        == "nanowells"
    for legacy in (options("--nanowell_segment_scale", "tile"),
                   {k: v for k, v in options().items() if k != "segment_scale"}):
        settings = settings_from_kwargs({"_nanowell": legacy})
        assert settings.segment_scale == "tile" and "segment_scale" not in settings.key()
    with pytest.raises(ValueError, match="segmenter scale"):
        nr.NanowellSettings("100um", segment_scale="percentile")


def test_a_bright_nanowell_is_levelled_to_the_others_before_the_pooled_range():
    """Reported: on brightfield, the nanowells counted empty were often the brightest. Under the pooled interior
    range (``nanowells``) a bright nanowell either lies above the range (a rare one: the segmenter is given values it
    never sees) or, being 1 % of the pixels or more, widens it and squashes every other nanowell's cells — here one in
    four, so the second. ``levelled`` brings its floor to the others' and keeps every cell's contrast."""
    tile, px, geoms = _rimmed_tile()
    yy, xx = np.mgrid[:200, :200]
    bright = np.hypot(xx - 149.5, yy - 149.5) <= 20                       # the fourth nanowell, 60 brighter
    tile = tile.copy()
    tile[bright] += 60.0
    plain, (lo, hi) = nm.segmenter_scale(tile, px, geoms, "nanowells")
    assert (plain[150, 150] - lo) / (hi - lo) >= 1.0                      # its floor at the top of the range
    assert (plain[50, 55] - plain[50, 50]) / (hi - lo) < 0.25             # and another nanowell's cell squashed
    seen, (lo2, hi2) = nm.segmenter_scale(tile, px, geoms, "levelled")
    floor = (seen[150, 150] - lo2) / (hi2 - lo2)
    others = (seen[50, 50] - lo2) / (hi2 - lo2)
    assert abs(floor - others) < 1e-9 and 0.0 <= floor <= 1.0             # at the others' floor, in range
    cell = (seen[150, 155] - lo2) / (hi2 - lo2)                           # its faint cell (cx + 5)
    assert cell - floor > 0.9                                              # the cell keeps its contrast
    assert (seen[50, 55] - seen[50, 50]) / (hi2 - lo2) > 0.9              # and so do the others' cells
    with pytest.raises(ValueError, match="segmenter scale"):
        nm.segmenter_scale(tile, px, geoms, "percentile")
