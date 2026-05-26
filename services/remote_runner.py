"""Remote-execution runner for ``process_microscopy.py``.

Stages per-well zips to an SSH host, launches the pipeline remotely, and
streams outputs back as wells complete. Designed as a drop-in replacement
for ``spawn_pipeline()`` from the GUI's point of view: the Popen handle
returned by :meth:`RemoteRunner.spawn` looks identical (stdout pipe, pgid
kill works, return code semantics preserved) so ``PipelineRunner`` and
``ProgressTracker`` need only a single-line branch to opt in.

The five transfer-minimizing levers from the v5.0.0 plan are implemented
here:

* **Per-well zip granularity** — :class:`LocalZipper` runs the existing
  ``WellPlateZipper`` locally first, so a 1500-image plate ships as
  ~100 large zips instead of 1500 small TIFs.
* **Persistent dataset cache** — :class:`DatasetCache` keys uploads by a
  stat-based content hash so re-runs of the same input are a metadata
  roundtrip, not a re-upload.
* **CSV-by-default output filter** — :class:`OutputFilter` decides which
  files to materialize locally and toggles ``--no_save_tophat`` on the
  remote so QC TIFs aren't even produced when not wanted.
* **Pipelined per-well execution** — :class:`Fetcher` watches the
  pipeline's stdout for the existing ``— done.`` marker and starts
  pulling each well's output the moment it lands, overlapping
  download with later-well compute.
* **Verified cleanup** — :meth:`RemoteRunner.verify_and_cleanup` only
  fires the remote ``rm -rf`` after the local output manifest is
  confirmed complete; a missing local file aborts the cleanup so the
  user can retry without losing remote state.
"""

from __future__ import annotations

import hashlib
import io
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional

from services.remote_config import (
    RemoteHost,
    resolve_rsync_binary,
    resolve_ssh_binary,
    to_rsync_path,
    update_bandwidth,
)

# Markers the pipeline's stdout emits; reused here so the Fetcher can
# trigger on the same lines the GUI's ProgressTracker already parses.
_RE_WELL_DONE = re.compile(r"^(?P<well>[A-Ha-h]\d{2})\s+—\s+done\.")
_RE_MODE_WELLS = re.compile(r"(?:Zip mode|Flat mode|Folder mode):\s+(\d+)\s+well")

# Default output-filter glob set: just CSVs and the top-level metadata
# sidecar. The remote also gets --no_save_tophat under this filter so QC
# files aren't even produced.
_CSV_ONLY_GLOBS: tuple[str, ...] = ("*.csv", "pipeline_info.json")


# Files this framework needs on the remote in order to run the pipeline.
# Kept minimal on purpose: process_microscopy.py plus its two local-module
# imports, plus the requirements list (so the bootstrap script knows what
# pip-installs to do). Paths are relative to the repo root.
#
# Synced before every run by :meth:`RemoteRunner.spawn` so a user who
# edits any of these files locally doesn't end up running stale code on
# the remote — the per-run cost is a few hundred KB and a quick rsync
# stat-pass when nothing changed.
_REQUIRED_REMOTE_FILES: tuple[str, ...] = (
    "process_microscopy.py",
    "auto_threshold_core.py",
    "well_token.py",
    # v5.2.1: headless-only requirements (no PySide6/pyobjc/pyinstaller
    # — the remote doesn't run the GUI). See _Docs/requirements.txt for
    # the GUI superset.
    "_Docs/requirements-remote.txt",
)


def repo_root() -> Path:
    """Absolute path to the repo root from which required-file paths
    are resolved. Single source of truth used by the runner and the
    bootstrap script's Python helpers."""
    return Path(__file__).resolve().parent.parent


class OutputFilter(str, Enum):
    """What to bring back from the remote when a run completes."""

    FULL = "full"
    """Full equivalence — every file a local run would have produced."""

    CSV_ONLY = "csv-only"
    """Just the per-well CSVs and ``pipeline_info.json``. Also injects
    ``--no_save_tophat`` so the remote skips QC generation entirely."""


class LocalInputMode(str, Enum):
    """How the user's input would have been processed locally.

    Recorded up-front so the Fetcher can reconstruct an output tree that's
    indistinguishable from a local run.
    """

    ZIP = "zip"
    """Input was already ``{well}.zip`` files. Local zip-mode run would
    produce ``{well}_out.zip`` files in ``output_dir`` — Fetcher drops
    them in unchanged."""

    FOLDER = "folder"
    """Input was ``{well}/`` subfolders. Local run extracts the work into
    loose files in ``output_dir`` — Fetcher unzips each remote
    ``{well}_out.zip`` after fetch."""

    FLAT = "flat"
    """Input was loose TIFs in the root. Local run would compress them
    into per-well zips first (since ``compress_input_well_folders``
    defaults to True), then proceed identically to zip mode. Treated
    like ZIP from the Fetcher's perspective."""


@dataclass
class RunEstimate:
    """Bandwidth-aware break-even estimate. Returned by
    :meth:`RemoteRunner.estimate` before each run so the user can see
    whether going remote is actually worth it on this network."""

    bytes_to_transfer: int
    transfer_s: float
    remote_compute_s: float
    local_compute_s: float

    @property
    def will_win(self) -> bool:
        return self.local_compute_s > self.transfer_s + self.remote_compute_s

    def format(self) -> str:
        gb = self.bytes_to_transfer / (1024**3)
        return (
            f"transfer {gb:.2f} GB ({_fmt_secs(self.transfer_s)}) + "
            f"remote compute {_fmt_secs(self.remote_compute_s)} = "
            f"{_fmt_secs(self.transfer_s + self.remote_compute_s)}  "
            f"vs local {_fmt_secs(self.local_compute_s)} — "
            f"{'remote wins' if self.will_win else 'LOCAL would be faster'}"
        )


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"


