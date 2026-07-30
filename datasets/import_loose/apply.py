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
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from util.well_token import canonical_well_label
from util.zip_write import atomic_zip
from datasets._common.concurrency import pick_workers
from datasets.import_loose.import_loose_compute import group_files_by_well


def _null_log(_message: str) -> None:
    return None


def _null_cancelled() -> bool:
    return False


def _null_progress(_well: str, _stage: str) -> None:
    return None


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
) -> str:
    """Write ``<dest_root>/in/<WELL>.zip`` for every well matched under
    *search_dir*, leaving *search_dir* untouched. Returns a summary line.

    *workers* threads convert wells in parallel (0 = auto — see
    :func:`_pick_workers`). ``log``/``progress`` are called under a lock so
    two wells' events are never interleaved or racing — a caller's own
    closure (e.g. ``ImportLooseWorker``'s ``done`` counter) can keep
    mutating a plain, un-locked variable across calls exactly as it does
    today, since only one well's callback runs at a time.
    """
    emit = log or _null_log
    cancelled = is_cancelled or _null_cancelled
    report = progress or _null_progress

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
        target = dest_in / f"{well}.zip"
        with atomic_zip(target) as zf:
            for path in by_well[well]:
                zf.write(path, arcname=path.name)
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


def _zip_well_folders(folder: Path, *, log_fn: Callable[[str], None] | None = None) -> int:
    """Convert every ``<well>/`` subfolder of *folder* into ``<well>.zip``,
    removing the folder once packed. Returns the count zipped.

    Unlike ``services.input_resolution_service._consolidate_mixed_to_zips``,
    this always converts every well folder it finds — it doesn't bail out
    just because zero zips exist yet (see this module's docstring for why
    that distinction matters). A well that already has a matching zip is
    left alone (idempotent re-run); a well folder with no TIFs in it is
    skipped rather than publishing a member-less archive.
    """
    emit = log_fn or _null_log
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
        if not well or well in zipped_wells:
            continue
        tifs = sorted(
            f for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in (".tif", ".tiff")
        )
        if not tifs:
            emit(f"  {well}: folder holds no TIFs — left as-is (no zip written)\n")
            continue
        target = folder / f"{well}.zip"
        with atomic_zip(target) as zf:
            for t in tifs:
                zf.write(t, arcname=t.name)
        shutil.rmtree(p, ignore_errors=True)
        zipped += 1
        emit(f"  {well}: folder → {well}.zip ({len(tifs)} file(s))\n")
    return zipped


def convert_in_place(
    search_dir: Path | str,
    *,
    filename_schema: str,
    filename_sep: str,
    move_source_tifs: bool = False,
    log: Callable[[str], None] | None = None,
    progress: Callable[[str], None] | None = None,
    proc_hook=None,
) -> str:
    """Group loose TIFs directly under *search_dir* into ``<well>.zip``,
    deleting (or moving) the sources — the same destructive path Analyze
    takes when it finds loose TIFs, exposed here as a standalone step.
    """
    from services.input_resolution_service import run_zipper

    emit = log or _null_log
    run_zipper(
        Path(search_dir), Path(search_dir),
        log_fn=emit, progress_fn=progress, filename_schema=filename_schema,
        filename_sep=filename_sep, proc_hook=proc_hook,
        move_source_tifs=move_source_tifs,
    )
    zipped = _zip_well_folders(Path(search_dir), log_fn=emit)
    return f"Grouped loose TIFs in {search_dir} into {zipped} per-well zip(s)."
