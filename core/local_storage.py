"""What the desktop app keeps on this machine, how big it is, and clearing it.

The title bar's storage button (``core/local_storage_view.py``) is built on this
module. It knows the app's own working files only — the folder
:func:`util.state_dir.gui_state_dir` names, the app's files in the other places
:func:`util.state_dir.legacy_gui_paths` and :data:`SYSTEM_TEMP_PREFIXES` cover,
and the row cache of the dataset that is open — and never the analysis
pipeline's: the run scratch and the CZI scan cache belong to runs, including
ones that are suspended and will resume from what is there, and are not
offered here.

Each :class:`CacheArea` is one checkbox in the dialog. Clearing is
conservative in the ways that matter:

* **Nothing in use is removed.** A folder under ``tmp`` that names a running
  process in its ``owner.pid`` (``core.process``; an owner on another host
  sharing this home folder always counts as running) — a cell pool's spill, a
  Batch Export in progress — is skipped, and so is one with no owner file that
  is younger than :data:`UNOWNED_GRACE_S`. The live boot log is never offered,
  nor a bootstrap log a remote setup may still be writing (:func:`_old_logs`).
* **The list is taken again when Clear is pressed.** :func:`clear_now` re-lists
  the chosen areas at that moment and removes only what is still offered, so a
  pool started while the dialog stood open is not cleared from under itself.
* **Links are removed, never followed.** A symbolic link, and on Windows a
  directory junction or any other reparse point, is unlinked where it stands;
  nothing it points at is read, measured or deleted (:func:`is_link`).
* **A failure is reported, not hidden.** A file Windows will not delete because
  something holds it open, or a folder that will not go, is left, named in the
  result, and the freed total counts only what actually went.

Qt-free: the dialog lists, measures and clears through ``core.worker_bridge`` so
a large cache never blocks the window, and this module can be driven from a test
without one.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from core.process import owner_alive
from util.state_dir import (
    BOOT_LOG_NAME,
    BOOTSTRAP_LOG_GLOB,
    FONT_CACHE_SUBDIR,
    TEMP_SUBDIR,
    gui_state_dir,
    legacy_gui_paths,
)

#: The folder each dataset's Parquet row cache sits in, beside its CSVs. The
#: same name as ``review.data.parquet_cache.CACHE_DIRNAME``, which ``core/`` may
#: not import; ``tests/test_local_storage.py`` pins the two together.
ROW_CACHE_DIRNAME = ".aw_parquet"

#: Prefixes of the app's working folders that can be found in the system temp
#: folder: cell-pool spills and Batch Export staging (which now go under
#: :data:`util.state_dir.TEMP_SUBDIR`), and the placeholder font folder
#: ``startup.mpl_bootstrap`` makes for one launch and a crash can leave behind.
SYSTEM_TEMP_PREFIXES = ("allwell-cellpool-", "allwell_batch_nodisk_", "allwell-mplfonts-")

#: A working folder with no owner file is treated as in use until it is this old
#: (seconds) — long enough to cover one being created and marked.
UNOWNED_GRACE_S = 3600.0

#: A bootstrap log written to this recently (seconds) may belong to a remote
#: setup that is still running — a conda and pip install can take that long —
#: so it is not offered. The newest bootstrap log is never offered either.
LIVE_LOG_GRACE_S = 3 * 3600.0

#: Windows ``FILE_ATTRIBUTE_REPARSE_POINT``: set on symbolic links, directory
#: junctions and mount points alike.
_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class CacheArea:
    """One clearable group of files, as the dialog shows it."""

    key: str
    label: str
    detail: str
    #: Files and folders this area clears, already filtered to what may go.
    paths: tuple[Path, ...]
    #: Whether the dialog ticks it to begin with.
    default_on: bool = True


@dataclass
class ClearResult:
    """What a clear did: bytes freed and paths it could not remove."""

    freed: int = 0
    failed: list[str] = field(default_factory=list)


# ── links ───────────────────────────────────────────────────────────────────

def is_link(path) -> bool:
    """Whether *path* is a link of any kind, so must be removed and not entered.

    ``os.path.islink`` misses a Windows directory junction, and ``os.walk`` on
    the Python the app ships with (3.10) descends into one — so a junction inside
    a cleared folder would have had its *target* emptied. A reparse point of any
    kind counts: a junction, a mount point, a symbolic link.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