# ────────────────────────────────────────────────────────────────────────────
# LocalZipper
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class ZippedInput:
    """The output of :class:`LocalZipper`: a directory of ``{well}.zip``
    files plus the local input mode the user would have triggered."""

    zip_dir: Path
    mode: LocalInputMode
    well_zips: tuple[Path, ...]
    """Sorted ``{well}.zip`` paths inside ``zip_dir``."""

    cleanup: Callable[[], None] | None = None
    """Optional callback that removes any temporary staging directory the
    zipper created. ``None`` when the input was already in zip form."""


def stage_local_zips(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    fluor_tokens: Iterable[str] | None = None,
    nuclear_token: str | None = None,
    log_fn: Callable[[str], None] | None = None,
    proc_hook: Callable[[subprocess.Popen | None], None] | None = None,
) -> ZippedInput:
    """Return a directory of per-well zips for ``input_dir``.

    For inputs that are already ``{well}.zip``, this is a no-op pointer.
    For loose-TIF or folder layouts, runs the existing
    ``WellPlateZipper`` (re-used via :mod:`services.input_resolution_service`)
    into a temp directory and returns that.

    Phase-3 selective staging: when ``fluor_tokens`` is restricted, the
    selective filter rewrites each well's zip to keep only the requested
    channels — cutting upload by the unused-channel fraction. The nuclear
    token is always kept (segmentation needs it).
    """
    from services.input_resolution_service import run_zipper

    input_dir = input_dir.resolve()
    mode = _detect_local_input_mode(input_dir)

    if mode is LocalInputMode.ZIP:
        well_zips = tuple(sorted(input_dir.glob("[A-H][0-9]*.zip")))
        if not well_zips:
            raise RuntimeError(
                f"Input dir reports zip-mode but contains no A01.zip-style files: {input_dir}"
            )
        if fluor_tokens is not None:
            return _filter_channels_in_place(
                input_dir, well_zips, mode,
                keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
                log_fn=log_fn,
            )
        return ZippedInput(zip_dir=input_dir, mode=mode, well_zips=well_zips, cleanup=None)

    # Folder or flat mode: shell out to WellPlateZipper. The zipper writes
    # `{well}.zip` files into the staging dir.
    staging = Path(tempfile.mkdtemp(prefix="allwell-zips-"))
    try:
        run_zipper(
            input_dir,
            staging,
            log_fn=log_fn,
            progress_fn=None,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    well_zips = tuple(sorted(staging.glob("[A-H][0-9]*.zip")))
    if not well_zips:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(
            "WellPlateZipper produced no per-well zips; check filename_schema and separator."
        )

    if fluor_tokens is not None:
        zin = _filter_channels_in_place(
            staging, well_zips, mode,
            keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
            log_fn=log_fn,
        )
        zin.cleanup = lambda d=staging: shutil.rmtree(d, ignore_errors=True)
        return zin

    return ZippedInput(
        zip_dir=staging,
        mode=mode,
        well_zips=well_zips,
        cleanup=lambda d=staging: shutil.rmtree(d, ignore_errors=True),
    )


def _detect_local_input_mode(input_dir: Path) -> LocalInputMode:
    """Mirror the discovery logic in process_microscopy's main()."""
    if any(input_dir.glob("[A-H][0-9]*.zip")):
        return LocalInputMode.ZIP
    if any(
        p.is_dir() and re.fullmatch(r"[A-H]\d{1,2}", p.name)
        for p in input_dir.iterdir()
    ):
        return LocalInputMode.FOLDER
    return LocalInputMode.FLAT


def _required_tokens(
    nuclear_token: str | None, fluor_tokens: Iterable[str]
) -> set[str]:
    """Keep the nuclear channel + every requested fluor channel."""
    keep = {t.strip() for t in fluor_tokens if t and t.strip()}
    if nuclear_token:
        keep.add(nuclear_token.strip())
    return keep


def _filter_channels_in_place(
    zip_dir: Path,
    well_zips: tuple[Path, ...],
    mode: LocalInputMode,
    *,
    keep_tokens: set[str],
    log_fn: Callable[[str], None] | None,
) -> ZippedInput:
    """Rewrite each well zip to drop members whose name doesn't contain one
    of ``keep_tokens``. Skipped silently if every member is kept.

    Phase-3 selective staging: this is the lever-1 multiplier — for a
    plate with 4 channels but only 2 in use, this cuts upload in half.
    Idempotent: a zip whose members are already filtered is a no-op.
    """
    if not keep_tokens:
        return ZippedInput(zip_dir=zip_dir, mode=mode, well_zips=well_zips, cleanup=None)

    total_in = 0
    total_kept = 0
    for wz in well_zips:
        try:
            with zipfile.ZipFile(wz, "r") as zf:
                members = zf.namelist()
            keep_mask = [_member_matches(m, keep_tokens) for m in members]
        except (OSError, zipfile.BadZipFile):
            continue
        total_in += len(members)
        total_kept += sum(keep_mask)
        if all(keep_mask):
            continue
        # Re-write the zip with only the kept members.
        tmp = wz.with_suffix(".zip.tmp")
        with zipfile.ZipFile(wz, "r") as src, zipfile.ZipFile(
            tmp, "w", compression=zipfile.ZIP_DEFLATED
        ) as dst:
            for member, keep in zip(members, keep_mask):
                if keep:
                    dst.writestr(src.getinfo(member), src.read(member))
        tmp.replace(wz)

    if log_fn and total_in:
        dropped = total_in - total_kept
        if dropped:
            log_fn(
                f"[remote] selective staging: kept {total_kept}/{total_in} "
                f"channel images (dropped {dropped} unused).\n"
            )
    return ZippedInput(zip_dir=zip_dir, mode=mode, well_zips=well_zips, cleanup=None)


def _member_matches(member: str, keep_tokens: set[str]) -> bool:
    """A zip member is kept if any keep_token is a substring of its
    basename (matches the same loose-token discipline process_microscopy
    uses when grouping channels)."""
    base = Path(member).name
    return any(tok in base for tok in keep_tokens)


# ────────────────────────────────────────────────────────────────────────────
# DatasetCache
# ────────────────────────────────────────────────────────────────────────────


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

    This helper centralises the policy so every call site (there are
    seven as of v5.2.5: cache upload, file sync, per-well fetch, glob
    pull, optional pull, two end-of-run retries) gets the same options.
    """
    ssh_inner = [
        resolve_ssh_binary(),
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
        """
        h = hashlib.blake2b(digest_size=16)
        for wz in zipped.well_zips:
            stat = wz.stat()
            h.update(wz.name.encode("utf-8"))
            h.update(b"\0")
            h.update(str(stat.st_size).encode("ascii"))
            h.update(b"\0")
            h.update(str(int(stat.st_mtime)).encode("ascii"))
            h.update(b"\0")
        return h.hexdigest()

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
            "--append-verify",
            "--info=stats2,progress2",
            *sources,
            dest,
        ]
        if log_fn:
            log_fn(f"[remote] $ {' '.join(shlex.quote(c) for c in cmd[:6])} … → {dest}\n")

        # Bounded retry on transient network failures (v5.1.1). The
        # ``--partial`` flag means each retry resumes where the previous
        # attempt left off, so a flaky link doesn't restart the whole
        # upload from zero. Non-transient errors (permission, bad path)
        # break out of the loop on the first attempt.
        t0 = time.monotonic()
        sent_bytes = 0
        rc = -1
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
            )
            sent_bytes += attempt_bytes  # each resumed attempt sends incremental bytes
            if rc == 0 or rc not in _RSYNC_RETRY_CODES:
                break
        elapsed = max(time.monotonic() - t0, 1e-6)

        # Refresh the bandwidth estimate when we actually moved bytes —
        # cache hits would skew the average toward infinity.
        if sent_bytes > 1_000_000:  # >1 MB → meaningful sample
            mbps = (sent_bytes * 8) / elapsed / 1_000_000
            update_bandwidth(self.host.name, mbps)

        if log_fn:
            mb = sent_bytes / (1024**2)
            log_fn(
                f"[remote] upload: {mb:.1f} MB in {_fmt_secs(elapsed)}"
                f"{' (cache hit)' if mb < 1 else ''}\n"
            )
        return key, sent_bytes, elapsed

    def gc(
        self, max_bytes: int, *, log_fn: Callable[[str], None] | None = None
    ) -> None:
        """LRU-evict old dataset caches until the staging dir is under
        ``max_bytes``. Run on every connect, independent of any
        ``--cleanup-remote`` flag — this is budget enforcement.
        """
        listing = self._ssh_run(
            [
                "sh", "-c",
                f"cd {shlex.quote(self.host.remote_staging_dir)}/datasets 2>/dev/null && "
                "du -sb -- */ 2>/dev/null | sort -nr || true",
            ],
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
        atime_cmd = self._ssh_run(
            [
                "sh", "-c",
                f"cd {shlex.quote(self.host.remote_staging_dir)}/datasets 2>/dev/null && "
                "stat -c '%X %n' -- */ 2>/dev/null | sort -n || true",
            ],
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
        self, remote_argv: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str] | None:
        cmd = self.host.ssh_argv(allocate_tty=False) + [
            shlex.join(remote_argv),
        ]
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, check=check
            )
        except subprocess.CalledProcessError as exc:
            if check:
                raise
            return exc  # type: ignore[return-value]


