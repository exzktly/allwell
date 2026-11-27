"""Which file a converter reads when it is handed a watched folder.

A converter linked to a Watch folder job is given that folder as its source
when it starts (``JobQueue.materialise`` fills ``source`` from the watch's
output). The ND2 and OME-TIFF converters read one file, so the file has to be
chosen then, from whatever the acquisition wrote — that is the parameter a
queued conversion cannot know when it is set up. The CZI converter reads a
folder as one plate already (ZEN's split-scene export), so for it the folder
*is* the source and nothing is chosen.

The rule is deliberately narrow: exactly one matching file is used; none, or
more than one, fails the job naming what was found. Picking the newest or the
largest of several would convert one plate and report success while the others
sat unconverted — a complete-looking result for part of the input
(CLAUDE.md §7). A person can always point the job at one file by hand.

Qt-free.
"""

from __future__ import annotations

import os
from pathlib import Path

#: File names no acquisition writes and that a person browsing the folder can
#: create. Compared case-insensitively. Shared with the Watch folder tool, which
#: leaves the same files out of the snapshots it compares.
IGNORED_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini", ".localized"})
#: Name prefixes of the same kind: AppleDouble sidecars and Office lock files.
IGNORED_PREFIXES = ("._", "~$")

#: How many of the files found a failure names before it says "and N more".
_NAMED = 5


def is_ignored(name: str) -> bool:
    """Whether a file called *name* is housekeeping rather than data."""
    lowered = name.lower()
    return lowered in IGNORED_NAMES or lowered.startswith(IGNORED_PREFIXES)


class WatchedSourceError(Exception):
    """No single source file could be chosen. The message is user-facing."""


def matching_files(folder: str | os.PathLike, suffixes: tuple[str, ...]) -> list[Path]:
    """Every file below *folder* whose name ends in one of *suffixes*.

    Case-insensitive, and compared on the whole name so a two-part suffix such
    as ``.ome.tif`` works. Housekeeping files and hidden folders are skipped,
    as the watch itself skips them.
    """
    wanted = tuple(s.lower() for s in suffixes)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if not is_ignored(name) and name.lower().endswith(wanted):
                found.append(Path(dirpath) / name)
    return found


def resolve_source(source: str, suffixes: tuple[str, ...], *,
                   folder_is_a_source: bool, noun: str) -> str:
    """The path a converter should read for *source*.

    A file is returned unchanged, and so is a folder when the converter reads
    folders itself (*folder_is_a_source*). Otherwise the folder must hold
    exactly one file ending in one of *suffixes*; *noun* names the format in
    the message (``"ND2"``).
    """
    path = Path(source)
    if not source or not path.is_dir() or folder_is_a_source:
        return source
    found = matching_files(path, suffixes)
    if len(found) == 1:
        return str(found[0])
    kinds = " / ".join(suffixes)
    if not found:
        raise WatchedSourceError(
            f"{path} holds no {noun} file ({kinds}) to convert.")
    names = ", ".join(str(p.relative_to(path)) for p in found[:_NAMED])
    more = f", and {len(found) - _NAMED} more" if len(found) > _NAMED else ""
    raise WatchedSourceError(
        f"{path} holds {len(found)} {noun} files ({names}{more}); a queued "
        "conversion of a folder converts exactly one. Point the job at the "
        "file to convert (Edit…), or queue one conversion per file.")
