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
from util.acquisition_record import read_record, write_record, filter_record_to_archives
import threading
from util.json_write import write_json_atomic
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from util.aw_safe import swallow
from domain.image_identity import normalize_fov, normalize_tp
from datasets.core.concurrency import pick_workers
from datasets.core.provenance import record_derivation
from datasets.core.containers import (
    FILENAME_COLUMN,
    OVERRIDES_FILENAME,
    OVERRIDES_SCHEMA_VERSION,
    WellFraction,
    clear_destination,
    copy_container_members,
    restamp_overrides,
    unstamped_note,
)
from datasets.subset.subset_compute import (
    IN_PROGRESS_MARKER,
    ROW_KEEP,
    ROW_UNJUDGEABLE,
    SubsetError,
    SubsetPlan,
    in_containers,
    member_matches,
    out_csvs,
    out_zips,
    row_verdict,
    timepoint_kept,
)
from datasets.core.nanowell_tables import carry_nanowell_tables


def _null_log(_message: str) -> None:
    return None


def _null_cancelled() -> bool:
    return False


def _null_progress(_well: str, _stage: str) -> None:
    return None


def _null_sub_progress(_well: str, _done: int, _total: int) -> None:
    return None


# One well's copy is two passes over files — the raw-input container, then the
# QC ``<WELL>_out.zip`` — fused by :class:`WellFraction` into a single
# 0 → 100% dial per well. Each pass is a single filtered publish rather than a
# read followed by a write, because ``copy_container_members`` moves each kept
# member's bytes straight across instead of decoding them into a list first.
_SLICES_PER_WELL = 2


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

    unstamped = restamp_overrides(kept_entries, plan.dest.out_dir)

    payload = {"version": OVERRIDES_SCHEMA_VERSION, "overrides": kept_entries}
    write_json_atomic(plan.dest.out_dir / OVERRIDES_FILENAME, payload, indent=None)
    emit(f"Carried over {len(kept_entries)} cell override(s).{unstamped_note(unstamped)}")
    return len(kept_entries)


def _verify(
    plan: SubsetPlan,
    well_row_counts: dict[str, int],
    kept_members: dict[str, int],
    unjudgeable_rows: dict[str, int],
) -> None:
    """Fail the subset when it wrote no measurement rows, naming the cause it
    can actually see.

    Zero rows had one message — "check that the filter actually matches data in
    the source dataset" — which is right for exactly one of the three ways this
    happens and actively misleading for the other two. A run whose *images*
    matched the same filter in their thousands has a filter that matches data
    fine; what disagrees is the measurements CSV, and sending the user back to
    re-pick timepoints cannot fix that. A run whose rows could not be tested at
    all (:data:`~datasets.subset.subset_compute.ROW_UNJUDGEABLE`) has a CSV
    that states no timepoint anywhere the schema can find one.
    """
    if sum(well_row_counts.values()):
        return
    wells = len(plan.wells_kept)
    unjudgeable = sum(unjudgeable_rows.values())
    members = sum(kept_members.values())
    if unjudgeable:
        raise SubsetError(
            "Verification failed: the subset wrote zero measurement rows across "
            f"{wells} well(s), and {unjudgeable} row(s) could not be tested "
            "against the filter at all — their 'timepoint_hours' and 'timepoint' "
            "columns are both blank or unparseable and their 'filename' column "
            "does not resolve against this dataset's schema. The filter is not "
            "the problem; the measurements CSVs are."
        )
    if members:
        raise SubsetError(
            "Verification failed: the subset wrote zero measurement rows across "
            f"{wells} well(s), even though {members} image(s) matched the same "
            "filter — the measurements CSVs and the image filenames disagree "
            "about timepoint/FOV, so no row could match a filter every image "
            "did."
        )
    raise SubsetError(
        "Verification failed: the subset wrote zero measurement rows across "
        f"{wells} well(s) — check that the filter actually "
        "matches data in the source dataset."
    )


