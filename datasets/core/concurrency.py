"""Shared worker-count heuristic for per-well thread pools.

Every Datasets tool that parallelises well-scoped file work (Zip Loose Folder,
Concatenate, Subset, Merge Plates) uses the same disjoint-ownership shape the
packed-file exploders pioneered for their own per-well thread pools: one thread
per well, each owning that well's own destination file(s), with no shared
mutable state beyond a callback lock around progress/log calls.

The heuristic itself lives in :mod:`pipeline.explode_common`, where the
exploders take it from, and this module re-exports it under the name the four
tools import. It used to be a second implementation of the same three lines
with a comment saying it mirrored the first — and a heuristic with two
definitions is one that will be tuned in one place and not the other, leaving
two halves of the app disagreeing about how many threads a plate deserves. The
arrow runs the right way for a plain import: ``pipeline/`` is Qt-free and sits
below the panes.
"""

from __future__ import annotations

from pipeline.explode_common import pick_workers  # noqa: F401 (re-export)

__all__ = ["pick_workers"]
