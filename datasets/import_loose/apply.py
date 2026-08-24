"""Qt-free (mostly) execution of an Import Loose Folder conversion.

Two modes, one code path each:

* **New destination** (default) — group matched files by well
  (:func:`~datasets.import_loose.import_loose_compute.group_files_by_well`,
  the same walk the preview uses) and write `<dest>/in/<WELL>.zip` directly
  via :func:`util.zip_write.atomic_zip`, never publishing a member-less
  archive. The source folder is untouched. Wells are converted in
  parallel — each one is an independent file with its own `atomic_zip`
  handle and no shared state, the same disjoint-ownership shape
  `pipeline.ome_explode.explode_ome_tiff` uses for its own per-well thread
  pool.
* **In-place** (opt-in, destructive) — shells out through the existing
  ``services.input_resolution_service.run_zipper`` (which invokes
  ``WellPlateZipper.py`` as a subprocess) so this destructive path stays the
  exact one Analyze already exercises when it finds loose TIFs, rather than
  a second reimplementation of the same grouping. ``run_zipper`` produces
  per-well *folders* (`WellPlateZipper.py` never zips anything itself); this
  module's own :func:`_zip_well_folders` then converts every one of them to
  ``<well>.zip`` so the folder ends up in the same ready-to-run layout as the
  new-destination mode. **Deliberately not**
  ``services.input_resolution_service._consolidate_mixed_to_zips`` — that
  helper only tops up a *mixed* zips+folders state (Analyze's own loose-TIF
  handling, which is happy to read pure per-well folders directly) and is a
  documented no-op when zero zips exist yet, which is exactly what a fresh
  ``run_zipper`` call produces. Calling it here previously left every well as
  a bare folder, never a zip, with no error — the run reported success.

  Its scope is therefore **every** well folder under the source, not only the
  wells grouped out of the loose TIFs: a folder a user (or an earlier tool)
  organised by hand is zipped and has its TIFs deleted too. That is deliberate —
  it is what leaves the folder in one uniform, ready-to-run layout, and it is
  what lets an interrupted run resume, since a crashed attempt's already-grouped
  wells have no loose TIFs left to find. It is also invisible to the preview,
  which is loose-TIF-only and non-recursive, so
  :func:`~datasets.import_loose.import_loose_compute.existing_well_folders`
  measures it with the same walk and both the page and the run's own summary
  name those folders before and after acting on them.

Both modes report per-well progress through the same ``(well, "writing" |
"done")`` callback shape, so :mod:`~datasets.import_loose.dashboard`'s
:class:`~datasets.import_loose.dashboard.ImportLooseDashboard` — a per-well
plate map in the same style Analyze's own ``RunDashboard`` uses — can drive
off one signal regardless of which mode is running.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from util.well_token import canonical_well_label
from util.zip_write import atomic_zip, replace_folder_with_archive
from datasets._common.concurrency import pick_workers
from datasets._common.containers import WellFraction
from datasets._common.dataset_paths import DatasetPaths, destination_has_content
from datasets.import_loose.import_loose_compute import (
    existing_well_folders,
    group_files_by_well,
)


def _null_log(_message: str) -> None:
    return None


def _null_cancelled() -> bool:
    return False


def _null_progress(_well: str, _stage: str) -> None:
    return None


def _null_sub_progress(_well: str, _done: int, _total: int) -> None:
    return None


# Unlike the copy tools, a well here is written in exactly one pass — its
# loose files go straight into one archive — so its dial needs only a single
# :class:`WellFraction` slice. The class is still used rather than raw file
# counts so the two conversion modes report on the same scale as each other
# and as the other Datasets tools, and so a well holding thousands of files
# reports a bounded number of updates instead of one per file.
_SLICES_PER_WELL = 1


def _check_new_destination(dest_root: Path | str) -> None:
    """Refuse a destination that already has any file under ``in``/``out``.

    Unlike Concatenate/Subset/Merge Plates, this mode writes directly via
    ``atomic_zip`` with no destination-state check at all — ``atomic_zip``
    publishes onto ``<dest>/in/<WELL>.zip`` unconditionally on success, so
    pointing this mode at an *existing analyzed dataset* silently overwrote
    that well's raw-input archive with the loose folder's (likely unrelated)
    images, while ``out/`` — its CSVs, QC zips, ``pipeline_info.json`` — was
    left untouched: a dataset whose measurements no longer correspond to its
    inputs, with the run still reporting success.
    """
    dest = Path(dest_root)
    paths = DatasetPaths(root=dest, in_dir=dest / "in", out_dir=dest / "out")
    if destination_has_content(paths):
        raise ValueError(
            f"The destination ({dest}) already has files under 'in' or "
            "'out' — choose a new, empty folder."
        )


def convert_to_new_destination(
    search_dir: Path | str,
    dest_root: Path | str,
    *,
    filename_schema: str,
    filename_sep: str,
    recursive: bool = False,
    workers: int = 0,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str, str], None] | None = None,
    sub_progress: Callable[[str, int, int], None] | None = None,
) -> str:
    """Write ``<dest_root>/in/<WELL>.zip`` for every well matched under
    *search_dir*, leaving *search_dir* untouched. Returns a summary line.

    *workers* threads convert wells in parallel (0 = auto — see
    :func:`_pick_workers`). ``log``/``progress`` are called under a lock so
    two wells' events are never interleaved or racing — a caller's own
    closure (e.g. ``ImportLooseWorker``'s ``done`` counter) can keep
    mutating a plain, un-locked variable across calls exactly as it does
    today, since only one well's callback runs at a time.

    ``sub_progress`` reports ``(well, done, total)`` as that well's files go
    into its archive, so a dashboard can draw the well as a filling dial
    instead of a binary writing/done state. It is deliberately *outside* the
    callback lock: every call names its own well, so interleaving is
    harmless, and taking the lock once per file would serialise the parallel
    conversion it is reporting on.
    """
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled
    report = progress or _null_progress
    report_fraction = sub_progress or _null_sub_progress

    _check_new_destination(dest_root)

    by_well = group_files_by_well(
        search_dir, filename_schema=filename_schema, filename_sep=filename_sep,
        recursive=recursive,
    )
    if not by_well:
        raise ValueError(
            "No files matched the filename schema — check the schema and "
            "separator against the actual filenames."
        )

    dest_in = Path(dest_root) / "in"
    dest_in.mkdir(parents=True, exist_ok=True)

    callback_lock = threading.Lock()

    def _write_well(well: str) -> None:
        if cancelled():
            raise ValueError("Cancelled.")
        with callback_lock:
            report(well, "writing")
            emit(f"  {well}: {len(by_well[well])} file(s)")
        paths = by_well[well]
        fraction = WellFraction(report_fraction, well, _SLICES_PER_WELL)
        on_file = fraction.slice_progress()
        target = dest_in / f"{well}.zip"
        with atomic_zip(target) as zf:
            for done, path in enumerate(paths, start=1):
                zf.write(path, arcname=path.name)
                on_file(done, len(paths))
        fraction.finish()
        with callback_lock:
            report(well, "done")

    wells = sorted(by_well)
    written_wells = 0
    with ThreadPoolExecutor(max_workers=pick_workers(workers, len(wells))) as ex:
        futures = [ex.submit(_write_well, well) for well in wells]
        for fut in as_completed(futures):
            fut.result()  # re-raises the first well's exception, incl. Cancelled
            written_wells += 1

    return f"Wrote {written_wells} well(s) into {dest_in}."


def _zip_well_folders(
    folder: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
    progress: Callable[[str, str], None] | None = None,
    sub_progress: Callable[[str, int, int], None] | None = None,
) -> int:
    """Convert every ``<well>/`` subfolder of *folder* into ``<well>.zip``,
    removing the folder once packed. Returns the count zipped.

    Unlike ``services.input_resolution_service._consolidate_mixed_to_zips``,
    this always converts every well folder it finds — it doesn't bail out
    just because zero zips exist yet (see this module's docstring for why
    that distinction matters). A well that already has a matching zip is
    left alone (idempotent re-run); a well folder with no TIFs in it is
    skipped rather than publishing a member-less archive. ``progress``, when
    given, fires ``(well, "done")`` once that well's zip lands on disk —
    the same "done" stage :func:`convert_to_new_destination` reports, so one
    dashboard callback works for both conversion modes. ``sub_progress``
    likewise fires ``(well, done, total)`` as that well's TIFs go into its
    archive, matching the other mode's per-well dial. A well that an earlier
    attempt already zipped has no files to walk, so it reports a full dial in
    one call rather than none at all — "already finished" must read as
    finished, not as never started.
    """
    emit = log_fn or _null_log
    report = progress or _null_progress
    report_fraction = sub_progress or _null_sub_progress
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    zipped_wells = {
        canonical_well_label(p.stem)
        for p in entries
        if p.is_file() and p.suffix.lower() == ".zip" and canonical_well_label(p.stem)
    }
    zipped = 0
    for p in sorted(entries):
        if not p.is_dir():
            continue
        well = canonical_well_label(p.name)
        if not well:
            continue
        if well in zipped_wells:
            # Already zipped by an earlier attempt this folder is resuming
            # from -- still report "done" so a well the scan's wellsPlanned
            # included (its loose TIFs hadn't all been moved/deleted yet
            # when the previous attempt crashed) doesn't sit stuck at
            # "writing" on the dashboard forever despite the run succeeding.
            WellFraction(report_fraction, well, _SLICES_PER_WELL).finish()
            report(well, "done")
            continue
        tifs = sorted(
            f for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in (".tif", ".tiff")
        )
        if not tifs:
            emit(f"  {well}: folder holds no TIFs — left as-is (no zip written)\n")
            continue
        target = folder / f"{well}.zip"
        fraction = WellFraction(report_fraction, well, _SLICES_PER_WELL)
        on_file = fraction.slice_progress()
        with atomic_zip(target) as zf:
            for done, t in enumerate(tifs, start=1):
                zf.write(t, arcname=t.name)
                on_file(done, len(tifs))
        fraction.finish()
        # Only the TIFs the archive holds are dropped. This runs against the
        # user's own dataset folder, and the archive takes ``.tif``/``.tiff``
        # only — removing the folder as a unit took every companion file,
        # acquisition log and mask subdirectory with it.
        outcome = replace_folder_with_archive(p, tifs)
        zipped += 1
        tail = (
            ""
            if outcome.folder_removed
            else (f" — folder kept, {len(outcome.kept)} file(s) not archived: "
                  f"{', '.join(outcome.kept)}")
        )
        emit(f"  {well}: folder → {well}.zip ({len(tifs)} file(s)){tail}\n")
        report(well, "done")
    return zipped


def convert_in_place(
    search_dir: Path | str,
    *,
    filename_schema: str,
    filename_sep: str,
    move_source_tifs: bool = False,
    log: Callable[[str], None] | None = None,
    progress: Callable[[str, str], None] | None = None,
    sub_progress: Callable[[str, int, int], None] | None = None,
    proc_hook=None,
) -> str:
    """Group loose TIFs directly under *search_dir* into ``<well>.zip``,
    deleting (or moving) the sources — the same destructive path Analyze
    takes when it finds loose TIFs, exposed here as a standalone step.

    *progress*, when given, reports the same ``(well, "writing"|"done")``
    stages :func:`convert_to_new_destination` does, so one dashboard callback
    drives both conversion modes. ``run_zipper``'s own ``progress_fn`` only
    reports a well once WellPlateZipper.py finishes grouping its files into a
    folder — there is no earlier "started" signal from the subprocess — so
    that single event is reported as "writing" (grouped, awaiting its zip);
    :func:`_zip_well_folders` then reports "done" once that well's zip
    actually lands on disk.

    *sub_progress* is likewise the same ``(well, done, total)`` callback the
    other mode takes, but it only covers the zipping half: ``run_zipper``
    shells out to WellPlateZipper.py, which reports nothing until a whole
    well has been grouped, so there is no per-file signal to forward from the
    grouping half at all. A well therefore sits at an empty dial while it is
    being grouped, then fills as it is zipped.
    """
    from services.input_resolution_service import run_zipper

    emit = log or _null_log
    report = progress or _null_progress

    # Measured *before* run_zipper, because after it every grouped well is a
    # well folder too and the two are no longer distinguishable on disk. These
    # are the folders this run will convert on top of the loose TIFs it was
    # pointed at — the scope the preview's loose-TIF-only, non-recursive scan
    # cannot see, and which was previously acted on without ever being named.
    pre_existing = existing_well_folders(Path(search_dir))
    if pre_existing:
        emit(
            f"  Note: {len(pre_existing)} well folder(s) were already here and "
            f"will be zipped as well: {', '.join(pre_existing)}\n"
        )

    def _on_grouped(well: str) -> None:
        report(well, "writing")

    run_zipper(
        Path(search_dir), Path(search_dir),
        log_fn=emit, progress_fn=_on_grouped, filename_schema=filename_schema,
        filename_sep=filename_sep, proc_hook=proc_hook,
        move_source_tifs=move_source_tifs,
    )
    zipped = _zip_well_folders(
        Path(search_dir), log_fn=emit, progress=report, sub_progress=sub_progress,
    )
    summary = f"Grouped loose TIFs in {search_dir} into {zipped} per-well zip(s)."
    if pre_existing:
        # Folded into the summary, not left as a log line: the raw log is hidden
        # by default, so a run that also emptied folders the user never asked
        # about would say so only where nobody looks. The summary is what the
        # dashboard headline shows and what the Job Queue records for the run.
        summary = (
            f"{summary} {len(pre_existing)} of those well(s) came from folders "
            f"already in the source ({', '.join(pre_existing)}), not from loose "
            "TIFs."
        )
    return summary
