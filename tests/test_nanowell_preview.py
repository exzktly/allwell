"""Test detection's analysis: one well at one timepoint, described for a person (tracker A4)."""

from __future__ import annotations

import pytest

import process_microscopy as pm
from pipeline import nanowell_run as nr
from pipeline.nanowell_preview import preview_timepoint
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter
from tests.test_nanowell_cli import SCHEMA, WELL, _write_plate

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def plate(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("preview")
    rendered, record = build_well(timepoint="0h", overlap=0.03)           # a short overlap, so seams are fused
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    for address in ((1, 2), (1, 3), (-2, 4)):
        paint_cell(rendered, truth[address], dx_um=3.0)
    root = _write_plate(tmp, rendered, record)
    return root / "in" / f"{WELL}.zip", tmp


def _preview(plate, tmp_path, **kwargs):
    zip_path, _ = plate
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return preview_timepoint(zip_path, WELL, "0h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                             parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                             scratch=scratch, segmenter=threshold_segmenter(), **kwargs)


def test_the_preview_describes_the_well_it_was_asked_about(plate, tmp_path):
    result = _preview(plate, tmp_path)
    assert result.status == nr.OK and result.well == WELL and result.timepoint == "0h"
    assert result.montage is not None and result.montage.dtype.name == "uint8" and result.um_per_px > 0
    assert result.origin_method == "fiducial" and result.tile_count == 4
    assert result.pitch_um == pytest.approx(172.0, rel=0.01)
    by_address = {s["address"]: s for s in result.sites if s["address"]}
    assert len(by_address) > 200
    assert by_address["1,2"]["n_cells"] == 1 and by_address["0,1"]["n_cells"] == 0


def test_the_overlap_is_reported_against_the_one_a_nanowell_needs(plate, tmp_path):
    result = _preview(plate, tmp_path)
    assert result.overlap_needed_um == pytest.approx(120.0)             # 100 um + 2 x a tenth
    measured = [o for o in result.overlap_um if o is not None]
    assert measured and all(o < result.overlap_needed_um for o in measured)       # 3 % of 3010 um = 90 um
    counts = result.counts()
    assert counts.get("fused", 0) > 0 and counts["empty"] > 100                  # sites on the seams are marked fused


def test_a_few_crops_show_the_rim_suppressed_site_beside_its_segmentation(plate, tmp_path):
    result = _preview(plate, tmp_path, n_crops=3)
    assert 1 <= len(result.crops) <= 3
    full = [c for c in result.crops if c["n_cells"]]
    empty = [c for c in result.crops if c["n_cells"] == 0]
    assert full and empty                                                # one with cells, one without
    for crop in result.crops:
        assert crop["rim"].shape == crop["labels"].shape and crop["rim"].ndim == 2
    assert all((c["labels"] > 0).any() for c in full) and not any((c["labels"] > 0).any() for c in empty)


def test_a_refusal_is_a_status_with_a_reason_not_an_exception(plate, tmp_path):
    zip_path, _ = plate
    (zip_path.parent / "acquisition.json").rename(zip_path.parent / "acquisition.json.bak")
    try:
        result = _preview(plate, tmp_path)
    finally:
        (zip_path.parent / "acquisition.json.bak").rename(zip_path.parent / "acquisition.json")
    assert result.status == "error" and "acquisition record" in result.reason and result.montage is None
    missing = preview_timepoint(zip_path, WELL, "99h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                                parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                                scratch=tmp_path / "s2")
    assert missing.status == "error" and "99h" in missing.reason


def test_test_detection_refuses_a_described_grid_the_well_s_fields_do_not_fill(tmp_path):
    """The run places a described grid against its shape; Test detection must agree with it, or it shows a
    smaller grid as fine and the plate-wide run then refuses every well."""
    rendered, record = build_well(timepoint="0h")
    root = _write_plate(tmp_path, rendered, record)
    (root / "in" / "acquisition.json").unlink()
    pattern = {"grid": [3, 2], "scan": "top-left-row", "overlap_percent": 10.0, "pixel_size_um": 4.3}
    result = _preview((root / "in" / f"{WELL}.zip", tmp_path), tmp_path, pattern=pattern)
    assert result.status == nr.INCOMPLETE_GRID
