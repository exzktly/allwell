"""Pure planning for the Concatenate Datasets feature.

Qt-free (it reads two ``pipeline_info.json`` files and globs the two dataset
folders to enumerate wells; the only writes are the destructive-restart marker
and, when clearing a failed prior attempt, folder removal — see
:mod:`~datasets.concatenate.apply`). The whole timepoint-remap decision lives
here so it can be unit-tested on tiny ``tmp_path`` fixtures without building
the app.

The one product rule this module encodes: to make dataset 2 *continue after*
dataset 1, dataset 2's first timepoint is relabelled to
``ds1_last + ds1_interval`` and every other dataset-2 timepoint keeps its
relative spacing (a single additive hour offset). ``ds1_interval`` is the median
gap between dataset 1's sorted, unique timepoints — robust to one irregular gap,
and undefined (an error) when dataset 1 has a single timepoint.

Every measurement row carries **three** timepoint-derived values, not one:
the ``timepoint`` label string, a numeric ``timepoint_hours`` column, and a
``filename`` column naming the source image. All three are rewritten for
dataset-2 rows — a merge that touches only the label string leaves the numeric
column (the one 19 viewer modules actually plot from) pointing at the
*original*, unshifted hours, which is how the first version of this feature
shipped broken. Timepoints reach us as hour-valued labels (``"0"``, ``"24h"``,
``"2d"``, or the compound ``"02d04h30m"`` form) and are read through
:func:`util.timepoint.parse_hours`; a label that can't be read as a time is
refused rather than guessed at. New dataset-2 labels are rendered in dataset
1's own detected format (see :class:`TimepointFormat`) so a merge never mixes
two label conventions in the same file, and dataset 1 stays byte-identical.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from util.aw_safe import swallow
from util.acquisition_record import AcquisitionRecordError, read_record
from util.timepoint import parse_hours, sort_key
from domain.image_identity import PipelineSchema, load_schema
from datasets.core.dataset_paths import (
    make_dataset_api,
    DatasetPaths,
    dataset_wells,
    describe_well_collisions,
    destination_is_complete_dataset,
    has_folder_mode_output,
    in_containers,  # noqa: F401 — re-exported for datasets.concatenate.apply
    out_csvs,
    out_zips,  # noqa: F401 — re-exported for datasets.concatenate.apply
    well_spelling_collisions,
    check_compatible as _check_compatible,
    known_channel_tokens as _known_channel_tokens,
)
from datasets.core.member_names import rewrite_member_field
from datasets.concatenate.raw_compute import (
    check_raw_compatible, is_raw_dataset, raw_info, raw_paths,
)

# Written at the start of a concatenation and removed only after the merged
# sidecar is written — the sole authority for "a previous attempt into this
# folder started and never finished." Content under in/out with no marker and
# no pipeline_info.json is NOT a failed-run signature (it could be a zipped
# but unanalyzed plate) and must never be auto-cleared.
IN_PROGRESS_MARKER = ".aw_concat_in_progress"

# Structural sidecar fields that must be identical for two datasets to be
# concatenatable: they describe *what* each measurement is and how filenames are
# laid out. Channel token lists (fluor_tokens/…) are allowed to differ and get
# unioned — the CSV column set already tolerates that — but if these disagree the
# two datasets aren't the same experiment and stitching them is meaningless.
# Sidecar list fields that are unioned (order-preserving) when merging.
_UNION_LIST_KEYS = ("fluor_tokens", "smfish_tokens", "shape_features")


class ConcatError(Exception):
    """A concatenation cannot proceed. The message is user-facing (shown in the
    status window), so it must read as a plain-English explanation."""


# The five dataset entry points, bound to this tool's error class and its
# own in-progress marker. See ``core.dataset_paths.make_dataset_api``
# for why they are generated rather than written out: three tools carried
# the same five wrappers, differing only in these two choices.
_api = make_dataset_api(ConcatError, IN_PROGRESS_MARKER)
resolve_dataset = _api.resolve_dataset
read_info = _api.read_info
destination_has_marker = _api.destination_has_marker
destination_is_failed_run = _api.destination_is_failed_run
destination_has_unrecognized_content = _api.destination_has_unrecognized_content

@dataclass(frozen=True)
class TimepointFormat:
    """The label convention dataset 1's timepoints are written in.

    ``kind`` is one of ``"compound"`` (``DDdHHhMMm``), ``"hours"`` (``Xh``),
    ``"days"`` (``Xd``), ``"minutes"`` (``Xmin``/``Xm``), ``"bare"`` (a plain
    number), or ``"fallback"`` (nothing recognisable was found — new labels
    render as plain ``Xh`` and the merge reports why).
    """

    kind: str
    day_width: int = 2
    hour_width: int = 2
    min_width: int = 2


_COMPOUND_RE = re.compile(r"^(\d+)d(\d+)h(\d+)m$")
_HOURS_RE = re.compile(r"^\d+(?:\.\d+)?h$")
_DAYS_RE = re.compile(r"^\d+d$")
_MINUTES_RE = re.compile(r"^\d+(?:\.\d+)?(?:min|m)$")
_BARE_RE = re.compile(r"^\d+(?:\.\d+)?$")

# Tried most-specific first; also the tie-break order when two kinds are
# equally common among dataset 1's labels.
_FORMAT_ORDER = ("compound", "hours", "days", "minutes", "bare")


def detect_timepoint_format(labels: list[str]) -> tuple[TimepointFormat, str | None]:
    """Infer the label convention dataset 1 uses, from its own labels.

    Returns ``(format, warning)`` — *warning* is set (naming what happened)
    only when no label matched a known convention, in which case *format*
    falls back to plain ``Xh``.
    """
    counts: dict[str, int] = {}
    widths: dict[str, tuple[int, int, int]] = {}
    for raw in labels:
        s = str(raw).strip()
        if not s:
            continue
        m = _COMPOUND_RE.match(s)
        if m:
            counts["compound"] = counts.get("compound", 0) + 1
            dw, hw, mw = len(m.group(1)), len(m.group(2)), len(m.group(3))
            pdw, phw, pmw = widths.get("compound", (dw, hw, mw))
            widths["compound"] = (max(pdw, dw), max(phw, hw), max(pmw, mw))
        elif _HOURS_RE.match(s):
            counts["hours"] = counts.get("hours", 0) + 1
        elif _DAYS_RE.match(s):
            counts["days"] = counts.get("days", 0) + 1
        elif _MINUTES_RE.match(s):
            counts["minutes"] = counts.get("minutes", 0) + 1
        elif _BARE_RE.match(s):
            counts["bare"] = counts.get("bare", 0) + 1

    if not counts:
        return TimepointFormat(kind="fallback"), (
            "Dataset 1's timepoint labels don't match a recognised format "
            "(compound DDdHHhMMm, Xh, Xd, Xmin, or a bare number) — new "
            "dataset-2 labels will use plain 'Xh' instead of matching "
            "dataset 1's convention."
        )

    best = max(_FORMAT_ORDER, key=lambda k: (counts.get(k, 0), -_FORMAT_ORDER.index(k)))
    if best == "compound":
        dw, hw, mw = widths["compound"]
        return TimepointFormat(kind="compound", day_width=dw, hour_width=hw, min_width=mw), None
    return TimepointFormat(kind=best), None


def render_timepoint(hours: float, fmt: TimepointFormat) -> str:
    """Render *hours* as a label in *fmt*'s convention.

    Always round-trips back through :func:`util.timepoint.parse_hours`. A
    convention that can't cleanly represent the value (e.g. a fractional day
    under the whole-days-only ``"days"`` format) falls back to plain ``Xh``
    for that value alone.
    """
    if fmt.kind == "compound":
        total_min = round(hours * 60)
        days, rem = divmod(total_min, 1440)
        hh, mm = divmod(rem, 60)
        return f"{days:0{fmt.day_width}d}d{hh:0{fmt.hour_width}d}h{mm:0{fmt.min_width}d}m"
    if fmt.kind == "days":
        days = hours / 24.0
        if days == int(days):
            return f"{int(days)}d"
        return f"{hours:g}h"
    if fmt.kind == "minutes":
        return f"{hours * 60:g}m"
    if fmt.kind == "bare":
        return f"{hours:g}"
    return f"{hours:g}h"


def remap_timepoint_label(old_label: str, offset_hours: float, fmt: TimepointFormat) -> str:
    """Shift *old_label* by *offset_hours* and render it in *fmt*.

    Raises :class:`ConcatError` if *old_label* isn't a finite, time-valued
    token — the rest of the app keys off the parsed hours, so a token that
    can't be read must stop the merge rather than pass through unchanged.
    """
    hours = parse_hours(old_label)
    if hours is None or not math.isfinite(hours):
        raise ConcatError(
            f"timepoint {old_label!r} can't be read as a time. Concatenation "
            "needs every timepoint to be time-valued (e.g. '0', '24h', '2d')."
        )
    return render_timepoint(hours + offset_hours, fmt)


# ── compatibility + timepoint remap ──────────────────────────────────────────
def check_compatible(info1: dict, info2: dict) -> None:
    """Raise :class:`ConcatError` if the two sidecars aren't concatenatable.

    The comparison itself is :func:`datasets.core.dataset_paths.check_compatible`,
    which Merge Plates needs too; this wrapper only names the error class, the
    same shape as :func:`resolve_dataset` above.
    """
    _check_compatible(info1, info2, error_cls=ConcatError)


def _timepoint_hours(info: dict, *, label: str) -> list[tuple[str, float]]:
    """Return ``(label, hours)`` for each timepoint, sorted, or raise."""
    pairs: list[tuple[str, float]] = []
    for raw in info.get("available_timepoints", []) or []:
        token = str(raw).strip()
        if not token:
            continue
        hours = parse_hours(token)
        if hours is None or not math.isfinite(hours):
            raise ConcatError(
                f"{label}: timepoint {token!r} can't be read as a time. "
                "Concatenation needs every timepoint to be time-valued "
                "(e.g. '0', '24h', '2d')."
            )
        pairs.append((token, hours))
    if not pairs:
        raise ConcatError(f"{label}: no timepoints listed in pipeline_info.json.")
    pairs.sort(key=lambda pair: pair[1])
    return pairs


def _interval_hours(sorted_hours: list[float]) -> float:
    """Median gap between consecutive unique timepoints (the cadence)."""
    uniq = sorted(set(sorted_hours))
    if len(uniq) < 2:
        raise ConcatError(
            "Dataset 1 has only one timepoint, so its imaging interval is "
            "unknown — there's no cadence to continue. Enter the time between "
            "dataset 1's timepoint and dataset 2's first one."
        )
    diffs = [later - earlier for earlier, later in zip(uniq, uniq[1:])]
    return statistics.median(diffs)


def _refuse_well_collisions(paths: DatasetPaths, label: str) -> None:
    """Refuse *paths* when two files claim one well under different
    spellings.

    Every well this tool copies is found through ``in_containers`` /
    ``out_zips`` / ``out_csvs``, each of which maps a canonical well to one
    path — so an ``A1.zip`` sitting beside an ``A01.zip`` is simply not in
    the map the copy walks, and a whole well's images leave no trace in the
    destination or in the summary. The map cannot say that happened, so the
    folder is asked directly, once, before anything is written.
    """
    message = describe_well_collisions(well_spelling_collisions(paths))
    if message:
        raise ConcatError(f"{label} can't be concatenated. {message}")


def compute_offset(info1: dict, info2: dict, *,
                   gap_hours: float | None = None) -> tuple[float, float, float | None]:
    """The additive hour offset that makes dataset 2 continue after dataset 1.

    Dataset 2's first timepoint lands *gap_hours* after dataset 1's last. With
    no gap given, the gap is dataset 1's median interval, the rule this tool
    always had, and a dataset 1 with one timepoint is refused because it has
    no interval to continue. Returns ``(offset_hours, ds1_last_hours,
    ds1_interval_hours)``; the interval is ``None`` when dataset 1 has no
    cadence and the caller supplied the gap instead.
    """
    tp1 = _timepoint_hours(info1, label="dataset 1")
    tp2 = _timepoint_hours(info2, label="dataset 2")

    ds1_last = tp1[-1][1]
    if gap_hours is None:
        interval = _interval_hours([hours for _, hours in tp1])
        gap_hours = interval
    else:
        if not math.isfinite(gap_hours) or gap_hours <= 0:
            raise ConcatError("The time gap must be a positive number of hours.")
        uniq = sorted({hours for _, hours in tp1})
        interval = (statistics.median([b - a for a, b in zip(uniq, uniq[1:])])
                    if len(uniq) > 1 else None)
    return ds1_last + gap_hours - tp2[0][1], ds1_last, interval


# ── the time gap between the two datasets ────────────────────────────────────
#: How far an entered gap may sit from the recorded one before the page says
#: so: two per cent of the gap, but never less than a minute. A stage that
#: takes a minute per round makes a nominal schedule and a stopwatch reading
#: differ by about that much, and that is not a disagreement worth a warning.
_GAP_TOLERANCE_FRACTION = 0.02
_GAP_TOLERANCE_FLOOR_H = 1.0 / 60.0

GAP_ENTERED = "entered"
GAP_RECORDED = "recorded"
GAP_INTERVAL = "interval"


@dataclass(frozen=True)
class GapDefault:
    """What the page pre-fills the time-gap field with, and why."""

    hours: float | None          # None → nothing to pre-fill; the field is required
    source: str | None           # GAP_RECORDED / GAP_INTERVAL / None
    recorded_hours: float | None = None
    note: str = ""               # why there is no (or a lesser) default


@dataclass(frozen=True)
class GapChoice:
    """The gap a merge uses, where it came from, and any warning about it."""

    hours: float
    source: str                  # GAP_ENTERED / GAP_RECORDED / GAP_INTERVAL
    recorded_hours: float | None = None
    warning: str | None = None


def format_gap(hours: float) -> str:
    """A gap as the text the field shows; ``parse_hours`` reads it back."""
    return f"{round(hours * 60) / 60:.4g}h"


def parse_gap_hours(text: str) -> float:
    """The hours in a typed gap (``6``, ``6h``, ``1d``, ``1d2h30m``), or raise."""
    text = (text or "").strip()
    if not text:
        raise ConcatError("Enter the time between dataset 1's last timepoint "
                          "and dataset 2's first one (for example 24h).")
    hours = parse_hours(text)
    if hours is None or not math.isfinite(hours):
        raise ConcatError(f"The time gap {text!r} can't be read as a duration "
                          "(use forms like 6, 24h, 1d or 1d2h30m).")
    if hours <= 0:
        raise ConcatError("The time gap must be longer than zero.")
    return hours


def _recorded_start(ds: DatasetPaths, token: str) -> tuple[datetime | None, str]:
    """The recorded absolute start of timepoint *token*, and why it is absent."""
    try:
        record = read_record(ds.root)
    except AcquisitionRecordError as exc:
        return None, f"its acquisition record is unreadable ({exc})"
    if record is None:
        return None, "it has no acquisition record"
    entry = (record.get("timepoints") or {}).get(token)
    if not entry or entry.get("started_at") is None:
        return None, f"its record has no start time for timepoint {token!r}"
    return datetime.fromisoformat(entry["started_at"].replace("Z", "+00:00")), ""


def recorded_gap_hours(ds1: DatasetPaths, ds2: DatasetPaths, info1: dict,
                       info2: dict) -> tuple[float | None, str]:
    """Hours from dataset 1's last recorded start to dataset 2's first.

    ``(None, reason)`` unless both datasets recorded those two instants on
    comparable clocks (both with a timezone, or both without) and dataset 2's
    is later. Rounded to the minute: the record keeps the precise instants.
    """
    last1 = _timepoint_hours(info1, label="dataset 1")[-1][0]
    first2 = _timepoint_hours(info2, label="dataset 2")[0][0]
    start1, why1 = _recorded_start(ds1, last1)
    if start1 is None:
        return None, f"dataset 1: {why1}"
    start2, why2 = _recorded_start(ds2, first2)
    if start2 is None:
        return None, f"dataset 2: {why2}"
    if (start1.tzinfo is None) != (start2.tzinfo is None):
        return None, ("the two recorded start times mix a clock with a timezone "
                      "and one without, so their difference is not decidable")
    hours = (start2 - start1).total_seconds() / 3600.0
    if hours <= 0:
        return None, (f"dataset 2 was recorded {format_gap(abs(hours))} before "
                      "dataset 1's last timepoint — check the order of the datasets")
    return round(hours * 60) / 60, ""


#: Two pixel sizes this far apart (relative) are different magnifications.
_SCALE_TOLERANCE = 0.01


def scale_warning(ds1: DatasetPaths, ds2: DatasetPaths, info1: dict, info2: dict) -> str:
    """A sentence when the two acquisitions were taken at different pixel sizes.

    Compares dataset 1's last timepoint with dataset 2's first, as the
    acquisition records state them. Different tile plans are fine (each
    timepoint keeps its own positions), but intensities measured at two
    magnifications may not compare, so the page says so. ``""`` when the
    sizes agree or either is not recorded: an unknown size is not a mismatch.
    """
    def size(ds: DatasetPaths, token: str) -> tuple[float | None, float | None] | None:
        try:
            record = read_record(ds.root)
        except AcquisitionRecordError:
            return None
        entry = ((record or {}).get("timepoints") or {}).get(token) or {}
        scale = entry.get("pixel_size_um") or {}
        return scale.get("x"), scale.get("y")

    first = size(ds1, _timepoint_hours(info1, label="dataset 1")[-1][0])
    second = size(ds2, _timepoint_hours(info2, label="dataset 2")[0][0])
    if not first or not second:
        return ""
    for axis, a, b in (("x", first[0], second[0]), ("y", first[1], second[1])):
        if a is not None and b is not None and abs(a - b) > _SCALE_TOLERANCE * max(a, b):
            return (f"The two datasets were imaged at different pixel sizes "
                    f"({axis}: {a:g} and {b:g} µm per pixel). Their positions are "
                    "kept as recorded, but intensities taken at different "
                    "magnifications may not be comparable.")
    return ""


def default_gap(ds1: DatasetPaths, ds2: DatasetPaths, info1: dict,
                info2: dict) -> GapDefault:
    """Pre-fill order: the recorded gap, then dataset 1's interval, then nothing."""
    recorded, why = recorded_gap_hours(ds1, ds2, info1, info2)
    if recorded is not None:
        return GapDefault(recorded, GAP_RECORDED, recorded_hours=recorded)
    hours = sorted({h for _, h in _timepoint_hours(info1, label="dataset 1")})
    if len(hours) > 1:
        interval = statistics.median([b - a for a, b in zip(hours, hours[1:])])
        return GapDefault(interval, GAP_INTERVAL, note=f"No recorded gap: {why}.")
    return GapDefault(None, None, note=(
        f"Dataset 1 has one timepoint and no recorded gap ({why}), so there is "
        "no interval to continue: enter the time gap."))


