"""What the compute node a Slurm job landed on is, said from inside the job.

A scheduler-backed run says which node it was granted (``allocation granted —
job … on <node> (partition …)``), but not what that node is: how many of its
CPUs the job was given, how much memory, which GPUs. Those are the facts that
explain a slow run — two CPUs of a sixty-four-core node, a CPU-only partition
for a Cellpose plate — and the only place they can be read is inside the
allocation, where Slurm exports them and the node's own ``/proc`` describes it.

So the pipeline says it: :func:`node_specs_line` is logged once at start-up by
``pipeline.parallelism.plan_parallelism`` whenever the run is inside a Slurm job
(``SLURM_JOB_ID`` set), and the app's ``ProgressTracker`` reads it back with
:func:`parse_node_specs` for the Analyze pane's job indicator. It lives here
rather than in the launch command because that command is packed into one line
under a hard length budget, and every scheduler launch carries it.

The line is ``Slurm node specs — key=value | key=value | …`` with the keys in
:data:`SPEC_KEYS`, in that order. A value that could not be read is ``?``.
Stdlib only, every probe optional and bounded: a node without ``nvidia-smi``,
``squeue`` or ``/proc`` still gets a line, and a hung tool cannot stall the run
for more than its timeout.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess

NODE_SPECS_PREFIX = "Slurm node specs — "

#: The fields, in the order they are printed and shown.
SPEC_KEYS = (
    "node", "partition", "cpus_allocated", "cpus_node", "cpu_model",
    "mem_allocated_mb", "mem_per_cpu_mb", "mem_node_mb", "gpus", "time_limit",
)

_SEP = " | "
_PROBE_TIMEOUT_S = 10


def _run(argv: list[str]) -> str:
    """A tool's stdout, or ``""`` when it is missing, fails or hangs."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=_PROBE_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _cpu_model() -> str:
    m = re.search(r"^model name\s*:\s*(.+)$", _read("/proc/cpuinfo"), re.MULTILINE)
    return m.group(1).strip() if m else ""


def _mem_node_mb() -> str:
    m = re.search(r"^MemTotal:\s*(\d+)\s*kB", _read("/proc/meminfo"), re.MULTILINE)
    return str(int(m.group(1)) // 1024) if m else ""


def _gpus() -> str:
    """``A100 (81920 MiB) + A100 (81920 MiB)``, or ``none``."""
    rows = _run(["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader"]).splitlines()
    gpus = []
    for row in rows:
        name, _, memory = row.partition(",")
        if name.strip():
            gpus.append(f"{name.strip()} ({memory.strip()})" if memory.strip()
                        else name.strip())
    return " + ".join(gpus) or "none"


def node_specs_line(env: dict | None = None) -> str:
    """The node-specs line, or ``""`` outside a Slurm job.

    ``SLURM_CPUS_ON_NODE`` / ``SLURM_MEM_PER_NODE`` / ``SLURM_MEM_PER_CPU`` are
    what the *job* was given; ``os.cpu_count()``, ``MemTotal`` and the CPU model
    are what the *node* has. Both halves are shown because the gap between them
    is the point. Memory is per node or per CPU depending on how the launcher
    asked (``--mem`` vs ``--mem-per-cpu``), and Slurm sets only the one that
    applies.
    """
    env = os.environ if env is None else env
    job = str(env.get("SLURM_JOB_ID") or "").strip()
    if not job:
        return ""
    values = {
        "node": env.get("SLURMD_NODENAME") or socket.gethostname(),
        "partition": env.get("SLURM_JOB_PARTITION"),
        "cpus_allocated": env.get("SLURM_CPUS_ON_NODE"),
        "cpus_node": os.cpu_count(),
        "cpu_model": _cpu_model(),
        "mem_allocated_mb": env.get("SLURM_MEM_PER_NODE"),
        "mem_per_cpu_mb": env.get("SLURM_MEM_PER_CPU"),
        "mem_node_mb": _mem_node_mb(),
        "gpus": _gpus(),
        "time_limit": _run(["squeue", "-h", "-j", job, "-o", "%l"]).split("\n")[0],
    }
    # A separator inside a value would split it into a field nobody named.
    body = _SEP.join(
        f"{k}={str(values[k] or '?').replace('|', '/').strip() or '?'}"
        for k in SPEC_KEYS)
    return NODE_SPECS_PREFIX + body


_RE_FIELD = re.compile(r"^([a-z_]+)=(.*)$")


def parse_node_specs(line: str) -> dict[str, str] | None:
    """The fields of a node-specs line, or ``None`` for any other line.

    The line may carry the pipeline's own ``asctime  LEVEL`` prefix, so the
    marker is found rather than anchored. Only :data:`SPEC_KEYS` are kept, so a
    field this version does not know is dropped rather than shown under a name
    nobody chose, and a ``?`` is dropped too: absent and unknown read the same.
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
