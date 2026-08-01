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
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from util.aw_safe import swallow
from util.well_token import canonical_well_label
from datasets._common.concurrency import pick_workers
from datasets._common.containers import (
    FILENAME_COLUMN,
    OVERRIDES_FILENAME,
    OVERRIDES_SCHEMA_VERSION,
    WellFraction,
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


def _null_sub_progress(_well: str, _done: int, _total: int) -> None:
    return None


# One destination well is four passes over files — read the source raw-input
# container, write the remapped one, then the same again for the QC
# ``<WELL>_out.zip`` — fused by :class:`WellFraction` into a single 0 → 100%
# dial per well.
_SLICES_PER_WELL = 4


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
    *, fraction: WellFraction,
) -> frozenset[str] | None:
    """Copy a well's raw-input container into the destination ``in/``.

    Returns the set of member names actually written when a rewrite
    happened (so the caller can verify a CSV row's ``filename`` lands in the
    merged archive); ``None`` when the container was copied byte-for-byte —
    a folder-mode input, or an unmapped (identity) well — so there is
    nothing rewritten to check against.

    *fraction* owns this well's dashboard dial; this pass claims its first
    two slices (read, then write). A byte-for-byte copy has no member-level
    steps to report, so it closes both slices out at once rather than
    leaving the dial stuck below half for a well that is in fact done.
    """
    if container is None:
        fraction.skip(2)
        return None
    if not _needs_rewrite(src_well, dest_well):
        _copy_container_as_is(container, plan.dest.in_dir)
        fraction.skip(2)
        return None
    if container.is_dir():
        raise MergePlatesError(
            f"Well {src_well} needs remapping to {dest_well}, but its raw input "
            f"is a folder ({container.name}), not a zip — Merge plates can only "
            "rewrite well tokens inside zip archives. Re-run the pipeline with "
            "compressed input, or don't remap this well."
        )
    members = read_members(container, progress=fraction.slice_progress())
    rewritten = [
        (_remap_well_field(name, schema=plan.schema, channels=plan.channels,
                           old_well=src_well, new_well=dest_well)[0], data)
        for name, data in members
    ]
    written, _dropped = write_zip(
        plan.dest.in_dir / f"{dest_well}.zip", rewritten,
        progress=fraction.slice_progress(),
    )
    return written


def _merge_qc_zip(
    plan: PlateMergePlan, dest_well: str, src_well: str, container: Path | None,
    *, fraction: WellFraction,
) -> None:
    """Copy a well's QC ``<WELL>_out.zip`` into the destination ``out/``,
    claiming the second half of *fraction*'s slices (see :func:`_merge_input`)."""
    if container is None:
        fraction.skip(2)
        return
    if not _needs_rewrite(src_well, dest_well):
        shutil.copy2(container, plan.dest.out_dir / f"{dest_well}_out.zip")
        fraction.skip(2)
        return
    members = read_members(container, progress=fraction.slice_progress())
    rewritten = [
        (_remap_well_field(name, schema=plan.schema, channels=plan.channels,
                           old_well=src_well, new_well=dest_well)[0], data)
        for name, data in members
    ]
    write_zip(
        plan.dest.out_dir / f"{dest_well}_out.zip", rewritten,
        progress=fraction.slice_progress(),
    )


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
    workers: int = 0,
    sub_progress: Callable[[str, int, int], None] | None = None,
) -> str:
    """Carry out *plan*, writing the merged dataset. Returns a summary line.

    *progress* receives ``(well, stage)`` as each destination well starts and
    finishes; *sub_progress* receives ``(well, done, total)`` repeatedly
    *within* one well, so a dashboard can render it as a filling dial instead
    of a binary merging/merged state. The four passes a well takes (read then
    write, for the raw input and again for the QC archive) are fused into one
    sweep from 0 to *total* by
    :class:`~datasets._common.containers.WellFraction` — without that, each
    pass would fill the same dial from empty on its own. ``sub_progress`` is
    called from the worker threads without the callback lock: every call names
    its own well, so interleaving is harmless, and locking once per member
    would serialise the parallel merge it is reporting on.

    Dataset 1's and dataset 2's wells are merged together in one parallel
    pass — *workers* threads (0 = auto, see
    :func:`datasets._common.concurrency.pick_workers`). Each unit of work
    owns one *destination* well's own files; a destination well only ever
    comes from one source (`build_plate_merge_plan` already refused a
    post-remap collision when the plan was built), so the two source sides
    can safely run in the same pool rather than two sequential rounds.
    ``log``/``progress`` calls before a well's work starts are serialised
    behind a lock; aggregating each result back into ``row_counts``/
    ``mismatches`` happens on this function's own thread as each future
    completes, so that bookkeeping needs no lock.
    """
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled
    report = progress or _null_progress
    report_fraction = sub_progress or _null_sub_progress

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

    callback_lock = threading.Lock()

    def _merge_ds1_well(src_well: str):
        if cancelled():
            raise MergePlatesError("Cancelled — the in-progress destination was left in place to resume later.")
        with callback_lock:
            report(src_well, "merging")
            emit(f"  {src_well} (dataset 1)…")
        fraction = WellFraction(report_fraction, src_well, _SLICES_PER_WELL)
        dest_names = _merge_input(plan, src_well, src_well, in1.get(src_well), fraction=fraction)
        _merge_qc_zip(plan, src_well, src_well, zip1.get(src_well), fraction=fraction)
        n, mism = _merge_measurements(
            plan, src_well, src_well, csv1.get(src_well), dest_in_names=dest_names,
        )
        fraction.finish()
        return src_well, n, mism

    def _merge_ds2_well(src_well: str):
        dest_well = plan.well_map.get(src_well, src_well)
        if cancelled():
            raise MergePlatesError("Cancelled — the in-progress destination was left in place to resume later.")
        with callback_lock:
            report(dest_well, "merging")
            emit(f"  {src_well} → {dest_well} (dataset 2)…")
        fraction = WellFraction(report_fraction, dest_well, _SLICES_PER_WELL)
        dest_names = _merge_input(plan, dest_well, src_well, in2.get(src_well), fraction=fraction)
        _merge_qc_zip(plan, dest_well, src_well, zip2.get(src_well), fraction=fraction)
        n, mism = _merge_measurements(
            plan, dest_well, src_well, csv2.get(src_well), dest_in_names=dest_names,
        )
        fraction.finish()
        return dest_well, n, mism

    ds1_wells = sorted(set(in1) | set(csv1) | set(zip1))
    ds2_wells = sorted(set(in2) | set(csv2) | set(zip2))
    total_units = len(ds1_wells) + len(ds2_wells)
    with ThreadPoolExecutor(max_workers=pick_workers(workers, total_units)) as ex:
        futures = [ex.submit(_merge_ds1_well, w) for w in ds1_wells]
        futures += [ex.submit(_merge_ds2_well, w) for w in ds2_wells]
        for fut in as_completed(futures):
            dest_well, n, mism = fut.result()
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
