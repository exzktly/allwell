"""Getting a run's input onto the remote host, once.

:class:`DatasetCache` keys each upload by a stat-based fingerprint of the
staged archives, so re-running the same input is a metadata round trip rather
than a re-upload; it streams rsync's output well by well, so the Upload rail and
the plate's dials fill as each archive lands, and it keeps the remote cache
inside the host's budget. The rsync streaming helpers beside it are what both
the whole-plate and the batched uploads go through.
"""

from __future__ import annotations
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from collections.abc import Callable
from services.subprocess_text import TEXT_UTF8
from services.remote_config import (
    RemoteHost,
    resolve_rsync_binary,
    to_rsync_path,
    update_bandwidth,
)
from services.remote_run_state import (
    _canonical_well_from_path,
)
from services.remote_wire import (
    TRANSIENT_PREFIX,
    _well_xfer_line,
)
from services.remote_staging import (
    ZippedInput,
)
from services.remote_transport import (
    _RSYNC_RETRY_CODES,
    _RSYNC_RETRY_DELAYS,
    _fmt_secs,
    _iter_rsync_lines,
    _looks_like_rsync_filename,
    _remote_join,
    _rsync_progress_flags,
    _rsync_ssh_transport,
    _rsync_supports_append_verify,
)
from services.remote_scratch import (
    fingerprint_archives,
)


# Ceiling on one short ssh control command (mkdir, touch, rm -rf). Generous
# enough for an ``rm -rf`` of a run directory on a slow shared filesystem, and
# far short of the hours an unbounded one can park a thread for.
SSH_CONTROL_TIMEOUT_S = 120.0


