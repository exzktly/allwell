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

# Re-exported: the timepoint naming lives in its own module so that a
# header scan can import it without pulling numpy into the process, and
# every existing importer of this one keeps working unchanged.
from pipeline.timepoints import (  # noqa: F401
    ElapsedTokens,
    TimeScan,
    elapsed_hour_tokens,
    hours_token,
    regularise_hours,
)
from util.well_token import canonical_well_label


def publishing_total(planes_per_well: int) -> int:
    """The denominator a well's progress is reported against: its planes + 1.

    The extra step is the archive being *published* — closed, fsynced,
    re-read and verified, and renamed from its staging name onto
    ``<WELL>.zip`` by :func:`util.zip_write.atomic_zip`. That happens after the
    last plane is written, and on a large well it is seconds of work. Reporting
    the last plane as ``planes / planes`` told the page the well was finished
    while its archive still sat under a staging name, so the plate drew a
    green ✓ on a well that did not yet exist on disk — and that a cancel or a
    failed verify at that moment would then leave missing. Every plane is
    therefore reported out of ``planes + 1``, and only the exit of the
    ``atomic_zip`` block reports the final step.
    """
    return max(1, int(planes_per_well)) + 1


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
    #: ``{well: images the source held no data for}``, for the wells that had
    #: any. A gap is skipped rather than written as a blank — a zero-filled
    #: image is indistinguishable from a real dark field — and is carried here
    #: so the run log, the closing summary and the dataset's own marker can
    #: each say which wells came out short. A well that is short and looks
    #: complete is CLAUDE.md §7's cardinal failure, so this travels with the
    #: result rather than being logged once and forgotten.
    images_missing: dict = field(default_factory=dict)


def shortfall_summary(missing: dict) -> str:
    """One line naming the wells a conversion could not fill, or ``""``.

    Wells and counts, which is the granularity asked for: enough to know which
    well to look at and how much of it is absent, without a per-plane list
    nobody reads. The wells are named in plate order rather than in whichever
    order the workers finished, so two runs of the same plate say the same
    thing.

    Shared by the run log, the page's closing summary and the dataset marker,
    because three spellings of one sentence is how the log and the artifact
    come to disagree about what happened.
    """
    if not missing:
        return ""
    named = ", ".join(f"{well} ({count})"
                      for well, count in sorted(missing.items()))
    total = sum(missing.values())
    return (f"{total} image(s) had no data in the source and were skipped, in "
            f"{len(missing)} well(s): {named}. Those wells are in the dataset "
            f"and are not complete.")


def uneven_fields_note(wells, counts, nominal: int) -> str:
    """One line naming the wells imaged at fewer positions than the plate, or ``""``.

    A CZI plate scan names one scene per imaged position, so the fields of a
    well are however many scenes name it, and nothing obliges an acquisition to
    give every well the same number. The plate is then described by its largest
    well — anything else would report a whole plate by its worst corner — and
    this is what stops that description being a lie about the rest: a readout
    saying "96 well(s) × 7 FOV(s)" over a plate where four wells hold five is
    indistinguishable from one where all ninety-six hold seven, which is
    CLAUDE.md §7's cardinal failure.

    Wells and counts, in plate order rather than acquisition order, capped at
    six named with the remainder counted so that a wholly irregular plate
    produces a sentence rather than a paragraph.
    """
    short = sorted((well, count) for well, count in zip(wells, counts)
                   if count < nominal)
    if not short:
        return ""
    named = ", ".join(f"{well} ({count})" for well, count in short[:6])
    more = "" if len(short) <= 6 else f", and {len(short) - 6} more"
    return (f"{len(short)} well(s) hold fewer than {nominal} field(s): "
            f"{named}{more}.")


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




