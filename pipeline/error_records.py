"""The failure records a run leaves in ``<output_dir>/errors/``: writing them, retiring them, and reading which are current.

A failure record is a small text file — a header of ``Key: value`` lines, a blank line, then a traceback or a
message — written whenever a well or one of its FOV-groups fails, or when a download has to leave a well short.
Three writers produce them: the per-well and per-FOV-group traces the pipeline worker writes
(:func:`write_well_failure`, :func:`write_fov_failure`), and the notes a remote download leaves when it had to drop
data that only a forced re-run can restore (:func:`write_note`).

**The top level of** ``errors/`` **holds the failures that still stand.** A record stops standing when its well is
later processed whole: :func:`retire_superseded` runs at that moment (from
:func:`pipeline.run_report.seal_well_complete`) and moves the well's earlier records into ``errors/superseded/``.
They are moved, never deleted — "why did this well fail last Tuesday?" keeps an answer — and everything that reads
``errors/`` globs its top level only, so an archived record is out of every count without any reader having to
know the archive exists.

Before this, ``errors/`` was never cleared and a record carried no run identity. A well that failed once and was
re-run successfully kept its traceback beside a whole output for ever, and every reader had to guess from file
times whether a record still applied: QC triage kept one if it was written after the latest run's start, Validate &
Repair counted every file and raised a ``recorded_failures`` error on a well whose output was whole. Both guesses
were wrong in a way the reader could not see — the degraded-versus-good confusion CLAUDE.md §7 forbids, here
pointed the other way: a good well reported as failed.

Which records a successful well retires:

* **Every record of an earlier run** — named by the ``Run:`` header each record carries
  (:func:`current_run_id`). This run's own records are never retired: a well this run sealed has none (a well with
  a failed group is not whole and is never sealed), and keying on the run rather than on file times means a
  coarse-grained filesystem clock cannot make a record look older than the run that wrote it.
* **Except a note that only a forced re-run resolves** (``Remedy: force``), unless the run rewrote the well from
  scratch. An incremental run that adds one new timepoint seals the well without restoring the images a download
  had to drop from its ``out.zip``; the note saying so is the only record that they are gone.

Records written before the ``Run:`` header existed have no run, so they count as an earlier run's — which they are,
since the current run writes the header. Their well is read from their ``Well:`` header, or failing that from the
filename's leading token, the same token Validate always read.

On a remote run the retirement happens on the worker host, and the download copies ``errors/`` back with ``rsync``,
which never deletes. :func:`mirror_retirements` then removes each top-level record whose identical copy the worker
archived, so the local folder agrees with the host's.

For a dataset whose records predate retirement, :func:`current_records` also reads two kinds of evidence that a
well has since completed — its completion seal, and an optional caller-supplied time the dataset's own completeness
check found it whole — and leaves out a record older than that evidence. A record is only ever left out on
evidence of a later whole well; a record with no such evidence is current, whichever run wrote it.

Qt-free and import-light: it is shipped to the remote worker (``services/remote_manifest.py``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from util.aw_safe import swallow

ERRORS_DIRNAME = "errors"
SUPERSEDED_DIRNAME = "superseded"

#: The environment variable that carries the run's identity to every worker process the run spawns.
RUN_ID_ENV = "ALLWELL_RUN_ID"

#: The ``Remedy:`` value of a note only a forced (non-incremental) re-run resolves.
REMEDY_FORCE = "force"

#: Notes written before the ``Remedy:`` header existed, recognised by name instead: both name a forced re-run
#: as their remedy in their own text.
_LEGACY_FORCE_SUFFIXES = ("_local_csv_cut_short.txt", "_local_zip_unreadable.txt")

#: Header lines read before giving up on finding the blank line that ends a header.
_MAX_HEADER_LINES = 12

_process_run_id: str | None = None


def begin_run() -> str:
    """Give this run a fresh identity, inherited by every worker process it spawns afterwards, and return it.

    Called once, at the start of a pipeline run, before any worker pool exists.
    """
    run_id = uuid.uuid4().hex[:12]
    os.environ[RUN_ID_ENV] = run_id
    return run_id


def current_run_id() -> str:
    """The identity of the run this process belongs to.

    The one :func:`begin_run` set, inherited through the environment. A process that no run began — a test calling
    a worker function directly — gets an identity of its own for its lifetime, which is still enough to tell its
    own records from any written before it.
    """
    global _process_run_id
    run_id = os.environ.get(RUN_ID_ENV, "").strip()
    if run_id:
        return run_id
    if _process_run_id is None:
        _process_run_id = uuid.uuid4().hex[:12]
    return _process_run_id


def errors_dir(output_dir: Path | str) -> Path:
    return Path(output_dir) / ERRORS_DIRNAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_token(text: str) -> str:
    from pipeline.io import _safe_well
    return _safe_well(str(text))


def _write(path: Path, header: list[tuple[str, str]], body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = max(len(key) for key, _ in header) + 1
    lines = [f"{key + ':':<{width}} {value}" for key, value in header]
    path.write_text("\n".join(lines) + "\n\n" + body, encoding="utf-8")
    return path


def write_well_failure(output_dir: Path, well_label: str, exc: BaseException) -> Path | None:
    """Persist a worker-side exception trace for *well_label*; the path, or ``None`` if it could not be written.

    ProcessPoolExecutor pickles the exception back to the parent, but if the worker's stderr is muted (e.g. a
    broken pipe to the GUI log queue) the underlying traceback never reaches the user — they only see a generic
    ``OSError: [Errno 9] Bad file descriptor`` re-raised at the parent's ``future.result()`` call site. Writing the
    full ``traceback.format_exc()`` to ``<output_dir>/errors/<well>_pid<N>.txt`` guarantees a readable trace even
    when the live log path is broken. Call it from inside the ``except`` block.
    """
    try:
        return _write(
            errors_dir(output_dir) / f"{well_label}_pid{os.getpid()}.txt",
            [("Well", str(well_label)), ("Run", current_run_id()), ("Written", _utc_now()),
             ("PID", str(os.getpid())), ("Exception type", type(exc).__name__)],
            traceback.format_exc())
    except Exception:
        return None


def write_fov_failure(output_dir: Path, well_label: str, fov_stem: str, exc: BaseException) -> Path | None:
    """Persist a worker-side exception trace for one FOV-group; the path, or ``None`` if it could not be written.

    A FOV-group failure is caught inside the worker — the well keeps going and writes a partial CSV / out.zip —
    and logged with ``exc_info=True``. If the live log is muted or the line scrolls past, the cause is lost and the
    well silently drops a timepoint, so the trace also goes to ``<output_dir>/errors/<well>_<fov>_pid<N>.txt``.

    *output_dir* must be the persistent dataset output dir (NOT the per-well ``tmp_images`` staging dir, which is
    deleted on cleanup). Call it from inside the ``except`` block.
    """
    try:
        name = f"{_safe_token(well_label)}_{_safe_token(fov_stem)}_pid{os.getpid()}.txt"
        return _write(
            errors_dir(output_dir) / name,
            [("Well", str(well_label)), ("FOV-group", str(fov_stem)), ("Run", current_run_id()),
             ("Written", _utc_now()), ("PID", str(os.getpid())), ("Exception type", type(exc).__name__)],
            traceback.format_exc())
    except Exception:
        return None


def write_note(output_dir: Path, well_label: str, name: str, message: str, *,
               exception_type: str = "", remedy: str = REMEDY_FORCE) -> Path:
    """Leave a note in ``errors/`` that *well_label* lost data outside a run, and return its path.

    For the download paths that had to drop data a later incremental run cannot recover. *remedy* is what resolves
    it — :data:`REMEDY_FORCE`, a re-run that rewrites the well from scratch — and is what keeps the note through an
    incremental run that seals the well without restoring what the note describes. Raises ``OSError`` when the note
    cannot be written, so the caller decides how loudly to say so.

    A note carries no ``Run:``: it is written by a download, between runs, and belongs to none of them — so the
    next run of the well that resolves it is never mistaken for the one that wrote it.
    """
    header = [("Well", str(well_label)), ("Written", _utc_now())]
    if exception_type:
        header.append(("Exception type", exception_type))
    if remedy:
        header.append(("Remedy", remedy))
    return _write(errors_dir(output_dir) / name, header, message.rstrip("\n") + "\n")


@dataclass(frozen=True)
class ErrorRecord:
    """One failure record in ``errors/``, as its header and file name describe it."""

    path: Path
    well: str
    run: str
    fov_group: str
    exception_type: str
    remedy: str
    mtime: float

    @property
    def needs_force(self) -> bool:
        return self.remedy == REMEDY_FORCE


def _canonical(well: str) -> str:
    from util.well_token import canonical_well_label
    return canonical_well_label(str(well)) or ""


def read_record(path: Path) -> ErrorRecord | None:
    """*path*'s header as an :class:`ErrorRecord`, or ``None`` when the file cannot be read or names no well."""
    fields: dict[str, str] = {}
    try:
        mtime = path.stat().st_mtime
        with path.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(_MAX_HEADER_LINES):
                line = fh.readline()
                if not line.strip():
                    break
                key, sep, value = line.partition(":")
                if sep:
                    fields.setdefault(key.strip().lower(), value.strip())
    except OSError:
        return None
    well = _canonical(fields.get("well", "")) or _canonical(path.stem.split("_")[0])
    if not well:
        return None
    remedy = fields.get("remedy", "")
    if not remedy and path.name.endswith(_LEGACY_FORCE_SUFFIXES):
        remedy = REMEDY_FORCE
    return ErrorRecord(
        path=path, well=well, run=fields.get("run", ""), fov_group=fields.get("fov-group", ""),
        exception_type=fields.get("exception type", ""), remedy=remedy, mtime=mtime)


