"""The resume path must never delete an image it did not group.

`resolve_input_output`'s GROUPED branch handles the case a user reaches by
following the documented incremental workflow: drop a new timepoint's TIFs
beside a plate that already has `in/<WELL>.zip`, and press Run. It re-runs the
zipper over the dataset root and returns a callback that unlinks the loose
files once the pipeline exits cleanly.

It was the only one of the module's three zipper call sites with no post-check.
`_group_loose_tifs_in_place` and the `ZIP_ROOT` path both re-check
`has_well_content` and raise "WellPlateZipper ran but produced no well folders";
this one ran the zipper, ignored what it produced, and deleted the sources
regardless. Two ways that ended:

* **The images existed nowhere.** With the filename separator set wrong the
  zipper matched no well at all, nothing refused the run, the pipeline
  succeeded against the *old* archives, and the callback removed both source
  files.
* **The images existed and were never read.** With the separator right the
  zipper created `in/A01/` beside `in/A01.zip`, and
  `process_microscopy` reads `well_folders` only when there are no zips at all
  (`well_folders = find_well_folders(input_dir) if not well_zips else []`). So
  the run analysed zero new wells, reported success, and deleted the only
  copies the user had outside that unreachable folder.

`has_well_content` cannot be the check here the way it is for the siblings: on
this path the directory already holds well content — the pre-existing zips — so
it is True whatever the zipper did. The question asked instead is whether any
of *these* files' wells landed.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services.input_resolution_service import (
    _fold_well_folders_into_existing_zips, resolve_input_output)

SCHEMA = "plate:well:fov:timepoint:channel"


def _plate(tmp_path: Path, *, sep: str = "_") -> Path:
    """A finished plate with T1 archived, and T2 dropped loose beside it.

    The archives carry a `.DS_Store`, because real ones do and because the
    fold-in step has to carry members it does not understand rather than
    dropping them.
    """
    root = tmp_path / "Plate1"
    (root / "in").mkdir(parents=True)
    for well in ("A01", "B02"):
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as zf:
            zf.writestr(f"Plate1{sep}{well}{sep}F1{sep}T1{sep}DAPI.tif", b"t1")
            zf.writestr(".DS_Store", b"junk")
        (root / f"Plate1{sep}{well}{sep}F1{sep}T2{sep}DAPI.tif").write_bytes(b"t2")
    return root


def _members(root: Path, well: str) -> list[str]:
    return sorted(zipfile.ZipFile(root / "in" / f"{well}.zip").namelist())


def test_a_zipper_that_grouped_nothing_refuses_instead_of_deleting(tmp_path):
    """The separator is wrong, so no well is matched. Nothing may be removed."""
    root = _plate(tmp_path)

    with pytest.raises(ValueError) as caught:
        resolve_input_output(
            root, log_fn=lambda _m: None,
            filename_schema=SCHEMA, filename_sep="-",   # files use "_"
            move_source_tifs=False)

    assert "grouped into a well" in str(caught.value), (
        f"it refused, but not about the grouping: {caught.value}")
    survivors = sorted(p.name for p in root.glob("*.tif"))
    assert len(survivors) == 2, (
        "a run that grouped nothing removed the user's images anyway: "
        f"{survivors} left of 2")


def test_new_images_land_inside_the_archive_their_well_already_has(tmp_path):
    """The separator is right. The images must be readable by the pipeline.

    Asserted on the archive contents rather than on "the loose file is gone",
    because the defect this pins had the loose file gone *and* the images
    unreachable — the two are not the same success.
    """
    root = _plate(tmp_path)

    _grouped, _out, on_success = resolve_input_output(
        root, log_fn=lambda _m: None,
        filename_schema=SCHEMA, filename_sep="_", move_source_tifs=False)
    assert on_success is not None
    on_success()

    for well in ("A01", "B02"):
        members = _members(root, well)
        assert f"Plate1_{well}_F1_T2_DAPI.tif" in members, (
            f"{well}: the new timepoint never reached the archive the "
            f"pipeline reads. Members: {members}")
        assert f"Plate1_{well}_F1_T1_DAPI.tif" in members, (
            f"{well}: folding the new image in dropped the old one")
        assert ".DS_Store" in members, (
            f"{well}: the fold-in dropped a member it did not understand")

    leftover_folders = sorted(p.name for p in (root / "in").iterdir() if p.is_dir())
    assert not leftover_folders, (
        "a <WELL>/ folder was left beside its <WELL>.zip; process_microscopy "
        f"ignores folders whenever any zip exists, so these are unread: "
        f"{leftover_folders}")


def test_only_the_images_whose_well_landed_are_deleted(tmp_path):
    """One file parses to a well, one does not. Exactly one may be removed."""
    root = _plate(tmp_path)
    orphan = root / "notaplate-file.tif"
    orphan.write_bytes(b"unparseable")

    _grouped, _out, on_success = resolve_input_output(
        root, log_fn=lambda _m: None,
        filename_schema=SCHEMA, filename_sep="_", move_source_tifs=False)
    assert on_success is not None
    on_success()

    assert orphan.exists(), (
        "a filename that does not decompose under the run's schema was "
        "deleted; a schema mismatch must cost a failed run, not the images")


def test_a_loose_image_already_inside_the_archive_is_not_re_grouped(tmp_path):
    """The up-to-date check must read zip members, not just folders.

    It scanned directories only. In the zip-grouped layout — what a plate looks
    like after any completed run — that set is always empty, so every loose TIF
    read as unconsumed and the zipper was re-run against files already archived.
    """
    root = tmp_path / "Plate1"
    (root / "in").mkdir(parents=True)
    with zipfile.ZipFile(root / "in" / "A01.zip", "w") as zf:
        zf.writestr("Plate1_A01_F1_T1_DAPI.tif", b"t1")
    # The same image, still sitting at the root from a copy-mode run.
    (root / "Plate1_A01_F1_T1_DAPI.tif").write_bytes(b"t1")

    log: list[str] = []
    resolve_input_output(
        root, log_fn=log.append,
        filename_schema=SCHEMA, filename_sep="_", move_source_tifs=False)

    blob = "".join(log)
    assert "unconsumed" not in blob, (
        "an image already inside the archive was reported unconsumed and the "
        f"zipper was re-run over it. Log:\n{blob}")


def test_folding_a_new_folder_into_an_archive_keeps_what_it_did_not_archive(tmp_path):
    """The fold packs images; the folder holds more than images.

    `_fold_well_folders_into_existing_zips` exists because a `<WELL>/` folder
    beside a `<WELL>.zip` is invisible to `process_microscopy`, which reads
    `well_folders` only when there are no zips at all. Its first form removed
    the folder outright once the images were in the archive, which is the
    deletion `tests/test_no_unarchived_delete.py` forbids: an OME companion
    XML, an acquisition log or a mask subdirectory exists nowhere else.
    """
    grouped = tmp_path / "in"
    grouped.mkdir(parents=True)
    with zipfile.ZipFile(grouped / "A01.zip", "w") as zf:
        zf.writestr("Plate1_A01_F1_T1_DAPI.tif", b"old")

    folder = grouped / "A01"
    folder.mkdir()
    (folder / "Plate1_A01_F2_T1_DAPI.tif").write_bytes(b"new")
    (folder / "A01.companion.ome").write_text("<OME/>")
    (folder / "acquisition.log").write_text("started")

    folded = _fold_well_folders_into_existing_zips(grouped, log_fn=None)

    assert folded == 1
    with zipfile.ZipFile(grouped / "A01.zip") as zf:
        assert sorted(zf.namelist()) == [
            "Plate1_A01_F1_T1_DAPI.tif", "Plate1_A01_F2_T1_DAPI.tif"]
    assert not (folder / "Plate1_A01_F2_T1_DAPI.tif").exists(), (
        "the archived image was left in the folder as well, so the same bytes "
        "now sit in two places and the next scan re-groups them")
    assert (folder / "A01.companion.ome").read_text() == "<OME/>", (
        "the companion XML was destroyed by the fold; the archive beside it "
        "holds only the images, so nothing anywhere has a copy")
    assert (folder / "acquisition.log").exists()


def test_a_folder_holding_only_images_already_archived_is_removed(tmp_path):
    """The other half: nothing is kept, so the folder does go."""
    grouped = tmp_path / "in"
    grouped.mkdir(parents=True)
    with zipfile.ZipFile(grouped / "A01.zip", "w") as zf:
        zf.writestr("Plate1_A01_F1_T1_DAPI.tif", b"old")

    folder = grouped / "A01"
    folder.mkdir()
    (folder / "Plate1_A01_F1_T1_DAPI.tif").write_bytes(b"old")

    assert _fold_well_folders_into_existing_zips(grouped, log_fn=None) == 0
    assert not folder.exists(), (
        "a folder whose every file is already in the archive was left behind, "
        "so the next scan sees a well folder that will never be read")
