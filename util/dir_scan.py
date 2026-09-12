"""One cheap answer to "what is directly inside this directory?".

The obvious spelling of that question in Python is a listing plus a test per
entry — ``[p for p in folder.iterdir() if p.is_file()]``, or the same shape
written with ``glob("*")``. It reads well and on macOS and Linux it costs very
little: ``pathlib``'s ``is_file`` is one ``stat()`` syscall answered out of the
kernel's name cache.

**On Windows it is not one cheap syscall.** CPython implements ``os.stat`` there
by *opening a handle* to the file — ``CreateFileW`` → ``GetFileInformationByHandle``
→ ``CloseHandle`` — because ``GetFileAttributesW`` does not traverse symlinks the
way POSIX ``stat`` does. Against a local SSD that is merely slower. Against a
network share, a VPN-ed mount or a cloud-sync root (OneDrive, Dropbox), each of
those handle opens is a round trip on the wire, and a filter driver may inspect
it on the way past. A plate of 5,800 loose images, walked three times while the
Analyze pane works out what the selected folder is, cost about 17,000 handle
opens and sat for twenty minutes at under one percent CPU — a freeze with no
visible cause, because nothing was computing anything.

:func:`os.scandir` is the answer, and it is a strict improvement rather than a
trade: the ``WIN32_FIND_DATA`` that ``FindNextFileW`` already returns carries the
attributes, so ``DirEntry.is_file()`` and ``DirEntry.is_dir()`` answer from data
the enumeration paid for, with no second call at all — and the enumeration itself
returns many entries per round trip. The same code on POSIX gets the entry type
from ``d_type`` where the filesystem supplies it and falls back to a ``stat`` where
it does not, so nothing regresses there either.

Symlink semantics are the ones ``pathlib`` has, deliberately: ``DirEntry.is_file``
and ``DirEntry.is_dir`` follow symlinks by default, which is what
``Path.is_file`` / ``Path.is_dir`` do, so a link to a file is a file and a
dangling link is neither. An entry that cannot be classified at all — its metadata
is unreadable — is dropped, which is again what ``pathlib`` does:
``Path.is_file`` answers ``False`` rather than raising.

One difference is real and is the price of the saving: the entry type travels
*with* the listing, so the answer describes the directory as it was read rather
than as it is now. A file deleted between the listing and the question is still
reported as a file, where ``Path.is_file`` would have re-asked the kernel and
noticed. Nothing here can close that window without spending the stat this
module exists to avoid, and it is narrower than it looks — any listing is a
snapshot, and a file that vanishes just after ``Path.is_file`` returned ``True``
reads exactly the same way to the caller. Every call site already copes with a
path that has gone by the time it is opened.

An unreadable *directory* is a different matter and is **not** handled here. The
callers disagree about what it means — some treat it as "no input", some let it
reach the user as an error — so the ``OSError`` from opening the directory
propagates and each caller keeps the answer it already had.

Lives in ``util/`` because both sides need it: ``services`` must stay Qt-free and
the pipeline may not import ``review`` at all, and this module is stdlib
only.
"""

from __future__ import annotations

import errno
import os
import pathlib as _pathlib
from pathlib import Path

__all__ = ["files_and_subdirs", "files_in", "subdirs_in"]


def _is_not_a_file(exc: OSError) -> bool:
    """Whether ``Path.is_file()`` would answer ``False`` for *exc* rather than raise.

    ``pathlib`` draws this line itself, in ``_ignore_error``: ENOENT, ENOTDIR,
    EBADF and ELOOP mean "the entry is not of this kind", plus a set of Windows
    winerror codes for a name the filesystem cannot even evaluate. Everything
    else — an EACCES or an EIO on the entry's own metadata — it *re-raises*.

    Borrowed rather than restated, because a copy of that list here would drift
    from the semantics this module promises to reproduce, and the difference is
    the one it must not get wrong: suppressing a read failure drops an image from
    the plate and reports the shortfall nowhere, which is a run that silently
    measured fewer fields than it was handed. The fallback covers a Python whose
    private name has moved; it is the documented set, so it errs toward raising.
    """
    predicate = getattr(_pathlib, "_ignore_error", None)
    if predicate is not None:
        return bool(predicate(exc))
    return exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP)


def files_and_subdirs(folder: Path | str) -> tuple[list[Path], list[Path]]:
    """The regular files and the sub-directories directly in *folder*.

    Both lists hold real :class:`~pathlib.Path` objects built from *folder*, so
    callers keep the paths they had, and both are sorted **as paths** rather
    than by name: ``PurePath`` comparison casefolds on Windows and does not on
    POSIX, so sorting the names instead would silently reorder a mixed-case
    plate on Windows only — and the schema scan strides its sample, so a
    different order there is a different sample. One directory
    enumeration answers both, which is the point wherever a caller wants the
    files *and* the folders — asking twice is two enumerations of a directory
    that has not changed in between.

    Raises :class:`OSError` if *folder* cannot be listed at all. Entries within
    it that cannot be classified are skipped, matching ``Path.is_file``.
    """
    base = Path(folder)
    with os.scandir(base) as scan:
        entries = list(scan)
    files: list[Path] = []
    dirs: list[Path] = []
    for entry in entries:
        try:
            if entry.is_file():
                files.append(base / entry.name)
            elif entry.is_dir():
                dirs.append(base / entry.name)
        except OSError as exc:
            # Deleted under us, or a name the filesystem cannot evaluate: the
            # entry is simply not one of the files, which is what `Path.is_file`
            # answers for these. Any *other* OSError is a failure to read the
            # entry and is raised — see `_is_not_a_file`.
            if not _is_not_a_file(exc):
                raise
            continue
    files.sort()
    dirs.sort()
    return files, dirs


def files_in(folder: Path | str) -> list[Path]:
    """The regular files directly in *folder*, in path order."""
    return files_and_subdirs(folder)[0]


def subdirs_in(folder: Path | str) -> list[Path]:
    """The sub-directories directly in *folder*, in path order."""
    return files_and_subdirs(folder)[1]
