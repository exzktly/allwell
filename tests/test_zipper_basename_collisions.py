"""Two source TIFs with one basename must not silently become one file.

``well_plate_zipper.folder_well`` placed every grouped file at
``<well>/<basename>`` with ``shutil.copy2`` / ``shutil.move`` and no existence
check. Under ``--recursive`` two subfolders can hold the same filename, and
either call simply overwrites: the first file's bytes were replaced by the
second's, and with ``--move-source-tifs`` the loser's original was gone from
disk too — one image destroyed with no copy anywhere. The line the Analyze log
parses then printed ``len(files)``, the source count, so the pipeline read one
image fewer than the run had just claimed to group.

First-write-wins instead — the rule the zip writers already apply to a duplicate
member name — the loser is left where it is, the collision is named on stderr
(which ``run_zipper`` folds into the Analyze log), and the count is what landed.

Qt-free, fast lane.
"""

from __future__ import annotations

import os

from pipeline import well_plate_zipper as wpz

_SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
_SEP = "_"
_NAME = "exp_GFP_A01_F001_24h.tif"


def _plate_with_a_basename_collision(root):
    for sub, payload in (("scan1", b"first"), ("scan2", b"second")):
        folder = root / sub
        folder.mkdir(parents=True)
        (folder / _NAME).write_bytes(payload)
    (root / "scan1" / "exp_GFP_A01_F002_24h.tif").write_bytes(b"other")
    return root


def test_a_colliding_copy_is_skipped_and_counted_honestly(tmp_path, capsys):
    src = _plate_with_a_basename_collision(tmp_path / "plate")
    out = tmp_path / "out"
    out.mkdir()

    grouped = wpz.group_by_well(str(src), True, _SCHEMA, _SEP)
    assert len(grouped["A01"]) == 3
    capsys.readouterr()

    wpz.folder_well("A01", grouped["A01"], str(out))
    captured = capsys.readouterr()

    placed = sorted(p.name for p in (out / "A01").iterdir())
    assert len(placed) == 2
    # The progress line the Analyze tab parses reports survivors, not sources.
    assert "A01: 2 files" in captured.out
    assert "A01: 3 files" not in captured.out
    # And the file that did not make it is named, with both paths.
    assert "was not grouped" in captured.err
    assert _NAME in captured.err


def test_move_mode_leaves_the_loser_s_original_on_disk(tmp_path, capsys):
    """The destructive half. ``shutil.move`` onto an existing name destroyed the
    bytes it landed on *and* removed the source, so one image existed nowhere
    afterwards."""
    src = _plate_with_a_basename_collision(tmp_path / "plate")
    out = tmp_path / "out"
    out.mkdir()

    grouped = wpz.group_by_well(str(src), True, _SCHEMA, _SEP)
    capsys.readouterr()
    wpz.folder_well("A01", grouped["A01"], str(out), move=True)
    capsys.readouterr()

    survivors = {p.read_bytes() for p in (out / "A01").iterdir()}
    left_behind = {p.read_bytes() for p in src.rglob("*.tif")}
    # Every byte that existed before the call still exists somewhere after it.
    assert {b"first", b"second", b"other"} <= (survivors | left_behind)


def test_a_file_already_in_its_own_well_folder_is_not_a_collision(tmp_path, capsys):
    """``--output-dir`` equal to ``--search-dir`` walked recursively re-finds the
    files a previous pass already placed. Copying a file onto itself raises
    ``SameFileError``; treating it as a collision would warn about a file that
    is exactly where it belongs."""
    src = tmp_path / "plate"
    (src / "A01").mkdir(parents=True)
    (src / "A01" / _NAME).write_bytes(b"already placed")

    grouped = wpz.group_by_well(str(src), True, _SCHEMA, _SEP)
    capsys.readouterr()
    wpz.folder_well("A01", grouped["A01"], str(src))
    captured = capsys.readouterr()

    assert "A01: 1 files" in captured.out
    assert "was not grouped" not in captured.err
    assert (src / "A01" / _NAME).read_bytes() == b"already placed"


def test_the_ordinary_non_colliding_case_is_unchanged(tmp_path, capsys):
    src = tmp_path / "plate"
    src.mkdir()
    for name in (_NAME, "exp_GFP_A01_F002_24h.tif"):
        (src / name).write_bytes(b"img")
    out = tmp_path / "out"
    out.mkdir()

    grouped = wpz.group_by_well(str(src), False, _SCHEMA, _SEP)
    capsys.readouterr()
    wpz.folder_well("A01", grouped["A01"], str(out))
    captured = capsys.readouterr()

    assert "A01: 2 files" in captured.out
    assert captured.err == ""
    assert len(list((out / "A01").iterdir())) == 2
    assert os.path.isfile(src / _NAME)          # copy mode leaves the sources
