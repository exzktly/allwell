"""``replace_folder_with_archive`` deletes names, so it must compare names.

It removes exactly the files an archive now holds and leaves the folder standing
if anything else is still in it. Its identity for "is this file one of the ones
that was archived?" was ``Path.resolve()`` on both sides, which follows the
final component — so a symlink and the file it points at came back as one path,
and that conflation lost something in both directions:

* A symlink *inside* the folder pointing at an archived file resolved equal to
  it, so it was not counted among the files left behind. The folder looked empty
  of un-archived content and was removed, taking a name the archive holds no
  member for.
* An archived path that is *itself* a symlink out of the folder resolved to
  somewhere outside, so it was dropped from the archived set and then reported
  back as ``kept`` — a caller's "N file(s) not archived" log line naming a file
  that was archived — and the link was never removed.

Symlink farms are how large plates are usually presented to this pipeline (the
raw TIFs live on another volume), so neither shape is exotic here.

Qt-free, fast lane.
"""

from __future__ import annotations

import os

import pytest

from util.zip_write import replace_folder_with_archive


def _tif(path, payload=b"img"):
    path.write_bytes(payload)
    return path


def test_a_link_the_archive_has_no_member_for_survives_the_folder_removal(tmp_path):
    folder = tmp_path / "A01"
    folder.mkdir()
    _tif(folder / "real.tif")
    os.symlink("real.tif", folder / "alias.tif")

    # Only real.tif went into the archive; "alias.tif" is not a member of it.
    outcome = replace_folder_with_archive(folder, [folder / "real.tif"])

    assert outcome.kept == ("alias.tif",)
    assert outcome.folder_removed is False
    assert folder.is_dir()
    assert (folder / "alias.tif").is_symlink()
    assert not (folder / "real.tif").exists()      # that one really was archived


def test_an_archived_link_out_of_the_folder_is_not_reported_as_kept(tmp_path):
    folder = tmp_path / "B02"
    folder.mkdir()
    outside = _tif(tmp_path / "elsewhere.tif")
    os.symlink(outside, folder / "linked.tif")     # zf.write follows it: archived
    (folder / "notes.txt").write_text("keep me")

    outcome = replace_folder_with_archive(folder, [folder / "linked.tif"])

    assert outcome.kept == ("notes.txt",)          # not ("linked.tif", "notes.txt")
    assert not (folder / "linked.tif").exists()    # the link is gone…
    assert outside.exists()                        # …and its target is untouched


def test_a_second_hard_link_to_an_archived_file_is_a_second_entry(tmp_path):
    folder = tmp_path / "C03"
    folder.mkdir()
    _tif(folder / "first.tif")
    try:
        os.link(folder / "first.tif", folder / "second.tif")
    except OSError:                                 # pragma: no cover - platform
        pytest.skip("this filesystem does not support hard links")

    outcome = replace_folder_with_archive(folder, [folder / "first.tif"])

    # The archive holds a member called "first.tif" and none called
    # "second.tif", so the second name is content left behind.
    assert outcome.kept == ("second.tif",)
    assert folder.is_dir()
    assert (folder / "second.tif").exists()


def test_a_broken_link_left_behind_keeps_the_folder(tmp_path):
    folder = tmp_path / "D04"
    folder.mkdir()
    _tif(folder / "real.tif")
    target = _tif(tmp_path / "gone.tif")
    os.symlink(target, folder / "dangling.tif")
    target.unlink()

    outcome = replace_folder_with_archive(folder, [folder / "real.tif"])

    assert outcome.kept == ("dangling.tif",)
    assert os.path.lexists(folder / "dangling.tif")


def test_the_ordinary_case_is_unchanged(tmp_path):
    """The control: no links at all. Everything archived is removed, the
    remainder is reported, and a folder emptied of everything is gone."""
    folder = tmp_path / "E05"
    folder.mkdir()
    _tif(folder / "a.tif")
    _tif(folder / "b.tif")
    (folder / "acquisition.log").write_text("companion")

    partial = replace_folder_with_archive(folder, [folder / "a.tif", folder / "b.tif"])
    assert partial.kept == ("acquisition.log",)
    assert partial.folder_removed is False

    (folder / "acquisition.log").unlink()
    empty = tmp_path / "F06"
    empty.mkdir()
    _tif(empty / "only.tif")
    assert replace_folder_with_archive(empty, [empty / "only.tif"]).folder_removed is True
    assert not empty.exists()


def test_a_path_outside_the_folder_is_still_ignored(tmp_path):
    """The safety rule this function has always carried: it deletes a caller's
    own inputs, so a mistaken path must not reach outside the folder."""
    folder = tmp_path / "G07"
    folder.mkdir()
    _tif(folder / "inside.tif")
    outside = _tif(tmp_path / "outside.tif")

    replace_folder_with_archive(folder, [folder / "inside.tif", outside])
    assert outside.exists()
