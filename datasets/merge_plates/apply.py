"""Qt-free execution of a Merge Plates :class:`~datasets.merge_plates.merge_plates_compute.PlateMergePlan`.

Reuses ``execute_plan``'s skeleton from Concatenate — marker written first,
per-well loop with a ``progress(well, stage)`` callback, ``_verify``, cell
overrides, sidecar, marker cleared last — swapping the timepoint shift for a
well-token rewrite. When dataset 2's well is unmapped (identity), nothing is
rewritten at all: containers are copied byte-for-byte and CSV rows are copied
verbatim, so the fast, common case (two already-disjoint plates) is a pure
copy.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from util.aw_safe import swallow
from util.well_token import canonical_well_label
from datasets._common.containers import (
    FILENAME_COLUMN,
    OVERRIDES_FILENAME,
    OVERRIDES_SCHEMA_VERSION,
    clear_destination,
    read_members,
    write_zip,
)
from datasets._common.member_names import rewrite_member_field
from datasets.merge_plates.merge_plates_compute import (
    IN_PROGRESS_MARKER,
    MergePlatesError,
    PlateMergePlan,
    in_containers,
    out_csvs,
    out_zips,
)


def _null_log(_message: str) -> None:
    return None


def _null_cancelled() -> bool:
    return False


def _null_progress(_well: str, _stage: str) -> None:
    return None


def _remap_well_field(name: str, *, schema, channels, old_well: str, new_well: str) -> tuple[str, bool]:
    def _predicate(old_token: str) -> str | None:
        return new_well if canonical_well_label(old_token) == old_well else None
    return rewrite_member_field(name, schema=schema, channels=channels, field="well", new_value=_predicate)


def _copy_container_as_is(src: Path, dest_dir: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest_dir / src.name, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest_dir / src.name)


def _needs_rewrite(src_well: str, dest_well: str) -> bool:
    return src_well != dest_well


def _merge_input(
    plan: PlateMergePlan, dest_well: str, src_well: str, container: Path | None,
) -> frozenset[str] | None:
    """Copy a well's raw-input container into the destination ``in/``.

    Returns the set of member names actually written when a rewrite
    happened (so the caller can verify a CSV row's ``filename`` lands in the
    merged archive); ``None`` when the container was copied byte-for-byte —
    a folder-mode input, or an unmapped (identity) well — so there is
    nothing rewritten to check against.
    """
    if container is None:
        return None
    if not _needs_rewrite(src_well, dest_well):
        _copy_container_as_is(container, plan.dest.in_dir)
        return None
    if container.is_dir():
        raise MergePlatesError(
            f"Well {src_well} needs remapping to {dest_well}, but its raw input "
            f"is a folder ({container.name}), not a zip — Merge plates can only "
            "rewrite well tokens inside zip archives. Re-run the pipeline with "
            "compressed input, or don't remap this well."
        )
    members = read_members(container)
    rewritten = [
        (_remap_well_field(name, schema=plan.schema, channels=plan.channels,
                           old_well=src_well, new_well=dest_well)[0], data)
        for name, data in members
    ]
    written, _dropped = write_zip(plan.dest.in_dir / f"{dest_well}.zip", rewritten)
    return written


def _merge_qc_zip(plan: PlateMergePlan, dest_well: str, src_well: str, container: Path | None) -> None:
    if container is None:
        return
    if not _needs_rewrite(src_well, dest_well):
        shutil.copy2(container, plan.dest.out_dir / f"{dest_well}_out.zip")
        return
    members = read_members(container)
    rewritten = [
        (_remap_well_field(name, schema=plan.schema, channels=plan.channels,
                           old_well=src_well, new_well=dest_well)[0], data)
        for name, data in members
    ]
    write_zip(plan.dest.out_dir / f"{dest_well}_out.zip", rewritten)


def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def _write_rows(dest: Path, fieldnames: list[str], rows: list[dict]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


_TRAILING_WELL_RE = re.compile(r"([A-Ha-h])(\d{1,2})$")


def _csv_prefix(csv_path: Path) -> str:
    """The part of a measurements-CSV stem before its trailing well token —
    derived from the actual filename (regardless of padding), never
    reconstructed from a canonical well label the way that has silently
    produced a nonexistent path elsewhere in this app."""
    match = _TRAILING_WELL_RE.search(csv_path.stem)
    if not match:
        return csv_path.stem
    return csv_path.stem[: match.start()].rstrip("_-") or "measurements"


def _merge_measurements(
    plan: PlateMergePlan, dest_well: str, src_well: str, csv_path: Path | None,
    *, dest_in_names: frozenset[str] | None,
) -> tuple[int, list[str]]:
    """Returns ``(row_count, filename_mismatches)``."""
    if csv_path is None:
        return 0, []
    rows, fieldnames = _read_csv(csv_path)
    mismatches: list[str] = []
    if _needs_rewrite(src_well, dest_well):
        for row in rows:
            row["well"] = dest_well
            fname = row.get(FILENAME_COLUMN)
            if fname:
                new_name, rewrote = _remap_well_field(
                    fname, schema=plan.schema, channels=plan.channels,
                    old_well=src_well, new_well=dest_well,
                )
                if rewrote:
                    row[FILENAME_COLUMN] = new_name

    dest_name = f"{_csv_prefix(csv_path)}_{dest_well}.csv"
    _write_rows(plan.dest.out_dir / dest_name, fieldnames, rows)
    if dest_in_names is not None:
        for row in rows:
            fname = row.get(FILENAME_COLUMN)
            if fname and fname not in dest_in_names:
                mismatches.append(fname)
    return len(rows), mismatches


def _merge_cell_overrides(plan: PlateMergePlan, emit: Callable[[str], None]) -> None:
    entries: list[dict] = []
    for paths, is_ds2 in ((plan.ds1, False), (plan.ds2, True)):
        src = paths.out_dir / OVERRIDES_FILENAME
        if not src.exists():
            continue
        data = None
        with swallow(f"read {src}"):
            data = json.loads(src.read_text())
        if not isinstance(data, dict):
            continue
        for entry in data.get("overrides") or []:
            if not isinstance(entry, dict):
                continue
            entry = dict(entry)
            well = str(entry.get("well", "")).strip()
            if is_ds2:
                entry["well"] = plan.well_map.get(well, well)
            entries.append(entry)

    if not entries:
        return
    for entry in entries:
        zip_path = plan.dest.out_dir / f"{entry.get('well', '')}_out.zip"
        with swallow(f"stat {zip_path}"):
            entry["seg_fp"] = f"{zip_path.stat().st_mtime_ns}"
    payload = {"version": OVERRIDES_SCHEMA_VERSION, "overrides": entries}
    (plan.dest.out_dir / OVERRIDES_FILENAME).write_text(json.dumps(payload))
    emit(f"Carried over {len(entries)} cell override(s) from the source dataset(s).")


def _verify(plan: PlateMergePlan, row_counts: dict[str, int], mismatches: dict[str, list[str]]) -> None:
    if sum(row_counts.values()) == 0:
        raise MergePlatesError(
            "Verification failed: the merge wrote zero measurement rows — check "
            "that each dataset's out/ folder actually contains its measurements CSVs."
        )
    all_mismatches = [f for lst in mismatches.values() for f in lst]
    if all_mismatches:
        raise MergePlatesError(
            f"Verification failed: {len(all_mismatches)} row(s) reference an "
            f"image file not present in the merged in/ archive (e.g. "
            f"{all_mismatches[0]!r})."
        )


def execute_plan(
    plan: PlateMergePlan,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> str:
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled
    report = progress or _null_progress

    if plan.timepoint_warning:
        emit(f"Note: {plan.timepoint_warning}")
    emit(
        f"Wells: {len(plan.wells_ds1)} from dataset 1, {len(plan.wells_ds2)} from "
        "dataset 2 (after remap)."
    )

    if plan.dest_is_restart:
        emit("Destination has leftover files from an earlier attempt — clearing.")
        clear_destination(plan.dest)

    plan.dest.root.mkdir(parents=True, exist_ok=True)
    marker_path = plan.dest.root / IN_PROGRESS_MARKER
    marker_path.write_text("")
    plan.dest.in_dir.mkdir(parents=True, exist_ok=True)
    plan.dest.out_dir.mkdir(parents=True, exist_ok=True)

    in1, in2 = in_containers(plan.ds1), in_containers(plan.ds2)
    csv1, csv2 = out_csvs(plan.ds1), out_csvs(plan.ds2)
    zip1, zip2 = out_zips(plan.ds1), out_zips(plan.ds2)

    row_counts: dict[str, int] = {}
    mismatches: dict[str, list[str]] = {}

    ds1_wells = sorted(set(in1) | set(csv1) | set(zip1))
    for src_well in ds1_wells:
        if cancelled():
            raise MergePlatesError("Cancelled — the in-progress destination was left in place to resume later.")
        report(src_well, "merging")
        emit(f"  {src_well} (dataset 1)…")
        dest_names = _merge_input(plan, src_well, src_well, in1.get(src_well))
        _merge_qc_zip(plan, src_well, src_well, zip1.get(src_well))
        n, mism = _merge_measurements(
            plan, src_well, src_well, csv1.get(src_well), dest_in_names=dest_names,
        )
        row_counts[src_well] = n
        mismatches[src_well] = mism
        report(src_well, "merged")

    ds2_wells = sorted(set(in2) | set(csv2) | set(zip2))
    for src_well in ds2_wells:
        dest_well = plan.well_map.get(src_well, src_well)
        if cancelled():
            raise MergePlatesError("Cancelled — the in-progress destination was left in place to resume later.")
        report(dest_well, "merging")
        emit(f"  {src_well} → {dest_well} (dataset 2)…")
        dest_names = _merge_input(plan, dest_well, src_well, in2.get(src_well))
        _merge_qc_zip(plan, dest_well, src_well, zip2.get(src_well))
        n, mism = _merge_measurements(
            plan, dest_well, src_well, csv2.get(src_well), dest_in_names=dest_names,
        )
        row_counts[dest_well] = n
        mismatches[dest_well] = mism
        report(dest_well, "merged")

    emit("Verifying the merged output before finishing…")
    _verify(plan, row_counts, mismatches)

    _merge_cell_overrides(plan, emit)

    (plan.dest.out_dir / "pipeline_info.json").write_text(json.dumps(plan.merged_info, indent=2))
    marker_path.unlink(missing_ok=True)
    emit("Wrote merged pipeline_info.json.")

    total_wells = len(plan.wells_ds1) + len(plan.wells_ds2)
    return f"Merged {total_wells} well(s) into {plan.dest.root}."