def choose_gap(ds1: DatasetPaths, ds2: DatasetPaths, info1: dict, info2: dict,
               entered_hours: float | None) -> GapChoice:
    """The gap to merge with: the user's entry, else the pre-filled default.

    An entered gap always wins. When it differs from the recorded one by more
    than the tolerance the choice carries a warning naming both numbers.
    """
    default = default_gap(ds1, ds2, info1, info2)
    if entered_hours is None:
        if default.hours is None:
            raise ConcatError(default.note)
        return GapChoice(default.hours, default.source or GAP_INTERVAL,
                         default.recorded_hours)
    recorded = default.recorded_hours
    warning = None
    if recorded is not None:
        tolerance = max(_GAP_TOLERANCE_FRACTION * recorded, _GAP_TOLERANCE_FLOOR_H)
        if abs(entered_hours - recorded) > tolerance:
            warning = (f"You entered {format_gap(entered_hours)}, but the "
                       f"acquisition records are {format_gap(recorded)} apart. "
                       "Using the gap you entered.")
    return GapChoice(entered_hours, GAP_ENTERED, recorded, warning)


KIND_ANALYSED = "analysed"
KIND_RAW = "raw"
KIND_ANALYSED_RAW = "analysed+raw"


def open_pair(ds1_root: Path | str, ds2_root: Path | str):
    """Resolve both datasets and read their sidecars: ``(ds1, ds2, info1, info2, kind)``.

    *kind* is :data:`KIND_ANALYSED` (two analysed datasets), :data:`KIND_RAW`
    (two raw ones: ``in/`` only, schema read from the archives) or
    :data:`KIND_ANALYSED_RAW` (an analysed dataset 1 and a raw dataset 2: the
    analysed outputs are kept and dataset 2's timepoints wait for an
    incremental Analyze run). A raw dataset 1 beside an analysed dataset 2 is
    refused: the earliest timepoints would be left unanalysed beneath analysed
    later ones, which the other three combinations cover better.
    """
    raw1, raw2 = is_raw_dataset(ds1_root), is_raw_dataset(ds2_root)
    if raw1 and not raw2:
        raise ConcatError(
            "Dataset 1 is a raw (not yet analysed) dataset and dataset 2 is analysed. "
            "That would leave the earliest timepoints unanalysed beneath analysed later "
            "ones. Put the analysed dataset first, or concatenate the raw datasets "
            "first and analyse the merged plate once.")
    if raw2:
        ds2 = raw_paths(ds2_root)
        info2 = raw_info(ds2, label="dataset 2", error_cls=ConcatError)
        if raw1:
            ds1 = raw_paths(ds1_root)
            return ds1, ds2, raw_info(ds1, label="dataset 1", error_cls=ConcatError), info2, KIND_RAW
        ds1 = resolve_dataset(ds1_root, label="dataset 1")
        return ds1, ds2, read_info(ds1, label="dataset 1"), info2, KIND_ANALYSED_RAW
    ds1 = resolve_dataset(ds1_root, label="dataset 1")
    ds2 = resolve_dataset(ds2_root, label="dataset 2")
    return ds1, ds2, read_info(ds1, label="dataset 1"), read_info(ds2, label="dataset 2"), KIND_ANALYSED


