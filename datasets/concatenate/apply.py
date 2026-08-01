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
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from util.timepoint import parse_hours
from util.aw_safe import swallow
from well_viewer.domain.tp_match import tp_close
from datasets._common.concurrency import pick_workers
from datasets._common.containers import (
    FILENAME_COLUMN as _FILENAME_COLUMN,
    OVERRIDES_FILENAME as _OVERRIDES_FILENAME,
    OVERRIDES_SCHEMA_VERSION as _OVERRIDES_SCHEMA_VERSION,
    TIMEPOINT_COLUMN as _TIMEPOINT_COLUMN,
    TIMEPOINT_HOURS_COLUMN as _TIMEPOINT_HOURS_COLUMN,
    WellFraction,
    clear_destination,
    read_members,
    write_zip,
)
from datasets.concatenate.concatenate_compute import (
    IN_PROGRESS_MARKER,
    ConcatError,
    MergePlan,
    in_containers,
    out_csvs,
    out_zips,
    remap_timepoint_label,
    rewrite_member_name,
)


# A no-op progress sink, so callers that don't care can omit one.
def _null_log(_message: str) -> None:
    return None


def _null_cancelled() -> bool:
    return False


def _null_progress(_well: str, _stage: str) -> None:
    return None


def _null_sub_progress(_well: str, _done: int, _total: int) -> None:
    return None


# Merging one well is six passes over files: for the raw input, read dataset
# 2's archive, read dataset 1's, then write the merged one — and the same
# three again for the QC ``<WELL>_out.zip``. :class:`WellFraction` fuses them
# into a single 0 → 100% dial per well, so the well's dashboard cell fills
# once rather than six times over.
_INPUT_SLICES = 3
_QC_SLICES = 3
_SLICES_PER_WELL = _INPUT_SLICES + _QC_SLICES


def _detached_fraction(well: str) -> WellFraction:
    """A :class:`WellFraction` that reports nowhere — for a direct call to
    :func:`merge_input` / :func:`merge_qc_zip` that has no dashboard behind
    it (both are called with a real one by :func:`execute_plan`)."""
    return WellFraction(_null_sub_progress, well, _SLICES_PER_WELL)


# ── container IO ─────────────────────────────────────────────────────────────
# read_members / write_zip now live in datasets._common.containers — see the
# import block above.
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


def merge_input(
    plan: MergePlan, well: str, c1: Path | None, c2: Path | None,
    *, fraction: WellFraction | None = None,
) -> tuple[int, frozenset[str] | None, frozenset[str]]:
    """Merge a well's raw-input container into the destination ``in/``.

    Returns ``(unmatched_member_count, written_names, dropped_names)`` —
    *written_names* is ``None`` when the well came only from dataset 1 (a pure
    byte-copy, so there's nothing dataset-2-derived to check filenames
    against). *dropped_names* is the set of member names that collided and
    were silently skipped by :func:`write_zip` — nonzero here means real data
    never made it into the merged archive and must not be treated as success
    (see :func:`execute_plan` and :func:`_verify`).

    *fraction* owns this well's dashboard dial; this pass claims its first
    :data:`_INPUT_SLICES` slices — dataset 2's read, dataset 1's read, and the
    merged write, in that order. Any of the three that has no work (the well
    is on one side only, or the whole container is copied byte-for-byte) is
    closed out with ``skip`` rather than left unclaimed, so a well that is in
    fact finished doesn't leave its dial short.
    """
    frac = fraction if fraction is not None else _detached_fraction(well)
    dest_zip = plan.dest.in_dir / f"{well}.zip"
    if c1 and not c2:
        _copy_container_as_is(c1, plan.dest.in_dir)
        frac.skip(_INPUT_SLICES)
        return 0, None, frozenset()
    if c2 and not c1:
        rewritten, unmatched = _rewrite_members(plan, read_members(c2, progress=frac.slice_progress()))
        frac.skip()  # no dataset-1 archive on this well
        written, dropped = write_zip(dest_zip, rewritten, progress=frac.slice_progress())
        return unmatched, written, dropped
    if c1 and c2:
        rewritten, unmatched = _rewrite_members(plan, read_members(c2, progress=frac.slice_progress()))
        members1 = read_members(c1, progress=frac.slice_progress())
        written, dropped = write_zip(dest_zip, members1 + rewritten, progress=frac.slice_progress())
        return unmatched, written, dropped
    frac.skip(_INPUT_SLICES)
    return 0, None, frozenset()


