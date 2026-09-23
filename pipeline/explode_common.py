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

from util.well_token import canonical_well_label


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


@dataclass(frozen=True)
class TimeScan:
    """Elapsed hours per timepoint index, and why any are missing.

    What a reader hands :func:`elapsed_hour_tokens` after sampling one
    position's acquisition stamps. Both packed readers produce one — a CZI's
    come from each subblock's own metadata, an ND2's from its frame-times
    cache — and both measure from the first timepoint's reading, so index 0 is
    ``0.0`` whenever it is present at all.

    ``reasons`` is empty on a clean scan and otherwise says, in the run log's
    words, what stopped it. An empty scan with nothing said about it would
    leave a positionally-named plate indistinguishable from one whose writer
    recorded no times.
    """

    hours_by_index: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    #: The position every reading was taken from. A plate scan visits its
    #: wells in turn, so one round's readings span the time it takes to cross
    #: the plate — measured at 31 minutes across 96 scenes — and mixing
    #: positions would fold that spread into the elapsed times.
    scene: int = -1
    bytes_read: int = 0


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
    #: What :func:`regularise_hours` did, where it is worth saying — currently
    #: only the uneven-spacing case, where the measured times were kept rather
    #: than forced onto one interval. Not a failure: the tokens are good.
    note: str = ""


#: Units a measured time may be rounded to, coarsest last. Rounding is what
#: turns a real four-hour run's 4.00139 h into a filename that reads ``4h``
#: instead of six significant figures of stage jitter — but which unit is
#: safe depends on the series, so the coarsest *usable* one is chosen per
#: series rather than fixed.
_ROUND_LADDER_H = tuple(seconds / 3600.0
                        for seconds in (1, 5, 15, 30, 60, 300, 900, 1800, 3600))

#: A measured gap this far from the series' own interval is acquisition jitter
#: rather than a different spacing. 2% of the interval, floored at one minute
#: so a multi-hour interval whose stage waits vary is not called irregular —
#: but that floor is itself capped at a quarter of the interval, or a
#: sub-minute series would have every gap inside tolerance by construction.
_JITTER_FRACTION = 0.02
_JITTER_FLOOR_H = 1.0 / 60.0
_JITTER_FLOOR_FRACTION = 0.25

#: A reading is never rounded to anything coarser than the whole minute.
#: This is the one hard cap, and it exists because the alternative was
#: measured: a unit chosen as "the coarsest that fits a tolerance derived from
#: the interval" gave a 48-hour run a *five-minute* unit, because a long
#: interval produced a large tolerance which licensed a coarse unit. The
#: permission to distort scaled with the thing being measured, which is
#: backwards. A minute is the unit the code this replaced used unconditionally,
#: and nothing about a longer interval makes a coarser one more honest.
_MAX_ROUND_UNIT_H = 60.0 / 3600.0

#: And never coarser than this share of the interval. Half a unit is how far
#: rounding can move one reading, so on a fine interval a whole minute is a
#: visible fraction of a gap: a 12.5-minute run rounded to the minute names
#: frame 19 at 238 min for a recorded 237.5. Two per cent keeps the worst
#: displacement near one per cent of a gap at every scale, which is below what
#: a filename's elapsed time is read to.
#:
#: This replaces a gate that divided the usable unit by the *number of gaps*,
#: modelling per-reading rounding as though its error accumulated. It does not
#: — only the snap accumulates, and the snap is checked directly. But that
#: gate did bound the unit relative to the interval as a side effect, and
#: removing it without putting this in its place reintroduced the drift on
#: exactly the 12.5-minute case the branch was opened to fix.
_ROUND_FRACTION = 0.02