@dataclass(frozen=True)
class GapPreview:
    """What the page shows for the time-gap field, for two datasets and a typed gap."""

    default_text: str = ""                 # what to pre-fill; "" when there is no default
    default_source: str | None = None      # GAP_RECORDED / GAP_INTERVAL / None
    note: str = ""                         # why the default is absent or weaker
    labels: tuple[str, ...] = ()           # dataset 2's timepoint labels after the merge
    warning: str = ""                      # entered gap disagrees with the records
    error: str = ""                        # the merge cannot proceed as typed


def gap_preview(ds1_root: Path | str, ds2_root: Path | str, text: str) -> GapPreview:
    """The default, the resulting labels and any warning, before anything is copied.

    Reads only the two sidecars and acquisition records. *text* is the field's
    contents; empty means "use the default", which is an error only when there
    is none.
    """
    try:
        ds1, ds2, info1, info2, _kind = open_pair(ds1_root, ds2_root)
        default = default_gap(ds1, ds2, info1, info2)
        default_text = format_gap(default.hours) if default.hours is not None else ""
        shown = dict(default_text=default_text, default_source=default.source,
                     note=default.note)
        try:
            entered = parse_gap_hours(text) if (text or "").strip() else None
            choice = choose_gap(ds1, ds2, info1, info2, entered)
        except ConcatError as exc:
            return GapPreview(**shown, error=str(exc))
        offset, _last, _interval = compute_offset(info1, info2, gap_hours=choice.hours)
        scale = scale_warning(ds1, ds2, info1, info2)
        fmt, _warn = detect_timepoint_format(
            [str(t) for t in info1.get("available_timepoints", []) or []])
        labels = tuple(remap_timepoint_label(str(t).strip(), offset, fmt)
                       for t in info2.get("available_timepoints", []) or [] if str(t).strip())
        warning = " ".join(w for w in (choice.warning or "", scale) if w)
        return GapPreview(**shown, labels=labels, warning=warning)
    except ConcatError as exc:
        return GapPreview(error=str(exc))


