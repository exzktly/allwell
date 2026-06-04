"""Background runner for the microscopy pipeline subprocess.

Owns the worker thread, the subprocess handle, and the log/event queue that
the GUI polls. Decouples ``analyze_tab.py`` from threading + subprocess +
log-line parsing so the tab module is a pure view + form.

Event protocol (queue payloads are ``(kind, payload)`` tuples):
    line          str        Raw stdout line.
    workers       int        Worker count parsed from a TF banner line.
    zipper_start  int        Begin grouping phase, expected well count.
    zipper_well   str|None   One well finished grouping.
    zipper_done   None       Grouping phase finished.
    remote_xfer   float      Remote rsync progress as a fraction in [0, 1].
                             Only emitted during remote-execution runs.
    done          str        Pipeline finished successfully.
    error         str        Pipeline failed; payload is a message.
    finished      None       Final event, always emitted in a finally.
"""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

from services.pipeline_service import build_pipeline_args, spawn_pipeline
from services.remote_config import RemoteHost
from services.remote_runner import (
    OutputFilter,
    RemoteRunner,
    stage_local_zips,
)
from services.subprocess_kill import signal_subprocess


def classify_log_line(line: str) -> str:
    """Return the severity tag for a single pipeline stdout line."""
    ll = line.lower()
    if "error" in ll or "traceback" in ll or "exception" in ll:
        return "ERROR"
    if "warning" in ll or "warn" in ll or "skipping" in ll:
        return "WARNING"
    return "INFO"


_RE_TF_WORKERS = re.compile(r"TF threads/worker\s*:\s*\d+\s+\(workers:\s*(\d+)\s+x")
_RE_MODE_WELLS = re.compile(r"(?:Zip mode|Flat mode|Folder mode):\s+(\d+)\s+well")


class ProgressTracker:
    """Pure log-line parser that tracks well-count progress.

    Instances are stateful (zip-mode warning + well counters) but never touch
    the GUI; ``parse(line)`` returns events that the tab forwards to the user.
    """

    def __init__(self) -> None:
        self.zip_mode_warning_logged = False
        self.well_total = 0
        self.well_done = 0

    def reset(self) -> None:
        self.zip_mode_warning_logged = False
        self.well_total = 0
        self.well_done = 0

    def parse(self, line: str) -> list[Tuple[str, object]]:
        """Return queue events for a single stdout line.

        Caller is expected to forward the events to its GUI queue. Returning a
        list (rather than enqueuing directly) keeps this method test-friendly
        and free of any dependency on the queue object.
        """
        events: list[Tuple[str, object]] = []
        if line.startswith("[zipper]"):
            return events
        # The "Zip mode detected" warning is emitted up-front in
        # _run_pipeline_thread (before the pipeline launches) so it
        # arrives even on early failures. No second emission needed
        # when the pipeline echoes the same banner via stdout.
        if "Zip mode:" in line or "Zip mode complete" in line:
            self.zip_mode_warning_logged = True
        m = _RE_TF_WORKERS.search(line)
        if m:
            events.append(("workers", int(m.group(1))))
        if not self.well_total:
            m = _RE_MODE_WELLS.search(line)
            if m:
                self.well_total = int(m.group(1))
                events.append(("well_total", self.well_total))
                return events
        if self.well_total:
            # Count a well as "done" when the pipeline emits its
            # dedicated marker line. The marker fires *before* the
            # finally-block cleanup (``remove_directory`` for zip mode
            # / staging-dir cleanup for folder mode) so the GUI's ETA
            # keeps ticking even when storage is slow (NAS / network
            # mounts can stall directory deletion for minutes).
            #
            # The CSV + zip artefacts are already on disk by the time
            # the marker fires, so the well *is* functionally done.
            #
            # Older pipeline versions emitted "temporary directories
            # removed" as the only completion log; keep matching it
            # too as a fallback for users running mismatched
            # client/server versions.
            if "— done." in line:
                self.well_done += 1
                events.append(("well_done", (self.well_done, self.well_total)))
        return events


