"""How bytes and commands actually reach a remote host.

The transport layer under ``services/remote_runner.py``: what this rsync build
can be asked to do, how a path is quoted so a remote shell expands ``~`` rather
than making a directory called it, how a transfer is retried when the network
blinks, and how rsync's progress chatter is turned back into lines.

Extracted because it is a self-contained cluster — sixteen names depending on
nothing else in that module — and because that module carries a line budget
whose point is that a cohesive cluster leaves rather than the file growing
again.

Knows nothing about a *run*: no run id, no journal, no well, no plate. It moves
bytes between two paths, one of which may be on another machine.
``remote_runner`` re-exports every name, so call sites and tests keep importing
them from where they always did.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path

from services.remote_config import (
    RemoteHost,
    resolve_rsync_binary,
    resolve_ssh_for_rsync,
    to_rsync_path,
)
from util.aw_safe import swallow
from services.subprocess_text import TEXT_UTF8

def _fmt_bytes(n: float) -> str:
    """Bytes at a unit a human reads without counting zeros.

    A fixed MB format renders every archive under a megabyte as "0 MB", which
    reads as a broken measurement rather than a small one — and the planner
    reports sizes for archives that legitimately span kilobytes to gigabytes.
    """
    for unit, scale in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{int(n)} B"

def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"

def _remote_quote(s: str) -> str:
    """Shell-quote ``s`` for embedding in a remote bash command line,
    preserving leading-``~/`` expansion.

    Stock ``shlex.quote`` wraps everything in single quotes, which the
    remote bash treats as literal — so ``shlex.quote("~/foo")`` →
    ``'~/foo'``, and the remote ``mkdir -p '~/foo'`` creates a folder
    literally named ``~`` containing ``foo``. (This bit a user in
    v5.2.10 with a `[remote.j]` config whose ``remote_staging_dir`` was
    the default ``~/.allwell-staging``.)

    Fix: a leading ``~/`` is rewritten to ``"$HOME"`` followed by the
    single-quoted remainder. The remote bash expands ``$HOME`` inside
    the double-quoted half and keeps the rest literal:

        _remote_quote("~/foo bar")  →  '"$HOME"'\\''/foo bar'\\'
                                       ^^^^^^^^   ^^^^^^^^^^^^^
                                       expanded   literal (quoted)

    A bare ``~`` becomes ``"$HOME"`` directly. Everything else falls
    through to ``shlex.quote`` so spaces / metacharacters in regular
    paths stay safe.
    """
    if s == "~":
        return '"$HOME"'
    if s.startswith("~/"):
        return '"$HOME"' + shlex.quote(s[1:])
    return shlex.quote(s)

def _remote_join(remote_argv: list[str]) -> str:
    """Like ``shlex.join`` but tilde-aware. Use everywhere we build a
    remote command line for execution by the remote bash."""
    return " ".join(_remote_quote(a) for a in remote_argv)

@lru_cache(maxsize=1)
def _rsync_version() -> tuple[int, int]:
    """Parsed ``(major, minor)`` of the local rsync, or ``(0, 0)`` if
    rsync is missing / unparseable. Cached for the process lifetime.
    Shared by ``_rsync_supports_info_flag`` (≥ 3.1) and
    ``_rsync_supports_append_verify`` (≥ 3.0) so we don't fork+exec
    ``rsync --version`` twice on startup."""
    try:
        result = subprocess.run(
            [resolve_rsync_binary(), "--version"],
            capture_output=True, timeout=5, **TEXT_UTF8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return (0, 0)
    if result.returncode != 0:
        return (0, 0)
    first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
    match = re.search(r"version\s+(\d+)\.(\d+)", first_line)
    if not match:
        return (0, 0)
    return int(match.group(1)), int(match.group(2))

def _rsync_supports_info_flag() -> bool:
    """Whether the local rsync accepts ``--info=...``.

    Added in rsync 3.1.0 (2013). macOS still ships the very old
    rsync 2.6.9 by default (Apple licensing — they froze before
    rsync went GPLv3); HPC clusters and minimal containers can
    have similar vintage. Those rsyncs reject ``--info=...`` with
    "rsync: --info=stats0: unknown option" → exit 1.

    Detected once at first call via ``rsync --version`` (which both
    old and new rsyncs support) and cached for the process lifetime.
    Returns False on any error so the caller defaults to the
    safer "omit --info" path.
    """
    return _rsync_version() >= (3, 1)

def _rsync_progress_flags() -> list[str]:
    """Flags that make rsync narrate a transfer, for whatever rsync is here.

    ``--info=name1,stats2,progress2`` needs rsync ≥ 3.1.0. macOS ships 2.6.9 and
    that is the machine most runs are launched from, so on the common case the
    gate around ``--info`` fell through to **nothing at all** — rsync ran
    silently for the entire transfer. Everything downstream is driven off its
    output: ``_looks_like_rsync_filename`` turns per-file lines into
    ``[remote] ↑ <well>.zip`` (the Upload rail and the per-well "uploaded"
    colour), and the progress stream drives the transfer bar and the per-well
    pie slices. With no flags none of it fires, so a multi-hour upload showed
    ``Upload 0/N`` and an unchanged plate from the first second to the last,
    which is indistinguishable from a hang.

    ``-v`` and ``--progress`` both predate 3.0 and are what the old rsync
    understands: ``-v`` names each file as it goes, ``--progress`` emits the
    percentage stream. The parsers already accept both shapes.
    """
    if _rsync_supports_info_flag():
        return ["--info=name1,stats2,progress2"]
    return ["-v", "--progress"]

def _rsync_supports_append_verify() -> bool:
    """Whether the local rsync accepts ``--append-verify``.

    Added in rsync 3.0.0 (2008). macOS's bundled rsync 2.6.9 rejects
    it with "Unrecognized option --append-verify" → exit 1, which was
    the failure mode that motivated this probe (v5.2.14 user hit it
    on macOS). On legacy rsync we drop the flag entirely; ``--partial``
    alone is still enough to resume an interrupted transfer (just
    without the tail-checksum verification step)."""
    return _rsync_version() >= (3, 0)

def _rsync_ssh_transport(host: RemoteHost) -> list[str]:
    """Build the ``-e "ssh …"`` argument every rsync in this module
    should pass.

    rsync invokes ssh as a separate child process — it does NOT use
    the runner's ``host.ssh_argv()`` machinery. Without an explicit
    ``-e`` flag, rsync's ssh would:

    * **Skip the wizard-generated key.** v5.2.2 threaded ``ssh_key_path``
      through every direct ssh call, but the rsync calls in this module
      pass bare argv with no ``-i``, so rsync's underlying ssh falls
      through to ssh's default identity search and never tries
      ``~/.ssh/allwell_<alias>``. A keys-only remote then rejects the
      connection with "Permission denied".

    * **Refuse first-contact hosts.** No ``StrictHostKeyChecking=
      accept-new``, so a host that isn't in ``~/.ssh/known_hosts`` yet
      kills rsync with exit 255 "Host key verification failed", same
      class of failure the bootstrap's ``_ssh_run`` had before v5.2.3.

    v5.x.x: the ssh binary is pulled via :func:`resolve_ssh_for_rsync`
    (not :func:`resolve_ssh_binary`) so that on Windows we pick an
    ssh.exe from the SAME MSYS / Cygwin runtime as the resolved
    rsync.exe. Mismatched runtimes (MSYS2 rsync + Git for Windows
    ssh, the two ship different msys-2.0.dll versions) make
    Cygwin's fork-emulation fail with ``dup() in/out/err failed``
    before ssh runs, surfacing as rsync exit 12 / "connection
    unexpectedly closed".

    This helper centralises the policy so every call site (there are
    seven as of v5.2.5: cache upload, file sync, per-well fetch, glob
    pull, optional pull, two end-of-run retries) gets the same options.
    """
    ssh_inner = [
        resolve_ssh_for_rsync(),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
    ]
    if host.ssh_key_path:
        key = str(Path(host.ssh_key_path).expanduser())
        ssh_inner += ["-i", key]
    # Join with shlex.quote so a path with spaces (rare on Linux, common
    # on macOS / Windows user dirs) survives the round-trip through the
    # shell rsync uses internally.
    return ["-e", " ".join(shlex.quote(a) for a in ssh_inner)]

_RSYNC_FILENAME_RE = re.compile(
    # Positive match: a single token (NO whitespace anywhere) that
    # either contains a path separator (e.g. ``subdir/foo.tif``) or
    # ends with a 1–8 character extension (e.g. ``A01.zip``,
    # ``process_microscopy.py``, ``requirements-remote.txt``).
    r"^\S+(?:[/\\]\S+|\.[A-Za-z0-9]{1,8})$"
)

def _looks_like_rsync_filename(line: str) -> bool:
    """Return True iff ``line`` looks like an rsync ``--info=name1``
    filename emission (vs progress, stats, warning, or error noise).

    Uses a POSITIVE match rather than elimination. The original
    negate-known-prefixes approach was leaky — rsync emits dozens of
    status lines (``total size is X``, ``Number of files: N``,
    ``sending incremental file list``, ``created directory /foo``,
    ``file has vanished: x.tmp``, ``deleting old.zip``, etc.) and
    every new one would silently get misclassified as a "filename"
    until the prefix list was extended.

    The positive form keys on two observations: (a) every transferred
    filename rsync emits with ``--info=name1`` is a single
    whitespace-free token, and (b) every status/diagnostic line
    contains whitespace (the prose part). So a no-whitespace line
    that also looks like a path is the filename; anything else is
    noise.
    """
    if not line:
        return False
    return bool(_RSYNC_FILENAME_RE.match(line))

def _iter_rsync_lines(stdout) -> Iterable[str]:
    """Yield rsync stdout 'lines' split on BOTH ``\\n`` and ``\\r``.

    Critical for surfacing ``--info=progress2`` updates in real time:
    rsync terminates its in-place updating progress bar with ``\\r``,
    not ``\\n``. The default ``for line in proc.stdout`` only splits
    on ``\\n``, so during a multi-GB transfer of a single well zip
    the progress updates accumulate in the pipe buffer for minutes
    until rsync moves on to the next file (or finishes). On Windows
    where non-TTY pipes are block-buffered at the OS level, this
    becomes ten-plus-minute silences in the GUI log — users
    reasonably assume the pipeline has hung.

    Reads small chunks (raw bytes), splits on ``[\\r\\n]+``, decodes
    each chunk on its own. Empty splits (consecutive terminators,
    typical of ``\\r\\n`` from Windows-built tools) are filtered.
    Returns a generator so the caller can interleave per-line work
    with reading.
    """
    buf = b""
    line_re = re.compile(rb"[\r\n]+")
    while True:
        chunk = stdout.read(64)
        if not chunk:
            if buf:
                yield buf.decode("utf-8", errors="replace")
            return
        buf += chunk
        parts = line_re.split(buf)
        # Last element may be a partial line (no terminator yet);
        # keep it for the next iteration.
        for part in parts[:-1]:
            if part:
                yield part.decode("utf-8", errors="replace")
        buf = parts[-1]

# Exit codes rsync uses to signal a network-side failure (per rsync(1)
# manpage, "EXIT VALUES" section). Anything outside this set is treated
# as a configuration / permission / logic error and surfaces immediately
# without retry — re-running such an error wouldn't help and would mask
# the real problem.
#
#   10: error in socket I/O
#   11: error in file I/O
#   12: error in rsync protocol data stream
#   30: timeout in data send/receive
#   35: timeout waiting for daemon connection
#
# 11 is here because it is what a fault on the *local* end of a transfer looks
# like: rsync writing a fetched well onto a network-mounted output dir reports
# ``write failed on "…": Bad file descriptor (9)`` and exits 11 when the SMB
# session drops and reconnects mid-file. That is the same transient the
# pipeline's own writes retry through, and without it a single blip lost the
# well's CSVs for the rest of the run. Genuine, non-transient file errors
# (permission denied, no such file) exit 23 or 3 and still surface immediately.
_RSYNC_RETRY_CODES = {10, 11, 12, 30, 35}

# Backoff schedule for rsync retries — mirrors the git push/pull policy
# documented at the top of the runner's invocation env. Total wait
# across the three retries is 14 s, an upper bound the user can sit
# through without thinking the run is hung.
_RSYNC_RETRY_DELAYS = (2.0, 4.0, 8.0)

def _rsync_with_retry(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None = None,
    timeout: int | None = 300,
    capture_output: bool = True,
    cwd: str | None = None,
    stop_event: threading.Event | None = None,
) -> subprocess.CompletedProcess:
    """Run an rsync command, retrying on transient network failures.

    Returns the final ``CompletedProcess``. A success on the first try
    is the common path; transient failures (rsync exit codes 10/12/30/35
    or an OSError from the subprocess machinery itself) trigger up to
    three retries with exponential backoff. Non-transient failures
    (any other non-zero exit code) return immediately so logic /
    configuration errors don't get masked behind a retry storm.

    This is the v5.1.1 patch to the v5.0.0 runner — every rsync call
    site in the framework funnels through here.

    v5.2.12: ``cwd`` is passed through to ``subprocess.run`` so callers
    can use bare relative source paths (the only portable way to make
    ``rsync -aR`` recreate the right directory layout on the remote;
    the absolute-path-with-``/./``-pivot trick is fragile on older
    rsync versions, which mirror the full local path instead).

    v5.2.28: ``stop_event`` makes the rsync cancellable. When set, the
    in-flight rsync subprocess is terminated and the retry loop
    short-circuits with synthetic ``rc=130`` (SIGINT-style). The
    Fetcher passes its own stop event through here so the Stop
    button actually interrupts per-well downloads instead of
    waiting for each rsync to finish on its own.
    """
    log = log_fn or (lambda _m: None)
    delays = (0.0,) + _RSYNC_RETRY_DELAYS  # delay before each attempt
    last_result: subprocess.CompletedProcess | None = None
    last_exc: BaseException | None = None

    for attempt, delay in enumerate(delays):
        if stop_event is not None and stop_event.is_set():
            log("[remote] rsync cancelled by Stop request.\n")
            return subprocess.CompletedProcess(
                cmd, returncode=130, stdout="", stderr="cancelled",
            )
        if delay > 0:
            log(
                f"[remote] rsync retry {attempt}/{len(_RSYNC_RETRY_DELAYS)} "
                f"in {delay:.0f}s (last exit code "
                f"{last_result.returncode if last_result else '?'})\n"
            )
            # Cooperatively interruptible sleep — check stop_event
            # every 0.25s so a long backoff doesn't deafen Stop.
            slept = 0.0
            while slept < delay:
                if stop_event is not None and stop_event.is_set():
                    log("[remote] rsync cancelled during retry backoff.\n")
                    return subprocess.CompletedProcess(
                        cmd, returncode=130, stdout="", stderr="cancelled",
                    )
                step = min(0.25, delay - slept)
                time.sleep(step)
                slept += step
        try:
            result = _rsync_run_cancellable(
                cmd,
                timeout=timeout,
                capture_output=capture_output,
                cwd=cwd,
                stop_event=stop_event,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_exc = exc
            log(f"[remote] rsync attempt {attempt + 1} raised: {exc}\n")
            continue
        last_result = result
        last_exc = None
        if result.returncode == 0:
            return result
        if result.returncode == 130:
            # Stop fired mid-rsync — don't retry, return immediately.
            return result
        if result.returncode not in _RSYNC_RETRY_CODES:
            # Non-transient — return immediately so the caller sees the
            # real error.
            return result

    if last_result is not None:
        return last_result
    # Every attempt raised — synthesise a CompletedProcess so callers
    # have a uniform return type.
    return subprocess.CompletedProcess(
        cmd, returncode=255, stdout="", stderr=str(last_exc),
    )

def _rsync_run_cancellable(
    cmd: list[str],
    *,
    timeout: int | None,
    capture_output: bool,
    cwd: str | None,
    stop_event: threading.Event | None,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` work-alike that polls ``stop_event`` while
    the child runs and terminates it on cancellation. v5.2.28.

    When ``stop_event`` is None this is equivalent to ``subprocess.run``
    — the only path that runs without cancellation is the bootstrap
    ``sync_required_files`` call, which is small and short-lived.

    ``timeout=None`` disables the wall-clock deadline entirely: the caller
    is relying on rsync's own ``--timeout`` (I/O-idle) + ssh ``ConnectTimeout``
    to bound stalls, so a large but steadily-progressing transfer is never
    killed mid-flight (the bug that made the final per-well fetch retry-loop
    and fail on big zips / slow links)."""
    if stop_event is None:
        return subprocess.run(
            cmd, capture_output=capture_output,
            timeout=timeout, cwd=cwd, **TEXT_UTF8,
        )
    stdout_pipe = subprocess.PIPE if capture_output else None
    stderr_pipe = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(
        cmd, stdout=stdout_pipe, stderr=stderr_pipe, cwd=cwd, **TEXT_UTF8,
    )
    deadline = None if timeout is None else time.monotonic() + timeout
    rc: int | None = None
    try:
        while True:
            try:
                rc = proc.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                if stop_event.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return subprocess.CompletedProcess(
                        cmd, returncode=130, stdout="", stderr="cancelled",
                    )
                if deadline is not None and time.monotonic() > deadline:
                    proc.kill()
                    raise subprocess.TimeoutExpired(cmd, timeout)
                continue
            break
    finally:
        if proc.poll() is None:
            proc.terminate()
    out, err = ("", "")
    if capture_output and proc.stdout is not None:
        try:
            out = proc.stdout.read()
        except Exception:
            out = ""
    if capture_output and proc.stderr is not None:
        try:
            err = proc.stderr.read()
        except Exception:
            err = ""
    assert rc is not None
    return subprocess.CompletedProcess(cmd, rc, out, err)


