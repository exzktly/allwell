"""Tests for the FOV-level incremental merge helpers in ``pipeline.io``.

``read_done_fov_keys`` / ``merge_records_into_csv`` / ``merge_images_into_zip``
are the primitives an ``--incremental`` run uses to absorb only the newly-added
FOV-groups into an already-processed well (new rows into the per-well CSV, new
images into ``<well>_out.zip``) without re-segmenting work already on disk.

``pipeline.io`` imports the imaging stack at module load, so these are skipped
where it isn't installed (they run in CI, which has the full stack).
"""

import csv
import zipfile

import pytest

pytest.importorskip("imageio")
pytest.importorskip("tifffile")

from pipeline.io import (  # noqa: E402
    merge_images_into_zip,
    merge_records_into_csv,
    read_done_fov_keys,
)


def _rows(csv_path):
    with open(csv_path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_read_done_fov_keys(tmp_path):
    p = tmp_path / "m_A01.csv"
    merge_records_into_csv(p, [
        {"well": "A01", "fov": "1", "timepoint": "T01", "nucleus_id": 1},
        {"well": "A01", "fov": "1", "timepoint": "T02", "nucleus_id": 1},
    ])
    assert read_done_fov_keys(p) == {("1", "T01"), ("1", "T02")}
    assert read_done_fov_keys(tmp_path / "missing.csv") == set()


def test_merge_records_appends_only_new_keys(tmp_path):
    p = tmp_path / "m_A01.csv"
    base = [
        {"well": "A01", "fov": "1", "timepoint": "T01", "nucleus_id": 1, "area_px": 10},
        {"well": "A01", "fov": "1", "timepoint": "T01", "nucleus_id": 2, "area_px": 20},
    ]
    assert merge_records_into_csv(p, base) == 2

    nxt = [
        # duplicate (same well/fov/tp/nucleus) → skipped
        {"well": "A01", "fov": "1", "timepoint": "T01", "nucleus_id": 1, "area_px": 10},
        # new FOV, and a brand-new column
        {"well": "A01", "fov": "2", "timepoint": "T01", "nucleus_id": 1, "area_px": 30,
         "shape_area": 99},
    ]
    assert merge_records_into_csv(p, nxt) == 1

    rows = _rows(p)
    assert [r["fov"] for r in rows] == ["1", "1", "2"]      # existing order preserved
    assert "shape_area" in rows[0].keys()                   # column unioned
    assert rows[0]["shape_area"] == ""                      # backfilled for old rows
    assert read_done_fov_keys(p) == {("1", "T01"), ("2", "T01")}
    # idempotent
    assert merge_records_into_csv(p, nxt) == 0
    assert len(_rows(p)) == 3


def test_merge_records_fresh_write(tmp_path):
    p = tmp_path / "m_C03.csv"
    assert merge_records_into_csv(p, [
        {"well": "C03", "fov": "1", "timepoint": "T01", "nucleus_id": 1},
    ]) == 1
    assert p.exists()
    assert len(_rows(p)) == 1


def test_merge_images_into_zip(tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    (imgs / "A01_1_labels.tif").write_bytes(b"x" * 32)
    z = tmp_path / "A01_out.zip"

    assert merge_images_into_zip(z, imgs) == 1            # fresh
    (imgs / "A01_2_labels.tif").write_bytes(b"y" * 32)
    assert merge_images_into_zip(z, imgs) == 1            # adds only the new one
    assert set(zipfile.ZipFile(z).namelist()) == {
        "A01_1_labels.tif", "A01_2_labels.tif"}
    assert merge_images_into_zip(z, imgs) == 0            # idempotent
    assert zipfile.is_zipfile(z)


def test_merge_images_keeps_existing_member_on_collision(tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    z = tmp_path / "A01_out.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("A01_1_labels.tif", b"original")
    (imgs / "A01_1_labels.tif").write_bytes(b"replacement")

    assert merge_images_into_zip(z, imgs) == 0
    assert zipfile.ZipFile(z).read("A01_1_labels.tif") == b"original"
