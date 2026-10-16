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
import time
import zipfile
from pathlib import Path

import pytest

from tests._append_crash import crash_append
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


def test_the_next_open_undoes_an_append_a_crash_left_behind(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)
    crash_append(archive, "mid")                         # killed part-way through a member
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(archive)
    result = recover(archive.parent)
    assert result.blocked == ""
    assert any("did not finish" in line for line in result.lines)
    assert _sha(archive) == original
    assert zip_append.pending_journals(archive.parent) == []
    assert recover(archive.parent).lines == []           # nothing left to do


def test_a_run_killed_after_its_append_is_undone_with_its_record(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    record = tmp_path / "in" / "acquisition.json"
    record.write_text('{"old": true}')
    original = _sha(archive)
    crash_append(archive, "after-then", str(record), '{"new": true}')
    result = recover(archive.parent)
    assert result.blocked == ""
    assert _sha(archive) == original
    assert record.read_text() == '{"old": true}'


def _edit_run_journal(archive: Path, **changes) -> None:
    run_journal = archive.parent / RUN_JOURNAL_NAME
    payload = json.loads(run_journal.read_text())
    payload.update(changes)
    run_journal.write_text(json.dumps(payload))


def test_recovery_leaves_a_running_append_alone(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    with AppendRun(archive.parent) as run:
        run.append(archive, _add)
        appended = _sha(archive)
        result = recover(archive.parent)
        assert "running in this window" in result.blocked
        assert _sha(archive) == appended                  # not rolled back under the run
        run.commit()


def test_a_live_process_on_this_computer_is_not_undone(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    crash_append(archive, "after")
    parent = os.getppid()
    _edit_run_journal(archive, pid=parent, pstart=zip_append._process_started(parent))
    appended = _sha(archive)
    result = recover(archive.parent)
    assert f"process {parent}" in result.blocked
    assert _sha(archive) == appended


def test_a_recycled_pid_is_a_dead_run_and_is_undone(tmp_path):
    """The run's pid now belongs to a process that started at another time."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)
    crash_append(archive, "after")
    _edit_run_journal(archive, pid=os.getppid(), pstart="linux:0")
    result = recover(archive.parent)
    assert result.blocked == ""
    assert _sha(archive) == original


def test_a_mac_whose_hostname_drifted_is_still_the_same_computer(tmp_path, monkeypatch):
    """A Mac's platform UUID is its own; its hostname follows the network."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)
    crash_append(archive, "after")
    monkeypatch.setattr(zip_append, "_machine_id", lambda: "mac:PLATFORM-UUID")
    _edit_run_journal(archive, host="Zaks-MacBook-Pro.local", machine="mac:PLATFORM-UUID")
    result = recover(archive.parent)
    assert result.blocked == ""
    assert _sha(archive) == original


def test_another_computer_s_run_is_reported_not_undone_unless_forced(tmp_path):
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)
    crash_append(archive, "after")
    _edit_run_journal(archive, host="some-other-mac", machine="mac:OTHER-UUID")
    appended = _sha(archive)
    result = recover(archive.parent)
    assert "'some-other-mac'" in result.blocked
    assert _sha(archive) == appended
    forced = recover(archive.parent, force=True)
    assert forced.blocked == "" and _sha(archive) == original


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
    crash_append(archive, "after")
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


# ── review findings (tracker M14-R*) ─────────────────────────────────────────
def _rewrite_whole(archive: Path, *, extra: dict | None = None, compression=zipfile.ZIP_STORED) -> None:
    """Another writer replacing the archive whole, as Analyze's loose-image fold does."""
    with zipfile.ZipFile(archive) as reader:
        members = [(i, reader.read(i.filename)) for i in reader.infolist()
                   if not i.filename.endswith("/")]
    tmp = archive.with_name("rewrite.tmp")
    with zipfile.ZipFile(tmp, "w", compression) as writer:
        for info, data in members:
            writer.writestr(info.filename, data)
        for name, data in (extra or {}).items():
            writer.writestr(name, data)
    os.replace(tmp, archive)


def test_an_archive_rewritten_after_the_append_is_not_undone_over(tmp_path):
    """M14-R1(b): the append finished (its size and time are journalled), then something
    rewrote the file whole. Writing the old tail into it would report "restored" over an
    archive that no longer reads; the undo refuses instead, changes nothing, keeps the journal."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    crash_append(archive, "after")
    _rewrite_whole(archive, extra={"E_GFP_A01_f9_0h.tif": b"loose" * 50})
    rewritten = _sha(archive)
    with pytest.raises(AppendJournalError, match="changed by something else"):
        recover(archive.parent)
    assert _sha(archive) == rewritten
    assert journal_path(archive).exists() and (archive.parent / RUN_JOURNAL_NAME).exists()


def test_old_members_that_moved_since_a_mid_member_crash_are_not_undone_over(tmp_path):
    """M14-R1(b), the case with no after-append stamp: the process died mid-member,
    then the archive was rebuilt with its members re-spelled (deflated)."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    crash_append(archive, "mid")
    _messy(tmp_path / "rebuilt" / "A01.zip")
    _rewrite_whole(tmp_path / "rebuilt" / "A01.zip", compression=zipfile.ZIP_DEFLATED,
                   extra={"E_GFP_A01_f9_0h.tif": os.urandom(4000)})   # as long as before, or longer
    os.replace(tmp_path / "rebuilt" / "A01.zip", archive)
    rebuilt = _sha(archive)
    with pytest.raises(AppendJournalError, match="no longer where or what they were"):
        recover(archive.parent)
    assert _sha(archive) == rebuilt
    assert journal_path(archive).exists()


def test_the_restored_archive_s_members_are_each_opened_before_the_journal_goes(tmp_path, monkeypatch):
    """The second line of defence: with the pre-check bypassed, a restore over a moved
    archive is caught by opening each member's local header, and the journal is kept."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    crash_append(archive, "mid")
    _messy(tmp_path / "rebuilt" / "A01.zip")
    _rewrite_whole(tmp_path / "rebuilt" / "A01.zip", compression=zipfile.ZIP_DEFLATED,
                   extra={"E_GFP_A01_f9_0h.tif": os.urandom(4000)})
    os.replace(tmp_path / "rebuilt" / "A01.zip", archive)
    monkeypatch.setattr(zip_append, "_refuse_unless_untouched", lambda *_a: None)
    with pytest.raises(AppendJournalError, match="does not open after the restore"):
        recover(archive.parent)
    assert journal_path(archive).exists()


def test_a_comment_holding_an_end_record_signature_is_refused_untouched(tmp_path):
    """M14-R2: zipfile reads the fake end record in the comment (0 members) while ours
    finds the real one; appending against zipfile's view would drop every member."""
    archive = tmp_path / "in" / "A01.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as writer:
        for i in range(3):
            writer.writestr(f"f{i}.tif", bytes([i]) * 500)
        writer.comment = b"xx PK\x05\x06" + b"\0" * 18 + b"yy"
    original = _sha(archive)
    with pytest.raises(AppendJournalError, match="declares 3 member"):
        with AppendRun(archive.parent) as run:
            run.append(archive, _add)
    assert _sha(archive) == original


def test_only_one_of_two_appends_starting_together_claims_the_folder(tmp_path, monkeypatch):
    """M14-R3: both passed "no journal here"; the run journal is created exclusively."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    (archive.parent / RUN_JOURNAL_NAME).write_text('{"format": "allwell-zip-append-run"}')
    monkeypatch.setattr(zip_append, "pending_journals", lambda _d: [])   # the race window
    with pytest.raises(AppendJournalError, match="has just started"):
        AppendRun(archive.parent).__enter__()
    assert (archive.parent / RUN_JOURNAL_NAME).read_text() == '{"format": "allwell-zip-append-run"}'


def test_no_append_can_start_while_a_recovery_is_undoing(tmp_path, monkeypatch):
    """M14-R4: the folder is claimed for the whole recovery, not only while it is checked."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    crash_append(archive, "after")
    real = zip_append._rollback
    seen = {}

    def rollback_while_someone_starts_an_append(in_dir):
        try:
            AppendRun(in_dir).__enter__()
        except AppendJournalError as exc:
            seen["refused"] = str(exc)
        return real(in_dir)

    monkeypatch.setattr(zip_append, "_rollback", rollback_while_someone_starts_an_append)
    recover(archive.parent)
    assert "already running" in seen.get("refused", "")


def test_a_create_that_died_mid_write_leaves_no_staging_file(tmp_path):
    """M14-R5: atomic_zip's staging file beside a created archive is removed with it."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    created = in_dir / "C03.zip"
    staging = in_dir / "__aw_tmp_C03.zip.pid99999"
    run = AppendRun(in_dir)
    run.__enter__()
    run.create(created, lambda: staging.write_bytes(b"half an archive"))
    run._release()
    recover(in_dir)
    assert not staging.exists() and not created.exists()


def test_a_torn_run_journal_with_no_archive_touched_is_cleared(tmp_path):
    in_dir = tmp_path / "in"
    archive = _messy(in_dir / "A01.zip")
    torn = in_dir / RUN_JOURNAL_NAME
    torn.write_text('{"format": "allwell-zip-ap')
    an_hour_ago = time.time() - 3600
    os.utime(torn, (an_hour_ago, an_hour_ago))
    result = recover(in_dir)
    assert "no archive had been touched" in result.lines[0]
    with AppendRun(in_dir) as run:                       # usable again
        run.append(archive, _add)
        run.commit()



def test_a_run_journal_still_being_written_is_left_alone(tmp_path):
    """M14-R15: the exclusive-create fallback opens the file before writing it, so a
    fresh unreadable run journal may belong to an append that is starting now."""
    in_dir = tmp_path / "in"
    _messy(in_dir / "A01.zip")
    (in_dir / RUN_JOURNAL_NAME).write_bytes(b"")
    result = recover(in_dir)
    assert "still being written" in result.blocked
    assert (in_dir / RUN_JOURNAL_NAME).exists()


def test_an_archive_whose_time_alone_changed_is_still_undone(tmp_path):
    """M14-R12: a copy that does not keep modification times (or a sync client, or a
    coarser-time volume) changes nothing an undo depends on."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    original = _sha(archive)
    crash_append(archive, "after")
    later = time.time() + 7200
    os.utime(archive, (later, later))
    try:
        result = recover(archive.parent)
    except AppendJournalError as exc:
        raise AssertionError(f"a time-only change refused the undo: {exc}") from exc
    assert result.blocked == ""
    assert _sha(archive) == original


def test_a_refused_undo_leaves_the_record_with_the_archives(tmp_path):
    """M14-R13: one archive could not be restored, so the record must not go back to
    describing less than the archives still hold."""
    in_dir = tmp_path / "in"
    a01, a02 = _messy(in_dir / "A01.zip"), _messy(in_dir / "A02.zip")
    record = in_dir / "acquisition.json"
    record.write_text('{"old": true}')
    run = AppendRun(in_dir, preserve=[record])
    run.__enter__()
    run.append(a01, _add)
    run.append(a02, _add)
    record.write_text('{"new": true}')
    run._release()
    with zipfile.ZipFile(a02, "a") as writer:            # something else changed A02 since
        writer.writestr("other.tif", b"other")
    with pytest.raises(AppendJournalError, match="left as they are"):
        recover(in_dir)
    assert record.read_text() == '{"new": true}'
    assert (in_dir / RUN_JOURNAL_NAME).exists()


def test_ps_start_times_read_the_same_in_any_timezone(monkeypatch):
    """M14-R14: the macOS/BSD token came from ``ps`` in the caller's timezone, so a live
    run read from another timezone looked recycled and was undone."""
    import subprocess
    import sys as _sys
    live = subprocess.Popen(["sleep", "30"])
    try:
        monkeypatch.setattr(_sys, "platform", "darwin")
        monkeypatch.setenv("TZ", "Europe/London")
        time.tzset()
        recorded = zip_append._process_started(live.pid)
        monkeypatch.setenv("TZ", "America/New_York")
        time.tzset()
        header = {"host": zip_append._host(), "machine": zip_append._machine_id(),
                  "pid": live.pid, "pstart": recorded}
        assert zip_append._process_started(live.pid) == recorded
        assert f"process {live.pid}" in zip_append._owner_blocks(header, Path("X"))
    finally:
        live.kill()
        monkeypatch.delenv("TZ")
        time.tzset()


def test_start_times_from_different_sources_are_not_evidence_of_a_dead_run(monkeypatch):
    """M14-R14: a mismatch only counts between tokens of the same stable source."""
    header = {"host": zip_append._host(), "machine": zip_append._machine_id(),
              "pid": os.getppid(), "pstart": "ps:Fri Oct 16 18:25:42 2026"}
    assert f"process {os.getppid()}" in zip_append._owner_blocks(header, Path("X"))


def test_a_cloned_machine_id_with_another_name_is_another_computer(tmp_path, monkeypatch):
    """M14-R14 (M-D): Linux VM templates and un-sysprepped Windows images share a
    machine id; only a Mac's id is taken alone."""
    archive = _messy(tmp_path / "in" / "A01.zip")
    crash_append(archive, "after")
    monkeypatch.setattr(zip_append, "_machine_id", lambda: "linux:CLONED")
    _edit_run_journal(archive, host="the-other-vm", machine="linux:CLONED")
    appended = _sha(archive)
    result = recover(archive.parent)
    assert "'the-other-vm'" in result.blocked
    assert _sha(archive) == appended
