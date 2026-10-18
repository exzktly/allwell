"""What the desktop app keeps on this machine, how big it is, and clearing it.

The title bar's storage button (``core/local_storage_view.py``) is built on this
module. It knows the app's own working files only — the folder
:func:`util.state_dir.gui_state_dir` names, what earlier versions left behind,
and the row cache of the dataset that is open — and never the analysis
pipeline's: the run scratch and the CZI scan cache belong to runs, including
ones that are suspended and will resume from what is there, and are not
offered here.

Each :class:`CacheArea` is one checkbox in the dialog. Clearing is
conservative in the ways that matter:

* **Nothing in use is removed.** A folder under ``tmp`` that names a running
  process in its ``owner.pid`` (``core.process``) — a cell pool's spill, a
  Batch Export in progress — is skipped, and so is one with no owner file that
  is younger than :data:`UNOWNED_GRACE_S`. The live boot log is never offered.
* **A failure is reported, not hidden.** A file Windows will not delete because
  something holds it open is left, counted, and named in the result, and the
  freed total counts only what actually went.

Qt-free: the dialog measures and clears through ``core.worker_bridge`` so a
large cache never blocks the window, and this module can be driven from a test
without one.
"""

from __future__ import annotations

import os
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

#: Prefixes of the working folders earlier versions made in the system temp
#: folder, before they moved under :data:`util.state_dir.TEMP_SUBDIR`.
LEGACY_TEMP_PREFIXES = ("allwell-cellpool-", "allwell_batch_nodisk_")

#: A working folder with no owner file is treated as in use until it is this old
#: (seconds) — long enough to cover one being created and marked.
UNOWNED_GRACE_S = 3600.0


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
    for entry in entries:
        if prefixes is not None and not entry.name.startswith(tuple(prefixes)):
            continue
        if entry.is_symlink():
            continue
        if entry.is_dir() and _in_use(entry, now=now):
            continue
        out.append(entry)
    return out


def _old_logs(base: Path) -> list[Path]:
    """Every log in *base* except the one this launch is writing."""
    found = [p for p in sorted(base.glob(BOOT_LOG_NAME + ".*")) if p.is_file()]
    found += [p for p in sorted(base.glob(BOOTSTRAP_LOG_GLOB)) if p.is_file()]
    return found


def cache_areas(dataset_dir: Path | None = None, *,
                now: Callable[[], float] = time.time) -> list[CacheArea]:
    """The areas the storage dialog offers, in the order it lists them.

    *dataset_dir* is the open dataset's folder, when one is open; its row cache
    is offered, unticked, because clearing it makes the next open of that
    dataset slower. Areas with nothing in them are still returned — the dialog
    shows them at zero rather than hiding them, so the user can see that
    something they expected to be large is not.
    """
    base = gui_state_dir()
    t = now()
    areas = [
        CacheArea(
            "fonts", "Font cache",
            "Matplotlib's list of the app's fonts. Rebuilt the next time the "
            "app starts.",
            tuple(p for p in (base / FONT_CACHE_SUBDIR,) if p.exists())),
        CacheArea(
            "temp", "Temporary working files",
            "Folders the app makes while it works — a cell pool's spill, a "
            "Batch Export's staging — left behind by a crash or a forced quit. "
            "Any still in use is skipped.",
            tuple(_working_folders(base / TEMP_SUBDIR, None, now=t))),
        CacheArea(
            "logs", "Old logs",
            "Logs of earlier launches that crashed, and the remote setup logs. "
            "This launch's own log is kept.",
            tuple(_old_logs(base))),
        CacheArea(
            "legacy", "Files left by earlier versions",
            "Logs and caches earlier versions kept in ~/.cache/allwell and in "
            "the system temp folder. Nothing reads them any more.",
            tuple(legacy_gui_paths())
            + tuple(_working_folders(Path(tempfile.gettempdir()),
                                     LEGACY_TEMP_PREFIXES, now=t))),
    ]
    if dataset_dir is not None:
        rows = _row_cache_dirs(Path(dataset_dir))
        areas.append(CacheArea(
            "rows", "Row cache of the open dataset",
            "Fast copies of this dataset's CSVs, in .aw_parquet folders beside "
            "them. Rebuilt from the CSVs as wells are read, so the next open "
            "is slower.",
            tuple(rows), default_on=False))
    return areas


def _row_cache_dirs(dataset_dir: Path) -> list[Path]:
    """The ``.aw_parquet`` folders of *dataset_dir*: its own, and its ``out/``'s."""
    found = []
    for folder in (dataset_dir, dataset_dir / "out"):
        candidate = folder / ROW_CACHE_DIRNAME
        if candidate.is_dir() and not candidate.is_symlink():
            found.append(candidate)
    return found


def size_of(path: Path) -> int:
    """Bytes under *path* (a file or a folder); unreadable parts count as nothing."""
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def measure(areas: Iterable[CacheArea]) -> dict[str, int]:
    """``{area.key: bytes}`` for *areas*."""
    return {area.key: sum(size_of(p) for p in area.paths) for area in areas}


def clear(areas: Iterable[CacheArea]) -> ClearResult:
    """Remove every path of *areas*; report what was freed and what was not.

    A folder is removed file by file so that one held-open file costs only
    itself: the rest of the folder still goes, and the freed total counts the
    files that went rather than the folder's size before the attempt.
    """
    result = ClearResult()
    for area in areas:
        for path in area.paths:
            _remove(path, result)
    return result


def _remove(path: Path, result: ClearResult) -> None:
    try:
        if path.is_symlink() or path.is_file():
            size = path.lstat().st_size
            path.unlink()
            result.freed += size
            return
    except FileNotFoundError:
        return
    except OSError:
        result.failed.append(str(path))
        return
    if not path.is_dir():
        return
    for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            file = Path(root) / name
            try:
                size = file.lstat().st_size
                file.unlink()
                result.freed += size
            except FileNotFoundError:
                continue
            except OSError:
                result.failed.append(str(file))
        for name in dirs:
            sub = Path(root) / name
            try:
                if sub.is_symlink():
                    sub.unlink()
                else:
                    sub.rmdir()
            except OSError:
                continue  # not empty: a file inside it failed, and is reported
    try:
        path.rmdir()
    except OSError:
        pass  # not empty: what is left is in ``result.failed``


def format_bytes(n: int) -> str:
    """``"0 B"``, ``"812 KB"``, ``"1.4 GB"`` — decimal units, as file managers show them."""
    if n < 1000:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1000.0
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if n >= 100 else f"{n:.1f} {unit}"
    return f"{n:.1f} TB"
