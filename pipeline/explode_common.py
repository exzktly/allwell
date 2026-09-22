"""What every packed-container importer does the same way.

Two importers now turn one packed acquisition file into the per-well ``.zip``
archives the pipeline reads — :mod:`pipeline.ome_explode` for a packed
OME-TIFF and :mod:`pipeline.czi_explode` for a Zeiss CZI. They differ entirely
in how they *read* their source and not at all in what they produce: the same
member names, the same atomic per-well archive, the same rule for deciding
whether a well an interrupted run left behind is finished, the same worker-count
policy, the same in-memory TIFF encode.

Those pieces live here rather than in either importer, because when they lived
in ``ome_explode`` the second importer's only choices were to import a private
name from the first or to write a second definition of each rule. Two
definitions of "which members does a finished well hold?" is not a style
problem: the two would answer differently the day one of them changed, and a
resume that consults the wrong one either rewrites a finished plate or accepts
a half-finished well as complete.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import tifffile


class ExplodeCancelled(Exception):
    """Raised when a ``should_abort`` callback stops an explode mid-run."""


@dataclass
class ExplodeResult:
    """A summary of an explode run, for logging and a pre-run preview."""

    wells: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    timepoints: list[str] = field(default_factory=list)
    fovs_per_well: int = 0
    images_written: int = 0
    # Wells a resumed run found already complete and did not rewrite. Reported
    # rather than merely skipped: a run that converted a tenth of a plate and
    # one that converted all of it otherwise print the same closing line.
    wells_resumed: list[str] = field(default_factory=list)


def resolve_positions(
    total: int, n_wells: int, fovs_per_well: int | None, *, what: str, name: str,
) -> int:
    """Return the validated FOVs-per-well for *total* positions/frames.

    ``None`` derives it (``total`` must be an exact multiple of *n_wells*); a
    given value is cross-checked against ``n_wells * fovs_per_well == total``.
    """
    if fovs_per_well is None:
        if total % n_wells != 0:
            raise ValueError(
                f"{name} has {total} {what}, which is not divisible by the "
                f"well grid ({n_wells} well(s)). Fields-of-view per well must be "
                f"a whole number — check the grid dimensions."
            )
        return total // n_wells
    if n_wells * fovs_per_well != total:
        raise ValueError(
            f"{name} has {total} {what}, but the {n_wells} well(s) × "
            f"{fovs_per_well} FOV(s) per well = {n_wells * fovs_per_well}. "
            f"Check the grid dimensions and the FOVs-per-well value."
        )
    return fovs_per_well


def expected_members(experiment: str, channels, well: str, fovs: int,
                     timepoints) -> set[str]:
    """Every member name a finished ``<well>.zip`` holds, for these settings.

    The exact set, not a count: an archive left by a run with a different grid
    holds the right *number* of images under the wrong names, and resuming onto
    it would leave a dataset stitched from two different well mappings.
    """
    return {
        f"{experiment}_{channel}_{well}_f{fov}_{tp}.tif"
        for channel in channels
        for fov in range(fovs)
        for tp in timepoints
    }


def well_is_complete(path: Path, expected: set[str]) -> bool:
    """True when *path* is a readable archive already holding *expected*.

    Reads the central directory only, so this costs a couple of seeks however
    large the archive is. An unreadable or truncated zip is treated as absent —
    ``atomic_zip`` cannot leave one, but a half-copied folder can.
    """
    if not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            return expected.issubset(set(zf.namelist()))
    except (OSError, zipfile.BadZipFile):
        return False


def pick_workers(workers: int, n_wells: int) -> int:
    """Resolve the worker-thread count (0 = auto), capped at the well count."""
    if n_wells <= 0:
        return 1
    if workers and workers > 0:
        return max(1, min(workers, n_wells))
    return max(1, min(8, os.cpu_count() or 4, n_wells))


def encode_tiff(plane: np.ndarray) -> bytes:
    """Encode a 2-D plane as a single-page TIFF in memory (no temp file)."""
    buf = io.BytesIO()
    tifffile.imwrite(buf, plane)
    return buf.getvalue()


def worker_groups(n_wells: int, n_workers: int) -> list[list[int]]:
    """Split ``range(n_wells)`` into *n_workers* disjoint, non-empty groups.

    Strided rather than contiguous, so a plate whose later wells are heavier
    than its earlier ones does not leave one thread finishing alone. Each group
    is one thread's wells and no two groups share one, which is what lets each
    thread own its archive handles without a lock.
    """
    if n_wells <= 0 or n_workers <= 0:
        return []
    return [g for g in (list(range(i, n_wells, n_workers))
                        for i in range(n_workers)) if g]
