"""What Subset knows about an in-only source: which part of each image name is which field.

An analysed dataset says how its filenames decompose in ``out/pipeline_info.json``. A raw one — a plate a
converter or Import Loose wrote, with only ``in/`` — has no sidecar, so the schema has to come from somewhere
else before Subset can cut by timepoint, FOV or channel. In the order tried:

1. **The acquisition record** (``in/acquisition.json``), which every packed-file converter writes. It names each
   tile's exact well, field and timepoint tokens, so a schema is accepted only when parsing every archive member
   under it reproduces exactly the record's tiles (``util.acquisition_record.validate_against_archives``). The
   converters name members ``experiment_channel_well_fN_TP.tif``, so that layout is tried first; a schema the
   user typed is tried instead when there is one, and one that contradicts the record is refused rather than
   preferred, because the record was written from the source file and the typed schema is a guess about it.
2. **A schema the user typed**, checked against every image member name: each must split into exactly as many
   tokens as the schema has fields.
3. **A confident inference** from the member names (``util.schema_infer``), the same one Concatenate uses.

With none of them a subset by **wells** still works — choosing whole archives needs no schema — and a filter on
any other axis is refused, naming the field it could not find. Refusing is the point: a schema guessed wrong
would keep the wrong images and report success.

Qt-free; read-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from datasets.core.dataset_paths import DatasetPaths, collect_input_image_stems, schema_values_from_stems
from util.acquisition_record import AcquisitionRecordError, read_record, validate_against_archives
from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP, parse_schema, validate_schema
from util.schema_infer import infer_from_dir
from util.timepoint import sort_key as tp_sort_key

#: Where the schema came from, as the page and the run log say it.
FROM_RECORD = "the acquisition record"
FROM_TYPED = "the schema you typed"
FROM_INFERRED = "the image filenames"

#: The axes a filter can cut a raw dataset by, beyond wells, and the schema field each needs.
AXIS_FIELDS = {"timepoint": "timepoint", "fov": "fov", "channel": "channel"}

#: How many of a validation's issues an error message quotes.
_ISSUES_SHOWN = 3


class RawSchemaError(Exception):
    """The source's schema cannot be established. The message is user-facing."""


@dataclass(frozen=True)
class RawSource:
    """An in-only dataset's schema and what its archives hold under it.

    *info* is sidecar-shaped — ``schema``, ``separator``, ``schema_fields``, the field indices, ``fluor_tokens``
    and the ``available_*`` lists — so the planner reads it exactly as it reads a real ``pipeline_info.json``.
    *origin* is one of the ``FROM_*`` phrases, or ``""`` when no schema could be established (wells only).
    *record* is the parsed acquisition record, or ``None`` when the dataset has none.
    """

    info: dict
    origin: str
    record: dict | None

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(self.info.get("schema_fields") or ())

    def describe(self) -> str:
        """One sentence for the page: where the schema came from, or why there is none."""
        if not self.origin:
            return ("No filename schema: this dataset has no acquisition record and its names don't "
                    "determine one, so only wells can be chosen. Type the schema to split it further.")
        return f"Filename schema {self.info['schema']} (separator {self.info['separator']!r}), from {self.origin}."


def _issues(validation) -> str:
    shown = "; ".join(validation.issues[:_ISSUES_SHOWN])
    more = len(validation.issues) - _ISSUES_SHOWN
    return shown + (f"; and {more} more" if more > 0 else "")


def _typed_fields(typed_schema: str, typed_sep: str) -> list[str]:
    fields = parse_schema(typed_schema)
    problems = validate_schema(fields, required=("well",))
    if not typed_sep:
        problems.append("The separator is empty.")
    if problems:
        raise RawSchemaError(f"The filename schema {typed_schema!r} can't be used: {' '.join(problems)}")
    return fields


