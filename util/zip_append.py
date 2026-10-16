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
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
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
    "pending_refusal",
    "refuse_if_pending",
    "recover",
    "recover_paths",
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
    """Where an archive's central directory starts, how long the file is, and
    how many entries its end record declares."""

    offset: int
    length: int
    entries: int = -1


def journal_path(archive: Path) -> Path:
    """The journal file that belongs to *archive*."""
    archive = Path(archive)
    return archive.with_name(f".{archive.name}{JOURNAL_SUFFIX}")


def pending_refusal(in_dir: Path) -> str:
    """Why *in_dir* must not be read as it stands, or ``""``.

    For a reader that does not undo anything itself — the pipeline's own
    command line, which a user can run on a dataset whose in-place addition a
    crash left unfinished, and would otherwise analyse an uncommitted timepoint.
    """
    pending = pending_journals(in_dir)
    if not pending:
        return ""
    return (f"{in_dir} holds an unfinished in-place addition "
            f"({', '.join(p.name for p in pending[:3])}): some well archives may hold part of "
            "a timepoint. Open the dataset in All-Well (Review, Analyze or Concatenate) to undo "
            "it, then run this again.")


def refuse_if_pending(in_dir: Path, refuse: Callable[[str], object]) -> None:
    """Call *refuse* with :func:`pending_refusal`'s reason when there is one.

    The shape a command line's argument check wants (``parser.error``), so its
    caller gains no branch of its own.
    """
    reason = pending_refusal(in_dir)
    if reason:
        refuse(reason)


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
        _sig, disk, cd_disk, _n_disk, entries, cd_size, cd_offset, _comment = fields
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
                (_s, _sz, _vm, _vn, disk, cd_disk, _nd, entries,
                 cd_size, cd_offset) = _Z64_EOCD.unpack(record)
                record_at = z64_at
    if disk or cd_disk:
        raise AppendJournalError(f"{archive.name} spans several disks; it can't be appended to.")
    if cd_offset + cd_size != record_at:
        raise AppendJournalError(
            f"{archive.name} has data before its first member (or a damaged central "
            "directory), so appending would rewrite more than its end; it can't be "
            "appended to in place.")
    return Tail(offset=cd_offset, length=size, entries=entries)


# ── who wrote a journal ──────────────────────────────────────────────────────
def _host() -> str:
    return socket.gethostname()


@lru_cache(maxsize=1)
def _machine_id() -> str | None:
    """A stable identity for this computer, or ``None`` when none can be read.

    The hostname is not one: macOS changes ``gethostname()`` with the network
    it is on, and a journal judged "from another computer" because the laptop
    moved from the office to home could never be undone in the app.
    """
    if sys.platform.startswith("linux"):
        for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                text = Path(candidate).read_text().strip()
            except OSError:
                continue
            if text:
                return f"linux:{text}"
        return None
    if sys.platform == "darwin":  # pragma: no cover - exercised on macOS only
        try:
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            if "IOPlatformUUID" in line and "=" in line:
                return "mac:" + line.split("=", 1)[1].strip().strip('"')
        return None
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                return "win:" + str(winreg.QueryValueEx(key, "MachineGuid")[0])
        except OSError:
            return None
    return None


def _process_started(pid: int) -> str | None:
    """When process *pid* started, as an opaque token, or ``None`` when unknown.

    Recorded beside the pid so that a pid the system has since given to another
    process is recognised as a dead run rather than a live one.
    """
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        fields = stat.rsplit(")", 1)[-1].split()
        return f"linux:{fields[19]}" if len(fields) > 19 else None
    if os.name == "nt":  # pragma: no cover - Windows: no start time, liveness only
        return None
    # ``ps`` prints the start time in the caller's timezone and locale, so two
    # windows (or one window either side of a timezone change) read different
    # text for the same process. Fixed here, the token is the same everywhere.
    env = {"PATH": os.environ.get("PATH", "/bin:/usr/bin"), "LC_ALL": "C", "TZ": "UTC0"}
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], env=env,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"ps-utc:{out}" if out else None


def _identity() -> dict:
    pid = os.getpid()
    return {"pid": pid, "pstart": _process_started(pid), "host": _host(),
            "machine": _machine_id()}


# ── journal files ────────────────────────────────────────────────────────────


def _publish(path: Path, payload: bytes) -> None:
    """Write *payload* to *path* durably and atomically: a journal that exists is whole."""
    tmp = path.with_name(path.name + _TMP_SUFFIX)
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_path(path.parent)


