"""Characterization tests for image grouping inside a well archive (audit A1).

``find_image_groups_in_zip`` parses channel/nuclear tokens out of member names.
These tests pin the exact grouping output — incomplete-group skipping, the
schema-channel slot, the substring fallback and basename dedup.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import process_microscopy as pm


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


# ── zip scan ─────────────────────────────────────────────────────────────
def test_find_image_groups_in_zip(tmp_path):
    dest = tmp_path / "dest"
    zp = tmp_path / "A1.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for n in ("exp_dapi_A1_F1_T1.tif", "exp_gfp_A1_F1_T1.tif",
                  "exp_rfp_A1_F1_T1.tif",
                  "exp_dapi_A1_F2_T1.tif", "exp_gfp_A1_F2_T1.tif"):  # F2 missing rfp
            z.writestr(n, b"")
    groups, needed = pm.find_image_groups_in_zip(zp, "dapi", ["gfp", "rfp"], dest)
    rel = [(n.relative_to(tmp_path).as_posix(),
            [f.relative_to(tmp_path).as_posix() for f in fl]) for n, fl in groups]
    assert rel == [
        ("dest/exp_dapi_A1_F1_T1.tif",
         ["dest/exp_gfp_A1_F1_T1.tif", "dest/exp_rfp_A1_F1_T1.tif"]),
    ]
    assert sorted(needed) == [
        "exp_dapi_A1_F1_T1.tif", "exp_gfp_A1_F1_T1.tif", "exp_rfp_A1_F1_T1.tif",
    ]


def test_find_image_groups_in_zip_dedup_basename(tmp_path, caplog):
    # Two members with the same basename → first wins, a warning is logged.
    dest = tmp_path / "dest"
    zp = tmp_path / "A1.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("a/exp_dapi_A1_F1_T1.tif", b"")
        z.writestr("b/exp_dapi_A1_F1_T1.tif", b"")   # duplicate basename
        z.writestr("exp_gfp_A1_F1_T1.tif", b"")
    with caplog.at_level(logging.WARNING):
        groups, needed = pm.find_image_groups_in_zip(zp, "dapi", ["gfp"], dest)
    assert len(groups) == 1
    assert any("duplicate basename" in r.getMessage() for r in caplog.records)


def test_find_image_groups_in_zip_substring_fallback(tmp_path):
    schema = ["experiment", "well", "fov", "timepoint"]  # no channel slot
    dest = tmp_path / "dest"
    zp = tmp_path / "A1.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("dapi_A1_F1_T1.tif", b"")
        z.writestr("gfp_A1_F1_T1.tif", b"")
    groups, needed = pm.find_image_groups_in_zip(zp, "dapi", ["gfp"], dest, schema=schema)
    rel = [(n.name, [f.name for f in fl]) for n, fl in groups]
    assert rel == [("dapi_A1_F1_T1.tif", ["gfp_A1_F1_T1.tif"])]