def _run_rsync_streaming(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None,
    progress_fn: Callable[[float], None] | None,
    stop_event: threading.Event | None,
) -> tuple[int, int]:
    """Run rsync, stream its stdout, return ``(bytes_sent, returncode)``.

    Honors the stop_event by terminating the rsync subprocess. Parses the
    ``--info=stats2`` summary line at the end for the byte count.

    v5.1.1: return value gained the exit code so the caller can drive a
    bounded retry on transient network failures (rsync codes 10/12/30/35).
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    sent_bytes = 0
    progress_re = re.compile(r"(\d[\d,]*)\s+(\d+)%\s+(\S+)\s+\S+(?:\s+\(.*\))?")
    bytes_sent_re = re.compile(r"^[Tt]otal transferred file size:\s+([\d,]+)\s+bytes")
    bytes_actual_re = re.compile(r"^sent\s+([\d,]+)\s+bytes")

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if stop_event and stop_event.is_set():
                proc.terminate()
                break
            line = line.rstrip("\n")
            if not line:
                continue
            # Progress lines (--info=progress2) print to a TTY-ish format
            # with carriage returns. When piped, rsync still emits them on
            # newlines; we just extract the percentage if present.
            m = progress_re.search(line)
            if m and progress_fn:
                try:
                    pct = float(m.group(2)) / 100.0
                    progress_fn(pct)
                except ValueError:
                    pass
            m = bytes_actual_re.match(line)
            if m:
                sent_bytes = int(m.group(1).replace(",", ""))
            elif sent_bytes == 0:
                m = bytes_sent_re.match(line)
                if m:
                    sent_bytes = int(m.group(1).replace(",", ""))
            if log_fn and line.startswith(("sent ", "Total ", "rsync:", "rsync error")):
                log_fn(f"[remote] {line}\n")
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return sent_bytes, proc.returncode if proc.returncode is not None else -1


# Exit codes rsync uses to signal a network-side failure (per rsync(1)
# manpage, "EXIT VALUES" section). Anything outside this set is treated
# as a configuration / permission / logic error and surfaces immediately
# without retry — re-running such an error wouldn't help and would mask
# the real problem.
#
#   10: error in socket I/O
#   12: error in rsync protocol data stream
#   30: timeout in data send/receive
#   35: timeout waiting for daemon connection
_RSYNC_RETRY_CODES = {10, 12, 30, 35}

# Backoff schedule for rsync retries — mirrors the git push/pull policy
# documented at the top of the runner's invocation env. Total wait
# across the three retries is 14 s, an upper bound the user can sit
# through without thinking the run is hung.
_RSYNC_RETRY_DELAYS = (2.0, 4.0, 8.0)


def _rsync_with_retry(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None = None,
    timeout: int = 300,
    capture_output: bool = True,
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
    """
    log = log_fn or (lambda _m: None)
    delays = (0.0,) + _RSYNC_RETRY_DELAYS  # delay before each attempt
    last_result: subprocess.CompletedProcess | None = None
    last_exc: BaseException | None = None

    for attempt, delay in enumerate(delays):
        if delay > 0:
            log(
                f"[remote] rsync retry {attempt}/{len(_RSYNC_RETRY_DELAYS)} "
                f"in {delay:.0f}s (last exit code "
                f"{last_result.returncode if last_result else '?'})\n"
            )
            time.sleep(delay)
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_exc = exc
            log(f"[remote] rsync attempt {attempt + 1} raised: {exc}\n")
            continue
        last_result = result
        last_exc = None
        if result.returncode == 0:
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