def _publish_exclusive(path: Path, payload: bytes) -> None:
    """Like :func:`_publish`, but fail when *path* already exists.

    Two appends into one folder can both pass "no journal here yet"; only one
    may then claim it. A hard link is atomic and refuses an existing target;
    where links are unsupported, an exclusive create (not atomic in content,
    which :func:`recover` tolerates while no archive has been touched).
    """
    tmp = path.with_name(path.name + _TMP_SUFFIX + f".{os.getpid()}")
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise AppendJournalError(f"Another in-place append into {path.parent} has just "
                                     "started; this one was not begun.") from exc
        except OSError:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError as exc:
                raise AppendJournalError(f"Another in-place append into {path.parent} has "
                                         "just started; this one was not begun.") from exc
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)
    fsync_path(path.parent)


def _local_headers(archive: Path, offsets: list[int]) -> str:
    """SHA-256 over the local header (fixed part, name, extra) at each offset.

    What an undo checks before writing anything: the members it would restore
    a central directory for must still be where, and what, they were. A
    whole-file rewrite by any other writer moves or re-spells them.
    """
    digest = hashlib.sha256()
    size = archive.stat().st_size
    with open(archive, "rb") as handle:
        for offset in offsets:
            if offset + 30 > size:
                return "missing"
            handle.seek(offset)
            fixed = handle.read(30)
            name_len, extra_len = struct.unpack("<HH", fixed[26:30])
            digest.update(fixed + handle.read(name_len + extra_len))
    return digest.hexdigest()


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
        # The staging file a create that died mid-write leaves beside it
        # (util.zip_write's ``__aw_tmp_<name>.pid<N>``).
        for staging in archive.parent.glob(f"__aw_tmp_{archive.name}.pid*"):
            staging.unlink(missing_ok=True)
        journal.unlink()
        fsync_path(journal.parent)
        return (f"{archive.name}: removed the archive the unfinished append had created."
                if existed else f"{archive.name}: the unfinished append had not created it yet.")
    offset, length = int(header["tail_offset"]), int(header["length"])
    if not archive.exists():
        raise AppendJournalError(f"{archive.name} is missing; its journal is kept in "
                                 f"{journal.name} so it can still be restored.")
    _refuse_unless_untouched(archive, header)
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
        infos = check.infolist()
        if len(infos) != int(header["members"]):
            raise AppendJournalError(f"{archive.name} lists {len(infos)} member(s) after the "
                                     f"restore, not its original {header['members']}; its "
                                     "journal is kept.")
        for info in infos:
            # Opening reads the member's local header and checks its signature
            # and name against the restored directory; no data is read.
            try:
                check.open(info).close()
            except (zipfile.BadZipFile, OSError) as exc:
                raise AppendJournalError(f"{archive.name}: member {info.filename!r} does not "
                                         f"open after the restore ({exc}); its journal is "
                                         "kept.") from exc
    journal.unlink()
    fsync_path(journal.parent)
    return f"{archive.name}: restored to its {header['members']} member(s) from before the append."


_KEEP_HINT = (" To keep the archives as they are now, use \"Undo the unfinished addition…\" in "
              "Concatenate and choose to keep them; the log then says what each one holds.")


def _end_digest(archive: Path) -> str:
    """SHA-256 of *archive*'s central directory to its end, or ``"unreadable"``."""
    try:
        tail = locate_tail(archive)
    except (AppendJournalError, OSError):
        return "unreadable"
    with open(archive, "rb") as handle:
        handle.seek(tail.offset)
        return hashlib.sha256(handle.read()).hexdigest()


