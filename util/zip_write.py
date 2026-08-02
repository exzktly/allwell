"""Atomic, verified publication of the per-well ``.zip`` archives.

All-Well hands two kinds of archive to the user: the per-well *input* archives
in ``in/`` (``<WELL>.zip``) and the per-well *output* archives in ``out/``
(``<WELL>_out.zip``). Both are written from several call sites — the pipeline's
compressor, the incremental merge, the Analyze-side input normaliser and the
remote runner's upload staging — and every one of them has to honour the same
two invariants, because these archives are opened by hand in Finder / Explorer
at least as often as they are opened by All-Well itself:

* **A published archive is readable.** The staged bytes are fsynced and the
  central directory is re-read from the staging file before it takes the
  target's name, so a run that is hard-killed (or a network share that drops)
  mid-write leaves the staging file behind rather than a truncated
  ``<WELL>.zip``. A truncated archive is one of the things macOS Archive
  Utility reports as *"Unable to expand …. It is in an unsupported format."*
* **A published archive has members.** A zero-member zip is structurally valid
  — Python and ``unzip`` read it as an empty archive — but macOS Archive
  Utility refuses to expand it, and All-Well reads it as a well whose images
  have all vanished. A caller with nothing to write must not create the file at
  all; :func:`atomic_zip` raises :class:`ZipWriteError` rather than publish one.

Qt-free and stdlib-only: the pipeline, ``services/`` and the GUI all write
well archives, so the helper has to live where the pipeline can import it.
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["ZipWriteError", "atomic_zip", "fsync_path", "staging_path", "zip_members"]

# Staging files are named so a later inspection can tell them apart from a
# user's own file: the prefix is what the pipeline's image scanners skip, and
# the pid keeps two concurrent well workers off each other's staging file.
_TMP_PREFIX = "__aw_tmp_"


class ZipWriteError(RuntimeError):
    """A staged archive failed its pre-publication check.

    The target is untouched when this is raised — either it never existed or it
    still holds the previous, complete archive.
    """


def fsync_path(path: Path | str) -> None:
    """Open *path* read-only and fsync it; ignore filesystems that don't allow it.

    Best-effort by design: network shares and some FUSE mounts reject fsync on
    a directory (or on any handle), and a durability hint that raises is worse
    than one that quietly does nothing.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def staging_path(target: Path) -> Path:
    """The staging file :func:`atomic_zip` builds *target* on."""
    return target.with_name(f"{_TMP_PREFIX}{target.name}.pid{os.getpid()}")


def zip_members(path: Path | str) -> list[str]:
    """Member names of the archive at *path*, read from its central directory.

    Cheap regardless of archive size — the directory is a footer, so this costs
    the same on a 20 MB well and a 20 GB one, and it never decompresses a
    member. Raises :class:`zipfile.BadZipFile` or ``OSError`` when the archive
    is truncated, empty-as-in-zero-bytes, or not a zip at all.
    """
    with zipfile.ZipFile(path, "r") as zf:
        return zf.namelist()


@contextmanager
def atomic_zip(
    target: Path | str,
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    compresslevel: int | None = None,
    durable: bool = True,
) -> Iterator[zipfile.ZipFile]:
    """Build the archive *target* on a staging file, publish it once verified.

    Yields a write-mode :class:`zipfile.ZipFile`; add members to it with
    ``write()`` / ``writestr()`` as usual. On a clean exit the staging file is
    fsynced, re-opened, checked to hold exactly the members that were written,
    and only then renamed over *target*. Any exception — including
    :class:`ZipWriteError` from the checks — removes the staging file and
    leaves *target* as it was.

    Raises :class:`ZipWriteError` if nothing was written to the archive: that
    means a caller-side guard is missing, and publishing the member-less result
    would leave the user with a ``<WELL>.zip`` that macOS refuses to expand.

    *compresslevel* is passed straight through to :class:`zipfile.ZipFile`.
    The Datasets tools pass ``1``: re-deflating already-dense microscopy image
    data at the default level 6 spends a lot of CPU for negligible size
    reduction, and those callers are performing a container-level copy rather
    than a size-optimization pass.

    ``durable=False`` skips the two ``fsync`` calls, keeping the staging file,
    the member-list verification and the atomic rename. Everything a *reader*
    can observe is unchanged — the target either does not exist or holds a
    complete, verified archive, never a readable short one — and only
    durability across a power loss is given up. Forcing a freshly written
    archive to disk costs the bulk of the write (measured: ~77% of the time to
    publish a filtered plate), which is worth paying when the archive is the
    only copy of data a hard-killed pipeline just computed, and not worth
    paying for a copy whose source is still sitting on disk beside it and which
    the tool's own in-progress marker already tells the next attempt to redo.
    Callers computing new data should leave it ``True``.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = staging_path(target)
    published = False
    try:
        with zipfile.ZipFile(
            tmp, "w", compression=compression, compresslevel=compresslevel,
        ) as zf:
            yield zf
            # Read the member list before close() — the write-mode handle knows
            # what it was asked to store; the re-read below says what landed.
            written = sorted(zf.namelist())
        if not written:
            raise ZipWriteError(
                f"refusing to publish {target.name}: nothing was written to it "
                "(a member-less archive is one macOS will not expand)"
            )
        if durable:
            fsync_path(tmp)
        try:
            landed = sorted(zip_members(tmp))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ZipWriteError(
                f"staged archive for {target.name} did not read back: {exc}"
            ) from exc
        if landed != written:
            raise ZipWriteError(
                f"staged archive for {target.name} read back with "
                f"{len(landed)} member(s), {len(written)} were written"
            )
        os.replace(str(tmp), str(target))
        published = True
        if durable:
            # The rename itself is metadata: fsync the directory so a crash
            # right after this can't resurrect the older archive (or no
            # archive).
            fsync_path(target.parent)
    finally:
        if not published:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
