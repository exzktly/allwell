"""Scratch, transit and fingerprint helpers for a remote run.

The leaves of ``services/remote_runner.py``'s staging work: where transient
bytes are put while a transfer is in flight, how a stale one is swept, whether
an archive arrived whole, and what the user's own input archives hash to.

They are here rather than there because they are genuinely separable — nine
functions whose only tie to the rest of that module was one constant — and
because that module is 8,000 lines with a line budget, whose whole point is
that a cohesive cluster leaves rather than the file growing again.

Stdlib and ``util`` only, deliberately: nothing here knows about ssh, rsync, a
host, or a run. ``remote_runner`` re-exports every name, so call sites and
tests keep importing them from where they always did.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

from util.well_token import is_valid_well_name

__all__ = [
    "TRANSIT_SUBDIR",
    "_transit_refused",
    "_new_scratch_dir",
    "_new_transit_dir",
    "_scratch_dir",
    "_transit_dir",
    "_well_zips_in",
    "_zip_is_complete",
    "fingerprint_archives",
    "source_fingerprint",
    "sweep_stale_scratch",
]


def _well_zips_in(input_dir: Path):
    """Every ``<WELL>.zip`` directly under *input_dir*.

    A filesystem glob cannot judge a well. ``[A-H][0-9]*.zip`` — what this
    replaced — accepted ``A13.zip`` and ``A1_out.zip`` while rejecting a
    lower-case ``a1.zip``, which is the opposite of what the folder-side scan a
    few lines away decides with ``is_valid_well_name``. Two rules for "is this a
    well" inside one function is the §15 defect in miniature: staging would take
    ``a1/`` but not ``a1.zip``, and ``A13.zip`` but not ``A13/``.
    """
    return (p for p in input_dir.glob("*.zip") if is_valid_well_name(p.stem))




def fingerprint_archives(paths) -> str:
    """Hash a set of archives by ``(basename, size, whole-second mtime)``.

    One definition, two questions. :meth:`DatasetCache.cache_key` asks it of the
    archives being *uploaded*, to decide whether the remote already holds them.
    :func:`source_fingerprint` asks it of the archives the user actually has, to
    decide whether two runs are looking at the same images at all. Those answers
    diverge exactly when staging rewrites — which is the whole reason the second
    one is needed — so they must not be two hashes that could drift apart in
    any other way.
    """
    h = hashlib.blake2b(digest_size=16)
    for path in paths:
        stat = path.stat()
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(str(stat.st_size).encode("ascii"))
        h.update(b"\0")
        h.update(str(int(stat.st_mtime)).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def source_fingerprint(input_dir: "Path | None") -> str:
    """Fingerprint the user's **own** input archives, or ``""`` if unreadable.

    The discriminator the re-attach turns on, and the reason it is not enough to
    key a recorded remote address on the pipeline arguments alone.

    The upload cache key churns for two completely different reasons: staging
    rewrote the archives (same images, new files, new mtimes) or the user
    replaced the images. Only the first is a reason to go looking for a previous
    run's output — the second means the old outputs describe images that no
    longer exist. Keyed on the argv alone the two are indistinguishable, and a
    user who re-exported a plate with corrected images, changed no setting and
    did not tick Force would have re-attached to the stale directory, been given
    a full cache hit against it, and had the previous run's numbers fetched back
    and reported as a successful run of the new data.

    Hashing the input directory tells them apart: it does not move when staging
    rewrites, and it moves the moment the images do. An input directory that
    cannot be resolved or read fingerprints as ``""``, which never matches a
    stored value, so the re-attach declines rather than guesses.
    """
    if input_dir is None:
        return ""
    try:
        archives = sorted(_well_zips_in(Path(input_dir)))
    except OSError:
        return ""
    if not archives:
        return ""
    try:
        return fingerprint_archives(archives)
    except OSError:
        return ""


def _scratch_dir() -> Path:
    """Base directory for AllWell's local scratch — input staging, transit
    output zips, and incremental merge temps.

    Resolution order, matching ``pipeline.io.scratch_base_for`` exactly:

    1. ``$ALLWELL_SCRATCH_DIR`` — the redirectable-scratch env the GUI sets from
       its "Scratch disk" field, so a user whose data lives on a NAS but whose
       OS temp is a small system drive (Windows ``%TEMP%`` on ``C:``) can put
       the potentially-GB-sized staging rewrite on a roomy volume.
    2. ``~/.allwell/scratch`` — the default.
    3. The OS temp dir — last resort, when the home directory is unwritable.

    The two resolvers agree deliberately. They used to differ: the pipeline
    defaulted to ``~/.allwell/scratch`` while this one defaulted to the OS temp
    dir, so a remote run wrote into a path the user had never heard of and could
    not find — ``/var/folders/jj/…/T`` on macOS, and on Linux frequently a tmpfs,
    i.e. RAM. One default, stated in one place, is one thing to reason about when
    a volume fills.

    This module cannot import ``pipeline.io`` to share the constant (that would
    drag numpy and tifffile into the remote client, which
    ``test_architecture_contracts`` forbids), so the path is repeated here and
    ``test_remote_runner`` pins the two to the same value.
    """
    override = os.environ.get("ALLWELL_SCRATCH_DIR", "").strip()
    if override:
        try:
            p = Path(override).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError as exc:
            # Don't silently ignore a user-set scratch dir — falling back
            # elsewhere can later fill a small system drive with GB of staging
            # (audit E14). Surface it so the misconfiguration is visible.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "ALLWELL_SCRATCH_DIR=%r is unusable (%s); falling back to "
                "~/.allwell/scratch", override, exc)
    try:
        home = Path.home() / ".allwell" / "scratch"
        home.mkdir(parents=True, exist_ok=True)
        return home
    except OSError:
        return Path(tempfile.gettempdir())


def _new_scratch_dir(prefix: str) -> Path:
    """``mkdtemp`` rooted at :func:`_scratch_dir` (honours ALLWELL_SCRATCH_DIR)."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(_scratch_dir())))


