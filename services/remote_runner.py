"""Remote-execution runner for ``process_microscopy.py``.

Stages per-well zips to an SSH host, launches the pipeline remotely, and
streams outputs back as wells complete. Designed as a drop-in replacement
for ``spawn_pipeline()`` from the GUI's point of view: the Popen handle
returned by :meth:`RemoteRunner.spawn` looks identical (stdout pipe, pgid
kill works, return code semantics preserved) so ``PipelineRunner`` and
``ProgressTracker`` need only a single-line branch to opt in.

The five transfer-minimizing levers are implemented here and in the modules
this one orchestrates — ``services.remote_staging`` turns the user's input into
the archives a run uploads, ``services.remote_fetch`` owns bringing output home
and proving it arrived, ``services.remote_run_state`` the record a run carries,
and ``services.remote_wire`` the lines the stages print and parse:

* **Per-well zip granularity** — ``remote_staging.stage_local_zips`` runs the existing
  ``well_plate_zipper`` locally first, so a 1500-image plate ships as
  ~100 large zips instead of 1500 small TIFs.
* **Persistent dataset cache** — :class:`DatasetCache` keys uploads by a
  stat-based content hash so re-runs of the same input are a metadata
  roundtrip, not a re-upload.
* **CSV-by-default output filter** — :class:`OutputFilter` decides which
  files to materialize locally and toggles ``--no_save_tophat`` on the
  remote so QC TIFs aren't even produced when not wanted.
* **Pipelined per-well execution** — ``remote_fetch.Fetcher`` watches the
  pipeline's stdout for the existing ``— done.`` marker and starts
  pulling each well's output the moment it lands, overlapping
  download with later-well compute.
* **Verified cleanup** — :meth:`RemoteRunner.verify_and_cleanup` only
  fires the remote ``rm -rf`` when ``remote_fetch.FetchedOutputVerifier``
  returns a passing verdict; a missing local file aborts the cleanup so the
  user can retry without losing remote state.
"""

from __future__ import annotations

import io
import os
import queue
import re
import shlex
import subprocess
import sys
from functools import lru_cache
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from services.subprocess_text import TEXT_UTF8
from services.remote_config import (
    RemoteHost,
    launcher_time_seconds,
    scale_launcher_time,
    resolve_rsync_binary,
    to_rsync_path,
    update_bandwidth,
)
# Stdlib-only (fov, tp) done-key helpers — shared with pipeline.io (audit A2).
# This module import must stay imaging-stack-free; incremental_keys is csv/
# zipfile/pathlib only, so it never drags numpy/tifffile into the remote client.
from util.channel_roles import LABELFREE_METHODS, quantified_channel_tokens
# The conda-environment resolution cluster, re-exported so every call site in
# this module (and the tests that reach for it) is unchanged by the move.
from services.remote_conda import (  # noqa: F401
    _CONDA_ACTIVATE_SNIPPET,
    _conda_env_guard,
    _conda_env_selector,
    _conda_run_prefix,
    _conda_run_prefix_for,
)
# The record half of field quality, which is stdlib-only. Importing
# ``pipeline.field_quality`` here would pull numpy and scipy into the remote
# client — a guard walks this module's import graph and says so, because the
# client is sent to hosts where the pipeline's dependencies are not installed.
from util.well_token import (
    background_well_key as _bg_well_key,
    blank_wells_for_run,
)
from services import run_journal
from util.aw_safe import swallow, swallowed
# The shared "retry an OSError with a backoff, then fail with an actionable
# message" shield. A remote run does as much I/O on the user's own volumes as a
# local one — it reads the whole plate off the input volume to stage the upload
# and writes every fetched well back onto the output volume — so it needs the
# same protection the pipeline's result writes have. ``pipeline.io`` owns the
# pipeline-side wrapper; this module cannot import it (that would drag numpy /
# tifffile into the remote client), which is why the loop lives in ``util``.
# The run record and the line vocabulary every stage of a remote run shares, and
# the owner of bringing its output home. Each lives in a module of its own; the
# runner orchestrates them.
from services.remote_run_state import (
    LocalInputMode,
    OutputFilter,
    _RemoteRun,
    _canonical_well_from_path,
)
from services.remote_wire import (  # noqa: F401 — WELL_XFER_PREFIX is facade
    TRANSIENT_PREFIX,
    WELL_XFER_PREFIX,
    _well_xfer_line,
)
# Staging the user's input into the archives a run uploads. ``stage_local_zips``
# and ``input_well_containers`` are part of this module's supported facade.
from services.remote_staging import (  # noqa: F401 — facade re-exports
    ZippedInput,
    input_well_containers,
    stage_local_zips,
)
from services.remote_fetch import (
    FetchedOutputVerifier,
    Fetcher,
    _snapshot_local_artifacts,
)






# The scratch / transit / fingerprint leaves live in their own module now — nine
# functions whose only tie to this one was ``TRANSIT_SUBDIR``. Re-exported here
# rather than left for callers to find, so every existing import of them from
# ``services.remote_runner`` keeps working and the move is a move rather than a
# rename of the whole call graph.
# The transport layer — rsync capability probes, remote-path quoting, the retry
# funnel every transfer passes through, and the progress-line reader — lives in
# its own module. Re-exported so every existing import keeps working.
# The shell a granted node runs while it waits for its input, and the clocks
# that time it. Re-exported so every call site and test here is unchanged --
# see services/remote_wait_snippets.py.
from services.remote_wait_snippets import (  # noqa: F401
    UPLOAD_HEARTBEAT_S,
    UPLOAD_HEARTBEAT_STALE_S,
    UPLOAD_WAIT_TIMEOUT_S,
)
from services.remote_transport import (  # noqa: F401
    _RSYNC_RETRY_CODES,
    _RSYNC_RETRY_DELAYS,
    _fmt_secs,
    _iter_rsync_lines,
    _looks_like_rsync_filename,
    _remote_join,
    _remote_quote,
    _rsync_progress_flags,
    _rsync_run_cancellable,
    _rsync_ssh_transport,
    _rsync_supports_append_verify,
    _rsync_supports_info_flag,
    _rsync_version,
    _rsync_with_retry,
)

# Where a run lives, and whether an earlier one's output may be reused — the
# addresses, the keys they are derived from, and the re-attach decision. Its own
# module because it is pure, which is what lets the reuse predicate be tested
# directly rather than through a harness. Re-exported so existing imports work.
from services.remote_suspend import (  # noqa: F401 — re-exported
    _AnySet,
)
from services.remote_suspend import request_suspend as _request_suspend
from services.remote_commands import (  # noqa: F401 — re-exported
    _BATCH_DONE_RE,
    BATCH_DONE,
    BATCH_DONE_MARKER,
    BATCH_READY,
    _batch_driver_snippet,
    _compose_driver_cmd,
    _compose_remote_cmd,
    _remote_scratch_export,
)
from services.remote_run_id import (  # noqa: F401
    _pipeline_launch_is_pointless,
    UPLOAD_HEARTBEAT,
    UPLOAD_SENTINEL,
    _choose_remote_output_dir,
    _compute_run_id,
    _forced_from_argv,
    _local_input_dir_from_argv,
    _parse_argv_flags,
    pipeline_argv_digest,
    remote_run_dir,
    remote_run_paths,
)

from services.remote_scratch import (  # noqa: F401
    TRANSIT_SUBDIR,
    # The same set object, not a copy: it is mutated, never rebound, so a
    # caller clearing it through either module clears the one the code reads.
    _transit_refused,
    _scratch_dir,
    fingerprint_archives,
    source_fingerprint,
)











# The remote upload manifest lives in ``services/remote_manifest`` — a data
# declaration with no behaviour, moved out so a newly-required entry cannot
# collide with this module's line budget. Re-exported because a dozen call
# sites, the PyInstaller spec and several docstrings name it by this path.
from services.remote_manifest import _REQUIRED_REMOTE_FILES  # noqa: E402

# The supported import surface for application and command-line callers.
# Every other name reached through this module is migration debt, classified
# in ``tests/test_remote_runner_facade.py::COMPAT_INVENTORY`` and only allowed
# to shrink. The private manifest name is deliberate: the bootstrap and the
# PyInstaller spec read it by this path as their packaging contract.
__all__ = (
    "LocalInputMode", "OutputFilter", "RemoteRunner", "TRANSIENT_PREFIX",
    "WELL_XFER_PREFIX", "ZippedInput", "_REQUIRED_REMOTE_FILES",
    "input_well_containers", "repo_root", "stage_local_zips",
)



def repo_root() -> Path:
    """Absolute path to the repo root from which required-file paths
    are resolved. Single source of truth used by the runner and the
    bootstrap script's Python helpers."""
    return Path(__file__).resolve().parent.parent





@dataclass
class RunEstimate:
    """What this run has to move, and how long that should take.

    **Deliberately not a local-vs-remote verdict.** It used to end with
    "remote wins" / "LOCAL would be faster", derived from a *hardcoded*
    ``max(60s, 5s per well)`` local-compute figure with a flat 4x speedup
    assumed for the remote. Neither number measured anything: on a real plate
    that guessed 60s against a run that took eight hours, roughly a
    thousand-fold error, and the banner used it to recommend running locally —
    printed beside a genuinely measured transfer size, which is exactly what
    made it look like a measurement.

    It could not have been right in principle either. The comparison assumes
    local compute pays no I/O, and a dataset that lives on a NAS pays network
    reads for every image whether or not a remote is involved, plus its scratch
    on top. Modelling that is a real piece of work; asserting a winner without
    it is a guess wearing a measurement's clothes.

    So this reports only what it actually knows — bytes, and the transfer time
    those bytes imply at the measured bandwidth. Which side is faster is a
    question this type does not answer.
    """

    bytes_to_transfer: int
    transfer_s: float

    def format(self) -> str:
        gb = self.bytes_to_transfer / (1024**3)
        return f"transfer {gb:.2f} GB ({_fmt_secs(self.transfer_s)}) to the remote"







# ────────────────────────────────────────────────────────────────────────────















def _record_remote_in_journal(
    local_output_dir: Path, *, run: "_RemoteRun", host_alias: str,
    argv_digest: str, source_key: str,
) -> None:
    """Write this run's remote address into the output directory's journal.

    Best-effort and deliberately quiet: a run whose journal cannot be written
    still runs, and the only thing lost is the fallback address a later run
    could have re-attached to.
    """
    from services import run_journal

    try:
        run_journal.record_remote(
            local_output_dir,
            run_id=run.run_id,
            cache_key=run.cache_key,
            host_alias=host_alias,
            remote_input_dir=run.remote_input_dir,
            remote_output_dir=run.remote_output_dir,
            argv_digest=argv_digest,
            source_key=source_key,
        )
    except OSError:
        swallowed(f"could not record the remote address in {local_output_dir}")





















































# ────────────────────────────────────────────────────────────────────────────
# DatasetCache
# ────────────────────────────────────────────────────────────────────────────






def _scheduler_handoff_lines(launcher: str, *, batch: int | None = None) -> str:
    """The lines announcing that the run is now waiting on a scheduler.

    Emitted by **every** launch path, which is the point of it being a function.
    A scheduler-backed host queues the pipeline instead of starting it, and a
    queue wait looks exactly like a hang: the upload finishes, the log stops,
    and nothing on screen says what is being waited for. The non-batched path
    said so; the batched one composed the same launcher and said nothing, so a
    batched run went quiet at the precise moment it began waiting.

    Returns ``""`` when the host has no launcher, so a caller can emit it
    unconditionally — a direct-ssh host is not waiting for anything and must not
    be told that it is.

    The job id is deliberately absent here. ``$SLURM_JOB_ID`` is written to the
    ``.slurm`` sidecar from *inside* the allocation, so at this moment it does
    not exist yet; the id first appears in the scheduler's own stderr, which
    ``ProgressTracker`` picks up.
    """
    launcher = (launcher or "").strip()
    if not launcher:
        return ""
    where = f"batch {batch}: " if batch is not None else ""
    return (
        f"[remote] scheduler: {where}requesting allocation via: {launcher}\n"
        f"[remote] scheduler: waiting for the scheduler to grant a node "
        f"(this can queue)…\n"
    )


# Written into the run directory once every byte of the plate has landed. The
# scheduled job waits for it before starting the pipeline, which is what lets
# the allocation be requested *while* the upload is still running instead of
# after it: on a busy cluster the queue wait and the transfer then overlap
# instead of running end to end.

# Ceiling on one short ssh control command (mkdir, touch, rm -rf). Generous
# enough for an ``rm -rf`` of a run directory on a slow shared filesystem, and
# far short of the hours an unbounded one can park a thread for.
SSH_CONTROL_TIMEOUT_S = 120.0


def _only_my_job(jid_var: str, marker: str) -> str:
    """Shell that refuses to cancel a job belonging to somebody else.

    Two locks rather than one. The id is read from a file this app wrote, so in
    the ordinary case it is our own job — but ``remote_staging_dir`` can point
    at shared scratch, and ``run_id`` is deterministic in (input, arguments),
    so two people processing the same plate share a run directory and therefore
    a sidecar. Slurm would refuse a non-owner's ``scancel`` anyway; that is the
    scheduler's policy, not this app's promise, and a cluster where operators
    run the GUI would not have it.

    So: ask the scheduler whose job it is and stop if the answer is not us, and
    pass ``-u`` to ``scancel`` as well, so even a job that changed hands between
    the question and the answer cannot be cancelled by us.
    """
    return (
        f'_aw_me=$(id -un 2>/dev/null || echo "$USER"); '
        f'_aw_owner=$(squeue -h -j "${jid_var}" -o %u 2>/dev/null | head -n1); '
        f'if [ -n "$_aw_owner" ] && [ "$_aw_owner" != "$_aw_me" ]; then '
        f'echo "{marker} ${jid_var} $_aw_owner"; exit 0; fi; '
    )


