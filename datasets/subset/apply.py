"""Qt-free execution of a Subset :class:`~datasets.subset.subset_compute.SubsetPlan`.

Per well: filter the raw-input container, the QC out-zip, and the measurements
CSV by the same predicate, publish through the shared container helpers, and
carry forward matching ``cell_overrides.json`` entries with their
segmentation fingerprint re-stamped to the *destination* zip — the same
pattern ``datasets.concatenate.apply._merge_cell_overrides`` already
establishes, copied rather than reinvented (see that module's docstring for
why the re-stamp is correct, not a workaround). The stamp is the **last**
write to a destination out-zip; nothing here rewrites one after.
"""

from __future__ import annotations

import csv
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from util.aw_safe import swallow
from datasets._common.concurrency import pick_workers
from datasets._common.containers import (
    FILENAME_COLUMN,
    OVERRIDES_FILENAME,
    OVERRIDES_SCHEMA_VERSION,
    clear_destination,
    read_members,
    write_zip,
)
from datasets.subset.subset_compute import (
    IN_PROGRESS_MARKER,
    SubsetError,
    SubsetPlan,
    in_containers,
    member_matches,
    out_csvs,
    out_zips,
    row_matches,
)


def _null_log(_message: str) -> None:
    return None


def _null_cancelled() -> bool:
    return False


def _null_progress(_well: str, _stage: str) -> None:
    return None


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


def _copy_filtered_overrides(plan: SubsetPlan, kept_keys: dict[str, set[tuple[str, str]]], emit: Callable[[str], None]) -> int:
    """Carry ``cell_overrides.json`` entries whose ``(fov, tp)`` survived the
    filter for their well, re-stamping ``seg_fp`` from the destination zip.

    Unlike Concatenate (which carries every entry — nothing there is
    filtered out), Subset must drop overrides for filtered-out wells/
    timepoints/FOVs first: an unfiltered carry would stamp a fresh
    fingerprint onto a row pointing at a cell that isn't in the destination
    at all.
    """
    src_path = plan.src.out_dir / OVERRIDES_FILENAME
    if not src_path.exists():
        return 0
    data = None
    with swallow(f"read {src_path}"):
        data = json.loads(src_path.read_text())
    if not isinstance(data, dict):
        return 0
    kept_entries = []
    for entry in data.get("overrides") or []:
        if not isinstance(entry, dict):
            continue
        well = str(entry.get("well", "")).strip()
        fov = str(entry.get("fov", "")).strip()
        tp = str(entry.get("tp", "")).strip()
        if well not in kept_keys or (fov, tp) not in kept_keys[well]:
            continue
        kept_entries.append(dict(entry))

    if not kept_entries:
        return 0

    for entry in kept_entries:
        well = str(entry.get("well", "")).strip()
        zip_path = plan.dest.out_dir / f"{well}_out.zip"
        with swallow(f"stat {zip_path}"):
            entry["seg_fp"] = f"{zip_path.stat().st_mtime_ns}"

    payload = {"version": OVERRIDES_SCHEMA_VERSION, "overrides": kept_entries}
    (plan.dest.out_dir / OVERRIDES_FILENAME).write_text(json.dumps(payload))
    emit(f"Carried over {len(kept_entries)} cell override(s).")
    return len(kept_entries)


def _verify(plan: SubsetPlan, well_row_counts: dict[str, int], in_names_by_well: dict[str, frozenset[str]]) -> None:
    if sum(well_row_counts.values()) == 0:
        raise SubsetError(
            "Verification failed: the subset wrote zero measurement rows across "
            f"{len(plan.wells_kept)} well(s) — check that the filter actually "
            "matches data in the source dataset."
        )


