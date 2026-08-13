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
    well_xfer     tuple      (well, "up"|"down", fraction) — one well's own
                             upload or download progress, for its dial on the
                             plate map. Parsed out of the log stream by
                             ProgressTracker; never written to the log.
    sched         tuple      (state, job_id, launcher) — the run is queued on a
                             remote scheduler, or has just been granted its
                             node. ``state`` is "waiting" | "queued" | "granted";
                             ``job_id`` is "" until the scheduler names it.
    done          str        Pipeline finished successfully.
    error         str        Pipeline failed; payload is a message.
    finished      None       Final event, always emitted in a finally.
"""

from __future__ import annotations

import collections
import os
import queue
import re
import subprocess
import threading
from pathlib import Path
from collections.abc import Callable

from services.pipeline_service import build_pipeline_args, spawn_pipeline
from services.remote_config import RemoteHost
from services.remote_runner import (
    OutputFilter,
    RemoteRunner,
    TRANSIENT_PREFIX,
    WELL_XFER_PREFIX,
    stage_local_zips,
)
from services.subprocess_kill import signal_subprocess
from util.aw_safe import swallowed

#: How many of the pipeline's own trailing output lines ride along with a
#: non-zero exit. Enough to carry a refusal sentence or a traceback's last
#: frame; short enough that the dashboard headline stays readable.
_ERROR_TAIL_LINES = 6

#: Error-level lines that are never a *cause*, and so cannot be quoted as one.
#: ``exiting non-zero`` is ``process_microscopy``'s own end-of-run summary,
#: emitted after the per-well error that produced it and saying only what the
#: exit code already says. The traceback header scores ERROR on the word
#: "traceback" while the exception line that explains it comes further down.
_RE_TRACEBACK_HEAD = re.compile(r"^Traceback \(most recent call last\)")
_RE_NOT_A_CAUSE = re.compile(
    r"exiting non-zero|^Traceback \(most recent call last\)", re.IGNORECASE)

# ``process_microscopy`` logs as "%(asctime)s  %(levelname)-8s  %(message)s"
# (workers add "[worker N]" after the level), so most lines state their own
# severity and there is nothing to infer. Anchored at the start so a level word
# appearing inside a *message* can't be mistaken for the line's own level.
_RE_LOG_LEVEL = re.compile(
    r"^\S+\s+\S+\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")

_LEVEL_TO_TAG = {
    "DEBUG": "INFO", "INFO": "INFO", "WARNING": "WARNING",
    "ERROR": "ERROR", "CRITICAL": "ERROR",
}

# Severity a line states about itself, for the lines that carry no ``levelname``
# — the app's own ``[warn]`` / ``[info]`` tags, and the ``note:`` marker the
# remote runner uses for "this is context, not a verdict". Checked *before* the
# keyword heuristics below, which read paths as well as prose and so cannot tell
# a failure from a directory named ``errors/``.
_RE_EXPLICIT_TAG = re.compile(r"\[(info|warn|warning|error)\]", re.IGNORECASE)
_TAG_TO_LEVEL = {"info": "INFO", "warn": "WARNING", "warning": "WARNING",
                 "error": "ERROR"}
_RE_SELF_DECLARED_NOTE = re.compile(r"\bnote:", re.IGNORECASE)


def classify_log_line(line: str) -> str:
    """Return the severity tag for a single pipeline stdout line.

    Trust the line's own ``levelname`` when it has one. The heuristics below are
    for the lines that don't: bare subprocess stderr, Python tracebacks, and the
    zipper's ``[zipper]`` output.

    Inferring severity from keywords is unreliable in the other direction too —
    the end-of-run summary is logged at INFO and reads "wells failed (no output)
    : 0 … FOV-groups failed : 0", so a substring test for "failed" painted a
    clean run's summary red. Those are column labels, not a verdict.

    **A line that declares its own severity is believed before it is sniffed.**
    The keyword pass reads whole lines, including *paths*, and the pipeline
    writes its per-failure tracebacks to a directory called ``errors/``. So
    ``[remote] note: …/errors/ not present on remote`` — which a clean run
    always emits, because that directory is only created when something fails —
    was painted red by the name of the folder it was reporting the absence of.
    Good news, rendered as an alarm. The same line about ``pipeline_info.json``
    came out INFO, which is how the mechanism gives itself away.
    """
    m = _RE_LOG_LEVEL.match(line)
    if m:
        return _LEVEL_TO_TAG[m.group(1)]

    tag = _RE_EXPLICIT_TAG.search(line)
    if tag:
        return _TAG_TO_LEVEL[tag.group(1).lower()]
    if _RE_SELF_DECLARED_NOTE.search(line):
        # "note:" is the author saying this is context, not a verdict. Nothing
        # that reports a failure introduces itself as a note.
        return "INFO"

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


def exit_message(rc: int, tail, *, where: str = "Pipeline") -> str:
    """The failure text for a pipeline that exited *rc*, quoting what it said.

    ``Pipeline exited with code 2.`` names nothing, and for a run refused
    before any work — the disk preflight, an input with no wells the pipeline
    recognises — the exit code is the *only* thing the user was shown. The
    pipeline always explains itself on stdout first; this carries that
    explanation to where the failure is reported.
    """
    lines = [ln.strip() for ln in (tail or []) if ln and ln.strip()]
    code = f"{where} exited with code {rc}."
    # Only a line the log classifier calls an ERROR may be quoted as the cause.
    # A substring test for "error" reads paths and column labels — the pipeline
    # writes its tracebacks to a directory named ``errors/`` and its clean
    # summary says "wells failed : 0" — which is the whole reason
    # ``classify_log_line`` exists forty lines below. And with no such line
    # there is nothing to quote: presenting the last line, whatever it is, as
    # the reason invents a cause ("quantify: 1.2 s" is not why a run stopped).
    errs = [ln for ln in lines if classify_log_line(ln) == "ERROR"]
    if not errs:
        return code + "\n"
    # The *earliest* error still in the ring is the cause; anything after it is
    # that cause's fallout (a cleanup that could not find the directory the
    # first error had already abandoned, an abort summary counting the first
    # error). One rule, shared with the GUI: ``AnalyzeTab._poll_log`` latches
    # the first ``("error", …)`` event of a run for the same reason, and the
    # two must not disagree about which line the run's reason is.
    #
    # "Earliest" is over the lines that are *candidates to be a cause*, which is
    # not every line the classifier calls an ERROR. Two shapes are error-level
    # and still say nothing about why:
    #
    # * the run's own exit summary ("N well(s) failed or produced partial
    #   output — exiting non-zero…"), which restates the exit code the reader
    #   already has and is emitted *after* the per-well error that caused it;
    # * a traceback's ``Traceback (most recent call last):`` header, which
    #   scores ERROR on the word "traceback" while the exception line beneath
    #   it — the actual reason — comes later.
    #
    # Both sit at an end of the list, which is why neither ``errs[0]`` nor
    # ``errs[-1]`` is the rule on its own: dropping them first makes "earliest"
    # mean earliest *reason*. If they are all there is, quoting one still beats
    # the bare code.
    candidates = [ln for ln in errs if not _RE_NOT_A_CAUSE.search(ln)] or errs
    # A traceback is the one shape that reads bottom-up: Python prints the
    # frames first and the exception last, and the intervening ``raise Foo(…)``
    # source lines score ERROR on the exception's own name. Inside one, the
    # last candidate is the reason.
    if any(_RE_TRACEBACK_HEAD.search(ln) for ln in lines):
        candidates = candidates[-1:]
    #
    # Reason first: this text leads a dashboard headline, and the exit code is
    # the part a user can do nothing with.
    # ``_RE_LOG_LEVEL`` already matches exactly this prefix — one spelling of
    # "where does the message start", not two.
    reason = _RE_LOG_LEVEL.sub("", candidates[0]).strip()
    return f"{reason} ({code[:-1].lower()})\n"



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

# A scheduler-backed host queues the pipeline rather than starting it, and a
# queue wait is indistinguishable from a hang: the upload finishes, the log
# stops. Four lines mark the two ends of that wait.
#
# The first and last are the app's own (``_scheduler_handoff_lines`` and the
# echo from inside the allocation), so their wording is ours to keep. The two
# ``srun:`` ones are SLURM's own stderr, merged into the run's stdout by the
# launcher's ``stderr=subprocess.STDOUT`` — and they are the ONLY source of the
# job id before the allocation exists, because the ``.slurm`` sidecar is written
# from inside it. They are matched permissively and treated as a bonus: a site
# whose scheduler words them differently still gets the wait announced and timed
# off our own lines, and loses only the id.
_RE_SCHED_REQUEST = re.compile(
    r"\[remote\] scheduler: (?:batch \d+: )?requesting allocation via: (.+)")
_RE_SCHED_GRANTED = re.compile(
    r"\[remote\] scheduler: allocation granted — job (\S+)")
_RE_SCHED_QUEUED = re.compile(
    r"srun: job (\d+) queued and waiting for resources", re.IGNORECASE)
_RE_SCHED_ALLOCATED = re.compile(
    r"srun: job (\d+) has been allocated resources", re.IGNORECASE)
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


def is_transient_progress(line: str) -> bool:
    """Whether *line* is live state for the status bar, not a log entry."""
    return line.lstrip().startswith(TRANSIENT_PREFIX)


# ``[remote] ⋯ xfer down C02 0.4180`` — see ``remote_runner.WELL_XFER_PREFIX``.
_RE_WELL_XFER = re.compile(r"^(up|down)\s+([A-Ha-h]\d{1,2})\s+([0-9.]+)\s*$")


def parse_well_transfer(line: str) -> tuple[str, str, float] | None:
    """``(well, direction, fraction)`` for a per-well transfer-progress line.

    ``None`` for anything else, including a malformed line carrying the marker
    — a transfer sample that cannot be read is dropped rather than guessed at,
    since the only thing it drives is a gauge position.
    """
    stripped = line.lstrip()
    if not stripped.startswith(WELL_XFER_PREFIX):
        return None
    m = _RE_WELL_XFER.match(stripped[len(WELL_XFER_PREFIX):].strip())
    if not m:
        return None
    try:
        fraction = float(m.group(3))
    except ValueError:
        return None
    return m.group(2).upper(), m.group(1), max(0.0, min(1.0, fraction))


class ProgressTracker:
    """Pure log-line parser that tracks well-count progress.

    Instances are stateful (zip-mode warning + well counters) but never touch
    the GUI; ``parse(line)`` returns events that the tab forwards to the user.
    """

    def __init__(self) -> None:
        self.zip_mode_warning_logged = False
        self.well_total = 0
        self.well_done = 0
        self.well_repeats = 0
        self._finalize_emitted = False
        self._fetch_parts: dict[str, set] = {}   # well -> {"zip","csv"} seen
        self._done_wells: set[str] = set()       # wells already counted done

    def reset(self) -> None:
        self.zip_mode_warning_logged = False
        self.well_total = 0
        self.well_done = 0
        self.well_repeats = 0
        self._finalize_emitted = False
        self._fetch_parts = {}
        self._done_wells = set()

    def _mark_fetch_part(self, well: str, part: str) -> bool:
        """Record that *part* ("zip"/"csv") of *well*'s output has landed.
        Returns True only on the transition where BOTH parts are now present
        (i.e. the well has just become fully downloaded)."""
        seen = self._fetch_parts.setdefault(well, set())
        was_complete = {"zip", "csv"} <= seen
        seen.add(part)
        return not was_complete and {"zip", "csv"} <= seen

    def _count_well_done(self, well: str) -> list[tuple[str, object]]:
        """Record that *well* reached a terminal state, counting it once.

        Progress is the number of DISTINCT wells that finished, not the number
        of completion markers seen. The two diverge in batched remote mode: the
        pipeline is launched once per batch against a shared remote input
        directory, so a well whose input zip is still there when the next batch
        starts is announced terminal a second time (re-processed, or skipped
        with "output already exists"). Counting markers made the headline reach
        "N / N wells (100%)" with wells still outstanding, and then climb past
        100% — the run reported itself complete while it was not.

        A repeat is reported rather than absorbed: it means a well was handled
        twice, which is either wasted compute or a stale input left behind, and
        the log is the only place that can say so.

        The announced total is a floor, never a ceiling. If more distinct wells
        finish than the banner promised, the total is raised to what was
        actually observed so the fraction stays truthful.
        """
        token = well.upper()
        if token in self._done_wells:
            self.well_repeats += 1
            return [("well_repeat", token)]
        self._done_wells.add(token)
        self.well_done = len(self._done_wells)
        events: list[tuple[str, object]] = []
        if self.well_done > self.well_total:
            self.well_total = self.well_done
            events.append(("well_total", self.well_total))
        events.append(("well_done", (self.well_done, self.well_total)))
        events.append(("well_stage", (well, "process")))
        return events

    def parse(self, line: str) -> list[tuple[str, object]]:
        """Return queue events for a single stdout line.

        Caller is expected to forward the events to its GUI queue. Returning a
        list (rather than enqueuing directly) keeps this method test-friendly
        and free of any dependency on the queue object.
        """
        events: list[tuple[str, object]] = []
        if line.startswith("[zipper]"):
            return events
        # The "Zip mode detected" warning is emitted up-front in
        # _run_pipeline_thread (before the pipeline launches) so it
        # arrives even on early failures. No second emission needed
        # when the pipeline echoes the same banner via stdout.
        if "Zip mode:" in line or "Zip mode complete" in line:
            self.zip_mode_warning_logged = True
        if line.lstrip().startswith(WELL_XFER_PREFIX):
            # Per-well upload/download progress: a gauge position for one
            # well's dial, and nothing else. Claimed before the generic
            # transient branch because it carries the same marker plus more —
            # and claimed even when it doesn't parse, so a malformed sample is
            # dropped rather than falling through to the status line, where it
            # would show the user "xfer down C02 0.4180" as prose.
            xfer = parse_well_transfer(line)
            return [("well_xfer", xfer)] if xfer is not None else []
        if is_transient_progress(line):
            # Live state: straight to the status line, nothing else to parse.
            return [("status", line.strip()[len(TRANSIENT_PREFIX):].strip())]
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
        # The end-of-run retry backstop emits these same two lines (tagged
        # "(end-of-run retry)") for a well it rescues, so a well fixed at the
        # end completes the pair by the same route. It has to report both halves
        # even where it only had to pull one: the in-stream fetcher returns on a
        # failed zip pull *before* reaching the CSVs, so a well that reaches the
        # backstop has usually registered neither.
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
        # Scheduler hand-off. Reported as its own kind rather than folded into
        # ``status`` because the GUI has to start and stop a clock, not just
        # write a string — a wait that does not visibly count up is the hang it
        # was mistaken for.
        msr = _RE_SCHED_REQUEST.search(line)
        if msr:
            events.append(("sched", ("waiting", "", msr.group(1).strip())))
        msq = _RE_SCHED_QUEUED.search(line)
        if msq:
            events.append(("sched", ("queued", msq.group(1), "")))
        for _pat in (_RE_SCHED_GRANTED, _RE_SCHED_ALLOCATED):
            msg = _pat.search(line)
            if msg:
                events.append(("sched", ("granted", msg.group(1), "")))
                break
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
            #
            # The three terminal markers — finished, incremental up-to-date,
            # and already-complete-so-skipped — all mean the same thing to the
            # progress count, and all name their well, so they share one
            # identity-aware counter. An up-to-date well never emits "— done.";
            # a skipped one never runs at all; both are complete.
            md = (
                _RE_WELL_DONE_ID.search(line)
                or _RE_WELL_UPTODATE.search(line)
                or _RE_WELL_SKIP.search(line)
            )
            if md:
                events.extend(self._count_well_done(md.group(1)))
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
        self.log_q: queue.Queue[tuple[str, object]] = queue.Queue(
            maxsize=self._LOG_QUEUE_MAXSIZE,
        )
        self._proc: subprocess.Popen | None = None
        # The grouping-phase subprocess (WellPlateZipper). Tracked
        # separately so a Stop click during grouping cancels it instead
        # of waiting for it to finish before reaching the pipeline.
        self._zipper_proc: subprocess.Popen | None = None
        # Guards ``_proc``/``_zipper_proc`` across the worker thread (which
        # assigns them at launch) and the GUI thread (which reads them in
        # stop()). Without it a Stop click racing a subprocess launch could
        # read _proc as None *after* the worker already passed its stop check,
        # orphaning the subprocess (audit E21). The launch path checks
        # _stop_requested and assigns _proc under this lock so the two are
        # atomic with respect to stop().
        self._proc_lock: threading.Lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_output_dir: Path | None = None
        self._log_overflow_warned: bool = False
        # Set on Stop; checked between the zipper phase and the pipeline
        # launch so a cancel during grouping doesn't slip through and
        # start a fresh pipeline run.
        self._stop_requested: threading.Event = threading.Event()
        # Set on Suspend, alongside ``_stop_requested``. The signal sent is the
        # same one Stop sends; what differs is that the pipeline catches it and
        # winds down, and that this runner then leaves the remote's files alone
        # so a resume can pick them up. Read by ``was_suspended``.
        self._suspend_requested: threading.Event = threading.Event()
        # Snapshots of the two events above, taken when a failure is queued —
        # see ``cancel_state_at_failure``.
        self._error_was_stopped = False
        self._error_was_suspended = False
        # Remote-execution state. Set in start() when the user chose a
        # remote host; the runner threads it through _run_pipeline_thread
        # to swap spawn_pipeline for the SSH-staged remote run.
        self._remote: RemoteHost | None = None
        self._remote_runner: RemoteRunner | None = None
        self._output_filter: str = "full"
        # v5.2.11: default to "none" so a shared-machine remote never
        # has files deleted without the user explicitly opting in.
        # Pre-v5.2.11 default was "run" (auto-delete per-run output);
        # users who want that behaviour now toggle it via the GUI
        # checkbox or pass --cleanup-remote=run on the CLI.
        self._cleanup_remote: str = "none"
        self._dry_run_cleanup: bool = False
        self._selective_channels: bool = True

    # Completion is decided by these three event kinds — a job queue's
    # scheduler advances or blocks entirely on having seen one. Dropping one
    # on queue overflow doesn't cost a log line, it stalls the job forever,
    # so they're exempt from the drop-on-overflow policy below.
    _CONTROL_KINDS = frozenset({"done", "error", "finished"})

    def _enqueue(self, event: tuple[str, object]) -> None:
        """Non-blocking enqueue for a log line; drops the message on overflow
        with a one-shot warning rather than blocking the reader thread.

        ``done`` / ``error`` / ``finished`` are never dropped — they block
        until the reader (draining at 80ms intervals in ``_poll_log``) makes
        room, since losing one leaves a run's completion unobservable.
        """
        if event[0] in self._CONTROL_KINDS:
            self.log_q.put(event)
            return
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
    def last_output_dir(self) -> Path | None:
        return self._last_output_dir

    @property
    def was_stopped(self) -> bool:
        """Whether the most recent (or in-flight) run was cancelled via
        :meth:`stop`. Cleared only at the *start* of the next run, so it is
        still readable after a stopped run's ``("finished", None)`` event —
        letting a caller tell a user-requested cancel apart from a genuine
        failure, which otherwise both surface as ``("error", …)``."""
        return self._stop_requested.is_set()

    def start(
        self,
        pipeline: Path,
        opts: dict,
        *,
        resolve_dirs: Callable[..., tuple[Path, Path] | None],
        remote: RemoteHost | None = None,
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
        self._remote_runner: RemoteRunner | None = (
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

    def _track_zipper_proc(self, proc: subprocess.Popen | None) -> None:
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

    def suspend(self) -> None:
        """Ask the run to wind down at a FOV-group boundary and exit cleanly.

        The signal is the one ``stop`` already sends — a SIGTERM to the process
        group. The difference is entirely on the receiving end: since
        ``pipeline.suspend`` installs a handler, the pipeline stops taking on new
        FOV-groups, lets the ones in flight finish, and exits through the normal
        end-of-well path so every well that got work done writes its CSV and its
        ``<well>_out.zip``. Those artifacts are what a resumed run reads as its
        done-set, so nothing else needs saving here.

        Two things are deliberately suppressed for the rest of this run:

        * **The SIGKILL escalation** that follows five seconds after the
          SIGTERM. A well mid-FOV can easily need longer than that, and killing
          it at second five would throw away exactly the work this is trying to
          preserve. A suspend that hangs is still recoverable — Stop is right
          there, and its second signal is a hard kill by design.
        * **Remote cleanup.** Auto-delete is on by default, and it only fires
          after a verified-successful run; a suspend is not that, but the guard
          is made explicit here rather than left to depend on it. The partial
          output on the remote is the thing a resume needs.
        """
        self._suspend_requested.set()
        self._cleanup_remote = "none"
        self.stop(escalate=False)

    @property
    def was_suspended(self) -> bool:
        """Whether the most recent run was wound down via :meth:`suspend`.

        Like ``was_stopped``, readable after the run's ``("finished", None)``
        event so a caller can tell a suspend apart from a cancel apart from a
        failure — all three of which otherwise look the same from outside.
        """
        return self._suspend_requested.is_set()

    @property
    def cancel_state_at_failure(self) -> tuple[bool, bool]:
        """``(stopped, suspended)`` as they stood when the failure was queued.

        ``was_stopped`` / ``was_suspended`` are live flags, and the GUI reads
        them one timer tick after the event reaches the queue. A Stop clicked
        inside that window makes a genuine failure read as a deliberate cancel:
        amber "Cancelled", no raw log opened, reason never shown — which is
        precisely the reported bug this reporting path exists to fix, put back
        by a race. So the answer is taken at ``_enqueue_error`` time and read
        from here.
        """
        return self._error_was_stopped, self._error_was_suspended

    def _enqueue_error(self, message: str) -> None:
        """Queue a failure, recording the cancel state at this instant."""
        self._error_was_stopped = self._stop_requested.is_set()
        self._error_was_suspended = self._suspend_requested.is_set()
        self._enqueue(("error", message))

    def stop(self, *, escalate: bool = True) -> None:
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
            self._signal_subprocess(p, escalate=escalate)

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
    def _signal_subprocess(proc: subprocess.Popen | None, *,
                           escalate: bool = True) -> None:
        """Cross-platform pgid-aware SIGTERM with SIGKILL escalation.

        Thin wrapper around :func:`services.subprocess_kill.signal_subprocess`
        — extracted in v5.1.0 so the bootstrap-from-GUI dialog and any
        future subprocess-managing component can share one
        implementation. The wrapper stays here as a static method to
        keep the PipelineRunner public surface unchanged for callers.
        """
        signal_subprocess(proc, escalate=escalate)

    def _run_pipeline_thread(
        self,
        pipeline: Path,
        opts: dict,
        resolve_dirs: Callable[..., tuple[Path, Path] | None],
    ) -> None:
        # Each run starts with a fresh "not cancelled" state.
        self._stop_requested.clear()
        self._suspend_requested.clear()
        self._error_was_stopped = False
        self._error_was_suspended = False
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
                self._enqueue_error(f"Cannot create output dir: {exc}\n")
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
            self._enqueue_error(f"Pipeline setup failed: {exc}\n")
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
            # The pipeline says why it is stopping on its own stream, one line
            # before it exits — "Not enough scratch space…", "No per-well zips
            # … found", a traceback's last frame. The exit code alone reaches
            # the user as "Pipeline exited with code 2", which names nothing, so
            # keep the tail and attach it to the failure.
            tail: collections.deque[str] = collections.deque(maxlen=_ERROR_TAIL_LINES)
            for line in proc.stdout:
                self._enqueue(("line", line))
                if line.strip():
                    tail.append(line.rstrip("\n"))
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
                self._enqueue_error(exit_message(rc, tail))
        except Exception as exc:  # surface any failure to the GUI
            self._enqueue_error(f"Failed to start pipeline: {exc}\n")

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
                force_wells=opts.get("force_wells"),
                # The empty-well blank is a reference the correction is built
                # from, not a well whose own output is wanted — so the staging
                # filters must not diff it away. Passed whenever either
                # empty-well mode is on; ignored otherwise.
                background_wells=(
                    opts.get("background_well", "")
                    if "empty_well" in (opts.get("background"),
                                        opts.get("flatfield"))
                    else ""),
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
            # Group's population is the whole plate, not the upload's subset:
            # every well was grouped locally, including the ones the
            # incremental split then decided not to send. Announcing only the
            # survivors narrowed the rail's shared denominator while the set of
            # grouped wells kept all of them, which is how the rail came to read
            # "Group 40/6".
            grouped = [wz.stem for wz in zipped.well_zips]
            grouped += [w for w in zipped.up_to_date_wells if w not in grouped]
            self._enqueue(("zipper_start", len(grouped)))
            for stem in grouped:
                self._enqueue(("zipper_well", stem))
            self._enqueue(("zipper_done", None))
            # Group and Download both end up covering the whole plate — a well
            # that was already current locally is, by the end, fetched locally.
            # The stages between them only ever see what was uploaded, which is
            # what the run's own total counts.
            for _stage in ("group", "download"):
                self._enqueue(("stage_total", (_stage, len(grouped))))

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
            # Same tail as the local path: the pipeline runs on the worker, so
            # every reason it can refuse to start — the disk preflight, an input
            # with no wells it recognises — reaches the user here too, and did so
            # as a naked exit code.
            tail: collections.deque[str] = collections.deque(maxlen=_ERROR_TAIL_LINES)
            for line in proc.stdout:
                self._enqueue(("line", line))
                if line.strip():
                    tail.append(line.rstrip("\n"))
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
                self._enqueue_error(exit_message(rc, tail, where="Remote pipeline"))
            else:
                self._enqueue((
                    "error",
                    "Remote pipeline finished but local verification failed; "
                    "remote files preserved for retry.\n",
                ))
        except Exception as exc:  # surface any failure to the GUI
            self._enqueue_error(f"Failed to run pipeline remotely: {exc}\n")
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
