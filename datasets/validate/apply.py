"""Metadata-only repairs for a dataset flagged by
:mod:`~datasets.validate.validate_compute`.

Never touches measurement CSVs or well archives — only the two sidecar
availability lists, the stale entries in ``cell_overrides.json``, and leftover
staging files. Rebuilding the availability lists **read-modify-writes the
already-loaded sidecar dict** rather than mimicking
``process_microscopy.write_pipeline_info``'s from-scratch rebuild (which must
therefore name its four GUI-written preserved keys — ``cell_gating``,
``sample_definitions``, ``ratios``, ``notes`` — explicitly): starting from the
loaded dict and mutating only ``available_timepoints``/``available_fovs``
preserves every other key, including one added later, for free.

``services.remote_runner.refresh_pipeline_info_available_from_outputs`` is the
wrong tool here — it only ever *unions* into the existing lists, so a stale
entry (a timepoint whose images were since deleted) can never be dropped. This
module's rebuild replaces the lists with what's actually on disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from util.timepoint import parse_hours, sort_key as tp_sort_key
from domain.image_identity import load_schema
from datasets.core.dataset_paths import (
    collect_available_schema_values,
    out_zips,
    resolve_dataset,
)
from datasets.validate.validate_compute import DatasetReport, _OVERRIDES_FILENAME
from util.json_write import write_json_atomic
from util.provenance_log import append_event


@dataclass(frozen=True)
class RepairResult:
    rebuilt_availability: bool
    pruned_overrides: int
    swept_staging_files: int
    summary: str


def _fov_sort_key(value: str):
    hours = parse_hours(value)
    return (hours is None, hours or 0.0, value)


def _sweep_staging_files(paths) -> int:
    """Remove leftover ``__aw_tmp_*`` staging files under ``in``/``out`` — a
    kill mid-write leaves these behind (``util.zip_write.atomic_zip``)."""
    removed = 0
    for folder in (paths.in_dir, paths.out_dir):
        if not folder.is_dir():
            continue
        for entry in folder.glob("__aw_tmp_*"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def _prune_stale_overrides(paths) -> int:
    overrides_path = paths.out_dir / _OVERRIDES_FILENAME
    if not overrides_path.exists():
        return 0
    try:
        data = json.loads(overrides_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    zip_map = out_zips(paths)
    kept = []
    dropped = 0
    for entry in data.get("overrides") or []:
        if not isinstance(entry, dict):
            continue
        well = str(entry.get("well", "")).strip()
        stored_fp = str(entry.get("seg_fp", "")).strip()
        zip_path = zip_map.get(well)
        if stored_fp and zip_path is not None:
            try:
                current_fp = f"{zip_path.stat().st_mtime_ns}"
            except OSError:
                kept.append(entry)
                continue
            if current_fp != stored_fp:
                dropped += 1
                continue
        kept.append(entry)
    if dropped:
        payload = {"version": data.get("version", 2), "overrides": kept}
        write_json_atomic(overrides_path, payload, indent=None)
    return dropped


def apply_repairs(root: Path | str, report: DatasetReport) -> RepairResult:
    """Apply the metadata-only repairs *report* flagged as available.

    Idempotent: re-running on an already-repaired dataset is a no-op that
    reports nothing changed.
    """
    paths = resolve_dataset(root, label="dataset")
    info_path = paths.out_dir / "pipeline_info.json"
    info = json.loads(info_path.read_text())

    # Resolved through load_schema, exactly as validate_compute.build_report
    # does for its own stale/missing diff -- a bare info.get("schema_fields")
    # here (no legacy-schema-string / index-only / default-fields fallback)
    # would silently disagree with the schema build_report actually diffed
    # against, and for any sidecar relying on one of those fallbacks (no
    # schema_fields key at all) would resolve to [], which
    # collect_available_schema_values treats as "field not in schema" and
    # returns [] for -- wiping a real, correct availability list to empty.
    schema = load_schema(info)
    schema_fields = list(schema.fields)
    sep = schema.sep

    rebuilt = False
    if report.stale_available_timepoints or report.missing_available_timepoints:
        info["available_timepoints"] = collect_available_schema_values(
            paths.in_dir, schema_fields=schema_fields, sep=sep,
            field_name="timepoint", sort_key=tp_sort_key,
        )
        rebuilt = True
    if report.stale_available_fovs or report.missing_available_fovs:
        info["available_fovs"] = collect_available_schema_values(
            paths.in_dir, schema_fields=schema_fields, sep=sep,
            field_name="fov", sort_key=_fov_sort_key,
        )
        rebuilt = True
    if rebuilt:
        write_json_atomic(info_path, info)

    pruned = _prune_stale_overrides(paths)
    swept = _sweep_staging_files(paths)

    parts = []
    if rebuilt:
        parts.append("rebuilt available_timepoints/available_fovs from disk")
    if pruned:
        parts.append(f"pruned {pruned} stale cell override(s)")
    if swept:
        parts.append(f"swept {swept} leftover staging file(s)")
    summary = "; ".join(parts) if parts else "nothing to repair"
    if parts:
        # Only a repair that changed something is history; the idempotent
        # re-run that found nothing to do leaves the log as it was.
        append_event(paths.out_dir, "repaired", source="datasets", detail={
            "actions": parts,
            "pruned_overrides": pruned,
            "swept_staging_files": swept,
            "rebuilt_availability": rebuilt,
        })

    return RepairResult(
        rebuilt_availability=rebuilt,
        pruned_overrides=pruned,
        swept_staging_files=swept,
        summary=summary,
    )