def _refuse_unless_untouched(archive: Path, header: dict) -> None:
    """Raise unless every byte below the saved tail is still what the journal saw.

    Writing the old tail back is only an undo when the members in front of it
    are the ones it describes. Something else may have rewritten the archive
    whole since — Analyze folding a loose image into it is one — and writing a
    stale directory into that file reports "restored" over an archive that no
    longer reads. Two checks, both cheap on a multi-gigabyte file: the size and
    a SHA-256 of the archive's own end (its new central directory to the end
    of the file, kilobytes) recorded once the append had finished — absent when
    the process died part-way, when nothing but that process wrote the file —
    and a hash of every old member's local header at its old offset.

    Content, not modification time: a copy that does not keep times, a sync
    client, or a volume with coarser timestamps changes the time and nothing
    else, and a time check then refused that archive for ever.
    """
    offset = int(header["tail_offset"])
    stat = archive.stat()
    keep = "; its journal is kept and nothing was changed."
    if stat.st_size < offset:
        raise AppendJournalError(f"{archive.name} is shorter than it was before the append "
                                 f"began, so its earlier members are damaged{keep}")
    stamp = header.get("appended")
    if stamp and (stat.st_size != stamp["size"] or _end_digest(archive) != stamp["end_sha256"]):
        what = (f"it is {stat.st_size} bytes, {stamp['size']} when the append finished"
                if stat.st_size != stamp["size"] else
                "its central directory is not the one the append wrote")
        raise AppendJournalError(
            f"{archive.name} was changed by something else after the unfinished append "
            f"({what}), so undoing it would damage it{keep}{_KEEP_HINT}")
    if "headers_sha256" in header and _local_headers(
            archive, list(header.get("offsets") or [])) != header["headers_sha256"]:
        raise AppendJournalError(
            f"{archive.name}'s earlier members are no longer where or what they were when the "
            f"append began, so undoing it would damage the archive{keep}{_KEEP_HINT}")


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
                       **_identity(), "started": time.time(), "preserved": preserved}
            _publish_exclusive(self.in_dir / RUN_JOURNAL_NAME, json.dumps(payload).encode("utf-8"))
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
                "run": self._run_id, **_identity()}

    def append(self, archive: Path, write: Callable[[zipfile.ZipFile], None]) -> None:
        """Journal *archive*, open it for append, let *write* add members, then check.

        *write* receives the open :class:`zipfile.ZipFile`. Raises
        :class:`AppendJournalError` when an old member's name, offset, CRC or
        sizes differ afterwards; the run's rollback restores it.
        """
        archive = Path(archive)
        tail = locate_tail(archive)
        with zipfile.ZipFile(archive) as before_zf:
            infos = before_zf.infolist()
        before = _member_identity(infos)
        if tail.entries != len(before):
            # zipfile found a different end record from ours — a comment that
            # happens to hold the end-record signature — and would append
            # against a directory that is not the archive's.
            raise AppendJournalError(
                f"{archive.name}'s end record declares {tail.entries} member(s) but zipfile "
                f"reads {len(before)} (a comment that looks like an end record?); it can't be "
                "appended to in place.")
        offsets = [info.header_offset for info in infos]
        with open(archive, "rb") as handle:
            handle.seek(tail.offset)
            saved = handle.read()
        if tail.offset + len(saved) != tail.length:
            raise AppendJournalError(f"{archive.name} changed while its journal was being written.")
        header = self._header(archive, _KIND_TAIL)
        header.update(length=tail.length, tail_offset=tail.offset, members=len(before),
                      tail_sha256=hashlib.sha256(saved).hexdigest(), offsets=offsets,
                      headers_sha256=_local_headers(archive, offsets))
        _publish(journal_path(archive), json.dumps(header).encode("utf-8") + b"\n" + saved)
        with open(archive, "r+b") as raw:
            guard = _FloorGuard(raw, tail.offset)
            with zipfile.ZipFile(guard, "a", allowZip64=True) as zf:
                write(zf)
            raw.flush()
            os.fsync(raw.fileno())
        # The archive as this run left it; an undo refuses a file that differs.
        header["appended"] = {"size": archive.stat().st_size, "end_sha256": _end_digest(archive)}
        _publish(journal_path(archive), json.dumps(header).encode("utf-8") + b"\n" + saved)
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
    if failures and run.exists():
        # The record must keep describing the archives: with any archive still
        # holding the addition, putting the old record back would leave it
        # claiming less than is there. Both stay as they are, together.
        lines.append("The preserved files (such as acquisition.json) were left as they are, "
                     "because not every archive could be restored.")
    elif run.exists():
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
                                 + "; ".join(failures) + " " + " ".join(lines))
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


def _same_computer(header: dict) -> bool:
    """Whether *header* was written on this computer: by machine id when both
    sides have one (the hostname drifts with the network on macOS), else by name."""
    recorded, here = header.get("machine"), _machine_id()
    same_name = header.get("host") == _host()
    if recorded and here:
        if recorded != here:
            return False
        # A Mac's platform UUID is its own; its hostname follows the network,
        # which is the drift this exists for. Elsewhere a machine id is cloned
        # with the image (Linux VM templates, un-sysprepped Windows), so two
        # computers can share one: the name must agree too.
        return same_name or recorded.startswith("mac:")
    return same_name


