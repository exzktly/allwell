"""Qt-free (mostly) execution of an Import Loose Folder conversion.

Two modes, one code path each:

* **New destination** (default) — group matched files by well
  (:func:`~datasets.import_loose.import_loose_compute.group_files_by_well`,
  the same walk the preview uses) and write `<dest>/in/<WELL>.zip` directly
  via :func:`util.zip_write.atomic_zip`, never publishing a member-less
  archive. The source folder is untouched.
* **In-place** (opt-in, destructive) — shells out through the existing
  ``services.input_resolution_service.run_zipper`` (which invokes
  ``WellPlateZipper.py`` as a subprocess) so this destructive path stays the
  exact one Analyze already exercises when it finds loose TIFs, rather than
  a second reimplementation of the same grouping. ``run_zipper`` produces
  per-well folders; ``_consolidate_mixed_to_zips`` then normalises them to
  ``<well>.zip`` so the folder ends up in the same ready-to-run layout as
  the new-destination mode.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from util.zip_write import atomic_zip
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
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> str:
    """Write ``<dest_root>/in/<WELL>.zip`` for every well matched under
    *search_dir*, leaving *search_dir* untouched. Returns a summary line.
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

    written_wells = 0
    for well in sorted(by_well):
        if cancelled():
            raise ValueError("Cancelled.")
        report(well, "writing")
        emit(f"  {well}: {len(by_well[well])} file(s)")
        target = dest_in / f"{well}.zip"
        with atomic_zip(target) as zf:
            for path in by_well[well]:
                zf.write(path, arcname=path.name)
        written_wells += 1
        report(well, "done")

    return f"Wrote {written_wells} well(s) into {dest_in}."


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
    from services.input_resolution_service import _consolidate_mixed_to_zips, run_zipper

    emit = log or _null_log
    run_zipper(
        Path(search_dir), Path(search_dir),
        log_fn=emit, progress_fn=progress, filename_schema=filename_schema,
        filename_sep=filename_sep, proc_hook=proc_hook,
        move_source_tifs=move_source_tifs,
    )
    _consolidate_mixed_to_zips(Path(search_dir), log_fn=emit)
    return f"Grouped loose TIFs in {search_dir} into per-well zips."