def standing_records(output_dir: Path | str) -> list[ErrorRecord]:
    """Every readable record at the top level of ``errors/``, by file name. The archive is not read."""
    folder = errors_dir(output_dir)
    try:
        paths = sorted(folder.glob("*.txt"))
    except OSError:
        return []
    return [rec for rec in map(read_record, paths) if rec is not None]


def _archive_path(folder: Path, name: str) -> Path:
    target = folder / name
    n = 1
    while target.exists():
        target = folder / f"{Path(name).stem}.{n}{Path(name).suffix}"
        n += 1
    return target


def retire_superseded(output_dir: Path | str, well_label: str, *, rewritten: bool) -> list[Path]:
    """Move *well_label*'s earlier runs' records into ``errors/superseded/``; the archived paths.

    Call it only when the well has just been processed whole (:func:`pipeline.run_report.seal_well_complete` is
    the one caller). *rewritten* says this run rebuilt the well's output from scratch rather than adding to it, and
    only then is a ``Remedy: force`` note retired too. Never raises: a record that cannot be moved is logged and
    left standing, which over-reports a failure rather than hiding one.
    """
    well = _canonical(well_label)
    if not well:
        return []
    run = current_run_id()
    retired: list[Path] = []
    archive = errors_dir(output_dir) / SUPERSEDED_DIRNAME
    for rec in standing_records(output_dir):
        if rec.well != well or rec.run == run or (rec.needs_force and not rewritten):
            continue
        with swallow(f"could not archive {rec.path.name}, a superseded failure record of well {well_label}",
                     level=logging.WARNING):
            archive.mkdir(parents=True, exist_ok=True)
            target = _archive_path(archive, rec.path.name)
            os.replace(rec.path, target)
            retired.append(target)
    return retired


