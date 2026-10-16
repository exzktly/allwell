"""Append members to an existing well archive in place, under a journal.

A plate's ``in/<WELL>.zip`` holds every timepoint of that well, and at the
scale an experiment runs (60 wells, 25 tiles, 3–4 channels, 20 timepoints) a
single archive passes 12 GB per channel per timepoint. Adding one timepoint by
writing a fresh copy of the whole dataset costs terabytes over an experiment,
so Concatenate's "add to dataset 1 in place" appends to the archive instead.
This module is the part of that which touches bytes, and it is Qt-free and
stdlib-only so that every opener of a dataset — Concatenate, the Analyze run,
Review — can run the crash recovery below.

**What an append changes.** A zip archive is its members' local entries (a
header plus the compressed data, one after another), then the *central
directory* listing them, then an end record pointing at the central directory
(preceded by a ZIP64 end record and locator once the archive passes 4 GB or
65,535 members). :class:`zipfile.ZipFile` opened in mode ``"a"`` reads that
directory, seeks to where it starts, writes each new member's local entry from
there — over the old central directory — and on close writes a new central
directory and end record after the last new member and truncates. So the
members already in the archive are never moved or rewritten; only the bytes
from the old central directory's start to the end of the file are replaced.
That is CPython's ``ZipFile.__init__`` (mode ``"a"``: ``_RealGetContents``
then ``self.fp.seek(self.start_dir)``) and ``_write_end_record`` (which ends
with ``self.fp.truncate()`` in mode ``"a"``). This module does not take that on
trust: the file handle given to ``zipfile`` refuses any write or truncation
below the old central directory's offset (:class:`_FloorGuard`), so a
``zipfile`` that ever behaved otherwise would fail the append before damaging
a byte, and :func:`AppendRun.append` compares every old member's name, offset,
CRC and sizes before and after.

**The journal.** Because only the tail changes, the tail is all that has to be
kept to undo an append: before an archive is touched, its length and the bytes
from its central directory to the end (kilobytes — about 100 bytes per member)
are written to ``in/.<WELL>.zip.aw-append-journal`` beside it, fsynced and
published by rename, so a journal that exists is complete. Restoring is: write
the saved tail back at its old offset, truncate to the old length, fsync. The
result is the original archive byte for byte, and the restore checks that it is
(the tail's SHA-256 and length, and that the archive opens with as many members
as it had) before deleting the journal. An archive the run *creates* (a well
dataset 1 did not have) gets a journal of kind ``created`` instead, and undoing
it deletes the archive.

**The run.** One in-place append touches every well of a plate and then writes
metadata (the acquisition record), and a plate holding the new timepoint in
some wells only is not a state worth leaving. So the archives are journalled
as one transaction, :class:`AppendRun`: it writes ``in/.aw-append-run.json``
before the first archive is touched — holding a copy of every file it was told
to preserve, such as ``in/acquisition.json`` — and deleting that file is the
commit. Until then any failure, a cancellation, or a crash rolls back every
archive with a journal and restores the preserved files. After it, the
leftover per-archive journals describe a finished append and are only removed.

**Recovery** (:func:`recover`) applies the same rule to whatever a crash left:
run journal present → roll everything back; no run journal → delete the
committed leftovers. It refuses to act on a run that is still going — one this
process has open (an in-process registry), or one whose recorded process is
still alive on this host — and on one recorded by another host, which it
cannot judge; it says so instead.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import struct
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from util.aw_safe import swallow
from util.zip_write import fsync_path

__all__ = [
    "JOURNAL_SUFFIX",
    "RUN_JOURNAL_NAME",
    "AppendJournalError",
    "AppendRun",
    "Recovery",
    "Tail",
    "journal_path",
    "locate_tail",
    "pending_journals",
    "recover",
]

_LOG = logging.getLogger("allwell.zip_append")

#: ``in/.<WELL>.zip`` + this: one archive's journal. Hidden (a leading dot) and
#: not ending in ``.zip``, so no well-archive scan in the app ever reads it.
JOURNAL_SUFFIX = ".aw-append-journal"
#: The run journal; its presence means an append is not committed.
RUN_JOURNAL_NAME = ".aw-append-run.json"
_TMP_SUFFIX = ".tmp"
_FORMAT = "allwell-zip-append-journal"
_RUN_FORMAT = "allwell-zip-append-run"
_VERSION = 1
_KIND_TAIL = "tail"
_KIND_CREATED = "created"

_EOCD_SIG = b"PK\x05\x06"
_EOCD = struct.Struct("<4s4H2LH")
_Z64_LOCATOR_SIG = b"PK\x06\x07"
_Z64_LOCATOR = struct.Struct("<4sLQL")
_Z64_EOCD_SIG = b"PK\x06\x06"
_Z64_EOCD = struct.Struct("<4sQ2H2L4Q")
_MAX_COMMENT = 0xFFFF


class AppendJournalError(Exception):
    """An append or its undo cannot proceed. The message is user-facing."""


@dataclass(frozen=True)
class Tail:
    """Where an archive's central directory starts, and how long the file is."""

    offset: int
    length: int


