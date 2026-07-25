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
from util.aw_safe import swallowed


def classify_log_line(line: str) -> str:
    """Return the severity tag for a single pipeline stdout line."""
    ll = line.lower()
    # "failed" catches the per-FOV-group "FAILED <fov> …" line and the run
    # failure summaries — otherwise a failure whose message lacks the substring
    # "error"/"exception" (e.g. "15334400 requested and 0 written") renders as
    # plain INFO and scrolls past unnoticed.
    if "error" in ll or "traceback" in ll or "exception" in ll or "failed" in ll:
        return "ERROR"
    if "warning" in ll or "warn" in ll or "skipping" in ll:
        return "WARNING"
    return "INFO"


_RE_TF_WORKERS = re.compile(r"TF threads/worker\s*:\s*\d+\s+\(workers:\s*(\d+)\s+x")
_RE_MODE_WELLS = re.compile(r"(?:Zip mode|Flat mode|Folder mode):\s+(\d+)\s+well")
# Per-well lifecycle markers emitted by the worker (process_microscopy):
#   "Well A01 — starting (pid …)"  /  "Well A01 — done."  /  "FAILED well A01: …"
# The separator is matched as a generic ``\s+\S+\s+`` token rather than the
# literal " — " so the markers still parse if the em-dash is mangled in transit
# (e.g. a UTF-8 remote stream decoded with a cp1252 Windows locale arrives as
# "Well A01 â€" done."). Without this the Process stage stuck at 0/N while the
# ASCII-only threshold markers reached N/N.
_RE_WELL_START = re.compile(r"Well (\S+)\s+\S+\s+starting")
_RE_WELL_DONE_ID = re.compile(r"Well (\S+)\s+\S+\s+done\.")
_RE_WELL_FAIL = re.compile(r"FAILED well (\S+?)[:\s]")
# Incremental mode: a well with no new FOVs is a cheap no-op — it never emits
# "— done.", so it needs its own terminal marker to count + colour on the plate.
_RE_WELL_UPTODATE = re.compile(r"Well (\S+)\s+\S+\s+up to date")
# A well skipped because its output already exists (re-run without --force) —
# it's complete, so the dashboard must count it as done, not leave it pending.
_RE_WELL_SKIP = re.compile(r"SKIP well (\S+)\s+\S+\s+output already exists")
# Remote incremental: the runner emits this for wells it didn't need to upload /
# re-fetch because their local output is already current — marks them complete.
_RE_REMOTE_UPTODATE = re.compile(r"\[remote\] ✓ ([A-Ha-h]\d{1,2}): up to date")
# Remote per-well lifecycle: input zip uploaded, then the output is fetched as
# TWO separate transfers — the well's output zip AND its per-well CSV(s). A well
# is only "downloaded" once BOTH have landed; the ``✓ materialised`` line fires
# even when the CSV pull failed (it's retried later), so it must NOT be the
# download trigger on its own.
_RE_REMOTE_UP_WELL = re.compile(r"\[remote\] ↑.*?([A-Ha-h]\d{1,2})\.zip")
_RE_FETCH_ZIP = re.compile(r"\[remote\] ↓ ([A-Ha-h]\d{1,2}): [\d.]+ MB zip")
_RE_FETCH_CSV = re.compile(r"\[remote\] ↓ ([A-Ha-h]\d{1,2}): \d+ CSV file")
# Auto-threshold pass completes each well: "completed well 3/12 (A01_out.zip)".
_RE_THRESH_WELL = re.compile(r"Auto-threshold: completed well \d+/\d+ \(([A-Ha-h]\d{1,2})")
# Per-well FOV-group progress (one group = one FOV × timepoint), well-tagged so
# it's attributable even when wells process in parallel:
#   "Well A01 — 12 FOV-group(s)"  (the total, at well start)
#   "Well A01 — FOV 3/12 done"    (each group as it completes)
_RE_WELL_FOV_TOTAL = re.compile(r"Well (\S+)\s+\S+\s+(\d+) FOV-group")
_RE_WELL_FOV_DONE = re.compile(r"Well (\S+)\s+\S+\s+FOV (\d+)/(\d+) done")
# Finalize-phase status lines. After the last well the run is still busy
# (auto-threshold scans every well's zip; a remote run then drains downloads
# and cleans up) — surface what it's doing so "Finalizing…" doesn't look hung.
_RE_TF_SCAN = re.compile(r"Auto-threshold: scanning (\d+) well")
_RE_TF_PROG = re.compile(r"Auto-threshold: completed well (\d+)/(\d+)")