def execute_plan(
    plan: SubsetPlan,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str, str], None] | None = None,
    workers: int = 0,
    sub_progress: Callable[[str, int, int], None] | None = None,
) -> str:
    """Carry out *plan*, writing the filtered dataset. Returns a summary line.

    Writes :data:`~datasets.subset.subset_compute.IN_PROGRESS_MARKER` first
    and removes it only after the narrowed sidecar is written, mirroring
    Concatenate's restart-detection discipline.

    *progress* receives ``(well, stage)`` as each well starts and finishes;
    *sub_progress* receives ``(well, done, total)`` repeatedly *within* one
    well, so a dashboard can render that well as a filling dial rather than a
    binary copying/done state. The two passes a well takes (the raw input, then
    the QC archive) are fused into one sweep from 0 to *total* by
    :class:`~datasets.core.containers.WellFraction` — without that, each
    pass would fill the same dial from empty on its own.

    Wells are copied in parallel — *workers* threads (0 = auto, see
    :func:`datasets.core.concurrency.pick_workers`), each owning one
    well's own destination files with nothing shared between wells. ``log``/
    ``progress`` calls before a well's work starts are serialised behind a
    lock; aggregating each well's result back into ``well_row_counts``/etc.
    happens on this function's own thread as each future completes, so that
    bookkeeping needs no lock. ``sub_progress`` is deliberately *not* under
    that lock: every call already names the well it belongs to, so two wells'
    fractions interleaving is harmless, and holding a lock once per member
    would serialise the parallel copy it is reporting on.
    """
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled
    report = progress or _null_progress
    report_fraction = sub_progress or _null_sub_progress

    acquisition = read_record(plan.src.root)

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
    kept_keys_by_well: dict[str, set[tuple[str, str]]] = {}
    kept_members_by_well: dict[str, int] = {}
    unjudgeable_by_well: dict[str, int] = {}

    callback_lock = threading.Lock()

    def _copy_one_well(well: str):
        if cancelled():
            raise SubsetError("Cancelled — the in-progress destination was left in place to resume later.")
        with callback_lock:
            emit(f"  {well}…")
            report(well, "copying")

        fraction = WellFraction(report_fraction, well, _SLICES_PER_WELL)

        def _keep(_container: Path, name: str) -> str | None:
            """Subset renames nothing — a member either keeps its own name or
            is left out, which is what makes an unfiltered well a whole-file
            copy rather than a re-pack."""
            if member_matches(name, schema=plan.schema, channels=plan.channels, filt=plan.filt):
                return name
            return None

        # ``None`` means "there was no raw-input container to compare a row
        # against"; an *empty* set means "there was one and the filter kept
        # nothing from it". Those were one value, and the row check below read
        # the empty set as the first case — so the one archive that certainly
        # cannot hold a row's image was the one archive whose rows went
        # unchecked, and the subset reported success with a CSV naming files
        # its own in/ folder does not contain.
        container = in_map.get(well)
        kept_in_names: frozenset[str] | None = None
        if container is not None:
            kept_in_names, _dropped = copy_container_members(
                plan.dest.in_dir / f"{well}.zip", [container], _keep,
                progress=fraction.slice_progress(),
            )
        else:
            fraction.skip()

        out_zip = zip_map.get(well)
        if out_zip is not None:
            copy_container_members(
                plan.dest.out_dir / f"{well}_out.zip", [out_zip], _keep,
                progress=fraction.slice_progress(),
            )
        else:
            fraction.skip()

        csv_path = csv_map.get(well)
        row_count = 0
        unjudgeable = 0
        keys: set[tuple[str, str]] = set()
        if csv_path is not None:
            rows, fieldnames = _read_csv(csv_path)
            verdicts = [
                row_verdict(r, plan.filt, schema=plan.schema, channels=plan.channels)
                for r in rows
            ]
            unjudgeable = sum(1 for v in verdicts if v == ROW_UNJUDGEABLE)
            kept_rows = [r for r, v in zip(rows, verdicts) if v == ROW_KEEP]
            if kept_rows:
                _write_rows(plan.dest.out_dir / csv_path.name, fieldnames, kept_rows)
                row_count = len(kept_rows)
                for row in kept_rows:
                    fname = row.get(FILENAME_COLUMN)
                    if fname and kept_in_names is not None and fname not in kept_in_names:
                        raise SubsetError(
                            f"Verification failed: well {well}'s row references "
                            f"{fname!r}, which isn't in the filtered in/{well}.zip"
                            + (
                                " — the filter kept no member of that archive at "
                                "all, so the destination has no in/"
                                f"{well}.zip for the row to point into."
                                if not kept_in_names
                                else "."
                            )
                        )
                    # cell_overrides.json stores fov/tp already normalized (via
                    # CellKey.as_tuple, domain/image_identity.py), so
                    # this set must be normalized the same way or a parseable
                    # timepoint label like "24h" never matches its normalized
                    # "24" and _copy_filtered_overrides drops the override.
                    keys.add((normalize_fov(row.get("fov", "")), normalize_tp(row.get("timepoint", ""))))
        # The CSV filter has no member-level sub-steps of its own, so the
        # well's dial closes here rather than lingering at whatever the last
        # container pass left it on.
        fraction.finish()
        return well, row_count, keys, len(kept_in_names or ()), unjudgeable

    with ThreadPoolExecutor(max_workers=pick_workers(workers, len(plan.wells_kept))) as ex:
        futures = [ex.submit(_copy_one_well, well) for well in plan.wells_kept]
        for fut in as_completed(futures):
            well, row_count, keys, kept_members, unjudgeable = fut.result()
            well_row_counts[well] = row_count
            kept_keys_by_well[well] = keys
            kept_members_by_well[well] = kept_members
            unjudgeable_by_well[well] = unjudgeable
            report(well, "done" if row_count else "empty")

    _verify(plan, well_row_counts, kept_members_by_well, unjudgeable_by_well)
    _copy_filtered_overrides(plan, kept_keys_by_well, emit)
    nanowell_rows = carry_nanowell_tables(
        plan.src.out_dir, plan.dest.out_dir, {well: well for well in plan.wells_kept},
        keep_timepoint=None if plan.filt.timepoint_hours is None else (lambda tp: timepoint_kept(tp, plan.filt)))
    if nanowell_rows:
        emit(f"Carried the nanowell tables of {len(nanowell_rows)} well(s) "
             f"({sum(nanowell_rows.values()):,} site rows).")
    if acquisition is not None:
        write_record(plan.dest.root, filter_record_to_archives(
            plan.dest.root, acquisition, schema=":".join(plan.schema.fields), sep=plan.schema.sep))

    write_json_atomic(plan.dest.out_dir / "pipeline_info.json", plan.merged_info)
    marker_path.unlink(missing_ok=True)
    emit("Wrote subset pipeline_info.json.")
    record_derivation(plan.dest, tool="subset", sources=(plan.src,),
                      wells=sorted(plan.wells_kept))

    return f"Subset written to {plan.dest.root} ({len(plan.wells_kept)} well(s))."