def _ordinal_suffix(n: int) -> str:
    """``st``/``nd``/``rd``/``th`` for *n*.

    The teens are the whole reason this is a function: 11, 12 and 13 take
    ``th`` despite ending in 1, 2 and 3, and a table keyed on the last digit
    alone prints "every 21th".
    """
    if n % 100 in (11, 12, 13):
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def stride_note(stride: int, size_t: int, kept_tokens: list[str]) -> str:
    """" (every 3rd of 12: t0, t3, t6, t9)" — or "" when nothing was dropped.

    Spelled out in the run log rather than left to arithmetic on the totals.
    A plate converted at a stride writes a complete-looking dataset holding a
    third of its timelapse, and the one place a reader would otherwise notice
    is a timepoint count they have no reason to be counting (CLAUDE.md §7).
    """
    if stride <= 1 or size_t <= 1:
        return ""
    shown = ", ".join(kept_tokens[:4])
    if len(kept_tokens) > 4:
        shown += f", ... {kept_tokens[-1]}"
    ordinal = f"{stride}{_ordinal_suffix(stride)}"
    return f" (every {ordinal} of {size_t}: {shown})"


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


def worker_groups(n_wells: "int | list[int]",
                  n_workers: int) -> list[list[int]]:
    """Split the wells to convert into *n_workers* disjoint, non-empty groups.

    Takes either a count — ``range(n_wells)``, the whole plate — or an explicit
    list of **well indices**, which is how a subset conversion is expressed.

    Strided rather than contiguous, so a plate whose later wells are heavier
    than its earlier ones does not leave one thread finishing alone. Each group
    is one thread's wells and no two groups share one, which is what lets each
    thread own its archive handles without a lock.
    """
    indices = (list(range(n_wells)) if isinstance(n_wells, int)
               else list(n_wells))
    if not indices or n_workers <= 0:
        return []
    return [g for g in ([indices[j] for j in range(i, len(indices), n_workers)]
                        for i in range(n_workers)) if g]


def subset_note(n_selected: int, n_wells: int) -> str:
    """``"3 of 96 well(s) (subset)"`` when a run covers part of the plate.

    A subset conversion writes a complete, correct dataset that holds a
    fraction of the acquisition, and a plain well count reads identically to a
    plate that only ever had that many wells (CLAUDE.md §7: degraded output
    must never be indistinguishable from good output). Both numbers, always,
    where a whole-plate run prints the count it always did.
    """
    if n_selected >= n_wells:
        return f"{n_wells} well(s)"
    return f"{n_selected} of {n_wells} well(s) (subset)"


def wells_to_convert(well_labels, subset) -> list[int]:
    """The **indices** of the wells a run should write, in plate order.

    Empty *subset* means the whole plate, which is the default and the case
    every caller had before subsets existed.

    Indices, deliberately, and this is the whole reason the helper exists.
    A well's index is what maps it back to its source: well ``i`` takes stage
    positions ``i * fovs`` onward in all three converters. Filtering the label
    *list* would renumber every well after the first one dropped, so a
    conversion of B01 alone would read A01's images and write them under B01's
    name — a complete, correctly named archive of the wrong well, which is the
    failure CLAUDE.md §7 is written against. The mapping therefore stays whole
    and only the set of indices walked changes, which is also why a subset
    cannot alter what any converted well contains.

    Matching is by canonical label, so ``a1`` and ``A01`` name the same well.
    A subset naming a well the plate does not have raises rather than silently
    converting fewer wells than asked for: a run that quietly drops half a
    request looks exactly like one that was asked for half.
    """
    labels = list(well_labels)
    if not subset:
        return list(range(len(labels)))
    wanted = {canonical_well_label(w) or str(w).strip().upper()
              for w in subset}
    wanted.discard("")
    index_of: dict[str, int] = {}
    for i, label in enumerate(labels):
        index_of.setdefault(canonical_well_label(label) or str(label), i)
    missing = sorted(w for w in wanted if w not in index_of)
    if missing:
        raise ValueError(
            f"The wells to convert include {', '.join(missing)}, which this "
            f"source does not hold. It has "
            f"{', '.join(labels[:8])}{' …' if len(labels) > 8 else ''}. "
            f"Clear the selection to convert the whole plate.")
    return sorted(index_of[w] for w in wanted)