def pull_small_file(host: "RemoteHost", remote_path: str, dest: Path, *,
                    stop_event=None, timeout: float = 60,
                    what: str = "a file") -> bool:
    """Pull one small file to *dest*, atomically, or leave *dest* as it was.

    For the bookkeeping files a run brings back beside its real output: they
    are a few hundred bytes, they are read by a *later* run rather than by this
    one, and a missing one has to be indistinguishable from one that was never
    written — so every failure here is quiet and answers ``False``.

    Staged under a dot-prefixed sibling and renamed, so a transfer cut halfway
    cannot leave a truncated file that parses as an empty record.
    """
    ok = False
    with swallow(f"preparing the destination for {what}"):
        dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    result = _rsync_with_retry(
        [resolve_rsync_binary(), *_rsync_ssh_transport(host),
         "-a", f"{host.ssh_alias}:{remote_path}", to_rsync_path(tmp)],
        log_fn=None, timeout=timeout, stop_event=stop_event,
    )
    if result.returncode == 0 and tmp.exists():
        with swallow(f"installing {what}"):
            os.replace(tmp, dest)
            ok = True
    if not ok:
        with swallow(f"cleaning up the staged copy of {what}"):
            tmp.unlink()
    return ok


def delete_remote_paths(host: "RemoteHost", paths: Iterable[str], *,
                        what: str, log_fn: Callable[[str], None]) -> None:
    """``rm -f`` *paths* (globs allowed) on *host*; *what* names them in the log.

    Non-fatal: a failure is logged and returns, because every caller has a
    backstop (the end-of-run ``verify_and_cleanup``).

    The script is piped to ``bash -s`` on stdin rather than passed as
    ``bash -c "..."`` arguments. ssh joins multi-arg remote commands with
    spaces, so a ``["bash", "-c", "rm -f a b c"]`` argv arrived at the remote
    bash as ``bash -c rm -f a b c`` — bash took ``rm`` as the ``-c`` script and
    ``-f a b c`` as positional args to it, and the bare ``rm`` failed with
    ``missing operand`` every time. The stdin pipe avoids the shell-quoting
    round-trip entirely.
    """
    script = "rm -f -- " + " ".join(paths) + "\n"
    try:
        result = subprocess.run(
            host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = (
                result.stderr.decode("utf-8", errors="replace")
                if isinstance(result.stderr, (bytes, bytearray))
                else (result.stderr or "")
            )
            log_fn(f"[remote] warn: cleanup of {what} on remote "
                   f"returned {result.returncode}: {stderr.strip()}\n")
    except (subprocess.TimeoutExpired, OSError) as exc:
        log_fn(f"[remote] warn: cleanup of {what} on remote failed: {exc}\n")