#: How far a *named* timepoint may sit from the time actually recorded for it,
#: before the naming is abandoned and the measured times are used instead.
#:
#: **One minute, flat.**
#:
#: Two rules were measured before this one and both were worse. Scaling the
#: budget with the interval — the shape the rounding unit also had — let a
#: 48-hour run name an image taken at 1104.94 h as ``1104h``: 56 minutes
#: invented, with nothing saying so. Every fraction-of-the-interval rule
#: permits the most fabrication exactly where the absolute error is largest,
#: and "two per cent of the axis" is not what a person reads off a filename.
#: They read an elapsed time.
#:
#: A fraction *capped* at ten minutes was tried next, to keep naming a plate
#: by its schedule — a stage that takes a minute per round makes an hourly run
#: reach frame 24 two minutes late, and ``0h … 23h`` is arguably what the
#: experiment *was*. It did not work, and the reason generalises: any
#: threshold is a discontinuity, so two sister plates whose overheads fall
#: either side of it are named differently and stop matching in
#: ``domain.tp_match``. Raising the budget moves that cliff; it does not
#: remove it. What it does remove is the bound.
#:
#: So the bound stays tight and the cliff is accepted. A minute is enough for
#: the case this rounding exists for — a few seconds of stage overhead on a
#: timelapse of a dozen frames — and small enough to state plainly: **a
#: timepoint's name is never more than a minute from when the image was
#: taken.** A run whose overhead accumulates past that is named at its
#: measured times instead: less tidy, and true.
#:
#: The cost is real and is not hidden. Two plates of one experiment with
#: different per-round overheads can carry timepoints that do not match, so
#: they cannot be concatenated without re-basing. That is a property of naming
#: by measurement rather than by schedule, it predates this module, and the
#: remedy is Review's *Interval per timepoint*.
#: …and never more than this, whatever the jitter, so a loosely-spaced run
#: cannot license an unbounded rewrite of its own clock.
_SNAP_CAP_H = 5.0 / 60.0


def _rounding_unit(interval: float) -> float:
    """The coarsest ladder unit a reading may be rounded to, or ``0.0``.

    Rounding is per *reading*, so the error it introduces is at most half a
    unit on each one and **does not accumulate** — an earlier version of this
    function divided the usable unit by the number of gaps as though it did,
    which is the modelling error behind two opposite defects. It made a long
    interval eligible for a coarse unit (a 48-hour run rounded to five
    minutes) and simultaneously starved a long *run* of any unit at all, so
    the jitter tidying this module exists for silently stopped past about
    eleven frames on an hourly plate and archives went back to carrying
    ``11.0153h`` in their filenames.

    What does accumulate is the *snap* — naming every timepoint at
    ``first + interval × index`` — and that is bounded by checking the result
    against the readings in :func:`regularise_hours`, which is the only place
    the bound can honestly be established.

    ``0.0`` means no unit is usable and the readings are named as they were
    measured, which is what a sub-second time-lapse gets: half a unit must
    fit inside half the interval, or rounding would merge two timepoints.
    """
    limit = min(abs(interval) / 2.0,
                abs(interval) * _ROUND_FRACTION,
                _MAX_ROUND_UNIT_H)
    best = 0.0
    for unit in _ROUND_LADDER_H:
        if unit > limit:
            break
        best = unit
    return best


def _snap_budget(raw_gaps: "list[float]", unit: float) -> float:
    """How far naming a series at one interval may move any of its readings.

    **From the series' own jitter**, which is what makes the rule statable:
    a snap that moves a timepoint further than the acquisition's own spacing
    already varied is inventing elapsed time rather than tidying it away.

    Two earlier versions derived this from the *interval* instead, and each
    was measured doing damage. A plain fraction let a 48-hour run name an
    image taken at 1104.94 h as ``1104h`` — 56 minutes invented. A fraction
    capped at five minutes fixed that and still moved a **perfectly regular**
    238.75-minute series 4.75 minutes onto a 240-minute grid, which is the
    plainest possible case of fabricating time: the acquisition had no jitter
    at all, so there was nothing to clean.

    The floor at *unit* is what keeps the feature working. A constant
    per-round stage overhead has zero gap jitter and is exactly what this
    rounding exists to absorb, so a series whose readings drift by less than
    one rounding unit is still allowed onto the grid.
    """
    if not raw_gaps:
        return min(abs(unit), _SNAP_CAP_H)
    jitter = max(raw_gaps) - min(raw_gaps)
    return min(max(jitter, abs(unit)), _SNAP_CAP_H)


