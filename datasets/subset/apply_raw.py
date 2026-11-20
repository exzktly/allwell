"""Qt-free execution of a Subset plan whose source holds only ``in/`` — into a new folder, or in place.

A raw dataset is its per-well archives plus what sits beside them in ``in/``: the acquisition record
(``acquisition.json``), the nanowell anchors set by hand, and at the root the converters' record of short wells
(``INCOMPLETE_WELLS.txt``). Each of those describes the images, so each is cut to the images kept — the record
through ``util.acquisition_record.filter_record_to_archives``, which reads the archives as written rather than
trusting the filter, and the anchors and the short-well record by well (and the anchors by timepoint). Nothing
is written under ``out/``: a subset of an unanalysed plate is an unanalysed plate.

**In place** the archives are the source's own. Each kept well is rewritten alone — its kept members copied to
a hidden staging file in ``in/``, which then replaces the archive by an atomic rename — so the extra space an
edit needs at any moment is the size of the wells being rewritten then, never a second copy of the dataset, and
nothing removed is kept anywhere. A well left with no image is deleted; a well every member of which is kept is
not touched. :data:`~datasets.subset.subset_compute.IN_PLACE_MARKER` is written before the first archive changes
and removed after the record is cut, so an interrupted edit is visible, is refused by a copy, and finishes when
the same edit is run again: every archive is then either untouched or already cut, and cutting a cut archive
again changes nothing.
"""

from __future__ import annotations

import os
import threading
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from datasets.core.concurrency import pick_workers
from datasets.core.containers import WellFraction, clear_destination, copy_container_members
from datasets.core.convert_marker import DONE_MARKER, read_gap_record, write_gap_record
from datasets.core.nanowell_anchors_carry import AnchorSource, carry_anchors
from datasets.subset.subset_compute import (
    IN_PLACE_MARKER,
    IN_PROGRESS_MARKER,
    SubsetError,
    SubsetPlan,
    in_containers,
    member_matches,
    timepoint_kept,
)
from util.acquisition_record import (
    AcquisitionRecordError,
    filter_record_to_archives,
    read_record,
    write_record,
)
from util.aw_safe import swallow
from util.image_exts import is_run_input_member
from util.json_write import write_json_atomic
from util.nanowell_anchors import anchors_path
from util.well_token import canonical_well_label

#: The suffix of a well archive being rewritten in place. Hidden and not ``.zip``, so no scan of ``in/`` reads
#: a half-written one as a well (``util.dir_scan.zip_files_in`` skips both), and the next run can sweep it.
STAGING_SUFFIX = ".subset-staging"


def _images(names) -> int:
    return sum(1 for name in names if not name.endswith("/") and is_run_input_member(name))


def _keep_fn(plan: SubsetPlan):
    def keep(_container: Path, name: str) -> str | None:
        if member_matches(name, schema=plan.schema, channels=plan.channels, filt=plan.filt):
            return name
        return None
    return keep


def _sweep_staging(in_dir: Path) -> None:
    for leftover in in_dir.glob(f".*{STAGING_SUFFIX}"):
        with swallow(f"remove the interrupted rewrite {leftover.name}"):
            leftover.unlink()


def _cut_in_place(plan: SubsetPlan, well: str, archive: Path, progress) -> tuple[int, int]:
    """Cut one archive of the source to the filter; ``(images kept, images removed)``."""
    try:
        with zipfile.ZipFile(archive) as zf:
            names = [info.filename for info in zf.infolist()]
    except (OSError, zipfile.BadZipFile) as exc:
        raise SubsetError(f"Well {well}'s archive {archive.name} can't be read ({exc}); nothing in it was "
                          "changed.") from exc
    keep = _keep_fn(plan)
    kept = [name for name in names if keep(archive, name) is not None]
    before, after = _images(names), _images(kept)
    if len(kept) == len(names):
        progress(1, 1)
        return after, 0
    if after == 0:
        archive.unlink()
        progress(1, 1)
        return 0, before
    staging = archive.with_name(f".{archive.name}{STAGING_SUFFIX}")
    staging.unlink(missing_ok=True)
    copy_container_members(staging, [archive], keep, progress=progress, durable=True)
    os.replace(staging, archive)
    return after, before - after


def _copy_well(plan: SubsetPlan, well: str, container: Path, progress) -> int:
    dest = plan.dest.in_dir / f"{well}.zip"
    written, _dropped = copy_container_members(dest, [container], _keep_fn(plan), progress=progress)
    kept = _images(written)
    if not kept and dest.exists():
        # Only an archive's non-image members (a folder entry, a sidecar) matched: that is not a well.
        dest.unlink()
    return kept


