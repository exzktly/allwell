"""``util.dir_scan`` answers "files or folders?" out of the directory listing.

The module exists because ``os.stat`` is cheap on POSIX and expensive on
Windows — CPython opens a handle there — so a listing plus a ``Path.is_file()``
per entry is a per-file round trip on any network or cloud-synced volume. The
saving is only worth having if the answers are the ones ``pathlib`` gave, so
that is what this file measures: the classification, the ordering, the symlink
cases, and what happens to a directory that cannot be read at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from util.dir_scan import files_and_subdirs, files_in, subdirs_in


def _tree(root: Path) -> None:
    (root / "b.tif").write_bytes(b"II*\0")
    (root / "a.tif").write_bytes(b"II*\0")
    (root / "d_folder").mkdir()
    (root / "c_folder").mkdir()


def test_files_and_folders_are_split_and_each_is_in_path_order(tmp_path):
    """Sorted as paths, which is what the replaced ``sorted(folder.glob("*"))``
    did — ``PurePath`` comparison casefolds on Windows and does not on POSIX, so
    sorting the bare names would reorder a mixed-case plate on Windows only."""
    _tree(tmp_path)
    files, subdirs = files_and_subdirs(tmp_path)

    assert [p.name for p in files] == ["a.tif", "b.tif"]
    assert [p.name for p in subdirs] == ["c_folder", "d_folder"]
    # Real paths under the folder asked about, not bare names — callers pass
    # them straight to `open`, `zipfile.ZipFile` and `Path.stat`.
    assert all(p.parent == tmp_path for p in files + subdirs)
    assert files_in(tmp_path) == files
    assert subdirs_in(tmp_path) == subdirs


def test_the_classification_is_the_one_pathlib_gives(tmp_path):
    """Including the symlink cases, which are the whole reason ``stat`` is used.

    ``Path.is_file`` follows symlinks, so a link to a file is a file and a
    dangling link is neither a file nor a directory — and a dangling ``*.tif``
    link is not a hypothetical here: ``tif_files_in``'s docstring records one
    reaching the zipper as an input image. ``DirEntry.is_file`` follows links
    too, which is why this holds; asserted rather than assumed because a
    ``follow_symlinks=False`` anywhere in the module would silently reclassify
    every link in a plate.
    """
    (tmp_path / "real.tif").write_bytes(b"II*\0")
    (tmp_path / "real_folder").mkdir()
    os.symlink(tmp_path / "real.tif", tmp_path / "link_to_file.tif")
    os.symlink(tmp_path / "real_folder", tmp_path / "link_to_folder")
    os.symlink(tmp_path / "gone.tif", tmp_path / "dangling.tif")

    files, subdirs = files_and_subdirs(tmp_path)
    names = {p.name for p in files} | {p.name for p in subdirs}

    assert {p.name for p in files} == {"real.tif", "link_to_file.tif"}
    assert {p.name for p in subdirs} == {"real_folder", "link_to_folder"}
    assert "dangling.tif" not in names
    # The same verdicts pathlib reaches, entry by entry.
    for entry in sorted(tmp_path.iterdir()):
        assert (entry in files) == entry.is_file()
        assert (entry in subdirs) == entry.is_dir()


def test_one_enumeration_and_no_stat_at_all(tmp_path):
    """The whole reason the module exists, and it needs both halves counted.

    One ``os.scandir`` is the cheap half to assert and the easy one to satisfy
    while missing the point: a helper that enumerated once and then called
    ``Path.is_file()`` on each entry would pass a scandir count, return
    byte-identical lists, and reintroduce exactly the per-file round trip this
    replaces. So the stat count is the assertion that matters, and it is zero.

    Both kinds of entry are in the fixture, because the classification is two
    questions and only the file half is covered end to end by
    ``test_input_scan_costs_no_stat_per_file``. A regression that stat-ed every
    entry to answer ``is_dir`` — the well-folder layout — would otherwise be
    invisible to the whole suite.

    The zero is exact rather than a budget, and it assumes the filesystem under
    ``tmp_path`` reports an entry type with the listing: ``d_type`` on POSIX,
    the ``WIN32_FIND_DATA`` attributes on Windows. One that reports neither
    makes ``DirEntry.is_file`` fall back to a stat and would fail this
    honestly — the saving really is unavailable there, and that is worth being
    told rather than absorbed into a tolerance.
    """
    _tree(tmp_path)
    for i in range(6):
        (tmp_path / f"extra{i}.tif").write_bytes(b"II*\0")
        (tmp_path / f"well{i}").mkdir()

    real_scandir, real_stat = os.scandir, os.stat
    listings: list[str] = []
    stats: list[str] = []

    def counting_scandir(path, *args, **kwargs):
        listings.append(str(path))
        return real_scandir(path, *args, **kwargs)

    def counting_stat(path, *args, **kwargs):
        stats.append(str(path))
        return real_stat(path, *args, **kwargs)

    os.scandir, os.stat = counting_scandir, counting_stat
    try:
        files, subdirs = files_and_subdirs(tmp_path)
    finally:
        os.scandir, os.stat = real_scandir, real_stat

    assert listings == [str(tmp_path)]
    assert stats == [], f"{len(stats)} stat call(s) for {len(files) + len(subdirs)} entries"
    # The counter is live: the same question asked the old way does register.
    os.stat = counting_stat
    try:
        for path in files + subdirs:
            path.is_file()
    finally:
        os.stat = real_stat
    assert len(stats) >= len(files) + len(subdirs)


def test_a_directory_that_cannot_be_listed_raises(tmp_path):
    """The error reaches the caller; it is not turned into "empty folder".

    Every call site had its own answer to an unreadable directory before this
    module existed — some report it, some treat it as no input — and swallowing
    it here would have silently given all of them the second answer. An empty
    plate and a plate nobody can read are not the same fact, and telling a user
    their folder holds no images when it could not be opened is exactly the
    silent substitution the pipeline is not allowed to make.
    """
    missing = tmp_path / "not_there"
    with pytest.raises(OSError):
        files_and_subdirs(missing)

    unreadable = tmp_path / "locked"
    unreadable.mkdir()
    (unreadable / "a.tif").write_bytes(b"II*\0")
    os.chmod(unreadable, 0o000)
    try:
        if os.access(unreadable, os.R_OK):
            pytest.skip("running as a user that ignores directory permissions")
        with pytest.raises(OSError):
            files_and_subdirs(unreadable)
    finally:
        os.chmod(unreadable, 0o755)


def test_the_classification_comes_from_the_listing_snapshot(tmp_path):
    """An entry deleted after the listing is still reported, and that is fine.

    ``os.scandir`` carries the entry type with the listing — ``d_type`` on
    POSIX, the ``WIN32_FIND_DATA`` attributes on Windows — which is exactly why
    classifying costs nothing, and it means the answer describes the directory
    as it was read rather than as it is now. ``Path.is_file`` re-asked the
    kernel per entry and would have noticed the deletion.

    Pinned because the difference is real and the fix for it would be a stat per
    file, i.e. the defect this module exists to remove. It is also narrower than
    it looks: any listing is a snapshot, and a file that vanishes a microsecond
    after ``Path.is_file`` returned True is reported the same way. Every caller
    already handles a path that has gone by the time it is opened.

    Not asserted the other way round: a filesystem that does not supply
    ``d_type`` makes ``DirEntry.is_file`` fall back to a stat, and would drop
    the entry. Either answer is correct here; what must not change is that no
    stat is spent to force one.
    """
    (tmp_path / "keep.tif").write_bytes(b"II*\0")
    (tmp_path / "vanishes.tif").write_bytes(b"II*\0")

    real = os.scandir

    def scandir_then_delete(path, *args, **kwargs):
        entries = list(real(path, *args, **kwargs))
        (tmp_path / "vanishes.tif").unlink()

        class _Snapshot:
            def __enter__(self):
                return iter(entries)

            def __exit__(self, *exc):
                return False

        return _Snapshot()

    os.scandir = scandir_then_delete
    try:
        names = [p.name for p in files_in(tmp_path)]
    finally:
        os.scandir = real

    assert "keep.tif" in names
    assert names in (["keep.tif"], ["keep.tif", "vanishes.tif"])
