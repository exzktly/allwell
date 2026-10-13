"""Remote-execution runner for ``process_microscopy.py``.

Stages per-well zips to an SSH host, launches the pipeline remotely, and
streams outputs back as wells complete. Designed as a drop-in replacement
for ``spawn_pipeline()`` from the GUI's point of view: the Popen handle
returned by :meth:`RemoteRunner.spawn` looks identical (stdout pipe, pgid
kill works, return code semantics preserved) so ``PipelineRunner`` and
``ProgressTracker`` need only a single-line branch to opt in.

The five transfer-minimizing levers are implemented here and in the modules
this one orchestrates — ``services.remote_staging`` turns the user's input into
the archives a run uploads, ``services.remote_upload`` gets them onto the host
once, ``services.remote_process`` presents the run as a process the GUI reads
and stops, ``services.remote_fetch`` owns bringing output home and proving it
arrived, ``services.remote_run_state`` the record a run carries,
``services.remote_jobs`` the scheduler jobs a run holds, and
``services.remote_wire`` the lines the stages print and parse:

* **Per-well zip granularity** — ``remote_staging.stage_local_zips`` runs the existing
  ``well_plate_zipper`` locally first, so a 1500-image plate ships as
  ~100 large zips instead of 1500 small TIFs.
* **Persistent dataset cache** — ``remote_upload.DatasetCache`` keys uploads by a
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

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from services.subprocess_kill import own_process_group_kwargs
from services.subprocess_text import TEXT_UTF8
from services.remote_config import (
    RemoteHost,
    resolve_rsync_binary,
)
from services.remote_conda import _conda_env_selector, _conda_run_prefix_for
from services import run_journal
from util.aw_safe import swallow, swallowed
# The run record and the line vocabulary every stage of a remote run shares.
from services.remote_run_state import (
    LocalInputMode,
    OutputFilter,
    _RemoteRun,
    _canonical_well_from_path,
)
from services.remote_wire import (  # noqa: F401 — WELL_XFER_PREFIX is facade
    TRANSIENT_PREFIX,
    WELL_XFER_PREFIX,
)
# Staging the user's input into the archives a run uploads. ``stage_local_zips``
# and ``input_well_containers`` are part of this module's supported facade.
from services.remote_staging import (  # noqa: F401 — facade re-exports
    ZippedInput,
    input_well_containers,
    stage_local_zips,
)
# The stages this module orchestrates: getting the input onto the host, the run
# as a process the GUI can read, wait on and stop, the scheduler jobs it holds,
# and bringing its output home.
from services.remote_upload import DatasetCache
from services.remote_process import (
    _BatchedRemoteProcess,
    _RemoteProcess,
    _UploadHeartbeat,
    _record_remote_in_journal,
    _scheduler_handoff_lines,
)
from services.remote_jobs import close_remote_job, sweep_orphaned_allocation
from services.remote_fetch import (
    FetchedOutputVerifier,
    Fetcher,
    _snapshot_local_artifacts,
)
from services.remote_transport import (
    _fmt_secs,
    _remote_join,
    _remote_quote,
    _rsync_ssh_transport,
    _rsync_supports_info_flag,
    _rsync_with_retry,
)
from services.remote_suspend import request_suspend as _request_suspend
from services.remote_commands import _compose_remote_cmd
# Where a run lives, and whether an earlier one's output may be reused — the
# addresses, the keys they are derived from, and the re-attach decision.
from services.remote_run_id import (
    RemoteRunPaths,
    _pipeline_launch_is_pointless,
    _choose_remote_output_dir,
    _compute_run_id,
    _forced_from_argv,
    _local_input_dir_from_argv,
    pipeline_argv_digest,
    remote_run_dir,
    remote_run_paths,
)
from services.remote_scratch import source_fingerprint
# The remote upload manifest lives in ``services/remote_manifest`` — a data
# declaration with no behaviour, moved out so a newly-required entry cannot
# collide with this module's line budget. Re-exported because a dozen call
# sites, the PyInstaller spec and several docstrings name it by this path.
from services.remote_manifest import _REQUIRED_REMOTE_FILES, repo_root  # noqa: E402

# The supported import surface for application and command-line callers, and
# the only names any file may reach through this module
# (``tests/test_remote_runner_facade.py``). Everything else is imported from
# the module that owns it. The private manifest name is deliberate: the bootstrap and the
# PyInstaller spec read it by this path as their packaging contract.
__all__ = (
    "LocalInputMode", "OutputFilter", "RemoteRunner", "TRANSIENT_PREFIX",
    "WELL_XFER_PREFIX", "ZippedInput", "_REQUIRED_REMOTE_FILES",
    "input_well_containers", "repo_root", "stage_local_zips",
)


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


@dataclass(frozen=True)
class _PreparedRun:
    """Where one run lives, decided before anything moves.

    ``remote_output`` is this run's own output directory, or an earlier run's
    it re-attached to; everything in ``paths`` (pidfile, sentinel, heartbeat,
    suspend request) is always this run's own, because the orphan sweep finds a
    crashed session's job by recomputing exactly that address.
    """

    key: str
    run_id: str
    remote_input: str
    remote_output: str
    paths: RemoteRunPaths


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

        # Single-shot path. Cache budget enforcement before each run.
        self._cache.gc(self.host.cache_budget_gb * (1024**3), log_fn=log)

        # Address the run and record where it goes, then decide whether the
        # pipeline needs to run at all, compose what the remote is sent, and
        # launch — in that order, each its own step below.
        prep = self._prepare_run(zipped, local_output_dir, pipeline_argv,
                                 output_filter, log)
        cache_hit = self._preflight_cache_hit(
            zipped, pipeline_argv, prep.remote_output, log)
        overlap, ssh_argv = self._compose_launch(
            prep, zipped, pipeline_argv, output_filter, cache_hit, log)
        remote_pidfile = prep.paths.pidfile
        remote_sentinel = prep.paths.sentinel
        remote_heartbeat = prep.paths.heartbeat

        def upload() -> tuple[int, float]:
            return self._upload_input(zipped, prep.key, local_output_dir, log,
                                      progress_fn, stop_event)

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
            popen = self._start_ssh(ssh_argv)
            try:
                heartbeat.start()
                upload()
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
            upload()
            popen = self._start_ssh(ssh_argv)

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
            self._run.suspend_request = prep.paths.suspend_request
        return _RemoteProcess(popen, self._run, fetcher, heartbeat=heartbeat)

    def _prepare_run(self, zipped: ZippedInput, local_output_dir: Path,
                     pipeline_argv: list[str], output_filter: OutputFilter,
                     log: Callable[[str], None]) -> _PreparedRun:
        """Address this run, create its remote directories and write down
        where it went — before a byte of the plate moves."""
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
        return _PreparedRun(key=key, run_id=run_id, remote_input=remote_input,
                            remote_output=remote_output, paths=own)

    def _preflight_cache_hit(self, zipped: ZippedInput, pipeline_argv: list[str],
                             remote_output: str,
                             log: Callable[[str], None]) -> bool:
        """Whether every well's output is already on the remote, so the
        pipeline need not be launched at all."""
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
        return cache_hit

    def _compose_launch(self, prep: _PreparedRun, zipped: ZippedInput,
                        pipeline_argv: list[str], output_filter: OutputFilter,
                        cache_hit: bool,
                        log: Callable[[str], None]) -> tuple[bool, list[str]]:
        """What the remote is sent, and whether to ask for the node before
        the upload. Returns ``(overlap, ssh_argv)``."""
        run_id, key = prep.run_id, prep.key
        remote_input, remote_output = prep.remote_input, prep.remote_output
        own = prep.paths
        remote_pidfile = own.pidfile
        remote_sentinel = own.sentinel
        remote_heartbeat = own.heartbeat
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
        return overlap, ssh_argv

    def _start_ssh(self, ssh_argv: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            ssh_argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # Decode the remote (Linux, UTF-8) pipeline output as UTF-8 so
            # the em-dash in per-well markers ("Well X — done.") isn't
            # mojibake'd on a cp1252 Windows client — otherwise the
            # dashboard's Process stage stays stuck at 0/N while ASCII-only
            # markers (threshold) reach N/N.
            **TEXT_UTF8,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            **own_process_group_kwargs(),
        )

    def _upload_input(self, zipped: ZippedInput, key: str, local_output_dir: Path,
                      log: Callable[[str], None], progress_fn,
                      stop_event) -> tuple[int, float]:
        """Send the staged archives, check they landed under the key the run
        was addressed by, and journal each well as uploaded."""
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
        """Close the scheduler job this run launched (see
        :func:`services.remote_jobs.close_remote_job`)."""
        close_remote_job(self.host, self._run, log_fn=log_fn)

    def _sweep_orphaned_allocation(
        self, remote_pidfile: str, heartbeat: str,
        *, log_fn: Callable[[str], None] | None = None,
    ) -> str:
        """Cancel an allocation a crashed earlier run left behind (see
        :func:`services.remote_jobs.sweep_orphaned_allocation`)."""
        return sweep_orphaned_allocation(self.host, remote_pidfile, heartbeat,
                                         log_fn=log_fn)

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
