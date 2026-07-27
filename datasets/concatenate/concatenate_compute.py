"""Pure planning for the Concatenate Datasets feature.

Qt-free and side-effect-light (it reads two ``pipeline_info.json`` files and
globs the two dataset folders to enumerate wells, but writes nothing). The
whole timepoint-remap decision lives here so it can be unit-tested on tiny
``tmp_path`` fixtures without building the app.

The one product rule this module encodes: to make dataset 2 *continue after*
dataset 1, dataset 2's first timepoint is relabelled to
``ds1_last + ds1_interval`` and every other dataset-2 timepoint keeps its
relative spacing (a single additive hour offset). ``ds1_interval`` is the median
gap between dataset 1's sorted, unique timepoints — robust to one irregular gap,
and undefined (an error) when dataset 1 has a single timepoint.

Timepoints reach us as hour-valued labels (``"0"``, ``"24h"``, ``"2d"``) and are
read through :func:`util.timepoint.parse_hours`; a label that can't be read as a
time is refused rather than guessed at, because the rest of the app keys off the
parsed hours. New dataset-2 labels are rendered ``f"{hours:g}h"``, which
round-trips back through ``parse_hours`` and stays chronologically after every
dataset-1 label.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from util.aw_safe import swallow
from util.timepoint import parse_hours, sort_key
from util.well_token import canonical_well_label

# A trailing 96-well token in a filename stem, e.g. "measurements_B10" -> B10.
_TRAILING_WELL_RE = re.compile(r"([A-Ha-h])(\d{1,2})$")

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


@dataclass(frozen=True)
class DatasetPaths:
    """A dataset root and its ``in``/``out`` children."""

    root: Path
    in_dir: Path
    out_dir: Path


@dataclass(frozen=True)
class MergePlan:
    """Everything the worker needs to execute a concatenation.

    The worker walks ``ds1``/``ds2`` on disk itself; this plan carries the
    decisions that are awkward to recompute mid-copy — the timepoint relabelling,
    the well partition, and the merged sidecar — plus the numbers the status
    window reports.
    """

    ds1: DatasetPaths
    ds2: DatasetPaths
    dest: DatasetPaths
    tp_label_map: dict[str, str]        # old dataset-2 label -> new label
    offset_hours: float                 # added to every dataset-2 timepoint
    ds1_last_hours: float
    ds1_interval_hours: float
    sep: str                            # filename field separator
    tp_index: int                       # position of the timepoint field
    wells_ds1_only: tuple[str, ...]
    wells_ds2_only: tuple[str, ...]
    wells_both: tuple[str, ...]
    merged_info: dict                   # full merged pipeline_info.json content
    dest_is_restart: bool = False       # dest has leftovers from an earlier, unfinished attempt


# ── dataset resolution + sidecar IO ──────────────────────────────────────────
def resolve_dataset(root: Path | str, *, label: str) -> DatasetPaths:
    """Resolve *root* to a :class:`DatasetPaths`, or raise :class:`ConcatError`.

    A dataset is a folder holding both an ``in`` and an ``out`` subfolder — the
    same layout Review detects in ``load_controller.load_path``.
    """
    root = Path(root)
    in_dir = root / "in"
    out_dir = root / "out"
    if not in_dir.is_dir() or not out_dir.is_dir():
        raise ConcatError(
            f"{label} ({root}) is not a dataset — it needs both an 'in' and an "
            "'out' folder."
        )
    return DatasetPaths(root=root, in_dir=in_dir, out_dir=out_dir)


def read_info(paths: DatasetPaths, *, label: str) -> dict:
    """Read a dataset's ``out/pipeline_info.json`` as a raw dict."""
    info_path = paths.out_dir / "pipeline_info.json"
    if not info_path.exists():
        raise ConcatError(f"{label}: no pipeline_info.json in its 'out' folder.")
    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConcatError(f"{label}: could not read pipeline_info.json ({exc}).") from exc
    if not isinstance(data, dict):
        raise ConcatError(f"{label}: pipeline_info.json is not a JSON object.")
    return data


# ── destination state (crash/restart detection) ──────────────────────────────
def destination_has_content(dest: DatasetPaths) -> bool:
    """True if *dest*'s ``in`` or ``out`` folder exists and holds any file."""

    def _has(folder: Path) -> bool:
        return folder.is_dir() and any(folder.iterdir())

    return _has(dest.in_dir) or _has(dest.out_dir)