class _UploadHeartbeat:
    """Touches the remote heartbeat for as long as this app owns the run.

    A daemon thread rather than a hook inside the transfer: rsync can be busy
    with one large archive for minutes without returning to any per-file
    callback, and a heartbeat that only advances between files would read as
    dead exactly during the slowest part of the upload.

    Stopping is the point. Whatever ends the run — success, failure, the Stop
    button, the process being killed — takes the beat with it, and a job still
    waiting for its input frees its node about a minute later instead of
    holding it for the full deadline.

    It beats for the whole run, not only the upload, because it is also what
    :meth:`RemoteRunner._sweep_orphaned_allocation` reads to decide whether an
    allocation still has an owner. ``run_id`` is deterministic in the plate, so
    two sessions on the same plate meet at the same sidecar; a beat that
    stopped when the upload finished would make an hour-long compute look
    abandoned and get itself cancelled by the second session.
    """

    def __init__(self, cache: "DatasetCache", path: str,
                 interval_s: float = UPLOAD_HEARTBEAT_S) -> None:
        self._cache = cache
        self._path = path
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        """One touch, now. Called once before the job starts waiting, so the
        first thing it looks at is a heartbeat that already exists.

        Bounded by the beat interval rather than the default: a touch that has
        not landed by the time the next one is due is not going to help, and
        letting them queue up would leave the thread minutes behind while the
        remote reads the silence as an app that died.
        """
        self._cache._ssh_run(["touch", self._path], check=False,
                             timeout_s=self._interval)

    def start(self) -> None:
        """Begin beating. Paired with :meth:`stop` — see the class docstring."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="aw-upload-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop beating, and wait for the thread to notice.

        The whole mechanism is "a beat that stops means the app is gone", so a
        beat that outlives its run is not a leak of one thread — it tells the
        remote a dead run is alive, holds the node for the full deadline, and
        keeps the orphan sweep from ever sweeping.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self) -> "_UploadHeartbeat":
        self.beat()
        self.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.beat()
            except Exception:
                # A missed beat is not fatal on its own — the job tolerates
                # `UPLOAD_HEARTBEAT_STALE_S` of silence — and an ssh blip must
                # not take down an upload that is otherwise fine.
                swallowed("upload heartbeat")

    def __exit__(self, *_exc) -> None:
        self.stop()


@lru_cache(maxsize=1)




class _LogToLineStream(__import__("logging").Handler):
    """Forward a ``logging`` record onto the run's own line stream.

    The GUI reads a run's progress off that stream, so a phase that reports
    itself through ``logging`` is invisible to it unless its records are put
    there. Used for the post-fetch auto-threshold pass, which runs in-process
    rather than as a subprocess and so has no stdout of its own to be read.

    Deliberately format-free: the consumer's regexes match the pipeline's own
    message text, so a prefix or a timestamp here would stop them matching —
    the same class of break as a progress flag that is never passed.
    """

    def __init__(self, emit_line) -> None:
        super().__init__()
        self._emit_line = emit_line

    def emit(self, record) -> None:
        try:
            self._emit_line(record.getMessage() + "\n")
        except Exception:  # a log line must never break the pass it describes
            self.handleError(record)








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






# ────────────────────────────────────────────────────────────────────────────
# RemoteRunner
# ────────────────────────────────────────────────────────────────────────────



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
        fetcher: Fetcher,
        *,
        heartbeat: "_UploadHeartbeat | None" = None,
    ):
        self._ssh = ssh_proc
        self._run = run
        self._fetcher = fetcher
        # Beats for as long as this process owns the allocation; every exit
        # from the run goes through wait(), terminate() or kill(), and each of
        # them stops it. See _UploadHeartbeat's docstring for why it may not
        # end with the upload.
        self._heartbeat = heartbeat
        # Tee the ssh stdout into the fetcher AND back out to the caller.
        # The caller (PipelineRunner._run_pipeline_subprocess) reads from
        # self.stdout one line at a time; the fetcher also receives
        # every line via the same tee.
        self._tee_thread: threading.Thread | None = None
        self._line_q: queue.Queue[str | None] = queue.Queue()
        self._start_tee()

    # Popen-compatible attributes ───────────────────────────────────────

    @property
    def pid(self) -> int:
        return self._ssh.pid

    @property
    def returncode(self) -> int | None:
        return self._ssh.returncode

    @property
    def stdout(self) -> _QueueLineIterator:
        return self._lines

    def poll(self) -> int | None:
        return self._ssh.poll()

    def wait(self, timeout: float | None = None) -> int:
        rc = self._ssh.wait(timeout=timeout)
        self._stop_heartbeat()
        # Drain remaining lines, then close the queue.
        if self._tee_thread is not None:
            self._tee_thread.join(timeout=5)
        self._line_q.put(None)
        self._fetcher.flush_and_join()
        return rc

    def terminate(self) -> None:
        self._stop_heartbeat()
        self._fetcher.cancel()
        self._ssh.terminate()

    def kill(self) -> None:
        self._stop_heartbeat()
        self._fetcher.cancel()
        self._ssh.kill()

    def _stop_heartbeat(self) -> None:
        """End the beat once, on whichever exit path gets here first."""
        hb, self._heartbeat = self._heartbeat, None
        if hb is not None:
            try:
                hb.stop()
            except Exception:
                swallowed("stopping the run heartbeat")

    def send_signal(self, sig: int) -> None:
        self._ssh.send_signal(sig)

    # Internals ──────────────────────────────────────────────────────────

    def _start_tee(self) -> None:
        assert self._ssh.stdout is not None
        self._lines = _QueueLineIterator(self._line_q)

        def _pump(src: io.TextIOBase, q: queue.Queue[str | None]) -> None:
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


def _empty_well_blanks(argv, well_zips) -> tuple[str, ...]:
    """The blank well archives this run needs for empty-well correction.

    Returns their stems, or empty when the run asks for no empty-well
    correction. Batched mode has to know about them because it slices the plate
    into batches and stages one at a time: the pipeline builds its correction
    from whatever wells are in the input directory *at that moment*, so a blank
    sitting in the last batch corrects the last batch and nothing else. Every
    earlier batch produced numbers with the background left in, and the output
    carried no sign of it.
    """
    flags = _parse_argv_flags(argv, multi=set(), single={
        "--background", "--flatfield", "--background_well", "--background_well_map"})
    if "empty_well" not in (flags.get("--background"), flags.get("--flatfield")):
        return ()
    # The pipeline's own parser and match key, not a fourth spelling of them.
    # A local `re.split(r"[,\s]+", ...)` here read "H12 G12" as two wells while
    # `split_background_wells` reads it as one, so batched mode held out and
    # uploaded two archives the pipeline then matched neither of — the run died
    # on the guard in every batch. `blank_wells_for_run` is that reading widened
    # to the map: a blank named only there is still a blank.
    wanted = {_bg_well_key(w) for w in blank_wells_for_run(
        flags.get("--background_well", ""), flags.get("--background_well_map", ""))}
    if not wanted:
        return ()
    return tuple(z.stem for z in well_zips if _bg_well_key(z.stem) in wanted)




def _auto_threshold_args_from_argv(argv):
    """Reconstruct enough of the pipeline's parsed args to re-run its threshold
    pass locally after a batched remote run.

    Rebuilding the arg object rather than re-implementing the pass is what keeps
    the local and remote answers identical when either gains a mode — this path
    previously drifted into thresholding only the first fluor channel.

    ``no_auto_threshold`` is forced False: the flag in *argv* is the batching
    suppression, not the user's choice, so honouring it would mean a batched run
    never got thresholds at all.

    *argv* is what the GUI built, so it is **pre-remap**: for a label-free method
    ``process_microscopy.main`` moves ``--phase_token`` onto the nuclear slot, and
    the threshold pass asks ``_segmentation_tokens`` which channel was segmented.
    Applying the same remap here keeps that answer right — without it the pass
    zeroes the channel the form happened to name and leaves the channel actually
    segmented with a threshold derived from a brightfield image.
    """
    from types import SimpleNamespace

    flags = _parse_argv_flags(
        argv,
        multi={"--fluor_tokens"},
        single={
            "--nuclear_token", "--cytoplasm_token", "--segmentation_method",
            "--csv_prefix", "--filename_schema", "--filename_sep",
            "--pos_control_well", "--neg_control_well", "--phase_token",
        },
    )
    nuclear_token = flags.get("--nuclear_token", "")
    phase_token = str(flags.get("--phase_token", "") or "").strip()
    if phase_token and flags.get("--segmentation_method", "") in LABELFREE_METHODS:
        nuclear_token = phase_token
    return SimpleNamespace(
        no_auto_threshold=False,
        fluor_tokens=flags.get("--fluor_tokens", []),
        nuclear_token=nuclear_token,
        phase_token=phase_token,
        cytoplasm_token=flags.get("--cytoplasm_token", ""),
        segmentation_method=flags.get("--segmentation_method", ""),
        csv_prefix=flags.get("--csv_prefix", "measurements"),
        filename_schema=flags.get("--filename_schema", ""),
        filename_sep=flags.get("--filename_sep", "_"),
        pos_control_well=flags.get("--pos_control_well", ""),
        neg_control_well=flags.get("--neg_control_well", ""),
    )


class _BatchedRemoteProcess:
    """``_RemoteProcess``-compatible wrapper that runs the pipeline in
    sequential batches of ``batch_size`` wells.

    One process runs for the whole plate: a driver
    (:func:`_batch_driver_snippet`) inside a single allocation, which the
    batches are fed to as they finish uploading. Per batch, this side uploads
    that batch's zips into a staging directory of its own, touches the batch's
    ``ready`` file, and waits for the driver's marker naming the exit code. The
    driver swaps the staged directory into the pipeline's input path whole, so
    a batch's pipeline sees that batch's zips and nothing else.

    **A failed batch does not end the run.** The node is already held and the
    remaining batches still need it, so the loop continues and the end-of-run
    tally names which batches failed; the run's own code carries the first of
    them. A single ``Fetcher`` instance persists across all batches with
    ``delete_after_download=True``, so peak remote disk stays at
    ``(M wells of input) + (~1 well of output)``.

    Exposes the same Popen-compatible surface as ``_RemoteProcess``
    (``stdout``, ``poll()``, ``wait()``, ``terminate()``, ``kill()``,
    ``pid``, ``returncode``) so :meth:`pipeline_runner._run_pipeline_remote`
    can consume either transparently.
    """

    def __init__(
        self,
        runner: RemoteRunner,
        zipped: ZippedInput,
        local_output_dir: Path,
        pipeline_argv: list[str],
        batch_size: int,
        *,
        output_filter: OutputFilter,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ):
        self._runner = runner
        self._zipped = zipped
        self._local_output_dir = local_output_dir
        self._pipeline_argv = pipeline_argv
        self._batch_size = max(1, batch_size)
        self._output_filter = output_filter
        self._log = log_fn or (lambda _m: None)
        self._progress = progress_fn
        self._stop = stop_event or threading.Event()
        # Set by `note_suspend_requested`: finish the batch in flight, send no
        # more. Separate from `_stop`, which tears the batch in flight down.
        self._suspending = threading.Event()
        self._returncode: int | None = None
        self._line_q: queue.Queue[str | None] = queue.Queue()
        self._lines = _QueueLineIterator(self._line_q)
        self._current_proc: subprocess.Popen | None = None
        # Guards _current_proc: the worker thread reassigns it per batch while
        # the GUI thread reads it in terminate()/kill()/pid/send_signal (E20).
        self._current_proc_lock = threading.Lock()
        # Set by the reader thread as each batch's marker arrives, and read by
        # the batch loop. One Event per batch rather than a single condition,
        # so a marker that lands early (the driver is ahead of the loop) is not
        # missed — an Event that is already set returns immediately.
        self._batch_events: dict[int, threading.Event] = {}
        self._batch_rc: dict[int, int] = {}
        # The driver's stdout has ended: it finished, the node died, or a Stop
        # killed the ssh. Whichever it was, no further batch will report back.
        self._stream_ended = threading.Event()
        # The stream ended with batches still unaccounted for — so it was not
        # the driver finishing. Read by `_await_batch` to say which of the two
        # happened, because "you stopped it" and "the node went away" call for
        # different words and only one of them is the user's doing.
        self._driver_lost = threading.Event()
        self._n_batches = 0
        # True once this object has launched a driver of its own. Until then
        # there is nothing of ours on the scheduler to cancel.
        self._owns_allocation = False
        self._reader: threading.Thread | None = None
        self._heartbeat: _UploadHeartbeat | None = None

        # Set up the shared run identifiers and remote dirs.
        # Keep the single-shot path's layout: input goes into the
        # persistent dataset cache (``datasets/<key>``) and output
        # into ``runs/<run_id>/out``. The "stale zips" diagnosis
        # of v5.4.7 was incorrect — the user cleared remote dirs
        # manually before the run that showed "processed 5, skipped
        # 5" — so we're not switching dirs blindly. The actual cause
        # of that log line is still under investigation; if it turns
        # out to be cache-dir pollution we can switch later, with
        # evidence.
        key = runner._cache.cache_key(zipped)
        run_id = "run-" + _compute_run_id(
            key, pipeline_argv, extras=runner._host_argv_extras(output_filter))
        self._run_id = run_id
        # Batched input lives in the *run* directory, not the shared dataset
        # cache. Each batch is staged beside it as ``b<i>`` and moved into
        # place by the driver, so the pipeline for a batch sees exactly that
        # batch's zips. The old shared directory relied on the Fetcher having
        # deleted the previous batch's input in time, which nothing waited for
        # — the next batch's startup scan could pick up leftovers and process
        # them twice, which is what `ProgressTracker.well_repeats` counts. The
        # cache is no warmer for it: the Fetcher deletes each zip as it lands
        # either way.
        own = remote_run_paths(runner.host, run_id)
        remote_input = f"{own.run_dir}/in"
        remote_output = own.out
        remote_pidfile = own.pidfile
        runner._cache._ssh_run(
            ["mkdir", "-p", remote_input, remote_output],
            check=True,
        )

        _force, _forced_wells = _forced_from_argv(pipeline_argv)
        runner._run = _RemoteRun(
            run_id=run_id,
            cache_key=key,
            remote_input_dir=remote_input,
            remote_output_dir=remote_output,
            remote_pidfile=remote_pidfile,
            local_output_dir=local_output_dir,
            local_input_mode=zipped.mode,
            local_input_dir=_local_input_dir_from_argv(pipeline_argv),
            output_filter=output_filter,
            incremental=zipped.incremental,
            force=_force, force_wells=_forced_wells,
            pre_run_artifacts=_snapshot_local_artifacts(local_output_dir),
        )
        # Batched runs record their address too. Without this a plate run in
        # batches journalled a ``remote-batched`` mode beside an empty remote
        # block — a record that names the mode and not the place, which is the
        # half that a later run actually needs.
        # The *same* extras the run id forty lines above was built from.
        # Recording the bare argv digest here made this the smaller key: a
        # batched CSV-only run journalled a digest identical to a plain FULL
        # run's, so a later FULL run re-attached to its directory, was found
        # complete, and had output with no tophat images fetched back as its
        # own — and in the other direction, after a batched run on a host with
        # overrides, the two never matched and re-attach silently never fired.
        _record_remote_in_journal(
            local_output_dir, run=runner._run, host_alias=runner.host.ssh_alias,
            argv_digest=pipeline_argv_digest(
                pipeline_argv,
                extras=runner._host_argv_extras(output_filter)),
            source_key=source_fingerprint(
                _local_input_dir_from_argv(pipeline_argv)),
        )

        # One Fetcher persisting across batches. Pre-set the total well
        # count: each batch's pipeline banner reports just THAT
        # batch's count, which would corrupt the cross-batch progress
        # math if the Fetcher trusted the per-batch banner.
        self._fetcher = Fetcher(
            runner.host, runner._run,
            output_filter=output_filter,
            log_fn=log_fn, progress_fn=progress_fn,
            delete_after_download=True,
            poll_sidecar=True,
        )
        # ``well_count_total`` is set in ``_run_batches`` AFTER the
        # local-completion filter runs (a pre-filter set would lock
        # in the raw count and the GUI's progress denominator would
        # be wrong when some wells were already-done locally).

        self._worker = threading.Thread(
            target=self._run_batches, daemon=True,
        )
        self._worker.start()

    # Popen-compatible surface ─────────────────────────────────────────

    @property
    def pid(self) -> int:
        with self._current_proc_lock:
            proc = self._current_proc
        return proc.pid if proc else -1

    @property
    def returncode(self) -> int | None:
        return self._returncode

    @property
    def stdout(self) -> _QueueLineIterator:
        return self._lines

    def poll(self) -> int | None:
        if self._worker.is_alive():
            return None
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            # Timed out; the worker is still running — not a completion, so
            # report the current (possibly None→0) state without inventing a
            # success code.
            return self._returncode if self._returncode is not None else 0
        # The worker finished. A ``None`` code here means it crashed before
        # setting one — surface that as a failure rather than coercing it to 0
        # (which previously made a crashed batched run look successful and
        # triggered verify_and_cleanup to delete remote data — audit E1).
        if self._returncode is None:
            self._returncode = 1
        return self._returncode

    def terminate(self) -> None:
        self._stop.set()
        with self._current_proc_lock:
            proc = self._current_proc
        if proc is not None:
            proc.terminate()
        self._fetcher.cancel()

    def kill(self) -> None:
        self._stop.set()
        with self._current_proc_lock:
            proc = self._current_proc
        if proc is not None:
            proc.kill()
        self._fetcher.cancel()

    def send_signal(self, sig: int) -> None:
        with self._current_proc_lock:
            proc = self._current_proc
        if proc is not None:
            proc.send_signal(sig)

    def note_suspend_requested(self) -> None:
        """Finish the batch in flight and send no more.

        :meth:`RemoteRunner.request_suspend` has already written the request
        file, which winds the running batch down and stops the driver from
        starting another; this is the local half, which stops *uploading* them.
        The uploads are the expensive part of a batch, and one sent after the
        user asked for the machine back would be thrown away unrun.
        """
        self._suspending.set()

    def _suspend_before(self, batch_no: int, n_batches: int) -> bool:
        """Whether a suspend means batch *batch_no* is not to be sent."""
        if not self._suspending.is_set():
            return False
        self._log(
            f"[remote] suspended — batch {batch_no}/{n_batches} and any after "
            f"it were not sent; resume to process them\n")
        return True

    # Internals ────────────────────────────────────────────────────────

    def _batch_flag(self, template: str, index: int) -> str:
        """The remote path of one batch's handshake file."""
        run_dir = remote_run_dir(self._runner.host, self._run_id)
        return f"{run_dir}/{template.format(i=index)}"

    def _batch_staging_dir(self, index: int) -> str:
        """Where batch *index* is uploaded, before the driver swaps it in."""
        run_dir = remote_run_dir(self._runner.host, self._run_id)
        return f"{run_dir}/b{index}"

    def _start_driver(self, n_batches: int) -> bool:
        """Put one allocation on a node and leave it there for the whole run.

        Every batch used to be its own ``srun``, so a scheduler-backed host
        queued once per batch and handed the node back in between. The driver
        holds it and takes the batches as they arrive
        (:func:`_batch_driver_snippet`).

        The ``--time`` in the launcher is scaled here, because it sized one
        batch and now has to cover the plate. The user's own launcher is never
        rewritten on disk — only the copy this run hands to the scheduler.
        """
        run = self._runner._run
        host = self._runner.host
        paths = remote_run_paths(host, self._run_id)
        run_dir = paths.run_dir
        heartbeat = paths.heartbeat

        # Ask *before* clearing anything. Everything below assumes this run
        # directory is ours to empty, and `run_id` is deterministic in the
        # plate and the arguments, so a second session on the same plate lands
        # on the same path — its `rm -rf` would take the first session's staged
        # batches and input directory out from under a running pipeline.
        if self._runner._sweep_orphaned_allocation(
                run.remote_pidfile, heartbeat, log_fn=self._log) == "owned":
            self._log(
                "[remote] ERROR another session is already running this exact "
                "dataset and configuration on this host. Both would use the "
                "same remote run directory, so this run stops here rather "
                "than corrupting the one already going.\n")
            self._returncode = 1
            return False

        # Clear any handshake files a previous run of this same dataset left:
        # `run_id` is deterministic, so the driver would otherwise find batch 1
        # already flagged ready and start against an empty input directory.
        self._runner._cache._ssh_run(["mkdir", "-p", run_dir], check=False)
        self._runner._cache._ssh_run(
            ["rm", "-f", paths.suspend_request]
            + [self._batch_flag(t, i)
               for i in range(1, n_batches + 1)
               for t in (BATCH_READY, BATCH_DONE)],
            check=False,
        )
        # …and any batch a previous, aborted run of this same dataset staged.
        # `run_id` is deterministic, so those directories are still sitting
        # there and the driver would swap one in as though it were ours.
        self._runner._cache._ssh_run(
            ["rm", "-rf", f"{run_dir}/in", f"{run_dir}/bg"]
            + [self._batch_staging_dir(i) for i in range(1, n_batches + 1)],
            check=False,
        )

        # The empty-well blank(s), uploaded once for the whole run. They sit
        # outside the batch staging so the driver's `rm -rf in && mv b<i> in`
        # never touches them, and every batch builds its correction from the
        # same reference. Unconditional: a blank whose CSV is already complete
        # locally is skipped by the batch filter, which on a re-run would
        # otherwise leave the whole plate uncorrected.
        blanks = [z for z in self._zipped.well_zips
                  if z.stem in getattr(self, "_blank_stems", ())]
        if blanks:
            sent, _elapsed, rc = self._runner._cache.upload_subset(
                blanks, f"{run_dir}/bg", log_fn=self._log,
                # A blank is a full well, and it is the first large transfer of
                # the run — without a progress callback the plate-wide
                # "Transferring…" label sits still for its whole duration.
                progress_fn=self._progress, stop_event=self._stop)
            if rc != 0:
                if self._stop.is_set():
                    # `upload_subset` watches `_stop`, so a Stop comes back here
                    # as a non-zero rsync code. Reporting the user's own Stop as
                    # a fault — with rsync's number rather than 130 — is what
                    # the batch-upload path below already avoids.
                    self._log("[remote] stopped while uploading the empty-well "
                              "reference\n")
                    self._returncode = 130
                    return False
                self._log(
                    f"[remote] ERROR could not upload the empty-well blank(s) "
                    f"(rsync rc={rc}). Every batch would run without the "
                    f"background correction this run asked for, and the output "
                    f"would not show it — stopping instead.\n")
                self._returncode = rc
                return False
            self._log(f"[remote] empty-well reference uploaded once "
                      f"({sent / (1024**2):.1f} MB) → {run_dir}/bg\n")
            # A blank uploaded as a reference only was already marked complete
            # by its SKIP marker; the upload's own per-well line then repaints
            # its cell as "uploading" and nothing ever moves it off, because
            # the well is never processed or downloaded. Settle it again.
            for stem in self._blank_stems:
                if stem not in getattr(self, "_link_blank_stems", ()):
                    self._line_q.put(
                        f"SKIP well {stem.upper()} — empty-well reference only; "
                        f"already complete locally.\n")

        # The heartbeat runs for the whole run, not just one upload: the driver
        # checks it at every batch boundary, so an app that dies between
        # batches gives the node back instead of parking it on a `ready` file
        # nobody will ever write.
        self._heartbeat = _UploadHeartbeat(self._runner._cache, heartbeat)
        self._heartbeat.beat()
        self._heartbeat.start()

        remote_argv = self._runner._build_remote_argv(
            self._pipeline_argv, run.remote_input_dir, run.remote_output_dir,
            self._output_filter, self._zipped.mode,
            background_dir=f"{run_dir}/bg" if blanks else "",
        )
        # Each batch still sees only its own slice, so the in-pipeline
        # auto-threshold pass would write defaults from a fraction of the plate
        # — it is suppressed here and re-run once locally over everything.
        if "--no_auto_threshold" not in remote_argv:
            remote_argv = list(remote_argv) + ["--no_auto_threshold"]
        remote_argv = list(remote_argv) + ["--suspend_file", paths.suspend_request]
        run.suspend_request = paths.suspend_request

        # The budget for one batch is what the operator wrote before this
        # change: their --time sized a single launch.
        per_batch_s = launcher_time_seconds(host.compute_launcher)
        launcher, note = scale_launcher_time(host.compute_launcher, n_batches)
        if note:
            self._log(note)
        # One hand-off for the run rather than one per batch — there is one
        # queue wait now, and this is the line that says it is happening.
        self._log(_scheduler_handoff_lines(launcher))

        driver = _batch_driver_snippet(run_dir, n_batches, remote_argv,
                                       heartbeat, per_batch_s,
                                       link_blanks=(
                                           getattr(self, "_link_blank_stems", ())
                                           if blanks else ()),
                                       suspend_request=paths.suspend_request)
        ssh_argv = host.ssh_argv(allocate_tty=True) + [
            _compose_driver_cmd(launcher, run.remote_pidfile, driver,
                                env_selector=self._runner.env_selector,
                                scratch_dir=host.remote_scratch_dir)
        ]
        proc = subprocess.Popen(
            ssh_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # The remote (Linux) pipeline logs UTF-8 — including the em-dash in
            # "Well X — done." Decode as UTF-8 so those markers survive on a
            # cp1252 Windows client; otherwise the dashboard's per-well Process
            # tracking never matches.
            **TEXT_UTF8,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=(os.name == "posix"),
        )
        with self._current_proc_lock:
            self._current_proc = proc
        self._n_batches = n_batches
        # From here on there is an allocation of ours to hand back, which is
        # what `_release_driver` acts on. Set only after the launch: an abort
        # before it would otherwise `scancel` a sidecar written by a *previous*
        # run of this same dataset, or by the other session that made us stop.
        self._owns_allocation = True
        self._reader = threading.Thread(
            target=self._read_driver, args=(proc,), daemon=True,
            name="aw-batch-driver-reader")
        self._reader.start()
        return True

    def _release_driver(self) -> None:
        """Stop the heartbeat and hand back an allocation nobody is feeding.

        Called from ``_run_batches``' ``finally``, so it covers the ends the
        old per-batch loop never had to think about: an upload that failed
        before the first batch, a driver that died, a Stop. The job may still
        be *queued* at this point, in which case its ``.slurm`` sidecar does
        not exist yet and ``close_remote_job`` has nothing to read — so the ssh
        that is holding the queue position is terminated directly as well. A
        suspend that ended the loop counts as such an end while the driver is
        still alive: a job still queued would otherwise take the node later,
        see the request file and hand it straight back.
        """
        # Read through `getattr`: this runs in a ``finally``, so it also runs
        # for a crash that happened before `_start_driver` ever set any of it.
        heartbeat = getattr(self, "_heartbeat", None)
        self._heartbeat = None
        if heartbeat is not None:
            with swallow("stopping the upload heartbeat"):
                heartbeat.stop()
        if not getattr(self, "_owns_allocation", False):
            return          # never launched: nothing here is ours to cancel
        lock = getattr(self, "_current_proc_lock", None)
        if lock is None or getattr(self, "_runner", None) is None:
            return          # nothing was ever launched
        with lock:
            proc = self._current_proc
        # A clean run's driver exits on its own; after a suspend a queued one has not.
        if self._returncode in (0, None) and not (
                self._suspending.is_set() and proc is not None and proc.poll() is None):
            return
        if proc is not None:
            self._log("[remote] releasing the allocation\n")
            with swallow("terminating the batch driver"):
                if proc.poll() is None:
                    proc.terminate()
        with swallow("cancelling the scheduler job"):
            self._runner.close_remote_job(log_fn=self._log)

    def _read_driver(self, proc: subprocess.Popen) -> None:
        """Tee the driver's output, and note where each batch ended.

        One reader for the whole run, where the old code teed inline inside
        each batch's own loop. It also watches for the driver's per-batch
        marker, which is how the batch loop learns a batch is finished: the
        stream is already being read, so that costs no extra ssh and has no
        poll interval to wait out.
        """
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                m = _BATCH_DONE_RE.search(line)
                if m:
                    # Consumed, not forwarded: this is the handshake between the
                    # driver and this loop, and `ProgressTracker` has no reason
                    # to see it.
                    index, rc = int(m.group(1)), int(m.group(2))
                    self._batch_rc[index] = rc
                    self._batch_events.setdefault(
                        index, threading.Event()).set()
                    continue
                self._fetcher.observe(line)
                self._line_q.put(line)
        except Exception:
            swallowed("batch driver reader")
        finally:
            # Whatever ended the stream — the driver finishing, the node dying,
            # a Stop killing the ssh — every batch still waiting has to be
            # released or the loop would block for ever.
            self._stream_ended.set()
            # Batches still unreported means the driver did not finish. This is
            # the only place that learns of it: a Stop reaches this object as a
            # kill of the ssh's process group (`signal_subprocess` uses
            # `proc.pid`, which is the driver's, so `terminate` never runs),
            # and a walltime expiry or a node failure never touches the app at
            # all. Without this the worker thread would sit inside the next
            # batch's rsync — `upload_subset` takes `_stop` and watches it —
            # pushing wells at a node that no longer exists.
            # A suspend is the one deliberate early exit the driver has
            # (`_batch_driver_snippet`), and reading it as a lost node would
            # cancel the Fetcher mid-way through the batch that just finished —
            # the wells the suspend was meant to keep.
            if (not self._stop.is_set() and not self._suspending.is_set()
                    and len(self._batch_rc) < self._n_batches):
                self._driver_lost.set()
                self._stop.set()
                with swallow("cancelling downloads after the driver went away"):
                    self._fetcher.cancel()
            # A snapshot: `_await_batch` may be adding an entry from the worker
            # thread at this instant, and iterating the live dict would raise
            # inside a ``finally``. `_stream_ended` is set first, so a batch
            # whose event is created after this still stops within the poll.
            for event in list(self._batch_events.values()):
                event.set()

    def _await_batch(self, index: int, n_batches: int) -> int | None:
        """This batch's exit code, or ``None`` if the driver went away."""
        event = self._batch_events.setdefault(index, threading.Event())
        while not event.wait(timeout=1.0):
            if self._stop.is_set() or self._stream_ended.is_set():
                break
        if index in self._batch_rc:
            return self._batch_rc[index]
        # No code came back. Either the user stopped the run, or the allocation
        # is gone — a walltime expiry, a node failure, somebody else's scancel.
        # `_driver_lost` is checked first because the reader sets `_stop` in
        # that case too, to get the worker out of whatever it is blocked in.
        if self._driver_lost.is_set():
            self._log(
                f"[remote] the allocation ended during batch {index}/"
                f"{n_batches} — no more batches will run\n")
            self._returncode = self._returncode or 1
        else:
            self._log(f"[remote] stopped during batch {index}/{n_batches}\n")
            self._returncode = 130
        return None

    def _run_batches(self) -> None:
        try:
            all_zips = list(self._zipped.well_zips)
            # The whole staged plate — the denominator the GUI's progress
            # headline is measured against. Wells dropped below because their
            # output is already complete locally stay in this count: they are
            # part of what the user asked for, and a denominator that shrinks
            # to the subset actually uploaded lets the headline read "N / N
            # wells (100%)" over a plate that still has wells with no output.
            staged_total = len(all_zips)
            skipped: list[Path] = []

            # Worked out before the local-completion filter below, from the
            # unfiltered plate. The blank is needed as *input to the
            # correction*, not for its own output, so a re-run that legitimately
            # skips it for processing must still upload it — otherwise the
            # filter silently leaves the whole plate uncorrected, which is the
            # worse half of the defect this path exists to fix.
            self._blank_stems = _empty_well_blanks(self._pipeline_argv, all_zips)
            blank_zips = [z for z in all_zips if z.stem in self._blank_stems]

            # v5.4.7: local-completion filter. The remote pipeline's
            # own skip check (``output_exists_for_well``) looks at the
            # REMOTE output dir — but per-well Fetcher cleanup deletes
            # outputs there as soon as they land locally, so a batched
            # re-run never sees a cache hit on the remote side. Instead
            # check the LOCAL ``output_dir`` up front.
            #
            # v5.4.8: relaxed from "both ``{well}_out.zip`` AND the CSV"
            # to "CSV only". The zip's local presence depends on the
            # output mode the Fetcher used to materialize it:
            #
            # * ZIP / FLAT mode → ``shutil.copyfile`` lands the zip
            #   verbatim at ``local_output_dir/{well}_out.zip``.
            # * FOLDER mode → ``zipfile.extractall`` extracts the zip
            #   contents (loose QC files) and the zip itself is
            #   discarded.
            # * CSV_ONLY mode → the zip is dropped on the floor
            #   entirely.
            #
            # So the zip exists locally in only ONE of three Fetcher
            # paths — requiring it broke the filter for the other two
            # and the user saw "wells were re-processed even though
            # output already existed". The per-well CSV is written by
            # the pipeline regardless of mode and pulled by the
            # Fetcher unconditionally, so it's the canonical
            # "this well is done" signal.
            #
            # ``--force`` in the pipeline argv short-circuits the
            # filter (matches the single-shot path and
            # ``process_microscopy.py``'s own ``force`` semantics).
            force = "--force" in self._pipeline_argv
            csv_prefix = "measurements"
            it = iter(self._pipeline_argv)
            for arg in it:
                if arg == "--csv_prefix":
                    csv_prefix = next(it, csv_prefix)
                    break
            # Incremental already split client-side: the staged set holds only
            # wells with genuinely-new FOVs (up-to-date wells were dropped during
            # staging), and each kept zip carries only its new-FOV members. The
            # local-CSV "already complete" skip would wrongly drop these touched
            # wells (they DO have a local CSV), so bypass it for incremental.
            if not force and not self._zipped.incremental:
                kept: list[Path] = []
                for p in all_zips:
                    well = p.stem.upper()
                    csv_path = self._local_output_dir / f"{csv_prefix}_{well}.csv"
                    if csv_path.exists() and csv_path.stat().st_size > 0:
                        skipped.append(p)
                    else:
                        kept.append(p)
                if skipped:
                    self._log(
                        f"[remote] batched mode: {len(skipped)} well(s) "
                        f"already complete locally (per-well CSV present "
                        f"under {self._local_output_dir.name}/) — skipping "
                        f"upload + compute. Pass --force to reprocess.\n"
                    )
                    if len(skipped) <= 8:
                        self._log(
                            "[remote] skipped wells: "
                            + ", ".join(p.stem for p in skipped)
                            + "\n"
                        )
                    all_zips = kept

            # Inject a synthetic "Zip mode: <total> wells" banner BEFORE
            # batch 1's stdout. The GUI's ``ProgressTracker`` locks on the
            # first such banner via ``if not self.well_total:``; without this,
            # each batch's own banner reports just THAT batch's count (e.g.
            # 50) and the run's progress is measured against one batch. We
            # always say "Zip mode" because the remote pipeline sees zips on
            # disk regardless of the local input mode the user started from.
            # The "— batched" suffix is informational only; the regex
            # ``\s+well`` matches both "well" and "wells" so the parenthetical
            # doesn't disturb the match.
            #
            # The count is the whole staged plate, including the wells skipped
            # above. Each of those then gets its own terminal marker in the
            # same stream, so it is counted and painted on the plate map
            # instead of sitting pending for the length of the run while the
            # headline counts only the wells that were uploaded.
            self._line_q.put(f"Zip mode: {staged_total} wells — batched\n")
            for p in skipped:
                self._line_q.put(
                    f"SKIP well {p.stem.upper()} — output already exists "
                    f"locally; not uploaded. Use --force to reprocess.\n"
                )

            if not all_zips:
                self._log(
                    "[remote] batched mode: every well already complete "
                    "locally — nothing to do.\n"
                )
                self._fetcher.run.well_count_total = 0
                self._returncode = 0
                return

            self._fetcher.run.well_count_total = len(all_zips)

            # Empty-well correction reads the blank from the input directory at
            # model-build time, and batched mode stages one batch at a time — so
            # a blank left in the ordinary slicing corrects the batch it lands
            # in and nothing else. It is held out here, uploaded once to its own
            # directory beside the batches, and named to the pipeline with
            # `--background_dir`. A blank is a full well of uncompressed frames,
            # the same size as any other, so uploading it per batch is not an
            # option: it crosses the wire exactly once.
            sliceable = [z for z in all_zips if z.stem not in self._blank_stems]
            # Holding the blank out only makes sense when there is something
            # else to run. With nothing but blanks left — every sample well
            # complete locally — an empty batch would be uploaded to a directory
            # that is never created, the driver's `mv` would fail, and the run
            # would end 90 without processing the blank at all. In that case the
            # blank is just an ordinary well: it corrects itself, exactly as a
            # single-shot run of it alone would.
            blanks = blank_zips if sliceable else []
            if not sliceable:
                sliceable = list(all_zips)
                # `_start_driver` reads these to decide the `bg/` upload, so
                # clearing them is what keeps the fallback to a single upload
                # rather than sending the blank to both `bg/` and its batch.
                self._blank_stems = ()
            # Which blanks still need processing — per well, not one flag for
            # all of them. With `--background_well G12,H12` where only G12 is
            # complete locally, linking the whole of `bg/` back in would
            # reprocess the well the user's own re-run just declined.
            self._link_blank_stems = tuple(
                z.stem for z in all_zips if z.stem in self._blank_stems)
            if blanks:
                self._log(
                    f"[remote] empty-well correction: {len(blanks)} blank "
                    f"well(s) ({', '.join(sorted(self._blank_stems))}) upload "
                    f"once, outside the batches, so every batch corrects "
                    f"against the same reference"
                    + ("" if self._link_blank_stems else
                       " (already complete locally, so uploaded as a reference "
                       "only)") + "\n"
                )

            # `sliceable` cannot be empty here: an all-complete plate returned
            # above, and a plate of nothing but blanks fell back to treating them
            # as ordinary wells. The `or [[]]` that used to guard this produced
            # exactly the empty batch that ended a run at 90.
            batches = [
                sliceable[i:i + self._batch_size]
                for i in range(0, len(sliceable), self._batch_size)
            ]
            n_batches = len(batches)
            self._log(
                f"[remote] batched mode: {len(all_zips)} well(s) in "
                f"{n_batches} batch(es) of up to {self._batch_size} "
                f"(peak remote disk ≈ {self._batch_size + len(blanks)} "
                f"well-input)\n"
            )

            if self._stop.is_set():
                # Before anything reaches the remote. A Stop that arrives while
                # the plate is still being planned must not leave an allocation
                # behind, and the tests that drive this method with a pre-set
                # stop event build the object without a `_runner` at all — so
                # this ordering is load-bearing twice over.
                self._log("[remote] stopped before the first batch\n")
                self._returncode = 130
                return

            if not self._start_driver(n_batches):
                return
            failed: dict[int, int] = {}

            for batch_idx, batch_zips in enumerate(batches):
                if self._suspend_before(batch_idx + 1, n_batches):
                    break
                if self._stop.is_set():
                    self._log(f"[remote] stopped before batch {batch_idx + 1}/{n_batches}\n")
                    self._returncode = 130
                    return

                self._log(
                    f"[remote] batch {batch_idx + 1}/{n_batches}: "
                    f"uploading {len(batch_zips)} well-zip(s)\n"
                )
                # v5.4.7: scale per-batch upload progress so the
                # status label advances smoothly across the WHOLE
                # run instead of resetting from 0 → 1 once per
                # batch. Each batch's rsync calls progress_fn with
                # ``pct`` in [0, 1]; map to
                # ``(batch_idx + pct) / n_batches`` so batch 1's
                # uploads animate 0 → 1/n_batches, batch 2's
                # 1/n_batches → 2/n_batches, etc.
                _outer_progress = self._progress
                if _outer_progress is not None:
                    def _scaled_progress(
                        pct: float,
                        _idx: int = batch_idx,
                        _n: int = n_batches,
                        _outer=_outer_progress,
                    ) -> None:
                        _outer((_idx + pct) / _n)
                    inner_progress: Callable[[float], None] | None = _scaled_progress
                else:
                    inner_progress = None
                sent_bytes, _elapsed, rc = self._runner._cache.upload_subset(
                    batch_zips,
                    self._batch_staging_dir(batch_idx + 1),
                    log_fn=self._log,
                    progress_fn=inner_progress,
                    # A suspend ends the upload too: a batch sent after the
                    # user asked for the machine back is thrown away unrun.
                    stop_event=_AnySet(self._stop, self._suspending),
                )
                if rc != 0:
                    # `upload_subset` watches `_stop`, so a Stop mid-transfer
                    # comes back here as a non-zero rsync code. Saying "upload
                    # failed" for a stop the user asked for reads as a fault.
                    if self._stop.is_set():
                        self._log(
                            f"[remote] batch {batch_idx + 1}/{n_batches}: "
                            f"upload interrupted\n")
                        self._returncode = (
                            1 if self._driver_lost.is_set() else 130)
                        return
                    if self._suspend_before(batch_idx + 1, n_batches):
                        break
                    self._log(
                        f"[remote] batch {batch_idx + 1} upload failed "
                        f"(rsync rc={rc}); aborting remaining batches\n"
                    )
                    self._returncode = rc
                    return
                if self._suspend_before(batch_idx + 1, n_batches):
                    break
                self._log(
                    f"[remote] batch {batch_idx + 1}: upload complete "
                    f"({sent_bytes / (1024**2):.1f} MB); handing it to the "
                    f"driver\n"
                )
                # The pipeline for this batch runs inside the allocation the
                # driver is already holding — there is no second queue wait and
                # no second argv to build. Hand it the batch and wait for the
                # code it reports back.
                self._runner._cache._ssh_run(
                    ["touch", self._batch_flag(BATCH_READY, batch_idx + 1)],
                    check=False,
                )
                rc = self._await_batch(batch_idx + 1, n_batches)
                if rc is None:
                    # The driver is gone. Nothing further will ever be run, and
                    # whether that was a Stop or a dead node the outcome is the
                    # same for the batches left.
                    return
                if rc != 0:
                    # Kept, not fatal: one bad well should not cost the plate
                    # the node it already holds. The tally after the loop names
                    # every batch that did this, and the run's own code carries
                    # the first of them.
                    failed[batch_idx + 1] = rc
                    self._log(
                        f"[remote] batch {batch_idx + 1} pipeline exited {rc}; "
                        f"continuing with the remaining batches\n"
                    )
                else:
                    self._log(f"[remote] batch {batch_idx + 1}/{n_batches} complete\n")

            if failed:
                names = ", ".join(str(i) for i in sorted(failed))
                self._log(
                    f"[remote] {len(failed)} of {n_batches} batch(es) failed "
                    f"({names}); the rest completed. Wells from a failed batch "
                    f"are missing from the output.\n"
                )
                self._returncode = failed[min(failed)]
            else:
                self._returncode = 0
        except BaseException as exc:
            # A crash anywhere in the batch loop (ssh spawn failure, argv
            # build, upload, stdout iteration) must NOT leave ``_returncode``
            # at None — that made ``wait()`` report success and
            # verify_and_cleanup delete remote data though nothing ran
            # (audit E1). Record a non-zero code and surface the error in the
            # GUI log; the ``finally`` below still drains the fetcher and, now
            # that rc != 0, skips the auto-threshold pass.
            import logging as _logging
            self._returncode = 1
            self._log(f"[remote] batched run failed: {exc}\n")
            _logging.getLogger(__name__).exception("batched remote run crashed")
        finally:
            # Whatever ended the run, the beat has to end with it: the driver
            # reads a fresh heartbeat as "the app is alive and the next batch is
            # coming", so one left running holds the node for the full deadline
            # and keeps `_sweep_orphaned_allocation` from ever sweeping.
            self._release_driver()
            # The stdout sentinel MUST be enqueued no matter what — if
            # flush_and_join (or the auto-threshold pass) raises, the reader
            # loop in pipeline_runner would otherwise block forever waiting on
            # a None that never arrives (audit X10). Keep it in an inner
            # ``finally`` so it's still emitted last on the happy path (after
            # the fetcher's progress lines) but guaranteed on any failure.
            try:
                # Drain the Fetcher first so every well's CSV + zip lands
                # locally BEFORE we attempt auto-threshold against
                # local_output_dir.
                self._fetcher.flush_and_join()
                # v5.4.7: single auto-threshold pass over the FULL downloaded
                # output. The per-batch passes were suppressed via
                # ``--no_auto_threshold`` so a batch wouldn't write thresholds
                # derived from a sliver of the plate. Fires only when the whole
                # run succeeded — a mid-run abort or a suspend leaves the
                # previous run's pipeline_info.json (if any) untouched; the
                # resumed run that finishes the plate writes the thresholds.
                if (self._returncode == 0 and not self._suspending.is_set()
                        and not self._auto_threshold_off_by_user()):
                    self._run_local_auto_threshold()
            finally:
                self._line_q.put(None)

    def _auto_threshold_off_by_user(self) -> bool:
        """True when the operator unchecked "Calculate cell-gating thresholds".

        ``--no_auto_threshold`` reaches the remote argv for two unrelated
        reasons: the user's choice, and this class appending it per batch so a
        batch cannot write thresholds derived from a sliver of the plate. Once
        appended the two are indistinguishable — which is why the local re-run
        used to write thresholds for someone who had asked for none. Read from
        ``self._pipeline_argv``, the argv as handed in: ``_run_batches`` builds
        each batch's ``remote_argv`` from a copy and never mutates this one, so
        it still answers only the first question.
        """
        return "--no_auto_threshold" in list(self._pipeline_argv or [])

    def _run_local_auto_threshold(self) -> None:
        """Re-run the pipeline's auto-threshold pass locally against
        the full ``local_output_dir`` after every batch has drained.
        Failures are logged but non-fatal (matches the remote
        pipeline's "informational only" stance on auto-threshold).

        The pass reports itself through the ``logging`` module — "Auto-threshold:
        scanning N well(s)", then "completed well i/N (WELL)" as it goes — and
        the GUI parses exactly those strings off the run's line stream to drive
        the Threshold stage. In a *subprocess* run they reach it because the
        pipeline's stdout is that stream. Here the pass runs in-process, so its
        records went to a logger nobody was reading and the only things on the
        line stream were this method's own two bookends: one line as it started
        and one when it finished. Over a full plate that is a long silence right
        at the end of a run, with the rail sitting still. Forwarding the records
        onto the same stream closes it, and the regexes that were always there
        start matching."""
        try:
            # Late import: ``process_microscopy`` is a heavy module
            # (pulls TensorFlow indirectly). Don't pay that cost on
            # every import of remote_runner.
            sys.path.insert(0, str(repo_root()))
            from process_microscopy import run_auto_threshold_pass
            import logging
            log_pylog = logging.getLogger("allwell.local_auto_threshold")
            self._log(
                "[remote] running auto-threshold pass locally over "
                f"{self._local_output_dir} (full plate, all batches drained)\n"
            )
            args = _auto_threshold_args_from_argv(self._pipeline_argv)
            forwarder = _LogToLineStream(self._log)
            log_pylog.addHandler(forwarder)
            log_pylog.setLevel(logging.INFO)
            log_pylog.propagate = False
            try:
                written = run_auto_threshold_pass(
                    args,
                    self._local_output_dir,
                    quantified_channel_tokens(
                        args.fluor_tokens,
                        nuclear_token=args.nuclear_token,
                        segmentation_method=args.segmentation_method,
                        cytoplasm_token=args.cytoplasm_token,
                    ),
                    log_pylog,
                )
            finally:
                log_pylog.removeHandler(forwarder)
            if written:
                self._log(
                    "[remote] local auto-threshold defaults written for "
                    f"{len(written)} channel(s): "
                    + ", ".join(f"{ch.upper()}={v:.4g}" for ch, v in written.items())
                    + "\n"
                )
            else:
                self._log(
                    "[remote] local auto-threshold pass produced no new defaults.\n"
                )
        except Exception as exc:  # informational, never fatal
            self._log(
                f"[remote] warn: local auto-threshold pass failed: {exc}\n"
            )


