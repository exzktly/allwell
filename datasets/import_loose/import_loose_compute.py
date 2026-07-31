"""Pure preview scanner for the Import Loose Folder feature.

Mirrors ``WellPlateZipper._extract_well_from_filename``'s rule exactly —
schema-position lookup, ``canonical_well_label``, and ``os.path.isfile`` to
drop dangling symlinks the way ``os.walk`` lists them — so this preview and
the executor (which shells out to ``WellPlateZipper.py`` for the in-place
mode, or writes zips directly for the new-destination mode) can never
disagree about what counts as a match. It deliberately does **not** call
``WellPlateZipper.group_by_well`` directly: that function ``print()``s
unconditionally (a scan banner, a per-5000-files progress line, a completion
line) to give the Analyze log something to show during a long directory
walk, and calling it in-process here would pollute this app's own captured
output stream with lines meant for a subprocess's stdout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from util.schema import parse_schema
from util.well_token import canonical_well_label

_IMAGE_SUFFIXES = (".tif", ".tiff")


def _well_index(schema: list[str]) -> int | None:
    try:
        return schema.index("well")
    except ValueError:
        return None


def _field_index(schema: list[str], field_name: str) -> int | None:
    try:
        return schema.index(field_name)
    except ValueError:
        return None


@dataclass(frozen=True)
class ImportPreview:
    """What a loose-TIF folder will convert into, without writing anything."""

    wells: tuple[str, ...] = ()
    fovs: tuple[str, ...] = ()
    timepoints: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    examined: int = 0
    matched: int = 0
    unreadable_count: int = 0
    unparsed_files: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        if not self.matched:
            return "No files matched the schema."
        return (
            f"{len(self.wells)} well(s) × {len(self.fovs) or 1} FOV(s) × "
            f"{len(self.timepoints)} timepoint(s) · channels: "
            f"{', '.join(self.channels) or '(none)'}"
        )


@dataclass(frozen=True)
class _WalkResult:
    by_well: dict           # well -> list[Path], matched + readable
    fovs: set
    timepoints: set
    channels: set
    examined: int
    unreadable_count: int
    unparsed_files: list


def _walk(
    search_dir: Path, *, schema: list[str], sep: str, recursive: bool,
) -> _WalkResult:
    """The one directory walk both :func:`scan_folder` and
    :func:`group_files_by_well` build on, so the preview and the executor
    can never disagree about what matched."""
    well_idx = _well_index(schema)
    fov_idx = _field_index(schema, "fov")
    tp_idx = _field_index(schema, "timepoint")
    channel_idx = _field_index(schema, "channel")

    by_well: dict[str, list[Path]] = {}
    fovs: set[str] = set()
    timepoints: set[str] = set()
    channels: set[str] = set()
    examined = 0
    unreadable = 0
    unparsed: list[str] = []

    for root, _dirs, files in os.walk(search_dir):
        for fname in files:
            if not fname.lower().endswith(_IMAGE_SUFFIXES):
                continue
            examined += 1
            stem = os.path.splitext(fname)[0]
            parts = stem.split(sep)
            well = None
            if well_idx is not None and well_idx < len(parts):
                well = canonical_well_label(parts[well_idx])
            if well is None:
                unparsed.append(os.path.join(root, fname))
                continue
            full = os.path.join(root, fname)
            if not os.path.isfile(full):
                unreadable += 1
                continue
            by_well.setdefault(well, []).append(Path(full))
            if fov_idx is not None and fov_idx < len(parts):
                fovs.add(parts[fov_idx])
            if tp_idx is not None and tp_idx < len(parts):
                timepoints.add(parts[tp_idx])
            if channel_idx is not None and channel_idx < len(parts):
                channels.add(parts[channel_idx])
        if not recursive:
            break

    return _WalkResult(
        by_well=by_well, fovs=fovs, timepoints=timepoints, channels=channels,
        examined=examined, unreadable_count=unreadable, unparsed_files=unparsed,
    )


def scan_folder(
    search_dir: Path | str, *, filename_schema: str, filename_sep: str,
    recursive: bool = False,
) -> ImportPreview:
    """Scan *search_dir* for loose TIFs and report how they'd split into
    wells/FOVs/timepoints/channels, without writing anything.

    Non-recursive by default, matching ``WellPlateZipper``'s own default (a
    single ``os.walk`` level) — the in-place mode this preview describes only
    ever groups the folder it's pointed at, not its subfolders.
    """
    schema = parse_schema(filename_schema)
    result = _walk(Path(search_dir), schema=schema, sep=filename_sep, recursive=recursive)
    matched = sum(len(v) for v in result.by_well.values())
    return ImportPreview(
        wells=tuple(sorted(result.by_well)),
        fovs=tuple(sorted(result.fovs)),
        timepoints=tuple(sorted(result.timepoints)),
        channels=tuple(sorted(result.channels)),
        examined=result.examined,
        matched=matched,
        unreadable_count=result.unreadable_count,
        unparsed_files=tuple(result.unparsed_files),
    )


def group_files_by_well(
    search_dir: Path | str, *, filename_schema: str, filename_sep: str,
    recursive: bool = False,
) -> dict[str, list[Path]]:
    """Every matched file, grouped by canonical well — the input the
    new-destination executor writes per-well zips from."""
    schema = parse_schema(filename_schema)
    result = _walk(Path(search_dir), schema=schema, sep=filename_sep, recursive=recursive)
    return result.by_well
