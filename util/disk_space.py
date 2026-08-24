"""Free-space primitives and the one wording for "this volume filled up".

Both halves of All-Well have to answer the same two questions — *how much room
is left where I am about to write?* and *what do I tell the user when the answer
turns out to be none?* — and neither can import the other's module:

* the **pipeline** sizes a run's staging and results against the volumes that
  hold them (:mod:`pipeline.capacity`, which needs numpy-free stdlib only but
  lives beside the imaging stack),
* the shared **retry shield** (:mod:`util.retry_io`) has to rewrite an
  ``ENOSPC`` as it passes through, and is used by the ``services/`` remote
  client, which must stay free of the imaging stack entirely.

So the primitives live here: stdlib-only, Qt-free, no imaging imports.

:func:`space_message` is deliberately the *only* wording for a full volume. A
bare ``OSError: [Errno 28] No space left on device`` names nothing, and the
volume it happened on is very often not the one the user would think to check:
the input archives are compressed, the staging is not, and the staging defaults
to the home volume rather than the one holding the dataset.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

_LOGGER = logging.getLogger("allwell.disk_space")

__all__ = ["directory_bytes", "free_bytes", "same_volume", "space_message"]


def free_bytes(path: Path | str) -> int | None:
    """Free bytes on the volume holding *path*, or ``None`` if unknowable.

    *path* need not exist — its parent is stat'ed, which is what makes this
    usable as a preflight on a file that is about to be created.
    """
    try:
        return shutil.disk_usage(Path(path).parent).free
    except OSError:
        return None


def same_volume(a: Path | str, b: Path | str) -> bool:
    """Are *a* and *b* on one filesystem? ``False`` when it can't be determined.

    Answering "no" on an error is the conservative direction for the callers
    here: two needs are then checked against their own free figures rather than
    summed, which under-warns rather than refusing a run that would have fit.
    """
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def directory_bytes(path: Path | str) -> int:
    """Total size of every file under *path*; 0 when it can't be walked.

    ``os.walk`` swallows the errors it meets — it yields nothing for a
    directory it cannot list and carries on — so the outer handler here never
    fired and an unreadable subtree simply counted zero bytes. The figure then
    fed a pre-flight free-space check that concluded the run would fit. The
    ``onerror`` hook below is the only way to hear about it.
    """
    total = 0

    def _unreadable(exc: OSError) -> None:
        _LOGGER.warning(
            "Could not read %s while sizing %s — its contents are counted as "
            "zero bytes, so any free-space estimate built on this figure is a "
            "lower bound: %s", getattr(exc, "filename", "?"), path, exc,
        )

    try:
        for root, _dirs, files in os.walk(path, onerror=_unreadable):
            for name in files:
                try:
                    total += os.stat(os.path.join(root, name)).st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def space_message(what: str, path: Path | str | None, *, need: int | None = None) -> str:
    """One sentence for a genuine ``ENOSPC``, naming the volume and its space.

    *what* names the target being written, *path* is where it was going, and
    *need* (when known) is how many bytes this particular write wanted.
    """
    where = Path(path).parent if path is not None else None
    free = free_bytes(path) if path is not None else None
    parts = [f"No space left on device while writing {what}"]
    if where is not None:
        parts.append(f" on {where}")
    detail = []
    if free is not None:
        detail.append(f"{free:,} byte(s) free")
    if need:
        detail.append(f"{need:,} byte(s) needed for this write")
    if detail:
        parts.append(f" ({'; '.join(detail)})")
    parts.append(
        ". A run needs far more room than its inputs occupy: the input archives "
        "are compressed, while the per-well staging (~6 float32 intermediates "
        "per FOV-group, times as many wells as run at once) and every "
        "<well>_out.zip the plate has produced so far are not. For a local run, "
        "unless ALLWELL_SCRATCH_DIR (Analyze -> Scratch disk) points elsewhere, "
        "that staging is on ~/.allwell/scratch and may be competing with the "
        "output for this same volume. For a remote run it is the remote's own "
        "scratch that carries it (the host's Remote scratch dir), and on this "
        "machine only the input and output volumes are used. Free space or move "
        "the scratch, then re-run with --incremental to resume what is missing."
    )
    return "".join(parts)