class ProgressTracker:
    """Pure log-line parser that tracks well-count progress.

    Instances are stateful (zip-mode warning + well counters) but never touch
    the GUI; ``parse(line)`` returns events that the tab forwards to the user.
    """

    def __init__(self) -> None:
        self.zip_mode_warning_logged = False
        self.well_total = 0
        self.well_done = 0
        self._finalize_emitted = False
        self._fetch_parts: dict[str, set] = {}   # well -> {"zip","csv"} seen

    def reset(self) -> None:
        self.zip_mode_warning_logged = False
        self.well_total = 0
        self.well_done = 0
        self._finalize_emitted = False
        self._fetch_parts = {}

    def _mark_fetch_part(self, well: str, part: str) -> bool:
        """Record that *part* ("zip"/"csv") of *well*'s output has landed.
        Returns True only on the transition where BOTH parts are now present
        (i.e. the well has just become fully downloaded)."""
        seen = self._fetch_parts.setdefault(well, set())
        was_complete = {"zip", "csv"} <= seen
        seen.add(part)
        return not was_complete and {"zip", "csv"} <= seen

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
        # Remote transfer phases (the runner logs "[remote] ↑" while uploading
        # inputs, "[remote] ↓" while fetching outputs) drive the active stage.
        # Per-well: the input zip's upload and the output zip's local landing.
        if "[remote] ↑" in line:
            events.append(("stage", "upload"))
            mu = _RE_REMOTE_UP_WELL.search(line)
            if mu:
                events.append(("well_stage", (mu.group(1), "upload")))
        elif "[remote] ↓" in line:
            events.append(("stage", "download"))
        # A well is "downloaded" only once BOTH its output zip and its CSV(s)
        # have transferred — not on the first part, and not on ``✓ materialised``
        # (which fires even when the CSV pull failed and is deferred to a retry).
        mz = _RE_FETCH_ZIP.search(line)
        if mz and self._mark_fetch_part(mz.group(1), "zip"):
            events.append(("well_stage", (mz.group(1), "download")))
        mc = _RE_FETCH_CSV.search(line)
        if mc and self._mark_fetch_part(mc.group(1), "csv"):
            events.append(("well_stage", (mc.group(1), "download")))
        # Per-well compute lifecycle (fires regardless of the well_total banner).
        ms = _RE_WELL_START.search(line)
        if ms:
            events.append(("stage", "process"))
            events.append(("well_stage", (ms.group(1), "active")))
        mf = _RE_WELL_FAIL.search(line)
        if mf:
            events.append(("well_stage", (mf.group(1), "error")))
        mt = _RE_THRESH_WELL.search(line)
        if mt:
            events.append(("well_stage", (mt.group(1), "threshold")))
        # Remote: a well skipped because its local output is already current —
        # mark it fetched/complete (green) on the plate.
        mru = _RE_REMOTE_UPTODATE.search(line)
        if mru:
            events.append(("well_stage", (mru.group(1), "download")))
        # Finalize-phase status — replace the generic "Finalizing…" with what
        # the run is actually doing so it doesn't read as a hang.
        if _RE_TF_SCAN.search(line):
            events.append(("status", "Auto-thresholding…"))
        mtp = _RE_TF_PROG.search(line)
        if mtp:
            events.append(("status", f"Auto-thresholding {mtp.group(1)}/{mtp.group(2)}…"))
        if "draining fetcher" in line or "] ↓ fetcher:" in line:
            events.append(("status", "Finalizing downloads…"))
        elif "[remote] verify:" in line or "[remote] removed " in line:
            events.append(("status", "Cleaning up remote…"))
        # Per-well FOV-group sub-progress (within the Process stage).
        mft = _RE_WELL_FOV_TOTAL.search(line)
        if mft:
            events.append(("well_fov", (mft.group(1), 0, int(mft.group(2)))))
        mfd = _RE_WELL_FOV_DONE.search(line)
        if mfd:
            events.append(("well_fov",
                           (mfd.group(1), int(mfd.group(2)), int(mfd.group(3)))))
        # Auto-threshold pass runs after the last well → the "Threshold" stage.
        if not self._finalize_emitted and line.lstrip().startswith("Auto-threshold:"):
            self._finalize_emitted = True
            events.append(("stage", "finalize"))
        if not self.well_total:
            m = _RE_MODE_WELLS.search(line)
            if m:
                self.well_total = int(m.group(1))
                events.append(("well_total", self.well_total))
                events.append(("stage", "process"))
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
            md = _RE_WELL_DONE_ID.search(line)
            if md:
                self.well_done += 1
                events.append(("well_done", (self.well_done, self.well_total)))
                events.append(("well_stage", (md.group(1), "process")))
            else:
                # Incremental up-to-date well: counts toward completion and
                # advances to the processed stage (the auto-threshold pass then
                # turns it green locally; the remote ✓ marker does so remotely).
                mu = _RE_WELL_UPTODATE.search(line)
                if mu:
                    self.well_done += 1
                    events.append(("well_done", (self.well_done, self.well_total)))
                    events.append(("well_stage", (mu.group(1), "process")))
                # Already-complete well skipped on a re-run: it's done, so count
                # it and advance it to the processed stage (the auto-threshold
                # pass over its existing _out.zip then turns it green).
                ms = _RE_WELL_SKIP.search(line)
                if ms:
                    self.well_done += 1
                    events.append(("well_done", (self.well_done, self.well_total)))
                    events.append(("well_stage", (ms.group(1), "process")))
        return events


