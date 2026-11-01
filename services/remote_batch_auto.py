"""Batch size 0: one well per worker.

The Analyze tab's **Batch size** says how many wells a remote run uploads and processes at a time. ``0`` sends one
well for each worker the pipeline will start, so the first batch is as small as it can be while keeping every worker
busy: the run starts after uploading that many wells rather than the whole plate, and the remote's disk holds one
batch at a time.

The workers are counted before the run, here, from what the node will be: the Workers field when it is set, and
otherwise the CPUs and GPUs the scheduler command asks for (``--cpus-per-task`` / ``-c``; ``--gres=gpu:…``,
``--gpus``, ``--gpus-per-node``, ``--gpus-per-task``, ``-G``), put through the pipeline's own arithmetic
(``pipeline.parallelism.expected_workers``). When neither says — a host with no scheduler command, a command that
names no CPUs, or one that takes the whole node (``--exclusive``), whose size is not known until it is granted — the
run uploads the whole plate as before and the log says why and what to set.

The setting stays ``0``: the resolved size is the run's (``RESOLVED_KEY`` in its options), never written back into the
form or the dataset's ``pipeline_info.json``, so a reloaded dataset keeps asking for one well per worker.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from pipeline.parallelism import CELLPOSE_METHODS, CELLPOSE_WORKERS_PER_GPU, expected_workers

#: The key under which a run's options carry the batch size it actually runs with, beside the user's own
#: ``remote_batch_size`` (which stays as typed, ``0`` included).
RESOLVED_KEY = "remote_batch_size_resolved"


@dataclass(frozen=True)
class LauncherResources:
    """What a scheduler command asks for, as far as the worker count needs it. ``None`` = it does not say."""

    cpus: int | None = None
    gpus: int | None = None
    exclusive: bool = False


def _int(text: str) -> int | None:
    try:
        n = int(text)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _count_at_end(fields: list[str]) -> int:
    """``["l4", "4"]`` → 4, ``["4"]`` → 4, ``["l4"]`` or ``[]`` → 1 (a type named, or nothing: one of it)."""
    if fields and re.fullmatch(r"\d+", fields[-1]):
        return int(fields[-1])
    return 1


def _gres_gpus(spec: str) -> int | None:
    """The GPUs in a ``--gres`` value — ``gpu``, ``gpu:4``, ``gpu:l4:4``, or a comma list with one GPU entry."""
    for part in spec.split(","):
        fields = part.strip().split(":")
        if fields[0] == "gpu":
            return _count_at_end(fields[1:])
    return None


def _gpus_value(spec: str) -> int | None:
    """The GPUs in a ``--gpus`` / ``-G`` / ``--gpus-per-node`` / ``--gpus-per-task`` value — ``4`` or ``l4:4``."""
    spec = spec.strip()
    return _count_at_end(spec.split(":")) if spec else None


def launcher_resources(launcher: str) -> LauncherResources:
    """The CPUs, GPUs and ``--exclusive`` a scheduler command (``srun …``) asks for."""
    try:
        tokens = shlex.split(launcher or "")
    except ValueError:
        tokens = (launcher or "").split()
    cpus = gpus = None
    exclusive = False
    gpus_per_task = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        name, eq, value = tok.partition("=")
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""

        def take() -> str:
            nonlocal i
            if eq:
                return value
            i += 1
            return nxt

        if name in ("--cpus-per-task", "-c"):
            cpus = _int(take())
        elif tok.startswith("-c") and len(tok) > 2 and tok[2:].isdigit():
            cpus = _int(tok[2:])
        elif name == "--gres":
            gpus = _gres_gpus(take())
        elif name in ("--gpus", "--gpus-per-node", "-G"):
            gpus = _gpus_value(take())
        elif name == "--gpus-per-task":
            gpus_per_task = _gpus_value(take())
        elif name == "--exclusive":
            exclusive = True
        i += 1
    if gpus is None and gpus_per_task is not None:
        gpus = gpus_per_task                            # every launch runs one task (remote_srun_retry.one_task)
    return LauncherResources(cpus=cpus, gpus=gpus, exclusive=exclusive)


def _opt_int(opts: dict, key: str) -> int:
    try:
        return max(0, int(str(opts.get(key, 0) or 0).strip() or 0))
    except (TypeError, ValueError):
        return 0


def resolve_batch_size(opts: dict, host) -> tuple[int, str]:
    """``(batch size the run uses, what the log should say about it)``.

    A positive Batch size is the user's and is returned as it is, with nothing to say. ``0`` becomes the worker count
    when it can be known (see the module docstring), or stays ``0`` — the whole plate in one launch — with a line
    saying why."""
    asked = _opt_int(opts, "remote_batch_size")
    if asked > 0:
        return asked, ""
    workers = _opt_int(opts, "workers")
    tf_threads = _opt_int(opts, "tf_threads")
    method = str(opts.get("segmentation_method", "") or "")
    launcher = str(getattr(host, "compute_launcher", "") or "")
    res = launcher_resources(launcher) if launcher.strip() else LauncherResources()
    exclusive = res.exclusive or bool(getattr(host, "exclusive_node", False))
    cpus = None if exclusive else res.cpus
    # ``cpu_only`` is StarDist's switch and never keeps Cellpose off a GPU (``pipeline.parallelism``'s docstring), so
    # the GPUs the scheduler command asks for count whatever it says: zeroing them planned a Cellpose run — every
    # nanowell run among them — as a CPU run, with batches larger than the workers the node then starts.
    gpus = res.gpus or 0
    head = "[remote] batch size 0 → "
    if cpus is not None:
        n = expected_workers(cpus, gpus, method, workers=workers, tf_threads=tf_threads)
        why = (f"the Workers field ({workers}), within {cpus} CPUs" if workers else
               f"{cpus} CPUs" + (f" and {gpus} GPU(s)" if method in CELLPOSE_METHODS and gpus else "")
               + " in the scheduler command"
               + (f", Cellpose at up to {CELLPOSE_WORKERS_PER_GPU} a GPU" if method in CELLPOSE_METHODS and gpus
                  else ""))
        return n, f"{head}{n} well(s) a batch, one per worker ({n} worker(s): {why}).\n"
    if workers:
        return workers, (f"{head}{workers} well(s) a batch, one per worker (the Workers field; the scheduler command "
                         "does not say how many CPUs the node has, so the pipeline may start fewer).\n")
    if not launcher.strip():
        reason = "this host has no scheduler command, so the number of workers is not known before the run"
    elif exclusive:
        reason = ("the scheduler command takes the whole node (--exclusive), whose size is not known until it is "
                  "granted")
    else:
        reason = "the scheduler command does not say how many CPUs the run gets (--cpus-per-task)"
    return 0, (f"{head}the whole plate in one batch: {reason}. Set the Workers field, or --cpus-per-task in the "
               "scheduler command, for one well per worker; or set a Batch size.\n")
