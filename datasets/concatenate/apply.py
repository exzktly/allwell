"""Qt-free execution of a Concatenate Datasets :class:`MergePlan`.

Kept separate from :mod:`~datasets.concatenate.worker` (the ``QThread`` wrapper)
so the whole file-merging behaviour — copying per-well inputs, re-packing
dataset 2's archives with remapped timepoint tokens, stitching CSVs and QC
zips, carrying over cell overrides, verifying the result, and writing the
merged sidecar — is unit-testable on ``tmp_path`` fixtures without a Qt event
loop. Progress is reported through a plain ``log`` callback the worker routes
to a queued signal.

Every measurement row carries three timepoint-derived values (label string,
numeric hours, source filename) and every zip member's *name* carries a
fourth — all four are kept in lock-step for dataset-2 rows/members here; see
the module docstring in :mod:`~datasets.concatenate.concatenate_compute` for
why that matters.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from util.timepoint import parse_hours
from util.aw_safe import swallow
from well_viewer.domain.tp_match import tp_close
from datasets.concatenate.concatenate_compute import (
    IN_PROGRESS_MARKER,
    ConcatError,
    DatasetPaths,
    MergePlan,
    in_containers,
    out_csvs,
    out_zips,
    remap_timepoint_label,
    rewrite_member_name,
)

_TIMEPOINT_COLUMN = "timepoint"
_TIMEPOINT_HOURS_COLUMN = "timepoint_hours"
_FILENAME_COLUMN = "filename"
_OVERRIDES_FILENAME = "cell_overrides.json"
_OVERRIDES_SCHEMA_VERSION = 2


# A no-op progress sink, so callers that don't care can omit one.
def _null_log(_message: str) -> None:
    return None


def _null_cancelled() -> bool:
    return False


# ── container IO ─────────────────────────────────────────────────────────────
def read_members(container: Path) -> list[tuple[str, bytes]]:
    """Read a well container's files as ``(member_name, bytes)``.

    Works for both input layouts: a ``.zip`` (its entries) or a folder (its files
    by POSIX-relative name). Directory entries are skipped.
    """
    if container.is_dir():
        members: list[tuple[str, bytes]] = []
        for path in sorted(container.rglob("*")):
            if path.is_file():
                members.append((path.relative_to(container).as_posix(), path.read_bytes()))
        return members
    with zipfile.ZipFile(container) as archive:
        return [
            (info.filename, archive.read(info.filename))
            for info in archive.infolist()
            if not info.is_dir()
        ]


def write_zip(dest: Path, members: list[tuple[str, bytes]]) -> frozenset[str]:
    """Write ``(name, bytes)`` members into a fresh deflated zip at *dest*.

    A later member whose name duplicates an earlier one is skipped rather than
    overwriting it — since dataset 1's members are always listed first when
    both datasets contribute to a well, this means a dataset-2 member that
    failed the schema-conformance check in :func:`rewrite_member_name` (and so
    kept its original name) can never silently clobber a same-named dataset-1
    member. Returns the set of names actually written, so callers can verify
    a CSV row's ``filename`` value actually landed in the archive.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            if name in written:
                continue
            written.add(name)
            archive.writestr(name, data)
    return frozenset(written)


def _rewrite_members(plan: MergePlan, members: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], int]:
    """Rewrite every member's timepoint field. Returns ``(members, unmatched)``
    — *unmatched* counts members that didn't match the filename schema and
    were kept unchanged (see :func:`rewrite_member_name`)."""
    out: list[tuple[str, bytes]] = []
    unmatched = 0
    for name, data in members:
        new_name, rewrote = rewrite_member_name(
            name, schema=plan.schema, channels=plan.channels,
            offset_hours=plan.offset_hours, tp_format=plan.tp_format,
        )
        if not rewrote:
            unmatched += 1
        out.append((new_name, data))
    return out, unmatched


def _copy_container_as_is(src: Path, dest_in_dir: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest_in_dir / src.name, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest_in_dir / src.name)


