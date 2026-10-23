"""The per-user folders All-Well keeps its local files in.

Two folders, for two different owners.

**The app's folder — :func:`gui_state_dir`.** Everything the desktop app itself
writes on this machine outside a dataset folder, in one fixed layout:

=========================  ======================================================
``allwell-boot.log``       the startup and crash log (``startup.boot_diag``),
                           with the last three crashes as ``.1`` / ``.2`` / ``.3``
``bootstrap-*.log``        the remote-bootstrap logs (``services.remote_bootstrap``)
``mplcache/``              matplotlib's font cache (``startup.mpl_bootstrap``)
``tmp/``                   spill and staging folders the app makes and removes
                           itself (``review.data.cell_pool``, Batch Export)
``scratch/``               an Analyze run's working files — extracted inputs,
                           intermediate images, per-FOV checkpoints — unless the
                           user points the run elsewhere (:func:`default_scratch_dir`)
=========================  ======================================================

``%LOCALAPPDATA%\\allwell`` on Windows — the folder Windows provides for exactly
this, per-user and never roamed — and ``~/.allwell`` on macOS and Linux. Each
falls back to the system temp folder when it cannot be written (Windows tries
``~/.allwell`` first). ``ALLWELL_STATE_DIR``, when set, replaces all of that —
the test harness points it at a per-worker scratch folder so no test writes
into the real one. The app's caches are cleared from the title bar's storage
button (``core.local_storage``).

:func:`legacy_gui_paths` names the app's files in the other places they can be
found — ``~/.cache/allwell``, the ``.mplcache`` folder of a source checkout —
so the storage button can clear them.

The Parquet row caches are not here, by design: each sits in a ``.aw_parquet``
folder beside the CSVs it caches, so it travels and is deleted with its dataset.

**The analysis pipeline's folder — :func:`user_state_dir`.** The CZI scan cache
(``pipeline.scan_cache``) is the pipeline's, runs on remote hosts as well, and
keeps its own rule: ``~/.cache/allwell`` first, then ``%LOCALAPPDATA%\\allwell``,
then the temp folder. It is not a GUI cache and does not move with the app's.

**Scratch sits in the app's folder on every platform.** The run scratch is the
pipeline's, but its default location is ``scratch/`` inside the app's folder —
``%LOCALAPPDATA%\\allwell\\scratch`` on Windows, ``~/.allwell/scratch`` on
macOS and Linux — so that everything the app keeps on a machine is in one place
whichever platform it is. On Windows that is also the right volume class: the
scratch holds gigabytes of transient files, and ``%LOCALAPPDATA%`` is the part
of a profile that is never roamed. The storage button still never clears it
(a suspended run resumes from its checkpoints); it only offers the old
``~/.allwell/scratch`` on Windows, which nothing reads any more
(:func:`legacy_scratch_dir`).

Stdlib-only and Qt-free: ``startup.boot_diag`` imports it before PySide6, and
``util/`` is shipped to the remote worker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def candidate_dirs() -> list[Path]:
    """The folders :func:`user_state_dir` tries, in order."""
    out = [Path(os.path.expanduser("~/.cache")) / "allwell"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "allwell")
    out.append(Path(tempfile.gettempdir()) / "allwell")
    return out


def user_state_dir() -> Path:
    """The first folder of :func:`candidate_dirs` that exists or can be made,
    *and* can be written; the system temp directory when none can.

    ``mkdir(exist_ok=True)`` succeeds on a folder that exists but cannot be
    written (one left owned by another user, say), so writability is checked
    separately — otherwise the log's ``open`` fails later, silently, and the
    fallbacks are never reached.
    """
    for d in candidate_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(d, os.W_OK):
            return d
    return Path(tempfile.gettempdir())


# ── the desktop app's own folder ────────────────────────────────────────────

#: The folder name under ``%LOCALAPPDATA%`` (Windows).
APP_DIRNAME = "allwell"
#: The folder name under the home folder (macOS, Linux).
HOME_DIRNAME = ".allwell"
#: Subfolders and files of :func:`gui_state_dir`, named once so every writer
#: and the cache-clearing code agree.
FONT_CACHE_SUBDIR = "mplcache"
TEMP_SUBDIR = "tmp"
#: The run scratch's default folder. Deliberately a dedicated subfolder rather
#: than the app's folder itself: the pipeline's stale sweeps glob
#: ``_tmp_extract_*`` / ``_tmp_images_*`` under the scratch base and delete what
#: they find.
SCRATCH_SUBDIR = "scratch"
#: The environment variable that overrides the scratch default — the Analyze
#: form's "Scratch disk" field and ``--scratch_dir`` both set it.
SCRATCH_DIR_ENV = "ALLWELL_SCRATCH_DIR"
BOOT_LOG_NAME = "allwell-boot.log"
BOOTSTRAP_LOG_GLOB = "bootstrap-*.log"
#: Where a source checkout kept its font cache before it moved into the app's
#: folder (git-ignored; absent from a frozen build).
REPO_FONT_CACHE = Path(__file__).resolve().parent.parent / ".mplcache"


def is_windows() -> bool:
    """Whether this is Windows. A function so tests can stand in for it."""
    return os.name == "nt"


#: Overrides :func:`gui_state_dir` outright when set.
STATE_DIR_ENV = "ALLWELL_STATE_DIR"


def gui_candidate_dirs() -> list[Path]:
    """The folders :func:`gui_state_dir` tries, in order."""
    out: list[Path] = []
    override = os.environ.get(STATE_DIR_ENV, "").strip()
    if override:
        out.append(Path(override).expanduser())
    local = os.environ.get("LOCALAPPDATA")
    if is_windows() and local:
        out.append(Path(local) / APP_DIRNAME)
    out.append(Path(os.path.expanduser("~")) / HOME_DIRNAME)
    out.append(Path(tempfile.gettempdir()) / APP_DIRNAME)
    return out


def gui_state_dir() -> Path:
    """The desktop app's one local folder: the first of :func:`gui_candidate_dirs`
    that exists or can be made *and* can be written; the system temp folder when
    none can. The writability check is :func:`user_state_dir`'s, for its reason.
    """
    for d in gui_candidate_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(d, os.W_OK):
            return d
    return Path(tempfile.gettempdir())


def gui_temp_dir() -> Path:
    """``tmp`` under :func:`gui_state_dir`, created; the system temp folder when it cannot be."""
    path = gui_state_dir() / TEMP_SUBDIR
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return Path(tempfile.gettempdir())


def default_scratch_dir() -> Path | None:
    """``scratch`` in the app's folder, created; ``None`` when it cannot be made.

    The candidates are :func:`gui_candidate_dirs`, in the same order, so the
    scratch follows the app's folder wherever that resolves —
    ``ALLWELL_STATE_DIR`` included — but **not** to the system temp folder. A
    run's scratch carries gigabytes, the temp folder on Linux is often a tmpfs
    (memory), and each caller has a better last resort of its own: the pipeline
    falls back to the output folder, the remote client to the temp folder with
    a warning (``pipeline.io.scratch_base_for``,
    ``services.remote_scratch._scratch_dir``).

    A home folder that cannot be resolved (``~`` left unexpanded) is skipped
    rather than created relative to the working directory.
    """
    temp_root = Path(tempfile.gettempdir()) / APP_DIRNAME
    for d in gui_candidate_dirs():
        if d == temp_root or not d.is_absolute():
            continue
        path = d / SCRATCH_SUBDIR
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(path, os.W_OK):
            return path
    return None


def default_scratch_label() -> str:
    """Where :func:`default_scratch_dir` puts the scratch, as a person reads it.

    Creates nothing, so a form placeholder or a help line can name the folder
    without making it. ``~/.allwell/scratch`` on macOS and Linux (the home folder
    written as ``~``), ``%LOCALAPPDATA%\\allwell\\scratch`` spelled out on
    Windows. It names the first candidate — the folder used unless that one
    cannot be written.
    """
    path = gui_candidate_dirs()[0] / SCRATCH_SUBDIR
    if not is_windows():
        try:
            return "~/" + path.relative_to(Path(os.path.expanduser("~"))).as_posix()
        except ValueError:
            pass  # not under the home folder (ALLWELL_STATE_DIR): the full path
    return str(path)


def legacy_scratch_dir() -> Path | None:
    """``~/.allwell/scratch`` when it is not where scratch goes now, else ``None``.

    That is where the run scratch lived on every platform before it moved into
    the app's folder, so on Windows an older version's scratch — extracted
    inputs and checkpoints, possibly gigabytes — is left there with nothing
    reading it. ``None`` on macOS and Linux, where the two are the same folder,
    and whenever the folder is (or holds, or sits inside) the scratch the user
    has pointed runs at with ``ALLWELL_SCRATCH_DIR``.
    """
    old = Path(os.path.expanduser("~")) / HOME_DIRNAME / SCRATCH_SUBDIR
    if not old.is_absolute():
        return None
    try:
        if not old.is_dir() or old.is_symlink():
            return None
    except OSError:
        return None
    current = default_scratch_dir()
    if current is not None and _overlaps(old, current):
        return None
    override = os.environ.get(SCRATCH_DIR_ENV, "").strip()
    if override and _overlaps(old, Path(override).expanduser()):
        return None
    return old


def _overlaps(a: Path, b: Path) -> bool:
    """Whether either path is, or contains, the other."""
    try:
        ra, rb = a.resolve(), b.resolve()
    except OSError:
        return True
    return _holds(ra, rb) or _holds(rb, ra)


def legacy_gui_paths() -> list[Path]:
    """Files and folders earlier versions of the app wrote and this one does not.

    In ``~/.cache/allwell``: the boot log and its crash copies, the bootstrap
    logs and the font cache — **not** ``czi-scans``, which is the pipeline's and
    lives there. The ``.mplcache`` folder at the root of a source checkout
    (git-ignored; a frozen build has none). And on Windows, the run scratch an
    older version left in ``~/.allwell/scratch`` (:func:`legacy_scratch_dir`).
    Only paths that exist are returned, and none that is, or holds, the current
    :func:`gui_state_dir`.
    """
    current = gui_state_dir().resolve()
    found: list[Path] = []
    old = Path(os.path.expanduser("~/.cache")) / APP_DIRNAME
    try:
        if old.is_dir() and old.resolve() != current:
            for pattern in (BOOT_LOG_NAME + "*", BOOTSTRAP_LOG_GLOB, FONT_CACHE_SUBDIR):
                found.extend(sorted(old.glob(pattern)))
    except OSError:
        pass  # an unreadable folder offers nothing; nothing is lost by it
    repo_cache = REPO_FONT_CACHE
    try:
        if repo_cache.is_dir() and not repo_cache.is_symlink():
            found.append(repo_cache)
    except OSError:
        pass  # likewise
    old_scratch = legacy_scratch_dir()
    if old_scratch is not None:
        found.append(old_scratch)
    return [p for p in found if not _holds(p, current)]


def _holds(path: Path, other: Path) -> bool:
    """Whether *path* is *other* or contains it."""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return resolved == other or other.is_relative_to(resolved)
