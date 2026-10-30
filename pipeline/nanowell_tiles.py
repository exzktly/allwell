"""A nanowell well's tiles, read from its archive once.

``pipeline.nanowell_run.run_well`` reads every timepoint's tiles to locate it, and — because every timepoint is
located before any is measured — reads them all again to measure it: about 30 tiles of 8 MB a timepoint, twice, so
8 GB a well of 17 timepoints off the cluster's shared filesystem, in bursts of a whole timepoint at a time. A
fluorescence tile was read twice too, once for its floor level and once for its nanowells' intensities. That
filesystem is also where the app's run heartbeat lands (``services/remote_heartbeat.py``), and a run of six workers
lost its heartbeat to it stalling.

:class:`WellTiles` keeps each tile, decoded in its own dtype, from the time it is first read until its timepoint has
been measured (``run_well(release=…)``), as far as the worker's share of memory allows
(``nanowell_parallel.worker_memory_budget``, ``CACHE_SHARE`` of it: the rest is for the threads locating
timepoints). A tile the cache has no room for is read again, as before. When the cache cannot hold the whole well
and the archive is on a network filesystem, the archive is first copied to the node's own disk
(:func:`local_temp_dir`: ``$SLURM_TMPDIR``, ``$TMPDIR``, the system's temporary directory) when that has room for
it, so the reads the cache cannot spare come off local disk; the copy is removed with the well.

A tile comes back as the float32 array the archive's TIF reads as — a fresh copy each time, as a fresh read was — so
nothing downstream can tell the cache is there except by the time it takes. Stdlib, numpy and tifffile only.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np

from pipeline.zip_members import ZipMemberReader

log = logging.getLogger("process_microscopy")

#: The share of the worker's memory budget the tile cache may fill; the rest is the timepoints being located at once
#: (``nanowell_run._locate_threads``).
CACHE_SHARE = 0.5

#: Filesystem types (``/proc/mounts``) whose reads cross the network, where a stall can hold up every node at once.
NETWORK_FILESYSTEMS = frozenset({
    "nfs", "nfs4", "lustre", "gpfs", "cifs", "smb3", "smbfs", "beegfs", "ceph", "cephfs", "panfs", "wekafs",
    "glusterfs", "fuse.glusterfs", "fuse.sshfs", "fuse.s3fs", "fuse.gcsfuse", "9p", "afs", "orangefs", "pvfs2",
})

#: Free space the node's disk must keep beyond the copies every worker may be making at once.
LOCAL_COPY_HEADROOM = 1 << 30


def filesystem_type(path: Path | str, mounts: str = "/proc/mounts") -> str | None:
    """The type of the filesystem *path* is on, from the longest mount point containing it; ``None`` where the
    mount table cannot be read (not Linux)."""
    try:
        target = os.path.realpath(path)
        best, kind = "", None
        with open(mounts, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 3:
                    continue
                point = fields[1].replace("\\040", " ")
                inside = target == point or target.startswith(point.rstrip("/") + "/") or point == "/"
                if inside and len(point) >= len(best):
                    best, kind = point, fields[2]
        return kind
    except OSError:
        return None


def is_network(path: Path | str) -> bool:
    """Whether *path* is on a filesystem in ``NETWORK_FILESYSTEMS``; ``False`` when that cannot be told."""
    return filesystem_type(path) in NETWORK_FILESYSTEMS


def local_temp_dir(environ=None) -> Path | None:
    """The node's own disk for a temporary copy: the first of ``$SLURM_TMPDIR``, ``$TMPDIR`` and the system's
    temporary directory that exists, is writable and is not on a network filesystem; ``None`` when none is."""
    environ = os.environ if environ is None else environ
    for raw in (environ.get("SLURM_TMPDIR"), environ.get("TMPDIR"), tempfile.gettempdir()):
        if not raw:
            continue
        folder = Path(raw)
        if folder.is_dir() and os.access(folder, os.W_OK) and not is_network(folder):
            return folder
    return None


def _phase_bytes(zip_path: Path, names) -> int:
    """The uncompressed size of the archive's members among *names* (by basename): about what they decode to."""
    wanted = {Path(n).name for n in names}
    with zipfile.ZipFile(zip_path) as archive:
        return sum(info.file_size for info in archive.infolist() if Path(info.filename).name in wanted)