def regularise_hours(hours_by_index: "dict[int, float]",
                     size_t: int) -> tuple["dict[int, float]", str]:
    """Snap a measured series onto its own interval, where it has one.

    An acquisition that ran every four hours does not produce readings exactly
    four hours apart: the stage takes as long as it takes, so a real series
    reads 0, 4.00139, 8.00278 and renders as ``0h``, ``4.00139h``,
    ``8.00278h`` — numbers that are true, unhelpful in a filename, and
    different on the next plate of the same experiment. So the gaps are
    measured, their median taken as *the* interval, and every timepoint named
    at ``first + index × interval``.

    Three things stop that tidying from inventing a time.

    **Only where the series really is regular.** A plate imaged at 0, 4, 8 and
    then 24 hours — a final overnight point, which is an ordinary design — has
    no single interval, and forcing one would state three spacings the
    acquisition never used. Such a series keeps its measured times and comes
    back with a note saying so, because the tokens are still correct and only
    the tidying was declined.

    **Only at a granularity the series can carry**, which
    :func:`_rounding_unit` decides per series. Rounding to the minute is
    jitter-removal on an hourly run, nine minutes of fabricated drift by frame
    20 of a 12.5-minute one, and total destruction on a time-lapse 100 ms
    apart, where every timepoint would land on ``0h``, collide, and send the
    whole run to positional tokens.

    **And only when the result still matches what was measured.** The
    per-gap regularity test above is not enough on a long series: drift that
    stays inside tolerance at every single step accumulates over twenty of
    them, so the snapped series is checked against the readings themselves
    before it is used. This is the load-bearing check — it is what makes the
    other two safe rather than merely careful.

    Returns ``(hours, note)``. The note is empty whenever nothing needs saying,
    which includes the ordinary regular case, a declined snap that changed no
    number, and a series too short to have a gap at all.
    """
    if size_t < 2 or len(hours_by_index) < size_t:
        return dict(hours_by_index), ""
    raw = [hours_by_index[i] for i in range(size_t)]
    n_gaps = size_t - 1
    raw_gaps = sorted(raw[i + 1] - raw[i] for i in range(n_gaps))
    raw_interval = raw_gaps[len(raw_gaps) // 2]
    tolerance = max(abs(raw_interval) * _JITTER_FRACTION,
                    min(_JITTER_FLOOR_H,
                        abs(raw_interval) * _JITTER_FLOOR_FRACTION))
    unit = _rounding_unit(raw_interval)
    measured = ([round(value / unit) * unit for value in raw] if unit
                else list(raw))
    gaps = [measured[i + 1] - measured[i] for i in range(n_gaps)]
    interval = sorted(gaps)[len(gaps) // 2]
    if any(abs(gap - interval) > tolerance for gap in gaps):
        spread = f"{min(gaps):g}–{max(gaps):g} h"
        return ({i: measured[i] for i in range(size_t)},
                f"the timepoints are not evenly spaced ({spread} apart), so "
                f"they are named at the times actually recorded rather than "
                f"at one interval")
    # The snap is the only step whose error accumulates, so it is the only one
    # checked against the readings themselves rather than gap by gap. A series
    # can be regular at every gap and still drift far from a single interval
    # over twenty of them — a per-round stage overhead does exactly that — and
    # the per-gap test above cannot see it.
    #
    # Checked against ``raw``, not against ``measured``, because the question
    # is what the filename claims about the acquisition, and the acquisition
    # is what was recorded.
    budget = _snap_budget(raw_gaps, unit)
    snapped = [measured[0] + interval * i for i in range(size_t)]
    if any(abs(snapped[i] - raw[i]) > budget for i in range(size_t)):
        # Regular gap by gap and drifting over the run. The measured times are
        # right and the interval is a story about them, so keep the times.
        return {i: measured[i] for i in range(size_t)}, ""
    return {i: snapped[i] for i in range(size_t)}, ""


def elapsed_hour_tokens(hours_by_index: "dict[int, float]", size_t: int,
                        *, source_reasons=()) -> ElapsedTokens:
    """Render elapsed hours into one token per timepoint, or decline.

    Declines — ``tokens=None`` — unless *every* timepoint has a reading, every
    reading renders, and the renderings are distinct. The distinctness check is
    the load-bearing one: two timepoints sharing a token write the same member
    name into one archive, ``zipfile`` stores both copies and a reader returns
    only the last, so one timepoint becomes unreachable while the run reports
    having written both.

    The readings are put through :func:`regularise_hours` first, so a run whose
    stage jitter makes every gap 4.00139 h is named ``0h``, ``4h``, ``8h``
    rather than at six significant figures of that jitter.

    *source_reasons* are the caller's own — an unreadable unit, a coordinate
    outside the file's own axis — which this function cannot know about and
    which belong in the same sentence as the ones it can see.
    """
    if size_t <= 1 and not hours_by_index:
        # A plate imaged once is not a time-lapse that lost its clock, and
        # telling its user the timepoints "fell back to positional names"
        # describes a degradation that did not happen. Declining with no
        # reason is how a caller knows to say nothing.
        return ElapsedTokens(None, [])
    reasons = list(source_reasons)
    complete = size_t > 0 and len(hours_by_index) >= size_t
    regular, note = (regularise_hours(hours_by_index, size_t) if complete
                     else (hours_by_index, ""))
    tokens = ([hours_token(regular[i]) for i in range(size_t)]
              if complete else [])
    collided = sorted({t for t in tokens if t and tokens.count(t) > 1})
    unrenderable = [i for i, t in enumerate(tokens) if not t]

    if complete and not collided and not unrenderable:
        return ElapsedTokens(tokens, [], note)

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
