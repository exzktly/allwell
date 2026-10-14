"""Versioned acquisition geometry and clock metadata, without pixel imports.

The dataset's record lives at ``in/acquisition.json``. It records the exact
filename field/timepoint tokens, never inferred cadence or missing positions
as zero. The schema uses micrometres for pixel scale and stage positions,
pixels for tile size and mosaic positions, and percent for declared overlap.
Missing metadata is JSON null, including a missing individual scale axis.
"""

from __future__ import annotations

import json
import math
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from util.image_exts import is_run_input_member
from util.json_write import write_json_atomic
from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP, parse_schema, validate_schema
from util.well_token import canonical_well_label

FORMAT_VERSION = 1
RECORD_FILENAME = "acquisition.json"


class AcquisitionRecordError(ValueError):
    """A record cannot be interpreted without inventing or losing information."""


@dataclass(frozen=True)
class AcquisitionValidation:
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def record_path(dataset_dir: str | Path) -> Path:
    return Path(dataset_dir) / "in" / RECORD_FILENAME


def new_record(*, source_files=()) -> dict:
    """An empty, valid record; producers fill only metadata they actually read."""
    return {
        "format_version": FORMAT_VERSION,
        "source_files": list(source_files),
        "pixel_size_um": {"x": None, "y": None},
        "tile_size_px": {"x": None, "y": None},
        "objective": None,
        "declared_overlap_percent": {"x": None, "y": None},
        "timepoints": {},
        "tiles": [],
    }


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _axes(payload, name, predicate, issues):
    values = payload.get(name)
    if not isinstance(values, dict) or set(values) != {"x", "y"}:
        issues.append(f"{name} must contain x and y, with null for missing axes")
        return
    for axis, value in values.items():
        if value is not None and not predicate(value):
            issues.append(f"Invalid {name}.{axis}: {value!r}")


def _position(value):
    return value is None or (isinstance(value, (list, tuple)) and len(value) == 2
                             and all(_number(v) for v in value))


def _timepoints(payload, issues):
    timepoints = payload.get("timepoints")
    if not isinstance(timepoints, dict):
        issues.append("timepoints must be an object keyed by exact filename tokens")
        return {}
    for token, metadata in timepoints.items():
        if not _text(token) or not isinstance(metadata, dict) or "started_at" not in metadata:
            issues.append(f"Invalid timepoint {token!r}: expected started_at or null")
            continue
        start = metadata["started_at"]
        if start is None:
            continue
        try:
            if not isinstance(start, str) or "T" not in start:
                raise ValueError("An ISO 8601 datetime is required")
            datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            issues.append(f"Invalid absolute acquisition start for {token!r}: {start!r}")
    return timepoints


def _tiles(payload, sources, timepoints, issues):
    tiles = payload.get("tiles")
    if not isinstance(tiles, list):
        issues.append("tiles must be a list of well/field/timepoint positions")
        return
    seen = set()
    used_times = set()
    required = {"well", "field", "timepoint", "mosaic_xy_px", "stage_xy_um"}
    for index, tile in enumerate(tiles):
        if not isinstance(tile, dict) or not required <= set(tile):
            issues.append(f"Tile {index} is missing its identity or nullable positions")
            continue
        well, field, token = (tile[key] for key in ("well", "field", "timepoint"))
        if not all(_text(v) for v in (well, field, token)):
            issues.append(f"Tile {index} requires nonempty well/field/timepoint tokens")
            continue
        if canonical_well_label(well) != well:
            issues.append(f"Tile {index} well {well!r} must be a canonical plate label")
        key = well, field, token
        if key in seen:
            issues.append(f"Duplicate tile identity {key!r}")
        seen.add(key)
        used_times.add(token)
        if token not in timepoints:
            issues.append(f"Tile {key!r} names an undeclared timepoint")
        for position in ("mosaic_xy_px", "stage_xy_um"):
            if not _position(tile[position]):
                issues.append(f"Tile {key!r} has an invalid {position}")
        source = tile.get("source_file")
        if source is not None and (not _text(source) or source not in sources):
            issues.append(f"Tile {key!r} names an undeclared source_file")
    for token in set(timepoints) - used_times:
        issues.append(f"Timepoint {token!r} has no recorded tiles")