def journal_path(archive: Path) -> Path:
    """The journal file that belongs to *archive*."""
    archive = Path(archive)
    return archive.with_name(f".{archive.name}{JOURNAL_SUFFIX}")


def _archive_for(journal: Path) -> Path:
    name = journal.name[1:-len(JOURNAL_SUFFIX)]
    return journal.with_name(name)


def pending_journals(in_dir: Path) -> list[Path]:
    """Every per-archive journal and the run journal in *in_dir*, sorted."""
    in_dir = Path(in_dir)
    if not in_dir.is_dir():
        return []
    found = sorted(in_dir.glob(f".*{JOURNAL_SUFFIX}"))
    run = in_dir / RUN_JOURNAL_NAME
    return found + ([run] if run.exists() else [])


# ── locating the tail ────────────────────────────────────────────────────────
def locate_tail(archive: Path) -> Tail:
    """Find where *archive*'s central directory starts, reading only its end.

    Parses the end record (and, when present, the ZIP64 locator and ZIP64 end
    record) rather than asking :mod:`zipfile`, so the journal does not depend
    on a private attribute. Refuses an archive with data prepended before its
    first member (a self-extractor, or a zip concatenated onto another file):
    :mod:`zipfile` shifts every member's offset for such a file and rewrites
    them on append, which is a rewrite of more than the tail.
    """
    archive = Path(archive)
    size = archive.stat().st_size
    with open(archive, "rb") as handle:
        back = min(size, _EOCD.size + _MAX_COMMENT)
        handle.seek(size - back)
        buf = handle.read(back)
        pos = buf.rfind(_EOCD_SIG)
        while pos >= 0:
            if pos + _EOCD.size <= len(buf):
                fields = _EOCD.unpack_from(buf, pos)
                if pos + _EOCD.size + fields[7] == len(buf):
                    break
            pos = buf.rfind(_EOCD_SIG, 0, pos)
        else:
            raise AppendJournalError(f"{archive.name} is not a readable zip archive "
                                     "(no end-of-central-directory record).")
        _sig, disk, cd_disk, _n_disk, _n_total, cd_size, cd_offset, _comment = fields
        eocd_at = size - back + pos
        record_at = eocd_at
        if eocd_at >= _Z64_LOCATOR.size:
            handle.seek(eocd_at - _Z64_LOCATOR.size)
            locator = handle.read(_Z64_LOCATOR.size)
            if locator[:4] == _Z64_LOCATOR_SIG:
                _s, disk, z64_at, _disks = _Z64_LOCATOR.unpack(locator)
                handle.seek(z64_at)
                record = handle.read(_Z64_EOCD.size)
                if len(record) != _Z64_EOCD.size or record[:4] != _Z64_EOCD_SIG:
                    raise AppendJournalError(f"{archive.name}: its ZIP64 end record is missing "
                                             "or damaged.")
                (_s, _sz, _vm, _vn, disk, cd_disk, _nd, _nt,
                 cd_size, cd_offset) = _Z64_EOCD.unpack(record)
                record_at = z64_at
    if disk or cd_disk:
        raise AppendJournalError(f"{archive.name} spans several disks; it can't be appended to.")
    if cd_offset + cd_size != record_at:
        raise AppendJournalError(
            f"{archive.name} has data before its first member (or a damaged central "
            "directory), so appending would rewrite more than its end; it can't be "
            "appended to in place.")
    return Tail(offset=cd_offset, length=size)