# Working directory for bytes in transit, created on the volume they are coming
# from or going to. Dot-prefixed so every dataset scan in the app skips it —
# `image_discovery`, `auto_threshold` and `zip_members` all drop a name starting
# with a dot — and removed as soon as the file it held is in place.
TRANSIT_SUBDIR = ".allwell-transit"

# Volumes that have already refused a transit directory this process. Without
# it, a fallback on a read-only output folder would repeat its warning once per
# well for the whole plate.
_transit_refused: set[str] = set()


def _transit_dir(near: Path | str,
                 *, log_fn: Callable[[str], None] | None = None) -> Path:
    """A working directory beside *near*, for a file on its way to or from it.

    A remote run's compute happens on the cluster; locally it only sends inputs
    and receives results. It has no reason to touch the home volume, and it used
    to touch it for every well: each downloaded ``<well>_out.zip`` was written to
    ``~/.allwell/scratch`` and then materialised into the output directory, so
    every byte of a plate's results crossed two volumes, and a full home volume
    stopped a run whose own input and output volumes had room to spare. That is
    the ``ENOSPC`` users hit.

    **Why this does not re-create the problem ``pipeline.io.scratch_base_for``
    warns about.** That docstring is right that putting churn on a
    network-mounted volume is what caused the macOS SMB "Bad file descriptor"
    well failures — but it is describing the *pipeline's* working set: roughly
    six float32 intermediates per FOV-group, each written tmp → fsync → rename,
    from every worker process at once. This is one file per well, written once
    and moved once. And the destination volume already receives exactly these
    bytes today, by copy from the home volume; the number of writes it sees does
    not change. What changes is that the home volume stops seeing them, and the
    move into place becomes a same-volume rename instead of a cross-device copy.

    Falls back to :func:`_scratch_dir` when *near* will not take a directory (a
    read-only or quota'd volume) — and says so, once per volume. A run that
    silently reverted to the volume the user is trying to get off would be the
    original failure with the explanation removed.
    """
    base = Path(near)
    path = base / TRANSIT_SUBDIR
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError as exc:
        key = str(base)
        if key not in _transit_refused:
            _transit_refused.add(key)
            if log_fn is not None:
                log_fn(f"[remote] WARN cannot use {path} for files in transit "
                       f"({exc}); falling back to {_scratch_dir()}. That volume "
                       f"has to hold a well's output on its way through, so a "
                       f"large plate can fill it.\n")
        return _scratch_dir()


def _new_transit_dir(near: Path | str, prefix: str,
                     *, log_fn: Callable[[str], None] | None = None) -> Path:
    """``mkdtemp`` rooted at :func:`_transit_dir`."""
    return Path(tempfile.mkdtemp(
        prefix=prefix, dir=str(_transit_dir(near, log_fn=log_fn))))


def sweep_stale_scratch(*, max_age_h: float = 6.0,
                        also: Iterable[Path | str] = (),
                        log_fn: Callable[[str], None] | None = None) -> int:
    """Best-effort removal of leftover ``allwell-*`` scratch from runs killed
    before their cleanup ran (a hard quit skips the ``finally`` that deletes the
    staging dir, leaking a full input copy onto the scratch volume). Only
    removes entries older than *max_age_h* so a concurrent run's live staging is
    never touched. Returns the number removed.

    *also* names further bases to sweep — the transit directories beside the
    run's own input and output volumes. Without them the leak does not go away
    when transit moves off the scratch volume, it just moves with it.
    """
    cutoff = time.time() - max_age_h * 3600.0
    removed = 0
    seen: set[Path] = set()
    for base in [_scratch_dir(), *[Path(p) / TRANSIT_SUBDIR for p in also]]:
        if base in seen:
            continue
        seen.add(base)
        try:
            entries = list(base.glob("allwell-*"))
        except OSError:
            continue
        here = 0
        for p in entries:
            try:
                if p.stat().st_mtime > cutoff:
                    continue
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink()
                here += 1
            except OSError:
                continue
        if here and log_fn:
            log_fn(f"[remote] cleaned {here} stale scratch item(s) from {base}\n")
        removed += here
    return removed


def _zip_is_complete(path: Path) -> bool:
    """True iff *path* is a fully-transferred, valid zip.

    ``zipfile.is_zipfile`` looks for the end-of-central-directory record, which
    only exists once the whole archive has landed — a truncated / partial
    download fails the check. Lets the Fetcher distinguish "rsync errored but the
    file is actually here" from "the file is incomplete"."""
    try:
        return path.exists() and zipfile.is_zipfile(path)
    except OSError:
        return False