# ────────────────────────────────────────────────────────────────────────────
# RemoteRunner
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class _RemoteRun:
    """Internal handle: everything one in-flight run needs to track."""

    run_id: str
    cache_key: str
    remote_input_dir: str
    remote_output_dir: str
    remote_pidfile: str
    local_output_dir: Path
    local_input_mode: LocalInputMode
    expected_wells: set[str] = field(default_factory=set)
    fetched_wells: set[str] = field(default_factory=set)
    well_count_total: int = 0


class _RemoteProcess:
    """Popen-compatible wrapper around the ssh client.

    Exposes the same surface (``stdout``, ``poll()``, ``wait()``, ``pid``,
    ``returncode``) so the existing ``_run_pipeline_subprocess`` reader
    loop in ``services/pipeline_runner.py`` works unchanged.

    The added value: a background Fetcher thread tees ``stdout`` while
    the main thread reads it, so each ``— done.`` line triggers a
    same-process per-well rsync without the GUI needing to know.
    """

    def __init__(
        self,
        ssh_proc: subprocess.Popen,
        run: _RemoteRun,
        fetcher: "Fetcher",
    ):
        self._ssh = ssh_proc
        self._run = run
        self._fetcher = fetcher
        self._stdout_pipe: Optional[io.TextIOBase] = None
        # Tee the ssh stdout into the fetcher AND back out to the caller.
        # The caller (PipelineRunner._run_pipeline_subprocess) reads from
        # self.stdout one line at a time; the fetcher also receives
        # every line via the same tee.
        self._tee_thread: Optional[threading.Thread] = None
        self._line_q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._start_tee()

    # Popen-compatible attributes ───────────────────────────────────────

    @property
    def pid(self) -> int:
        return self._ssh.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._ssh.returncode

    @property
    def stdout(self) -> "_QueueLineIterator":
        return self._lines

    def poll(self) -> Optional[int]:
        return self._ssh.poll()

    def wait(self, timeout: Optional[float] = None) -> int:
        rc = self._ssh.wait(timeout=timeout)
        # Drain remaining lines, then close the queue.
        if self._tee_thread is not None:
            self._tee_thread.join(timeout=5)
        self._line_q.put(None)
        self._fetcher.flush_and_join()
        return rc

    def terminate(self) -> None:
        self._fetcher.cancel()
        self._ssh.terminate()

    def kill(self) -> None:
        self._fetcher.cancel()
        self._ssh.kill()

    def send_signal(self, sig: int) -> None:
        self._ssh.send_signal(sig)

    # Internals ──────────────────────────────────────────────────────────

    def _start_tee(self) -> None:
        assert self._ssh.stdout is not None
        self._lines = _QueueLineIterator(self._line_q)

        def _pump(src: io.TextIOBase, q: "queue.Queue[Optional[str]]") -> None:
            try:
                for line in src:
                    self._fetcher.observe(line)
                    q.put(line)
            finally:
                q.put(None)

        self._tee_thread = threading.Thread(
            target=_pump,
            args=(self._ssh.stdout, self._line_q),
            daemon=True,
        )
        self._tee_thread.start()


class _QueueLineIterator:
    """Iterator that pulls lines off a queue; sentinel ``None`` ends
    iteration. Lets the Popen consumer treat us like a file."""

    def __init__(self, q: "queue.Queue[Optional[str]]"):
        self._q = q

    def __iter__(self) -> "_QueueLineIterator":
        return self

    def __next__(self) -> str:
        item = self._q.get()
        if item is None:
            raise StopIteration
        return item


# ────────────────────────────────────────────────────────────────────────────
# Fetcher
# ────────────────────────────────────────────────────────────────────────────


