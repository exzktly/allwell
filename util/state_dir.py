"""The one per-user folder All-Well keeps its local files in.

**The app's folder — :func:`gui_state_dir`.** Everything All-Well keeps on a
machine outside a dataset folder, in one fixed layout:

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
``czi-scans/``             the pipeline's CZI header cache (``pipeline.scan_cache``)
``settings.ini``           window geometry, recent datasets and the other Qt
                           settings (``core.app_settings``)
``remotes.toml``           the remote hosts (``services.remote_config``)
``job_queue.json``         the Datasets job queue (``services.job_queue``)
``run_index.json``         the run journal's index (``services.run_journal``)
``suspended_runs.json``    the suspended Analyze runs (``services.suspend_record``)
=========================  ======================================================

``%LOCALAPPDATA%\\allwell`` on Windows — the folder Windows provides for exactly
this, per-user and never roamed — and ``~/.allwell`` on macOS and Linux. Each
falls back to the system temp folder when it cannot be
written (Windows tries ``~/.allwell`` first). ``ALLWELL_STATE_DIR``, when set,
replaces all of that — the test harness points it at a per-worker scratch
folder so no test writes into the real one. The app's caches are cleared from
the title bar's storage button (``core.local_storage``), which offers the
scratch only behind a warning; the scan cache and the four records are never
offered there.

**A remote host keeps its own folders.** The scratch and the scan cache follow
this layout only in the desktop app and the processes it starts on this machine
(:func:`on_the_desktop`, the ``ALLWELL_DESKTOP`` mark ``allwell.py`` sets). The
pipeline on a remote host runs the same code without the mark, and resolves
them exactly as before the app's folder existed — ``~/.allwell/scratch`` (or
the host's *Remote scratch dir*) and ``~/.cache/allwell/czi-scans`` — so
nothing on a remote host moves. The records and settings are the desktop's
alone and never resolved there.

**One folder on every platform.** Earlier versions spread these over four
trees: the run scratch in ``~/.allwell`` even on Windows, the scan cache in
``~/.cache/allwell``, the remotes, job queue and run index in
``~/.config/allwell`` (``%APPDATA%\\allwell`` on Windows), and the Qt settings
in the platform's native store (a plist on macOS, the registry on Windows,
``~/.config/AllWell`` on Linux). The user's own data among them is carried
over on first use by :func:`adopt_legacy_file` (and by ``core.app_settings``
for the Qt settings); the caches are not carried — they rebuild — and the
storage button offers their old copies instead (:func:`legacy_gui_paths`).

The Parquet row caches are not here, by design: each sits in a ``.aw_parquet``
folder beside the CSVs it caches, so it travels and is deleted with its dataset.

**Scratch on Windows.** The scratch holds gigabytes of transient files, and
``%LOCALAPPDATA%`` is the part of a profile that is never roamed, so the move
out of ``%USERPROFILE%\\.allwell`` is also the right volume class. The storage
button offers the scratch only unticked, behind a warning the user must accept,
and never while an Analyze run is going or for anything written to in the last
hour (a suspended run resumes from its checkpoints; ``core.local_storage``). It
also offers the old ``~/.allwell/scratch`` on Windows, which nothing reads any
more (:func:`legacy_scratch_dir`).

Stdlib-only and Qt-free: ``startup.boot_diag`` imports it before PySide6, and
``util/`` is shipped to the remote worker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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
#: Where the scratch goes when nothing above it in ``pipeline.io.scratch_base_for`` names a folder, in place of the
#: app folder's scratch. Set from ``--scratch_fallback_dir``, which the remote runner passes as the run's output
#: folder; an environment variable so the spawned workers inherit it.
SCRATCH_FALLBACK_ENV = "ALLWELL_SCRATCH_FALLBACK_DIR"
#: The pipeline's CZI header cache (``pipeline.scan_cache``), its override
#: variable, and the override values that switch it off.
SCAN_CACHE_SUBDIR = "czi-scans"
SCAN_CACHE_ENV = "ALLWELL_SCAN_CACHE"
SCAN_CACHE_OFF = frozenset({"off", "0", "false", "no"})
#: The user's records, each carried over from where an earlier version kept it
#: by :func:`adopt_legacy_file`.
SETTINGS_NAME = "settings.ini"
REMOTES_NAME = "remotes.toml"
JOB_QUEUE_NAME = "job_queue.json"
RUN_INDEX_NAME = "run_index.json"
SUSPENDED_RUNS_NAME = "suspended_runs.json"
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

#: Set to ``"1"`` by the desktop app (``allwell.py``) at import, and inherited
#: by every process it starts on this machine. See :func:`on_the_desktop`.
DESKTOP_ENV = "ALLWELL_DESKTOP"


def on_the_desktop() -> bool:
    """Whether this process is the desktop app or one it started on this machine.

    Only then do the pipeline's own folders — the run scratch and the CZI scan
    cache — follow the app's folder. Everywhere else, and above all when the
    pipeline runs on a **remote host**, they keep the rules they had before the
    app's folder existed (``~/.allwell/scratch``; ``~/.cache/allwell`` first for
    the scan cache), so nothing on a remote host moves. The variable does not
    travel to a remote host: ssh forwards no environment unless configured to,
    and the remote command names none.
    """
    return os.environ.get(DESKTOP_ENV, "").strip() == "1"


def candidate_dirs() -> list[Path]:
    """The folders the CZI scan cache tries off the desktop, in order —
    ``~/.cache/allwell`` first (``pipeline.scan_cache``)."""
    out = [Path(os.path.expanduser("~/.cache")) / APP_DIRNAME]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / APP_DIRNAME)
    out.append(Path(tempfile.gettempdir()) / APP_DIRNAME)
    return out


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

    Off the desktop (:func:`on_the_desktop` — a remote host, or the pipeline run
    by hand), ``~/.allwell/scratch`` exactly as before the app's folder existed:
    that folder, created, or ``None`` when it cannot be.

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
    if not on_the_desktop():
        try:
            path = Path.home() / HOME_DIRNAME / SCRATCH_SUBDIR
            path.mkdir(parents=True, exist_ok=True)
            return path
        except (OSError, RuntimeError):
            # RuntimeError: Path.home() cannot resolve a home folder at all.
            return None
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
    if not on_the_desktop():
        return "~/" + (Path(HOME_DIRNAME) / SCRATCH_SUBDIR).as_posix()
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


def app_file(name: str) -> Path:
    """*name* in the app's folder — where every record in the layout table lives."""
    return gui_state_dir() / name


