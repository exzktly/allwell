"""The in-place append journal (:mod:`util.zip_append`), on real archive shapes.

Every check that matters is made on the archive's bytes: an undo is correct
only when the file hashes the same as before the append began, not merely
when it opens. The fixtures carry what real well archives carry — a
``.DS_Store``, a directory entry, an AppleDouble sidecar — and ZIP64 archives
of both kinds (an end record forced by member count, as a big well's is, and
by offsets past a lowered limit, as a >4 GB well's is).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from util import zip_append
from util.zip_append import (
    RUN_JOURNAL_NAME,
    AppendJournalError,
    AppendRun,
    journal_path,
    locate_tail,
    recover,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _messy(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("sub/"), b"")
        archive.writestr(".DS_Store", b"\x00\x01junk")
        archive.writestr("__MACOSX/._E_GFP_A01_f0_0h.tif", b"appledouble")
        for i in range(6):
            archive.writestr(f"E_GFP_A01_f{i}_0h.tif", bytes([i]) * (300 + i))
    return path


def _zip64_by_offset(path: Path) -> Path:
    """A real ZIP64 archive (ZIP64 extras, end record and locator) without 4 GB on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = zipfile.ZIP64_LIMIT
    zipfile.ZIP64_LIMIT = 64
    try:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(".DS_Store", b"junk")
            for i in range(5):
                archive.writestr(f"E_GFP_A01_f{i}_0h.tif", bytes([i]) * 200)
    finally:
        zipfile.ZIP64_LIMIT = saved
    assert b"PK\x06\x06" in path.read_bytes()[-200:]
    return path


