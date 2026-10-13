"""How many worker processes a run uses, how many CPU threads each gets, and
whether Cellpose has a GPU to use.

Extracted from ``process_microscopy.main`` so the run's parallelism is one
decision with one home, testable without starting a run.

**CPU (StarDist and the rest of the per-field work).** StarDist inference has
its sweet spot at about 4 threads on modern x86 — L3 bandwidth saturates past
that for typical microscopy frames — so the default is ``available // 4``
workers of 4 threads each, every core owned by exactly one worker. One core is
reserved for the main process (orchestration, CSV writing, pool management);
without it the workers saturate every core and the OS time-slices the main
process in. On macOS StarDist runs on the Metal GPU, so the worker *count*
scales with cores on a small per-worker budget instead.

**Cellpose and the GPU.** Cellpose runs in Torch, independently of
TensorFlow, so the pipeline's "hide the GPU from TF" policy never applied to
it — but the model was built CPU-only, so a node with a GPU still segmented on
CPU at tens of seconds per field. :func:`pipeline.cellpose._cellpose_device`
now puts each worker's model on a CUDA device when Torch can see one. This
module decides how many workers share the GPUs: every worker holds its own CUDA
context and model, so letting the CPU formula's worker count (often a dozen or
more on a compute node) all land on one GPU wastes its memory and serialises on
it. :data:`CELLPOSE_WORKERS_PER_GPU` per visible GPU is the cap — two, so one
worker's CPU-side work (correction, quantification, writing) overlaps the
other's inference — and the cores freed by the cap go to each worker's thread
budget. ``--workers`` still wins.

The GPU count is read **without importing Torch** in the main process: from
``CUDA_VISIBLE_DEVICES`` when it is set (Slurm sets it for a ``--gres=gpu``
allocation), otherwise from ``nvidia-smi -L``. It is a planning estimate; the
device each worker actually loaded on is what the workers log.

``--cpu_only`` does **not** reach Cellpose. It has always been StarDist's
switch (its help says so), and a remote host's ``cpu_only`` is written by the
bootstrap from whether TensorFlow saw a GPU on the *login* node — false on most
clusters, whose GPUs are on the compute nodes — so honouring it here would keep
Cellpose off exactly the GPU node a ``--gres=gpu`` launcher asked for. A
Cellpose run that should not touch a GPU should not request one.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import subprocess
import sys
from dataclasses import dataclass

#: The segmentation methods that run Cellpose (mirrors ``_CELLPOSE_METHODS`` in
#: ``process_microscopy``, restated so this module imports nothing from it).
CELLPOSE_METHODS = ("cellpose", "cellpose_labelfree")

#: Workers allowed per visible GPU when Cellpose runs on it.
CELLPOSE_WORKERS_PER_GPU = 2


@dataclass(frozen=True)
class ParallelPlan:
    cpu_count: int
    available: int
    tf_threads: int
    workers: int
    threads_per_worker: int
    workers_override: bool
    device_str: str
    #: One line saying where Cellpose will run and why; empty for other methods.
    cellpose_note: str = ""


def visible_gpu_count() -> int:
    """NVIDIA GPUs this process may use, without importing Torch.

    ``CUDA_VISIBLE_DEVICES`` is authoritative when set: Slurm sets it to the
    allocated devices, and an empty value, ``-1`` or ``NoDevFiles`` means none.
    Unset, ``nvidia-smi -L`` is asked; a host without the tool or a driver has
    no GPU this run can use.
    """
    env = os.environ.get("CUDA_VISIBLE_DEVICES")
    if env is not None:
        ids = [d.strip() for d in env.split(",") if d.strip()]
        if not ids or ids[0] in ("-1", "NoDevFiles"):
            return 0
        return len(ids)
    try:
        # Explicit UTF-8: the pipeline cannot import services.subprocess_text,
        # and a GPU name must not be decoded with a Windows code page.
        out = subprocess.run(["nvidia-smi", "-L"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0
    if out.returncode != 0:
        return 0
    return sum(1 for line in out.stdout.splitlines() if line.startswith("GPU "))


def _cpu_count() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return mp.cpu_count()


def _cellpose_plan(method: str, workers: int, available: int,
                   workers_override: bool) -> tuple[int, int | None, str]:
    """``(workers, threads_per_worker or None, note)`` for a Cellpose run."""
    if method not in CELLPOSE_METHODS:
        return workers, None, ""
    n_gpu = visible_gpu_count()
    if n_gpu == 0:
        visible = os.environ.get("CUDA_VISIBLE_DEVICES")
        why = (f"CUDA_VISIBLE_DEVICES={visible!r}" if visible is not None
               else "nvidia-smi reports no GPU")
        return workers, None, f"CPU — no GPU visible on this node ({why})"
    cap = n_gpu * CELLPOSE_WORKERS_PER_GPU
    if workers_override or workers <= cap:
        return workers, None, (
            f"GPU — {n_gpu} visible, {workers} worker(s) share it; each worker "
            f"logs the device its model loaded on")
    return cap, max(1, available // cap), (
        f"GPU — {n_gpu} visible; workers capped at {cap} "
        f"({CELLPOSE_WORKERS_PER_GPU} per GPU) so they do not all hold a CUDA "
        f"context on one device; each worker logs the device its model loaded on")


def plan_parallelism(args, log: logging.Logger) -> ParallelPlan:
    """The run's worker count, threads per worker and device notes, from *args*."""
    cpu_count = _cpu_count()
    available = max(1, cpu_count - 1)
    # Pinning applies whenever workers share cores, --cpu_only or not: an
    # unpinned TF worker takes every core and the workers contend. The default
    # is clamped to the core budget so a 2-core host does not run 4 threads on
    # its one spare core.
    tf_threads = (min(args.tf_threads, available) if args.tf_threads > 0
                  else min(4, available))
    workers = max(1, available // tf_threads)
    on_mac = sys.platform == "darwin"
    if on_mac and not args.cpu_only:
        tf_threads = min(args.tf_threads, available) if args.tf_threads > 0 else 2
        workers = max(2, available // tf_threads)

    workers_override = args.workers > 0
    if workers_override:
        workers = max(1, min(args.workers, available))
        if workers != args.workers:
            log.warning(
                "Requested --workers=%d exceeds available CPU budget (%d); using %d.",
                args.workers, available, workers)

    workers, threads, note = _cellpose_plan(
        str(getattr(args, "segmentation_method", "") or ""),
        workers, available, workers_override)
    if threads is not None:
        tf_threads = threads

    if args.cpu_only:
        device_str = "CPU only (--cpu_only)"
    elif on_mac:
        device_str = "Metal GPU (macOS) if tensorflow-metal installed, else CPU"
    else:
        device_str = "CPU only (non-macOS)"
    return ParallelPlan(cpu_count, available, tf_threads, workers, tf_threads,
                        workers_override, device_str, note)
