"""The node's own disk: which folders on the machine a run is on are local, and where a run's scratch goes there.

Two callers need the same answer. ``pipeline.nanowell_tiles`` copies a well's archive to the node before reading its
tiles, and ``pipeline.io.scratch_base_for`` puts a run's working files (extracted inputs, intermediate images,
per-field checkpoints) somewhere. On a cluster the default for the second is the user's home folder, which is a
network filesystem shared by every node and served, on AWS ParallelCluster, by the head node the app's ssh and run
heartbeat also go through. A run's scratch traffic there stalled the heartbeat's ``touch`` past its 75 s allowance
(``services/remote_heartbeat.py``).

:func:`node_local_scratch` is the answer for a run inside a Slurm job that was given no scratch folder. It tries
``/scratch`` first — where AWS ParallelCluster mounts a node's local NVMe drive by default — then ``$SLURM_TMPDIR``
on the sites that set it (job-private there), and takes the first that is a directory, writable, on neither a network
nor an in-memory filesystem, and has :data:`LOCAL_SCRATCH_MIN_FREE` free. ``$TMPDIR`` is not a candidate: a login
environment often sets it to ``/tmp``. It never takes the system temporary directory: on a node shared with other jobs (a launcher without ``--exclusive``) that is
the node's boot disk, and filling it can take the node out of service for everyone on it. When none qualifies the
run keeps the shared default, and the reason says what each candidate lacked.

Stdlib only: it is shipped to the remote worker (``services/remote_manifest.py``).
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
from pathlib import Path

#: Filesystem types (``/proc/mounts``) whose reads cross the network, where a stall can hold up every node at once.
NETWORK_FILESYSTEMS = frozenset({
    "nfs", "nfs4", "lustre", "gpfs", "cifs", "smb3", "smbfs", "beegfs", "ceph", "cephfs", "panfs", "wekafs",
    "glusterfs", "fuse.glusterfs", "fuse.sshfs", "fuse.s3fs", "fuse.gcsfuse", "9p", "afs", "orangefs", "pvfs2",
})


#: Filesystems that hold their files in memory: a copy there is memory taken exactly when memory was short, and it
#: counts against the job's own limit.
MEMORY_FILESYSTEMS = frozenset({"tmpfs", "ramfs", "devtmpfs"})


def _unescape(field: str) -> str:
    """A ``/proc/mounts`` field with its octal escapes (``\\040`` space, ``\\011`` tab, ``\\012`` newline,
    ``\\134`` backslash) decoded."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


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
                point = _unescape(fields[1])
                inside = target == point or target.startswith(point.rstrip("/") + "/") or point == "/"
                if inside and len(point) >= len(best):
                    best, kind = point, fields[2]
        return kind
    except OSError:
        return None


def is_network(path: Path | str) -> bool:
    """Whether *path* is on a filesystem in ``NETWORK_FILESYSTEMS``; ``False`` when that cannot be told."""
    return filesystem_type(path) in NETWORK_FILESYSTEMS


#: Where AWS ParallelCluster mounts a compute node's local NVMe drive by default
#: (``SlurmQueues → ComputeSettings → LocalStorage → EphemeralVolume → MountDir``).
NODE_SCRATCH_MOUNT = "/scratch"

#: Free space a node-local folder must have before a run's scratch goes there. A run's working set is about six
#: full-size float32 intermediates per field for every well in progress; this is far more than one well needs and
#: well short of what a local NVMe drive offers, so a drive mostly filled by other jobs is passed over.
LOCAL_SCRATCH_MIN_FREE = 20 * 2 ** 30


def _local(folder: Path) -> str:
    """``""`` when *folder* can hold a run's scratch, otherwise what it lacks."""
    if not folder.is_dir():
        return "does not exist"
    if not os.access(folder, os.W_OK):
        return "is not writable"
    kind = filesystem_type(folder)
    if kind in NETWORK_FILESYSTEMS:
        return f"is on a network filesystem ({kind})"
    if kind in MEMORY_FILESYSTEMS:
        return f"is held in memory ({kind})"
    return ""


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # getpass raises a bare KeyError/OSError where no name is known
        return str(os.getuid()) if hasattr(os, "getuid") else "user"


def node_local_scratch(environ=None, *, min_free: int = LOCAL_SCRATCH_MIN_FREE,
                       free_bytes=lambda p: shutil.disk_usage(p).free) -> tuple[Path | None, str]:
    """``(folder, reason)``: a node-local folder for a Slurm job's scratch, or ``None`` and why there is none.

    ``(None, "")`` outside a Slurm job (no ``SLURM_JOB_ID``): the question does not arise there. The folder returned
    is ``<candidate>/allwell-<user>``, created; *reason* says which disk it is and how much it has free, or, with no
    folder, what every candidate lacked.
    """
    environ = os.environ if environ is None else environ
    if not str(environ.get("SLURM_JOB_ID", "")).strip():
        return None, ""
    candidates = [NODE_SCRATCH_MOUNT, environ.get("SLURM_TMPDIR")]
    lacked: list[str] = []
    seen: set[Path] = set()
    for raw in candidates:
        if not raw or not str(raw).strip():
            continue
        folder = Path(str(raw).strip())
        if folder in seen:
            continue
        seen.add(folder)
        why = _local(folder)
        if not why:
            free = free_bytes(folder)
            if free < min_free:
                why = f"has {free / 2 ** 30:.1f} GB free, under {min_free / 2 ** 30:.0f} GB"
        if why:
            lacked.append(f"{folder} {why}")
            continue
        target = folder / f"allwell-{_user()}"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            lacked.append(f"{target} could not be created ({exc})")
            continue
        return target, f"this node's own disk {folder} ({free / 2 ** 30:.0f} GB free)"
    return None, "no node-local disk qualifies: " + "; ".join(lacked)