def destination_is_complete_dataset(dest: DatasetPaths) -> bool:
    """True if *dest* already holds a finished dataset.

    ``apply.execute_plan`` writes ``out/pipeline_info.json`` last, only after
    every well has been copied — so its presence means either a prior
    concatenation into this folder ran to completion, or the folder is an
    unrelated, already-analyzed dataset. Either way it must not be silently
    overwritten.
    """
    return (dest.out_dir / "pipeline_info.json").exists()


def destination_is_failed_run(dest: DatasetPaths) -> bool:
    """True if *dest* holds leftovers from an attempt that never finished.

    The marker is the same one :func:`destination_is_complete_dataset` checks
    for the opposite case: content under ``in``/``out`` *without*
    ``out/pipeline_info.json`` can only come from a concatenation that started
    writing into this folder and was interrupted (crash, kill, disk full)
    before reaching its last step — a finished run always leaves that file
    behind. That makes it safe to treat as a resumable restart rather than an
    existing dataset: :func:`~datasets.concatenate.apply.execute_plan` clears
    it and rebuilds every well fresh from the two source datasets, so no
    partial or orphaned file from the earlier attempt survives.
    """
    return destination_has_content(dest) and not destination_is_complete_dataset(dest)


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
        if hours is None:
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


def _format_hours(hours: float) -> str:
    """Render an hour value as a timepoint label that round-trips."""
    return f"{hours:g}h"


def build_timepoint_map(info1: dict, info2: dict) -> tuple[dict[str, str], float, float, float]:
    """Compute dataset 2's ``{old_label: new_label}`` remap.

    Returns ``(mapping, offset_hours, ds1_last_hours, ds1_interval_hours)``.
    """
    tp1 = _timepoint_hours(info1, label="dataset 1")
    tp2 = _timepoint_hours(info2, label="dataset 2")

    ds1_last = tp1[-1][1]
    interval = _interval_hours([hours for _, hours in tp1])
    target_first = ds1_last + interval
    ds2_first = tp2[0][1]
    offset = target_first - ds2_first

    mapping = {label: _format_hours(hours + offset) for label, hours in tp2}
    return mapping, offset, ds1_last, interval


# ── well enumeration ─────────────────────────────────────────────────────────
def _well_from_stem(stem: str) -> str | None:
    """Canonical well from a trailing token, e.g. ``measurements_a1`` -> A01."""
    match = _TRAILING_WELL_RE.search(stem)
    if not match:
        return None
    return f"{match.group(1).upper()}{int(match.group(2)):02d}"


def in_containers(paths: DatasetPaths) -> dict[str, Path]:
    """Map each well to its raw-input container (``<WELL>.zip`` or ``<WELL>/``).

    Keys are canonical wells (``A01``); a zip wins over a same-well folder. This
    is what lets the worker find a container from a canonical well even when the
    file on disk is unpadded (``A1.zip``).
    """
    containers: dict[str, Path] = {}
    for zip_path in paths.in_dir.glob("*.zip"):
        well = canonical_well_label(zip_path.stem)
        if well:
            containers[well] = zip_path
    with swallow("enumerate in/ well folders"):
        for entry in paths.in_dir.iterdir():
            if entry.is_dir():
                well = canonical_well_label(entry.name)
                if well:
                    containers.setdefault(well, entry)
    return containers


def out_csvs(paths: DatasetPaths) -> dict[str, Path]:
    """Map each well to its ``<prefix>_<WELL>.csv`` measurements file."""
    csvs: dict[str, Path] = {}
    for csv_path in paths.out_dir.glob("*.csv"):
        well = _well_from_stem(csv_path.stem)
        if well:
            csvs[well] = csv_path
    return csvs


def out_zips(paths: DatasetPaths) -> dict[str, Path]:
    """Map each well to its ``<WELL>_out.zip`` QC-image archive."""
    zips: dict[str, Path] = {}
    for zip_path in paths.out_dir.glob("*_out.zip"):
        stem = zip_path.stem
        well = canonical_well_label(stem[:-4]) if stem.endswith("_out") else None
        if well:
            zips[well] = zip_path
    return zips


