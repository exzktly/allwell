"""Qt-free execution of a Concatenate Datasets :class:`MergePlan`.

Kept separate from :mod:`~datasets.concatenate.worker` (the ``QThread`` wrapper)
so the whole file-merging behaviour — copying per-well inputs, re-packing
dataset 2's archives with remapped timepoint tokens, stitching CSVs and QC zips,
writing the merged sidecar — is unit-testable on ``tmp_path`` fixtures without a
Qt event loop. Progress is reported through a plain ``log`` callback the worker
routes to a queued signal.
"""

from __future__ import annotations

import csv
import json
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

from datasets.concatenate.concatenate_compute import (
    DatasetPaths,
    MergePlan,
    in_containers,
    out_csvs,
    out_zips,
    rewrite_timepoint_in_name,
)

_TIMEPOINT_COLUMN = "timepoint"

# A no-op progress sink, so callers that don't care can omit one.
def _null_log(_message: str) -> None:
    return None


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


def write_zip(dest: Path, members: list[tuple[str, bytes]]) -> None:
    """Write ``(name, bytes)`` members into a fresh deflated zip at *dest*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)


def remap_csv_timepoints(src: Path, dest: Path, tp_map: dict[str, str]) -> None:
    """Copy a measurements CSV, remapping its ``timepoint`` column values."""
    with src.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = _remap_rows(list(reader), tp_map)
        fieldnames = list(reader.fieldnames or [])
    _write_rows(dest, fieldnames, rows)


def merge_csvs(src1: Path, src2: Path, dest: Path, tp_map: dict[str, str]) -> None:
    """Write dataset 1's rows then dataset 2's remapped rows into one CSV.

    Column sets are unioned (dataset 1's order first, then any new dataset 2
    columns); missing cells fill with ``""`` — the same reconciliation
    ``pipeline/io.merge_records_into_csv`` uses.
    """
    with src1.open(newline="") as handle:
        reader1 = csv.DictReader(handle)
        rows1 = list(reader1)
        fields1 = list(reader1.fieldnames or [])
    with src2.open(newline="") as handle:
        reader2 = csv.DictReader(handle)
        rows2 = _remap_rows(list(reader2), tp_map)
        fields2 = list(reader2.fieldnames or [])

    fieldnames = fields1 + [f for f in fields2 if f not in fields1]
    _write_rows(dest, fieldnames, rows1 + rows2)


def _remap_rows(rows: list[dict], tp_map: dict[str, str]) -> list[dict]:
    for row in rows:
        current = row.get(_TIMEPOINT_COLUMN)
        if current is not None:
            row[_TIMEPOINT_COLUMN] = tp_map.get(current, current)
    return rows


def _write_rows(dest: Path, fieldnames: list[str], rows: list[dict]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


# ── per-well merge ───────────────────────────────────────────────────────────
def _rewrite_members(plan: MergePlan, members: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    return [
        (rewrite_timepoint_in_name(name, plan.sep, plan.tp_index, plan.tp_label_map), data)
        for name, data in members
    ]


def _copy_container_as_is(src: Path, dest_in_dir: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest_in_dir / src.name, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest_in_dir / src.name)


def merge_input(plan: MergePlan, well: str, c1: Path | None, c2: Path | None) -> None:
    """Merge a well's raw-input container into the destination ``in/``."""
    dest_zip = plan.dest.in_dir / f"{well}.zip"
    if c1 and not c2:
        _copy_container_as_is(c1, plan.dest.in_dir)
    elif c2 and not c1:
        write_zip(dest_zip, _rewrite_members(plan, read_members(c2)))
    elif c1 and c2:
        write_zip(dest_zip, read_members(c1) + _rewrite_members(plan, read_members(c2)))


def merge_measurements(plan: MergePlan, well: str, c1: Path | None, c2: Path | None) -> None:
    """Merge a well's measurements CSV into the destination ``out/``."""
    if c1 and not c2:
        shutil.copy2(c1, plan.dest.out_dir / c1.name)
    elif c2 and not c1:
        remap_csv_timepoints(c2, plan.dest.out_dir / c2.name, plan.tp_label_map)
    elif c1 and c2:
        merge_csvs(c1, c2, plan.dest.out_dir / c1.name, plan.tp_label_map)


def merge_qc_zip(plan: MergePlan, well: str, z1: Path | None, z2: Path | None) -> None:
    """Merge a well's ``<WELL>_out.zip`` QC archive into the destination ``out/``."""
    dest_zip = plan.dest.out_dir / f"{well}_out.zip"
    if z1 and not z2:
        shutil.copy2(z1, dest_zip)
    elif z2 and not z1:
        write_zip(dest_zip, _rewrite_members(plan, read_members(z2)))
    elif z1 and z2:
        write_zip(dest_zip, read_members(z1) + _rewrite_members(plan, read_members(z2)))


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


# ── top-level execution ──────────────────────────────────────────────────────
def execute_plan(plan: MergePlan, log: Callable[[str], None] | None = None) -> str:
    """Carry out *plan*, writing the merged dataset. Returns a summary line.

    *log* receives human-readable progress lines (defaults to a no-op).
    """
    emit = log or _null_log
    emit(
        f"Dataset 1 last timepoint {plan.ds1_last_hours:g}h, interval "
        f"{plan.ds1_interval_hours:g}h → dataset 2 shifted by {plan.offset_hours:g}h."
    )
    emit(
        f"Wells: {len(plan.wells_ds1_only)} from dataset 1 only, "
        f"{len(plan.wells_ds2_only)} from dataset 2 only, "
        f"{len(plan.wells_both)} stitched."
    )

    if plan.dest_is_restart:
        emit(
            "Destination has leftover files from an earlier attempt that didn't "
            "finish — clearing them before rebuilding."
        )
        clear_destination(plan.dest)

    plan.dest.in_dir.mkdir(parents=True, exist_ok=True)
    plan.dest.out_dir.mkdir(parents=True, exist_ok=True)

    in1, in2 = in_containers(plan.ds1), in_containers(plan.ds2)
    csv1, csv2 = out_csvs(plan.ds1), out_csvs(plan.ds2)
    zip1, zip2 = out_zips(plan.ds1), out_zips(plan.ds2)

    all_wells = sorted(
        set(in1) | set(in2) | set(csv1) | set(csv2) | set(zip1) | set(zip2)
    )
    for well in all_wells:
        emit(f"  {well}…")
        merge_input(plan, well, in1.get(well), in2.get(well))
        merge_measurements(plan, well, csv1.get(well), csv2.get(well))
        merge_qc_zip(plan, well, zip1.get(well), zip2.get(well))

    (plan.dest.out_dir / "pipeline_info.json").write_text(json.dumps(plan.merged_info, indent=2))
    emit("Wrote merged pipeline_info.json.")

    return (
        f"Concatenated {len(all_wells)} well(s) into {plan.dest.root} "
        f"(dataset 2 shifted +{plan.offset_hours:g}h)."
    )
