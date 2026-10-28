"""Getting the pipeline's code onto a remote host — one directory per version.

Every run pushes the files the pipeline needs (``remote_manifest``) before it
starts. They used to go into one shared ``remote_project_dir``, which made a
run's code whatever the *last* sync left there: a second app instance of a
different version (or a newer build started while a batched run was between
batches) rewrote the files under a run that was still reading them. Python
imports lazily, and a batched run starts the pipeline afresh for every batch,
so one run could execute two versions — the mixed code nothing would report.

So each run syncs into ``<remote_project_dir>/code/<digest>/``, where the
digest is a hash of the files' own contents, and launches the pipeline from
there. A directory's contents are fixed by its name: two versions never share
one, the same version re-syncs as rsync's no-op, and a run's code cannot change
under it. A version directory nobody has synced into for :data:`CODE_KEEP_DAYS`
is removed after a successful sync, so old builds do not pile up for ever.

The bootstrap still syncs into ``remote_project_dir`` itself (``versioned=False``),
because its environment install reads ``requirements-remote.txt`` from there.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path

from services.remote_config import RemoteHost, resolve_rsync_binary
from services.remote_transport import (
    _rsync_ssh_transport,
    _rsync_supports_info_flag,
    _rsync_with_retry,
)

#: Subdirectory of ``remote_project_dir`` holding one directory per version.
CODE_SUBDIR = "code"
#: A version directory not synced into for this many days is removed.
CODE_KEEP_DAYS = 30


def code_digest(root: Path, files: Iterable[str]) -> str:
    """A short hash of *files* (paths relative to *root*) and their bytes."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8") + b"\0")
        h.update((root / rel).read_bytes() + b"\0")
    return h.hexdigest()[:16]


def versioned_code_dir(project_dir: str, digest: str) -> str:
    """Where the code whose digest is *digest* lives on the remote."""
    return f"{project_dir}/{CODE_SUBDIR}/{digest}"


def sync_pipeline_code(host: RemoteHost, cache, root: Path, files: Iterable[str], *,
                       log_fn: Callable[[str], None] | None = None,
                       versioned: bool = True) -> str:
    """Push *files* from *root* to *host*; return the remote directory that
    now holds them (the version directory, or ``remote_project_dir`` itself
    when ``versioned`` is false). *cache* is the run's ``DatasetCache``, whose
    ``_ssh_run`` makes the directories.

    Idempotent and cheap: rsync's mtime+size skip-pass makes a no-op sync one
    round trip. The relative layout is preserved (``pipeline/io.py`` lands at
    ``<dir>/pipeline/io.py``).
    """
    files = list(files)
    log = log_fn or (lambda _m: None)
    missing_local = [f for f in files if not (root / f).is_file()]
    if missing_local:
        raise FileNotFoundError(
            "Required pipeline files missing locally: "
            + ", ".join(missing_local) + f"  (looked under {root})")
    target = (versioned_code_dir(host.remote_project_dir, code_digest(root, files))
              if versioned else host.remote_project_dir)

    # rsync with individual file sources doesn't create intermediate dirs on
    # the target, so one mkdir -p makes every subdirectory the manifest names.
    subdirs = sorted({str(Path(f).parent) for f in files if str(Path(f).parent) not in (".", "")})
    cache._ssh_run(["mkdir", "-p", target, *[f"{target}/{d}" for d in subdirs]], check=True)

    # Bare relative sources with cwd=root, so ``-R`` preserves only the path
    # below the repository root. The ``/./`` pivot in an absolute path is not
    # honoured by older rsync (macOS's 2.6.9), which mirrored the whole local
    # path on the remote instead.
    cmd = [resolve_rsync_binary(), *_rsync_ssh_transport(host), "-aR"]
    if _rsync_supports_info_flag():
        cmd.append("--info=stats0")  # rsync >= 3.1 only; purely cosmetic
    cmd += [*files, f"{host.ssh_alias}:{target}/"]
    result = _rsync_with_retry(cmd, log_fn=log_fn, timeout=60, cwd=str(root))
    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"rsync exited {result.returncode}"
        raise RuntimeError(f"Failed to sync pipeline files to remote ({host.ssh_alias}): {err}")
    log(f"[remote] code sync: {len(files)} file(s) verified on {host.ssh_alias}:{target}\n")
    if versioned:
        _prune_old_versions(host, cache, target, log)
    return target


def _prune_old_versions(host: RemoteHost, cache, target: str, log: Callable[[str], None]) -> None:
    """Mark *target* as just used, then remove version directories nobody has
    synced into for :data:`CODE_KEEP_DAYS`. Failure costs disk, never a run."""
    root = f"{host.remote_project_dir}/{CODE_SUBDIR}"
    touched = cache._ssh_run(["touch", target], check=False)
    pruned = cache._ssh_run(
        ["find", root, "-mindepth", "1", "-maxdepth", "1", "-type", "d",
         "-mtime", f"+{CODE_KEEP_DAYS}", "!", "-name", target.rsplit("/", 1)[-1],
         "-exec", "rm", "-rf", "{}", "+"], check=False)
    for what, r in (("mark the code version in use", touched),
                    ("remove old code versions", pruned)):
        if r.returncode != 0:
            log(f"[remote] warn: could not {what} under {root} "
                f"(exit {r.returncode}: {(r.stderr or '').strip()})\n")
