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
        "timepoints": {},
        "tiles": [],
    }


def new_timepoint(*, started_at=None) -> dict:
    """Calibration and one shared acquisition clock for a filename token."""
    return {
        "started_at": started_at,
        "start_reason": None,
        "tiles_without_time": 0,
        "source_timepoint_index": None,
        "pixel_size_um": {"x": None, "y": None},
        "tile_size_px": {"x": None, "y": None},
        "objective": None,
        "declared_overlap_percent": {"x": None, "y": None},
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
        local_issues = []
        _axes(metadata, "pixel_size_um", lambda v: _number(v) and v > 0, local_issues)
        _axes(metadata, "tile_size_px", lambda v: type(v) is int and v > 0, local_issues)
        _axes(metadata, "declared_overlap_percent", lambda v: _number(v) and 0 <= v <= 100, local_issues)
        if "objective" not in metadata or (metadata["objective"] is not None and not _text(metadata["objective"])):
            local_issues.append("objective must be a nonempty name or null")
        if "start_reason" not in metadata or (metadata["start_reason"] is not None and not _text(metadata["start_reason"])):
            local_issues.append("start_reason must be a nonempty reason or null")
        missing = metadata.get("tiles_without_time")
        if type(missing) is not int or missing < 0:
            local_issues.append("tiles_without_time must be a nonnegative integer")
        source_index = metadata.get("source_timepoint_index")
        if source_index is not None and (type(source_index) is not int or source_index < 0):
            local_issues.append("source_timepoint_index must be a nonnegative integer or null")
        issues.extend(f"Timepoint {token!r}: {issue}" for issue in local_issues)
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
        if "acquired_at" in tile or "started_at" in tile:
            issues.append(f"Tile {key!r} must use its timepoint's shared acquisition time")
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
    if any(key in payload for key in ("pixel_size_um", "tile_size_px", "objective", "declared_overlap_percent")):
        issues.append("Calibration belongs to each timepoint, not the dataset")
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
    return read_record_file(record_path(dataset_dir))


def publication_marker(path: str | Path) -> Path:
    return Path(path).with_name("acquisition.pending.json")


def parse_record_text(text: str, *, source="record") -> dict:
    """Parse and validate record text strictly (duplicate keys, malformed JSON and invalid
    content all raise :class:`AcquisitionRecordError`), without the publication-marker check.

    A resumed conversion reads the record its interrupted predecessor left, while the
    marker is still present, so it cannot go through :func:`read_record_file`; it must
    still be as strict as that is.
    """
    try:
        payload = json.loads(text, object_pairs_hook=_json_object)
    except ValueError as error:
        raise AcquisitionRecordError(f"Cannot read {source}: {error}") from error
    _require_valid(payload)
    return payload


def read_record_file(path: str | Path) -> dict | None:
    """Explicit-file API; incomplete publication is never read as absent."""
    path = Path(path)
    if publication_marker(path).exists():
        raise AcquisitionRecordError(f"Acquisition publication is incomplete in {path.parent}; resume the conversion")
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
    write_record_file(record_path(dataset_dir), payload)


def write_record_file(path: str | Path, payload) -> None:
    """Explicit-file writer; the publisher clears its guard only after this."""
    _require_valid(payload)
    write_json_atomic(Path(path), payload)


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
    return validate_record_paths((Path(dataset_dir) / "in").glob("*.zip"), payload,
                                 schema=schema, sep=sep)


def validate_record_paths(paths, payload, *, schema=DEFAULT_SCHEMA, sep=DEFAULT_SEP):
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
    for path in sorted(paths):
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


def filter_record_to_archives(dataset_dir, payload, *, schema=DEFAULT_SCHEMA, sep=DEFAULT_SEP):
    """Keep exact published identities; retain the acquisition's shared clock."""
    return filter_record_to_paths((Path(dataset_dir) / "in").glob("*.zip"), payload,
                                  schema=schema, sep=sep)


def filter_record_to_paths(paths, payload, *, schema=DEFAULT_SCHEMA, sep=DEFAULT_SEP):
    paths = tuple(paths)
    import copy
    fields = parse_schema(schema)
    actual = set()
    issues = []
    for path in sorted(paths):
        well = canonical_well_label(path.stem)
        sites = _archive_tiles(path, well, fields, sep, issues)
        if sites is not None:
            actual.update(sites)
    if issues:
        raise AcquisitionRecordError("; ".join(issues))
    result = copy.deepcopy(payload)
    result["tiles"] = [tile for tile in result["tiles"]
                       if (tile["well"], tile["field"], tile["timepoint"]) in actual]
    tokens = {tile["timepoint"] for tile in result["tiles"]}
    result["timepoints"] = {token: data for token, data in result["timepoints"].items()
                            if token in tokens}
    sources = {tile["source_file"] for tile in result["tiles"] if "source_file" in tile}
    result["source_files"] = [name for name in result["source_files"] if name in sources]
    if "source_file_stats" in result:
        result["source_file_stats"] = {name: data for name, data in result["source_file_stats"].items()
                                       if name in sources}
    validation = validate_record_paths(paths, result, schema=schema, sep=sep)
    if not validation.ok:
        raise AcquisitionRecordError("; ".join(validation.issues))
    return result


#: A timepoint's calibration: what the image files state about scale and tiling, as opposed to its clock.
_CALIBRATION_KEYS = frozenset({"pixel_size_um", "tile_size_px", "objective", "declared_overlap_percent"})


def merge_plate_records(first, second, well_map) -> dict | None:
    """Combine wells only when their shared timepoint metadata agrees."""
    import copy
    if first is None and second is None:
        return None
    if first is None or second is None:
        raise AcquisitionRecordError("Merge Plates requires both acquisition records or neither; one input has no record")
    for token in first["timepoints"].keys() & second["timepoints"].keys():
        left, right = first["timepoints"][token], second["timepoints"][token]
        differences = sorted(key for key in left.keys() | right.keys()
                             if key != "source_timepoint_index" and left.get(key) != right.get(key))
        if differences and set(differences) <= _CALIBRATION_KEYS:
            # Same clock, different calibration: one source stated a scale or tile size the other did not
            # (or another one). The merged record can describe only one per timepoint, and taking the known
            # value would claim it for tiles whose file never stated it — so refuse, saying what differs.
            detail = "; ".join(f"{key}: {left.get(key)} against {right.get(key)}" for key in differences)
            raise AcquisitionRecordError(
                f"Merge Plates: the two datasets' records disagree about the calibration of timepoint {token!r} "
                f"({detail}), and a merged record can state only one. An unknown value means a source file did "
                "not record it: convert both from files that state it, or merge datasets with matching "
                "calibration.")
        if differences:
            raise AcquisitionRecordError(
                f"Merge Plates: acquisition metadata conflicts at {token!r}: {', '.join(differences)}. "
                "Use Concatenate for successive acquisitions of the same wells.")
    result = new_record()
    result["source_records"] = [copy.deepcopy(first), copy.deepcopy(second)]
    for ordinal, payload in enumerate((first, second), 1):
        prefix = f"dataset{ordinal}/"
        result["source_files"].extend(prefix + name for name in payload["source_files"])
        for token, metadata in payload["timepoints"].items():
            if token not in result["timepoints"]:
                result["timepoints"][token] = copy.deepcopy(metadata)
            else:
                result["timepoints"][token]["source_timepoint_index"] = None
        for original in payload["tiles"]:
            tile = copy.deepcopy(original)
            if ordinal == 2:
                tile["well"] = well_map.get(tile["well"], tile["well"])
            if "source_file" in tile:
                tile["source_file"] = prefix + tile["source_file"]
            result["tiles"].append(tile)
    validation = validate_record(result)
    if not validation.ok:
        raise AcquisitionRecordError("; ".join(validation.issues))
    return result


def concatenate_records(first, second, *, relabel, offset_hours: float, gap_hours: float,
                        gap_source: str) -> dict | None:
    """The record of two acquisitions placed one after the other.

    Dataset 1's timepoints keep their tokens; dataset 2's are renamed by
    *relabel* (the same shift the images' filenames got), so a tile's
    identity matches the member it describes. Every timepoint keeps its own
    calibration and its own absolute start, and every tile keeps its own
    positions, because a plate put back on the stage does not sit where it
    did. The gap the user chose and where it came from are written beside
    them. Both records or neither: with one missing the merged timepoints
    could not all be described, so the caller says so and writes none.
    """
    import copy
    if first is None and second is None:
        return None
    if first is None or second is None:
        raise AcquisitionRecordError(
            "Concatenate carries acquisition records only when both datasets have one")
    result = new_record()
    result["concatenations"] = (list(first.get("concatenations", []))
                                + list(second.get("concatenations", []))
                                + [{"gap_hours": gap_hours, "gap_source": gap_source,
                                    "offset_hours": offset_hours}])
    for ordinal, payload in enumerate((first, second), 1):
        prefix = f"dataset{ordinal}/"
        result["source_files"].extend(prefix + name for name in payload["source_files"])
        for key in ("source_file_stats", "clock_source_file_stats"):
            if key in payload:
                result.setdefault(key, {}).update(
                    {prefix + name: copy.deepcopy(data) for name, data in payload[key].items()})
        rename = (lambda token: token) if ordinal == 1 else relabel
        for token, metadata in payload["timepoints"].items():
            new_token = rename(token)
            if new_token in result["timepoints"]:
                raise AcquisitionRecordError(
                    f"Timepoint {new_token!r} would exist twice after the merge")
            result["timepoints"][new_token] = copy.deepcopy(metadata)
        for original in payload["tiles"]:
            tile = copy.deepcopy(original)
            tile["timepoint"] = rename(tile["timepoint"])
            if "source_file" in tile:
                tile["source_file"] = prefix + tile["source_file"]
            result["tiles"].append(tile)
    validation = validate_record(result)
    if not validation.ok:
        raise AcquisitionRecordError("; ".join(validation.issues))
    return result