# ── what is offered ─────────────────────────────────────────────────────────

def _in_use(folder: Path, *, now: float) -> bool:
    alive = owner_alive(folder)
    if alive is not None:
        return alive
    try:
        return now - folder.stat().st_mtime < UNOWNED_GRACE_S
    except OSError:
        return True


def _working_folders(root: Path, prefixes: Iterable[str] | None, *, now: float) -> list[Path]:
    """Entries of *root* (optionally only those starting with a prefix) not in use."""
    out: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    wanted = None if prefixes is None else tuple(prefixes)
    for entry in entries:
        if wanted is not None and not entry.name.startswith(wanted):
            continue
        if is_link(entry):
            continue
        if entry.is_dir() and _in_use(entry, now=now):
            continue
        out.append(entry)
    return out


def _old_logs(base: Path, *, now: float) -> list[Path]:
    """The logs in *base* that no one is writing.

    Never the boot log this launch is writing (only its numbered crash copies),
    never the newest bootstrap log, and never one written to within
    :data:`LIVE_LOG_GRACE_S` — a remote setup in progress holds its log open, and
    on macOS and Linux deleting it would succeed and lose the record of a setup
    that may be about to fail.
    """
    found = [p for p in sorted(base.glob(BOOT_LOG_NAME + ".*")) if p.is_file() and not is_link(p)]
    boot = []
    for p in base.glob(BOOTSTRAP_LOG_GLOB):
        try:
            if p.is_file() and not is_link(p):
                boot.append((p.stat().st_mtime, p))
        except OSError:
            continue
    boot.sort()
    for mtime, p in boot[:-1]:
        if now - mtime >= LIVE_LOG_GRACE_S:
            found.append(p)
    return found


def _row_cache_dirs(dataset_dir: Path) -> list[Path]:
    """The ``.aw_parquet`` folders of *dataset_dir*: its own, and its ``out/``'s."""
    found = []
    for folder in (dataset_dir, dataset_dir / "out"):
        candidate = folder / ROW_CACHE_DIRNAME
        if candidate.is_dir() and not is_link(candidate):
            found.append(candidate)
    return found


def cache_areas(dataset_dir: Path | None = None, *,
                now: Callable[[], float] = time.time) -> list[CacheArea]:
    """The areas the storage dialog offers, in the order it lists them.

    *dataset_dir* is the open dataset's folder, when one is open; its row cache
    is offered, unticked, because clearing it makes the next open of that
    dataset slower. Areas with nothing in them are still returned — the dialog
    shows them at zero rather than hiding them, so the user can see that
    something they expected to be large is not.

    Reads folders (the system temp folder among them), so the dialog calls it
    off the GUI thread.
    """
    base = gui_state_dir()
    t = now()
    fonts = base / FONT_CACHE_SUBDIR
    areas = [
        CacheArea(
            "fonts", "Font cache",
            "Matplotlib's list of the app's fonts. Rebuilt the next time the "
            "app starts.",
            tuple(p for p in (fonts,) if p.exists() and not is_link(p))),
        CacheArea(
            "temp", "Temporary working files",
            "Folders the app makes while it works — a cell pool's spill, a "
            "Batch Export's staging — left behind by a crash or a forced quit. "
            "Any still in use is skipped.",
            tuple(_working_folders(base / TEMP_SUBDIR, None, now=t))),
        CacheArea(
            "logs", "Old logs",
            "Logs of earlier launches that crashed, and of finished remote "
            "setups. This launch's log, and a remote setup's that may still be "
            "running, are kept.",
            tuple(_old_logs(base, now=t))),
        CacheArea(
            "legacy", "App files in other places",
            "Logs and caches in ~/.cache/allwell, a source checkout's "
            ".mplcache, and working folders in the system temp folder. Nothing "
            "reads them any more.",
            tuple(p for p in legacy_gui_paths() if not is_link(p))
            + tuple(_working_folders(Path(tempfile.gettempdir()),
                                     SYSTEM_TEMP_PREFIXES, now=t))),
    ]
    if dataset_dir is not None:
        areas.append(CacheArea(
            "rows", f"Row cache of the open dataset ({Path(dataset_dir).name})",
            "Fast copies of this dataset's CSVs, in .aw_parquet folders beside "
            "them. Rebuilt from the CSVs as wells are read, so the next open "
            "is slower.",
            tuple(_row_cache_dirs(Path(dataset_dir))), default_on=False))
    return areas


