"""What a run's own stream says about how it ended, read as it goes.

``PipelineRunner`` knows two things about a run that has stopped: its exit code
and whether *the user* asked it to stop or suspend. Neither says why a run
nobody stopped came to an end, and both of the misreports this module exists to
end came from treating the exit code as the whole story:

* **A termination the app did not ask for was reported as the user's Suspend**
  (tracker D1). The pipeline catches SIGTERM as a cooperative suspend and says
  so (``pipeline.suspend.LOG_MARKER``), and a SIGTERM reaches it for many
  reasons besides the Suspend button — the scheduler's walltime, a preemption,
  an administrator's ``scancel``, the batch driver's own time budget. The
  headline read "Cancelled — suspended; resume to finish", the raw log stayed
  shut, and the journal recorded ``suspended``, while the real cause —
  slurmstepd's ``CANCELLED AT … DUE TO TIME LIMIT`` — sat in the log unread.
* **A dropped connection was reported as "Remote pipeline exited with code
  255"** (tracker D3): ssh's own reason ("Timeout, server … not responding",
  "Connection … closed", "Broken pipe") was in the stream and classed as an
  ordinary line, and nothing said that the job on the host might still be
  running.

So the runner hands every line to :meth:`RunEvidence.note`, and asks it at the
end. Qt-free and import-light, so it can be read beside the runner and tested
without one.
"""

from __future__ import annotations

import re

from pipeline.suspend import LOG_MARKER as SUSPEND_MARKER

# slurmstepd, when the scheduler ends a job or a step:
#   slurmstepd: error: *** JOB 4242 ON gpu07 CANCELLED AT 2026-10-28T10:00:00 DUE TO TIME LIMIT ***
#   slurmstepd: error: *** STEP 4242.0 ON gpu07 CANCELLED AT 2026-10-28T10:00:00 DUE TO PREEMPTION ***
#   slurmstepd: error: *** JOB 4242 ON gpu07 CANCELLED AT 2026-10-28T10:00:00 ***   (an scancel)
_RE_SLURM_CANCELLED = re.compile(
    r"\*\*\* (?:JOB|STEP) (?P<job>[\w.]+) ON (?P<node>\S+) CANCELLED AT (?P<at>\S+)"
    r"(?: DUE TO (?P<cause>[A-Z][A-Z _]*[A-Z]))?[^*]*\*\*\*")
# The batch driver's own per-batch budget (``remote_commands``): a batch whose
# pipeline outlived it is reported with its number.
_RE_TIME_BUDGET = re.compile(r"batch (?P<batch>\d+)(?:/\d+)? exceeded its time budget")

#: ssh's own words for a connection that broke or never formed. Matched on the
#: phrase rather than on a prefix: OpenSSH prints them bare ("Timeout, server …"),
#: prefixed ("client_loop: send disconnect: Broken pipe") or from its multiplexer
#: ("mux_client_request_session: …"). ``classify_log_line`` calls these errors.
_RE_SSH_REASON = re.compile(
    r"(Timeout, server \S+ not responding"
    r"|Connection to \S+ closed"
    r"|Connection closed by \S+"
    r"|Connection reset by \S+"
    r"|client_loop: send disconnect"
    r"|packet_write_wait: .*"
    r"|Broken pipe"
    r"|ssh: connect to host .*"
    r"|ssh: Could not resolve hostname .*"
    r"|kex_exchange_identification: .*)",
    re.IGNORECASE)

# The job id the scheduler named on the run's own stream, for a probe that has
# no sidecar to read it from (the job never started).
_RE_JOB_ID = re.compile(
    r"(?:srun: job (\d+) (?:queued|has been allocated)|allocation granted — job (\d+))", re.IGNORECASE)

#: What each slurmstepd cause means, said for the person reading the headline.
_CAUSE_WORDS = {
    "TIME LIMIT": "the scheduler ended job {job} at its time limit",
    "PREEMPTION": "the scheduler preempted job {job} for another job",
    "NODE FAILURE": "job {job}'s node ({node}) failed",
}


def is_ssh_reason(line: str) -> bool:
    """Whether *line* is ssh saying why a connection broke or never formed."""
    return bool(_RE_SSH_REASON.search(line))


class RunEvidence:
    """What one run's stream said about how it ended. One per run."""

    def __init__(self) -> None:
        self.suspend_seen = False
        self.cancelled: re.Match | None = None
        self.budget_line = ""
        self.ssh_reason = ""
        self.job_id = ""

    def note(self, line: str) -> None:
        """Read one line of the run's output."""
        if not self.suspend_seen and SUSPEND_MARKER in line:
            self.suspend_seen = True
        found = _RE_SLURM_CANCELLED.search(line)
        if found and self.cancelled is None:
            self.cancelled = found
        if not self.budget_line and _RE_TIME_BUDGET.search(line):
            self.budget_line = line.strip()
        reason = _RE_SSH_REASON.search(line)
        if reason:
            # The last one: an ssh that times out and then gives up says the
            # second, more final thing last.
            self.ssh_reason = reason.group(0).strip().rstrip(".")
        job = _RE_JOB_ID.search(line)
        if job:
            self.job_id = job.group(1) or job.group(2)

    def termination(self, where: str = "Remote pipeline") -> str | None:
        """The failure text for a run something other than the user ended, or
        ``None`` when the stream holds no such evidence.

        Asked only of a run the user neither stopped nor suspended: a suspend
        marker on such a run is a signal this app did not send, whatever its
        source, and the cause is named when the scheduler or the driver said it.
        """
        resume = ("The wells that finished are kept; resume from the Analyze banner, or re-run "
                  "with 'incremental', to run the rest")
        if self.cancelled is not None:
            job, node = self.cancelled.group("job"), self.cancelled.group("node")
            cause = (self.cancelled.group("cause") or "").strip()
            said = _CAUSE_WORDS.get(cause, "job {job} was cancelled outside this app "
                                    "(an scancel by you or an administrator)").format(job=job, node=node)
            hint = " — with a longer --time in the host's launcher, or a smaller batch" \
                if cause == "TIME LIMIT" else ""
            return (f"{where} was terminated: {said}. {resume}{hint}. "
                    f"(slurmstepd: {self.cancelled.group(0).strip('* ')})\n")
        if self.budget_line:
            return (f"{where} was terminated: a batch exceeded its time budget and the driver "
                    f"stopped it ({self.budget_line}). {resume}, or raise the per-batch budget.\n")
        if self.suspend_seen:
            return (f"{where} was terminated by a signal this app did not send — on a cluster "
                    f"usually the job's time limit, a preemption or an administrator. {resume}.\n")
        return None

    def connection_lost(self, host: str, *, job_words: str = "") -> str:
        """The failure text for a run whose ssh exited 255 (the connection
        broke, or never formed): ssh's own reason, and what that means for the
        job on the host, which may well still be running."""
        why = f" ({self.ssh_reason})" if self.ssh_reason else ""
        job = f" {job_words}" if job_words else (
            " The job may still be running on the host.")
        return (f"Connection to {host} lost{why}.{job} Resume from the Analyze banner "
                f"downloads the wells it finished and runs the rest. "
                f"(remote pipeline exited with code 255)\n")