def retire_force_notes(output_dir: Path | str, wells) -> list[Path]:
    """Move the ``Remedy: force`` notes of *wells* into ``errors/superseded/``; the archived paths.

    For a remote run, after the fetch has placed and verified a whole, forced re-run of each of *wells*. The notes
    that ask for that re-run (``<well>_local_csv_cut_short.txt``, ``<well>_local_zip_unreadable.txt``) are written
    by a *local* download, so the worker host never had them: its own :func:`retire_superseded` archives nothing for
    them and :func:`mirror_retirements` finds no copy to mirror, and they stood for ever after the re-run they asked
    for, listing a rebuilt well as failed. Only force notes move: any other record is retired by the host's seal and
    mirrored. Never raises; a note that cannot be moved is logged and left standing.
    """
    wanted = {_canonical(w) for w in wells} - {""}
    retired: list[Path] = []
    if not wanted:
        return retired
    archive = errors_dir(output_dir) / SUPERSEDED_DIRNAME
    for rec in standing_records(output_dir):
        if rec.well not in wanted or not rec.needs_force:
            continue
        with swallow(f"could not archive {rec.path.name}, a note well {rec.well}'s forced re-run has answered",
                     level=logging.WARNING):
            archive.mkdir(parents=True, exist_ok=True)
            target = _archive_path(archive, rec.path.name)
            os.replace(rec.path, target)
            retired.append(target)
    return retired


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def mirror_retirements(output_dir: Path | str) -> int:
    """Remove each top-level record whose byte-identical copy is in ``errors/superseded/``; how many were removed.

    After a remote run's ``errors/`` is copied back: the worker host archived the records its successful wells
    superseded, but ``rsync`` only adds files, so the copies an earlier download brought back would otherwise
    stand beside the archive for ever. Only an identical copy is removed — the archive then holds the same bytes —
    so a record that merely shares a name with an archived one is left alone.
    """
    folder = errors_dir(output_dir)
    archive = folder / SUPERSEDED_DIRNAME
    if not archive.is_dir():
        return 0
    archived = {d for d in map(_digest, archive.glob("*.txt")) if d}
    removed = 0
    for rec in standing_records(output_dir):
        if _digest(rec.path) in archived:
            with swallow(f"removing {rec.path.name}, which the worker host archived as superseded"):
                rec.path.unlink()
                removed += 1
    return removed


