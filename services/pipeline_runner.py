"""Background runner for the microscopy pipeline subprocess.

Owns the worker thread, the subprocess handle, and the log/event queue that
the GUI polls. Decouples ``analyze/tab.py`` from threading + subprocess +
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
    slurm_job     tuple      ("specs", {field: value}) — what the node a job was
                             granted is (``pipeline.slurm_node``); or
                             ("closed", {}) — the job is no longer open. Both
                             parsed out of the log stream by ProgressTracker.
    done          str        Pipeline finished successfully.
    error         str        Pipeline failed; payload is a message.
    finished      None       Final event, always emitted in a finally.
"""

from __future__ import annotations

import collections
import logging
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from collections.abc import Callable

from util.well_token import blank_wells_for_run, canonical_well_label
from util.csv_gz import existing_csv
from pipeline.well_skip import _canonical_force_wells
from services.input_resolution_service import has_well_zip
from services.pipeline_service import build_pipeline_args, spawn_pipeline
from services.remote_config import RemoteHost
from services.remote_runner import (
    OutputFilter,
    RemoteRunner,
    TRANSIENT_PREFIX,
    WELL_XFER_PREFIX,
    stage_local_zips,
)
from services import run_journal, run_provenance
from services.remote_recover import recover_finished_outputs
from services.remote_run_state import refuse_remote_nanowell_incremental
from services.subprocess_kill import signal_subprocess
from pipeline.slurm_node import parse_node_specs
from util.aw_safe import swallow, swallowed
from util.zip_append import recover_paths as recover_appends
from pipeline.suspend import LOG_MARKER as _SUSPEND_LOG_MARKER