def dataset_wells(paths: DatasetPaths) -> set[str]:
    """Every 96-well position present in a dataset's ``in`` or ``out``."""
    return set(in_containers(paths)) | set(out_csvs(paths)) | set(out_zips(paths))


# ── sidecar merge ────────────────────────────────────────────────────────────
def _ordered_union(first, second) -> list:
    """Concatenate two sequences, dropping later duplicates, preserving order."""
    merged = list(first)
    for item in second:
        if item not in merged:
            merged.append(item)
    return merged


def merge_info(info1: dict, info2: dict, tp_map: dict[str, str]) -> dict:
    """Build the merged sidecar.

    Structural fields and the user-side blocks (``cell_gating``,
    ``sample_definitions``, ``ratios``, ``notes``) come from dataset 1 — the
    wells and channels are shared, so they transfer unchanged. Timepoints are
    dataset 1's followed by dataset 2's remapped labels; the token lists are
    unioned.
    """
    merged = dict(info1)

    ds1_tps = [str(t).strip() for t in info1.get("available_timepoints", []) or [] if str(t).strip()]
    ds2_tps = [tp_map[str(t).strip()] for t in info2.get("available_timepoints", []) or [] if str(t).strip()]
    merged["available_timepoints"] = sorted(_ordered_union(ds1_tps, ds2_tps), key=sort_key)

    merged["available_fovs"] = _ordered_union(
        info1.get("available_fovs", []) or [], info2.get("available_fovs", []) or []
    )
    for key in _UNION_LIST_KEYS:
        merged[key] = _ordered_union(info1.get(key, []) or [], info2.get(key, []) or [])
    return merged


# ── top-level plan ───────────────────────────────────────────────────────────
def build_merge_plan(ds1_root: Path | str, ds2_root: Path | str, dest_root: Path | str) -> MergePlan:
    """Validate two datasets and a destination, and produce a :class:`MergePlan`.

    Raises :class:`ConcatError` (user-facing message) on any incompatibility.
    Reads only — the destination is not created here.
    """
    ds1 = resolve_dataset(ds1_root, label="dataset 1")
    ds2 = resolve_dataset(ds2_root, label="dataset 2")
    dest_root = Path(dest_root)
    dest = DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")

    if dest.root in (ds1.root, ds2.root):
        raise ConcatError("The destination must be a different folder from both datasets.")

    if destination_is_complete_dataset(dest):
        raise ConcatError(
            f"The destination ({dest.root}) already holds a finished dataset "
            "(out/pipeline_info.json exists there) — concatenating into it "
            "would overwrite real data. Choose a new, empty folder."
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

    tp_map, offset, ds1_last, interval = build_timepoint_map(info1, info2)
    merged_info = merge_info(info1, info2, tp_map)

    wells1 = dataset_wells(ds1)
    wells2 = dataset_wells(ds2)
    if not wells1 and not wells2:
        raise ConcatError("Neither dataset has any wells to concatenate.")

    return MergePlan(
        ds1=ds1,
        ds2=ds2,
        dest=dest,
        tp_label_map=tp_map,
        offset_hours=offset,
        ds1_last_hours=ds1_last,
        ds1_interval_hours=interval,
        sep=str(info1.get("separator", "_")),
        tp_index=tp_index,
        wells_ds1_only=tuple(sorted(wells1 - wells2)),
        wells_ds2_only=tuple(sorted(wells2 - wells1)),
        wells_both=tuple(sorted(wells1 & wells2)),
        merged_info=merged_info,
        dest_is_restart=dest_is_restart,
    )


# ── filename timepoint rewrite (used by the worker) ──────────────────────────
def rewrite_timepoint_in_name(name: str, sep: str, tp_index: int, tp_map: dict[str, str]) -> str:
    """Return *name* with its timepoint field remapped through *tp_map*.

    Splits the stem on *sep*, replaces the token at *tp_index* if it is in the
    map, and rejoins — leaving the extension untouched. A name whose timepoint
    field isn't in the map (or that has too few fields) is returned unchanged, so
    non-schema members of a QC zip pass through.
    """
    path = Path(name)
    suffix = "".join(path.suffixes)
    stem = path.name[: len(path.name) - len(suffix)] if suffix else path.name
    parts = stem.split(sep)
    if 0 <= tp_index < len(parts):
        parts[tp_index] = tp_map.get(parts[tp_index], parts[tp_index])
    return sep.join(parts) + suffix