# ── sidecar merge ────────────────────────────────────────────────────────────
def _ordered_union(first, second) -> list:
    """Concatenate two sequences, dropping later duplicates, preserving order."""
    merged = list(first)
    for item in second:
        if item not in merged:
            merged.append(item)
    return merged


def merge_info(info1: dict, info2: dict, *, offset_hours: float, tp_format: TimepointFormat) -> dict:
    """Build the merged sidecar.

    Structural fields and the user-side blocks (``cell_gating``,
    ``sample_definitions``, ``ratios``, ``notes``) come from dataset 1 — the
    wells and channels are shared, so they transfer unchanged. Timepoints are
    dataset 1's followed by dataset 2's remapped labels; the token lists are
    unioned.
    """
    merged = dict(info1)

    ds1_tps = [str(t).strip() for t in info1.get("available_timepoints", []) or [] if str(t).strip()]
    ds2_tps = [
        remap_timepoint_label(str(t).strip(), offset_hours, tp_format)
        for t in info2.get("available_timepoints", []) or []
        if str(t).strip()
    ]
    merged["available_timepoints"] = sorted(_ordered_union(ds1_tps, ds2_tps), key=sort_key)

    merged["available_fovs"] = _ordered_union(
        info1.get("available_fovs", []) or [], info2.get("available_fovs", []) or []
    )
    for key in _UNION_LIST_KEYS:
        merged[key] = _ordered_union(info1.get(key, []) or [], info2.get(key, []) or [])
    return merged