class PipelineRunner:
    """Run the pipeline subprocess on a daemon thread and report progress.

    The runner owns its thread, subprocess handle, and event queue. Callers
    (the GUI) consume events via :meth:`poll` and call :meth:`stop` to cancel.
    """

    # Cap the log queue so a stalled UI thread can't let the reader stuff
    # unbounded lines into memory. Overflow drops are surfaced to the GUI
    # via a single warning line — the ring buffer in all_well captures
    # everything for the help-drawer log tab.
    _LOG_QUEUE_MAXSIZE = 10000

    def __init__(self) -> None:
        self.log_q: "queue.Queue[Tuple[str, object]]" = queue.Queue(
            maxsize=self._LOG_QUEUE_MAXSIZE,
        )
        self._proc: Optional[subprocess.Popen] = None
        # The grouping-phase subprocess (WellPlateZipper). Tracked
        # separately so a Stop click during grouping cancels it instead
        # of waiting for it to finish before reaching the pipeline.
        self._zipper_proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._last_output_dir: Optional[Path] = None
        self._log_overflow_warned: bool = False
        # Set on Stop; checked between the zipper phase and the pipeline
        # launch so a cancel during grouping doesn't slip through and
        # start a fresh pipeline run.
        self._stop_requested: threading.Event = threading.Event()
        # Remote-execution state. Set in start() when the user chose a
        # remote host; the runner threads it through _run_pipeline_thread
        # to swap spawn_pipeline for the SSH-staged remote run.
        self._remote: Optional[RemoteHost] = None
        self._remote_runner: Optional[RemoteRunner] = None
        self._output_filter: str = "full"
        # v5.2.11: default to "none" so a shared-machine remote never
        # has files deleted without the user explicitly opting in.
        # Pre-v5.2.11 default was "run" (auto-delete per-run output);
        # users who want that behaviour now toggle it via the GUI
        # checkbox or pass --cleanup-remote=run on the CLI.
        self._cleanup_remote: str = "none"
        self._dry_run_cleanup: bool = False
        self._selective_channels: bool = True

    def _enqueue(self, event: "Tuple[str, object]") -> None:
        """Non-blocking enqueue; drops the message on overflow with a
        one-shot warning rather than blocking the reader thread."""
        try:
            self.log_q.put_nowait(event)
        except queue.Full:
            if not self._log_overflow_warned:
                self._log_overflow_warned = True
                try:
                    self.log_q.put_nowait((
                        "line",
                        "[warn] log queue full — dropping further "
                        "lines from the live view. Subsequent output is "
                        "still captured in the help-drawer log tab.\n",
                    ))
                except queue.Full:
                    pass

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def last_output_dir(self) -> Optional[Path]:
        return self._last_output_dir

    def start(
        self,
        pipeline: Path,
        opts: dict,
        *,
        resolve_dirs: Callable[..., Optional[Tuple[Path, Path]]],
        remote: Optional[RemoteHost] = None,
        output_filter: str = "full",
        cleanup_remote: str = "none",
        dry_run_cleanup: bool = False,
        selective_channels: bool = True,
    ) -> None:
        """Spawn the worker thread that runs the pipeline subprocess.

        ``resolve_dirs`` is called with ``(opts, log_q, proc_hook=…)`` where
        ``proc_hook`` is the runner's zipper-tracking callback (forwarded
        to the grouping subprocess so Stop can reach it). Old call sites
        that ignore the kwarg still work.

        When ``remote`` is set, the pipeline runs on the named SSH host
        instead of locally. ``output_filter``, ``cleanup_remote``,
        ``dry_run_cleanup``, and ``selective_channels`` map to the
        corresponding flags on :func:`scripts.allwell_remote.main` —
        they're ignored in local mode.
        """
        self._remote = remote
        self._remote_runner: Optional[RemoteRunner] = (
            RemoteRunner(remote) if remote is not None else None
        )
        self._output_filter = output_filter
        self._cleanup_remote = cleanup_remote
        self._dry_run_cleanup = dry_run_cleanup
        self._selective_channels = selective_channels
        self._thread = threading.Thread(
            target=self._run_pipeline_thread,
            args=(pipeline, opts, resolve_dirs),
            daemon=True,
        )
        self._thread.start()

    def _track_zipper_proc(self, proc: Optional[subprocess.Popen]) -> None:
        """Register / clear the grouping subprocess so ``stop()`` can
        signal it. Passed to ``resolve_input_output`` as ``proc_hook``."""
        self._zipper_proc = proc

    def stop(self) -> None:
        # Flag the in-progress run as cancelled — used by the worker
        # thread to abort between phases (e.g. between grouping and
        # pipeline launch) so a Stop click during the zipper phase
        # doesn't slip through and start the pipeline.
        self._stop_requested.set()

        # Reach both the grouping subprocess (if active) and the pipeline
        # subprocess. Signal each one's whole process group so any
        # multiprocessing workers it spawned get torn down too.
        for proc in (self._zipper_proc, self._proc):
            self._signal_subprocess(proc)

        # For remote runs, the local SIGTERM above tears down the ssh
        # client; ssh -tt propagates SIGHUP to the remote pipeline. The
        # second channel here is belt-and-braces — a fresh ssh that kills
        # the PID we wrote on startup, in case the PTY hangup didn't
        # propagate (older OpenSSH on the remote, NAT teardown, etc.).
        if self._remote_runner is not None and self._proc is not None:
            try:
                inner = getattr(self._proc, "_ssh", None) or self._proc
                self._remote_runner.cancel(inner)
            except Exception:  # noqa: BLE001 — best-effort kill
                pass

    @staticmethod
    def _signal_subprocess(proc: Optional[subprocess.Popen]) -> None:
        """Cross-platform pgid-aware SIGTERM with SIGKILL escalation.

        Thin wrapper around :func:`services.subprocess_kill.signal_subprocess`
        — extracted in v5.1.0 so the bootstrap-from-GUI dialog and any
        future subprocess-managing component can share one
        implementation. The wrapper stays here as a static method to
        keep the PipelineRunner public surface unchanged for callers.
        """
        signal_subprocess(proc)

    def _run_pipeline_thread(
        self,
        pipeline: Path,
        opts: dict,
        resolve_dirs: Callable[..., Optional[Tuple[Path, Path]]],
    ) -> None:
        # Each run starts with a fresh "not cancelled" state.
        self._stop_requested.clear()
        try:
            # Try the new signature first (with proc_hook); fall back for
            # callers that haven't been updated yet.
            try:
                resolved = resolve_dirs(opts, self.log_q, proc_hook=self._track_zipper_proc)
            except TypeError:
                resolved = resolve_dirs(opts, self.log_q)
            # Stop button may have fired during the (potentially long)
            # grouping / resolution phase. Bail before launching the
            # pipeline in that case.
            if self._stop_requested.is_set():
                self._enqueue(("line", "[stopped] Cancelled before pipeline launch.\n"))
                return
            if resolved is None:
                return
            # v5.x.x: ``resolve_dirs`` now returns ``(input_dir,
            # output_dir, on_success)`` where ``on_success`` is a
            # callback that cleans up source artifacts (loose TIFs in
            # ``raw/`` for the zipper-fired path) after a clean
            # pipeline completion. Older 2-tuple callers keep working
            # via the unpack fallback below.
            on_success: Callable[[], None] | None = None
            if len(resolved) == 3:
                input_dir, output_dir, on_success = resolved  # type: ignore[misc]
            else:
                input_dir, output_dir = resolved  # type: ignore[misc]
            if any(input_dir.glob("*.zip")):
                self._enqueue((
                    "line",
                    "[warn] Zip mode detected; folder-mode compression options do not apply.\n",
                ))
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._enqueue(("error", f"Cannot create output dir: {exc}\n"))
                return
            self._last_output_dir = output_dir
            args = build_pipeline_args(pipeline, input_dir, output_dir, opts)
            self._enqueue(("line", f"$ {' '.join(args)}\n"))
            target = (
                f"remote:{self._remote.name}" if self._remote is not None else "local"
            )
            self._enqueue((
                "line",
                f"Input  : {input_dir}\nOutput : {output_dir}\n"
                f"Schema : {opts['filename_schema']}  sep={opts['filename_sep']!r}\n"
                f"Target : {target}\n\n",
            ))
            if self._remote is not None and self._remote_runner is not None:
                self._run_pipeline_remote(
                    input_dir, output_dir, args, opts,
                    on_success=on_success,
                )
            else:
                self._run_pipeline_subprocess(args, on_success=on_success)
        finally:
            self._enqueue(("finished", None))

    def _run_pipeline_subprocess(
        self,
        args: list[str],
        *,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        try:
            self._proc = spawn_pipeline(args)
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                self._enqueue(("line", line))
            self._proc.wait()
            rc = self._proc.returncode
            if rc == 0:
                # v5.x.x: zipper-fired-this-run source cleanup. Only
                # runs on a clean exit (rc == 0); any failure leaves
                # the loose TIFs intact so the user can re-run.
                if on_success is not None:
                    try:
                        on_success()
                    except OSError as exc:
                        self._enqueue((
                            "line",
                            f"[warn] post-success cleanup of source "
                            f"files failed: {exc}\n",
                        ))
                self._enqueue(("done", "Pipeline completed successfully.\n"))
            else:
                self._enqueue(("error", f"Pipeline exited with code {rc}.\n"))
        except Exception as exc:  # noqa: BLE001 — surface any failure to the GUI
            self._enqueue(("error", f"Failed to start pipeline: {exc}\n"))

    def _run_pipeline_remote(
        self,
        input_dir: Path,
        output_dir: Path,
        args: list[str],
        opts: dict,
        *,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        """Stage inputs, launch the pipeline on the remote, stream output.

        The pipeline.subprocess.Popen handle returned by
        ``RemoteRunner.spawn`` is API-compatible with the local spawn
        path, so the reader loop below is the same shape as
        ``_run_pipeline_subprocess``. After the pipeline exits, we
        verify the local manifest and run the configured remote cleanup.
        """
        assert self._remote_runner is not None
        assert self._remote is not None
        zipped = None
        try:
            self._enqueue(("line", f"[remote] target: {self._remote.name}\n"))
            zipped = stage_local_zips(
                input_dir,
                filename_schema=opts["filename_schema"],
                filename_sep=opts["filename_sep"],
                fluor_tokens=opts["fluor_tokens"] if self._selective_channels else None,
                nuclear_token=opts["nuclear_token"] if self._selective_channels else None,
                log_fn=lambda m: self._enqueue(("line", m)),
                move_source_tifs=bool(opts.get("move_source_tifs", False)),
            )
            self._enqueue((
                "line",
                f"[remote] {len(zipped.well_zips)} well-zip(s) staged "
                f"({zipped.mode.value} mode).\n",
            ))

            est = self._remote_runner.estimate(zipped)
            self._enqueue(("line", f"[remote] {est.format()}\n"))

            output_filter = OutputFilter(self._output_filter)

            def _on_xfer_progress(pct: float) -> None:
                # Surface rsync progress as a dedicated queue event so the
                # GUI can show a real bar during upload/download instead
                # of a static spinner. A separate event kind keeps it out
                # of the existing well-progress signal in ProgressTracker.
                self._enqueue(("remote_xfer", pct))

            try:
                batch_size = int(opts.get("remote_batch_size", 0) or 0)
            except (ValueError, TypeError):
                batch_size = 0
            self._proc = self._remote_runner.spawn(
                zipped,
                output_dir,
                args,
                output_filter=output_filter,
                log_fn=lambda m: self._enqueue(("line", m)),
                progress_fn=_on_xfer_progress,
                batch_size=max(0, batch_size),
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                self._enqueue(("line", line))
            self._proc.wait()
            rc = self._proc.returncode

            ok = self._remote_runner.verify_and_cleanup(
                ok=(rc == 0),
                cleanup_mode=self._cleanup_remote,
                dry_run=self._dry_run_cleanup,
                log_fn=lambda m: self._enqueue(("line", m)),
            )
            if rc == 0 and ok:
                # v5.x.x: clean run-to-completion. Fire both success
                # hooks:
                # 1. ``zipped.on_pipeline_success`` — deletes the
                #    source loose TIFs / well folders that
                #    ``_stage_input_zips`` converted to zips inside
                #    the runner's input_dir (i.e., raw/in/ when the
                #    Analyze tab ran the zipper into raw/in/, OR the
                #    user's selected dir when it was already a folder
                #    of mixed-mode TIFs).
                # 2. ``on_success`` — the outer resolve_input_output
                #    callback, which deletes the loose TIFs at the
                #    user's top-level ``raw/`` for the
                #    "raw/ → raw/in/{well}.zip" flow.
                # After this the user's input layout is pure-ZIP and
                # the next run against the same folder skips both
                # the zipper and the staging step.
                if zipped is not None and zipped.on_pipeline_success is not None:
                    try:
                        zipped.on_pipeline_success()
                    except OSError as exc:
                        self._enqueue((
                            "line",
                            f"[warn] post-success cleanup of source files "
                            f"failed: {exc}\n",
                        ))
                if on_success is not None:
                    try:
                        on_success()
                    except OSError as exc:
                        self._enqueue((
                            "line",
                            f"[warn] post-success cleanup of raw TIFs "
                            f"failed: {exc}\n",
                        ))
                self._enqueue(("done", "Remote pipeline completed successfully.\n"))
            elif rc != 0:
                self._enqueue(("error", f"Remote pipeline exited with code {rc}.\n"))
            else:
                self._enqueue((
                    "error",
                    "Remote pipeline finished but local verification failed; "
                    "remote files preserved for retry.\n",
                ))
        except Exception as exc:  # noqa: BLE001 — surface any failure to the GUI
            self._enqueue(("error", f"Failed to run pipeline remotely: {exc}\n"))
        finally:
            if zipped is not None and zipped.cleanup is not None:
                try:
                    zipped.cleanup()
                except OSError:
                    pass