def merge_input(plan: MergePlan, well: str, c1: Path | None, c2: Path | None) -> tuple[int, frozenset[str] | None]:
    """Merge a well's raw-input container into the destination ``in/``.

    Returns ``(unmatched_member_count, written_names)`` — *written_names* is
    ``None`` when the well came only from dataset 1 (a pure byte-copy, so
    there's nothing dataset-2-derived to check filenames against).
    """
    dest_zip = plan.dest.in_dir / f"{well}.zip"
    if c1 and not c2:
        _copy_container_as_is(c1, plan.dest.in_dir)
        return 0, None
    if c2 and not c1:
        rewritten, unmatched = _rewrite_members(plan, read_members(c2))
        return unmatched, write_zip(dest_zip, rewritten)
    if c1 and c2:
        rewritten, unmatched = _rewrite_members(plan, read_members(c2))
        return unmatched, write_zip(dest_zip, read_members(c1) + rewritten)
    return 0, None


def merge_qc_zip(plan: MergePlan, well: str, z1: Path | None, z2: Path | None) -> int:
    """Merge a well's ``<WELL>_out.zip`` QC archive into the destination
    ``out/``. Returns the unmatched-member count (see :func:`merge_input`)."""
    dest_zip = plan.dest.out_dir / f"{well}_out.zip"
    if z1 and not z2:
        shutil.copy2(z1, dest_zip)
        return 0
    if z2 and not z1:
        rewritten, unmatched = _rewrite_members(plan, read_members(z2))
        write_zip(dest_zip, rewritten)
        return unmatched
    if z1 and z2:
        rewritten, unmatched = _rewrite_members(plan, read_members(z2))
        write_zip(dest_zip, read_members(z1) + rewritten)
        return unmatched
    return 0


# ── CSV row merge ─────────────────────────────────────────────────────────────
def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def _ensure_hours_column(fieldnames: list[str]) -> list[str]:
    """Guarantee ``timepoint_hours`` is in *fieldnames* — the plotting key
    every viewer module reads — inserting it right after ``timepoint`` when
    that's present, else appending it."""
    if _TIMEPOINT_HOURS_COLUMN in fieldnames:
        return fieldnames
    if _TIMEPOINT_COLUMN in fieldnames:
        idx = fieldnames.index(_TIMEPOINT_COLUMN) + 1
        return fieldnames[:idx] + [_TIMEPOINT_HOURS_COLUMN] + fieldnames[idx:]
    return fieldnames + [_TIMEPOINT_HOURS_COLUMN]


def _backfill_hours(rows: list[dict]) -> None:
    """Fill a missing/blank ``timepoint_hours`` from the ``timepoint`` string.

    Applied to rows we otherwise leave untouched (dataset 1, and dataset 2
    rows before remapping) — a legacy CSV written before the pipeline emitted
    this column would otherwise carry a blank cell, which ``load_well_csv``
    coerces to NaN and every viewer module treats as unplottable.
    """
    for row in rows:
        current = str(row.get(_TIMEPOINT_HOURS_COLUMN, "") or "").strip()
        if current and current.lower() != "nan":
            continue
        tp = row.get(_TIMEPOINT_COLUMN)
        if tp is None:
            continue
        hours = parse_hours(tp)
        if hours is not None and math.isfinite(hours):
            row[_TIMEPOINT_HOURS_COLUMN] = str(hours)


def _remap_ds2_rows(plan: MergePlan, rows: list[dict]) -> int:
    """Rewrite dataset-2 rows' ``timepoint``, ``timepoint_hours``, and
    ``filename`` columns in place. Returns the count of rows whose
    ``filename`` didn't match the schema and was left unchanged."""
    unmatched = 0
    for row in rows:
        old_tp = row.get(_TIMEPOINT_COLUMN)
        if old_tp is None or str(old_tp).strip() == "":
            raise ConcatError("A dataset-2 measurement row is missing its 'timepoint' value.")
        new_label = remap_timepoint_label(old_tp, plan.offset_hours, plan.tp_format)
        row[_TIMEPOINT_COLUMN] = new_label
        row[_TIMEPOINT_HOURS_COLUMN] = str(parse_hours(new_label))
        fname = row.get(_FILENAME_COLUMN)
        if fname:
            new_name, rewrote = rewrite_member_name(
                fname, schema=plan.schema, channels=plan.channels,
                offset_hours=plan.offset_hours, tp_format=plan.tp_format,
            )
            if rewrote:
                row[_FILENAME_COLUMN] = new_name
            else:
                unmatched += 1
    return unmatched