# ── measuring and clearing ──────────────────────────────────────────────────

def size_of(path: Path) -> int:
    """Bytes under *path* (a file or a folder); links and unreadable parts count as nothing."""
    if is_link(path):
        return 0
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        # Prune links (junctions included) so the walk never enters one.
        dirs[:] = [d for d in dirs if not is_link(os.path.join(root, d))]
        for name in files:
            full = os.path.join(root, name)
            if is_link(full):
                continue  # a link's own few bytes; what it points at is not ours
            try:
                total += os.lstat(full).st_size
            except OSError:
                continue
    return total


def measure(areas: Iterable[CacheArea]) -> dict[str, int]:
    """``{area.key: bytes}`` for *areas*."""
    return {area.key: sum(size_of(p) for p in area.paths) for area in areas}


def survey(dataset_dir: Path | None = None) -> tuple[list[CacheArea], dict[str, int]]:
    """:func:`cache_areas` and :func:`measure` together — the dialog's one background read."""
    areas = cache_areas(dataset_dir)
    return areas, measure(areas)


def clear(areas: Iterable[CacheArea]) -> ClearResult:
    """Remove every path of *areas*; report what was freed and what was not."""
    result = ClearResult()
    for area in areas:
        for path in area.paths:
            _remove(path, result)
    return result


def clear_now(keys: Iterable[str], dataset_dir: Path | None = None) -> ClearResult:
    """Re-list the areas named by *keys* and clear what is offered *now*.

    The dialog's Clear runs this rather than clearing the list it showed, which
    may be minutes old: a pool opened since then has a spill that was not in use
    when it was listed, and must not be removed.
    """
    wanted = set(keys)
    return clear([a for a in cache_areas(dataset_dir) if a.key in wanted])


def _remove(path: Path, result: ClearResult) -> None:
    """Remove *path*, adding what went to ``freed`` and what did not to ``failed``.

    ``shutil.rmtree``, which never enters a link or a junction, with an error
    handler that clears a read-only flag and retries once (Windows refuses to
    delete a read-only file) and otherwise records the path and carries on, so
    one held-open file costs only itself. The freed total is the size before
    less the size of what is still there.
    """
    before = size_of(path)
    if is_link(path):
        # The link itself, never what it points at. A Windows directory
        # junction is removed with ``rmdir``, which removes the reparse point
        # and leaves the target alone.
        for remove in (os.unlink, os.rmdir):
            try:
                remove(path)
                return
            except FileNotFoundError:
                return
            except OSError:
                continue
        result.failed.append(str(path))
        return
    if not path.is_dir():
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except PermissionError:
            if not _retry_writable(os.unlink, str(path)):
                result.failed.append(str(path))
                return
        except OSError:
            result.failed.append(str(path))
            return
        result.freed += before
        return

    def _on_error(func, failed_path, _exc) -> None:
        if _retry_writable(func, failed_path):
            return
        # A folder that would not go because a file inside it would not is
        # reported through that file alone — one held file is one item.
        prefix = os.path.join(str(failed_path), "")
        if any(f.startswith(prefix) for f in result.failed):
            return
        result.failed.append(str(failed_path))

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_error)
    else:
        shutil.rmtree(path, onerror=_on_error)
    result.freed += max(0, before - (size_of(path) if path.exists() else 0))


def _retry_writable(func, target: str) -> bool:
    """Clear a read-only flag on *target* and call *func* on it once more."""
    try:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)
        return True
    except OSError:
        return False


def format_bytes(n: int) -> str:
    """``"0 B"``, ``"812 KB"``, ``"1.4 GB"`` — decimal units, as file managers show them.

    Three significant figures at most, and the unit chosen after rounding, so a
    value just under a boundary reads ``"1.0 MB"`` rather than ``"1000 KB"``.
    """
    if n < 1000:
        return f"{n} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1000.0
        text = f"{value:.1f}" if value < 99.95 else f"{value:.0f}"
        if float(text) < 1000 or unit == "TB":
            return f"{text} {unit}"
    return f"{value:.0f} TB"