def legacy_config_dirs() -> list[Path]:
    """The folders earlier versions kept the remotes, job queue and run index in.

    ``%APPDATA%\\allwell`` (Windows only) and ``~/.config/allwell`` (every
    platform; Windows read it too when it already held a ``remotes.toml``).
    """
    out: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if is_windows() and appdata:
        out.append(Path(appdata) / APP_DIRNAME)
    out.append(Path(os.path.expanduser("~/.config")) / APP_DIRNAME)
    return [d for d in out if d.is_absolute()]


def adopt_legacy_file(target: Path, legacy: list[Path], *,
                      log=None) -> Path:
    """Return *target*, first moving a record an earlier version kept elsewhere.

    When *target* does not exist, the first file of *legacy* that does is moved
    to it — copied, then the original removed — so the user's remotes, queue or
    suspended runs follow the app into its folder instead of silently starting
    empty. A copy that fails leaves the original where it was and *target*
    absent, so nothing is lost and the next call tries again; an original that
    will not go after a good copy is left, and is never read again because
    *target* now exists. Each outcome is reported through *log* (a
    ``logging.Logger``), when given.

    Never moves anything while ``ALLWELL_STATE_DIR`` is set: that variable
    means "not the real folders" (the test harness sets it), and a move would
    take the user's real records out of their real home folder.
    """
    if os.environ.get(STATE_DIR_ENV, "").strip():
        return target
    try:
        if target.exists():
            return target
    except OSError:
        return target
    for old in legacy:
        try:
            if not old.is_file() or _overlaps(old, target):
                continue
        except OSError:
            continue
        import shutil
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".adopting")
            shutil.copy2(old, tmp)
            os.replace(tmp, target)
        except OSError as exc:
            if log is not None:
                log.warning("Could not move %s to %s (%s); it stays where it is "
                            "and is read from there until the move succeeds.",
                            old, target, exc)
            return old
        try:
            old.unlink()
            _remove_if_empty(old.parent)
        except OSError as exc:
            if log is not None:
                log.warning("Moved %s to %s but could not remove the original "
                            "(%s); it is no longer read.", old, target, exc)
        else:
            if log is not None:
                log.info("Moved %s to %s.", old, target)
        return target
    return target


def _remove_if_empty(folder: Path) -> None:
    """Remove *folder* when the move just emptied it; leave it otherwise."""
    try:
        folder.rmdir()
    except OSError:
        pass  # not empty, or not ours to remove: either way it stays


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
    logs, the font cache and the CZI scan cache (``czi-scans``, unless
    ``ALLWELL_SCAN_CACHE`` points runs at it). The ``.mplcache`` folder at the root of a source checkout
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
            for pattern in (BOOT_LOG_NAME + "*", BOOTSTRAP_LOG_GLOB, FONT_CACHE_SUBDIR,
                            SCAN_CACHE_SUBDIR):
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
    scan_override = os.environ.get(SCAN_CACHE_ENV, "").strip()
    in_use = [current]
    if scan_override and scan_override.lower() not in SCAN_CACHE_OFF:
        in_use.append(Path(scan_override).expanduser())
    return [p for p in found if not any(_overlaps(p, u) for u in in_use)]


def _holds(path: Path, other: Path) -> bool:
    """Whether *path* is *other* or contains it."""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return resolved == other or other.is_relative_to(resolved)
