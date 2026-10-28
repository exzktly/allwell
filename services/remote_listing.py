"""Asking the remote host what files it holds — and telling "none" from "could not ask".

Every decision on the fetch, verification and cleanup path starts from a listing of the remote: which CSVs a
well has, which wells have an ``<well>_out.zip``, which nanowell tables exist. Those listings used to be written
``ls -1 <glob> 2>/dev/null || true`` with the ssh exit status never read, so an ssh that failed — a throttled login
node refusing the connection, a dropped link, a timeout — produced exactly the output of a directory with nothing
in it. The code downstream then acted on "nothing there": it marked a well fetched whose CSV never arrived, passed
verification over zero wells and removed the run directory, released a nanowell well whose re-anchored output was
never pulled (the remote pipeline review (whose tracker is in git history) rows A1–A4, A10).

So a listing here has three answers, never two:

* a list of paths, possibly empty — the remote ran the script to its end and this is what matched;
* ``None`` — the listing itself failed (ssh exited non-zero, timed out, or could not be started), so what the
  remote holds is **unknown**. Every caller must treat that as "keep everything and fail visibly", never as
  "there is nothing to keep".

The remote script expands the glob in bash and tests each match with ``[ -e ]`` instead of relying on ``ls``
failing for an unmatched glob, so it exits 0 on a genuinely empty directory and the exit status carries only the
transport's verdict. The script is piped to ``bash -s`` as bytes so a ``~`` in the path expands on the remote and
Windows text mode cannot turn ``\\n`` into ``\\r\\n``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

#: How long one listing may take. A listing is one ``readdir`` on the remote; a minute is a dead link, not a slow one.
LISTING_TIMEOUT_S = 60

_BYTES_MARK = "---allwell-bytes---"


def glob_script(path_glob: str, *, with_bytes: bool = False) -> str:
    """The remote bash that prints every existing path matching *path_glob* (space-separated globs allowed), one
    per line, and with *with_bytes* their total size after a marker line. Exits 0 whenever it ran to its end."""
    script = (f"for f in {path_glob}; do\n"
              f'  if [ -e "$f" ]; then printf \'%s\\n\' "$f"; fi\n'
              f"done\n")
    if with_bytes:
        script += (f"echo '{_BYTES_MARK}'\n"
                   f"ls -ln {path_glob} 2>/dev/null | awk '{{s+=$5}} END {{print s+0}}'\n")
    return script + "exit 0\n"


def describe_failure(rc: int | None, stderr: str = "", exc: BaseException | None = None) -> str:
    """Why a listing is unknown, in words for the run log."""
    if exc is not None:
        return f"{type(exc).__name__}: {exc}"
    detail = (stderr or "").strip().splitlines()
    tail = f": {detail[-1]}" if detail else ""
    if rc == 124:
        return f"ssh timed out (rc=124){tail}"
    return f"ssh exited {rc}{tail}"


def list_remote_glob(
    host, src_glob: str, *, timeout: float = LISTING_TIMEOUT_S,
    on_failure: Callable[[str], None] | None = None,
) -> tuple[str, list[str] | None, int | None]:
    """``(host alias, paths or None, total bytes or None)`` for ``"<alias>:<glob>"``, in one ssh.

    ``paths`` is ``None`` when the listing failed and the remote's contents are unknown; *on_failure*, when given,
    receives the reason. ``total bytes`` is ``None`` whenever it is unknown or zero — it is a progress-dial
    denominator only, and an unknown one means no dial, never an invented one.
    """
    alias, path_glob = src_glob.split(":", 1)
    try:
        result = subprocess.run(
            host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
            input=glob_script(path_glob, with_bytes=True).encode("utf-8"),
            capture_output=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        if on_failure is not None:
            on_failure(describe_failure(None, exc=exc))
        return alias, None, None
    if result.returncode != 0:
        if on_failure is not None:
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            on_failure(describe_failure(result.returncode, stderr))
        return alias, None, None
    out = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    head, _, tail = out.partition(_BYTES_MARK)
    files = [ln for ln in head.splitlines() if ln.strip()]
    total: int | None = None
    for token in tail.split():
        if token.isdigit():
            total = int(token) or None
            break
    return alias, files, total


def list_completed_wells(ssh_script, remote_dir: str, *,
                         on_failure: Callable[[str], None] | None = None) -> set[str] | None:
    """The wells with an ``<well>_out.zip`` in *remote_dir*, or ``None`` when the listing failed.

    *ssh_script* is ``DatasetCache._ssh_script``. A directory that does not exist lists as empty (the remote ran
    and found nothing); only a failure of the listing itself is ``None``.
    """
    from services.remote_run_state import _canonical_well_from_path

    try:
        listing = ssh_script(glob_script(f"{remote_dir}/*_out.zip"), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        if on_failure is not None:
            on_failure(describe_failure(None, exc=exc))
        return None
    rc = getattr(listing, "returncode", None) if listing is not None else None
    if rc != 0:
        if on_failure is not None:
            on_failure(describe_failure(rc, str(getattr(listing, "stderr", "") or "")))
        return None
    wells: set[str] = set()
    for line in str(listing.stdout or "").splitlines():
        stem = Path(line.strip()).name.removesuffix(".zip")
        if stem.endswith("_out"):
            label = _canonical_well_from_path(stem[:-len("_out")])
            if label is not None:
                wells.add(label)
    return wells
