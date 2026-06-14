"""Tests for folding loose image files into already-grouped well zips.

Covers ``services.input_resolution_service.append_loose_images_to_zips`` — the
step that lets a user drop extra TIFFs next to existing per-well zips and have
them absorbed (appended into the matching zip, or a fresh zip for a new well)
before the pipeline runs. Stdlib-only, so it runs without the imaging stack.
"""

import zipfile
from pathlib import Path

from services.input_resolution_service import (
    append_loose_images_to_zips,
    select_incremental_scope,
)

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_appends_new_file_into_existing_well_zip(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_A01_2_T01.tif").write_bytes(b"newfov")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 1
    assert wells == {"A01"}
    names = set(zipfile.ZipFile(tmp_path / "A01.zip").namelist())
    assert names == {"exp_w1_A01_1_T01.tif", "exp_w1_A01_2_T01.tif"}
    # original member preserved verbatim
    assert zipfile.ZipFile(tmp_path / "A01.zip").read("exp_w1_A01_1_T01.tif") == b"old"
    # loose source consumed
    assert not (tmp_path / "exp_w1_A01_2_T01.tif").exists()


def test_creates_zip_for_new_well(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_C03_1_T01.tif").write_bytes(b"newwell")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 1
    assert "C03" in wells
    assert (tmp_path / "C03.zip").exists()
    assert zipfile.ZipFile(tmp_path / "C03.zip").namelist() == ["exp_w1_C03_1_T01.tif"]
    assert not (tmp_path / "exp_w1_C03_1_T01.tif").exists()


def test_duplicate_member_keeps_existing_and_keeps_loose(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_A01_1_T01.tif").write_bytes(b"newer-dup")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 0
    assert wells == set()
    # existing member untouched, loose duplicate left in place (not deleted)
    assert zipfile.ZipFile(tmp_path / "A01.zip").read("exp_w1_A01_1_T01.tif") == b"old"
    assert (tmp_path / "exp_w1_A01_1_T01.tif").exists()


def test_unparsable_and_nonimage_files_untouched(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    bad = tmp_path / "exp_w1_ZZ9_1_T01.tif"   # ZZ9 is not a valid 96-well token
    bad.write_bytes(b"junk")
    notes = tmp_path / "readme.txt"
    notes.write_bytes(b"hi")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 0
    assert bad.exists() and notes.exists()


def test_no_op_when_no_well_zip_present(tmp_path):
    # Folder-grouped / loose-only layouts are left to the normal zipper path.
    (tmp_path / "exp_w1_A01_1_T01.tif").write_bytes(b"x")
    (tmp_path / "A01").mkdir()

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 0
    assert wells == set()
    assert (tmp_path / "exp_w1_A01_1_T01.tif").exists()


def _opts(tmp_path, **over):
    o = dict(filename_schema=SCHEMA, filename_sep=SEP,
             flatfield="gaussian", background="sliding_disk", background_well="")
    o.update(over)
    return o


def _stage_with_new_loose(tmp_path, *, well="A01", bg=False):
    _make_zip(tmp_path / f"{well}.zip", {f"exp_w1_{well}_1_T01.tif": b"old"})
    (tmp_path / f"exp_w1_{well}_2_T01.tif").write_bytes(b"new")


def test_scope_incremental_for_per_well_models(tmp_path):
    _stage_with_new_loose(tmp_path)
    opts = _opts(tmp_path)
    select_incremental_scope(opts, tmp_path)
    assert opts.get("incremental") is True
    assert not opts.get("force")


def test_scope_forces_full_for_basic_flatfield(tmp_path):
    _stage_with_new_loose(tmp_path)
    opts = _opts(tmp_path, flatfield="basic")
    select_incremental_scope(opts, tmp_path)
    assert opts.get("force") is True
    assert opts.get("incremental") is False


def test_scope_forces_full_when_empty_well_background_touched(tmp_path):
    # Append lands on the configured empty-well background well → models stale.
    _stage_with_new_loose(tmp_path, well="A01")
    opts = _opts(tmp_path, background="empty_well", background_well="A01")
    select_incremental_scope(opts, tmp_path)
    assert opts.get("force") is True


def test_scope_incremental_when_empty_well_background_untouched(tmp_path):
    # Empty-well model in use, but the new files are for a different well.
    _stage_with_new_loose(tmp_path, well="A01")
    opts = _opts(tmp_path, background="empty_well", background_well="H12")
    select_incremental_scope(opts, tmp_path)
    assert opts.get("incremental") is True
    assert not opts.get("force")


def test_scope_noop_when_nothing_appended(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})  # no loose files
    opts = _opts(tmp_path)
    select_incremental_scope(opts, tmp_path)
    assert not opts.get("incremental")
    assert not opts.get("force")


def test_result_zip_is_valid_and_short_well_token_canonicalised(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    # short, lowercase well token "a1" → canonical "A01"
    (tmp_path / "exp_w1_a1_2_T01.tif").write_bytes(b"new")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 1
    assert wells == {"A01"}
    assert zipfile.is_zipfile(tmp_path / "A01.zip")
    assert "exp_w1_a1_2_T01.tif" in zipfile.ZipFile(tmp_path / "A01.zip").namelist()
