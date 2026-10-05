"""Getting StarDist's pretrained weights onto disk, once and intact.

One job, and every function here is part of it: install a ``pkg_resources``
stand-in where setuptools no longer ships one, find the Keras model cache, hold
a cross-process download lock so two workers starting at the same moment do not
fetch the same archive over each other, notice a lock whose owner has died, and
purge a cache left truncated by an interrupted download so the next load can
succeed instead of failing the same way for ever.

Extracted from ``process_microscopy``, which is at its line budget
(``tests/test_monolith_budgets.py``) and could not otherwise grow. The budget
ratchets down to what this leaves.

Callers reach these here. ``services/frame_preview`` calls
``_ensure_pkg_resources_shim`` and ``_load_stardist_pretrained`` on this module by
attribute at call time, which is also where its tests patch them, and
``tests/test_model_recovery.py`` drives them here too. ``process_microscopy``
imports the two it calls for its own workers.

Stdlib only, Qt-free and free of ``review`` / ``widgets``, like every
``pipeline`` module. TensorFlow and StarDist are **not** imported here: the
model class is passed in, so importing this module costs nothing and the load
stays where the caller decides.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import time
from pathlib import Path

from util.aw_safe import swallowed

log = logging.getLogger("process_microscopy")


def _ensure_pkg_resources_shim() -> None:
    """Install a minimal ``pkg_resources`` stand-in if setuptools' real
    one is missing.

    setuptools >= 80 and recent Python builds drop ``pkg_resources``,
    but stardist's ``bioimageio_utils`` still does
    ``from pkg_resources import get_distribution`` at import time. Back
    that single call with ``importlib.metadata`` so the worker keeps
    loading even when setuptools no longer ships the legacy module.
    """
    import sys as _sys
    try:
        import pkg_resources  # noqa: F401
        return
    except Exception:
        swallowed()  # was: pass (silent except)
    import types
    from importlib import metadata as _md

    class _DistInfo:
        def __init__(self, version: str) -> None:
            self.version = version

    def _get_distribution(name: str) -> _DistInfo:
        try:
            return _DistInfo(_md.version(name))
        except _md.PackageNotFoundError as exc:
            raise Exception(f"DistributionNotFound: {exc}") from exc

    shim = types.ModuleType("pkg_resources")
    shim.get_distribution = _get_distribution           # type: ignore[attr-defined]
    shim.DistributionNotFound = Exception               # type: ignore[attr-defined]
    shim.VersionConflict = Exception                    # type: ignore[attr-defined]
    shim.parse_version = lambda v: v                    # type: ignore[attr-defined]
    _sys.modules["pkg_resources"] = shim


# Substrings in an exception message that indicate a corrupt / truncated cached
# model file (HDF5 weights or its download archive) rather than a transient
# network / SSL problem — so we know it is safe to purge the cache and re-fetch.
_CORRUPT_MODEL_MARKERS = (
    "truncated file",
    "unable to synchronously open file",
    "file signature not found",
    "bad object header",
    "not a valid hdf5",
    "unable to open file",
    "bad magic number",         # truncated zip archive
    "file is not a zip file",
)


def _purge_stardist_cache() -> list[str]:
    """Remove cached StarDist pretrained-model files so the next
    ``from_pretrained`` re-downloads them.

    ``StarDist2D.from_pretrained`` caches the model under the keras cache
    (``$KERAS_HOME`` or ``~/.keras/models/…``) as ``weights_best.h5`` +
    ``config.json``. A run interrupted mid-download (e.g. the worker was killed,
    or an earlier failure aborted the pool) can leave a **truncated**
    ``weights_best.h5``; the next run reuses it and h5py dies with
    ``OSError: Unable to synchronously open file (truncated file: eof = …)``.

    Deletes any model folder containing a StarDist weights file (and stray
    ``*.h5`` / partial download archives) under each keras cache root. Returns
    the paths removed, for logging.
    """
    roots: list[Path] = []
    kh = os.environ.get("KERAS_HOME")
    if kh:
        roots.append(Path(kh))
    roots.append(Path(os.path.expanduser("~")) / ".keras")
    removed: list[str] = []
    seen: set[str] = set()
    for models in (r / "models" for r in roots):
        if not models.is_dir():
            continue
        targets: set[Path] = set()
        for p in models.rglob("*"):
            if not p.is_file():
                continue
            low = p.name.lower()
            if low == "weights_best.h5" or low.endswith("weights_best.h5"):
                targets.add(p.parent)                       # whole model folder
            elif low.endswith((".h5", ".zip", ".part", ".download", ".tmp")):
                targets.add(p)                              # stray / partial file
        for t in targets:
            key = str(t)
            if key in seen:
                continue
            seen.add(key)
            try:
                if t.is_dir():
                    shutil.rmtree(t, ignore_errors=True)
                else:
                    t.unlink()
                removed.append(key)
            except OSError:
                pass
    return removed


# Cross-process download serialisation (see _stardist_download_lock).
_STARDIST_LOCK_TIMEOUT = 120.0   # s; max wait on a *live* download before going unlocked
_STARDIST_LOCK_STALE   = 180.0   # s; reclaim a no-live-PID lock older than this
_STARDIST_LOCK_POLL    = 0.25    # s between acquisition attempts


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a local PID (the download workers all share
    the host). Unknown/unsupported (e.g. Windows ``os.kill(pid, 0)``) → assume
    alive, so we never reclaim a lock we can't prove is dead."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False                       # definitively gone
    except PermissionError:
        return True                        # exists, owned by another user
    except OSError:
        return True                        # can't tell → assume alive (safe)
    return True


def _stardist_lock_is_dead(lockdir: Path) -> bool:
    """A leaked lock is reclaimable when its recorded holder PID(s) are no longer
    running (instant — the crashed/killed-worker case), or — no PID recorded, a
    reused PID, or a hung holder — it is older than the stale window."""
    pids: list[int] = []
    try:
        for p in lockdir.iterdir():
            if p.name.startswith("pid.") and p.name[4:].isdigit():
                pids.append(int(p.name[4:]))
    except OSError:
        return False
    if pids and not any(_pid_alive(pid) for pid in pids):
        return True                        # all recorded holders dead → reclaim now
    try:
        return (time.time() - lockdir.stat().st_mtime) > _STARDIST_LOCK_STALE
    except OSError:
        return False


def _stardist_cache_models_dir() -> Path:
    """Primary keras model-cache dir — ``$KERAS_HOME`` else ``~/.keras``, then
    ``/models`` — where ``from_pretrained`` downloads the weights. Created if
    missing. Mirrors the root logic in :func:`_purge_stardist_cache`."""
    root = os.environ.get("KERAS_HOME") or os.path.join(os.path.expanduser("~"), ".keras")
    models = Path(root) / "models"
    models.mkdir(parents=True, exist_ok=True)
    return models


@contextlib.contextmanager
def _stardist_download_lock(timeout: float | None = None):
    """Cross-process advisory lock serialising the StarDist model download/purge.

    All spawn-pool workers run ``_worker_init`` → ``from_pretrained`` at once; on
    a cold cache they download the same file concurrently and truncate each
    other (the recurring "cache corrupt" failure), and an unguarded purge can
    delete another worker's in-flight download. Atomic ``os.mkdir`` of a lock
    directory is the primitive: exactly one process wins and downloads; the rest
    block, then load a *warm* cache. Degrades safely — on timeout, an unusable
    cache dir, or any lock error, it proceeds WITHOUT the lock rather than fail
    the run, and reclaims a stale lock left by a dead holder.
    """
    if timeout is None:                 # resolve at call time so tests can patch it
        timeout = _STARDIST_LOCK_TIMEOUT
    try:
        lockdir = _stardist_cache_models_dir() / ".allwell_stardist_download.lock"
    except OSError:
        yield                       # cache dir unusable — run unsynchronised
        return
    deadline = time.monotonic() + timeout
    held = False
    while True:
        try:
            os.mkdir(lockdir)
            held = True
            # Record our PID so other workers can reclaim instantly if we die.
            try:
                (lockdir / f"pid.{os.getpid()}").touch()
            except OSError:
                pass
            break
        except FileExistsError:
            # Reclaim immediately if the holder is dead (PID gone) — the leaked-
            # lock-after-crash case — so a rerun never stalls for minutes; a
            # genuinely live download is waited on up to the (short) timeout.
            if _stardist_lock_is_dead(lockdir):
                shutil.rmtree(lockdir, ignore_errors=True)
                if lockdir.exists():            # couldn't remove → don't spin
                    log.warning("StarDist download lock could not be reclaimed — "
                                "proceeding without it.")
                    break
                continue
            if time.monotonic() >= deadline:
                log.warning("StarDist download lock held by a live process "
                            ">%.0fs — proceeding without it.", timeout)
                break
            time.sleep(_STARDIST_LOCK_POLL)
        except OSError:
            break                   # can't create the lock (perms/fs) — unsynchronised
    try:
        yield
    finally:
        if held:
            shutil.rmtree(lockdir, ignore_errors=True)


def _load_stardist_pretrained(StarDist2D, name: str = "2D_versatile_fluo"):
    """Load a StarDist pretrained model, recovering once from a corrupt cache.

    If the first load fails because the cached weights / archive is truncated
    (see :data:`_CORRUPT_MODEL_MARKERS`) — the classic symptom of a previous
    run that died mid-download — purge the cache and re-download exactly once.
    Any other error (and a second failure) propagates unchanged.

    The whole body runs under :func:`_stardist_download_lock` so concurrent
    workers serialise: only one downloads (the rest hit a warm cache), and the
    purge+retry can't delete another worker's in-flight download.
    """
    with _stardist_download_lock():
        try:
            return StarDist2D.from_pretrained(name)
        except Exception as exc:
            if not any(m in str(exc).lower() for m in _CORRUPT_MODEL_MARKERS):
                raise
            removed = _purge_stardist_cache()
            log.warning(
                "StarDist model cache was corrupt (%s: %s) — purged %s and "
                "re-downloading once.",
                exc.__class__.__name__, exc, removed or "(nothing found to remove)")
            return StarDist2D.from_pretrained(name)