def merge_qc_zip(
    plan: MergePlan, well: str, z1: Path | None, z2: Path | None,
    *, fraction: WellFraction | None = None,
) -> tuple[int, frozenset[str]]:
    """Merge a well's ``<WELL>_out.zip`` QC archive into the destination
    ``out/``. Returns ``(unmatched_member_count, dropped_names)`` (see
    :func:`merge_input`, whose *fraction* contract this mirrors — this pass
    claims the well's remaining :data:`_QC_SLICES` slices)."""
    frac = fraction if fraction is not None else _detached_fraction(well)
    dest_zip = plan.dest.out_dir / f"{well}_out.zip"
    if z1 and not z2:
        shutil.copy2(z1, dest_zip)
        frac.skip(_QC_SLICES)
        return 0, frozenset()
    if z2 and not z1:
        rewritten, unmatched = _rewrite_members(plan, read_members(z2, progress=frac.slice_progress()))
        frac.skip()  # no dataset-1 QC archive on this well
        _written, dropped = write_zip(dest_zip, rewritten, progress=frac.slice_progress())
        return unmatched, dropped
    if z1 and z2:
        rewritten, unmatched = _rewrite_members(plan, read_members(z2, progress=frac.slice_progress()))
        members1 = read_members(z1, progress=frac.slice_progress())
        _written, dropped = write_zip(dest_zip, members1 + rewritten, progress=frac.slice_progress())
        return unmatched, dropped
    frac.skip(_QC_SLICES)
    return 0, frozenset()


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
# clear_destination now lives in datasets._common.containers — see the import
# block above.


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
def _verify(
    plan: MergePlan, well_stats: dict[str, WellCsvStats],
    dropped_members: dict[str, frozenset[str]] | None = None,
) -> None:
    """Raise :class:`ConcatError` if the merged output doesn't actually plot
    correctly — this is the check that would have caught the original bug.
    Runs before ``pipeline_info.json`` is written (see :func:`execute_plan`),
    so a failure never leaves a destination that looks finished.

    *dropped_members* — per-well sets of zip-member names that collided
    during the merge and were silently discarded by :func:`write_zip`. A
    merge that discarded real data must never report success, so this is
    checked first: a colliding member usually means a same-named file exists
    on both sides under a name that still "looks fine" (it exists in the
    archive), which is exactly what the ``filename_mismatches`` check below
    cannot detect on its own.
    """
    if dropped_members:
        well, names = next(iter(dropped_members.items()))
        total_dropped = sum(len(v) for v in dropped_members.values())
        raise ConcatError(
            f"Verification failed: {total_dropped} file(s) across "
            f"{len(dropped_members)} well(s) collided by name during the "
            f"merge and were dropped (e.g. well {well}: {sorted(names)[0]!r}) "
            "— dataset 2's data did not survive the merge for these files."
        )

    total_rows = sum(s.rows_ds1 + s.rows_ds2 for s in well_stats.values())
    if well_stats and total_rows == 0:
        raise ConcatError(
            "Verification failed: the merge wrote zero measurement rows "
            f"across {len(well_stats)} well(s) — no measurements_<WELL>.csv "
            "was matched for any well in either dataset (images were still "
            "copied). Check that each dataset's out/ folder actually "
            "contains its measurements CSVs, not just the QC image zips."
        )

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
    progress: Callable[[str, str], None] | None = None,
    workers: int = 0,
    sub_progress: Callable[[str, int, int], None] | None = None,
) -> str:
    """Carry out *plan*, writing the merged dataset. Returns a summary line.

    *log* receives human-readable progress lines (defaults to a no-op).
    *is_cancelled* is polled once per well; when it returns ``True`` the merge
    stops with a plain :class:`ConcatError` and the in-progress marker is left
    in place, so a later re-run is correctly detected as a resumable restart.
    *progress* receives ``(well, stage)`` for each well as it's processed —
    ``stage`` is ``"merging"`` before that well's containers/CSV are merged,
    then ``"merged"`` or ``"warned"`` (image data survived on one side with no
    matching measurements CSV) once it's done — for a live per-well status
    display (defaults to a no-op). *sub_progress* receives ``(well, done,
    total)`` repeatedly *within* one well, so that display can render the well
    as a filling dial rather than a binary merging/merged state; the six
    passes a well takes are fused into one 0-to-*total* sweep by
    :class:`~datasets._common.containers.WellFraction` (see
    :func:`merge_input`). It is called from the worker threads without the
    callback lock: every call names its own well, so interleaving is
    harmless, and locking once per member would serialise the parallel merge
    it is reporting on.

    Wells are merged in parallel — *workers* threads (0 = auto, see
    :func:`datasets._common.concurrency.pick_workers`), each owning one
    well's own destination files (``in/<WELL>.zip``, ``out/<WELL>_out.zip``,
    its measurements CSV) with nothing shared between wells. ``log``/
    ``progress`` calls that happen before a well's work starts are
    serialised behind a lock; aggregating each well's result into
    ``well_stats``/``dropped_members``/etc. and reporting its "merged"/
    "warned" outcome both happen back on this function's own thread as each
    future completes, so that bookkeeping needs no lock at all.

    Writes :data:`~datasets.concatenate.concatenate_compute.IN_PROGRESS_MARKER`
    first and removes it only after the merged sidecar is written — the whole
    restart-detection mechanism (see ``concatenate_compute.destination_has_marker``)
    depends on that ordering, as does never writing ``pipeline_info.json``
    until :func:`_verify` has passed.
    """
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled
    report = progress or _null_progress
    report_fraction = sub_progress or _null_sub_progress

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
    images_without_csv: list[str] = []
    dropped_members: dict[str, frozenset[str]] = {}

    callback_lock = threading.Lock()

    def _merge_one_well(well: str):
        if cancelled():
            raise ConcatError("Cancelled — the in-progress destination was left in place to resume later.")
        with callback_lock:
            emit(f"  {well}…")
            report(well, "merging")
        fraction = WellFraction(report_fraction, well, _SLICES_PER_WELL)
        unmatched_in, in_names, dropped_in = merge_input(
            plan, well, in1.get(well), in2.get(well), fraction=fraction)
        unmatched_qc, dropped_qc = merge_qc_zip(
            plan, well, zip1.get(well), zip2.get(well), fraction=fraction)
        stats = merge_measurements(plan, well, csv1.get(well), csv2.get(well), ds2_in_names=in_names)
        fraction.finish()
        unmatched_total = unmatched_in + unmatched_qc + stats.unmatched_filenames
        well_dropped = dropped_in | dropped_qc
        # A well's images (in/ input or out/ QC zip) existing without a
        # measurements CSV on that same side is legitimate on its own — the
        # well may have segmented zero cells at every timepoint — but it's
        # exactly the situation a naming mismatch in out_csvs() would also
        # produce, silently, for every well. Surface it rather than let a
        # merge that copied images but wrote no data pass as an ordinary
        # success; _verify's zero-rows check below is the hard backstop.
        well_warned = bool(well_dropped)
        well_images_without_csv: list[str] = []
        if (in1.get(well) or zip1.get(well)) and not csv1.get(well):
            well_images_without_csv.append(f"{well} (dataset 1)")
            well_warned = True
        if (in2.get(well) or zip2.get(well)) and not csv2.get(well):
            well_images_without_csv.append(f"{well} (dataset 2)")
            well_warned = True
        return well, stats, unmatched_total, well_dropped, well_images_without_csv, well_warned

    with ThreadPoolExecutor(max_workers=pick_workers(workers, len(all_wells))) as ex:
        futures = [ex.submit(_merge_one_well, well) for well in all_wells]
        for fut in as_completed(futures):
            well, stats, unmatched_total, well_dropped, well_images_without_csv, well_warned = fut.result()
            well_stats[well] = stats
            total_unmatched += unmatched_total
            if well_dropped:
                dropped_members[well] = well_dropped
            images_without_csv.extend(well_images_without_csv)
            report(well, "warned" if well_warned else "merged")

    if total_unmatched:
        emit(
            f"Note: {total_unmatched} file(s) didn't match the filename "
            "schema and were copied unchanged (their timepoint wasn't shifted)."
        )
    if images_without_csv:
        emit(
            f"Note: {len(images_without_csv)} well/dataset pair(s) have image "
            f"data but no measurements CSV — no cells were carried over for "
            f"them: {', '.join(images_without_csv)}."
        )

    emit("Verifying the merged output before finishing…")
    _verify(plan, well_stats, dropped_members)

    _merge_cell_overrides(plan, emit)

    (plan.dest.out_dir / "pipeline_info.json").write_text(json.dumps(plan.merged_info, indent=2))
    marker_path.unlink(missing_ok=True)
    emit("Wrote merged pipeline_info.json.")

    return (
        f"Concatenated {len(all_wells)} well(s) into {plan.dest.root} "
        f"(dataset 2 shifted +{plan.offset_hours:g}h)."
    )