def _info(fields: list[str], sep: str, stems: set[str]) -> dict:
    def index(name: str) -> int:
        return fields.index(name) if name in fields else -1

    def values(name: str, key=sorted) -> list[str]:
        return schema_values_from_stems(stems, schema_fields=fields, sep=sep, field_name=name, sort_key=key)

    channels = values("channel", key=str.lower)
    return {
        "schema": ":".join(fields),
        "separator": sep,
        "schema_fields": list(fields),
        "well_index": index("well"),
        "channel_index": index("channel"),
        "fov_index": index("fov"),
        "tp_index": index("timepoint"),
        "fluor_tokens": [c.lower() for c in channels],
        "available_timepoints": values("timepoint", key=tp_sort_key),
        "available_fovs": values("fov", key=lambda f: (len(f), f)),
        "available_channels": channels,
    }


def resolve_raw_source(paths: DatasetPaths, *, typed_schema: str = "", typed_sep: str = DEFAULT_SEP,
                       record_override: dict | None = None) -> RawSource:
    """Establish *paths*' schema (see the module docstring), or raise :class:`RawSchemaError`.

    *record_override* is for an interrupted in-place edit, whose archives have already moved past the record
    they will be checked against once the edit finishes: the caller passes the schema it stored instead, through
    *typed_schema*, and ``{}`` here to skip the record check.
    """
    typed_schema = (typed_schema or "").strip()
    typed_sep = typed_sep or ""
    try:
        record = read_record(paths.root) if record_override is None else (record_override or None)
    except AcquisitionRecordError as exc:
        raise RawSchemaError(f"The acquisition record (in/acquisition.json) can't be read: {exc}") from exc
    stems = collect_input_image_stems(paths.in_dir)

    if record is not None:
        if typed_schema:
            _typed_fields(typed_schema, typed_sep)
            schema, sep = typed_schema, typed_sep
        else:
            schema, sep = DEFAULT_SCHEMA, DEFAULT_SEP
        validation = validate_against_archives(paths.root, record, schema=schema, sep=sep)
        if not validation.ok:
            if typed_schema:
                raise RawSchemaError(
                    f"The filename schema {schema!r} doesn't agree with this dataset's acquisition record "
                    f"(in/acquisition.json): {_issues(validation)}.")
            raise RawSchemaError(
                "This dataset's archives don't match its acquisition record (in/acquisition.json) under the "
                f"converters' naming ({DEFAULT_SCHEMA}, separator {DEFAULT_SEP!r}): {_issues(validation)}. "
                "If the images are named another way, type the filename schema.")
        return RawSource(_info(parse_schema(schema), sep, stems), FROM_RECORD, record)

    if typed_schema:
        fields = _typed_fields(typed_schema, typed_sep)
        bad = sorted(s for s in stems if len(s.split(typed_sep)) != len(fields))
        if bad:
            raise RawSchemaError(
                f"{len(bad)} image name(s) don't fit the filename schema {typed_schema!r} with separator "
                f"{typed_sep!r} ({len(fields)} fields) — for example {bad[0]!r}.")
        return RawSource(_info(fields, typed_sep, stems), FROM_TYPED, None)

    guess = infer_from_dir(paths.in_dir)
    if guess is not None and guess.confident:
        return RawSource(_info(list(guess.fields), guess.separator, stems), FROM_INFERRED, None)
    return RawSource({"schema_fields": [], "available_timepoints": [], "available_fovs": [],
                      "available_channels": [], "fluor_tokens": []}, "", None)


def require_axes(source: RawSource, axes: set[str]) -> None:
    """Refuse when the filter cuts by an axis *source*'s schema has no field for."""
    missing = [axis for axis in sorted(axes) if AXIS_FIELDS[axis] not in source.fields]
    if not missing:
        return
    named = ", ".join({"fov": "FOV"}.get(axis, axis) for axis in missing)
    if not source.origin:
        raise RawSchemaError(
            f"Subset can't choose by {named} here: the dataset has no acquisition record and its image names "
            "don't determine a filename schema, so it can't tell which part of each name that is. Type the "
            "filename schema, or choose wells only.")
    raise RawSchemaError(
        f"The filename schema {source.info['schema']} (from {source.origin}) has no {named} field, so Subset "
        "can't choose by it.")


__all__ = [
    "AXIS_FIELDS", "FROM_INFERRED", "FROM_RECORD", "FROM_TYPED", "RawSchemaError", "RawSource",
    "require_axes", "resolve_raw_source",
]
