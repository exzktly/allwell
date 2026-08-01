"""A well folder is deleted as soon as its own archive is published.

Grouping a plate produces one ``<well>/`` folder per well; the zip-up step that
follows turns each into ``<well>.zip``. The deletion of the folder used to be
deferred to an ``on_pipeline_success`` hook that runs after the whole pipeline
finishes, so for the entire run the input dir held both ``<well>/`` and
``<well>.zip`` for every well — two full copies of the plate on the user's own
volume, at peak, on exactly the runs where the input is largest.

They are now consumed one at a time, each folder removed the moment its archive
lands. ``_zip_well_folder`` publishes through ``atomic_zip``, which renames a
verified archive into place or leaves nothing at all, so a folder is only ever
removed once a complete archive sits beside it. Two folders are deliberately
left alone: one holding no TIFs (there is no archive to replace it with, and it
may hold other content) and one whose well already had a user-written zip (that
archive was not written by this run and has not been verified against the
folder).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services.remote_runner import LocalInputMode, stage_local_zips


def _well_folder(root: Path, well: str, channels=("NIR", "GFP")) -> Path:
    d = root / well
    d.mkdir()
    for ch in channels:
        (d / f"{well}_{ch}_F001.tif").write_bytes(b"\xff" * 64)
    return d


def _stage(root: Path, **kw):
    return stage_local_zips(
        root, filename_schema="well:channel:fov", filename_sep="_", **kw)


def test_every_folder_is_gone_and_every_zip_is_there(tmp_path):
    _well_folder(tmp_path, "A01")
    _well_folder(tmp_path, "A02")

    zipped = _stage(tmp_path)

    assert zipped.mode is LocalInputMode.FOLDER
    assert sorted(p.name for p in zipped.well_zips) == ["A01.zip", "A02.zip"]
    assert not (tmp_path / "A01").exists()
    assert not (tmp_path / "A02").exists()
    with zipfile.ZipFile(tmp_path / "A01.zip") as zf:
        assert sorted(zf.namelist()) == ["A01_GFP_F001.tif", "A01_NIR_F001.tif"]


def test_the_plate_is_never_duplicated_beyond_the_well_in_hand(tmp_path, monkeypatch):
    """The property the deferral broke: peak disk, not just end state.

    After each well is zipped, at most one well may hold both a folder and an
    archive — the one just written, and only until the next statement removes
    it. Deferred deletion made that count climb to the whole plate.
    """
    import services.remote_runner as rr

    for well in ("A01", "A02", "A03"):
        _well_folder(tmp_path, well)

    real = rr._zip_well_folder
    both_seen: list[int] = []

    def _spy(folder: Path, target: Path) -> int:
        n = real(folder, target)
        both_seen.append(sum(
            1 for w in ("A01", "A02", "A03")
            if (tmp_path / w).is_dir() and (tmp_path / f"{w}.zip").exists()
        ))
        return n

    monkeypatch.setattr(rr, "_zip_well_folder", _spy)
    _stage(tmp_path)

    assert both_seen == [1, 1, 1], both_seen


def test_a_folder_with_no_tifs_keeps_its_contents(tmp_path):
    _well_folder(tmp_path, "A01")
    junk = tmp_path / "A02"
    junk.mkdir()
    (junk / "notes.txt").write_text("keep me")

    _stage(tmp_path)

    assert not (tmp_path / "A01").exists()
    assert (junk / "notes.txt").read_text() == "keep me"
    assert not (tmp_path / "A02.zip").exists()


def test_a_folder_whose_well_already_has_a_user_zip_is_left_alone(tmp_path):
    """That archive was not written by this run, so nothing here verified it."""
    _well_folder(tmp_path, "A01")
    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        zf.writestr("A01_NIR_F001.tif", b"from an earlier run")
    _well_folder(tmp_path, "A02")

    _stage(tmp_path)

    assert (tmp_path / "A01" / "A01_NIR_F001.tif").exists()
    with zipfile.ZipFile(tmp_path / "A01.zip") as zf:
        assert zf.namelist() == ["A01_NIR_F001.tif"]     # not rewritten
    assert not (tmp_path / "A02").exists()


def test_a_failure_partway_keeps_the_wells_already_archived(tmp_path, monkeypatch):
    """The rollback that used to run here would now destroy real data.

    It unlinked every zip written in the pass. With each folder consumed as its
    archive lands, that would delete the only remaining copy of every well that
    had already succeeded. A part-zipped plate is the correct resume state: the
    finished wells read as ZIP mode next time.
    """
    import services.remote_runner as rr

    for well in ("A01", "A02", "A03"):
        _well_folder(tmp_path, well)

    real = rr._zip_well_folder

    def _boom(folder: Path, target: Path) -> int:
        if folder.name == "A03":
            raise OSError("drive yanked mid-zip")
        return real(folder, target)

    monkeypatch.setattr(rr, "_zip_well_folder", _boom)
    with pytest.raises(OSError, match="drive yanked"):
        _stage(tmp_path)

    # The two finished wells survive, as archives.
    for well in ("A01", "A02"):
        assert (tmp_path / f"{well}.zip").exists()
        assert not (tmp_path / well).exists()
    # The one that failed still has its folder — atomic_zip published nothing.
    assert (tmp_path / "A03" / "A03_NIR_F001.tif").exists()
    assert not (tmp_path / "A03.zip").exists()