class PipelineRunner:
    """Run the pipeline subprocess on a daemon thread and report progress.

    The runner owns its thread, subprocess handle, and event queue. Callers
    (the GUI) consume events via :meth:`poll` and call :meth:`stop` to cancel.
    """

    # Cap the log queue so a stalled UI thread can't let the reader stuff
    # unbounded lines into memory. Overflow drops are surfaced to the GUI
    # via a single warning line — the ring buffer in allwell captures
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
        # Guards ``_proc``/``_zipper_proc`` across the worker thread (which
        # assigns them at launch) and the GUI thread (which reads them in
        # stop()). Without it a Stop click racing a subprocess launch could
        # read _proc as None *after* the worker already passed its stop check,
        # orphaning the subprocess (audit E21). The launch path checks
        # _stop_requested and assigns _proc under this lock so the two are
        # atomic with respect to stop().
        self._proc_lock: threading.Lock = threading.Lock()
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
        with self._proc_lock:
            self._zipper_proc = proc

    def _spawn_tracked(self, spawn_fn: Callable[[], subprocess.Popen]):
        """Launch the pipeline subprocess and record it in ``_proc`` under the
        lock, but only if a Stop has not already been requested.

        Making the stop-check and the assignment atomic (w.r.t. ``stop()``,
        which sets ``_stop_requested`` then reads ``_proc`` under the same
        lock) closes the launch-after-stop race in audit E21: either stop()
        wins and we never launch, or we launch and stop() sees the assigned
        ``_proc`` and signals it. Returns the ``Popen`` or ``None`` if the run
        was already cancelled.
        """
        with self._proc_lock:
            if self._stop_requested.is_set():
                return None
            self._proc = spawn_fn()
            return self._proc

    def stop(self) -> None:
        # Flag the in-progress run as cancelled — used by the worker
        # thread to abort between phases (e.g. between grouping and
        # pipeline launch) so a Stop click during the zipper phase
        # doesn't slip through and start the pipeline.
        self._stop_requested.set()

        # Snapshot both subprocess handles under the lock so we can't read a
        # half-assigned _proc mid-launch (audit E21). Signalling is done on the
        # snapshot outside the lock — a SIGTERM must not block a launch racing
        # on the same lock.
        with self._proc_lock:
            zipper_proc, proc = self._zipper_proc, self._proc

        # Reach both the grouping subprocess (if active) and the pipeline
        # subprocess. Signal each one's whole process group so any
        # multiprocessing workers it spawned get torn down too.
        for p in (zipper_proc, proc):
            self._signal_subprocess(p)

        # For remote runs, the local SIGTERM above tears down the ssh
        # client; ssh -tt propagates SIGHUP to the remote pipeline. The
        # second channel here is belt-and-braces — a fresh ssh that kills
        # the PID we wrote on startup, in case the PTY hangup didn't
        # propagate (older OpenSSH on the remote, NAT teardown, etc.).
        if self._remote_runner is not None and proc is not None:
            try:
                inner = getattr(proc, "_ssh", None) or proc
                self._remote_runner.cancel(inner)
            except Exception:  # best-effort kill
                swallowed()  # was: pass (silent except)

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
        # The Analyze "Scratch disk" field routes per-well working files via
        # ALLWELL_SCRATCH_DIR. Set it on this process's env so the local
        # pipeline subprocess (inherits os.environ) and the in-process remote
        # staging both honor it; clear it when empty so a prior run's value
        # can't leak into this one.
        _scratch = opts.get("scratch_dir")
        if _scratch:
            os.environ["ALLWELL_SCRATCH_DIR"] = str(_scratch)
        else:
            os.environ.pop("ALLWELL_SCRATCH_DIR", None)
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
        except Exception as exc:
            # A setup-phase failure (grouping/zipper RuntimeError, a bad
            # build_pipeline_args value, etc.) must surface as an ``error`` so
            # the GUI shows *why* — the bare try/finally previously fell
            # straight through to ``finished`` with no reason (audit E6).
            self._enqueue(("error", f"Pipeline setup failed: {exc}\n"))
        finally:
            self._enqueue(("finished", None))

    def _run_pipeline_subprocess(
        self,
        args: list[str],
        *,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        try:
            proc = self._spawn_tracked(lambda: spawn_pipeline(args))
            if proc is None:
                self._enqueue(("line", "[info] Run cancelled before launch.\n"))
                return
            assert proc.stdout is not None
            for line in proc.stdout:
                self._enqueue(("line", line))
            proc.wait()
            rc = proc.returncode
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
        except Exception as exc:  # surface any failure to the GUI
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
                incremental=bool(opts.get("incremental", False)),
                prior_output_dir=output_dir,
                csv_prefix=opts.get("csv_prefix", "measurements"),
            )
            # Incremental: wells whose local output already covers every input
            # FOV were dropped from the upload. Surface a synthetic per-well
            # marker so the dashboard shows them complete (they're already
            # current locally) rather than stuck pending.
            for tok in zipped.up_to_date_wells:
                self._enqueue(("line", f"[remote] ✓ {tok}: up to date (local)\n"))
            self._enqueue((
                "line",
                f"[remote] {len(zipped.well_zips)} well-zip(s) staged "
                f"({zipped.mode.value} mode).\n",
            ))
            # The remote grouping runs inside stage_local_zips (not the local
            # zipper subprocess), so it never emitted the per-well group events
            # the dashboard's Group stage listens for — Group sat at 0 on remote
            # runs. Surface them now that staging is complete.
            self._enqueue(("zipper_start", len(zipped.well_zips)))
            for wz in zipped.well_zips:
                self._enqueue(("zipper_well", wz.stem))
            self._enqueue(("zipper_done", None))

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
            proc = self._spawn_tracked(lambda: self._remote_runner.spawn(
                zipped,
                output_dir,
                args,
                output_filter=output_filter,
                log_fn=lambda m: self._enqueue(("line", m)),
                progress_fn=_on_xfer_progress,
                batch_size=max(0, batch_size),
            ))
            if proc is None:
                self._enqueue(("line", "[info] Remote run cancelled before launch.\n"))
                return
            assert proc.stdout is not None
            for line in proc.stdout:
                self._enqueue(("line", line))
            proc.wait()
            rc = proc.returncode

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
        except Exception as exc:  # surface any failure to the GUI
            self._enqueue(("error", f"Failed to run pipeline remotely: {exc}\n"))
        finally:
            # Always close any scheduler job this run spawned — on success,
            # failure, or exception. ``srun`` normally exits when the
            # pipeline ends, so this is a no-op in the common case; it's the
            # guarantee that an orphaned SLURM allocation never lingers in
            # the queue waiting on the user. No-op on direct-execution hosts.
            if self._remote_runner is not None:
                try:
                    self._remote_runner.close_remote_job(
                        log_fn=lambda m: self._enqueue(("line", m)),
                    )
                except Exception:  # best-effort teardown
                    swallowed("remote run: scheduler job close failed")
            if zipped is not None and zipped.cleanup is not None:
                try:
                    zipped.cleanup()
                except OSError:
                    swallowed("remote run: staging zip cleanup failed")
