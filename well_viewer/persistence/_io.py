"""Shared I/O helpers for persistence modules.

Centralises the tmp + ``os.replace`` pattern so a crash, signal, or full-disk
condition mid-write can't leave a truncated JSON sidecar, and the
read-modify-write pattern layered on top of it (:func:`update_json_atomic`) so
one writer's update cannot silently discard another's.

Windows-specific note: ``os.replace`` and ``open(..., "w")`` can both raise
``PermissionError`` when another process briefly holds a handle on the
target — antivirus scanners (Defender / cloud-sync agents like
OneDrive/Dropbox), Excel viewers, or the IDE indexer all do this for
tens to hundreds of milliseconds at a time. On POSIX neither call cares
about other readers. The retry-with-backoff loops below absorb those
transient handle conflicts so the smFISH writer doesn't surface
``[WinError 5] Access is denied`` to the user mid-Apply.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")


# --------------------------------------------------------------------------
# Deferring a write to the background writer
# --------------------------------------------------------------------------
#
# Thread-local rather than a module global, and that is load-bearing: the
# background writer runs these same functions to do the actual I/O, and on its
# thread there is no sink, so the call executes inline instead of re-queueing
# itself for ever.
_sink = threading.local()


@contextmanager
def background_writes(submit: "Callable[[Job], None]") -> Iterator[None]:
    """Within this block, ``deferrable=True`` writes are handed to *submit*.

    *submit* takes one no-arg callable returning ``(ok, detail)`` and is
    expected to run it on another thread —
    :class:`well_viewer.persistence._writer.PersistenceWriter` is the
    implementation, and ``PersistenceRegistry._flush`` is the only caller.
    Nesting restores the outer sink; a thread with no sink writes inline.

    Only calls that pass ``deferrable=True`` are redirected. That opt-in is at
    the call site on purpose: deferring changes what the return value *means*
    (see :func:`update_json_atomic`), so only a site whose result is not
    inspected in the same call may take it.
    """
    prev = getattr(_sink, "submit", None)
    _sink.submit = submit
    try:
        yield
    finally:
        _sink.submit = prev


#: A no-arg callable performing the I/O and returning ``(ok, detail)``.
Job = "Callable[[], tuple[bool, str]]"


def defer_io(job) -> bool:
    """Hand *job* to the active background writer. ``False`` when there is none.

    ``False`` is the ordinary answer everywhere except inside a debounced
    autosave, and the caller then performs the I/O itself.
    """
    submit = getattr(_sink, "submit", None)
    if submit is None:
        return False
    submit(job)
    return True


# Bounded retry budget for transient Windows handle conflicts (AV,
# cloud sync, etc. typically release within 50–500ms). At 100ms base
# with exponential backoff we burn 100+200+400+800+1600 = 3.1s max
# before propagating — long enough to outlast a normal AV scan, short
# enough that a real lock (file genuinely held open by Excel) still
# surfaces an error promptly.
_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY_S = 0.10


def _unique_tmp_path(path: Path) -> Path:
    """Return a per-call tmp filename so concurrent atomic_write callers
    (different processes, retry-driven cleanup races) never collide on
    a single fixed ``<path>.tmp`` name. Includes pid + a uuid4 segment."""
    return path.with_suffix(
        f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    )


def _retry_on_permission_error(op_name: str, op):
    """Run *op* (a no-arg callable) with exponential backoff retries on
    ``PermissionError``. Re-raises on the final attempt — callers decide
    whether to log + swallow or propagate. *op_name* is included in
    nothing user-facing; it exists so debuggers can step on a meaningful
    frame label."""
    last_exc: PermissionError | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return op()
        except PermissionError as exc:
            last_exc = exc
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_BASE_DELAY_S * (2 ** attempt))
    # Unreachable — the final attempt either returns or re-raises.
    assert last_exc is not None
    raise last_exc


def _replace_with_text(path: Path, text: str, *, durable: bool) -> None:
    """Land *text* at *path* through a uniquified tmp file + ``os.replace``.

    The half of :func:`atomic_write_json` that touches the filesystem, split out
    so the serialisation can stay on the calling thread while this runs on the
    background writer. Raises ``OSError`` exactly as before.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp_path(path)

    def _write_tmp() -> None:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            if not durable:
                return
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync isn't supported on every filesystem (e.g. some
                # network mounts). The replace below is still atomic on
                # POSIX and Windows.
                swallowed("atomic_write_json: fsync unsupported on this fs")

    try:
        _retry_on_permission_error("write_tmp", _write_tmp)
        _retry_on_permission_error("replace", lambda: os.replace(tmp, path))
    finally:
        # If replace didn't run (exception during write), clean up the
        # tmp file rather than leaving it behind. unlink can also race
        # against AV; retry but never propagate from the cleanup arm.
        try:
            if tmp.exists():
                _retry_on_permission_error("unlink", tmp.unlink)
        except OSError:
            swallowed("atomic_write_json: tmp cleanup unlink failed")