def validate_record(payload) -> AcquisitionValidation:
    """Validate schema and units; unsupported versions are not read as version 1.

    ISO timestamps retain their offset or lack of one. A missing timezone is
    not converted into UTC; consumers must establish clock comparability.
    Additional provenance keys survive round trips without being discarded.
    """
    issues = []
    if not isinstance(payload, dict):
        return AcquisitionValidation(("The acquisition record must be a JSON object",))
    version = payload.get("format_version")
    if type(version) is not int or version != FORMAT_VERSION:
        issues.append(f"Unsupported acquisition format_version {version!r}; expected {FORMAT_VERSION}")
    sources = payload.get("source_files")
    if not isinstance(sources, list) or not all(_text(v) for v in sources):
        issues.append("source_files must be a list of nonempty names")
        sources = []
    elif len(set(sources)) != len(sources):
        issues.append("source_files contains duplicate names")
    _axes(payload, "pixel_size_um", lambda v: _number(v) and v > 0, issues)
    _axes(payload, "tile_size_px", lambda v: type(v) is int and v > 0, issues)
    _axes(payload, "declared_overlap_percent", lambda v: _number(v) and 0 <= v <= 100, issues)
    if "objective" not in payload or (payload["objective"] is not None and not _text(payload["objective"])):
        issues.append("objective must be a nonempty name or null")
    timepoints = _timepoints(payload, issues)
    _tiles(payload, sources, timepoints, issues)
    return AcquisitionValidation(tuple(issues))


def _require_valid(payload):
    validation = validate_record(payload)
    if not validation.ok:
        raise AcquisitionRecordError("; ".join(validation.issues))


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AcquisitionRecordError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def read_record(dataset_dir: str | Path) -> dict | None:
    """Return absent as None; corrupt or unsupported metadata is an explicit error."""
    path = record_path(dataset_dir)
    try:
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream, object_pairs_hook=_json_object)
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as error:
        raise AcquisitionRecordError(f"Cannot read {path}: {error}") from error
    _require_valid(payload)
    return payload


def write_record(dataset_dir: str | Path, payload) -> None:
    """Validate before the atomic replacement; failures preserve the old record."""
    _require_valid(payload)
    write_json_atomic(record_path(dataset_dir), payload)


def _archive_tiles(path, well, fields, sep, issues) -> set[tuple[str, str, str]] | None:
    actual = set()
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not is_run_input_member(name):
                    continue
                tokens = Path(name).stem.split(sep)
                if len(tokens) != len(fields):
                    issues.append(f"{path.name}: image {name!r} does not match the filename schema")
                    continue
                identity = dict(zip(fields, tokens))
                if canonical_well_label(identity["well"]) != well:
                    issues.append(f"{path.name}: image {name!r} names a different well")
                    continue
                actual.add((well, identity["fov"], identity["timepoint"]))
    except (zipfile.BadZipFile, OSError) as error:
        issues.append(f"Cannot inspect archive {path.name}: {error}")
        return None
    return actual


def validate_against_archives(dataset_dir: str | Path, payload, *,
                              schema=DEFAULT_SCHEMA, sep=DEFAULT_SEP) -> AcquisitionValidation:
    """Compare full well/field/timepoint coverage using ZIP headers, never pixels.

    Channel planes share a tile identity. Opaque field/timepoint tokens retain
    exact spelling; metadata-only members and macOS sidecars are ignored using
    the pipeline's input-member predicate. Unreadable archives are reported and
    their field coverage is not fabricated as an empty set.
    """
    validation = validate_record(payload)
    if not validation.ok:
        return validation
    fields = parse_schema(schema)
    schema_errors = validate_schema(fields, required=("well", "fov", "timepoint"))
    if schema_errors or not _text(sep):
        return AcquisitionValidation(tuple(schema_errors or ["Filename separator must be nonempty"]))
    issues = []
    actual = set()
    archive_wells = set()
    unreadable = set()
    for path in sorted((Path(dataset_dir) / "in").glob("*.zip")):
        well = canonical_well_label(path.stem)
        if well is None:
            issues.append(f"Archive {path.name!r} does not name a plate well")
            continue
        if well in archive_wells:
            issues.append(f"Multiple archives name well {well}")
        archive_wells.add(well)
        sites = _archive_tiles(path, well, fields, sep, issues)
        if sites is None:
            unreadable.add(well)
        else:
            actual.update(sites)
    recorded = {(tile["well"], tile["field"], tile["timepoint"]) for tile in payload["tiles"]}
    recorded_wells = {site[0] for site in recorded}
    for well in sorted(recorded_wells - archive_wells):
        issues.append(f"Record names absent well archive {well}")
    for well in sorted(archive_wells - recorded_wells):
        issues.append(f"Record is missing well archive {well}")
    for key in sorted(recorded - actual):
        if key[0] not in unreadable:
            issues.append(f"Record names absent tile {key!r}")
    for key in sorted(actual - recorded):
        issues.append(f"Record is missing tile {key!r}")
    return AcquisitionValidation(tuple(issues))
