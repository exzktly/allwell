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
