"""The Slurm job a remote run holds, as the Analyze pane's indicator shows it.

Three states, because the user asked to tell exactly one of them apart from
the other two: a job that is **open** — granted a node and holding it — as
against one still **queued** for a node, or none at all (never requested, or
closed). The indicator is lit only while open; queued shows it dimmed, so a
queue wait is not mistaken for a running job; with no job it is hidden.

Every transition comes from a line the run prints
(``services.pipeline_runner.ProgressTracker`` turns them into ``sched`` and
``slurm_job`` events; the node's specs are ``pipeline.slurm_node``'s line), so this module is a pure fold over those events and the
tooltip they add up to. Qt-free so both are testable without a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

NONE = "none"
QUEUED = "queued"
OPEN = "open"


@dataclass(frozen=True)
class SlurmJob:
    """What the indicator knows about the current run's scheduler job."""

    state: str = NONE
    job_id: str = ""
    specs: dict[str, str] = field(default_factory=dict)


def on_sched(job: SlurmJob, state: str, job_id: str) -> SlurmJob:
    """Fold one ``sched`` event (``waiting`` / ``queued`` / ``granted``).

    A request for an allocation starts a new job: whatever the previous one
    was, it is no longer the one this run is waiting on, so its node's specs
    must not be shown against the new one.
    """
    if state == "granted":
        return replace(job, state=OPEN, job_id=job_id or job.job_id)
    if state == "waiting":
        return SlurmJob(state=QUEUED)
    # "queued" — the scheduler's own line naming the id of the job we asked for.
    return replace(job, state=QUEUED, job_id=job_id or job.job_id)


def on_specs(job: SlurmJob, specs: dict[str, str]) -> SlurmJob:
    """The node-specs line is printed by the pipeline inside the job, so the
    job is open."""
    return replace(job, state=OPEN, specs=dict(specs))


def on_closed(_job: SlurmJob) -> SlurmJob:
    return SlurmJob()


#: Tooltip rows: (label, how to render from the specs). A row whose fields
#: are all missing is left out rather than shown as "?".
def _cpus(s: dict[str, str]) -> str:
    got, node = s.get("cpus_allocated", ""), s.get("cpus_node", "")
    if got and node:
        return f"{got} allocated of {node} on the node"
    if got:
        return f"{got} allocated"
    return f"{node} on the node" if node else ""


def _gb(mb: str) -> str:
    try:
        return f"{int(float(mb)) / 1024:.1f} GB"
    except ValueError:
        return mb


def _memory(s: dict[str, str]) -> str:
    node = s.get("mem_node_mb", "")
    if s.get("mem_allocated_mb"):
        got = _gb(s["mem_allocated_mb"]) + " allocated"
    elif s.get("mem_per_cpu_mb"):
        got = _gb(s["mem_per_cpu_mb"]) + " per CPU allocated"
    else:
        got = ""
    if got and node:
        return f"{got} of {_gb(node)} on the node"
    return got or (f"{_gb(node)} on the node" if node else "")


_ROWS = (
    ("Node", lambda s: s.get("node", "")),
    ("Partition", lambda s: s.get("partition", "")),
    ("CPUs", _cpus),
    ("CPU model", lambda s: s.get("cpu_model", "")),
    ("Memory", _memory),
    ("GPUs", lambda s: s.get("gpus", "")),
    ("Time limit", lambda s: s.get("time_limit", "")),
)


def tooltip(job: SlurmJob) -> str:
    """The indicator's tooltip for *job*: its state, then the node it holds."""
    ident = f" {job.job_id}" if job.job_id else ""
    if job.state == QUEUED:
        return f"Slurm job{ident} is queued — waiting for the scheduler to grant a node."
    if job.state != OPEN:
        return "No Slurm job is open."
    lines = [f"Slurm job{ident} is open."]
    rows = [(label, render(job.specs)) for label, render in _ROWS]
    rows = [(label, value) for label, value in rows if value]
    lines.append("")
    if rows:
        lines.extend(f"{label}: {value}" for label, value in rows)
    else:
        lines.append("The node did not report its specifications.")
    return "\n".join(lines)