def execute_plan(
    plan: SubsetPlan,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str, str], None] | None = None,
    workers: int = 0,
) -> str:
    """Carry out *plan*, writing the filtered dataset. Returns a summary line.

    Writes :data:`~datasets.subset.subset_compute.IN_PROGRESS_MARKER` first
    and removes it only after the narrowed sidecar is written, mirroring
    Concatenate's restart-detection discipline.

    Wells are copied in parallel — *workers* threads (0 = auto, see
    :func:`datasets._common.concurrency.pick_workers`), each owning one
    well's own destination files with nothing shared between wells. ``log``/
    ``progress`` calls before a well's work starts are serialised behind a
    lock; aggregating each well's result back into ``well_row_counts``/etc.
    happens on this function's own thread as each future completes, so that
    bookkeeping needs no lock.
    """
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled
    report = progress or _null_progress

    if plan.dest_is_restart:
        emit("Destination has leftover files from an earlier attempt — clearing.")
        clear_destination(plan.dest)

    plan.dest.root.mkdir(parents=True, exist_ok=True)
    marker_path = plan.dest.root / IN_PROGRESS_MARKER
    marker_path.write_text("")
    plan.dest.in_dir.mkdir(parents=True, exist_ok=True)
    plan.dest.out_dir.mkdir(parents=True, exist_ok=True)

    in_map = in_containers(plan.src)
    csv_map = out_csvs(plan.src)
    zip_map = out_zips(plan.src)

    well_row_counts: dict[str, int] = {}
    in_names_by_well: dict[str, frozenset[str]] = {}
    kept_keys_by_well: dict[str, set[tuple[str, str]]] = {}

    callback_lock = threading.Lock()

    def _copy_one_well(well: str):
        if cancelled():
            raise SubsetError("Cancelled — the in-progress destination was left in place to resume later.")
        with callback_lock:
            emit(f"  {well}…")
            report(well, "copying")

        container = in_map.get(well)
        kept_in_names: frozenset[str] = frozenset()
        if container is not None:
            members = read_members(container)
            kept = [(n, d) for n, d in members if member_matches(n, schema=plan.schema, filt=plan.filt)]
            if kept:
                kept_in_names, _dropped = write_zip(plan.dest.in_dir / f"{well}.zip", kept)

        out_zip = zip_map.get(well)
        if out_zip is not None:
            members = read_members(out_zip)
            kept = [(n, d) for n, d in members if member_matches(n, schema=plan.schema, filt=plan.filt)]
            if kept:
                write_zip(plan.dest.out_dir / f"{well}_out.zip", kept)

        csv_path = csv_map.get(well)
        row_count = 0
        keys: set[tuple[str, str]] = set()
        if csv_path is not None:
            rows, fieldnames = _read_csv(csv_path)
            kept_rows = [r for r in rows if row_matches(r, plan.filt)]
            if kept_rows:
                _write_rows(plan.dest.out_dir / csv_path.name, fieldnames, kept_rows)
                row_count = len(kept_rows)
                for row in kept_rows:
                    fname = row.get(FILENAME_COLUMN)
                    if fname and kept_in_names and fname not in kept_in_names:
                        raise SubsetError(
                            f"Verification failed: well {well}'s row references "
                            f"{fname!r}, which isn't in the filtered in/{well}.zip."
                        )
                    keys.add((str(row.get("fov", "")), str(row.get("timepoint", ""))))
        return well, row_count, kept_in_names, keys

    with ThreadPoolExecutor(max_workers=pick_workers(workers, len(plan.wells_kept))) as ex:
        futures = [ex.submit(_copy_one_well, well) for well in plan.wells_kept]
        for fut in as_completed(futures):
            well, row_count, kept_in_names, keys = fut.result()
            well_row_counts[well] = row_count
            in_names_by_well[well] = kept_in_names
            kept_keys_by_well[well] = keys
            report(well, "done" if row_count else "empty")

    _verify(plan, well_row_counts, in_names_by_well)
    _copy_filtered_overrides(plan, kept_keys_by_well, emit)

    (plan.dest.out_dir / "pipeline_info.json").write_text(json.dumps(plan.merged_info, indent=2))
    marker_path.unlink(missing_ok=True)
    emit("Wrote subset pipeline_info.json.")

    return f"Subset written to {plan.dest.root} ({len(plan.wells_kept)} well(s))."
