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
import math
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


def hours_token(hours: float) -> str:
    """``"<hours>h"`` in a spelling ``util.timepoint`` can read, or ``""``.

    Six significant figures in **fixed** notation. ``f"{v:g}"`` switches to
    exponent form below 1e-4, which was rare while every OME ``DeltaT`` was
    read as seconds and became routine once milliseconds, microseconds and
    nanoseconds were added: a six-frame time-lapse 100 ms apart rendered
    ``2.77778e-05h``, and ``util.timepoint._UNIT_RE`` matches no exponent, so
    ``parse_hours`` answered ``None`` and ``sort_key`` fell back to sorting
    those tokens as text — putting frames 5 and 6 before frames 2 to 4. A
    number the parser cannot read is worse than the wrong number it replaced,
    because the wrong one at least ordered correctly.

    Answers ``""`` for a value the parser cannot represent at all: not finite,
    or negative (``_UNIT_RE`` accepts no sign, and a writer stamping a
    pre-treatment frame at a negative elapsed time is the case that produces
    one). The caller reads that as a reason to name every timepoint by
    position, and says so.

    Shared by both importers so that one acquisition exported twice — once as
    an OME-TIFF and once as a CZI — cannot produce two different spellings of
    the same elapsed hour. A plate concatenated from the two would then hold
    ``4h`` and ``4.0h`` as separate timepoints.
    """
    if not math.isfinite(hours) or hours < 0.0:
        return ""
    if hours == 0.0:
        return "0h"
    # Decimal places chosen so six significant figures survive, capped: below
    # a microsecond in hours the label is more zeros than number, and two such
    # timepoints colliding is caught by the duplicate check either way.
    places = min(12, max(0, 5 - int(math.floor(math.log10(hours)))))
    text = f"{hours:.{places}f}".rstrip("0").rstrip(".")
    return f"{text}h" if text and text != "0" else "0h"


@dataclass(frozen=True)
class ElapsedTokens:
    """Elapsed-time timepoint tokens, or the reasons there are none.

    ``tokens`` is ``None`` whenever the caller must keep its positional
    ``t0``/``t1`` names, and ``reasons`` then says why in the run log's words.
    Both are always populated together: a fallback with nothing said about it
    leaves a positionally-named plate indistinguishable from one whose writer
    recorded no times at all.
    """

    tokens: "list[str] | None"
    reasons: list[str] = field(default_factory=list)


def elapsed_hour_tokens(hours_by_index: "dict[int, float]", size_t: int,
                        *, source_reasons=()) -> ElapsedTokens:
    """Render elapsed hours into one token per timepoint, or decline.

    Declines — ``tokens=None`` — unless *every* timepoint has a reading, every
    reading renders, and the renderings are distinct. The distinctness check is
    the load-bearing one: two timepoints sharing a token write the same member
    name into one archive, ``zipfile`` stores both copies and a reader returns
    only the last, so one timepoint becomes unreachable while the run reports
    having written both.

    *source_reasons* are the caller's own — an unreadable unit, a coordinate
    outside the file's own axis — which this function cannot know about and
    which belong in the same sentence as the ones it can see.
    """
    reasons = list(source_reasons)
    complete = size_t > 0 and len(hours_by_index) >= size_t
    tokens = ([hours_token(hours_by_index[i]) for i in range(size_t)]
              if complete else [])
    collided = sorted({t for t in tokens if t and tokens.count(t) > 1})
    unrenderable = [i for i, t in enumerate(tokens) if not t]

    if complete and not collided and not unrenderable:
        return ElapsedTokens(tokens, [])

    if collided:
        reasons.append(
            f"the elapsed times render to {len(set(tokens))} distinct label(s) "
            f"for {len(tokens)} timepoint(s) — {', '.join(collided)} repeat(s), "
            f"and two timepoints sharing a label would write every image of the "
            f"second over the first")
    if unrenderable:
        reasons.append(
            f"{len(unrenderable)} elapsed time(s) are not finite or not "
            f"positive, and the app's timepoint parser reads neither")
    if not reasons:
        reasons.append(
            f"only {len(hours_by_index)} of {size_t} timepoint(s) carry a "
            f"readable acquisition time")
    return ElapsedTokens(None, reasons)


def timepoint_plan(timepoints, stride: int = 1) -> list[tuple[int, str]]:
    """The timepoints a conversion keeps, as ``(source index, token)`` pairs.

    *stride* of 1 keeps everything, which is the default and the only value
    that produces a complete dataset. A larger one keeps every *stride*-th
    timepoint counting from the first: a twelve-point timelapse at stride 3
    converts four of them, the source's 0, 3, 6 and 9.

    **The tokens are the source's own, never renumbered.** Stride 3 over
    ``t0 … t11`` yields ``t0, t3, t6, t9`` and not ``t0, t1, t2, t3``, because
    :func:`util.timepoint.parse_hours` reads ``t3`` as three hours and a
    renumbered set would state a spacing the acquisition never had — the whole
    point of dropping timepoints is to convert fewer images, not to rewrite
    when they were taken. Review's *Interval per timepoint* then rescales a
    kept set as correctly as it rescales a complete one.

    **It always keeps the first.** On a treatment timecourse that is the
    pre-treatment reading every later point is normalised against, so it is
    the one timepoint no stride may drop.

    Index and token are returned together rather than separately because the
    caller needs both — the index to read the plane, the token to name it —
    and two lists that must stay in step are a desynchronisation waiting to
    happen.
    """
    tokens = list(timepoints)
    if stride < 1:
        raise ValueError(
            f"A timepoint stride must be 1 or more; got {stride}. Use 1 to "
            f"convert every timepoint."
        )
    return [(index, tokens[index]) for index in range(0, len(tokens), stride)]


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
