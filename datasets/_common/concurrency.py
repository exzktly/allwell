"""Shared worker-count heuristic for per-well thread pools.

Every Datasets tool that parallelises well-scoped file work (Import Loose
Folder, Concatenate, Subset, Merge Plates) uses the same disjoint-ownership
shape ``pipeline.ome_explode.explode_ome_tiff`` pioneered for its own
per-well thread pool: one thread per well, each owning that well's own
destination file(s), with no shared mutable state beyond a callback lock
around progress/log calls. This is the one heuristic that shape needs, kept
in one place so every tool tunes its worker count the same way.
"""

from __future__ import annotations

import os


def pick_workers(workers: int, n_units: int) -> int:
    """Resolve a worker-thread count.

    ``workers <= 0`` means auto: capped at 8 and at the CPU count, mirroring
    ``pipeline.ome_explode._pick_workers``. An explicit ``workers > 0`` is
    still capped at *n_units* — more threads than units of work just contend
    for the same disk I/O with nothing left to parallelise.
    """
    if n_units <= 0:
        return 1
    if workers > 0:
        return max(1, min(workers, n_units))
    return max(1, min(8, os.cpu_count() or 4, n_units))