# ── journal files ────────────────────────────────────────────────────────────
def _host() -> str:
    return socket.gethostname()


def _publish(path: Path, payload: bytes) -> None:
    """Write *payload* to *path* durably and atomically: a journal that exists is whole."""
    tmp = path.with_name(path.name + _TMP_SUFFIX)
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_path(path.parent)


def _read_journal(journal: Path) -> tuple[dict, bytes]:
    data = journal.read_bytes()
    head, sep, body = data.partition(b"\n")
    try:
        header = json.loads(head.decode("utf-8")) if sep else None
    except ValueError:
        header = None
    if not isinstance(header, dict) or header.get("format") != _FORMAT:
        raise AppendJournalError(f"{journal.name} is not an All-Well append journal "
                                 "or is damaged; nothing was changed.")
    if header.get("version") != _VERSION:
        raise AppendJournalError(f"{journal.name} was written by a different version "
                                 f"(journal version {header.get('version')!r}); nothing was changed.")
    if header.get("kind") == _KIND_TAIL:
        expected = int(header["length"]) - int(header["tail_offset"])
        if len(body) != expected or hashlib.sha256(body).hexdigest() != header["tail_sha256"]:
            raise AppendJournalError(f"{journal.name} is damaged (its saved tail does not match "
                                     "its checksum); the archive was left as it is.")
    return header, body


def _restore(journal: Path) -> str:
    """Undo one archive's append from its journal; returns what was done."""
    header, tail = _read_journal(journal)
    archive = _archive_for(journal)
    if header["kind"] == _KIND_CREATED:
        existed = archive.exists()
        archive.unlink(missing_ok=True)
        journal.unlink()
        fsync_path(journal.parent)
        return (f"{archive.name}: removed the archive the unfinished append had created."
                if existed else f"{archive.name}: the unfinished append had not created it yet.")
    offset, length = int(header["tail_offset"]), int(header["length"])
    if not archive.exists():
        raise AppendJournalError(f"{archive.name} is missing; its journal is kept in "
                                 f"{journal.name} so it can still be restored.")
    if archive.stat().st_size < offset:
        raise AppendJournalError(f"{archive.name} is shorter than it was before the append "
                                 "began, so its earlier members are damaged; its journal is kept.")
    with open(archive, "r+b") as handle:
        handle.seek(offset)
        handle.write(tail)
        handle.truncate(length)
        handle.flush()
        os.fsync(handle.fileno())
    with open(archive, "rb") as handle:
        handle.seek(offset)
        restored = handle.read()
    if (archive.stat().st_size != length
            or hashlib.sha256(restored).hexdigest() != header["tail_sha256"]):
        raise AppendJournalError(f"{archive.name} did not read back as it was after the "
                                 "restore; its journal is kept.")
    with zipfile.ZipFile(archive) as check:
        members = len(check.infolist())
    if members != int(header["members"]):
        raise AppendJournalError(f"{archive.name} lists {members} member(s) after the restore, "
                                 f"not its original {header['members']}; its journal is kept.")
    journal.unlink()
    fsync_path(journal.parent)
    return f"{archive.name}: restored to its {header['members']} member(s) from before the append."