def _seal_times(output_dir: Path | str) -> dict[str, float]:
    """``{canonical well: mtime}`` of every completion seal in *output_dir*."""
    from pipeline.run_report import COMPLETE_DIRNAME, well_of_seal

    times: dict[str, float] = {}
    folder = Path(output_dir) / COMPLETE_DIRNAME
    if not folder.is_dir():
        return times
    with swallow(f"reading the completion seals in {folder}"):
        for path in folder.iterdir():
            well = well_of_seal(path.name)
            if well:
                times[well] = max(times.get(well, 0.0), path.stat().st_mtime)
    return times


def current_records(output_dir: Path | str, *, whole_since: dict[str, float] | None = None) -> list[ErrorRecord]:
    """The records that still stand: the top level of ``errors/``, less any a later whole well has superseded.

    Retirement (:func:`retire_superseded`) keeps the top level current for every well processed since it existed.
    This also covers records older than that: a record is left out when its well was found whole *after* the record
    was written — by the well's completion seal, or by *whole_since*, a ``{canonical well: epoch seconds}`` map of
    when the caller's own evidence (a completeness check) last found the well whole. A ``Remedy: force`` note is
    never left out on that evidence, since an incremental run can seal a well without resolving it.
    """
    evidence = _seal_times(output_dir)
    for well, when in (whole_since or {}).items():
        key = _canonical(well) or str(well)
        evidence[key] = max(evidence.get(key, 0.0), float(when))
    return [rec for rec in standing_records(output_dir)
            if rec.needs_force or rec.mtime >= evidence.get(rec.well, float("-inf"))]