def _zip64_by_count(path: Path) -> Path:
    """More than 65,535 members: the ZIP64 end record CPython writes for a big well."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for i in range(65_536):
            archive.writestr(f"m{i}.tif", b"x")
    assert b"PK\x06\x06" in path.read_bytes()[-200:]
    return path


SHAPES = {"messy": _messy, "zip64-offsets": _zip64_by_offset}


def _add(zf: zipfile.ZipFile) -> None:
    zf.writestr("E_GFP_A01_f0_24h.tif", b"new" * 400)
    zf.writestr("E_GFP_A01_f1_24h.tif", b"NEW" * 400)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_our_tail_is_where_zipfile_starts_its_central_directory(tmp_path, shape):
    archive = SHAPES[shape](tmp_path / "in" / "A01.zip")
    with zipfile.ZipFile(archive) as reader:
        assert locate_tail(archive).offset == reader.start_dir
    assert locate_tail(archive).length == archive.stat().st_size


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_an_append_rewrites_only_the_old_central_directory_onward(tmp_path, shape):
    archive = SHAPES[shape](tmp_path / "in" / "A01.zip")
    before = archive.read_bytes()
    floor = locate_tail(archive).offset
    with zipfile.ZipFile(archive) as reader:
        old = [(i.filename, i.header_offset, i.CRC) for i in reader.infolist()]
    with AppendRun(archive.parent) as run:
        run.append(archive, _add)
        run.commit()
    after = archive.read_bytes()
    assert after[:floor] == before[:floor]
    with zipfile.ZipFile(archive) as reader:
        assert [(i.filename, i.header_offset, i.CRC) for i in reader.infolist()][:len(old)] == old
        assert reader.read("E_GFP_A01_f1_24h.tif") == b"NEW" * 400
        assert reader.testzip() is None
    assert zip_append.pending_journals(archive.parent) == []


def test_a_member_count_zip64_archive_is_appended_and_undone(tmp_path):
    archive = _zip64_by_count(tmp_path / "in" / "A01.zip")
    original = _sha(archive)
    with pytest.raises(RuntimeError, match="after the append"):
        with AppendRun(archive.parent) as run:
            run.append(archive, _add)
            with zipfile.ZipFile(archive) as reader:
                assert len(reader.infolist()) == 65_538
            raise RuntimeError("a failure after the append")
    assert _sha(archive) == original


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_failure_part_way_through_a_member_restores_the_archive_byte_for_byte(tmp_path, shape):
    archive = SHAPES[shape](tmp_path / "in" / "A01.zip")
    original = _sha(archive)

    def half_then_fail(zf: zipfile.ZipFile) -> None:
        zf.writestr("E_GFP_A01_f0_24h.tif", b"first" * 100)
        with zf.open("E_GFP_A01_f1_24h.tif", "w") as handle:
            handle.write(b"half a member")
            raise OSError("No space left on device")

    run = AppendRun(archive.parent)
    with pytest.raises(OSError, match="No space"):
        with run:
            run.append(archive, half_then_fail)
    assert _sha(archive) == original
    assert run.rollback_lines and "restored" in run.rollback_lines[0]
    assert zip_append.pending_journals(archive.parent) == []


def test_the_journal_is_on_disk_with_the_old_tail_before_the_archive_is_touched(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = archive.read_bytes()
    tail = locate_tail(archive)
    seen = {}

    def look(zf: zipfile.ZipFile) -> None:
        journal = journal_path(archive)
        seen["journal"] = journal.exists()
        if seen["journal"]:
            header, _sep, saved = journal.read_bytes().partition(b"\n")
            seen["header"], seen["tail"] = json.loads(header), saved
        seen["archive_untouched"] = archive.read_bytes() == original
        _add(zf)

    with AppendRun(archive.parent) as run:
        run.append(archive, look)
        run.commit()
    assert seen["journal"] and seen["archive_untouched"]
    assert seen["tail"] == original[tail.offset:]
    assert seen["header"]["length"] == len(original)
    assert seen["header"]["tail_offset"] == tail.offset
    assert journal_path(archive).name == ".A01.zip.aw-append-journal"


def test_zipfile_is_never_allowed_below_the_old_central_directory(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)

    def overwrite_a_member(zf: zipfile.ZipFile) -> None:
        zf.fp.seek(10)
        zf.fp.write(b"clobber")

    with pytest.raises(AppendJournalError, match="before the old central directory"):
        with AppendRun(archive.parent) as run:
            run.append(archive, overwrite_a_member)
    assert _sha(archive) == original


def test_an_old_member_described_differently_afterwards_fails_the_append(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)

    def misdescribe(zf: zipfile.ZipFile) -> None:
        _add(zf)
        zf.filelist[3].CRC ^= 1          # the rewritten directory would lie about an old member

    with pytest.raises(AppendJournalError, match="is not where or what it was"):
        with AppendRun(archive.parent) as run:
            run.append(archive, misdescribe)
    assert _sha(archive) == original


def _crashed_run(archive: Path, *, then=None) -> None:
    """Leave *archive* the way a killed process would: journalled, appended, never committed."""
    run = AppendRun(archive.parent, preserve=[archive.parent / "acquisition.json"])
    run.__enter__()
    run.append(archive, _add)
    if then is not None:
        then()
    run._release()          # the process is gone: no rollback ran, nothing is held


def test_the_next_open_undoes_an_append_a_crash_left_behind(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)
    # Killed mid-member: the new central directory never made it to disk.
    _crashed_run(archive, then=lambda: os.truncate(archive, archive.stat().st_size - 300))
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(archive)
    result = recover(archive.parent)
    assert result.blocked == ""
    assert any("did not finish" in line for line in result.lines)
    assert _sha(archive) == original
    assert zip_append.pending_journals(archive.parent) == []
    assert recover(archive.parent).lines == []           # nothing left to do


def test_recovery_of_a_dead_process_s_run_restores_the_preserved_record(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    record = tmp_path / "in" / "acquisition.json"
    record.write_text('{"old": true}')
    original = _sha(archive)
    _crashed_run(archive, then=lambda: record.write_text('{"new": true}'))
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    run_journal = archive.parent / RUN_JOURNAL_NAME
    payload = json.loads(run_journal.read_text())
    payload["pid"] = int(dead.stdout)
    run_journal.write_text(json.dumps(payload))
    result = recover(archive.parent)
    assert result.blocked == ""
    assert _sha(archive) == original
    assert record.read_text() == '{"old": true}'


def test_recovery_leaves_a_running_append_alone(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    with AppendRun(archive.parent) as run:
        run.append(archive, _add)
        appended = _sha(archive)
        result = recover(archive.parent)
        assert "running in this window" in result.blocked
        assert _sha(archive) == appended                  # not rolled back under the run
        run.commit()


def test_a_live_process_on_this_host_is_not_undone(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    _crashed_run(archive)
    run_journal = archive.parent / RUN_JOURNAL_NAME
    payload = json.loads(run_journal.read_text())
    payload["pid"] = os.getppid()                         # alive, and not us
    run_journal.write_text(json.dumps(payload))
    appended = _sha(archive)
    result = recover(archive.parent)
    assert f"process {os.getppid()}" in result.blocked
    assert _sha(archive) == appended


def test_another_host_s_run_is_reported_not_undone(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    _crashed_run(archive)
    run_journal = archive.parent / RUN_JOURNAL_NAME
    payload = json.loads(run_journal.read_text())
    payload["host"] = "some-other-mac"
    run_journal.write_text(json.dumps(payload))
    result = recover(archive.parent)
    assert "'some-other-mac'" in result.blocked


def test_a_committed_append_s_leftover_journal_is_removed_and_the_append_kept(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    kept = {}
    with AppendRun(archive.parent) as run:
        run.append(archive, _add)
        kept["journal"] = journal_path(archive).read_bytes()
        run.commit()
    appended = _sha(archive)
    # The crash came after the commit (the run journal's deletion) and before
    # the per-archive journal's.
    journal_path(archive).write_bytes(kept["journal"])
    result = recover(archive.parent)
    assert any("had finished" in line for line in result.lines)
    assert _sha(archive) == appended
    assert not journal_path(archive).exists()


def test_a_damaged_journal_is_refused_and_the_archive_left_as_it_is(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    _crashed_run(archive)
    journal = journal_path(archive)
    journal.write_bytes(journal.read_bytes()[:-5] + b"XXXXX")
    appended = _sha(archive)
    with pytest.raises(AppendJournalError, match="damaged"):
        recover(archive.parent)
    assert _sha(archive) == appended
    assert journal.exists() and (archive.parent / RUN_JOURNAL_NAME).exists()


def test_an_archive_created_by_an_unfinished_run_is_removed(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    created = in_dir / "C03.zip"
    run = AppendRun(in_dir)
    run.__enter__()
    run.create(created, lambda: _messy(created))
    run._release()
    assert created.exists()
    recover(in_dir)
    assert not created.exists()


def test_an_archive_with_data_before_its_first_member_is_refused(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    archive.write_bytes(b"#!/bin/sh\nexit 0\n" + archive.read_bytes())
    with pytest.raises(AppendJournalError, match="data before its first member"):
        locate_tail(archive)