#: Start-time sources whose tokens are comparable between processes: the
#: kernel's tick count, and ``ps`` read under a fixed timezone and locale.
#: A mismatch between anything else is not evidence of a dead run.
_STABLE_SOURCES = ("linux", "ps-utc")


def _token_source(token: str) -> str:
    return token.split(":", 1)[0]


def _owner_blocks(header: dict, where: Path) -> str:
    """Why the process that wrote *header* may still be working, or ``""``.

    A pid that is alive but started at a different time from the one recorded
    is a recycled pid, so its run is dead. A pid that is alive and whose start
    time cannot be compared is treated as alive: an undo under a live append
    would destroy it, and the page's "undo anyway" exists for that case.
    """
    if not _same_computer(header):
        return (f"An in-place append into {where} was started on {header.get('host')!r} and "
                "not finished. Open the dataset on that computer to undo it, or undo it anyway "
                "from the Concatenate page if that computer is not still running it.")
    pid = int(header.get("pid") or 0)
    if pid == os.getpid() or not _pid_alive(pid):
        return ""
    recorded, current = header.get("pstart"), _process_started(pid)
    if (recorded and current and recorded != current
            and _token_source(recorded) == _token_source(current) in _STABLE_SOURCES):
        return ""                                   # the pid now belongs to another process
    return (f"An in-place append into {where} is still running in process {pid}; it was "
            "left alone. If that process is not All-Well, undo it anyway from the "
            "Concatenate page.")


def recover(in_dir: Path, *, force: bool = False, keep: bool = False) -> Recovery:
    """Undo an unfinished in-place append in *in_dir*, or clear a finished one's leftovers.

    Safe to call on every open: with nothing to do it reads one directory
    listing. Never raises for a run it must leave alone — it returns
    :attr:`Recovery.blocked` — and raises :class:`AppendJournalError` only when
    an undo it attempted could not complete or was refused as unsafe.

    *force* skips the judgement of whether the recorded process may still be
    running (another computer, a live pid): the page's "undo anyway", asked
    only after the user has confirmed. It never overrides a run this process
    holds, and never the checks that the archive is still the one journalled.

    *keep* is the exit when an undo is refused as unsafe: nothing is restored,
    every archive and preserved file is left exactly as it is, the journals are
    removed, and the lines say what each archive now holds — which wells carry
    members added since the addition began — so the user decides with the facts.
    Like *force*, it is asked for only after the user has confirmed.

    The folder is claimed in the in-process registry for the whole recovery, so
    an append cannot start in it — and a second recovery cannot run — meanwhile.
    """
    in_dir = Path(in_dir)
    result = Recovery()
    if not in_dir.is_dir() or not _has_journal_files(in_dir):
        return result
    key = _key(in_dir)
    with _ACTIVE_LOCK:
        if key in _ACTIVE:
            result.blocked = f"An in-place append into {in_dir} is running in this window; it was left alone."
            return result
        _ACTIVE.add(key)
    try:
        if keep:
            return Recovery(lines=_keep_as_is(in_dir))
        return _recover_claimed(in_dir, force=force)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(key)


def _archive_state(archive: Path, header: dict, tail: bytes) -> str:
    """One sentence on what *archive* holds now, relative to its journal."""
    if not archive.exists():
        return f"{archive.name}: missing."
    try:
        with zipfile.ZipFile(archive) as reader:
            names = reader.namelist()
    except (zipfile.BadZipFile, OSError) as exc:
        return f"{archive.name}: does not open as a zip archive ({exc}); check it before use."
    if header.get("kind") == _KIND_CREATED:
        return f"{archive.name}: kept as created by the unfinished addition ({len(names)} member(s))."
    old = int(header.get("members", len(names)))
    if (archive.stat().st_size == int(header.get("length", -1))
            and _end_digest(archive) == hashlib.sha256(tail).hexdigest()):
        return f"{archive.name}: exactly as it was before the addition ({old} member(s))."
    added = names[old:]
    shown = ", ".join(added[:5]) + (", …" if len(added) > 5 else "")
    # "Added since", not "added by": another writer may have added some of them.
    return (f"{archive.name}: kept with {len(added)} member(s) added since the addition began"
            + (f" ({shown})" if added else "") + f", after its {old} earlier one(s).")


