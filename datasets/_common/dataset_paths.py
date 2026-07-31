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
import zipfile
from collections.abc import Callable
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


def out_folders(paths: DatasetPaths) -> dict[str, Path]:
    """Map each well to its ``out/<WELL>/`` folder-mode QC-image folder
    (``compressed_output=False`` — see :func:`has_folder_mode_output`).
    Disjoint from :func:`out_zips` in practice (a dataset is written either
    zipped or folder-mode, never both), but a caller that needs "done" image
    evidence regardless of which mode produced it should check both."""
    folders: dict[str, Path] = {}
    with swallow("scan out/ for folder-mode wells"):
        for entry in paths.out_dir.iterdir():
            well = canonical_well_label(entry.name) if entry.is_dir() else None
            if well:
                folders[well] = entry
    return folders


def dataset_wells(paths: DatasetPaths) -> set[str]:
    """Every 96-well position present in a dataset's ``in`` or ``out``."""
    return (
        set(in_containers(paths)) | set(out_csvs(paths))
        | set(out_zips(paths)) | set(out_folders(paths))
    )


def preview_wells(root: Path | str) -> list[str]:
    """Best-effort ``dataset_wells`` for a GUI status preview — sorted, and
    ``[]`` for anything that isn't (yet) a real dataset folder, rather than
    raising. Deliberately not :func:`resolve_dataset`: a page calls this on
    every keystroke/field change while the user is still typing a path, and
    "not a valid path yet" is the normal, expected state through most of
    that, not an error to report."""
    root = Path(root)
    if not root.is_dir():
        return []
    paths = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    with swallow("preview dataset wells"):
        return sorted(dataset_wells(paths))
    return []


def shorten_headline(text: str, *full_paths: str) -> str:
    """Replace every occurrence of each of *full_paths* in *text* with its
    basename.

    A ``execute_plan``/``apply`` success summary is built by formatting a
    full destination (or source) path into a sentence — right for the log
    line, unreadable as a dashboard headline, where it pushed the actual
    "N well(s)" result off to the side or off-screen. The caller keeps the
    original *text* for the log and passes it again as the dashboard's
    tooltip, so nothing is actually lost — only the headline itself is
    shortened. Skips any path that is empty (the in-place Import Loose
    case, whose ``dest_root`` is ``""``) — ``"".replace`` would otherwise
    insert its basename between every character of *text*.
    """
    shortened = text
    for full_path in full_paths:
        if full_path:
            shortened = shortened.replace(full_path, Path(full_path).name)
    return shortened


# ── input-side availability scan ─────────────────────────────────────────────
# Mirrors process_microscopy._collect_image_stems / _collect_available_schema_
# values, generalised so a Datasets feature can ask "what timepoints/FOVs does
# the raw input actually cover" independent of what pipeline_info.json claims
# — the question Validate needs to answer, and the pipeline's own answer at
# write_pipeline_info time, so a rebuilt sidecar can't disagree with a fresh
# run's. Deliberately not imported from process_microscopy itself: that would
# drag the whole pipeline import graph into a Datasets compute module.
def collect_input_image_stems(in_dir: Path) -> set[str]:
    """Every image filename stem findable under *in_dir* — loose top-level
    TIFs, one level of well subfolders, or well zips' TIF members."""
    stems: set[str] = set()
    for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
        for p in in_dir.glob(pat):
            stems.add(p.stem)
    with swallow(f"list well subfolders under {in_dir}"):
        for entry in in_dir.iterdir():
            if not entry.is_dir():
                continue
            for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
                for p in entry.glob(pat):
                    stems.add(p.stem)
    for zip_path in in_dir.glob("*.zip"):
        with swallow(f"scan {zip_path} for image stems"):
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    name = Path(member).name
                    if name.lower().endswith((".tif", ".tiff")):
                        stems.add(Path(name).stem)
    return stems


def collect_available_schema_values(
    in_dir: Path,
    *,
    schema_fields,
    sep: str,
    field_name: str,
    sort_key: Callable,
) -> list[str]:
    """Every distinct value of *field_name* found across ``in_dir``'s images,
    sorted by *sort_key*. Returns ``[]`` if *field_name* isn't in the schema."""
    fields = [str(f).strip().lower() for f in schema_fields]
    if field_name not in fields:
        return []
    field_idx = fields.index(field_name)
    values: set[str] = set()
    for stem in collect_input_image_stems(in_dir):
        parts = stem.split(sep)
        if 0 <= field_idx < len(parts):
            tok = str(parts[field_idx]).strip()
            if tok:
                values.add(tok)
    return sorted(values, key=sort_key)
