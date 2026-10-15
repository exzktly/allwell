"""What the compute node a Slurm job landed on is, said from inside the job.

A scheduler-backed run says which node it was granted (``allocation granted —
job … on <node> (partition …)``), but not what that node is: how many of its
CPUs the job was given, how much memory, which GPUs. Those are the facts that
explain a slow run — two CPUs of a sixty-four-core node, a CPU-only partition
for a Cellpose plate — and the only place they can be read is inside the
allocation, where Slurm exports them and the node's own ``/proc`` describes it.

So :func:`node_specs_snippet` is one more ``echo`` beside the granted line, in
both launch shapes (``services.remote_commands``), and :func:`parse_node_specs`
reads it back for the Analyze pane's job indicator. The line is
``[remote] scheduler: node specs — key=value | key=value | …``: the keys are
:data:`SPEC_KEYS`, in that order, and a value Slurm or the node did not report
is ``?``. Every probe is optional — a node without ``nvidia-smi``, ``squeue`` or
``/proc`` still prints the line, with the fields it could not fill marked.
"""

from __future__ import annotations

import re

#: The line's prefix. The wording is this app's own (the echo is ours), so the
#: parser can anchor on it exactly.
NODE_SPECS_PREFIX = "[remote] scheduler: node specs — "

#: The line the runner prints when the remote process that held the job has
#: exited — the moment the job is no longer open. Printed locally, after the
#: ssh session ends, because nothing inside the allocation runs after the
#: pipeline: the single-run launch ``exec``s it.
JOB_CLOSED_LINE = "[remote] scheduler: job closed — the remote process has exited\n"

#: The fields, in the order they are printed and shown.
SPEC_KEYS = (
    "node", "partition", "cpus_allocated", "cpus_node", "cpu_model",
    "mem_allocated_mb", "mem_per_cpu_mb", "mem_node_mb", "gpus", "time_limit",
)

_SEP = " | "


def node_specs_snippet() -> str:
    """Shell that prints the node-specs line. Runs inside the allocation.

    ``SLURM_CPUS_ON_NODE`` / ``SLURM_MEM_PER_NODE`` / ``SLURM_MEM_PER_CPU`` are
    what the *job* was given; ``nproc --all`` and ``MemTotal`` are what the
    *node* has. Both halves are shown because the gap between them is the
    point — a job given a sliver of a large node is the classic slow run.
    Memory is per node or per CPU depending on how the launcher asked
    (``--mem`` vs ``--mem-per-cpu``), and Slurm sets only the one that applies.
    """
    probes = (
        "_aw_gpu=$(nvidia-smi --query-gpu=name,memory.total "
        "--format=csv,noheader 2>/dev/null "
        "| awk -F', *' '{printf \"%s%s (%s)\", (NR>1?\" + \":\"\"), $1, $2}'); "
        "_aw_cpu=$(awk -F': *' '/^model name/{print $2; exit}' "
        "/proc/cpuinfo 2>/dev/null); "
        "_aw_mem=$(awk '/^MemTotal:/{print int($2/1024); exit}' "
        "/proc/meminfo 2>/dev/null); "
        "_aw_ncpu=$(nproc --all 2>/dev/null); "
        '_aw_lim=$(squeue -h -j "${SLURM_JOB_ID:-0}" -o %l 2>/dev/null | head -n1); '
    )
    values = {
        "node": "${SLURMD_NODENAME:-$(hostname)}",
        "partition": "${SLURM_JOB_PARTITION:-?}",
        "cpus_allocated": "${SLURM_CPUS_ON_NODE:-?}",
        "cpus_node": "${_aw_ncpu:-?}",
        "cpu_model": "${_aw_cpu:-?}",
        "mem_allocated_mb": "${SLURM_MEM_PER_NODE:-?}",
        "mem_per_cpu_mb": "${SLURM_MEM_PER_CPU:-?}",
        "mem_node_mb": "${_aw_mem:-?}",
        "gpus": "${_aw_gpu:-none}",
        "time_limit": "${_aw_lim:-?}",
    }
    body = _SEP.join(f"{k}={values[k]}" for k in SPEC_KEYS)
    return f'{probes}echo "{NODE_SPECS_PREFIX}{body}"; '


_RE_FIELD = re.compile(r"^([a-z_]+)=(.*)$")


def parse_node_specs(line: str) -> dict[str, str] | None:
    """The fields of a node-specs line, or ``None`` for any other line.

    Only :data:`SPEC_KEYS` are kept, so a field this version does not know is
    dropped rather than shown under a name nobody chose. A value reported as
    ``?`` is dropped too: absent and unknown read the same in the tooltip.
    """
    start = line.find(NODE_SPECS_PREFIX)
    if start < 0:
        return None
    fields: dict[str, str] = {}
    for part in line[start + len(NODE_SPECS_PREFIX):].strip().split(_SEP):
        m = _RE_FIELD.match(part.strip())
        if m and m.group(1) in SPEC_KEYS:
            value = m.group(2).strip()
            if value and value != "?":
                fields[m.group(1)] = value
    return fields
