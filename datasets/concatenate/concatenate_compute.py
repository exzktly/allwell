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
from pathlib import Path

from util.aw_safe import swallow
from util.timepoint import parse_hours, sort_key
from well_viewer.data.image_identity import PipelineSchema, load_schema
from datasets._common.dataset_paths import (
    DatasetPaths,
    DatasetPathsError,
    dataset_wells,
    destination_has_marker as _destination_has_marker,
    destination_has_unrecognized_content as _destination_has_unrecognized_content,
    destination_is_complete_dataset,
    destination_is_failed_run as _destination_is_failed_run,
    has_folder_mode_output,
    in_containers,  # noqa: F401 — re-exported for datasets.concatenate.apply
    out_csvs,
    out_zips,  # noqa: F401 — re-exported for datasets.concatenate.apply
    resolve_dataset as _resolve_dataset,
    read_info as _read_info,
)
from datasets._common.member_names import rewrite_member_field

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
_COMPAT_KEYS = (
    "schema",
    "separator",
    "schema_fields",
    "well_index",
    "channel_index",
    "fov_index",
    "tp_index",
    "nuclear_token",
    "segmentation_method",
)

# Sidecar list fields that are unioned (order-preserving) when merging.
_UNION_LIST_KEYS = ("fluor_tokens", "smfish_tokens", "shape_features")


class ConcatError(Exception):
    """A concatenation cannot proceed. The message is user-facing (shown in the
    status window), so it must read as a plain-English explanation."""


def resolve_dataset(root: Path | str, *, label: str) -> DatasetPaths:
    """:func:`datasets._common.dataset_paths.resolve_dataset`, translated to
    :class:`ConcatError` so every raise in this module carries the same type."""
    try:
        return _resolve_dataset(root, label=label)
    except DatasetPathsError as exc:
        raise ConcatError(str(exc)) from exc


def read_info(paths: DatasetPaths, *, label: str) -> dict:
    """:func:`datasets._common.dataset_paths.read_info`, translated to
    :class:`ConcatError`."""
    try:
        return _read_info(paths, label=label)
    except DatasetPathsError as exc:
        raise ConcatError(str(exc)) from exc


def destination_has_marker(dest: DatasetPaths) -> bool:
    return _destination_has_marker(dest, IN_PROGRESS_MARKER)


def destination_is_failed_run(dest: DatasetPaths) -> bool:
    return _destination_is_failed_run(dest, IN_PROGRESS_MARKER)


def destination_has_unrecognized_content(dest: DatasetPaths) -> bool:
    return _destination_has_unrecognized_content(dest, IN_PROGRESS_MARKER)


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
    """Raise :class:`ConcatError` if the two sidecars aren't concatenatable."""
    diffs = [
        f"  • {key}: dataset 1 = {info1.get(key)!r}, dataset 2 = {info2.get(key)!r}"
        for key in _COMPAT_KEYS
        if info1.get(key) != info2.get(key)
    ]
    if diffs:
        raise ConcatError(
            "The two datasets were not analysed the same way, so their "
            "measurements can't be concatenated:\n" + "\n".join(diffs)
        )


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
            "unknown — there's no cadence to continue. Concatenation needs "
            "dataset 1 to have at least two timepoints."
        )
    diffs = [later - earlier for earlier, later in zip(uniq, uniq[1:])]
    return statistics.median(diffs)


def compute_offset(info1: dict, info2: dict) -> tuple[float, float, float]:
    """Compute the additive hour offset that makes dataset 2 continue after
    dataset 1. Returns ``(offset_hours, ds1_last_hours, ds1_interval_hours)``."""
    tp1 = _timepoint_hours(info1, label="dataset 1")
    tp2 = _timepoint_hours(info2, label="dataset 2")

    ds1_last = tp1[-1][1]
    interval = _interval_hours([hours for _, hours in tp1])
    target_first = ds1_last + interval
    ds2_first = tp2[0][1]
    return target_first - ds2_first, ds1_last, interval


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
    """Every channel-scoped-role discriminator token either dataset declares.

    Used to disambiguate a channel-scoped role suffix (``_tophat_gfp``,
    ``_smfish_nir``) when rewriting a zip member's timepoint field — the role
    catalog can't strip that suffix without knowing which tokens are valid
    channels for *this* dataset (see :func:`rewrite_member_name`).
    """
    tokens: list[str] = []
    for info in (info1, info2):
        for key in ("fluor_tokens", "smfish_tokens"):
            for tok in info.get(key, []) or []:
                tok = str(tok).strip().lower()
                if tok and tok not in tokens:
                    tokens.append(tok)
        phase = str(info.get("phase_token", "") or "").strip().lower()
        if phase and phase not in tokens:
            tokens.append(phase)
    return tuple(tokens)


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
    :func:`datasets._common.member_names.rewrite_member_field` — see that
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
    ds1_interval_hours: float
    wells_ds1_only: tuple[str, ...]
    wells_ds2_only: tuple[str, ...]
    wells_both: tuple[str, ...]
    merged_info: dict                   # full merged pipeline_info.json content
    dest_is_restart: bool = False       # dest has leftovers from an earlier, unfinished attempt
    format_warning: str | None = None
    column_warnings: tuple[str, ...] = ()


def build_merge_plan(ds1_root: Path | str, ds2_root: Path | str, dest_root: Path | str) -> MergePlan:
    """Validate two datasets and a destination, and produce a :class:`MergePlan`.

    Raises :class:`ConcatError` (user-facing message) on any incompatibility.
    Reads only — the destination is not created or cleared here.
    """
    ds1 = resolve_dataset(ds1_root, label="dataset 1")
    ds2 = resolve_dataset(ds2_root, label="dataset 2")
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

    info1 = read_info(ds1, label="dataset 1")
    info2 = read_info(ds2, label="dataset 2")
    check_compatible(info1, info2)

    tp_index = int(info1.get("tp_index", -1))
    if tp_index < 0:
        raise ConcatError(
            "pipeline_info.json has no valid tp_index, so the timepoint field in "
            "filenames can't be located — concatenation can't relabel dataset 2."
        )

    if has_folder_mode_output(ds1) or has_folder_mode_output(ds2):
        which = "dataset 1" if has_folder_mode_output(ds1) else "dataset 2"
        raise ConcatError(
            f"{which} stores its QC images as per-well folders (out/<WELL>/) "
            "instead of <WELL>_out.zip archives — Concatenate only supports "
            "the zipped output layout. Re-run the pipeline with compressed "
            "output, or zip the folders manually, then try again."
        )

    offset, ds1_last, interval = compute_offset(info1, info2)
    tp_format, format_warning = detect_timepoint_format(
        [str(t) for t in info1.get("available_timepoints", []) or []]
    )
    merged_info = merge_info(info1, info2, offset_hours=offset, tp_format=tp_format)

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
        wells_ds1_only=tuple(sorted(wells1 - wells2)),
        wells_ds2_only=tuple(sorted(wells2 - wells1)),
        wells_both=wells_both,
        merged_info=merged_info,
        dest_is_restart=dest_is_restart,
        format_warning=format_warning,
        column_warnings=column_warnings(ds1, ds2, wells_both),
    )
