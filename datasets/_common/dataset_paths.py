"""Shared dataset-folder helpers for the Datasets-pane features.

Lifted verbatim from ``datasets.concatenate.concatenate_compute`` (the first
feature to need them) so every Datasets utility that reads an ``in``/``out``
dataset folder — Concatenate, Validate, Subset, Import loose, Merge plates —
shares one implementation instead of five drifting copies.

Qt-free and ``well_viewer``-free: only ``util.aw_safe`` and
``util.well_token``, so this module can be imported from ``services/`` without
pulling Qt or the Review pane in.

The destination-state predicates (``destination_has_marker`` and the two that
depend on it) take the in-progress marker filename as a parameter, because
each feature owns its own marker (``.aw_concat_in_progress``,
``.aw_subset_in_progress``, ``.aw_import_in_progress``, ``.aw_merge_in_progress``)
— a marker from one feature must never be mistaken for another's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from util.aw_safe import swallow
from util.well_token import canonical_well_label


class DatasetPathsError(Exception):
    """A dataset folder or destination can't be resolved. The message is
    user-facing (shown in a feature's status window)."""


@dataclass(frozen=True)
class DatasetPaths:
    """A dataset root and its ``in``/``out`` children."""

    root: Path
    in_dir: Path
    out_dir: Path


# A trailing 96-well token in a filename stem, e.g. "measurements_B10" -> B10.
_TRAILING_WELL_RE = re.compile(r"([A-Ha-h])(\d{1,2})$")


def well_from_stem(stem: str) -> str | None:
    """Canonical well from a trailing token, e.g. ``measurements_a1`` -> A01."""
    match = _TRAILING_WELL_RE.search(stem)
    if not match:
        return None
    return f"{match.group(1).upper()}{int(match.group(2)):02d}"


# ── dataset resolution + sidecar IO ──────────────────────────────────────────
def resolve_dataset(root: Path | str, *, label: str) -> DatasetPaths:
    """Resolve *root* to a :class:`DatasetPaths`, or raise
    :class:`DatasetPathsError`.

    A dataset is a folder holding both an ``in`` and an ``out`` subfolder — the
    same layout Review detects in ``load_controller.load_path``.
    """
    root = Path(root)
    in_dir = root / "in"
    out_dir = root / "out"
    if not in_dir.is_dir() or not out_dir.is_dir():
        raise DatasetPathsError(
            f"{label} ({root}) is not a dataset — it needs both an 'in' and an "
            "'out' folder."
        )
    return DatasetPaths(root=root, in_dir=in_dir, out_dir=out_dir)


def read_info(paths: DatasetPaths, *, label: str) -> dict:
    """Read a dataset's ``out/pipeline_info.json`` as a raw dict."""
    info_path = paths.out_dir / "pipeline_info.json"
    if not info_path.exists():
        raise DatasetPathsError(f"{label}: no pipeline_info.json in its 'out' folder.")
    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetPathsError(f"{label}: could not read pipeline_info.json ({exc}).") from exc
    if not isinstance(data, dict):
        raise DatasetPathsError(f"{label}: pipeline_info.json is not a JSON object.")
    return data


def has_folder_mode_output(paths: DatasetPaths) -> bool:
    """True if *paths* holds per-well QC output as ``out/<WELL>/`` folders
    (``compressed_output=False``) rather than ``<WELL>_out.zip`` archives —
    a layout most Datasets utilities don't merge/copy member-by-member."""
    with swallow("scan out/ for folder-mode wells"):
        for entry in paths.out_dir.iterdir():
            if entry.is_dir() and canonical_well_label(entry.name):
                return True
    return False


# ── destination state (crash/restart detection) ──────────────────────────────
def destination_has_content(dest: DatasetPaths) -> bool:
    """True if *dest*'s ``in`` or ``out`` folder exists and holds any file."""

    def _has(folder: Path) -> bool:
        return folder.is_dir() and any(folder.iterdir())

    return _has(dest.in_dir) or _has(dest.out_dir)


def destination_is_complete_dataset(dest: DatasetPaths) -> bool:
    """True if *dest* already holds a finished dataset.

    Every feature's ``apply.execute_plan`` writes ``out/pipeline_info.json``
    last, only after every well has been copied and verified — so its
    presence means either a prior run into this folder completed, or the
    folder is an unrelated, already-analyzed dataset. Either way it must not
    be silently overwritten.
    """
    return (dest.out_dir / "pipeline_info.json").exists()


def destination_has_marker(dest: DatasetPaths, marker_name: str) -> bool:
    """True if a prior run into *dest* started and never finished.

    A feature's ``apply.execute_plan`` writes its own marker (*marker_name*)
    as its first action and removes it as its last (after the merged sidecar
    is written) — this is the **only** signal that leftover content under
    ``in``/``out`` came from an interrupted attempt. Content without the
    marker is never assumed to be a failed run (it could be a zipped-but-
    unanalyzed plate) and is refused instead — see
    :func:`destination_has_unrecognized_content`.
    """
    return (dest.root / marker_name).exists()


def destination_is_failed_run(dest: DatasetPaths, marker_name: str) -> bool:
    """True if *dest* holds leftovers from a run that never finished — safe
    to clear and rebuild. See :func:`destination_has_marker`."""
    return destination_has_marker(dest, marker_name) and not destination_is_complete_dataset(dest)


def destination_has_unrecognized_content(dest: DatasetPaths, marker_name: str) -> bool:
    """True if *dest* has files under ``in``/``out`` that are neither a
    finished dataset nor a marked in-progress attempt — e.g. a plate that's
    been zipped but not yet analyzed. Never auto-cleared; the destination
    field requires an empty folder."""
    return (
        destination_has_content(dest)
        and not destination_has_marker(dest, marker_name)
        and not destination_is_complete_dataset(dest)
    )


# ── well enumeration ─────────────────────────────────────────────────────────
def in_containers(paths: DatasetPaths) -> dict[str, Path]:
    """Map each well to its raw-input container (``<WELL>.zip`` or ``<WELL>/``).

    Keys are canonical wells (``A01``); a zip wins over a same-well folder. This
    is what lets a caller find a container from a canonical well even when the
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
    """Map each well to its ``<prefix>_<WELL>.csv`` measurements file.

    A duplicate well key (two CSVs from two different ``--csv_prefix`` runs
    both ending in the same well token) is resolved by plain dict overwrite —
    last one globbed wins, in filesystem glob order. Callers that need to
    notice a mixed-prefix ``out/`` folder (Validate does) must glob
    ``out/*.csv`` themselves rather than trust this map for that purpose.
    """
    csvs: dict[str, Path] = {}
    for csv_path in paths.out_dir.glob("*.csv"):
        well = well_from_stem(csv_path.stem)
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
