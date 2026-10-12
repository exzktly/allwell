"""The remote run as the GUI sees it: a process it can read, wait on and stop.

:class:`_RemoteProcess` wraps the single ssh that runs the pipeline, and
:class:`_BatchedRemoteProcess` the driver that holds one allocation and runs
the plate batch by batch. Both present the ``subprocess.Popen`` surface
``services.pipeline_runner`` reads — ``stdout``, ``poll``, ``wait``,
``terminate``, ``kill``, ``pid``, ``returncode`` — and both tee every line to the
``Fetcher`` so per-well downloads start the moment a well is announced done.
The heartbeat that tells a later session this run is alive, and the small
helpers the batch loop shares with the runner (the scheduler hand-off lines,
the journal record of where a run went), live here too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.remote_runner import RemoteRunner
    from services.remote_upload import DatasetCache
import io
import os
import queue
import subprocess
import sys
from functools import lru_cache
import threading
from pathlib import Path
from collections.abc import Callable
from services.subprocess_kill import own_process_group_kwargs
from services.subprocess_text import TEXT_UTF8
from services.remote_config import (
    launcher_time_seconds,
    scale_launcher_time,
)
from util.channel_roles import LABELFREE_METHODS, quantified_channel_tokens
from util.well_token import (
    background_well_key as _bg_well_key,
    blank_wells_for_run,
)
from util.aw_safe import swallow, swallowed
from services.remote_run_state import (
    OutputFilter,
    _RemoteRun,
)
from services.remote_staging import (
    ZippedInput,
)
from services.remote_fetch import (
    Fetcher,
    _snapshot_local_artifacts,
)
from services.remote_wait_snippets import (
    UPLOAD_HEARTBEAT_S,
)
from services.remote_suspend import (
    _AnySet,
)
from services.remote_commands import (
    _BATCH_DONE_RE,
    BATCH_DONE,
    BATCH_READY,
    _batch_driver_snippet,
    _compose_driver_cmd,
)
from services.remote_run_id import (
    _compute_run_id,
    _forced_from_argv,
    _local_input_dir_from_argv,
    _parse_argv_flags,
    pipeline_argv_digest,
    remote_run_dir,
    remote_run_paths,
)
from services.remote_batch_staging import prune_staged
from services.remote_scratch import (
    source_fingerprint,
)
from services.remote_manifest import repo_root


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
        # An aborted run's half-processed `in/` is cleared; its staged batches
        # and `bg/` are kept for the retry (`remote_batch_staging`).
        self._runner._cache._ssh_run(["rm", "-rf", f"{run_dir}/in"], check=False)

        # The empty-well blank(s), uploaded once for the whole run. They sit
        # outside the batch staging so the driver's `rm -rf in && mv b<i> in`
        # never touches them, and every batch builds its correction from the
        # same reference. Unconditional: a blank whose CSV is already complete
        # locally is skipped by the batch filter, which on a re-run would
        # otherwise leave the whole plate uncorrected.
        blanks = [z for z in self._zipped.well_zips
                  if z.stem in getattr(self, "_blank_stems", ())]
        if blanks:
            prune_staged(self._runner._cache, f"{run_dir}/bg", blanks,
                         "empty-well reference", self._log)
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
            **own_process_group_kwargs(),
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
                prune_staged(self._runner._cache, self._batch_staging_dir(
                    batch_idx + 1), batch_zips, f"batch {batch_idx + 1}", self._log)
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
            # Late import: the threshold pass pulls in the imaging stack.
            # Don't pay that cost on every import of the remote client.
            sys.path.insert(0, str(repo_root()))
            from pipeline.auto_threshold_pass import run_auto_threshold_pass
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
