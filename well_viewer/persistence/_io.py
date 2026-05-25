"""Shared I/O helpers for persistence modules.

Centralises the tmp + ``os.replace`` pattern so a crash, signal, or full-disk
condition mid-write can't leave a truncated JSON sidecar.

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
import os
import time
import uuid
from pathlib import Path
from typing import Any


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


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Serialise *data* as JSON to *path*, atomically.

    Writes to a uniquified ``<path>.<pid>.<rand>.tmp`` then
    ``os.replace`` onto the destination so a reader never sees a
    partial file. Parents are created on demand. Both the initial
    ``open`` and the final ``os.replace`` retry on Windows
    ``PermissionError`` (AV / cloud sync / viewer holding the handle).

    Raises ``OSError`` on filesystem failure — callers decide whether to
    catch (usually yes, with a logger.warning) or propagate (Save-via-dialog
    paths where the user should see the error).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp_path(path)

    def _write_tmp() -> None:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync isn't supported on every filesystem (e.g. some
                # network mounts). The replace below is still atomic on
                # POSIX and Windows.
                pass

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
            pass


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
                pass

    try:
        _retry_on_permission_error("write_tmp", _write_tmp)
        _retry_on_permission_error("replace", lambda: os.replace(tmp, path))
    finally:
        try:
            if tmp.exists():
                _retry_on_permission_error("unlink", tmp.unlink)
        except OSError:
            pass