def _write_rows(dest: Path, fieldnames: list[str], rows: list[dict]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _row_hours(row: dict) -> float | None:
    raw = row.get(_TIMEPOINT_HOURS_COLUMN)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


@dataclass
class WellCsvStats:
    rows_ds1: int = 0
    rows_ds2: int = 0
    hours_ds1: set = field(default_factory=set)
    hours_ds2: set = field(default_factory=set)  # already offset-shifted
    unmatched_filenames: int = 0
    filename_mismatches: list = field(default_factory=list)
    tp_mismatches: list = field(default_factory=list)  # (timepoint, timepoint_hours)


def merge_measurements(
    plan: MergePlan, well: str, c1: Path | None, c2: Path | None,
    *, ds2_in_names: frozenset[str] | None,
) -> WellCsvStats:
    """Merge a well's measurements CSV into the destination ``out/``.

    *ds2_in_names* is the set of member names :func:`merge_input` actually
    wrote into the destination ``in/<WELL>.zip`` for this well (``None`` if
    dataset 2 contributed nothing) — used to verify every dataset-2 row's
    ``filename`` column actually points at a file that exists in the merged
    archive.
    """
    stats = WellCsvStats()
    if c1 is None and c2 is None:
        return stats

    rows1, fields1 = _read_csv(c1) if c1 else ([], [])
    rows2, fields2 = _read_csv(c2) if c2 else ([], [])

    if rows1:
        _backfill_hours(rows1)
    if rows2:
        stats.unmatched_filenames = _remap_ds2_rows(plan, rows2)
        if ds2_in_names is not None:
            for row in rows2:
                fname = row.get(_FILENAME_COLUMN)
                if fname and fname not in ds2_in_names:
                    stats.filename_mismatches.append(fname)

    fieldnames = fields1 + [f for f in fields2 if f not in fields1]
    fieldnames = _ensure_hours_column(fieldnames)
    dest_name = c1.name if c1 else c2.name
    _write_rows(plan.dest.out_dir / dest_name, fieldnames, rows1 + rows2)

    stats.rows_ds1 = len(rows1)
    stats.rows_ds2 = len(rows2)
    stats.hours_ds1 = {h for h in (_row_hours(r) for r in rows1) if h is not None}
    stats.hours_ds2 = {h for h in (_row_hours(r) for r in rows2) if h is not None}
    for row in rows1 + rows2:
        tp, hours = row.get(_TIMEPOINT_COLUMN), _row_hours(row)
        if tp is None or hours is None:
            continue
        parsed = parse_hours(tp)
        if parsed is None or not math.isfinite(parsed) or not tp_close(parsed, hours):
            stats.tp_mismatches.append((well, tp, hours))
    return stats


# ── restart cleanup ───────────────────────────────────────────────────────────
def clear_destination(dest: DatasetPaths) -> None:
    """Remove a destination's ``in``/``out`` content (a failed prior attempt).

    Every well is rebuilt from the two source datasets on each
    :func:`execute_plan` call — nothing here is incremental — so the only
    correct way to resume into a destination that already has leftovers is to
    clear them first; otherwise a well from an earlier, differently-scoped
    attempt (or a partially-written file) would linger in the "finished"
    output.
    """
    for folder in (dest.in_dir, dest.out_dir):
        if folder.exists():
            shutil.rmtree(folder)


# ── cell overrides carry-over ─────────────────────────────────────────────────
def _merge_cell_overrides(plan: MergePlan, emit: Callable[[str], None]) -> None:
    """Carry ``cell_overrides.json`` (Segmentation-tab include/exclude
    patches) from both source datasets into the merged destination.

    Dataset-2 entries have their ``tp`` key remapped through the same offset
    as everything else; a malformed entry (unparseable ``tp``) is dropped
    rather than carried over with a wrong value. Every entry's segmentation
    fingerprint is restamped to the *merged* ``<WELL>_out.zip``'s mtime —
    repacking changes the file's mtime even though the segmentation itself
    (nucleus IDs) is untouched by a merge, and the fingerprint exists only to
    catch a real re-segmentation, so restamping is correct rather than a
    workaround.
    """
    entries: list[dict] = []
    for paths, offset_side in ((plan.ds1, False), (plan.ds2, True)):
        src = paths.out_dir / _OVERRIDES_FILENAME
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
            if offset_side:
                tp = str(entry.get("tp", "")).strip()
                if not tp:
                    continue
                try:
                    entry["tp"] = remap_timepoint_label(tp, plan.offset_hours, plan.tp_format)
                except ConcatError:
                    continue
            entries.append(entry)

    if not entries:
        return

    for entry in entries:
        well = str(entry.get("well", "")).strip()
        zip_path = plan.dest.out_dir / f"{well}_out.zip"
        with swallow(f"stat {zip_path}"):
            entry["seg_fp"] = f"{zip_path.stat().st_mtime_ns}"

    payload = {"version": _OVERRIDES_SCHEMA_VERSION, "overrides": entries}
    (plan.dest.out_dir / _OVERRIDES_FILENAME).write_text(json.dumps(payload))
    emit(f"Carried over {len(entries)} cell override(s) from the source dataset(s).")


# ── verification (runs before the sidecar is written) ────────────────────────
def _verify(plan: MergePlan, well_stats: dict[str, WellCsvStats]) -> None:
    """Raise :class:`ConcatError` if the merged output doesn't actually plot
    correctly — this is the check that would have caught the original bug.
    Runs before ``pipeline_info.json`` is written (see :func:`execute_plan`),
    so a failure never leaves a destination that looks finished.
    """
    tp_mismatches = [m for s in well_stats.values() for m in s.tp_mismatches]
    if tp_mismatches:
        well, tp, hours = tp_mismatches[0]
        raise ConcatError(
            f"Verification failed: {len(tp_mismatches)} row(s) have a "
            f"'timepoint' label that doesn't match their 'timepoint_hours' "
            f"value (e.g. well {well}: {tp!r} vs {hours!r}). The merge did "
            "not complete correctly."
        )

    for well, stats in well_stats.items():
        if not stats.hours_ds1 or not stats.hours_ds2:
            continue
        collision = stats.hours_ds1 & stats.hours_ds2
        if collision:
            raise ConcatError(
                f"Verification failed: well {well} has dataset-2 rows landing "
                f"on the same timepoint(s) as dataset 1 ({sorted(collision)}) "
                "after the offset — dataset 2 did not shift correctly."
            )
        if min(stats.hours_ds2) <= max(stats.hours_ds1):
            raise ConcatError(
                f"Verification failed: well {well}'s dataset-2 timepoints "
                "don't all come after dataset 1's — the merge did not "
                "complete correctly."
            )

    all_mismatches = [f for s in well_stats.values() for f in s.filename_mismatches]
    if all_mismatches:
        raise ConcatError(
            f"Verification failed: {len(all_mismatches)} dataset-2 row(s) "
            f"reference an image file not present in the merged archive "
            f"(e.g. {all_mismatches[0]!r}). The merge did not complete "
            "correctly."
        )

    declared_hours: set[float] = set()
    for token in plan.merged_info.get("available_timepoints", []) or []:
        hours = parse_hours(str(token))
        if hours is not None and math.isfinite(hours):
            declared_hours.add(hours)
    all_row_hours = {h for s in well_stats.values() for h in (s.hours_ds1 | s.hours_ds2)}
    missing = [h for h in all_row_hours if not any(tp_close(h, d) for d in declared_hours)]
    if missing:
        raise ConcatError(
            f"Verification failed: {len(missing)} distinct timepoint(s) in "
            f"the merged CSVs (e.g. {missing[0]:g}h) aren't listed in the "
            "merged pipeline_info.json. The merge did not complete correctly."
        )


# ── top-level execution ──────────────────────────────────────────────────────
def execute_plan(
    plan: MergePlan,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    """Carry out *plan*, writing the merged dataset. Returns a summary line.

    *log* receives human-readable progress lines (defaults to a no-op).
    *is_cancelled* is polled once per well; when it returns ``True`` the merge
    stops with a plain :class:`ConcatError` and the in-progress marker is left
    in place, so a later re-run is correctly detected as a resumable restart.

    Writes :data:`~datasets.concatenate.concatenate_compute.IN_PROGRESS_MARKER`
    first and removes it only after the merged sidecar is written — the whole
    restart-detection mechanism (see ``concatenate_compute.destination_has_marker``)
    depends on that ordering, as does never writing ``pipeline_info.json``
    until :func:`_verify` has passed.
    """
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled

    emit(
        f"Dataset 1 last timepoint {plan.ds1_last_hours:g}h, interval "
        f"{plan.ds1_interval_hours:g}h → dataset 2 shifted by {plan.offset_hours:g}h."
    )
    if plan.format_warning:
        emit(f"Note: {plan.format_warning}")
    for warning in plan.column_warnings:
        emit(f"Note: {warning}")
    emit(
        f"Wells: {len(plan.wells_ds1_only)} from dataset 1 only, "
        f"{len(plan.wells_ds2_only)} from dataset 2 only, "
        f"{len(plan.wells_both)} stitched."
    )
    if plan.merged_info.get("cell_gating") or plan.merged_info.get("sample_definitions"):
        emit(
            "Note: gating thresholds and sample definitions carry over from "
            "dataset 1 and apply to dataset 2's rows too — review them after "
            "opening the merged dataset."
        )

    if plan.dest_is_restart:
        emit(
            "Destination has leftover files from an earlier attempt that didn't "
            "finish — clearing them before rebuilding."
        )
        clear_destination(plan.dest)

    plan.dest.root.mkdir(parents=True, exist_ok=True)
    marker_path = plan.dest.root / IN_PROGRESS_MARKER
    marker_path.write_text("")
    plan.dest.in_dir.mkdir(parents=True, exist_ok=True)
    plan.dest.out_dir.mkdir(parents=True, exist_ok=True)

    in1, in2 = in_containers(plan.ds1), in_containers(plan.ds2)
    csv1, csv2 = out_csvs(plan.ds1), out_csvs(plan.ds2)
    zip1, zip2 = out_zips(plan.ds1), out_zips(plan.ds2)

    all_wells = sorted(
        set(in1) | set(in2) | set(csv1) | set(csv2) | set(zip1) | set(zip2)
    )

    well_stats: dict[str, WellCsvStats] = {}
    total_unmatched = 0
    for well in all_wells:
        if cancelled():
            raise ConcatError("Cancelled — the in-progress destination was left in place to resume later.")
        emit(f"  {well}…")
        unmatched_in, in_names = merge_input(plan, well, in1.get(well), in2.get(well))
        unmatched_qc = merge_qc_zip(plan, well, zip1.get(well), zip2.get(well))
        stats = merge_measurements(plan, well, csv1.get(well), csv2.get(well), ds2_in_names=in_names)
        well_stats[well] = stats
        total_unmatched += unmatched_in + unmatched_qc + stats.unmatched_filenames

    if total_unmatched:
        emit(
            f"Note: {total_unmatched} file(s) didn't match the filename "
            "schema and were copied unchanged (their timepoint wasn't shifted)."
        )

    emit("Verifying the merged output before finishing…")
    _verify(plan, well_stats)

    _merge_cell_overrides(plan, emit)

    (plan.dest.out_dir / "pipeline_info.json").write_text(json.dumps(plan.merged_info, indent=2))
    marker_path.unlink(missing_ok=True)
    emit("Wrote merged pipeline_info.json.")

    return (
        f"Concatenated {len(all_wells)} well(s) into {plan.dest.root} "
        f"(dataset 2 shifted +{plan.offset_hours:g}h)."
    )