class DatasetCache:
    """Manage the persistent per-well-zip cache on the remote.

    The cache key is a stat-based hash (size + mtime per well zip), which
    is what rsync itself uses as its quick-skip heuristic — re-running on
    the same dataset is a metadata roundtrip, not a re-upload.

    A change to any well's images bumps that well's zip mtime, which
    bumps the cache key — so the next run re-uploads. That's the correct
    cache-invalidation granularity: per-well, not per-image.
    """

    def __init__(self, host: RemoteHost):
        self.host = host

    @staticmethod
    def cache_key(zipped: ZippedInput) -> str:
        """Stable hash of the (basename, size, mtime) triple for each
        well zip in ``zipped.zip_dir``. blake2b at 16 bytes is more than
        enough collision resistance for a per-user cache.

        This hashes the archives as *uploaded*, which for a rewritten upload
        means files staging created moments ago — see
        :func:`fingerprint_archives` for the one that hashes what the user
        actually has.
        """
        return fingerprint_archives(zipped.well_zips)

    def remote_dataset_dir(self, key: str) -> str:
        """Return the remote-side absolute path for a cache entry.

        Returns a shell-quoted path safe to embed in remote commands.
        """
        return f"{self.host.remote_staging_dir}/datasets/{key}"

    def ensure_uploaded(
        self,
        zipped: ZippedInput,
        *,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> tuple[str, int, float]:
        """Upload the per-well zips to ``datasets/<key>/`` on the remote.

        Returns ``(key, bytes_transferred, elapsed_s)``. On a cache hit,
        ``bytes_transferred`` will be ~0 — rsync only sends the file list.

        Cancellation: if ``stop_event`` fires mid-upload, the in-flight
        rsync is terminated and a partial state is left on the remote.
        That's fine — rsync's ``--partial`` flag means the next run
        resumes rather than restarting.
        """
        key = self.cache_key(zipped)
        remote_path = self.remote_dataset_dir(key)

        # Ensure remote target dir exists.
        self._ssh_run(["mkdir", "-p", remote_path], check=True)

        sources = [to_rsync_path(p) for p in zipped.well_zips]
        # The trailing slash on the remote dest is critical for rsync's
        # tree-merge semantics — without it rsync would create
        # `<key>/<key>/` on the second run.
        dest = f"{self.host.ssh_alias}:{remote_path}/"
        cmd = [
            resolve_rsync_binary(),
            *_rsync_ssh_transport(self.host),
            "-aH",
            "--partial",
        ]
        # v5.2.15: --append-verify needs rsync ≥ 3.0.0. macOS still
        # ships rsync 2.6.9 by default and rejects it with
        # "Unrecognized option --append-verify" → exit 1. Without the
        # flag, --partial alone still gives resume semantics — we just
        # lose the tail-checksum verification on the resumed bytes.
        if _rsync_supports_append_verify():
            cmd.append("--append-verify")
        # v5.2.10: --info= needs rsync ≥ 3.1.0. Old rsync (macOS's
        # bundled 2.6.9, some HPC clusters) rejects it with exit 1.
        # The progress callback degrades to "stats appear at end" on
        # legacy rsync, but the transfer itself still works.
        # v5.2.20: add ``name1`` so rsync emits a bare filename per
        # transferred file. ``_run_rsync_streaming`` forwards those
        # lines as ``[remote] ↑ <filename>`` so the user can see
        # which well is in flight.
        cmd += _rsync_progress_flags()
        if log_fn:
            # Log the full flag list so the user can see every gating
            # flag (--append-verify, --info=name1,...) in the log;
            # pre-cleanup we sliced ``cmd[:6]`` which truncated before
            # the --info flag on modern rsync.
            log_fn(
                f"[remote] $ {' '.join(shlex.quote(c) for c in cmd)} … → {dest} "
                f"({len(sources)} file(s))\n"
            )
        cmd += [*sources, dest]

        # Bounded retry on transient network failures (v5.1.1). The
        # ``--partial`` flag means each retry resumes where the previous
        # attempt left off, so a flaky link doesn't restart the whole
        # upload from zero. Non-transient errors (permission, bad path)
        # break out of the loop on the first attempt. Shared with
        # ``upload_subset`` via ``_stream_rsync_with_retry`` (audit A11).
        sent_bytes, elapsed, rc, attempts_run = _stream_rsync_with_retry(
            cmd, log_fn=log_fn, progress_fn=progress_fn, stop_event=stop_event,
            well_sizes=_well_zip_sizes(zipped.well_zips),
        )

        # v5.2.14: pre-v5.2.14 ``ensure_uploaded`` returned even when
        # rsync exited non-zero (e.g. rc=23 "partial transfer due to
        # error"). ``spawn()`` then started the pipeline against an
        # incomplete remote input dir — the user saw "pipeline failed"
        # without ever seeing the upstream upload failure.
        #
        # Fail loud here so the upload error is the user's first signal,
        # not a downstream "no wells found" or KeyError deep in the
        # pipeline. The retry loop above already handles transient
        # codes; reaching here with rc != 0 means a real error.
        if rc != 0:
            raise RuntimeError(
                f"rsync upload of well zips to {self.host.ssh_alias} "
                f"failed with exit code {rc} (after {attempts_run} "
                f"attempt{'s' if attempts_run != 1 else ''}). The "
                f"remote dataset at {remote_path} is incomplete. The "
                f"specific rsync error appears earlier in the log "
                f"(look for a line starting with 'rsync:' or 'rsync "
                f"error:')."
            )

        # v5.2.14: belt-and-braces verification. Some rsync failure
        # modes (notably source-side read errors on a subset of files)
        # can result in rc=0 with a partial transfer — rsync logs the
        # per-file error to stderr but still reports overall success.
        # Count zips on the remote and match against the local well
        # count before letting ``spawn`` proceed.
        #
        # v5.2.16: pipe the count script via ``_ssh_script`` (stdin to
        # remote bash) rather than ``_ssh_run`` (which would
        # ``shlex.quote`` the ``~/.allwell-staging/...`` path into
        # single quotes — killing tilde expansion on the remote and
        # always returning 0). The previous v5.2.14 implementation
        # falsely reported "expected 2 found 0" for runs whose files
        # had actually transferred fine.
        expected = len(zipped.well_zips)
        listing = self._ssh_script(
            f"ls -1 {remote_path}/*.zip 2>/dev/null | wc -l",
            check=False,
        )
        try:
            actual = int((listing.stdout or "0").strip())
        except (TypeError, ValueError):
            actual = -1
        if actual != expected:
            raise RuntimeError(
                f"Upload verification failed: expected {expected} well "
                f"zip(s) at {self.host.ssh_alias}:{remote_path}/, found "
                f"{actual}. rsync reported success but some files did "
                f"not reach the remote. Re-run to retry."
            )

        # Refresh the bandwidth estimate when we actually moved bytes —
        # cache hits would skew the average toward infinity.
        if sent_bytes > 1_000_000:  # >1 MB → meaningful sample
            mbps = (sent_bytes * 8) / elapsed / 1_000_000
            update_bandwidth(self.host.name, mbps)

        mb = sent_bytes / (1024**2)
        cache_hit = mb < 1
        if log_fn:
            _log_wells_uploaded(zipped.well_zips, log_fn)
            log_fn(
                f"[remote] upload: {mb:.1f} MB in {_fmt_secs(elapsed)}"
                f"{' (cache hit)' if cache_hit else ''}\n"
            )
        return key, sent_bytes, elapsed

    def upload_subset(
        self,
        well_zips: list[Path],
        remote_path: str,
        *,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> tuple[int, float, int]:
        """Upload a chosen subset of zips to ``remote_path``.

        Shares all the safety knobs of :meth:`ensure_uploaded` (the
        ``--info`` gating, the bounded retry on transient codes, the
        rsync transport policy from :func:`_rsync_ssh_transport`)
        but skips the cache-key bookkeeping — used by batched mode
        where ``remote_path`` is a single shared input dir that
        sees a rolling window of well-zips while the per-well
        Fetcher cleanup drains it.

        Returns ``(bytes_transferred, elapsed_s, returncode)``.
        """
        if not well_zips:
            return 0, 0.0, 0
        sources = [to_rsync_path(p) for p in well_zips]
        dest = f"{self.host.ssh_alias}:{remote_path}/"
        cmd = [
            resolve_rsync_binary(),
            *_rsync_ssh_transport(self.host),
            "-aH",
            "--partial",
        ]
        if _rsync_supports_append_verify():
            cmd.append("--append-verify")
        cmd += _rsync_progress_flags()
        if log_fn:
            log_fn(
                f"[remote] $ {' '.join(shlex.quote(c) for c in cmd)} … → {dest} "
                f"({len(sources)} file(s))\n"
            )
        cmd += [*sources, dest]
        # Shared streaming retry loop (audit A11).
        sent_bytes, elapsed, rc, _attempts = _stream_rsync_with_retry(
            cmd, log_fn=log_fn, progress_fn=progress_fn, stop_event=stop_event,
            well_sizes=_well_zip_sizes(well_zips),
        )
        if rc == 0 and log_fn:
            _log_wells_uploaded(well_zips, log_fn)
        return sent_bytes, elapsed, rc

    def gc(
        self, max_bytes: int, *, log_fn: Callable[[str], None] | None = None
    ) -> None:
        """LRU-evict old dataset caches until the staging dir is under
        ``max_bytes``.

        v5.2.11: gated on ``host.auto_gc``. Defaults to OFF so a
        shared-machine remote never has files deleted without the
        user opting in. Power users set ``auto_gc = true`` in
        remotes.toml; the field is documented on ``RemoteHost``.
        """
        if not self.host.auto_gc:
            if log_fn:
                log_fn(
                    "[remote] gc: skipped (auto_gc disabled). "
                    "Set auto_gc=true in remotes.toml to enable LRU "
                    "eviction of old dataset caches.\n"
                )
            return
        # Send the script via stdin so the remote bash parses
        # ~/.allwell-staging normally. Embedding it in a one-line ssh
        # arg always loses tilde expansion via outer shell quoting.
        listing = self._ssh_script(
            f"cd {self.host.remote_staging_dir}/datasets 2>/dev/null && "
            "du -sb -- */ 2>/dev/null | sort -nr || true",
            check=False,
        )
        if not listing or not listing.stdout:
            return
        entries: list[tuple[int, str]] = []
        for line in listing.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                entries.append((int(parts[0]), parts[1].rstrip("/")))
        total = sum(b for b, _ in entries)
        if total <= max_bytes:
            return
        # Sort by access time ascending (oldest first). We don't have an
        # easy LRU stamp without a stat round-trip, so approximate via the
        # current sort: largest-first is wrong, so refetch with atime.
        atime_cmd = self._ssh_script(
            f"cd {self.host.remote_staging_dir}/datasets 2>/dev/null && "
            "stat -c '%X %n' -- */ 2>/dev/null | sort -n || true",
            check=False,
        )
        if not atime_cmd or not atime_cmd.stdout:
            return
        ordered: list[str] = []
        for line in atime_cmd.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                ordered.append(parts[1].rstrip("/"))
        size_by_name = {n: b for b, n in entries}
        for name in ordered:
            if total <= max_bytes:
                break
            b = size_by_name.get(name, 0)
            self._ssh_run(
                ["rm", "-rf", f"{self.host.remote_staging_dir}/datasets/{name}"],
                check=False,
            )
            total -= b
            if log_fn:
                log_fn(f"[remote] gc: evicted dataset {name} ({b // (1024**2)} MB)\n")

    def _ssh_run(
        self, remote_argv: list[str], *, check: bool,
        timeout_s: float = SSH_CONTROL_TIMEOUT_S,
    ) -> subprocess.CompletedProcess[str] | None:
        """Run one short control command over ssh.

        Every caller is a ``mkdir`` / ``touch`` / ``rm -rf`` — seconds of work —
        so the call is bounded. Unbounded, a dead network with no keepalive
        parks the calling thread for ever: the batch loop stops on its
        ``touch <ready>`` and never reaches the wait that would notice the run
        is over, and the heartbeat thread stops beating while believing it is
        still beating.

        A timeout is reported as return code 124 (``timeout``'s own
        convention) so a ``check=False`` caller reading ``returncode`` sees a
        failure rather than a success it never had.
        """
        # v5.2.11: use the tilde-aware joiner so ``mkdir -p ~/foo`` reaches
        # the remote shell as ``mkdir -p "$HOME"/foo`` rather than as
        # ``mkdir -p '~/foo'`` (which creates a literal ``~`` directory).
        cmd = self.host.ssh_argv(allocate_tty=False) + [
            _remote_join(remote_argv),
        ]
        try:
            return subprocess.run(
                cmd, capture_output=True, check=check,
                timeout=timeout_s, **TEXT_UTF8,
            )
        except subprocess.CalledProcessError as exc:
            if check:
                raise
            return exc  # type: ignore[return-value]
        except subprocess.TimeoutExpired as exc:
            if check:
                raise RuntimeError(
                    f"the remote did not answer within {timeout_s:g}s: "
                    f"{' '.join(str(a) for a in remote_argv[:3])}"
                ) from exc
            return subprocess.CompletedProcess(cmd, 124, "", "")

    def _ssh_script(
        self, script: str, *, check: bool,
    ) -> subprocess.CompletedProcess[str] | None:
        """Pipe ``script`` to the remote bash via stdin.

        For multi-line / embedded-glob scripts, this is safer than
        building a one-line command via ``_ssh_run`` + ``_remote_join``:
        bash reads the script from stdin and parses it normally, so
        any ``~`` inside expands without us having to know about it.
        The price is one extra round-trip (no, the same number — ssh
        executes immediately) and the requirement that ``bash`` be on
        PATH on the remote (it is, for every host our framework supports).
        """
        cmd = self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"]
        # v5.x.x: pipe the script as bytes, not as a text-mode string.
        # On Windows, text=True translates ``\n`` → ``\r\n`` when
        # writing to ssh's stdin pipe; the remote bash then sees ``\r``
        # at the end of every line and bails with
        # ``bash: line 1: $'\r': command not found``. Decode the
        # captured streams on the way back so the CompletedProcess
        # ``.stdout`` / ``.stderr`` remain ``str``.
        try:
            raw = subprocess.run(
                cmd, input=script.encode("utf-8"),
                capture_output=True, check=check,
            )
        except subprocess.CalledProcessError as exc:
            if isinstance(exc.stdout, (bytes, bytearray)):
                exc.stdout = exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, (bytes, bytearray)):
                exc.stderr = exc.stderr.decode("utf-8", errors="replace")
            if check:
                raise
            return exc  # type: ignore[return-value]
        return subprocess.CompletedProcess(
            raw.args, raw.returncode,
            raw.stdout.decode("utf-8", errors="replace") if raw.stdout else "",
            raw.stderr.decode("utf-8", errors="replace") if raw.stderr else "",
        )


def _well_zip_sizes(well_zips) -> dict[str, int]:
    """``{archive basename: bytes}`` for the well zips about to be uploaded.

    The denominator for per-well upload progress. An archive that has gone
    missing between staging and upload is simply left out — the transfer will
    fail on its own and say so; guessing a size here would put a dial on a
    well that is not moving.
    """
    sizes: dict[str, int] = {}
    for p in well_zips or ():
        path = Path(p)
        try:
            sizes[path.name] = path.stat().st_size
        except OSError:
            continue
    return sizes


class _UploadWellProgress:
    """Turn one rsync's ``--info=name1,progress2`` stream into per-well upload
    fractions.

    ``progress2`` reports bytes transferred for the *whole* invocation, not per
    file, so a percentage on its own says nothing about any individual well.
    ``name1`` fills the gap: rsync prints a file's name as it starts sending
    it, so the cumulative byte count at that moment is that well's zero point,
    and everything after it until the next name belongs to that well. With the
    local zip's own size as the denominator that gives a genuine per-well
    fraction rather than a plate-wide one smeared across every well.

    Files rsync skips — a cache hit, an incremental re-send — emit no name line
    and contribute no bytes, so they never open a window at all. That is the
    right answer: nothing about them is in flight.
    """

    def __init__(self, sizes: dict[str, int], emit: Callable[[str], None]) -> None:
        # {basename: size_in_bytes} for the archives this rsync was asked for.
        self._sizes = sizes
        self._emit = emit
        self._well: str | None = None
        self._size = 0
        self._base_bytes = 0
        self._cumulative = 0
        self._last_sent = -1.0

    def note_bytes(self, cumulative: int) -> None:
        """Record rsync's running total, and emit the open well's share of it."""
        self._cumulative = cumulative
        if self._well is None or self._size <= 0:
            return
        frac = (cumulative - self._base_bytes) / self._size
        # Rounded before the compare so a byte-level dribble doesn't emit a
        # line per progress update; the dial cannot show finer than this.
        frac = round(max(0.0, min(1.0, frac)), 3)
        if frac != self._last_sent:
            self._last_sent = frac
            self._emit(_well_xfer_line(self._well, "up", frac))

    def note_file(self, name: str) -> None:
        """A new file just started transferring: close the previous well's
        window at full and open this one at zero."""
        self.finish()
        base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        size = self._sizes.get(base)
        if size is None:
            return
        well = _canonical_well_from_path(Path(base).stem)
        if well is None:
            return
        self._well, self._size = well, int(size)
        self._base_bytes = self._cumulative
        self._last_sent = 0.0
        self._emit(_well_xfer_line(well, "up", 0.0))

    def finish(self) -> None:
        """Close the open well's window at full — it is on the remote now."""
        if self._well is not None:
            self._emit(_well_xfer_line(self._well, "up", 1.0))
            self._well = None
            self._size = 0
            self._last_sent = -1.0


def _run_rsync_streaming(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None,
    progress_fn: Callable[[float], None] | None,
    stop_event: threading.Event | None,
    well_sizes: dict[str, int] | None = None,
) -> tuple[int, int]:
    """Run rsync, stream its stdout, return ``(bytes_sent, returncode)``.

    Honors the stop_event by terminating the rsync subprocess. Parses the
    ``--info=stats2`` summary line at the end for the byte count.

    ``well_sizes`` maps ``{archive basename: bytes}`` for the well zips this
    invocation is uploading. When supplied (and ``log_fn`` is set) the stream
    also yields per-well upload fractions — see :class:`_UploadWellProgress`.

    v5.1.1: return value gained the exit code so the caller can drive a
    bounded retry on transient network failures (rsync codes 10/12/30/35).

    v5.4.5: binary stdout + manual ``[\\r\\n]+`` splitting via
    ``_iter_rsync_lines`` so ``--info=progress2`` updates (which
    terminate with ``\\r``) reach the log promptly. Without this, the
    GUI shows up to ten minutes of silence between the
    ``[remote] $ rsync …`` line and the first per-file ↑ marker
    during the upload of a large well zip.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Binary mode + small ``read`` chunks so we can split on
        # ``\r`` alongside ``\n``. ``text=True`` + ``for line in
        # stdout`` only saw ``\n`` and missed every progress update.
        bufsize=0,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    sent_bytes = 0
    progress_re = re.compile(r"(\d[\d,]*)\s+(\d+)%\s+(\S+)\s+\S+(?:\s+\(.*\))?")
    bytes_sent_re = re.compile(r"^[Tt]otal transferred file size:\s+([\d,]+)\s+bytes")
    bytes_actual_re = re.compile(r"^sent\s+([\d,]+)\s+bytes")
    # v5.2.20: when --info=name1 is on, rsync emits a bare filename
    # per transferred file. Detect them by elimination — they're
    # non-empty lines that aren't a progress line, a stats footer,
    # an rsync error line, or one of the well-known directory or
    # incremental-listing markers. Captures both the well-zip names
    # (e.g. "A01.zip") and the required-files names (e.g.
    # "process_microscopy.py", "requirements-remote.txt").
    file_count = 0
    # Rate-limit the progress-bar surfacing — rsync can emit many
    # progress updates per second, and the GUI doesn't need
    # sub-second resolution. Emit at most every 2 seconds, plus the
    # final 100% line.
    last_progress_log = 0.0
    PROGRESS_LOG_MIN_INTERVAL = 1.0
    wells = (
        _UploadWellProgress(well_sizes, log_fn)
        if (well_sizes and log_fn is not None) else None
    )

    try:
        assert proc.stdout is not None
        for line in _iter_rsync_lines(proc.stdout):
            if stop_event and stop_event.is_set():
                proc.terminate()
                break
            # Progress lines (--info=progress2) print to a TTY-ish format
            # with carriage returns. We now read \r-delimited and can
            # surface them as percentage updates.
            m = progress_re.search(line)
            is_progress = m is not None
            if is_progress and progress_fn:
                try:
                    pct = float(m.group(2)) / 100.0
                    progress_fn(pct)
                except ValueError:
                    pass
            if is_progress and wells is not None:
                try:
                    wells.note_bytes(int(m.group(1).replace(",", "")))
                except ValueError:
                    pass
            m_bytes = bytes_actual_re.match(line)
            if m_bytes:
                sent_bytes = int(m_bytes.group(1).replace(",", ""))
            elif sent_bytes == 0:
                m_bytes = bytes_sent_re.match(line)
                if m_bytes:
                    sent_bytes = int(m_bytes.group(1).replace(",", ""))
            if log_fn:
                if line.startswith(("sent ", "Total ", "rsync:", "rsync error")):
                    log_fn(f"[remote] {line}\n")
                elif is_progress:
                    # rsync's own percentage stream is live state, not events:
                    # marked transient so it drives the status line and the
                    # transfer bar without writing to the log at all. Still
                    # rate-limited, since the status line is repainted per
                    # update and a fast link emits these continuously.
                    now = time.monotonic()
                    if now - last_progress_log >= PROGRESS_LOG_MIN_INTERVAL:
                        log_fn(f"{TRANSIENT_PREFIX}{line}\n")
                        last_progress_log = now
                elif _looks_like_rsync_filename(line):
                    file_count += 1
                    if wells is not None:
                        wells.note_file(line)
                    log_fn(f"[remote] ↑ ({file_count}) {line}\n")
        if wells is not None:
            wells.finish()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return sent_bytes, proc.returncode if proc.returncode is not None else -1


def _log_wells_uploaded(well_zips, log_fn: Callable[[str], None]) -> None:
    """Say which wells are now on the remote, once their transfer succeeded.

    The dashboard's Upload rail counts wells off ``[remote] ↑ …<well>.zip``
    lines, and those come from rsync's own per-file output — which is a poor
    thing to depend on, because rsync stays silent in three ordinary cases:

    * **a cache hit**, where it moves nothing because the inputs are already
      there;
    * **an incremental re-send**, where it skips the files that match;
    * **an rsync without ``--info=name1``** — macOS still ships 2.6.9, which
      does not have the flag at all (see ``_rsync_supports_info_flag``).

    In every one of those the transfer succeeded and the rail sat at 0/N, which
    reads as a stalled upload. Batched mode hit it hardest: ``upload_subset``
    had no compensator of any kind, so a batched run's rail never moved off
    zero on any of the three.

    Emitted from the success path instead, where the caller knows exactly which
    archives it asked for and that rsync returned 0. The dashboard de-dupes
    into sets, so this is harmless alongside live per-file lines.
    """
    for wz in well_zips:
        log_fn(f"[remote] ↑ {Path(wz).stem}.zip on remote\n")


def _stream_rsync_with_retry(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None = None,
    progress_fn=None,
    stop_event: threading.Event | None = None,
    well_sizes: dict[str, int] | None = None,
) -> tuple[int, float, int, int]:
    """Run a *streaming* rsync (:func:`_run_rsync_streaming`) with bounded retry
    on transient network failures, resuming via ``--partial`` each attempt.

    The progress-streaming twin of :func:`_rsync_with_retry` (which *captures*
    output for small control-plane rsyncs). Consolidates the two byte-identical
    retry loops the ``ensure_uploaded`` + ``upload_subset`` transfer paths
    hand-rolled (audit A11). Non-transient rsync errors break out on the first
    attempt; a set ``stop_event`` breaks at the top of the next iteration.

    Returns ``(sent_bytes, elapsed_s, rc, attempts_run)`` — ``sent_bytes``
    accumulates the incremental bytes each resumed attempt sent.
    """
    t0 = time.monotonic()
    sent_bytes = 0
    rc = -1
    attempts_run = 0  # v5.2.15: actual count, for the error message
    for attempt, delay in enumerate((0.0,) + _RSYNC_RETRY_DELAYS):
        if stop_event is not None and stop_event.is_set():
            break
        if delay > 0:
            if log_fn:
                log_fn(
                    f"[remote] rsync retry {attempt}/{len(_RSYNC_RETRY_DELAYS)} "
                    f"in {delay:.0f}s (last exit code {rc})\n"
                )
            time.sleep(delay)
        attempt_bytes, rc = _run_rsync_streaming(
            cmd, log_fn=log_fn, progress_fn=progress_fn, stop_event=stop_event,
            well_sizes=well_sizes,
        )
        sent_bytes += attempt_bytes  # each resumed attempt sends incremental bytes
        attempts_run += 1
        if rc == 0 or rc not in _RSYNC_RETRY_CODES:
            break
    elapsed = max(time.monotonic() - t0, 1e-6)
    return sent_bytes, elapsed, rc, attempts_run