class Fetcher:
    """Per-well output puller. Triggered by stdout markers from the
    running pipeline; runs each pull in a small thread pool so the
    upload of well N+1 doesn't block the download of well N's output.
    """

    def __init__(
        self,
        host: RemoteHost,
        run: _RemoteRun,
        *,
        output_filter: OutputFilter,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
    ):
        self.host = host
        self.run = run
        self.output_filter = output_filter
        self._log = log_fn or (lambda _msg: None)
        self._progress = progress_fn
        self._stop = threading.Event()
        self._pending: queue.Queue[Optional[str]] = queue.Queue()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def observe(self, stdout_line: str) -> None:
        """Hand a pipeline-stdout line to the Fetcher.

        Looks for the ``— done.`` marker that the existing
        ``ProgressTracker`` already parses, plus the mode banner that
        announces the well total.
        """
        m = _RE_MODE_WELLS.search(stdout_line)
        if m:
            self.run.well_count_total = int(m.group(1))
        m = _RE_WELL_DONE.match(stdout_line.strip())
        if m:
            well = m.group("well").upper()
            self.run.expected_wells.add(well)
            self._pending.put(well)

    def cancel(self) -> None:
        self._stop.set()
        self._pending.put(None)

    def flush_and_join(self, timeout: float = 60.0) -> None:
        """Signal end-of-stream and wait for outstanding pulls.

        Called by the Popen wrapper's ``wait()`` so the run isn't
        reported complete until every well's output has landed.
        """
        self._pending.put(None)
        self._worker.join(timeout=timeout)

    # Internals ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while True:
            if self._stop.is_set():
                return
            try:
                item = self._pending.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                return
            self._fetch_one(item)

    def _fetch_one(self, well: str) -> None:
        """Pull a single well's output. The remote pipeline produces
        ``{output_dir}/{well}_out.zip`` and a sibling
        ``{csv_prefix}_{well}.csv`` (see report §2)."""
        tmp_zip = Path(tempfile.gettempdir()) / f"allwell-{self.run.run_id}-{well}_out.zip"
        zip_src = f"{self.host.ssh_alias}:{self.run.remote_output_dir}/{well}_out.zip"
        csv_glob_src = f"{self.host.ssh_alias}:{self.run.remote_output_dir}/*_{well}.csv"

        # Output zip — exists iff the pipeline finished this well. Pull it
        # even under CSV-only mode: we'll discard it after extracting the
        # files we want.
        if not self._rsync(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", zip_src, to_rsync_path(tmp_zip)]
        ):
            self._log(f"[remote] warn: could not pull {well}_out.zip; will retry at end.\n")
            return

        # CSVs live at the top of the remote output_dir, not inside the
        # well zip — pull them separately. A failure here isn't fatal:
        # the per-well CSV is the most important file and any retry can
        # re-pull it.
        self._rsync_glob(csv_glob_src, self.run.local_output_dir)

        # Now materialize the well's output in the user's output_dir
        # according to local input mode.
        self._materialize(well, tmp_zip)
        try:
            tmp_zip.unlink()
        except OSError:
            pass
        self.run.fetched_wells.add(well)
        if self._progress and self.run.well_count_total > 0:
            self._progress(len(self.run.fetched_wells) / self.run.well_count_total)

    def _materialize(self, well: str, well_zip: Path) -> None:
        """Place the well's outputs in ``local_output_dir`` so the result
        matches what a local run would have produced."""
        dest = self.run.local_output_dir
        dest.mkdir(parents=True, exist_ok=True)

        if self.output_filter is OutputFilter.CSV_ONLY:
            # Nothing to extract — the CSV was pulled separately. Drop
            # the well zip on the floor.
            return

        if self.run.local_input_mode is LocalInputMode.FOLDER:
            # Local folder-mode runs produce loose QC files (the pipeline
            # decompresses on its own when --compress_output_well_folders
            # is off). For the remote case we always run in
            # compress-output mode (it's the simplest fetch unit), so
            # extract here to match.
            try:
                with zipfile.ZipFile(well_zip, "r") as zf:
                    zf.extractall(dest)
            except (OSError, zipfile.BadZipFile) as exc:
                self._log(f"[remote] warn: extracting {well}_out.zip failed: {exc}\n")
            return

        # Zip- or flat-mode local run: the zip itself IS the expected
        # output unit. Drop it in place.
        try:
            shutil.copyfile(well_zip, dest / f"{well}_out.zip")
        except OSError as exc:
            self._log(f"[remote] warn: copying {well}_out.zip failed: {exc}\n")

    def _rsync(self, argv: list[str]) -> bool:
        # Routed through the shared retry helper (v5.1.1) so transient
        # network glitches during the Fetcher's per-well pull don't
        # silently lose a well's output. Non-transient rsync errors
        # (permission, bad path) still fail immediately.
        result = _rsync_with_retry(argv, log_fn=self._log)
        return result.returncode == 0

    def _rsync_glob(self, src_glob: str, dest_dir: Path) -> bool:
        """Pull files matching a glob. Wraps the source in a quoted-glob
        rsync so the remote side expands ``*``, not the local shell."""
        # We invoke `ssh host 'ls -1 GLOB'` first; rsync's own glob
        # support requires the source to be quoted in a way that's awkward
        # to spell cross-shell.
        host, path_glob = src_glob.split(":", 1)
        # v5.2.5: the ls-listing ssh and the per-file rsync both go
        # through the runner's ssh policy (key, accept-new, batchmode).
        # Without this they fall through to bare ssh argv and fail on
        # the very hosts the rest of the framework can talk to.
        listing = subprocess.run(
            self.host.ssh_argv(allocate_tty=False) + [
                "sh", "-c", shlex.quote(f"ls -1 {path_glob} 2>/dev/null || true"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        files = [ln for ln in listing.stdout.splitlines() if ln.strip()]
        if not files:
            return False
        ok = True
        dest_str = to_rsync_path(dest_dir).rstrip("/") + "/"
        for f in files:
            if not self._rsync(
                [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
                 "-a", f"{host}:{f}", dest_str]
            ):
                ok = False
        return ok


# ────────────────────────────────────────────────────────────────────────────
# RemoteRunner — orchestrator
# ────────────────────────────────────────────────────────────────────────────


class RemoteRunner:
    """High-level entry point. Drives a single remote run end-to-end.

    Lifecycle::

        runner = RemoteRunner(host)
        zipped = stage_local_zips(input_dir, …)
        proc = runner.spawn(zipped, output_dir, args, output_filter=...)
        for line in proc.stdout:
            ...  # GUI consumes lines exactly like spawn_pipeline
        proc.wait()
        runner.verify_and_cleanup(proc.returncode == 0, cleanup_mode=...)
    """

    def __init__(self, host: RemoteHost):
        self.host = host
        self._run: Optional[_RemoteRun] = None
        self._cache: DatasetCache = DatasetCache(host)

    @property
    def run(self) -> Optional[_RemoteRun]:
        return self._run

    # ── Required-file sync ──────────────────────────────────────────────

    def sync_required_files(
        self,
        *,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        """Push the minimal set of files the pipeline needs to the remote.

        Idempotent and cheap: rsync's mtime+size skip-pass means a no-op
        run is a single round-trip with no bytes transferred. Called
        automatically by :meth:`spawn` before every run so the user
        never accidentally runs against stale remote code.

        Files synced: see :data:`_REQUIRED_REMOTE_FILES`. The relative
        layout is preserved on the remote (so ``_Docs/requirements.txt``
        lands at ``{remote_project_dir}/_Docs/requirements.txt``).
        """
        log = log_fn or (lambda _m: None)
        root = repo_root()
        missing_local = [f for f in _REQUIRED_REMOTE_FILES if not (root / f).is_file()]
        if missing_local:
            raise FileNotFoundError(
                "Required pipeline files missing locally: "
                + ", ".join(missing_local)
                + f"  (looked under {root})"
            )

        # Make sure subdirectories exist on the remote — rsync with
        # individual file sources doesn't create intermediate dirs on
        # the target. One mkdir -p covers everything.
        remote_subdirs = sorted({
            str(Path(f).parent) for f in _REQUIRED_REMOTE_FILES
            if str(Path(f).parent) not in (".", "")
        })
        mk_target = f"{self.host.remote_project_dir}"
        mkdirs = [f"{mk_target}/{d}" for d in remote_subdirs]
        self._cache._ssh_run(  # noqa: SLF001
            ["mkdir", "-p", mk_target, *mkdirs],
            check=True,
        )

        # Use rsync's -R (--relative) so the source paths are recreated
        # under the destination — i.e. _Docs/requirements.txt lands at
        # <remote_project_dir>/_Docs/requirements.txt, not flattened.
        # The /./ pivot anchors the relative root at the repo top.
        # On Windows the root path is translated to /c/Users/... form
        # so Git Bash's rsync (Cygwin build) accepts it.
        root_for_rsync = to_rsync_path(root)
        sources = [f"{root_for_rsync}/./{f}" for f in _REQUIRED_REMOTE_FILES]
        dest = f"{self.host.ssh_alias}:{self.host.remote_project_dir}/"
        cmd = [
            resolve_rsync_binary(),
            *_rsync_ssh_transport(self.host),
            "-aR", "--info=stats0", *sources, dest,
        ]
        result = _rsync_with_retry(cmd, log_fn=log_fn, timeout=60)
        if result.returncode != 0:
            err = (result.stderr or "").strip() or f"rsync exited {result.returncode}"
            raise RuntimeError(
                f"Failed to sync pipeline files to remote "
                f"({self.host.ssh_alias}): {err}"
            )
        log(
            f"[remote] code sync: {len(_REQUIRED_REMOTE_FILES)} file(s) "
            f"verified on {self.host.ssh_alias}:{self.host.remote_project_dir}\n"
        )

    # ── Estimation ──────────────────────────────────────────────────────

    def estimate(
        self,
        zipped: ZippedInput,
        *,
        is_cache_hit: bool = False,
        local_compute_s: float | None = None,
        remote_compute_speedup: float = 4.0,
    ) -> RunEstimate:
        """Rough break-even estimate. The compute-time numbers come from a
        tiny local cache of past runs — first run has no history, so we
        assume a 4× CPU speedup as a placeholder.
        """
        total_bytes = sum(p.stat().st_size for p in zipped.well_zips)
        bw_mbps = max(self.host.measured_bandwidth_mbps, 1.0)
        bps = bw_mbps * 1_000_000 / 8
        transfer_s = (total_bytes / bps) if not is_cache_hit else 0.0
        # Default local-compute fallback: very rough — ~5s per well zip
        # on a laptop. The Phase-3 timings.json refinement lands later.
        if local_compute_s is None:
            local_compute_s = max(60.0, len(zipped.well_zips) * 5.0)
        remote_compute_s = local_compute_s / max(remote_compute_speedup, 1.0)
        return RunEstimate(
            bytes_to_transfer=total_bytes,
            transfer_s=transfer_s,
            remote_compute_s=remote_compute_s,
            local_compute_s=local_compute_s,
        )

    # ── Spawn ───────────────────────────────────────────────────────────

    def spawn(
        self,
        zipped: ZippedInput,
        local_output_dir: Path,
        pipeline_argv: list[str],
        *,
        output_filter: OutputFilter,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> _RemoteProcess:
        """Stage inputs, build the remote command, launch ssh.

        ``pipeline_argv`` is the result of
        :func:`services.pipeline_service.build_pipeline_args` — i.e. the
        same argv we'd pass locally. We rewrite the two path args and
        append the filter flags before launching remotely.

        Returns a Popen-compatible handle the GUI's stdout reader can
        consume unchanged.
        """
        log = log_fn or (lambda _m: None)

        # Cache budget enforcement before each run.
        self._cache.gc(self.host.cache_budget_gb * (1024**3), log_fn=log)

        # Ensure the pipeline code is up to date on the remote. Cheap —
        # ~300 KB of source plus rsync's delta-skip when nothing changed.
        # Guarantees the user is never running stale code after a local
        # edit, and turns the first-ever invocation against a host where
        # only the conda env was pre-installed (no bootstrap rsync) into
        # a working run instead of an obscure "can't open file" error.
        self.sync_required_files(log_fn=log)

        # Upload.
        key, sent, elapsed = self._cache.ensure_uploaded(
            zipped, log_fn=log, progress_fn=progress_fn, stop_event=stop_event
        )

        run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        remote_input = self._cache.remote_dataset_dir(key)
        remote_output = f"{self.host.remote_staging_dir}/runs/{run_id}/out"
        remote_pidfile = f"{self.host.remote_staging_dir}/runs/{run_id}/run.pid"

        self._cache._ssh_run(  # noqa: SLF001 — short-circuit reuse of ssh helper
            ["mkdir", "-p", remote_output],
            check=True,
        )

        self._run = _RemoteRun(
            run_id=run_id,
            cache_key=key,
            remote_input_dir=remote_input,
            remote_output_dir=remote_output,
            remote_pidfile=remote_pidfile,
            local_output_dir=local_output_dir,
            local_input_mode=zipped.mode,
        )

        remote_argv = self._build_remote_argv(
            pipeline_argv,
            remote_input,
            remote_output,
            output_filter,
            zipped.mode,
        )

        # Compose the remote shell command. PID echoed first so the
        # cancel path can target the pipeline directly.
        remote_cmd = (
            f"echo $$ > {shlex.quote(remote_pidfile)}; "
            f"exec {shlex.join(remote_argv)}"
        )
        ssh_argv = self.host.ssh_argv(allocate_tty=True) + [remote_cmd]

        log(
            f"[remote] run_id={run_id}  cache_key={key[:8]}  "
            f"(upload {sent / (1024**2):.1f} MB in {_fmt_secs(elapsed)})\n"
            f"[remote] $ {shlex.join(remote_argv)}\n"
        )

        popen = subprocess.Popen(
            ssh_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=(os.name == "posix"),
        )

        fetcher = Fetcher(
            self.host,
            self._run,
            output_filter=output_filter,
            log_fn=log,
            progress_fn=progress_fn,
        )
        return _RemoteProcess(popen, self._run, fetcher)

    # ── Verify + cleanup ────────────────────────────────────────────────

    def verify_and_cleanup(
        self,
        ok: bool,
        *,
        cleanup_mode: str = "run",
        log_fn: Callable[[str], None] | None = None,
    ) -> bool:
        """Pull top-level artefacts, then verify and clean up.

        Returns True iff the local manifest checks pass. The remote
        cleanup is only fired on success — a failed manifest leaves the
        remote state in place for retry.
        """
        log = log_fn or (lambda _m: None)
        if self._run is None:
            return False
        run = self._run

        # Pull pipeline_info.json + any top-level CSVs we may have missed
        # (well CSVs are pulled by the Fetcher, but a top-level summary
        # written at end-of-run wouldn't be).
        for top in ("pipeline_info.json",):
            self._rsync_optional(
                f"{self.host.ssh_alias}:{run.remote_output_dir}/{top}",
                run.local_output_dir / top,
                log_fn=log,
            )

        if not ok:
            log("[remote] pipeline returned non-zero — skipping cleanup, leaving remote state.\n")
            return False

        # Verify each expected well's output is present locally.
        # v5.1.1: before declaring the manifest incomplete, give the
        # Fetcher one more pass at any well whose CSV didn't make it
        # back. A transient connection blip during a per-well rsync no
        # longer requires a full pipeline re-run — we re-pull the
        # missing files using the same retry-aware helpers.
        missing = self._verify_local_manifest(log=log)
        if missing:
            self._retry_missing_wells(missing, log=log)
            missing = self._verify_local_manifest(log=log)
        if missing:
            log(
                f"[remote] keeping remote files for retry — verification failed: "
                f"{len(missing)} file(s) missing.\n"
            )
            return False

        # Cleanup according to the user's flag. Fires only after verify
        # passes — there's no path that can lose remote state if a fetch
        # was incomplete.
        if cleanup_mode == "none":
            log("[remote] --cleanup-remote=none — leaving everything in place.\n")
            return True
        self._cache._ssh_run(  # noqa: SLF001
            ["rm", "-rf", f"{self.host.remote_staging_dir}/runs/{run.run_id}"],
            check=False,
        )
        log(f"[remote] removed run dir {run.run_id}.\n")
        if cleanup_mode == "all":
            self._cache._ssh_run(  # noqa: SLF001
                ["rm", "-rf", self._cache.remote_dataset_dir(run.cache_key)],
                check=False,
            )
            log(f"[remote] removed dataset cache {run.cache_key[:8]}.\n")
        return True

    def _rsync_optional(
        self, src: str, dest: Path, *, log_fn: Callable[[str], None]
    ) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = _rsync_with_retry(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", src, to_rsync_path(dest)],
            log_fn=log_fn, timeout=60,
        )
        if result.returncode != 0:
            log_fn(
                f"[remote] note: {src} not present on remote "
                f"(rc={result.returncode}).\n"
            )

    def _retry_missing_wells(
        self, missing_messages: list[str], *, log: Callable[[str], None],
    ) -> None:
        """Re-pull the per-well outputs for wells whose CSV is missing.

        Closes the "transient drop ate one well's output" hole that
        existed in v5.0.0–v5.1.0: the in-stream Fetcher already gave up
        on a failed pull with a "will retry at end" comment that wasn't
        actually implemented. This is that retry.
        """
        run = self._run
        if run is None:
            return
        # Extract well IDs from the manifest messages. Format from
        # _verify_local_manifest: "missing CSV for A01" / "empty CSV
        # for A01". Match a two-letter+two-digit token.
        well_re = re.compile(r"\b([A-H]\d{2})\b")
        wells: set[str] = set()
        for m in missing_messages:
            match = well_re.search(m)
            if match:
                wells.add(match.group(1))
        if not wells:
            return

        log(f"[remote] end-of-run retry: re-pulling {len(wells)} well(s)…\n")
        host = self.host
        for well in sorted(wells):
            # Pull the per-well CSV (top-level on remote out dir).
            csv_glob_src = (
                f"{host.ssh_alias}:{run.remote_output_dir}/*_{well}.csv"
            )
            try:
                listing = subprocess.run(
                    host.ssh_argv(allocate_tty=False) + [
                        "sh", "-c",
                        shlex.quote(f"ls -1 {run.remote_output_dir}/*_{well}.csv 2>/dev/null || true"),
                    ],
                    capture_output=True, text=True, timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                log(f"[remote] retry: listing {well} failed: {exc}\n")
                continue
            files = [ln for ln in (listing.stdout or "").splitlines() if ln.strip()]
            for f in files:
                _rsync_with_retry(
                    [resolve_rsync_binary(), *_rsync_ssh_transport(host),
                     "-a", f"{host.ssh_alias}:{f}",
                     to_rsync_path(run.local_output_dir).rstrip("/") + "/"],
                    log_fn=log,
                )
            # Also re-pull the {well}_out.zip in case the user has a
            # full output filter — it's harmless under csv-only mode
            # since the temp zip gets discarded.
            _rsync_with_retry(
                [resolve_rsync_binary(), *_rsync_ssh_transport(host),
                 "-a",
                 f"{host.ssh_alias}:{run.remote_output_dir}/{well}_out.zip",
                 to_rsync_path(run.local_output_dir).rstrip("/") + "/"],
                log_fn=log,
            )

    def _verify_local_manifest(
        self, *, log: Callable[[str], None]
    ) -> list[str]:
        """Check that every well declared 'done' has its outputs locally.

        Returns the list of missing paths (empty list = all good).
        """
        run = self._run
        assert run is not None
        missing: list[str] = []
        for well in sorted(run.expected_wells):
            # CSV is always expected (full or csv-only modes both keep it).
            csvs = list(run.local_output_dir.glob(f"*_{well}.csv"))
            if not csvs:
                missing.append(f"missing CSV for {well}")
                continue
            if any(p.stat().st_size == 0 for p in csvs):
                missing.append(f"empty CSV for {well}")
        if missing:
            for m in missing[:10]:
                log(f"[remote] verify: {m}\n")
            if len(missing) > 10:
                log(f"[remote] verify: … and {len(missing) - 10} more\n")
        else:
            log(
                f"[remote] verify: all {len(run.expected_wells)} wells "
                f"present locally.\n"
            )
        return missing

    # ── Cancel ──────────────────────────────────────────────────────────

    def cancel(self, ssh_proc: subprocess.Popen) -> None:
        """Best-effort remote kill. The local pgid kill (handled by
        PipelineRunner) is the primary mechanism; this is the belt-and-
        braces second channel via a fresh ssh that ``kill``s the PID we
        wrote on startup."""
        if self._run is None:
            return
        run = self._run
        try:
            subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [
                    f"kill -TERM $(cat {shlex.quote(run.remote_pidfile)} 2>/dev/null) "
                    "2>/dev/null || true"
                ],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    # ── Argv building ───────────────────────────────────────────────────

    def _build_remote_argv(
        self,
        local_argv: list[str],
        remote_input: str,
        remote_output: str,
        output_filter: OutputFilter,
        mode: LocalInputMode,
    ) -> list[str]:
        """Rewrite the local pipeline argv for remote execution.

        Replaces ``--input_dir`` / ``--output_dir`` with the remote-side
        paths, swaps the local python path for the host's
        ``remote_python``, and appends the filter flags.
        """
        # The local argv is shaped like:
        #   [sys.executable, /path/to/process_microscopy.py, --input_dir, X, ...]
        # We replace the interpreter and script path with the host's.
        remote_script = f"{self.host.remote_project_dir}/process_microscopy.py"
        out = [self.host.remote_python, remote_script]
        i = 2  # skip the interpreter + script slots
        while i < len(local_argv):
            arg = local_argv[i]
            if arg == "--input_dir":
                out.extend([arg, remote_input])
                i += 2
            elif arg == "--output_dir":
                out.extend([arg, remote_output])
                i += 2
            else:
                out.append(arg)
                i += 1
        # Apply the output filter.
        if output_filter is OutputFilter.CSV_ONLY and "--no_save_tophat" not in out:
            out.append("--no_save_tophat")
        # Host-level override args.
        out.extend(self.host.extra_args)
        if self.host.cpu_only and "--cpu_only" not in out:
            out.append("--cpu_only")
        return out