def atomic_write_json(path: Path, data: Any, *, indent: int = 2,
                      durable: bool = True, deferrable: bool = False) -> None:
    """Serialise *data* as JSON to *path*, atomically.

    Writes to a uniquified ``<path>.<pid>.<rand>.tmp`` then
    ``os.replace`` onto the destination so a reader never sees a
    partial file. Parents are created on demand. Both the initial
    ``open`` and the final ``os.replace`` retry on Windows
    ``PermissionError`` (AV / cloud sync / viewer holding the handle).

    ``durable=False`` skips the ``fsync`` before the replace. The replace is
    still atomic, so no reader ever sees a partial file; what is given up is
    the guarantee that the bytes have reached the platter before this returns,
    which only matters if the machine loses power in the next moment. Debounced
    autosaves on the GUI thread pass False: an fsync on a network share is a
    visible pause there, and the value is rewritten seconds later anyway.

    Raises ``OSError`` on filesystem failure — callers decide whether to
    catch (usually yes, with a logger.warning) or propagate (Save-via-dialog
    paths where the user should see the error).
    """
    path = Path(path)
    # Serialise here, on the calling thread, whether or not the write is
    # deferred. It snapshots *data* — the persistence doc handed in by
    # ``_doc.write`` is the live in-memory cache, and a background thread
    # iterating it while the GUI thread edits it would raise "dictionary
    # changed size during iteration" and lose the write. It also keeps a
    # payload that cannot be serialised raising here, in the caller's frame,
    # rather than on a thread with nobody to tell.
    text = json.dumps(data, indent=indent)
    if deferrable and defer_io(
            lambda: _write_result(path, text, durable=durable)):
        return
    _replace_with_text(path, text, durable=durable)


def _write_result(path: Path, text: str, *, durable: bool) -> tuple[bool, str]:
    """:func:`_replace_with_text` in the ``(ok, detail)`` shape a job returns."""
    try:
        _replace_with_text(path, text, durable=durable)
    except OSError as exc:
        return False, f"could not write {path.name}: {exc}"
    return True, ""