def known_channel_tokens(info1: dict, info2: dict) -> tuple[str, ...]:
    """Re-export of :func:`datasets.core.dataset_paths.known_channel_tokens`,
    pinned to this module's two-dataset signature so existing callers and tests
    are unchanged."""
    return _known_channel_tokens(info1, info2)


def _csv_header(path: Path) -> list[str]:
    import csv

    with swallow(f"read CSV header {path}"):
        with path.open(newline="") as handle:
            return next(csv.reader(handle), [])
    return []


def column_warnings(ds1: DatasetPaths, ds2: DatasetPaths, wells_both: tuple[str, ...]) -> tuple[str, ...]:
    """Warn about measurement columns present in only one dataset.

    A column absent from one side gets filled with ``""`` on merge, which
    ``load_well_csv`` coerces to NaN — and ``Included``/QC gating treats NaN
    as excluded, so those rows silently drop out of every plot. Cheap
    (header-only reads), so it runs for every shared well.
    """
    csv1, csv2 = out_csvs(ds1), out_csvs(ds2)
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for well in wells_both:
        p1, p2 = csv1.get(well), csv2.get(well)
        if p1 is None or p2 is None:
            continue
        cols1, cols2 = set(_csv_header(p1)), set(_csv_header(p2))
        only1 = sorted(cols1 - cols2)
        only2 = sorted(cols2 - cols1)
        for col in only1:
            if ("1", col) not in seen:
                seen.add(("1", col))
                warnings.append(f"{col!r} is present in dataset 1 but not dataset 2 — those rows will be blank there.")
        for col in only2:
            if ("2", col) not in seen:
                seen.add(("2", col))
                warnings.append(f"{col!r} is present in dataset 2 but not dataset 1 — those rows will be blank there.")
    return tuple(warnings)