def _keep_as_is(in_dir: Path) -> list[str]:
    """Stop tracking an unfinished addition, leaving every file as it is, and say what is there."""
    lines = ["The unfinished addition's notes were discarded and nothing was restored. "
             "Each archive now:"]
    for journal in sorted(Path(in_dir).glob(f".*{JOURNAL_SUFFIX}")):
        try:
            header, tail = _read_journal(journal)
        except AppendJournalError:
            header, tail = {}, b""
        lines.append("  " + _archive_state(_archive_for(journal), header, tail))
        journal.unlink()
    run = Path(in_dir) / RUN_JOURNAL_NAME
    if run.exists():
        with swallow(f"reading the preserved files in {run}", level=logging.WARNING, logger=_LOG):
            payload = json.loads(run.read_text(encoding="utf-8"))
            for rel, content in (payload.get("preserved") or {}).items():
                target = Path(in_dir) / rel
                now = target.read_bytes() if target.exists() else None
                before = None if content is None else base64.b64decode(content)
                lines.append(f"  {target.name}: left as it is now "
                             f"({'unchanged by' if now == before else 'changed by'} the addition).")
        run.unlink()
    for tmp in Path(in_dir).glob(f".*{JOURNAL_SUFFIX}{_TMP_SUFFIX}"):
        tmp.unlink(missing_ok=True)
    fsync_path(in_dir)
    return lines


def _has_journal_files(in_dir: Path) -> bool:
    return any(in_dir.glob(f".*{JOURNAL_SUFFIX}*")) or any(in_dir.glob(f"{RUN_JOURNAL_NAME}*"))


#: How long an unreadable run journal is taken to be one still being written.
_TORN_RUN_JOURNAL_AGE_S = 120.0


def _recover_claimed(in_dir: Path, *, force: bool) -> Recovery:
    result = Recovery()
    run = in_dir / RUN_JOURNAL_NAME
    journals = sorted(in_dir.glob(f".*{JOURNAL_SUFFIX}"))
    stray_tmp = sorted([*in_dir.glob(f".*{JOURNAL_SUFFIX}{_TMP_SUFFIX}"),
                        *in_dir.glob(f"{RUN_JOURNAL_NAME}{_TMP_SUFFIX}*")])
    if run.exists():
        try:
            header = json.loads(run.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            if journals:
                raise AppendJournalError(f"{run} is damaged ({exc}); nothing was changed.") from exc
            # Unreadable and no archive journalled yet: either torn by a crash
            # while being created, or being written right now by an append in
            # another process (the exclusive-create fallback opens the file
            # before writing it). Only an old one is a crash.
            age = time.time() - run.stat().st_mtime
            if age < _TORN_RUN_JOURNAL_AGE_S and not force:
                result.blocked = (f"An in-place append into {in_dir} appears to be starting "
                                  "(its run journal is still being written); it was left alone.")
                return result
            run.unlink()
            result.lines.append(f"{RUN_JOURNAL_NAME}: removed an unfinished run journal; no "
                                "archive had been touched.")
            return result
        result.blocked = "" if force else _owner_blocks(header, in_dir)
        if result.blocked:
            return result
        # A journal still being published was never followed by a write to its
        # archive, so the half-written file is simply discarded.
        for tmp in stray_tmp:
            tmp.unlink(missing_ok=True)
        result.lines = ["An in-place append into this dataset did not finish; undoing it."]
        result.lines += _rollback(in_dir)
        return result
    for tmp in stray_tmp:
        tmp.unlink(missing_ok=True)
    for journal in journals:
        try:
            header, _tail = _read_journal(journal)
        except AppendJournalError:
            header = {}
        blocked = _owner_blocks(header, in_dir) if header and not force else ""
        if blocked:
            result.blocked = blocked
            continue
        journal.unlink(missing_ok=True)
        result.lines.append(f"{_archive_for(journal).name}: its in-place append had finished; "
                            "removed the leftover journal.")
    return result


def recover_paths(*paths: Path | str) -> Recovery:
    """:func:`recover` every folder a run may read: each path, and its ``in/`` beside it.

    For a caller that holds a dataset root, an ``in/`` folder, or a raw input
    folder and does not yet know which — the Analyze runner before it resolves
    its input. Lines from every folder are kept; the first block is returned.
    """
    total = Recovery()
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        for folder in (Path(raw), Path(raw) / "in"):
            if not folder.is_dir() or _key(folder) in seen:
                continue
            seen.add(_key(folder))
            one = recover(folder)
            total.lines += one.lines
            total.blocked = total.blocked or one.blocked
    return total