# ── the write guard ──────────────────────────────────────────────────────────
class _FloorGuard:
    """A file handle that refuses to write or truncate below *floor*.

    Handed to :class:`zipfile.ZipFile` in mode ``"a"``: the members before the
    old central directory are the dataset, and nothing an append does may
    reach them. Everything else is delegated.
    """

    def __init__(self, raw, floor: int) -> None:
        self._raw = raw
        self._floor = floor

    def write(self, data) -> int:
        if self._raw.tell() < self._floor:
            raise AppendJournalError(
                f"refused a write at offset {self._raw.tell()}, before the old central "
                f"directory at {self._floor}: an append must not touch existing members.")
        return self._raw.write(data)

    def truncate(self, size: int | None = None) -> int:
        target = self._raw.tell() if size is None else size
        if target < self._floor:
            raise AppendJournalError(f"refused a truncation to {target}, before the old "
                                     f"central directory at {self._floor}.")
        return self._raw.truncate(size)

    def __getattr__(self, name):
        return getattr(self._raw, name)


def _member_identity(infos: Iterable[zipfile.ZipInfo]) -> list[tuple]:
    return [(i.filename, i.header_offset, i.CRC, i.compress_size, i.file_size, i.compress_type)
            for i in infos]


# ── the run ──────────────────────────────────────────────────────────────────
_ACTIVE: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


def _key(in_dir: Path) -> str:
    return os.path.normcase(str(Path(in_dir).resolve()))