class _QueueLineIterator:
    """Iterator that pulls lines off a queue; sentinel ``None`` ends
    iteration. Lets the Popen consumer treat us like a file."""

    def __init__(self, q: queue.Queue[str | None]):
        self._q = q

    def __iter__(self) -> _QueueLineIterator:
        return self

    def __next__(self) -> str:
        item = self._q.get()
        if item is None:
            raise StopIteration
        return item


# ────────────────────────────────────────────────────────────────────────────
# Fetcher
# ────────────────────────────────────────────────────────────────────────────





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
        # Which conda env the pipeline runs in, resolved once from the host's
        # "Remote python" setting. Both the argv builder and the shell composer
        # read it, so they can never disagree about which env is being used.
        self.env_selector, self._env_warning = _conda_env_selector(
            host.remote_python)
        self._run: _RemoteRun | None = None
        self._cache: DatasetCache = DatasetCache(host)
        # The Fetcher created in spawn(), kept so cancel() can interrupt an
        # in-flight per-well download, and a stop flag the end-of-run retry
        # backstop polls. Without these, Stop did nothing while the final
        # per-well rsync was in flight / retry-looping.
        self._fetcher: Fetcher | None = None
        self._cancelled: threading.Event = threading.Event()

    @property
    def run(self) -> _RemoteRun | None:
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
        layout is preserved on the remote (so ``requirements.txt``
        lands at ``{remote_project_dir}/requirements.txt``).
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
        self._cache._ssh_run(
            ["mkdir", "-p", mk_target, *mkdirs],
            check=True,
        )

        # v5.2.12: use bare relative source paths with cwd=root so
        # rsync's -R preserves only the path BELOW the repo root.
        #
        # Previously we used the absolute-path-with-`/./`-pivot trick:
        #   sources = ["/Users/me/allwell/./process_microscopy.py", ...]
        # which is supposed to make rsync use everything after /./ as
        # the relative path. Older rsync (macOS bundles 2.6.9) doesn't
        # honor that pivot reliably and mirrors the WHOLE local path
        # on the remote, so the user saw:
        #   ~/allwell/Users/me/allwell/requirements-remote.txt
        # instead of the expected:
        #   ~/allwell/requirements-remote.txt
        #
        # Using cwd + bare relative sources works on every rsync
        # version we've seen. rsync's -R then preserves just the
        # relative path from cwd (`requirements-remote.txt`)
        # rather than the absolute prefix.
        sources = list(_REQUIRED_REMOTE_FILES)
        dest = f"{self.host.ssh_alias}:{self.host.remote_project_dir}/"
        cmd = [
            resolve_rsync_binary(),
            *_rsync_ssh_transport(self.host),
            "-aR",
        ]
        # v5.2.10: --info=stats0 (suppresses summary footer) needs
        # rsync ≥ 3.1.0. Old rsync (macOS 2.6.9, older HPC images)
        # rejects with exit 1. Omitting it is purely cosmetic — rsync
        # just prints its stats summary to stdout, which we don't
        # parse or display anyway.
        if _rsync_supports_info_flag():
            cmd.append("--info=stats0")
        cmd += [*sources, dest]
        result = _rsync_with_retry(
            cmd, log_fn=log_fn, timeout=60, cwd=str(root),
        )
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
    ) -> RunEstimate:
        """How much this run has to upload, and how long that should take.

        Both are measurable here: the staged archives' sizes, and the host's
        measured bandwidth. Nothing about compute time is — see
        :class:`RunEstimate` for why the local-vs-remote verdict this used to
        end with was removed rather than improved.
        """
        total_bytes = sum(p.stat().st_size for p in zipped.well_zips)
        bw_mbps = max(self.host.measured_bandwidth_mbps, 1.0)
        bps = bw_mbps * 1_000_000 / 8
        transfer_s = (total_bytes / bps) if not is_cache_hit else 0.0
        return RunEstimate(bytes_to_transfer=total_bytes, transfer_s=transfer_s)

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
        batch_size: int = 0,
    ):
        """Stage inputs, build the remote command, launch ssh.

        ``pipeline_argv`` is the result of
        :func:`services.pipeline_service.build_pipeline_args` — i.e. the
        same argv we'd pass locally. We rewrite the two path args and
        append the filter flags before launching remotely.

        ``batch_size`` controls remote-disk peak usage:

        * ``0`` (default) — upload every well-zip up front, run the
          pipeline once against the whole plate. Lowest total runtime
          (StarDist loads once, full parallelism inside the pipeline).
          Peak remote disk = ``(all inputs) + (all outputs accumulated
          before fetch)``.
        * ``> 0`` — split into batches of this many wells, fed one at a
          time to a driver that holds a single allocation for the whole
          run: upload → hand it to the driver → wait for its exit code →
          next batch. The Fetcher deletes each well's input + output zip
          on the remote as soon as the local download lands, so peak disk
          ≈ ``batch_size`` wells of input + ~1 well of output. Tradeoff:
          the pipeline starts once per batch, so StarDist reloads each
          time (~5–10 s per batch). Pick ``batch_size`` based on your
          remote's free disk; the smaller it is, the more loads and the
          lower the peak.

        Returns a Popen-compatible handle the GUI's stdout reader can
        consume unchanged.
        """
        log = log_fn or (lambda _m: None)

        # Pipeline code sync runs for either path.
        self.sync_required_files(log_fn=log)

        if batch_size > 0:
            log(
                f"[remote] batched mode enabled: batch_size={batch_size}\n"
            )
            return _BatchedRemoteProcess(
                self,
                zipped,
                local_output_dir,
                pipeline_argv,
                batch_size,
                output_filter=output_filter,
                log_fn=log_fn,
                progress_fn=progress_fn,
                stop_event=stop_event,
            )

        # Single-shot path (the v5.4.5 and earlier behavior, byte-
        # identical apart from the moved sync_required_files() call).
        # Cache budget enforcement before each run.
        self._cache.gc(self.host.cache_budget_gb * (1024**3), log_fn=log)

        # ``sync_required_files`` ran above (covers both single-shot
        # and batched paths). Cheap — ~300 KB of source plus rsync's
        # delta-skip when nothing changed.

        # The cache key is a local computation over the input archives, so the
        # remote paths — and therefore the run directory the scheduled job will
        # wait in — are known before a single byte moves. That is what lets the
        # allocation be asked for first; see the upload below.
        key = self._cache.cache_key(zipped)

        # v5.2.25: deterministic run_id keyed on the input cache_key
        # + pipeline-args hash, so re-running the same input with the
        # same config lands in the same remote output dir. Combined
        # with ``cleanup_mode=none`` (the default), the pipeline's
        # own ``output_exists_for_well`` logic then skips wells whose
        # ``{well}_out.zip`` already exists from a prior run — making
        # re-runs cheap. The ``[run-]`` prefix marks them visually
        # different from the timestamp-style dirs older builds left
        # on the remote.
        host_extras = self._host_argv_extras(output_filter)
        run_id = "run-" + _compute_run_id(key, pipeline_argv, extras=host_extras)
        remote_input = self._cache.remote_dataset_dir(key)
        own = remote_run_paths(self.host, run_id)
        own_run_dir = own.run_dir
        remote_output = own.out
        remote_pidfile = own.pidfile
        remote_sentinel = own.sentinel
        remote_heartbeat = own.heartbeat

        # Only the *output* directory can be re-attached, and only it is. The
        # input dir keys on this run's own staged archives, and the pidfile /
        # sentinel / heartbeat stay under this run's own ``run_id`` because
        # ``_sweep_orphaned_allocation`` finds a crashed session's SLURM job by
        # recomputing exactly that address — pointing them at a previous run's
        # directory would make this run adopt an allocation it does not own.
        argv_digest = pipeline_argv_digest(pipeline_argv, extras=host_extras)
        # Fingerprint what the *user* has, not what staging produced. See
        # ``source_fingerprint``: the upload key churns for two different
        # reasons and only one of them is a reason to re-attach.
        source_key = source_fingerprint(_local_input_dir_from_argv(pipeline_argv))
        own_output = remote_output
        # An incremental run uploads a split — only the groups still missing —
        # so any earlier directory's outputs can only mislead it: the pre-flight
        # counts them as this run's, and a pipeline that finds a well's output
        # already there skips the well. It never borrows a directory, and its
        # own is emptied below (`run_id` is deterministic, so its own may hold
        # an earlier incremental run's outputs too).
        if not zipped.incremental:
            remote_output = self._reattach_output_dir(
                remote_output, local_output_dir, pipeline_argv,
                argv_digest=argv_digest, source_key=source_key,
                host_alias=self.host.ssh_alias, log=log,
            )
        reattached = remote_output != own_output
        if zipped.incremental:
            self._cache._ssh_run(["rm", "-rf", own_output], check=False)

        # **Both** directories, and the second one is not optional. A
        # re-attached run writes its outputs under a *previous* run's id, but
        # its pidfile, upload sentinel and heartbeat stay under its own — and
        # nothing else on this path creates that. Making only the output
        # directory left ``runs/<own_run_id>/`` absent for exactly the runs
        # that re-attach (re-attach fires only when the cache key churned,
        # which is what makes the id new), so ``echo $$ > run.pid`` failed and
        # Stop could no longer reach the remote pipeline; on a scheduler host
        # the heartbeat failed silently, the allocation was released after
        # ``UPLOAD_HEARTBEAT_STALE_S``, and the sentinel ``touch`` then raised
        # after the whole plate had uploaded.
        #
        # The sentinel is cleared with the same call: ``run_id`` is
        # deterministic in (input, arguments), so a re-run lands in the
        # directory the last one used and would otherwise find that run's
        # "upload finished" flag already waiting for it.
        self._cache._ssh_run(  # short-circuit reuse of ssh helper
            ["mkdir", "-p", remote_output, own_run_dir],
            check=True,
        )
        # Two calls rather than one shell line: ``_remote_join`` quotes every
        # token, so an ``&&`` between them would reach the remote as a
        # directory named "&&" instead of as an operator.
        self._cache._ssh_run(["rm", "-f", remote_sentinel], check=False)
        # A suspend request the last run of this plate left behind would stop
        # this one before it did anything: ``run_id`` is deterministic, so a
        # resume lands in the very directory the suspend was requested in.
        self._cache._ssh_run(["rm", "-f", own.suspend_request], check=False)

        _force, _forced_wells = _forced_from_argv(pipeline_argv)
        self._run = _RemoteRun(
            run_id=run_id,
            cache_key=key,
            remote_input_dir=remote_input,
            remote_output_dir=remote_output,
            remote_pidfile=remote_pidfile,
            local_output_dir=local_output_dir,
            local_input_mode=zipped.mode,
            local_input_dir=_local_input_dir_from_argv(pipeline_argv),
            output_filter=output_filter,
            incremental=zipped.incremental,
            force=_force, force_wells=_forced_wells,
            pre_run_artifacts=_snapshot_local_artifacts(local_output_dir),
            reattached=reattached,
        )
        # Where this run's work went, written down while the run is still
        # starting rather than reconstructed afterwards. Nothing local knew a
        # remote run existed until this line: ``run_id`` was re-derivable only
        # for as long as the staged archives kept the same mtimes, which a
        # rewritten upload does not.
        _record_remote_in_journal(
            local_output_dir, run=self._run, host_alias=self.host.ssh_alias,
            argv_digest=argv_digest, source_key=source_key,
        )

        # v5.2.27: pre-flight cache-hit check. Before launching the
        # pipeline, ssh in and count the ``*_out.zip`` files already
        # present in the remote output dir. If every well in the
        # input is already represented there, the previous run
        # finished its compute phase — skip launching the pipeline
        # entirely and let the verify-backstop + retry loop pull
        # whatever the user is missing locally. This handles the
        # case the user reported in v5.2.26+: pipeline completed,
        # local fetch was interrupted (network drop or Stop button),
        # re-run should NOT re-analyze.
        #
        # The replacement remote command is ``true`` — a no-op shell
        # builtin that exits 0 immediately. Downstream flow
        # (PipelineRunner reading stdout to EOF, then
        # verify_and_cleanup → backstop → _retry_missing_wells) is
        # unchanged.
        expected_well_count = len(zipped.well_zips)
        cache_hit = False
        if expected_well_count > 0:
            # v5.2.28: surface the path + raw listing so when a user
            # expects a cache hit but doesn't get one, we can see
            # whether (a) the run_id drifted between runs (different
            # remote dir than the prior run wrote to), or (b) the
            # dir exists but is empty / missing the right files.
            log(
                f"[remote] pre-flight: checking {self.host.ssh_alias}:"
                f"{remote_output} for prior outputs\n"
            )
            completed_remote = self._output_verifier()._list_remote_completed_wells()
            log(
                f"[remote] pre-flight: remote output dir has "
                f"{len(completed_remote)}/{expected_well_count} well "
                f"output zip(s) "
                f"(found wells: "
                f"{sorted(completed_remote)[:5]}"
                f"{'…' if len(completed_remote) > 5 else ''})\n"
            )
            if _pipeline_launch_is_pointless(
                    pipeline_argv, expected_well_count, completed_remote,
                    uploaded={wz.stem.upper() for wz in zipped.well_zips},
                    incremental=zipped.incremental):
                cache_hit = True
                log(
                    "[remote] FULL CACHE HIT — every well has output "
                    "already; skipping pipeline launch and proceeding "
                    "straight to verify + fetch.\n"
                )
            elif len(completed_remote) >= expected_well_count:
                # The remote has everything, but the run was told to reprocess.
                # Say so: the cache-hit line above is the one a user expects
                # here, and its absence is otherwise unexplained.
                log(
                    "[remote] remote output dir is already complete, but this "
                    "run was asked to reprocess — launching the pipeline "
                    "anyway (it will overwrite).\n"
                )

        if cache_hit:
            # Replace the pipeline with a no-op. The shell still runs
            # ``echo $$ > pidfile`` (so cancel() can find the PID if
            # the user clicks Stop during the trivial ssh), then exec
            # ``true``. The ``true`` exits 0 immediately, downstream
            # verify-and-cleanup runs the v5.2.25 backstop, and the
            # existing _retry_missing_wells re-pulls anything the
            # user is missing locally.
            remote_argv = ["true"]
        else:
            remote_argv = self._build_remote_argv(
                pipeline_argv,
                remote_input,
                remote_output,
                output_filter,
                zipped.mode,
            ) + ["--suspend_file", own.suspend_request]

        # Compose the remote shell command. PID echoed first so the
        # cancel path can target the pipeline directly. v5.2.11: tilde-
        # aware quoting so the pidfile (under ~/.allwell-staging) and
        # the pipeline argv (with --output_dir ~/.allwell-staging/...)
        # don't end up creating a literal ~ directory on the remote.
        # v5.2.13: source conda.sh and exec via ``conda run`` so the
        # pipeline runs inside the ``allwell`` env regardless of where
        # the user's conda is installed.
        #
        # When the host has a ``compute_launcher`` (e.g. an ``srun …``
        # line), the pipeline is launched into a scheduler allocation so
        # it runs on a compute node the remote schedules rather than on
        # the login/"lobby" node we ssh into — see _compose_remote_cmd.
        # A cache hit short-circuits to a ``true`` no-op, which must NOT
        # consume a compute allocation, so the launcher is skipped there.
        # **Ask for the node before sending the data**, so a queue wait and the
        # transfer overlap instead of running end to end. Only where there is
        # something to queue *for*: a host with no launcher runs on the login
        # node the moment ssh connects, and a cache hit runs ``true``, so both
        # must keep the old order or the pipeline would start against an input
        # directory that is still filling.
        overlap = bool(self.host.compute_launcher.strip()) and not cache_hit
        remote_cmd = _compose_remote_cmd(
            self.host.compute_launcher,
            remote_pidfile,
            remote_argv,
            cache_hit=cache_hit,
            env_selector=self.env_selector,
            upload_sentinel=remote_sentinel if overlap else "",
            upload_heartbeat=remote_heartbeat if overlap else "",
            scratch_dir=self.host.remote_scratch_dir,
        )
        ssh_argv = self.host.ssh_argv(allocate_tty=True) + [remote_cmd]

        # Name the environment before the run starts. When it turns out not to
        # exist, the guard's error names the same thing, so the two lines read
        # as one story instead of a raw conda traceback out of nowhere.
        kind, value = self.env_selector
        log(f"[remote] conda env: {kind} {value}\n")
        if self._env_warning:
            log(f"[remote] warn: {self._env_warning}\n")
        log(
            f"[remote] run_id={run_id}  cache_key={key[:8]}\n"
            f"[remote] $ {_remote_join(remote_argv)}\n"
        )
        # Surface the scheduler hand-off so a queue wait reads as "waiting",
        # not a hang. The ``[remote] scheduler: allocation granted …`` line
        # (emitted from inside the srun step) follows once a node is granted.
        if not cache_hit:
            # A cache hit short-circuits to a ``true`` no-op and consumes no
            # allocation, so it is not waiting for one.
            log(_scheduler_handoff_lines(self.host.compute_launcher))

        def _start_ssh() -> subprocess.Popen:
            return subprocess.Popen(
                ssh_argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # Decode the remote (Linux, UTF-8) pipeline output as UTF-8 so
                # the em-dash in per-well markers ("Well X — done.") isn't
                # mojibake'd on a cp1252 Windows client — otherwise the
                # dashboard's Process stage stays stuck at 0/N while ASCII-only
                # markers (threshold) reach N/N.
                **TEXT_UTF8,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                start_new_session=(os.name == "posix"),
            )

        def _upload() -> tuple[int, float]:
            up_key, sent, elapsed = self._cache.ensure_uploaded(
                zipped, log_fn=log, progress_fn=progress_fn,
                stop_event=stop_event,
            )
            if up_key != key:
                # The key decides the remote input directory, and the job was
                # sent to wait in the one derived above. Two answers here means
                # the bytes went somewhere the pipeline will not look.
                raise RuntimeError(
                    f"upload landed under cache key {up_key[:8]} but the run "
                    f"was prepared for {key[:8]}")
            log(f"[remote] uploaded {sent / (1024**2):.1f} MB in "
                f"{_fmt_secs(elapsed)}\n")
            # Their input is on the remote now. Recorded because it is the one
            # thing that distinguishes "this run never got to that well" from
            # "its images were sent and the run died before computing them" —
            # and on a run that dies mid-plate, that is the difference between
            # re-uploading a plate and not.
            for wz in zipped.well_zips:
                label = _canonical_well_from_path(Path(wz).stem)
                if label:
                    with swallow(f"journalling {label} as uploaded"):
                        run_journal.record_well(
                            local_output_dir, label, "uploaded")
            return sent, elapsed

        heartbeat: _UploadHeartbeat | None = None
        if overlap:
            # Before adding an allocation of our own, hand back one a crashed
            # run of this same dataset may have left holding a node.
            self._sweep_orphaned_allocation(
                remote_pidfile, remote_heartbeat, log_fn=log)
            heartbeat = _UploadHeartbeat(self._cache, remote_heartbeat)
            # One beat before the job can look, so the first thing it sees is a
            # heartbeat that exists rather than one that is missing.
            heartbeat.beat()
            popen = _start_ssh()
            try:
                heartbeat.start()
                _upload()
            except BaseException:
                # An allocation is already in flight and nothing will ever
                # write its sentinel. The stopped heartbeat would free it
                # within the minute anyway; this is the direct route.
                heartbeat.stop()
                log("[remote] upload failed — releasing the allocation\n")
                try:
                    self.cancel(popen)
                except Exception:
                    swallowed("releasing the allocation after a failed upload")
                raise
            # Inside the guard, not after it: this is a ``check=True`` call, so
            # a failure here raises with an allocation already granted, a
            # heartbeat thread still beating and the whole plate uploaded. It
            # is the last step of the upload, so it fails for the same reasons
            # the upload does and wants the same cleanup.
            try:
                # The job is watching for exactly this file.
                self._cache._ssh_run(["touch", remote_sentinel], check=True)
            except BaseException:
                heartbeat.stop()
                log("[remote] could not signal upload completion — "
                    "releasing the allocation\n")
                try:
                    self.cancel(popen)
                except Exception:
                    swallowed("releasing the allocation after a failed sentinel")
                raise
        else:
            _upload()
            popen = _start_ssh()

        fetcher = Fetcher(
            self.host,
            self._run,
            output_filter=output_filter,
            log_fn=log,
            progress_fn=progress_fn,
            poll_sidecar=True,
        )
        # Keep a handle so cancel() can stop an in-flight per-well download.
        self._fetcher = fetcher
        self._cancelled.clear()
        # Suspendable only from here: the pipeline is launched and its input is
        # all on the host. Earlier, a request would wait out the rest of the
        # upload and a node for a run that then does nothing, so a suspend
        # during the upload stops it instead (`PipelineRunner.suspend`).
        if not cache_hit:
            self._run.suspend_request = own.suspend_request
        return _RemoteProcess(popen, self._run, fetcher, heartbeat=heartbeat)

    # ── Verify + cleanup ────────────────────────────────────────────────

    def _output_verifier(self) -> FetchedOutputVerifier:
        """The verifier for this runner's current run, with its collaborators."""
        return FetchedOutputVerifier(self.host, self._run, self._cache, self._cancelled)

    def verify_and_cleanup(
        self,
        ok: bool,
        *,
        cleanup_mode: str = "none",
        dry_run: bool = False,
        log_fn: Callable[[str], None] | None = None,
    ) -> bool:
        """Pull top-level artefacts, then verify and clean up.

        Returns True iff the local manifest checks pass. The remote
        cleanup is only fired on success — a failed manifest leaves the
        remote state in place for retry.

        v5.2.23: when ``dry_run`` is True, the ``rm -rf`` paths are
        replaced with an ssh probe that surfaces the path, file count,
        and total bytes that WOULD be removed. The remote files are
        left in place. Useful for shared machines where the user
        wants to confirm what gets deleted before turning auto-delete
        on for real.
        """
        log = log_fn or (lambda _m: None)
        if self._run is None:
            return False
        run = self._run

        # Everything up to the verdict — the top-level pulls, the sidecar
        # merges, the completeness record, the manifest check and its retry —
        # belongs to the fetched-output verifier. What to delete on the remote
        # is decided here, from its verdict and nothing else.
        verdict = self._output_verifier().verify(ok, log=log)
        if not verdict.passed:
            return False

        # Cleanup according to the user's flag. Fires only after verify
        # passes — there's no path that can lose remote state if a fetch
        # was incomplete.
        if cleanup_mode == "none":
            log("[remote] --cleanup-remote=none — leaving everything in place.\n")
            return True

        # A re-attached run wrote its output into a directory named for a
        # *previous* run (see ``_choose_remote_output_dir``), so the path this
        # would otherwise derive from ``run_id`` holds nothing this run
        # produced. Deleting it would report a cleanup that removed the wrong
        # directory and left the real outputs on the remote for ever — and
        # would take with it whatever else that other run's directory still
        # holds. Say so and keep both rather than guess which is safe.
        if run.reattached:
            # The borrowed directory is left alone; this run's own is not. It
            # holds only the pidfile, the sentinel and the heartbeat this run
            # created, and nothing prunes ``runs/`` — ``DatasetCache.gc``
            # covers ``datasets/`` — so leaving it would drop two sidecar files
            # on the host for every re-attaching run, for ever.
            own_dir = remote_run_dir(self.host, run.run_id)
            # The fate of this run's own directory is said inside the branch
            # that decides it. Stating it afterwards told a dry run that the
            # directory "has been cleaned up" three lines before announcing
            # that nothing was deleted — in the one mode whose whole purpose is
            # saying truthfully what *would* happen.
            if dry_run:
                self._log_would_delete(
                    own_dir, label=f"run dir {run.run_id}", log=log)
            else:
                self._cache._ssh_run(["rm", "-rf", own_dir], check=False)
                log(f"[remote] removed this run's own dir {own_dir}.\n")
            log(
                f"[remote] re-attached to {run.remote_output_dir}, which "
                f"belongs to an earlier run of this plate — leaving it in "
                f"place; remove it by hand once you are done with it.\n"
            )
        else:
            run_dir = remote_run_dir(self.host, run.run_id)
            if dry_run:
                self._log_would_delete(
                    run_dir, label=f"run dir {run.run_id}", log=log)
            else:
                self._cache._ssh_run(
                    ["rm", "-rf", run_dir],
                    check=False,
                )
                log(f"[remote] removed run dir {run.run_id}.\n")
        if cleanup_mode == "all":
            cache_dir = self._cache.remote_dataset_dir(run.cache_key)
            if dry_run:
                self._log_would_delete(
                    cache_dir,
                    label=f"dataset cache {run.cache_key[:8]}",
                    log=log,
                )
            else:
                self._cache._ssh_run(
                    ["rm", "-rf", cache_dir],
                    check=False,
                )
                log(f"[remote] removed dataset cache {run.cache_key[:8]}.\n")
        if dry_run:
            log(
                "[remote] [DRY-RUN] no files were actually deleted. "
                "Re-run without --dry-run-cleanup to commit.\n"
            )
        return True

    def _log_would_delete(
        self, remote_path: str, *, label: str, log: Callable[[str], None]
    ) -> None:
        """v5.2.23 dry-run probe: ssh in, count files + bytes at
        ``remote_path``, log the result without deleting anything.

        Uses ``_ssh_script`` (stdin-piped to remote bash) so the
        ``~/.allwell-staging`` default expands correctly on the
        remote — the v5.2.16 lesson, generalised."""
        script = (
            f"if [ ! -e {remote_path} ]; then echo MISSING; exit 0; fi; "
            f"_b=$(du -sb {remote_path} 2>/dev/null | awk '{{print $1}}'); "
            f"_f=$(find {remote_path} -type f 2>/dev/null | wc -l | tr -d ' '); "
            f"echo BYTES $_b; "
            f"echo FILES $_f"
        )
        result = self._cache._ssh_script(script, check=False)
        out = (result.stdout if result is not None else "") or ""
        if "MISSING" in out:
            log(
                f"[remote] [DRY-RUN] would delete {label} at {remote_path} "
                f"— but path doesn't exist on remote (already cleaned up?).\n"
            )
            return
        bytes_match = re.search(r"BYTES\s+(\d+)", out)
        files_match = re.search(r"FILES\s+(\d+)", out)
        bytes_n = int(bytes_match.group(1)) if bytes_match else 0
        files_n = int(files_match.group(1)) if files_match else 0
        mb = bytes_n / (1024 * 1024)
        log(
            f"[remote] [DRY-RUN] would delete {label} at {remote_path} "
            f"({files_n} file(s), {mb:.1f} MB)\n"
        )

    def _reattach_output_dir(
        self,
        fresh: str,
        local_output_dir: Path,
        pipeline_argv: list[str],
        *,
        argv_digest: str,
        source_key: str,
        host_alias: str,
        log: Callable[[str], None],
    ) -> str:
        """The remote output dir to use, preferring a recorded one that still
        holds output.

        Everything decidable without the network is decided by
        :func:`_choose_remote_output_dir`; this method exists to supply it with
        the two facts only an ssh round-trip can establish, and to keep that
        round-trip off the path entirely when no stored address exists — which
        is every first run of a plate, and every run whose upload was not
        rewritten.
        """
        from services import run_journal

        try:
            stored = run_journal.stored_remote_address(
                local_output_dir, argv_digest=argv_digest,
                source_key=source_key, host_alias=host_alias)
        except OSError:
            swallowed(f"could not read the run journal in {local_output_dir}")
            return fresh
        candidate = str((stored or {}).get("remote_output_dir") or "")
        if not candidate or candidate == fresh:
            return fresh
        forced, forced_wells = _forced_from_argv(pipeline_argv)
        # The listing is skipped for a forced run: it cannot change the answer,
        # and an ssh round-trip whose result is discarded is a wait the user
        # pays for twice — once here, once when the pipeline runs anyway.
        has_outputs = False
        if not (forced or forced_wells):
            has_outputs = bool(self._output_verifier()._list_completed_wells_in(candidate))
        chosen, reason = _choose_remote_output_dir(
            fresh, candidate,
            forced=bool(forced or forced_wells),
            remote_has_outputs=has_outputs,
        )
        if reason:
            log(f"[remote] {reason}\n")
        return chosen

    # ── Cancel ──────────────────────────────────────────────────────────

    def close_remote_job(
        self, *, log_fn: Callable[[str], None] | None = None
    ) -> None:
        """Guaranteed-teardown backstop: ensure the scheduler job this run
        launched is closed.

        Idempotent and best-effort — a no-op on direct-execution hosts (no
        ``compute_launcher``), when no run has started, or when the job has
        already exited (``scancel`` on a finished id is harmless). Reads the
        ``$SLURM_JOB_ID`` the run wrote to ``<pidfile>.slurm`` on the shared
        filesystem and ``scancel``s it via a fresh ssh.

        ``srun`` already exits when the pipeline finishes *or* fails, so in
        the common case the job is gone before we get here and this does
        nothing. Its job is to make sure an *orphaned* allocation — ssh /
        srun client died, connection dropped, AllWell crashed mid-run —
        never lingers in the queue waiting on the user. Called from the
        PipelineRunner's ``finally`` (covers complete / fail / exception)
        and from :meth:`cancel` (covers user Stop)."""
        if not self.host.compute_launcher.strip():
            return
        run = self._run
        if run is None:
            return
        log = log_fn or (lambda _m: None)
        slurm_sidecar_q = _remote_quote(f"{run.remote_pidfile}.slurm")
        cmd = (
            f"_jid=$(cat {slurm_sidecar_q} 2>/dev/null); "
            '[ -z "$_jid" ] && exit 0; '
            + _only_my_job("_jid", "AW_NOTMINE") +
            'command -v scancel >/dev/null 2>&1 '
            '&& scancel -u "$_aw_me" "$_jid" 2>/dev/null '
            '&& echo "AW_SCANCELLED $_jid" || true'
        )
        try:
            result = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [cmd],
                capture_output=True, timeout=10, **TEXT_UTF8,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            # A failed teardown ssh must not be silent — an orphaned SLURM
            # allocation could keep running (and billing) unnoticed (audit E13).
            log(f"[remote] WARN scheduler: could not confirm scancel of the "
                f"remote job ({exc}); a SLURM allocation may still be running.\n")
            return
        out = (result.stdout or "")
        if "AW_SCANCELLED" in out:
            jid = out.split("AW_SCANCELLED", 1)[1].strip().split()[0:1]
            log(
                f"[remote] scheduler: cancelled lingering job "
                f"{jid[0] if jid else ''}.\n"
            )
        elif "AW_NOTMINE" in out:
            # Not "already finished": it is running, and it is somebody else's.
            # Reporting it as a clean teardown would say the opposite of what
            # happened, and this is the log a user reads when a node is still
            # busy after they stopped.
            rest = out.split("AW_NOTMINE", 1)[1].strip().split()
            log(f"[remote] scheduler: job {rest[0] if rest else '?'} belongs to "
                f"{rest[1] if len(rest) > 1 else 'another user'} — left "
                f"running.\n")
        else:
            # The common case: ``srun`` already exited with the pipeline, so
            # there was no allocation left to cancel. Confirm the clean
            # teardown so the log has a definite end-of-job marker rather
            # than silence.
            log("[remote] scheduler: job already finished — nothing to cancel.\n")

    def _sweep_orphaned_allocation(
        self, remote_pidfile: str, heartbeat: str,
        *, log_fn: Callable[[str], None] | None = None,
    ) -> str:
        """Cancel an allocation a *previous* run of this app left behind.

        The scancel backstop covers every exit the app survives: a clean quit,
        a finished run, a Stop click. It cannot cover the ones where no Python
        of ours runs at all — SIGKILL, a segfault, a force-quit, the power
        going — because it needs the job id held in memory. The id is on the
        shared filesystem the whole time (`<pidfile>.slurm`), and ``run_id`` is
        deterministic in the input and the arguments, so the next run of the
        same plate can find exactly the job the crashed one left.

        **Guarded, because that determinism cuts both ways.** Two instances
        running the same plate compute the same ``run_id``, so an unconditional
        sweep would have each cancel the other's live job. A run that is still
        owned is beating its heartbeat, and one that started moments ago has a
        fresh sidecar; either is left alone. What gets cancelled is a job whose
        owner has been silent for longer than a stalled upload would be.

        Every step that cannot answer refuses to cancel rather than guessing.
        A job that is somebody else's, or one whose age this host's ``stat``
        cannot report, is left running: the cost of not sweeping is an idle
        node until its walltime, and the cost of sweeping wrongly is somebody's
        half-finished plate.

        Returns which of those it found — ``"swept"``, ``"owned"``,
        ``"notmine"``, ``"unknown"`` or ``"none"`` — because the batched path
        also has to know whether the run directory it is about to empty
        currently belongs to somebody.
        """
        if not self.host.compute_launcher.strip():
            return "none"
        log = log_fn or (lambda _m: None)
        sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
        hb_q = _remote_quote(heartbeat)
        stale = UPLOAD_HEARTBEAT_STALE_S
        # One round trip: read the id, and the two ages that decide whether it
        # is anyone's. Ages are computed remotely so no clock is compared
        # against another machine's, and both spellings of ``stat`` are tried
        # — GNU's ``-c %Y`` and BSD's ``-f %m`` — because a host with only the
        # second used to report every age as zero, which reads as "long dead"
        # and would cancel a job that had started seconds earlier.
        script = (
            f'_jid=$(cat {sidecar_q} 2>/dev/null); '
            f'[ -z "$_jid" ] && exit 0; '
            f'_now=$(date +%s); '
            f'_sc=$(stat -c %Y {sidecar_q} 2>/dev/null || '
            f'stat -f %m {sidecar_q} 2>/dev/null || echo ""); '
            f'if [ -z "$_sc" ]; then echo "AW_UNKNOWN $_jid"; exit 0; fi; '
            f'_hb=0; if [ -e {hb_q} ]; then '
            f'_hb=$(stat -c %Y {hb_q} 2>/dev/null || '
            f'stat -f %m {hb_q} 2>/dev/null || echo 0); fi; '
            f'if [ "$_hb" -ne 0 ] && '
            f'[ "$((_now - $_hb))" -le {stale} ]; then '
            f'echo "AW_OWNED $_jid"; exit 0; fi; '
            f'if [ "$((_now - $_sc))" -le {stale} ]; then '
            f'echo "AW_OWNED $_jid"; exit 0; fi; '
            + _only_my_job("_jid", "AW_NOTMINE") +
            'command -v scancel >/dev/null 2>&1 '
            '&& scancel -u "$_aw_me" "$_jid" '
            '2>/dev/null && echo "AW_SWEPT $_jid" || true'
        )
        try:
            result = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [script],
                capture_output=True, timeout=15, **TEXT_UTF8,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log(f"[remote] WARN scheduler: could not check for a leftover "
                f"allocation ({exc}).\n")
            return "unknown"
        out = result.stdout or ""
        if "AW_SWEPT" in out:
            jid = out.split("AW_SWEPT", 1)[1].strip().split()[:1]
            log(f"[remote] scheduler: cancelled a job left over from an "
                f"earlier run of this dataset ({jid[0] if jid else '?'}) — "
                f"the app it belonged to is gone.\n")
            return "swept"
        if "AW_NOTMINE" in out:
            rest = out.split("AW_NOTMINE", 1)[1].strip().split()
            log(f"[remote] scheduler: the job in this dataset's run directory "
                f"({rest[0] if rest else '?'}) belongs to "
                f"{rest[1] if len(rest) > 1 else 'another user'} — not touching "
                f"it.\n")
            return "notmine"
        if "AW_OWNED" in out:
            jid = out.split("AW_OWNED", 1)[1].strip().split()[:1]
            log(f"[remote] scheduler: another session is running this same "
                f"dataset (job {jid[0] if jid else '?'}); leaving it alone.\n")
            return "owned"
        if "AW_UNKNOWN" in out:
            jid = out.split("AW_UNKNOWN", 1)[1].strip().split()[:1]
            log(f"[remote] WARN scheduler: this host cannot report a file's "
                f"modification time, so there is no way to tell whether job "
                f"{jid[0] if jid else '?'} in this dataset's run directory is "
                f"still in use. Leaving it running.\n")
            return "unknown"
        return "none"

    @property
    def can_request_suspend(self) -> bool:
        """Whether a pipeline has been launched that :meth:`request_suspend` can reach."""
        run = self._run
        return run is not None and bool(run.suspend_request)

    def request_suspend(self, proc=None, *,
                        log_fn: Callable[[str], None] | None = None) -> bool:
        """Ask the running pipeline to wind down; see :func:`remote_suspend.request_suspend`."""
        return _request_suspend(self, proc, log_fn=log_fn)

    def cancel(self, ssh_proc: subprocess.Popen) -> None:
        """Best-effort remote kill. The local pgid kill (handled by
        PipelineRunner) is the primary mechanism; this is the belt-and-
        braces second channel via a fresh ssh that ``kill``s the PID we
        wrote on startup.

        On a scheduler-backed host this also calls :meth:`close_remote_job`
        to ``scancel`` the allocation — killing the login-side ``srun``
        client usually tears it down, but a queued/odd state can leave the
        job alive.

        Also interrupts the local download side: the per-well Fetcher runs
        rsync on a background thread in THIS process (separate from the ssh
        process group), and the end-of-run retry backstop polls ``_cancelled``.
        Without these, clicking Stop while the final per-well rsync was in
        flight / retry-looping did nothing — the GUI only ever tore down the
        ssh process, never the download that was actually blocking."""
        self._cancelled.set()
        if self._fetcher is not None:
            try:
                self._fetcher.cancel()
            except Exception:  # best-effort
                swallowed()  # was: pass (silent except)
        if self._run is None:
            return
        run = self._run
        pidfile_q = _remote_quote(run.remote_pidfile)
        try:
            subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [
                    f"kill -TERM $(cat {pidfile_q} 2>/dev/null) 2>/dev/null || true"
                ],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Best-effort (the local process-group kill is primary), but don't
            # swallow it silently — record that this teardown channel failed
            # (audit E13); close_remote_job() below is the scheduler backstop.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "remote cancel: ssh kill failed (%s); relying on the local "
                "process-group kill + scancel backstop", exc)
        # Belt-and-braces: also cancel the scheduler job (no-op on
        # direct-execution hosts / when no job id was recorded).
        self.close_remote_job()

    # ── Argv building ───────────────────────────────────────────────────

    def _build_remote_argv(
        self,
        local_argv: list[str],
        remote_input: str,
        remote_output: str,
        output_filter: OutputFilter,
        mode: LocalInputMode,
        background_dir: str = "",
    ) -> list[str]:
        """Rewrite the local pipeline argv for remote execution.

        Replaces ``--input_dir`` / ``--output_dir`` with the remote-side
        paths, swaps the local python path for ``conda run`` inside the
        ``allwell`` env, and appends the filter flags.
        """
        # The local argv is shaped like:
        #   [sys.executable, /path/to/process_microscopy.py, --input_dir, X, ...]
        # We replace the interpreter and script path. v5.2.13: instead
        # of pinning ``self.host.remote_python`` (which broke on hosts
        # whose conda lives outside ``~/miniforge3``), we wrap with
        # ``conda run --no-capture-output -n allwell python`` — the
        # surrounding shell prefix in ``spawn`` sources conda.sh first.
        remote_script = f"{self.host.remote_project_dir}/process_microscopy.py"
        out = [*_conda_run_prefix_for(self.env_selector), "python", remote_script]
        i = 2  # skip the interpreter + script slots
        # Suppress the remote completeness check unconditionally; the client
        # runs it after the fetch instead (see verify_and_cleanup). A record
        # written remotely would describe the wrong tree — an incremental run's
        # remote output dir holds only the uploaded split — and would be
        # destroyed anyway when the fetched sidecar overwrites the local one.
        # The client-side check answers "did it get here", which strictly
        # supersets "did the remote produce it".
        suppress_run_check = "--no_run_check" not in local_argv
        while i < len(local_argv):
            arg = local_argv[i]
            if arg == "--input_dir":
                out.extend([arg, remote_input])
                i += 2
            elif arg == "--output_dir":
                out.extend([arg, remote_output])
                # Pin the remote scratch to the remote output dir. The pipeline's
                # default moved to ``~/.allwell/scratch`` to keep a local run's
                # bulk I/O off a network-mounted output volume; on a cluster that
                # default would be wrong — the login/compute home is typically a
                # small-quota NFS share, while ``remote_output`` already lives in
                # the host's configured staging area. Passing it explicitly keeps
                # remote runs byte-identical to their previous behaviour.
                out.append(f"--scratch_dir={remote_output}")
                i += 2
            elif arg.startswith("--scratch_dir"):
                # Never forward a local scratch path to the remote host.
                if arg == "--scratch_dir":
                    i += 2
                else:
                    i += 1
            elif arg == "--incremental":
                # Incremental is a client-side concern on remote runs: the
                # client uploads only the new-FOV files and merges the partial
                # outputs back on fetch. The remote just processes what it
                # received, so the flag (and its prior-dir) are dropped here.
                i += 1
            elif arg == "--incremental_prior_dir":
                i += 2
            else:
                out.append(arg)
                i += 1
        if suppress_run_check:
            out.append("--no_run_check")
        # Batched mode stages one batch at a time, so the empty-well blank is
        # not in most batches' input. It is uploaded once to its own directory
        # and named here, which is what lets every batch build the same
        # correction. Empty on every other path, where the whole plate is in
        # `--input_dir` and the pipeline's default already finds it.
        if background_dir:
            out.append(f"--background_dir={background_dir}")
        for token in self._host_argv_extras(output_filter):
            # Only the two switches are deduplicated, and only against their
            # own spelling. A blanket ``token not in out`` filter reads ``out``
            # as it grows, so any ``extra_args`` token already present anywhere
            # in the argv was dropped and its flag/value pair came apart:
            # a host overriding ``--min_nucleus_area_px 500``, a flag
            # ``build_pipeline_args`` always emits, launched with a bare
            # ``500`` and died at argparse. ``extra_args`` is appended whole.
            if token in self._DEDUPED_HOST_FLAGS and token in out:
                continue
            out.append(token)
        return out

    #: The host-side additions that are bare switches, and so are worth
    #: suppressing when the local argv already carries them —
    #: ``build_pipeline_args`` emits ``--cpu_only`` from the form's own
    #: checkbox. Everything else in ``extra_args`` is positional with respect
    #: to its value and must never be filtered.
    _DEDUPED_HOST_FLAGS = frozenset({"--no_save_tophat", "--cpu_only"})

    def _host_argv_extras(self, output_filter: OutputFilter) -> list[str]:
        """The pipeline flags this *host* and *filter* add, over the local argv.

        Split out because two questions need the same answer and had different
        ones. :meth:`_build_remote_argv` appends these to the command it
        launches; the run id and the journal's ``argv_digest`` are computed
        from the local argv, which does not contain them — so switching the
        Fetch filter, adding a host override, or ticking CPU-only changed the
        bytes the remote produced while leaving both the address and the reuse
        key identical. A run configured that differently then found the
        previous run's outputs complete, skipped the pipeline, and fetched the
        old numbers back as its own.

        Every one of these changes what the pipeline computes or writes:
        ``--no_save_tophat`` drops the tophat QC images from the output zip,
        ``extra_args`` are arbitrary pipeline flags (segmentation thresholds
        among them), and ``--cpu_only`` changes the execution path. None of
        them is a re-processing knob like ``--force``, which is exactly why
        ``pipeline_argv_digest`` skips that and folds these in.
        """
        extras: list[str] = []
        if output_filter is OutputFilter.CSV_ONLY:
            extras.append("--no_save_tophat")
        extras.extend(self.host.extra_args)
        if self.host.cpu_only:
            extras.append("--cpu_only")
        return extras