class WellTiles:
    """One well's archive, opened for its run, and its phase tiles kept between locating and measuring.

    *phase_names* are the members holding the tiles (any path whose basename names one), which size the estimate. *budget* is the bytes
    the cache may hold; ``None`` asks ``nanowell_parallel.worker_memory_budget`` for the worker's share, and a
    machine that cannot say keeps nothing, as before. *local_dir* is where a copy may go (default
    :func:`local_temp_dir`); *workers* is how many workers may be copying at once (default the run's).
    """

    def __init__(self, zip_path: Path | str, well: str, phase_names, *, budget: int | None = None,
                 local_dir: Path | None = None, workers: int | None = None, network: bool | None = None):
        from pipeline import nanowell_parallel as par

        self.zip_path, self.well = Path(zip_path), well
        if budget is None:
            share = par.worker_memory_budget(workers=workers)
            budget = 0 if share is None else int(share * CACHE_SHARE)
        self.budget = budget
        self._tiles: dict = {}
        self._held = 0
        self._lock = threading.Lock()
        self.kept = self.reread = self.refused = 0
        self._seen: set = set()
        self.estimate = _phase_bytes(self.zip_path, phase_names)
        self.copy: Path | None = None
        self.copy_note = ""
        network = is_network(self.zip_path) if network is None else network
        if self.estimate > self.budget and network:
            self._copy_locally(local_dir, par.pool_workers() if workers is None else workers)
        self.reader = ZipMemberReader(self.copy or self.zip_path)

    def _copy_locally(self, local_dir: Path | None, workers: int) -> None:
        folder = local_dir or local_temp_dir()
        if folder is None:
            self.copy_note = "no node-local disk to copy the archive to"
            return
        size = self.zip_path.stat().st_size
        free = shutil.disk_usage(folder).free
        if free < size * max(1, workers) + LOCAL_COPY_HEADROOM:
            self.copy_note = f"{folder} has {free / 2 ** 30:.1f} GB free, too little to copy it to"
            return
        target = folder / f"allwell_{self.well}_{os.getpid()}_{self.zip_path.name}"
        started = time.monotonic()
        try:
            shutil.copyfile(self.zip_path, target)
        except OSError as exc:
            target.unlink(missing_ok=True)
            self.copy_note = f"copying it to {folder} failed ({exc})"
            return
        self.copy = target
        self.copy_note = f"copied to {folder} in {time.monotonic() - started:.0f} s"

    def tile(self, key, member, read: Callable) -> np.ndarray:
        """The float32 tile *key* — ``(timepoint, member)``, so a file read as phase and as a channel is one tile — from
        the cache or read from *member* by *read* (``f(file) -> array``, the TIF decoded in its own dtype), and kept
        when there is room."""
        with self._lock:
            native = self._tiles.get(key)
        if native is None:
            native = np.asarray(read(self.reader.open(member)))
            with self._lock:
                if key in self._seen:
                    self.reread += 1
                self._seen.add(key)
                if key not in self._tiles:
                    if self._held + native.nbytes <= self.budget:
                        self._tiles[key] = native
                        self._held += native.nbytes
                        self.kept += 1
                    else:
                        self.refused += 1
        return np.array(native, dtype=np.float32)

    def release(self, timepoint) -> None:
        """Forget *timepoint*'s tiles, every channel's: it has been measured, and nothing reads them again."""
        with self._lock:
            for key in [k for k in self._tiles if k[0] == timepoint]:
                self._held -= self._tiles.pop(key).nbytes
                self._seen.discard(key)

    def summary(self) -> str:
        """One line on how the well's tiles were read, for the run log."""
        parts = [f"{self.kept} tile(s) kept in memory between locating and measuring "
                 f"({self.budget / 2 ** 30:.1f} GB allowed for about {self.estimate / 2 ** 30:.1f} GB of tiles)"]
        if self.refused:
            parts.append(f"{self.refused} without room, read again ({self.reread} re-read)")
        if self.copy_note:
            parts.append(f"the archive was {self.copy_note}" if self.copy else f"the archive was read in place: "
                                                                               f"{self.copy_note}")
        return "; ".join(parts)

    def close(self) -> None:
        """Close the archive, forget the tiles and remove the local copy. Idempotent."""
        self.reader.close()
        with self._lock:
            self._tiles.clear()
            self._held = 0
        if self.copy is not None:
            self.copy.unlink(missing_ok=True)
            self.copy = None


def read_native(fileobj) -> np.ndarray:
    """A TIF from an open binary file, in its own dtype (what ``nanowell_task._read_tif`` converts to float32)."""
    import tifffile

    return tifffile.imread(fileobj if hasattr(fileobj, "read") else io.BytesIO(fileobj))
