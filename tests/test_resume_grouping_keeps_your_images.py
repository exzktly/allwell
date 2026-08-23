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


# ── identity is the bytes, not the name ─────────────────────────────────────

SCHEMA_CWFT = "experiment:channel:well:fov:timepoint"
IMG = "Plate1_DAPI_A01_F1_T1.tif"


def _resume_plate(tmp_path, *, loose: bytes, archived: bytes) -> Path:
    root = tmp_path / "Plate1"
    (root / "in").mkdir(parents=True)
    with zipfile.ZipFile(root / "in" / "A01.zip", "w") as zf:
        zf.writestr(IMG, archived)
    (root / IMG).write_bytes(loose)
    return root


def test_a_re_exported_image_is_not_mistaken_for_the_one_it_replaces(tmp_path):
    """The reuse predicate here is "is this loose TIF already grouped?".

    Answering it from the basename alone keys it on less than what invalidates
    the answer. A user who re-exports a plate with a corrected exposure writes
    the same filenames, so every corrected image read as already consumed: the
    archive kept the old pixels, the pipeline analysed them and reported
    success, and the loose originals were deleted as redundant. Nothing
    anywhere then held the corrected data.
    """
    root = _resume_plate(tmp_path, loose=b"NEW-CORRECTED", archived=b"OLD-WRONG")

    _grouped, _out, on_success = resolve_input_output(
        root, log_fn=lambda _m: None,
        filename_schema=SCHEMA_CWFT, filename_sep="_", move_source_tifs=False)
    if on_success is not None:
        on_success()

    with zipfile.ZipFile(root / "in" / "A01.zip") as zf:
        assert zf.read(IMG) == b"NEW-CORRECTED", (
            "the archive kept the pixels the user replaced, so the run "
            "analyses the old exposure and reports success")


def test_a_genuinely_redundant_copy_is_still_tidied_away(tmp_path):
    """The other direction, or the fix would leave every copy-mode run's
    sources behind for ever."""
    root = _resume_plate(tmp_path, loose=b"SAME", archived=b"SAME")

    _grouped, _out, on_success = resolve_input_output(
        root, log_fn=lambda _m: None,
        filename_schema=SCHEMA_CWFT, filename_sep="_", move_source_tifs=False)
    if on_success is not None:
        on_success()

    assert not (root / IMG).exists(), (
        "a byte-identical copy of an already-archived image was kept, so a "
        "copy-mode plate never sheds its sources")


# ── what the fold packs, and what it leaves alone ───────────────────────────

def test_the_fold_does_not_flatten_a_subdirectory_into_the_input_archive(tmp_path):
    """A `masks/` folder is not run input.

    Packing it with `arcname=q.name` both destroys it as an identifiable
    artefact and offers its contents back to the next scan as images to
    segment. `_consolidate_mixed_to_zips` next door reads the folder's top
    level only; this must agree with it.
    """
    grouped = tmp_path / "in"
    grouped.mkdir(parents=True)
    with zipfile.ZipFile(grouped / "A01.zip", "w") as zf:
        zf.writestr("P_DAPI_A01_F1_T1.tif", b"one")

    folder = grouped / "A01"
    (folder / "masks").mkdir(parents=True)
    (folder / "P_DAPI_A01_F2_T1.tif").write_bytes(b"two")
    (folder / "masks" / "P_DAPI_A01_F1_T1_mask.tif").write_bytes(b"mask")

    _fold_well_folders_into_existing_zips(grouped, log_fn=None)

    with zipfile.ZipFile(grouped / "A01.zip") as zf:
        assert sorted(zf.namelist()) == [
            "P_DAPI_A01_F1_T1.tif", "P_DAPI_A01_F2_T1.tif"]
    assert (folder / "masks" / "P_DAPI_A01_F1_T1_mask.tif").exists(), (
        "the mask subdirectory was emptied into the well's input archive")


def test_the_fold_replaces_a_member_whose_bytes_changed(tmp_path):
    """The folder is what the zipper just wrote; where they disagree it wins."""
    grouped = tmp_path / "in"
    grouped.mkdir(parents=True)
    with zipfile.ZipFile(grouped / "A01.zip", "w") as zf:
        zf.writestr("P_DAPI_A01_F1_T1.tif", b"OLD")

    folder = grouped / "A01"
    folder.mkdir()
    (folder / "P_DAPI_A01_F1_T1.tif").write_bytes(b"NEW")

    _fold_well_folders_into_existing_zips(grouped, log_fn=None)

    with zipfile.ZipFile(grouped / "A01.zip") as zf:
        assert zf.read("P_DAPI_A01_F1_T1.tif") == b"NEW"


def test_the_fold_keeps_a_carried_members_storage_and_timestamp(tmp_path):
    """Re-deflating and re-stamping churns bytes nothing asked to change.

    `ome_explode` writes input archives `ZIP_STORED` on purpose, and both the
    remote upload cache key and rsync's size-and-mtime quick-skip read member
    timestamps — so re-writing them forces a full re-upload of a well that
    gained one image.
    """
    grouped = tmp_path / "in"
    grouped.mkdir(parents=True)
    stamp = (2020, 1, 2, 3, 4, 6)
    with zipfile.ZipFile(grouped / "A01.zip", "w", zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("P_DAPI_A01_F1_T1.tif", date_time=stamp)
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"one")

    folder = grouped / "A01"
    folder.mkdir()
    (folder / "P_DAPI_A01_F2_T1.tif").write_bytes(b"two")

    _fold_well_folders_into_existing_zips(grouped, log_fn=None)

    with zipfile.ZipFile(grouped / "A01.zip") as zf:
        carried = zf.getinfo("P_DAPI_A01_F1_T1.tif")
    assert carried.compress_type == zipfile.ZIP_STORED, (
        "a stored member was re-deflated by a fold that only added a file")
    assert carried.date_time == stamp, (
        "a carried member's timestamp was reset, which churns the upload "
        "cache key for a well that gained one image")


def test_one_unreadable_archive_does_not_stop_the_wells_after_it(tmp_path):
    """The `swallow` was around the whole loop.

    A single bad archive aborted folding for every well after it at DEBUG
    level. Those wells keep a `<WELL>/` folder the pipeline never reads, while
    their loose sources are still deleted as consumed — a run that reports
    success having analysed nothing new for most of the plate.
    """
    grouped = tmp_path / "in"
    grouped.mkdir(parents=True)
    (grouped / "A01.zip").write_bytes(b"not a zip at all")
    (grouped / "A01").mkdir()
    (grouped / "A01" / "P_DAPI_A01_F1_T1.tif").write_bytes(b"one")

    with zipfile.ZipFile(grouped / "B02.zip", "w") as zf:
        zf.writestr("P_DAPI_B02_F1_T1.tif", b"one")
    (grouped / "B02").mkdir()
    (grouped / "B02" / "P_DAPI_B02_F2_T1.tif").write_bytes(b"two")

    _fold_well_folders_into_existing_zips(grouped, log_fn=None)

    with zipfile.ZipFile(grouped / "B02.zip") as zf:
        assert sorted(zf.namelist()) == [
            "P_DAPI_B02_F1_T1.tif", "P_DAPI_B02_F2_T1.tif"], (
            "B02 was never folded because A01's archive could not be read")
