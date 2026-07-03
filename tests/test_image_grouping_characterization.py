"""Characterization tests for the two image-grouping paths (audit A1).

``find_image_groups`` (directory rglob) and ``find_image_groups_in_zip`` (zip
namelist) independently parse channel/nuclear tokens out of filenames. Their
parsing MUST agree — segmentation correctness depends on it — yet they were
~90 lines of duplicated logic each. These tests pin the exact grouping output
(including incomplete-group skipping, the schema-channel slot, the substring
fallback, directory-awareness, and zip basename dedup) so the two can be routed
through one shared core with byte-identical behaviour.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import process_microscopy as pm


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


# ── directory scan ───────────────────────────────────────────────────────
def test_find_image_groups_dir_schema_channel(tmp_path):
    # channel slot = index 1 (experiment:channel:well:fov:timepoint)
    _touch(tmp_path / "exp_dapi_A1_F1_T1.tif")
    _touch(tmp_path / "exp_gfp_A1_F1_T1.tif")
    _touch(tmp_path / "exp_rfp_A1_F1_T1.tif")
    # incomplete (no rfp) — must be skipped
    _touch(tmp_path / "exp_dapi_A1_F2_T1.tif")
    _touch(tmp_path / "exp_gfp_A1_F2_T1.tif")
    # nuclear + fluor nested in a subdir — directory-aware resolution
    _touch(tmp_path / "sub" / "exp_dapi_B2_F1_T1.tif")
    _touch(tmp_path / "sub" / "exp_gfp_B2_F1_T1.tif")
    _touch(tmp_path / "sub" / "exp_rfp_B2_F1_T1.tif")

    groups = pm.find_image_groups(tmp_path, "dapi", ["gfp", "rfp"])
    rel = [(n.relative_to(tmp_path).as_posix(),
            [f.relative_to(tmp_path).as_posix() for f in fl]) for n, fl in groups]
    assert rel == [
        ("exp_dapi_A1_F1_T1.tif",
         ["exp_gfp_A1_F1_T1.tif", "exp_rfp_A1_F1_T1.tif"]),
        ("sub/exp_dapi_B2_F1_T1.tif",
         ["sub/exp_gfp_B2_F1_T1.tif", "sub/exp_rfp_B2_F1_T1.tif"]),
    ]


def test_find_image_groups_dir_substring_fallback(tmp_path):
    # No 'channel' slot → substring replacement of the nuclear token.
    schema = ["experiment", "well", "fov", "timepoint"]  # no channel
    _touch(tmp_path / "dapi_A1_F1_T1.tif")
    _touch(tmp_path / "gfp_A1_F1_T1.tif")
    groups = pm.find_image_groups(tmp_path, "dapi", ["gfp"], schema=schema)
    rel = [(n.name, [f.name for f in fl]) for n, fl in groups]
    assert rel == [("dapi_A1_F1_T1.tif", ["gfp_A1_F1_T1.tif"])]


def test_find_image_groups_dir_no_match_returns_empty(tmp_path):
    _touch(tmp_path / "exp_gfp_A1_F1_T1.tif")  # no nuclear file at all
    assert pm.find_image_groups(tmp_path, "dapi", ["gfp"]) == []


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