# ── zip-member filename rewrite ──────────────────────────────────────────────
def rewrite_member_name(
    name: str,
    *,
    schema: PipelineSchema,
    channels: tuple[str, ...],
    offset_hours: float,
    tp_format: TimepointFormat,
) -> tuple[str, bool]:
    """Return ``(new_name, was_rewritten)`` with *name*'s timepoint field
    shifted by *offset_hours*.

    A thin timepoint-shift wrapper over the field-agnostic
    :func:`datasets.core.member_names.rewrite_member_field` — see that
    module for the structural-conformance gate this shares with Merge
    plates' well-token rewrite.
    """
    def _shift(old_token: str) -> str | None:
        hours = parse_hours(old_token)
        if hours is None or not math.isfinite(hours):
            return None
        return render_timepoint(hours + offset_hours, tp_format)

    return rewrite_member_field(
        name, schema=schema, channels=channels, field="timepoint", new_value=_shift,
    )


# ── top-level plan ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MergePlan:
    """Everything the worker needs to execute a concatenation.

    The worker walks ``ds1``/``ds2`` on disk itself; this plan carries the
    decisions that are awkward to recompute mid-copy — the timepoint offset
    and format, the well partition, and the merged sidecar — plus the numbers
    and warnings the status window reports.
    """

    ds1: DatasetPaths
    ds2: DatasetPaths
    dest: DatasetPaths
    schema: PipelineSchema
    channels: tuple[str, ...]           # known channel tokens, for member-name discovery
    offset_hours: float                 # added to every dataset-2 timepoint
    tp_format: TimepointFormat
    ds1_last_hours: float
    ds1_interval_hours: float | None    # None: dataset 1 has one timepoint
    gap_hours: float                    # dataset 2's first timepoint lands this long after dataset 1's last
    gap_source: str                     # GAP_ENTERED / GAP_RECORDED / GAP_INTERVAL
    wells_ds1_only: tuple[str, ...]
    wells_ds2_only: tuple[str, ...]
    wells_both: tuple[str, ...]
    merged_info: dict                   # full merged pipeline_info.json content
    dest_is_restart: bool = False       # dest has leftovers from an earlier, unfinished attempt
    format_warning: str | None = None
    gap_warning: str | None = None
    scale_warning: str = ""
    #: Both datasets are raw (``in/`` only): the merge writes a raw ``in/`` and
    #: no ``out/`` at all, and is verified on its images rather than on rows.
    raw: bool = False
    #: Dataset 1 is analysed and dataset 2 raw: dataset 1's outputs are kept as
    #: they are, and dataset 2's images wait for an incremental Analyze run.
    ds2_raw: bool = False
    column_warnings: tuple[str, ...] = ()
    #: User-side blocks dataset 2 carried that the merged sidecar does not.
    #: ``merge_info`` takes these from dataset 1 wholesale — the documented
    #: rule — but it was applied silently, and ``wells_ds2_only`` is normally
    #: non-empty, so a plate's replicate sets, groups, notes and ratios simply
    #: were not in the merged dataset with nothing saying they had been left.
    ds2_blocks_dropped: tuple[str, ...] = ()