_LOG = logging.getLogger("allwell.pipeline_runner")

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
#: What the runner prints when the remote process that held a scheduler job
#: has exited — the moment the job is no longer open. Printed locally, after
#: the ssh session ends, because nothing inside the allocation runs after the
#: pipeline: the single-run launch ``exec``s it.
JOB_CLOSED_LINE = "[remote] scheduler: job closed — the remote process has exited\n"
_RE_JOB_CLOSED = re.compile(
    re.escape(JOB_CLOSED_LINE.strip()) + r"|\[remote\] releasing the allocation")
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
        self.well_total = 0
        self.well_done = 0
        self.well_repeats = 0
        self._finalize_emitted = False
        self._fetch_parts: dict[str, set] = {}   # well -> {"zip","csv"} seen
        self._done_wells: set[str] = set()       # wells already counted done

    def reset(self) -> None:
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

    #: What the run journal records for each of the three terminal markers,
    #: keyed by the marker's own name. Exhaustive on purpose — a new marker
    #: raises here rather than defaulting to one of the two answers, because
    #: guessing wrong in either direction is a record that claims work that did
    #: not happen or denies work that did. It is a dict literal so that
    #: ``tests/test_run_journal.py::test_every_well_state_is_written_by_some_caller``
    #: can see both states: that roster's liveness check reads dict values, and
    #: an earlier spelling as a conditional expression left ``analysed``
    #: visible only in a mapping nothing reached (CLAUDE.md §7.5 #10).
    _RECORD_FOR_MARKER = {"process": "analysed", "uptodate": "skipped"}

    def _count_well_done(self, well: str,
                         stage: str = "process") -> list[tuple[str, object]]:
        """Record that *well* reached a terminal state, counting it once.

        *stage* is what the marker said happened, and it is not always
        "processed". Three lines reach here — "— done.", "up to date" and
        "SKIP … output already exists" — and all three mean the well is
        complete *for progress purposes*, which is why they share a counter.
        Only the first means the pipeline computed anything. Collapsing them
        into one stage made an incremental run that touched 6 of 96 wells
        journal 96 as ``analysed`` and the readout say "It recorded 96
        finished", which is a claim about work that did not happen.

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
        # The plate is told "process" whatever the marker was: all three mean
        # this well is finished, and the plate paints finished wells alike. The
        # journal is told the truth, and it rides in the *same* event, because
        # what the plate paints and what the record says are one fact about one
        # well and nothing downstream should be able to have one without the
        # other. The stage stays "process" because the dashboard ignores any
        # stage outside its ``_STAGE_KEYS``; sending it the record's word would
        # have stopped those wells being marked done on the plate, trading a
        # wrong record for a wrong picture.
        #
        # This is a simplification, not a bug fix, and the difference is worth
        # writing down because the commit that made the change claimed
        # otherwise. The two-event shape it replaced was said to be droppable
        # half-way — two events outside ``PipelineRunner._CONTROL_KINDS``,
        # dropped independently on a full queue. Measured, that cannot happen:
        # ``parse`` is called in one place (``AnalyzeTab._poll_log``) on the
        # GUI thread, and every event it returns except ``line`` and
        # ``workers`` is applied inline rather than re-queued. The only thing
        # the queue can drop is the whole ``("line", …)`` these are derived
        # from, which loses both facts together. Describing an unreachable
        # branch as the app's behaviour is worse than saying nothing
        # (CLAUDE.md §7.1 #3), and the one-event shape has to stand on being
        # simpler, which it is.
        events.append(("well_stage",
                       (well, "process", self._RECORD_FOR_MARKER[stage])))
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
        # What the granted node is, and the end of the job — for the Analyze
        # pane's job indicator. "Closed" has two sources: the batched driver
        # handing its allocation back, and the runner noting that the remote
        # process which held the job has exited (the single-run launch execs
        # the pipeline, so nothing inside the job can say it is over).
        specs = parse_node_specs(line)
        if specs is not None:
            events.append(("slurm_job", ("specs", specs)))
        if _RE_JOB_CLOSED.search(line):
            events.append(("slurm_job", ("closed", {})))
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
            # Matched separately rather than as one `or` chain, because which
            # of the three fired is the thing the journal needs: they agree
            # that the well is complete and disagree about whether this run
            # computed it.
            for pattern, stage in (
                (_RE_WELL_DONE_ID, "process"),
                (_RE_WELL_UPTODATE, "uptodate"),
                (_RE_WELL_SKIP, "uptodate"),
            ):
                md = pattern.search(line)
                if md:
                    events.extend(self._count_well_done(md.group(1), stage))
                    break
        return events


class RunEventQueue(queue.Queue):
    """The run's event queue, which notices a failure however it is put.

    ``log_q`` is public, and it has to be: the input resolver the Analyze pane
    passes to :meth:`PipelineRunner.start` is handed the queue directly and
    reports its own failures on it. So a failure could reach the GUI without
    passing through :meth:`PipelineRunner._enqueue_error`, and two pieces of
    per-run bookkeeping that live in that method were then simply not done.

    The one the user saw: ``cancel_state_at_failure`` is a snapshot of the stop
    and suspend flags taken *when the failure was queued*, because the GUI reads
    them a timer tick later and a Stop clicked inside that window makes a
    genuine failure read as a deliberate cancel. An error that bypassed the
    snapshot read ``(False, False)`` — so pressing Stop while a large plate was
    grouping painted the dashboard red with a signal exit code as the headline
    and forced open the raw log pane the user had closed, while the journal
    correctly recorded ``stopped``. Two surfaces, one run, opposite verdicts.

    The one nobody had reported: ``_had_error`` was not set either, so a failure
    put here by a writer outside the runner left the run journalling ``ok`` —
    recorded as a run that succeeded, and therefore never offered for resume.

    Where that bites is worth stating precisely, because the obvious case is not
    it. An input failure — a folder that no longer exists, a schema the resolver
    rejects — is reported *during resolution*, and the journal has no home until
    the output directory is resolved a few lines later, so such a run writes no
    record at all: the flag is still set, which is what the dashboard reads, but
    there is nothing on disk for it to be wrong in. The outcome the flag decides
    is the one for a failure put on this queue once the run is under way, and
    that is what ``tests/test_every_failure_goes_through_one_seam.py`` measures —
    on the record, through the app's own open-fail-close order, rather than on
    the flag.

    Latching here rather than in ``_enqueue`` is deliberate: ``_enqueue`` is one
    of the writers, not the boundary. Every write goes through ``Queue.put``,
    including ``put_nowait``, which CPython implements in terms of it.
    """

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize)
        self._on_error: Callable[[], None] | None = None

    def put(self, item, block: bool = True, timeout: float | None = None) -> None:
        # Before the put, not after: the snapshot has to be of the flags as
        # they stood when the failure happened, and a ``put`` on a full queue
        # blocks for as long as the GUI takes to drain it.
        if (isinstance(item, tuple) and item and item[0] == "error"
                and self._on_error is not None):
            self._on_error()
        super().put(item, block, timeout)



def scope_staged_to_only_wells(zipped, only_wells, *, keep=()):
    """Narrow a staged remote upload to a ``--only_wells`` subset.

    Returns ``(zipped, scoped)``: the staged input with every well outside the
    subset dropped from ``well_zips`` and ``up_to_date_wells``, and the subset's
    wells actually present (``None`` when there is no subset). Applied once to
    whatever :func:`stage_local_zips` returns, rather than inside it, because it
    returns from several branches and a filter placed in only some of them is a
    subset that uploads the whole plate on the others.

    *keep* names wells that are uploaded whatever the subset says: the
    empty-well blanks, which the remote pipeline builds its correction from
    while running none of them as a well. A named well the staged input does
    not hold raises ``ValueError`` before anything is uploaded — the pipeline
    would refuse it on the host anyway, after the upload had been paid for.
    """
    if not only_wells:
        return zipped, None
    from dataclasses import replace

    from util.well_token import canonical_well_label

    wanted = _canonical_force_wells(only_wells)
    kept_blanks = _canonical_force_wells(keep)
    present = ({canonical_well_label(Path(p).stem) for p in zipped.well_zips}
               | {canonical_well_label(w) for w in zipped.up_to_date_wells})
    missing = sorted(wanted - present)
    if missing:
        raise ValueError(
            f"--only_wells names well(s) not in the input: {', '.join(missing)}")
    zips = tuple(p for p in zipped.well_zips
                 if canonical_well_label(Path(p).stem) in wanted | kept_blanks)
    current = tuple(w for w in zipped.up_to_date_wells
                    if canonical_well_label(w) in wanted)
    return (replace(zipped, well_zips=zips, up_to_date_wells=current),
            sorted(wanted & present))


def argv_with_only_wells(argv, wells):
    """*argv* with its ``--only_wells`` values replaced by *wells*.

    Values run from the flag to the next ``--`` option, which is how argparse
    reads a ``nargs="+"`` list. Returns a new list.
    """
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--only_wells":
            out += ["--only_wells", *sorted(wells)]
            i += 1
            while i < len(argv) and not str(argv[i]).startswith("--"):
                i += 1
            continue
        out.append(argv[i])
        i += 1
    return out

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
        self.log_q: RunEventQueue = RunEventQueue(
            maxsize=self._LOG_QUEUE_MAXSIZE,
        )
        self.log_q._on_error = self._latch_error_state
        self._proc: subprocess.Popen | None = None
        # The grouping-phase subprocess (well_plate_zipper). Tracked
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
        # True while `_spawn_tracked` is inside its spawn call, which for a
        # remote run is the whole staging and upload. `stop` reads it so that it
        # never waits on that lock from the GUI thread (see `_spawn_tracked`).
        self._spawning = False
        self._thread: threading.Thread | None = None
        # The thread `_signal_run` starts to kill and `scancel` a remote run;
        # held so app close can wait for it (see `wait_for_teardown`).
        self._cancel_thread: threading.Thread | None = None
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
        # The thread a remote suspend's request runs on (see ``suspend``), and
        # the two counters that keep a late one from undoing a later decision:
        # `_suspend_seq` numbers the requests within a run, `_run_generation`
        # the runs, so neither an older click nor a previous run's thread can
        # clear what a newer one set.
        self._suspend_thread: threading.Thread | None = None
        self._suspend_seq = 0
        self._run_generation = 0
        # Set when the remote pipeline's own stream says it saw a suspend
        # request (`pipeline.suspend.LOG_MARKER`) — the evidence that decides.
        self._suspend_seen = False
        # Snapshots of the two events above, taken when a failure is queued —
        # see ``cancel_state_at_failure``.
        self._error_was_stopped = False
        self._error_was_suspended = False
        # Latched by ``_enqueue_error``. The run journal has to be closed with
        # an outcome from inside the worker thread's ``finally``, which is
        # upstream of the GUI's ``_run_ok`` / ``_run_error`` latch and cannot
        # see the queue it just wrote to. Reading "was there a failure" off a
        # flag set at the moment of failure is the only answer available there
        # — and it is the same discipline the two snapshots above already use.
        self._had_error = False
        # The ``RunSpec.to_json()`` payload for the run in flight, recorded
        # into the journal so a run that dies can be offered back with the
        # settings it had. ``None`` for a caller that supplies none.
        self._journal_spec: dict | None = None
        # Whether the job queue started this run and therefore owns resuming
        # it; see ``RunJournal.queued``.
        self._journal_queued: bool = False
        #: Where this run's journal lives, and its token — `open_run`'s
        #: `opened_at`, handed to every write so a record can say *which* run
        #: it belongs to. Declared here rather than only in `start()`, because
        #: a runner that is asked about them before its first run should get
        #: `None` rather than an `AttributeError`: `stop()` and the queue's
        #: gate can both be reached on a freshly built runner.
        self._journal_dir: Path | None = None
        self._journal_token: float | None = None
        #: The run whose ``run_started`` provenance event was written, so
        #: ``_close_journal`` can write the matching ``run_finished``. Separate
        #: from ``_journal_dir`` because a run whose journal could not be
        #: opened still belongs in the history.
        self._provenance_run: run_provenance.RunMark | None = None
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
    #: ``suspended`` is the fourth ending: a run the user wound down with
    #: Suspend exits 0 on purpose, and it is neither a completion nor a failure.
    _CONTROL_KINDS = frozenset({"done", "suspended", "error", "finished"})

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
                    # The overflow warning itself could not be queued, which
                    # means the reader has not drained a single slot since the
                    # queue filled. Nothing to do and nothing to say — the one
                    # channel for saying it is the thing that is full — and the
                    # flag above already ensures this is attempted once per run
                    # rather than per dropped line.
                    swallowed("the log-overflow warning could not be queued "
                              "either; the reader has drained nothing")

    @property
    def is_running(self) -> bool:
        """Whether the pipeline *subprocess* is alive.

        Not the same question as :attr:`is_busy`, and the difference is the
        window in which the run is resolving directories and grouping input —
        seconds to minutes on a large plate — where the subprocess does not
        exist yet. Callers asking "may I start another run?" want
        :attr:`is_busy`; callers asking about the pipeline process itself (a
        stop, a progress read) want this.
        """
        return self._proc is not None and self._proc.poll() is None

    @property
    def is_busy(self) -> bool:
        """Whether this runner would refuse a new :meth:`start`.

        Keyed on the worker thread, so it is true from the moment ``start``
        returns until the run has finished — including the grouping window that
        :attr:`is_running` cannot see. The job queue's dispatch gate reads this:
        with the subprocess-only answer it dispatched into that window, reset a
        live run's progress UI, and then took the ``RuntimeError`` from
        ``start`` with ``_current`` already set and no ``jobFinished`` to come,
        which stops the queue for the session.
        """
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_output_dir(self) -> Path | None:
        return self._last_output_dir

    @property
    def running_spec(self) -> dict | None:
        """The spec of the run in flight, exactly as it was launched.

        The Analyze form stays editable while a run goes, so the form is not a
        description of what is running; this is. A copy, so a caller cannot
        change what the journal will record.
        """
        return dict(self._journal_spec) if self._journal_spec else None

    @property
    def journal_queued(self) -> bool:
        """Whether the job queue started the run currently in flight.

        Read by the suspend record, which has to answer the same
        who-owns-resuming-this question the journal does — a suspend writes both
        pointers, and filtering one is filtering neither.
        """
        return self._journal_queued

    @property
    def journal_token(self) -> float | None:
        """This run's journal token, for a writer that is not on this thread.

        ``None`` when the journal could not be opened — a writer that sees that
        has nothing of its own to write into and must not write at all.

        Deliberately *not* cleared by ``_close_journal``: the GUI thread is
        still draining this run's completion lines when the worker thread
        closes the record, and every one of them needs the token to be
        accepted. It is reset when the next run starts.
        """
        return self._journal_token

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
        journal_spec: dict | None = None,
        queued: bool = False,
    ) -> None:
        """Spawn the worker thread that runs the pipeline subprocess.

        ``resolve_dirs`` is called with ``(opts, log_q, proc_hook=…)`` where
        ``proc_hook`` is the runner's zipper-tracking callback (forwarded to the
        grouping subprocess so Stop can reach it). It is **required**: the
        ``except TypeError`` that used to accept a resolver without it was
        removed, because it could not be entered by a caller with the wrong
        signature — only by a ``TypeError`` raised *inside* resolution, which it
        answered by re-running a function that groups images and deletes their
        sources.

        ``journal_spec`` is the ``RunSpec.to_json()`` payload behind this run,
        recorded into the output directory's run journal. It arrives here
        rather than being rebuilt because the spec exists only at the GUI's
        start seam, while the output directory — the journal's home — is not
        resolved until this runner's own worker thread. Omitting it costs the
        journal only its resume payload; everything else it records still
        works, which is why a CLI driver can leave it out.

        When ``remote`` is set, the pipeline runs on the named SSH host
        instead of locally. ``output_filter``, ``cleanup_remote``,
        ``dry_run_cleanup``, and ``selective_channels`` map to the
        corresponding flags on :func:`scripts.allwell_remote.main` —
        they're ignored in local mode.
        """
        # The GUI is not the only thing that can ask twice, and it is not the
        # right place to enforce this: overwriting `self._thread` and `_proc`
        # leaves the first run unreachable — `stop()` can only signal whichever
        # process was assigned last — while both write the same output
        # directory and both fire `finished`, so a queue bridge starts the next
        # job twice.
        # Asked of the worker thread, not of `is_running`: that property reads
        # the pipeline *subprocess*, which does not exist yet while the run is
        # resolving directories and grouping — a window of seconds to minutes
        # on a large plate, and exactly when the GUI's Run button was being
        # re-armed. A finished run has joined, so the queue starting the next
        # job on the same runner is unaffected.
        if self.is_busy:
            raise RuntimeError(
                "a pipeline is already running in this runner; stop it before "
                "starting another")
        self._remote = remote
        self._remote_runner: RemoteRunner | None = (
            RemoteRunner(remote) if remote is not None else None
        )
        self._output_filter = output_filter
        self._cleanup_remote = cleanup_remote
        self._dry_run_cleanup = dry_run_cleanup
        self._selective_channels = selective_channels
        self._journal_spec = journal_spec
        self._journal_queued = bool(queued)
        # Every per-run flag is cleared **here**, on the caller's thread,
        # before the worker is started — not at the top of the worker itself.
        # `start()` returns as soon as the thread is created, and the thread
        # may not be scheduled for some time after that. A Stop pressed in
        # that gap sets `_stop_requested`, and the worker's first act used to
        # be clearing it: the run carried on with the user's cancellation
        # discarded, and the button had already gone back to its idle state.
        # Clearing before the thread exists closes the window entirely.
        self._stop_requested.clear()
        self._suspend_requested.clear()
        self._suspend_seen = False
        self._run_generation += 1
        self._error_was_stopped = False
        self._error_was_suspended = False
        self._had_error = False
        # Cleared per run rather than at ``close_run``: a setup-phase failure
        # returns before the journal is ever opened, and a stale directory
        # from the previous run would then be closed in this run's name.
        self._journal_dir = None
        self._journal_token = None
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

    def _spawn_tracked(self, spawn_fn: Callable[[], subprocess.Popen]) -> subprocess.Popen | None:
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
            self._spawning = True
            try:
                self._proc = spawn_fn()
            finally:
                self._spawning = False
            proc = self._proc
        # A remote spawn is the whole staging and upload, and it runs with the
        # lock held. `stop` therefore does not wait for it — it sets the stop
        # event the upload watches and returns — so a Stop that landed while
        # this was spawning has not signalled what it launched. Do it here.
        if self._stop_requested.is_set():
            self._signal_run(proc, escalate=not self._suspend_requested.is_set())
        return proc

    def suspend(self) -> None:
        """Ask the run to wind down at a FOV-group boundary and exit cleanly.

        For a local run the signal is the one ``stop`` already sends — a SIGTERM
        to the process group. The difference is entirely on the receiving end:
        since
        ``pipeline.suspend`` installs a handler, the pipeline stops taking on new
        FOV-groups, lets the ones in flight finish, and exits through the normal
        end-of-well path so every well that got work done writes its CSV and its
        ``<well>_out.zip``. Those artifacts are what a resumed run reads as its
        done-set, so nothing else needs saving here.

        A remote run is asked through a request file instead
        (``RemoteRunner.request_suspend``), because no signal the app can send
        reliably reaches the pipeline there, and the one ``stop`` sends ends the
        ssh session the run's output and downloads travel over. Nothing local
        is signalled: the run keeps streaming and downloading until it exits 0.

        **The SIGKILL escalation** that follows five seconds after the SIGTERM
        is suppressed for the rest of this run. A well mid-FOV can easily need
        longer than that, and killing it at second five would throw away
        exactly the work this is trying to preserve. A suspend that hangs is
        still recoverable — Stop is right there, and its second signal is a
        hard kill by design.

        Remote cleanup is **not** suppressed. It fires only once the fetch has
        been verified, so everything the suspended run produced is local by
        then — and nothing on the host is read by the resume, which uploads
        only the missing groups and never re-attaches (``RemoteRunner.spawn``).
        Keeping the directory "for the resume" left it on the host for ever.
        """
        if self._suspend_requested.is_set() and self._remote_runner is None:
            # Already winding down. The pipeline's handler gives SIGTERM back to
            # its default after the first, so a second one is a hard stop —
            # the opposite of what a second Suspend asks for. (A remote run's
            # request is a file, and asking twice is harmless.)
            return
        self._suspend_requested.set()
        remote = self._remote_runner
        if remote is not None and remote.can_request_suspend:
            # A remote run is asked through its request file and nothing else
            # (``RemoteRunner.request_suspend``): signalling the local ssh, as
            # below, ends the session and every download with it. The ask is an
            # ssh round trip, and this is called from a button — so it goes on
            # a thread of its own rather than freezing the window for as long
            # as an unreachable host takes to time out.
            self._suspend_seq += 1
            self._suspend_thread = threading.Thread(
                target=self._ask_remote_to_suspend,
                args=(remote, self._suspend_seq, self._run_generation),
                daemon=True)
            self._suspend_thread.start()
            return
        # A local run, or a remote one still staging or uploading — there is no
        # pipeline on the host yet to wind down, so stopping the transfer is
        # what a suspend can do.
        self.stop(escalate=False)

    def _ask_remote_to_suspend(self, remote, seq: int, generation: int) -> None:
        """Worker half of a remote :meth:`suspend`.

        The process handle is read here rather than by the button: the lock
        that guards it is held by the spawn for the whole of the upload.
        """
        with self._proc_lock:
            proc = self._proc
        try:
            asked = remote.request_suspend(
                proc, log_fn=lambda m: self._enqueue(("line", m)))
        except Exception:
            swallowed("asking the remote run to suspend")
            asked = False
        if asked or seq != self._suspend_seq or generation != self._run_generation:
            # Asked; or a newer click, or a newer run, has decided since.
            return
        # The ssh call failed, so the run is taken to be carrying on — but a
        # timeout can fail after the ``touch`` ran. That case is not guessed at
        # here: a pipeline that saw the request says so on its stream, and
        # `_run_pipeline_remote` restores the suspend from that line.
        self._suspend_requested.clear()

    def _note_suspend_evidence(self, line: str) -> None:
        """Remember that the remote pipeline reported seeing a suspend request."""
        if not self._suspend_seen and _SUSPEND_LOG_MARKER in line:
            self._suspend_seen = True

    def _note_scheduler_job_closed(self) -> None:
        """Say that the remote process holding the run's scheduler job exited.

        The job has exited with it, and the Analyze pane's job indicator goes
        out on this line (``JOB_CLOSED_LINE``). A host with no
        ``compute_launcher`` holds no job, so it says nothing.
        """
        host = getattr(self._remote_runner, "host", None)
        if str(getattr(host, "compute_launcher", "") or "").strip():
            self._enqueue(("line", JOB_CLOSED_LINE))

    def _settle_remote_suspend(self) -> str:
        """Decide, once the remote pipeline has exited, whether it was suspended.

        The pipeline's own word wins over the ssh call's: a request whose ssh
        timed out may still have landed, and a run that says it saw one wound
        down whatever the app believed. Returns the cleanup mode to use: the
        configured one for a finished or suspended run (see :meth:`suspend`),
        never a deleting one for a stopped run.
        """
        if self._suspend_seen:
            self._suspend_requested.set()
        if self._stop_requested.is_set():
            return "none"
        return self._cleanup_remote

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

    def _latch_error_state(self) -> None:
        """Record that this run failed, and how things stood when it did.

        Called by :class:`RunEventQueue` for every ``("error", …)`` event put on
        the queue, whoever puts it — so a failure reported by the input resolver
        (which is handed ``log_q`` directly) is bookkept exactly like one the
        runner raises itself. Idempotent by construction: a second failure
        re-snapshots flags that only ever move from unset to set.
        """
        self._error_was_stopped = self._stop_requested.is_set()
        self._error_was_suspended = self._suspend_requested.is_set()
        self._had_error = True

    def _enqueue_suspended(self, *, where: str = "Pipeline") -> None:
        """Report a run that wound down after Suspend and exited cleanly.

        ``pipeline.suspend`` makes a suspended pipeline leave through its normal
        end-of-well path, so it exits 0 — and an exit of 0 was read as "the
        plate is finished". That reading did three things a suspend must never
        do: it ran the success hooks that tidy away the source images the run
        grouped, it handed the half-written output folder to Review as a
        finished dataset (a load that then failed on the wells the run never
        reached), and it told the job queue the job had succeeded, so the queue
        dropped it rather than keeping it to resume.

        A distinct event rather than ``done`` or ``error``: the tab needs to
        say "suspended" rather than "complete" or "failed", and the queue needs
        ``ok`` to be false with no error message, which is how it recognises a
        suspend (``JobQueueController._was_suspended``).
        """
        self._enqueue((
            "suspended",
            f"{where} suspended: the wells in flight were finished and written; "
            "the rest were not started. Resume from the Analyze banner, or "
            "re-run this input with 'incremental', to finish the plate.\n",
        ))

    def _enqueue_stopped(self, *, where: str = "Pipeline") -> None:
        """Report a run the user stopped that nonetheless exited 0.

        Stop sends SIGTERM, and the pipeline answers SIGTERM by winding down
        (`pipeline.suspend`), so a run that finishes its in-flight groups inside
        the five seconds before the SIGKILL exits 0. Read as a completion, a
        Stop printed "Processing Complete", ran the source-image success hooks,
        handed the half-done folder to Review and told the queue the job had
        succeeded, while the journal recorded it as stopped. Reported through
        the error seam, so it is latched as a cancel (`cancel_state_at_failure`)
        and the GUI shows it amber, the same as a Stop that ended by signal.
        """
        self._enqueue_error(
            f"{where} stopped: the wells that finished are kept; the rest were "
            "not processed.\n")

    def _recover_appends(self, *paths) -> bool:
        """Undo an unfinished in-place append under *paths*; ``False`` stops the run.

        A run still going elsewhere, or an undo refused as unsafe, fails this
        run rather than letting it read a half-appended archive.
        """
        recovered = recover_appends(*paths)
        for line in recovered.lines:
            self._enqueue(("line", f"[recover] {line}\n"))
        if recovered.blocked:
            self._enqueue_error(f"{recovered.blocked}\n")
            return False
        return True

    def _enqueue_error(self, message: str) -> None:
        """Queue a failure. The queue itself records the cancel state."""
        self._enqueue(("error", message))

    def _journal_outcome(self) -> str:
        """How this run ended, in the run journal's vocabulary.

        Order matters and is not arbitrary. A suspend is a stop with the
        escalation suppressed, so ``_stop_requested`` is set for both and
        asking about the stop first would record every suspend as a cancel —
        which is the one distinction a resume banner depends on. A failure is
        only reported when neither flag is set: a Stop mid-run makes the
        pipeline exit non-zero, and calling the user's own cancel an error is
        exactly the misreport ``cancel_state_at_failure`` exists to prevent.
        """
        if self._suspend_requested.is_set():
            return "suspended"
        if self._stop_requested.is_set():
            return "stopped"
        return "error" if self._had_error else "ok"

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
        #
        # Never waits on a spawn in progress. A remote spawn holds the lock for
        # the whole staging and upload — minutes to hours — and this runs on the
        # GUI thread, so taking the lock there froze the window until the upload
        # ended, which then launched the pipeline only to kill it. The stop event
        # set above is the one the upload watches, and `_spawn_tracked` signals
        # whatever the spawn launched once it returns.
        if self._spawning:
            self._signal_subprocess(self._zipper_proc, escalate=escalate)
            return
        with self._proc_lock:
            zipper_proc, proc = self._zipper_proc, self._proc

        # Reach both the grouping subprocess (if active) and the pipeline
        # subprocess. Signal each one's whole process group so any
        # multiprocessing workers it spawned get torn down too.
        self._signal_subprocess(zipper_proc, escalate=escalate)
        self._signal_run(proc, escalate=escalate)

    def _signal_run(self, proc, *, escalate: bool) -> None:
        """Signal the pipeline process *proc*, and the remote run behind it."""
        self._signal_subprocess(proc, escalate=escalate)
        # For remote runs, the local SIGTERM above tears down the ssh
        # client; ssh -tt propagates SIGHUP to the remote pipeline. The
        # second channel here is belt-and-braces — a fresh ssh that kills
        # the PID we wrote on startup, in case the PTY hangup didn't
        # propagate (older OpenSSH on the remote, NAT teardown, etc.).
        #
        # On a thread of its own: it is two ssh round trips (a `kill` with a
        # 10 s timeout, then the scheduler's `scancel`), and this is reached
        # from the Stop button, where they froze the window for as long as a
        # slow or unreachable host took to answer. Nothing in it touches Qt.
        if self._remote_runner is not None and proc is not None:
            remote, inner = self._remote_runner, getattr(proc, "_ssh", None) or proc

            def _cancel() -> None:
                try:
                    remote.cancel(inner)
                except Exception:  # best-effort kill
                    swallowed("cancelling the remote run")

            self._cancel_thread = threading.Thread(
                target=_cancel, name="aw-remote-cancel", daemon=True)
            self._cancel_thread.start()

    def wait_for_teardown(self, timeout_s: float = 25.0) -> bool:
        """Wait, bounded, for a stopped run to give back what it holds remotely.

        For app close. Stop hands the remote kill and the ``scancel`` to a daemon
        thread so the button never freezes the window — but daemon threads die
        with the interpreter, so a Stop issued by closing the app was cut off
        before its ssh reached the host and the allocation was left running. The
        run thread's own ``finally`` (the scheduler-job close) is waited on too:
        a stop during the upload never reaches ``_signal_run`` at all.

        Returns whether everything finished inside *timeout_s*.
        """
        deadline = time.monotonic() + timeout_s
        threads = (self._cancel_thread, self._thread)
        for thread in threads:
            if thread is not None and thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))
        return not any(t is not None and t.is_alive() for t in threads)

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
            # No compatibility fallback. The only production caller,
            # `analyze.domain.input._resolve_run_dirs_for_runner`, takes `proc_hook`,
            # and the `except TypeError` that used to sit here could therefore
            # not be entered by a caller with the wrong signature — only by a
            # `TypeError` raised from *inside* the resolver, which it then
            # answered by running the whole resolver a second time. That
            # resolver groups loose images into archives and deletes their
            # sources; running it twice on a partially-completed first pass is
            # not a fallback, it is a second pass over a directory the first
            # one had already changed. Without `proc_hook` the grouping
            # subprocess is also untracked, so Stop cannot reach it.
            # An in-place append (Concatenate's "add to dataset 1") that a crash
            # left unfinished is undone *before* the resolver runs: the resolver
            # folds loose images into ``in/<WELL>.zip`` by rewriting it whole,
            # after which the journalled tail no longer describes the file.
            if not self._recover_appends(opts.get("raw")):
                return
            resolved = resolve_dirs(
                opts, self.log_q, proc_hook=self._track_zipper_proc)
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
            # The resolved folder may differ from the one named in the form.
            if not self._recover_appends(input_dir):
                return
            # A *well* archive, not any archive. ``has_well_zip`` asks the
            # well-token canonicaliser, which is the same question the pipeline
            # asks when it decides to segment from ``in/<WELL>.zip`` — so a
            # stray ``archive.zip`` or ``backup.zip`` a user left in a
            # folder-mode ``in/`` no longer tells them their compression
            # settings were ignored when they were not.
            # ``has_well_content``'s own docstring records this exact defect
            # being fixed for the layout planner; this was the surviving
            # sibling.
            if has_well_zip(input_dir):
                self._enqueue((
                    "line",
                    "[warn] Zip mode detected; folder-mode compression options do not apply.\n",
                ))
            # Built before the directory is created, and deliberately so.
            # ``build_pipeline_args`` is pure string assembly, but it reads
            # required keys off ``opts`` and raises on a missing one — and
            # creating the output directory first meant a form the pipeline
            # could not be launched from still left an empty ``out/`` behind,
            # for a run that never started and left nothing to explain it.
            args = build_pipeline_args(pipeline, input_dir, output_dir, opts)
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._enqueue_error(f"Cannot create output dir: {exc}\n")
                return
            self._last_output_dir = output_dir
            # The run begins on disk here, and not one line earlier: the output
            # directory is resolved inside this thread, so this is the first
            # moment the journal has a home. Opening it before the fork below
            # means local, remote and batched runs are all covered by one call.
            self._open_journal(input_dir, output_dir, args, opts)
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
            # Before ``finished``, so a queue bridge that starts the next job
            # off that event cannot open its journal while this one is still
            # recorded as running.
            self._close_journal()
            self._enqueue(("finished", None))

    # ── the run journal ──────────────────────────────────────────────────
    def _open_journal(
        self, input_dir: Path, output_dir: Path, argv: list[str], opts: dict,
    ) -> None:
        """Record that this run started, before it processes anything.

        Best-effort throughout: a run whose journal cannot be written still
        runs, because the artefacts remain the authoritative record of what was
        produced and this is the narrative beside them.

        The run's ``run_started`` provenance event is written first, and
        independently of the journal: the journal describes the latest run and
        is replaced by the next, while the provenance log keeps every run in
        order (``services/run_provenance.py``).
        """
        mode = "local"
        if self._remote is not None:
            # Read the batch size the same way ``_run_pipeline_remote`` does —
            # off ``opts``, not off the host — so the journal cannot name a
            # mode the run did not take.
            try:
                batched = int(opts.get("remote_batch_size", 0) or 0) > 0
            except (ValueError, TypeError):
                batched = False
            mode = "remote-batched" if batched else "remote"
        self._provenance_run = run_provenance.begin_run(
            output_dir, mode=mode, input_dir=input_dir, argv=argv, opts=opts)
        try:
            self._journal_token = run_journal.open_run(
                output_dir,
                spec=self._journal_spec,
                mode=mode,
                input_dir=input_dir,
                argv=argv,
                queued=self._journal_queued,
            )
        except (OSError, ValueError):
            # ``ValueError`` is ``open_run`` refusing a mode it does not know —
            # a programming error, but one that must not take a run down with
            # it, because the artefacts and not this record are what the run
            # produces. It is logged with its reason rather than passed over.
            swallowed(f"could not open the run journal in {output_dir}")
            return
        if self._journal_token is None:
            # ``open_run`` answers ``None`` for a write that failed as well as
            # raising for a mode it will not accept, and only the second reached
            # the handler above. Recording the directory anyway armed
            # ``_close_journal`` with no token, and ``close_run`` treats a
            # missing token as "close it anyway" — so the *previous* run's
            # record, which the index is holding open precisely so it can be
            # offered for resume, was closed as this run's outcome and the
            # offer vanished. That is the exact scenario ``close_run``'s
            # docstring describes, and the guard could not see it because this
            # side never told it there was nothing to guard.
            # Not ``swallowed``: nothing was raised here. ``open_run``
            # answered, and its answer was no. A warning rather than a debug
            # line because the run continues with no record of itself, and a
            # degraded run must not look like a good one (§7).
            _LOG.warning(
                "the run journal in %s could not be opened, so this run will "
                "not write one — the previous run's record is left as it "
                "stands", output_dir)
            # And on the surface the user is actually reading. The logger
            # reaches the help drawer's Log tab, which nobody has open during a
            # run; every other message from this function goes to the run log
            # as a "[warn]" line, and a run that silently keeps no record of
            # itself is exactly the degraded-looks-like-good case (§7).
            self._enqueue((
                "line",
                "[warn] Could not open the run journal in "
                f"{output_dir} — this run will not be recorded, so it cannot "
                "be offered for resume if it is interrupted. The output "
                "itself is unaffected.\n",
            ))
            return
        self._journal_dir = output_dir

    def _close_journal(self) -> None:
        """Close the journal with how this run ended, whatever ended it.

        A journal left open is how the app tells, at its next start, that a run
        died rather than finished — so this must run on every exit the process
        survives, which is why it sits in the worker thread's ``finally``
        rather than beside any one outcome.
        """
        mark, self._provenance_run = self._provenance_run, None
        if mark is not None:
            run_provenance.finish_run(mark, outcome=self._journal_outcome())
        output_dir, self._journal_dir = self._journal_dir, None
        if output_dir is None:
            return
        try:
            # Keyed on this run's own token, so a run whose `open_run` write
            # failed cannot close the record still standing from the run
            # before it — which the index is holding open precisely so that
            # run can be offered for resume.
            run_journal.close_run(output_dir, outcome=self._journal_outcome(),
                                  run_token=self._journal_token)
        except OSError:
            swallowed(f"could not close the run journal in {output_dir}")

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
            try:
                for line in proc.stdout:
                    self._enqueue(("line", line))
                    if line.strip():
                        tail.append(line.rstrip("\n"))
            except Exception as exc:
                # Whatever ends the read, the child must still be signalled and
                # reaped. It was started with `start_new_session=True`, so it
                # is its own session leader and nothing else will reap it: an
                # exception escaping here left the pipeline running against the
                # user's plate while the GUI reported the run had never
                # started. The decode case is now impossible (`errors="replace"`
                # on the spawn) and this is the general guarantee, not a fix
                # for that one cause.
                self._enqueue((
                    "line",
                    f"[warn] reading the pipeline's output stopped early "
                    f"({type(exc).__name__}: {exc}); the run is being "
                    f"stopped.\n"))
                self._signal_subprocess(proc)
                raise
            finally:
                with swallow("waiting for the pipeline subprocess"):
                    proc.wait()
            rc = proc.returncode
            if rc == 0 and self._suspend_requested.is_set():
                self._enqueue_suspended()
            elif rc == 0 and self._stop_requested.is_set():
                self._enqueue_stopped()
            elif rc == 0:
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

    def _scope_remote_run(self, zipped, opts: dict, args: list) -> tuple | None:
        """Apply a ``--only_wells`` subset to a staged remote run.

        Returns ``(zipped, args, grouped, download_n)``: the staged input
        narrowed to the subset (the empty-well blanks kept); the argv the host
        is to run, naming only the wells that were actually sent — incremental
        staging may keep a named well back as already whole, and the host's
        pipeline refuses a subset naming a well its input lacks; the wells the
        Group stage counts, which are the whole plate that was grouped, taken
        before the subset narrows it; and the Download stage's count, which is
        the run's own wells. ``None`` when every named well is already whole
        locally: the run has been ended with a "nothing to run" and the caller
        returns. Without a subset the input and argv come back unchanged.
        """
        grouped = [wz.stem for wz in zipped.well_zips]
        grouped += [w for w in zipped.up_to_date_wells if w not in grouped]
        blanks = ()
        if "empty_well" in (opts.get("background"), opts.get("flatfield")):
            blanks = blank_wells_for_run(opts.get("background_well", ""),
                                         opts.get("background_well_map", ""))
        zipped, scoped = scope_staged_to_only_wells(
            zipped, opts.get("only_wells"), keep=blanks)
        if scoped is None:
            return zipped, args, grouped, len(grouped)
        current = set(zipped.up_to_date_wells)
        sent = [w for w in scoped if w not in current]
        if not sent:
            # Launching the host with an empty subset would be refused there,
            # after an upload of nothing had been paid for.
            for tok in zipped.up_to_date_wells:
                self._enqueue(("line", f"[remote] ✓ {tok}: up to date (local)\n"))
            self._enqueue(("done", "Nothing to run: every named well is "
                                   "already up to date locally.\n"))
            return None
        return zipped, argv_with_only_wells(args, sent), grouped, len(scoped)

    def _announce_staged_plate(self, zipped, grouped: list[str],
                               download_n: int) -> None:
        """Tell the dashboard what staging produced, before anything uploads."""
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
        self._enqueue(("zipper_start", len(grouped)))
        for stem in grouped:
            self._enqueue(("zipper_well", stem))
        self._enqueue(("zipper_done", None))
        # Group and Download both end up covering the whole plate — a well
        # that was already current locally is, by the end, fetched locally.
        # The stages between them only ever see what was uploaded, which is
        # what the run's own total counts. Upload is given that number here
        # rather than left to inherit the plate's: the run's own total does
        # not arrive until the pipeline announces it, which is after the
        # upload is over, so until then the Upload rail row (and the
        # headline naming the same count) divided the wells being sent by
        # the whole plate.
        # Download is the run's wells, which on a --only_wells run is the
        # subset rather than the plate that was grouped.
        for _stage, _n in (("group", len(grouped)),
                           ("download", download_n),
                           ("upload", len(zipped.well_zips))):
            self._enqueue(("stage_total", (_stage, _n)))

    def _nothing_left_to_upload(self, zipped, opts: dict, output_dir: Path) -> bool:
        """End an incremental run whose staging left nothing to send.

        Every well is already complete locally — typically because
        :meth:`_recover_remote_outputs` just downloaded what the previous run
        had finished on the server. Launching the host with an empty input
        would be refused there, after a node had been asked for; there is
        nothing left for it to do. ``True`` means the run has been ended.

        **An empty-well blank alone is nothing left, too.** Staging uploads a
        blank whole on every run and never diffs it (``_plan_staging``): it is
        the *input* the correction is built from, so no filter may drop it while
        a well that needs correcting is being sent. When nothing else is being
        sent and the blank's own output is already here, it is the only thing
        in the upload — and the run then uploaded it and asked the scheduler for
        a node to recompute a well that needed nothing.
        """
        if not zipped.incremental:
            return False
        if zipped.well_zips and not self._only_measured_blanks(zipped, opts, output_dir):
            return False
        for tok in (*zipped.up_to_date_wells, *(Path(w).stem for w in zipped.well_zips)):
            self._enqueue(("line", f"[remote] ✓ {tok}: up to date (local)\n"))
        self._enqueue(("done", "Nothing to upload: every well is already "
                               "complete locally.\n"))
        return True

    @staticmethod
    def _only_measured_blanks(zipped, opts: dict, output_dir: Path) -> bool:
        """Whether every staged archive is an empty-well blank already measured
        here (its per-well CSV and, for a full fetch, its output archive)."""
        if "empty_well" not in (opts.get("background"), opts.get("flatfield")):
            return False
        blanks = set(blank_wells_for_run(opts.get("background_well", ""),
                                         opts.get("background_well_map", "")))
        prefix = opts.get("csv_prefix", "measurements")
        for wz in zipped.well_zips:
            label = canonical_well_label(Path(wz).stem)
            csv_path = existing_csv(output_dir / f"{prefix}_{label}.csv")
            if (label not in blanks or csv_path is None
                    or csv_path.stat().st_size == 0
                    or not (output_dir / f"{label}_out.zip").is_file()):
                return False
        return True

    def _recover_remote_outputs(self, output_dir: Path, args: list[str],
                                opts: dict) -> bool | None:
        """Download what an earlier run of this plate finished on the remote,
        and say whether staging must be incremental.

        Runs before every unforced remote run stages its upload — incremental
        or not, single-shot or batched — because staging decides what to send
        from the *local* folder alone: a well the previous run finished on the
        server and never downloaded looks unprocessed, so it was uploaded again
        and recomputed. See :mod:`services.remote_recover`.

        Returns whether to stage incrementally: what the run asked for, or
        ``True`` once wells were pulled, because only the incremental split
        leaves a locally complete well out of the upload on every path. Never
        under ``force`` (its wells are to be reprocessed, and staging must send
        them whole), which also skips the pull: the old output is not wanted.
        ``None`` means the run has been ended with an error and the caller
        returns: the recorded directory holds finished wells the pull could not
        bring home, and staging past that would re-upload and recompute them.
        """
        if opts.get("force", False):
            return False
        if int(opts.get("remote_batch_size", 0) or 0) > 0:
            # A batched run takes what an earlier attempt finished as part of the run (remote_batch_resume): the
            # Fetcher downloads those wells while the batches upload and compute, instead of every one of them being
            # pulled here, one after another, before anything is staged or the scheduler is asked for a node — and
            # before the wells already local are even painted on the plate.
            return bool(opts.get("incremental", False))
        recovery = recover_finished_outputs(
            self._remote_runner, output_dir, args,
            output_filter=OutputFilter(self._output_filter),
            log_fn=lambda m: self._enqueue(("line", m)),
            stop_event=self._stop_requested,
        )
        if recovery.pulled and not opts.get("incremental", False) and "--nanowell_format" not in args:
            self._enqueue(("line", "[remote] resume: staging incrementally, so "
                                   "the wells just downloaded are not uploaded "
                                   "again\n"))
        if recovery.ok:
            if recovery.pulled and "--nanowell_format" in args:
                # A nanowell run cannot add to earlier results on a remote host (NANOWELL_REMOTE_INCREMENTAL_REFUSAL),
                # so the wells just brought home are recomputed with the rest rather than the run being refused for
                # an Incremental the user never ticked (roll-up review X29).
                self._enqueue(("line", "[remote] resume: a nanowell run cannot resume on a remote host, so the plate "
                                       "is analysed in full\n"))
                return bool(opts.get("incremental", False))
            return bool(opts.get("incremental", False)) or recovery.pulled
        if self._stop_requested.is_set():
            self._enqueue_stopped(where="Remote run")
            return None
        self._enqueue_error(
            f"Could not download {len(recovery.still_missing)} file(s) that the "
            f"previous run of this plate already finished on the remote "
            f"({recovery.remote_dir}). Not re-uploading those wells — resume "
            f"again to retry the download, or tick Force to recompute the "
            f"plate from scratch.\n")
        return None

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
            incremental = self._recover_remote_outputs(output_dir, args, opts)
            if incremental is None:
                return
            refuse_remote_nanowell_incremental(args)       # before anything is staged or sent (X29)
            zipped = stage_local_zips(
                input_dir,
                filename_schema=opts["filename_schema"],
                filename_sep=opts["filename_sep"],
                fluor_tokens=opts["fluor_tokens"] if self._selective_channels else None,
                nuclear_token=opts["nuclear_token"] if self._selective_channels else None,
                log_fn=lambda m: self._enqueue(("line", m)),
                move_source_tifs=bool(opts.get("move_source_tifs", False)),
                # Never under --force: an incremental staging drops every well
                # that is complete locally before it is uploaded, and the
                # pipeline's own --force cannot reprocess what never arrived.
                incremental=incremental,
                prior_output_dir=output_dir,
                csv_prefix=opts.get("csv_prefix", "measurements"),
                force_wells=opts.get("force_wells"),
                # The empty-well blank is a reference the correction is built
                # from, not a well whose own output is wanted — so the staging
                # filters must not diff it away. Passed whenever either
                # empty-well mode is on; ignored otherwise.
                background_wells=(
                    blank_wells_for_run(opts.get("background_well", ""),
                                        opts.get("background_well_map", ""))
                    if "empty_well" in (opts.get("background"),
                                        opts.get("flatfield"))
                    else ""),
            )
            scope = self._scope_remote_run(zipped, opts, args)
            if scope is None:
                return
            zipped, args, grouped, download_n = scope
            if self._nothing_left_to_upload(zipped, opts, output_dir):
                return
            self._announce_staged_plate(zipped, grouped, download_n)

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
                # What Stop sets first. Without it the upload never heard a
                # Stop: it ran to the end and the pipeline was launched.
                stop_event=self._stop_requested,
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
            try:
                for line in proc.stdout:
                    self._enqueue(("line", line))
                    self._note_suspend_evidence(line)
                    if line.strip():
                        tail.append(line.rstrip("\n"))
            except Exception as exc:
                # Whatever ends the read, the child must still be signalled and
                # reaped. It was started with `start_new_session=True`, so it
                # is its own session leader and nothing else will reap it: an
                # exception escaping here left the pipeline running against the
                # user's plate while the GUI reported the run had never
                # started. The decode case is now impossible (`errors="replace"`
                # on the spawn) and this is the general guarantee, not a fix
                # for that one cause.
                self._enqueue((
                    "line",
                    f"[warn] reading the pipeline's output stopped early "
                    f"({type(exc).__name__}: {exc}); the run is being "
                    f"stopped.\n"))
                self._signal_subprocess(proc)
                raise
            finally:
                with swallow("waiting for the pipeline subprocess"):
                    proc.wait()
                self._note_scheduler_job_closed()
            rc = proc.returncode
            cleanup_mode = self._settle_remote_suspend()

            ok = self._remote_runner.verify_and_cleanup(
                ok=(rc == 0),
                cleanup_mode=cleanup_mode,
                dry_run=self._dry_run_cleanup,
                log_fn=lambda m: self._enqueue(("line", m)),
            )
            if rc == 0 and ok and self._suspend_requested.is_set():
                self._enqueue_suspended(where="Remote pipeline")
            elif rc == 0 and self._stop_requested.is_set():
                self._enqueue_stopped(where="Remote pipeline")
            elif rc == 0 and ok:
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
                # Through `_enqueue_error`, not by hand. Building the tuple
                # here skipped `_had_error` and the cancel-state latch, so a
                # remote run that finished and then failed local verification
                # was journalled `ok`: the index held it as a completed run,
                # the resume banner never offered it, and the remote files it
                # had deliberately preserved for retry were never fetched.
                self._enqueue_error(
                    "Remote pipeline finished but local verification failed; "
                    "remote files preserved for retry.\n")
        except Exception as exc:  # surface any failure to the GUI
            if self._stop_requested.is_set():
                # Stop (or a Suspend before launch) ends a remote upload by
                # interrupting its rsync, which then fails: that failure is the
                # stop, and reads as one rather than as the run breaking.
                self._enqueue(("line", f"[stopped] {exc}\n"))
                self._enqueue_stopped(where="Remote run")
            else:
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