def _carry_sidecars(plan: SubsetPlan, record, kept_wells: set[str], emit: Callable[[str], None]) -> None:
    """Cut the acquisition record, the anchors and the short-well record to what the destination holds."""
    if record is not None:
        info = plan.raw_source.info
        try:
            write_record(plan.dest.root, filter_record_to_archives(
                plan.dest.root, record, schema=info["schema"], sep=info["separator"]))
        except AcquisitionRecordError as exc:
            raise SubsetError(f"The acquisition record could not be cut to the images kept: {exc}") from exc
        emit("Cut the acquisition record (in/acquisition.json) to the images kept.")
    had_anchors = anchors_path(plan.src.in_dir).exists()
    try:
        carried = carry_anchors(
            [AnchorSource(plan.src.in_dir, well=lambda w: w if w in kept_wells else None,
                          timepoint=lambda tp: tp if timepoint_kept(tp, plan.filt) else None)],
            plan.dest.in_dir, emit)
    except ValueError as exc:
        raise SubsetError(str(exc)) from exc
    if plan.in_place and had_anchors and not carried:
        anchors_path(plan.dest.in_dir).unlink(missing_ok=True)
    gaps = read_gap_record(plan.src.root)
    if gaps:
        write_gap_record(plan.dest.root, {w: n for w, n in gaps.items() if w in kept_wells})
        if plan.filt.axes:
            emit("Note: INCOMPLETE_WELLS.txt still counts the images each well was short of before the subset; "
                 "some of them may have been in what was removed.")


def execute_raw_plan(
    plan: SubsetPlan,
    emit: Callable[[str], None],
    cancelled: Callable[[], bool],
    report: Callable[[str, str], None],
    report_fraction: Callable[[str, int, int], None],
    workers: int = 0,
) -> str:
    """Carry out a raw *plan* (``plan.raw``); see the module docstring. Returns a summary line."""
    record = None
    try:
        record = read_record(plan.src.root)
    except AcquisitionRecordError as exc:
        raise SubsetError(f"The acquisition record (in/acquisition.json) can't be read: {exc}") from exc

    root = plan.dest.root
    if plan.in_place:
        write_json_atomic(root / IN_PLACE_MARKER, {
            "schema": plan.raw_source.info.get("schema", ""),
            "separator": plan.raw_source.info.get("separator", ""),
            "filter": plan.filt.as_json(),
        })
        _sweep_staging(plan.src.in_dir)
        emit("Resuming the interrupted in-place edit." if plan.in_place_resume
             else f"Editing {root} in place. Removed images are not kept anywhere.")
    else:
        if plan.dest_is_restart:
            emit("Destination has leftover files from an earlier attempt — clearing.")
            clear_destination(plan.dest, sources=(plan.src,))
        root.mkdir(parents=True, exist_ok=True)
        (root / IN_PROGRESS_MARKER).write_text("")
        plan.dest.in_dir.mkdir(parents=True, exist_ok=True)

    containers = in_containers(plan.src)
    lock = threading.Lock()

    def one(well: str) -> tuple[str, int, int]:
        if cancelled():
            raise SubsetError("Cancelled — run the same subset again to finish it."
                              if plan.in_place else
                              "Cancelled — the in-progress destination was left in place to resume later.")
        with lock:
            emit(f"  {well}…")
            report(well, "copying")
        fraction = WellFraction(report_fraction, well, 1)
        container = containers[well]
        if plan.in_place:
            kept, removed = _cut_in_place(plan, well, container, fraction.slice_progress())
        else:
            kept, removed = _copy_well(plan, well, container, fraction.slice_progress()), 0
        fraction.finish()
        return well, kept, removed

    kept_by_well: dict[str, int] = {}
    removed = 0
    with ThreadPoolExecutor(max_workers=pick_workers(workers, len(plan.wells_kept))) as ex:
        for fut in as_completed([ex.submit(one, well) for well in plan.wells_kept]):
            well, kept, gone = fut.result()
            kept_by_well[well] = kept
            removed += gone
            report(well, "done" if kept else "empty")

    dropped_wells = sorted(set(containers) - set(plan.wells_kept)) if plan.in_place else []
    for well in dropped_wells:
        archive = containers[well]
        with swallow(f"count the images of {archive.name} before deleting it"):
            with zipfile.ZipFile(archive) as zf:
                removed += _images(zf.namelist())
        archive.unlink()
    if dropped_wells:
        emit(f"Deleted the archives of {len(dropped_wells)} well(s): {', '.join(dropped_wells)}.")

    if not plan.in_place and not sum(kept_by_well.values()):
        raise SubsetError("Nothing in the source matched the requested filter, so no image was copied.")

    kept_wells = {canonical_well_label(w) or w for w, n in kept_by_well.items() if n}
    _carry_sidecars(plan, record, kept_wells, emit)
    if plan.in_place:
        if plan.filt.axes:
            # The converters' record of the settings that built this folder lets a later conversion add wells to
            # it. With timepoints, fields or channels gone, a well added that way would hold what the others no
            # longer do, so the folder is no longer that conversion's to extend.
            with swallow(f"remove {DONE_MARKER}"):
                (root / DONE_MARKER).unlink(missing_ok=True)
        (root / IN_PLACE_MARKER).unlink(missing_ok=True)
        emptied = sorted(w for w, n in kept_by_well.items() if not n)
        note = f"; {len(emptied)} well(s) left with no image were deleted" if emptied else ""
        return (f"Edited {root} in place: removed {removed:,} image(s), kept {sum(kept_by_well.values()):,} "
                f"in {len(kept_wells)} well(s){note}.")
    (root / IN_PROGRESS_MARKER).unlink(missing_ok=True)
    return f"Subset written to {root} ({len(kept_wells)} well(s), {sum(kept_by_well.values()):,} image(s))."