def build_merge_plan(ds1_root: Path | str, ds2_root: Path | str, dest_root: Path | str,
                     *, gap_hours: float | None = None) -> MergePlan:
    """Validate two datasets and a destination, and produce a :class:`MergePlan`.

    *gap_hours* is the time the user entered between dataset 1's last timepoint
    and dataset 2's first; ``None`` takes the pre-filled default
    (:func:`default_gap`). Raises :class:`ConcatError` (user-facing message) on any incompatibility.
    Reads only — the destination is not created or cleared here.
    """
    ds1, ds2, info1, info2, kind = open_pair(ds1_root, ds2_root)
    raw = kind == KIND_RAW
    ds2_raw = kind == KIND_ANALYSED_RAW
    dest_root = Path(dest_root)
    dest = DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")

    if dest.root in (ds1.root, ds2.root):
        raise ConcatError("The destination must be a different folder from both datasets.")

    if ds1.root == ds2.root:
        raise ConcatError(
            "Dataset 1 and Dataset 2 are the same folder — concatenating a "
            "dataset with itself isn't supported. Pick two different source "
            "folders."
        )

    if destination_is_complete_dataset(dest):
        raise ConcatError(
            f"The destination ({dest.root}) already holds a finished dataset "
            "(out/pipeline_info.json exists there) — concatenating into it "
            "would overwrite real data. Choose a new, empty folder."
        )
    if destination_has_unrecognized_content(dest):
        raise ConcatError(
            f"The destination ({dest.root}) already has files that weren't "
            "created by a previous concatenation attempt — concatenating "
            "into it risks overwriting or mixing in unrelated data. Choose "
            "an empty folder."
        )
    dest_is_restart = destination_is_failed_run(dest)

    if kind == KIND_ANALYSED:
        check_compatible(info1, info2)
    else:
        check_raw_compatible(info1, info2, error_cls=ConcatError)

    tp_index = int(info1.get("tp_index", -1))
    if tp_index < 0:
        raise ConcatError(
            "pipeline_info.json has no valid tp_index, so the timepoint field in "
            "filenames can't be located — concatenation can't relabel dataset 2."
        )

    _refuse_well_collisions(ds1, "Dataset 1")
    _refuse_well_collisions(ds2, "Dataset 2")

    if not raw and (has_folder_mode_output(ds1) or (not ds2_raw and has_folder_mode_output(ds2))):
        which = "dataset 1" if has_folder_mode_output(ds1) else "dataset 2"
        raise ConcatError(
            f"{which} stores its QC images as per-well folders (out/<WELL>/) "
            "instead of <WELL>_out.zip archives — Concatenate only supports "
            "the zipped output layout. Re-run the pipeline with compressed "
            "output, or zip the folders manually, then try again."
        )

    gap = choose_gap(ds1, ds2, info1, info2, gap_hours)
    offset, ds1_last, interval = compute_offset(info1, info2, gap_hours=gap.hours)
    tp_format, format_warning = detect_timepoint_format(
        [str(t) for t in info1.get("available_timepoints", []) or []]
    )
    # Dataset 2's inferred sidecar is a reading of filenames, not an analysis:
    # nothing of it (channel tokens, say) belongs in dataset 1's sidecar.
    merged_info = (dict(info1) if ds2_raw
                   else merge_info(info1, info2, offset_hours=offset, tp_format=tp_format))
    ds2_blocks_dropped = () if (raw or ds2_raw) else tuple(
        name for name in ("sample_definitions", "notes", "ratios", "cell_gating")
        if (info2 or {}).get(name)
    )

    wells1 = dataset_wells(ds1)
    wells2 = dataset_wells(ds2)
    if not wells1 and not wells2:
        raise ConcatError("Neither dataset has any wells to concatenate.")
    wells_both = tuple(sorted(wells1 & wells2))

    return MergePlan(
        ds1=ds1,
        ds2=ds2,
        dest=dest,
        schema=load_schema(info1),
        channels=known_channel_tokens(info1, info2),
        offset_hours=offset,
        tp_format=tp_format,
        ds1_last_hours=ds1_last,
        ds1_interval_hours=interval,
        gap_hours=gap.hours,
        gap_source=gap.source,
        gap_warning=gap.warning,
        scale_warning=scale_warning(ds1, ds2, info1, info2),
        wells_ds1_only=tuple(sorted(wells1 - wells2)),
        wells_ds2_only=tuple(sorted(wells2 - wells1)),
        ds2_blocks_dropped=ds2_blocks_dropped,
        wells_both=wells_both,
        raw=raw,
        ds2_raw=ds2_raw,
        merged_info=merged_info,
        dest_is_restart=dest_is_restart,
        format_warning=format_warning,
        column_warnings=() if (raw or ds2_raw) else column_warnings(ds1, ds2, wells_both),
    )