def atomic_write_text(path: Path, text: str) -> None:
    """Same shape as ``atomic_write_json`` but for arbitrary text payloads.

    Used by ``smfish_worker`` to write per-well CSVs. Inherits the same
    retry-on-``PermissionError`` and uniquified tmp filename so a
    concurrent AV scan on a freshly-written CSV doesn't surface
    ``[WinError 5] Access is denied`` mid Apply-Global-Threshold.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp_path(path)

    def _write_tmp() -> None:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                swallowed("atomic_write_text: fsync unsupported on this fs")

    try:
        _retry_on_permission_error("write_tmp", _write_tmp)
        _retry_on_permission_error("replace", lambda: os.replace(tmp, path))
    finally:
        try:
            if tmp.exists():
                _retry_on_permission_error("unlink", tmp.unlink)
        except OSError:
            swallowed("atomic_write_text: tmp cleanup unlink failed")


def update_json_atomic(
    path: Path,
    mutate: "Callable[[dict], dict | None]",
    *,
    create_missing: bool = False,
    attempts: int = 4,
    durable: bool = True,
    deferrable: bool = False,
) -> bool:
    """Read *path*, apply *mutate* to the object in it, and write it back.

    Returns ``True`` when the file was written, ``False`` on every refusal:
    a missing file (unless *create_missing*), a payload that is not a JSON
    object, a *mutate* that returned ``None``, an unwritable destination, or a
    file being rewritten so persistently by somebody else that the update could
    not be landed on a copy it had actually read.

    **Why this exists.** ``pipeline_info.json`` has several writers — the smFISH
    thresholds, the cell-gating block, the sample definitions, and the pipeline
    itself, which owns ``fluor_tokens``, ``schema`` and ``available_timepoints``
    and rewrites the file at the end of a run. Analyze and Review share one
    window, so a run can finish while the viewer is between its own read and its
    own write. Each writer preserved the keys it *read*, which makes a lost
    update look like the pipeline's keys reverting on their own — the symptom of
    a viewer that suddenly cannot see a channel or a timepoint the run produced.

    **What the guard is, and what it is not.** The file is compared byte-for-byte
    against the copy that was read, immediately before the replace; a difference
    means another writer landed in between, so the mutation is re-applied to the
    new content instead of overwriting it. That is optimistic concurrency, not a
    lock: it narrows the window from "however long the caller takes to build its
    block" — arbitrarily long, since callers read state, format values and log —
    to the microseconds between the final comparison and ``os.replace``. It
    needs no cooperation from the other writer, which is what makes it usable
    here at all: the pipeline runs in a separate process (often on a remote
    host) and takes no lock this code could share. A residual race remains and
    is deliberate; the alternative is a lock protocol every writer of the file,
    in every process, would have to honour.

    *mutate* is called with the parsed object and returns the object to write,
    or ``None`` to abandon the update. It must be free of side effects the
    caller cannot afford to repeat, because a concurrent write re-runs it.

    *durable* is passed to :func:`atomic_write_json`; pass False from a
    debounced GUI-thread autosave, where an fsync per keystroke-pause is a
    visible stall on a network share.

    *deferrable* lets the whole read-modify-verify-write loop run on the
    background writer when one is active (see :func:`background_writes`). The
    seven filesystem round trips above are what froze the window for about a
    second per autosave on a network share, and the loop moves as a unit
    because the re-read guard is only worth anything next to the write it
    guards. **It changes what the return value means:** a deferred call returns
    ``True`` for "accepted for writing", not "written", and a later failure is
    reported by the writer — logged, and put on the status line — instead of
    returned here. Only pass it from a call site that does not inspect the
    result, and note that *mutate* then runs on the writer thread, so it must
    read nothing but the ``existing`` dict it is handed.
    """
    path = Path(path)
    if deferrable and defer_io(lambda: (
            _update_json_now(path, mutate, create_missing=create_missing,
                             attempts=attempts, durable=durable),
            f"{path.name} refused the update; see the log",
    )):
        return True
    return _update_json_now(path, mutate, create_missing=create_missing,
                            attempts=attempts, durable=durable)


def _update_json_now(
    path: Path,
    mutate: "Callable[[dict], dict | None]",
    *,
    create_missing: bool,
    attempts: int,
    durable: bool,
) -> bool:
    """:func:`update_json_atomic`'s loop, always inline on the calling thread."""
    for attempt in range(max(1, attempts)):
        try:
            raw = path.read_bytes() if path.exists() else None
        except OSError as exc:
            _logger.warning("Could not read %s: %s", path, exc)
            return False
        if raw is None:
            if not create_missing:
                return False
            doc: Any = {}
        else:
            try:
                doc = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                _logger.warning("Could not parse %s: %s", path, exc)
                return False
            if not isinstance(doc, dict):
                _logger.warning("%s is not a JSON object; not updating.", path)
                return False

        updated = mutate(doc)
        if updated is None:
            return False

        # Re-read immediately before the write and compare against the bytes
        # the mutation was built from. Content, not mtime: two writes inside one
        # filesystem timestamp tick are exactly the case this has to catch.
        try:
            current = path.read_bytes() if path.exists() else None
        except OSError as exc:
            _logger.warning("Could not re-read %s: %s", path, exc)
            return False
        if current != raw:
            _logger.info(
                "%s changed while it was being updated (attempt %d of %d); "
                "re-applying the change to the new contents.",
                path, attempt + 1, max(1, attempts),
            )
            continue

        try:
            atomic_write_json(path, updated, durable=durable)
        except OSError as exc:
            _logger.warning("Could not write %s: %s", path, exc)
            return False
        return True

    _logger.warning(
        "Gave up updating %s after %d attempts — another writer is rewriting "
        "it continuously. The change was not saved.", path, max(1, attempts),
    )
    return False