class AppendRun:
    """One all-or-nothing in-place append over the archives of one ``in/`` folder.

    Use as a context manager. :meth:`append` and :meth:`create` may be called
    from several threads at once for *different* archives. :meth:`commit` makes
    the run permanent; leaving the block without it — an exception, a
    cancellation — rolls every archive back and restores the preserved files,
    and :attr:`rollback_lines` says what was restored.
    """

    def __init__(self, in_dir: Path, *, preserve: Iterable[Path] = ()) -> None:
        self.in_dir = Path(in_dir)
        self._preserve = [Path(p) for p in preserve]
        self._run_id = uuid.uuid4().hex
        self._committed = False
        self._entered = False
        #: What the rollback restored, line by line, once the block has exited uncommitted.
        self.rollback_lines: list[str] = []
        #: Set when the rollback itself could not complete; the journals are then kept.
        self.rollback_error = ""

    # context management ----------------------------------------------------
    def __enter__(self) -> AppendRun:
        key = _key(self.in_dir)
        with _ACTIVE_LOCK:
            if key in _ACTIVE:
                raise AppendJournalError(f"An in-place append into {self.in_dir} is already running.")
            _ACTIVE.add(key)
        self._entered = True
        try:
            leftovers = pending_journals(self.in_dir)
            if leftovers:
                raise AppendJournalError(
                    f"{self.in_dir} holds journals from an earlier in-place append "
                    f"({', '.join(p.name for p in leftovers[:3])}); recover it first.")
            preserved = {}
            for path in self._preserve:
                rel = os.path.relpath(path, self.in_dir)
                preserved[rel] = (base64.b64encode(path.read_bytes()).decode("ascii")
                                  if path.exists() else None)
            payload = {"format": _RUN_FORMAT, "version": _VERSION, "run": self._run_id,
                       "pid": os.getpid(), "host": _host(), "started": time.time(),
                       "preserved": preserved}
            _publish(self.in_dir / RUN_JOURNAL_NAME, json.dumps(payload).encode("utf-8"))
        except BaseException:
            self._release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if not self._committed:
                # The caller's exception is the one to propagate; a failed undo
                # is reported beside it, never instead of it.
                try:
                    self.rollback_lines = _rollback(self.in_dir)
                except (AppendJournalError, OSError) as undo:
                    self.rollback_error = str(undo)
                    _LOG.error("in-place append into %s could not be undone: %s", self.in_dir, undo)
        finally:
            self._release()
        return False

    def _release(self) -> None:
        if self._entered:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(_key(self.in_dir))
            self._entered = False

    # work ------------------------------------------------------------------
    def _header(self, archive: Path, kind: str) -> dict:
        return {"format": _FORMAT, "version": _VERSION, "kind": kind, "archive": archive.name,
                "run": self._run_id, "pid": os.getpid(), "host": _host()}

    def append(self, archive: Path, write: Callable[[zipfile.ZipFile], None]) -> None:
        """Journal *archive*, open it for append, let *write* add members, then check.

        *write* receives the open :class:`zipfile.ZipFile`. Raises
        :class:`AppendJournalError` when an old member's name, offset, CRC or
        sizes differ afterwards; the run's rollback restores it.
        """
        archive = Path(archive)
        tail = locate_tail(archive)
        with zipfile.ZipFile(archive) as before_zf:
            before = _member_identity(before_zf.infolist())
        with open(archive, "rb") as handle:
            handle.seek(tail.offset)
            saved = handle.read()
        if tail.offset + len(saved) != tail.length:
            raise AppendJournalError(f"{archive.name} changed while its journal was being written.")
        header = self._header(archive, _KIND_TAIL)
        header.update(length=tail.length, tail_offset=tail.offset, members=len(before),
                      tail_sha256=hashlib.sha256(saved).hexdigest())
        _publish(journal_path(archive), json.dumps(header).encode("utf-8") + b"\n" + saved)
        with open(archive, "r+b") as raw:
            guard = _FloorGuard(raw, tail.offset)
            with zipfile.ZipFile(guard, "a", allowZip64=True) as zf:
                write(zf)
            raw.flush()
            os.fsync(raw.fileno())
        with zipfile.ZipFile(archive) as after_zf:
            after = _member_identity(after_zf.infolist())
        if after[:len(before)] != before:
            changed = next((old[0] for old, new in zip(before, after) if old != new), "?")
            raise AppendJournalError(f"{archive.name}: existing member {changed!r} is not where "
                                     "or what it was after the append.")

    def create(self, archive: Path, publish: Callable[[], None]) -> None:
        """Journal *archive* as created by this run, then let *publish* write it."""
        archive = Path(archive)
        if archive.exists():
            raise AppendJournalError(f"{archive.name} already exists; it would be appended to, "
                                     "not created.")
        _publish(journal_path(archive), json.dumps(self._header(archive, _KIND_CREATED)).encode("utf-8")
                 + b"\n")
        publish()

    def commit(self) -> None:
        """Make the run permanent: delete the run journal, then the archive journals."""
        run = self.in_dir / RUN_JOURNAL_NAME
        run.unlink()
        fsync_path(self.in_dir)
        self._committed = True
        for journal in pending_journals(self.in_dir):
            with swallow(f"removing the committed append journal {journal}", level=logging.WARNING,
                         logger=_LOG):
                journal.unlink()


def _rollback(in_dir: Path) -> list[str]:
    """Restore every journalled archive and every preserved file, then drop the run journal.

    Every archive is attempted even when one fails, so one damaged journal
    does not leave the rest of the plate half-appended; the run journal is
    kept when anything failed, so the next open tries again.
    """
    lines: list[str] = []
    failures: list[str] = []
    for journal in sorted(Path(in_dir).glob(f".*{JOURNAL_SUFFIX}")):
        try:
            lines.append(_restore(journal))
        except (AppendJournalError, OSError, zipfile.BadZipFile) as exc:
            failures.append(f"{_archive_for(journal).name}: {exc}")
    run = Path(in_dir) / RUN_JOURNAL_NAME
    if run.exists():
        try:
            payload = json.loads(run.read_text(encoding="utf-8"))
            for rel, content in (payload.get("preserved") or {}).items():
                target = Path(in_dir) / rel
                if content is None:
                    target.unlink(missing_ok=True)
                    lines.append(f"{target.name}: removed (it did not exist before the append).")
                else:
                    _publish(target, base64.b64decode(content))
                    lines.append(f"{target.name}: restored to its content from before the append.")
        except (ValueError, OSError) as exc:
            failures.append(f"{RUN_JOURNAL_NAME}: {exc}")
    if failures:
        raise AppendJournalError("Could not undo the in-place append completely: "
                                 + "; ".join(failures))
    run.unlink(missing_ok=True)
    fsync_path(in_dir)
    return lines


