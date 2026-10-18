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
=========================  ======================================================

``%LOCALAPPDATA%\\allwell`` on Windows — the folder Windows provides for exactly
this, per-user and never roamed — and ``~/.allwell`` on macOS and Linux. Each
falls back to the system temp folder when it cannot be written (Windows tries
``~/.allwell`` first). The app's caches are cleared from the title bar's
storage button (``services.local_storage``).

These used to sit in ``~/.cache/allwell`` on every platform, with the font cache
inside the repository for a run from source and the spill folders in the system
temp folder. :func:`legacy_gui_paths` names what earlier versions left in those
places, so the storage button can clear it.

The Parquet row caches are not here, by design: each sits in a ``.aw_parquet``
folder beside the CSVs it caches, so it travels and is deleted with its dataset.

**The analysis pipeline's folder — :func:`user_state_dir`.** The CZI scan cache
(``pipeline.scan_cache``) is the pipeline's, runs on remote hosts as well, and
keeps its own rule: ``~/.cache/allwell`` first, then ``%LOCALAPPDATA%\\allwell``,
then the temp folder. So does the pipeline's scratch (``pipeline.io``,
``services.remote_scratch``), which is ``~/.allwell/scratch`` unless the user
points it elsewhere. Neither is a GUI cache, and neither moves with the app's.

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
BOOT_LOG_NAME = "allwell-boot.log"
BOOTSTRAP_LOG_GLOB = "bootstrap-*.log"


def is_windows() -> bool:
    """Whether this is Windows. A function so tests can stand in for it."""
    return os.name == "nt"


def gui_candidate_dirs() -> list[Path]:
    """The folders :func:`gui_state_dir` tries, in order."""
    out: list[Path] = []
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


def legacy_gui_paths() -> list[Path]:
    """Files and folders earlier versions of the app wrote and this one does not.

    In ``~/.cache/allwell``: the boot log and its crash copies, the bootstrap
    logs and the font cache — **not** ``czi-scans``, which is the pipeline's and
    still lives there. On Windows, the same three in ``%LOCALAPPDATA%\\allwell``
    are current, not legacy, so nothing there is named. Only paths that exist
    are returned, and none inside the current :func:`gui_state_dir`.
    """
    current = gui_state_dir().resolve()
    old = Path(os.path.expanduser("~/.cache")) / APP_DIRNAME
    found: list[Path] = []
    try:
        if not old.is_dir() or old.resolve() == current:
            return found
        for pattern in (BOOT_LOG_NAME + "*", BOOTSTRAP_LOG_GLOB, FONT_CACHE_SUBDIR):
            found.extend(sorted(old.glob(pattern)))
    except OSError:
        return found
    return found