# ── recovery ─────────────────────────────────────────────────────────────────
@dataclass
class Recovery:
    """What :func:`recover` did. *blocked* is set, with the reason, when it left things alone."""

    lines: list[str] = field(default_factory=list)
    blocked: str = ""


def _pid_alive(pid: int) -> bool:
    """Whether process *pid* exists on this host. Unknown → ``True`` (never undo a live run)."""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5             # ERROR_ACCESS_DENIED: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                        # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _owner_blocks(header: dict, where: Path) -> str:
    """Why the process that wrote *header* may still be working, or ``""``."""
    host, pid = header.get("host"), int(header.get("pid") or 0)
    if host != _host():
        return (f"An in-place append into {where} was started on {host!r} and not finished. "
                "Open the dataset on that computer to undo it, or wait for it to finish.")
    if pid != os.getpid() and _pid_alive(pid):
        return (f"An in-place append into {where} is still running in process {pid}; "
                "it was left alone.")
    return ""


def recover(in_dir: Path) -> Recovery:
    """Undo an unfinished in-place append in *in_dir*, or clear a finished one's leftovers.

    Safe to call on every open: with nothing to do it reads one directory
    listing. Never raises for a run it must leave alone — it returns
    :attr:`Recovery.blocked` — and raises :class:`AppendJournalError` only when
    an undo it attempted could not complete.
    """
    in_dir = Path(in_dir)
    result = Recovery()
    if not in_dir.is_dir():
        return result
    run = in_dir / RUN_JOURNAL_NAME
    journals = sorted(in_dir.glob(f".*{JOURNAL_SUFFIX}"))
    stray_tmp = sorted(in_dir.glob(f".*{JOURNAL_SUFFIX}{_TMP_SUFFIX}"))
    run_tmp = in_dir / (RUN_JOURNAL_NAME + _TMP_SUFFIX)
    if not (run.exists() or journals or stray_tmp or run_tmp.exists()):
        return result
    with _ACTIVE_LOCK:
        if _key(in_dir) in _ACTIVE:
            result.blocked = f"An in-place append into {in_dir} is running in this window; it was left alone."
            return result
    if run.exists():
        try:
            header = json.loads(run.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise AppendJournalError(f"{run} is damaged ({exc}); nothing was changed.") from exc
        result.blocked = _owner_blocks(header, in_dir)
        if result.blocked:
            return result
        # A journal still being published was never followed by a write to its
        # archive, so the half-written file is simply discarded.
        for tmp in stray_tmp:
            tmp.unlink(missing_ok=True)
        result.lines = ["An in-place append into this dataset did not finish; undoing it."]
        result.lines += _rollback(in_dir)
        return result
    for tmp in [*stray_tmp, *([run_tmp] if run_tmp.exists() else [])]:
        tmp.unlink(missing_ok=True)
    for journal in journals:
        try:
            header, _tail = _read_journal(journal)
        except AppendJournalError:
            header = {}
        blocked = _owner_blocks(header, in_dir) if header else ""
        if blocked:
            result.blocked = blocked
            continue
        journal.unlink(missing_ok=True)
        result.lines.append(f"{_archive_for(journal).name}: its in-place append had finished; "
                            "removed the leftover journal.")
    return result
