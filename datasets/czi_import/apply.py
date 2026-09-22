"""Qt-free executor for the CZI converter.

Writes the de-interleaved per-plane TIFs into ``<destination>/in/`` by
delegating to :func:`pipeline.czi_explode.explode_czi`, threading the page's
log, progress and cancellation through. All the pixel work lives in the
pipeline modules; this one applies the converter's conventions (the ``dest/in``
layout, the experiment token taken from the filename, the resume marker) and
formats the closing summary.

It also owns the *quantising* of per-well progress. The exploder reports one
tick per plane written; a well of a few thousand planes would be a few thousand
queued Qt signals and plate repaints, so each well's ticks are folded into a
single monotonic 0 → 100 sweep by :class:`datasets.core.containers.WellFraction`
— the same class every other Datasets tool uses for the same job, so a dial
rises the same way whichever tool is filling it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from datasets.core.containers import WellFraction
from datasets.czi_import.czi_import_compute import (
    ConversionParams,
    CziImportError,
    clear_run_marker,
    destination_problem,
    resume_state,
    source_files,
    strip_czi_suffix,
    write_done_marker,
    write_run_marker,
)
from pipeline.czi_explode import explode_czi


def convert(
    src: Path | str,
    dest: Path | str,
    params: ConversionParams,
    *,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    well_progress: Callable[[str, int, int], None] | None = None,
) -> str:
    """Convert *src* into ``<dest>/in`` and return a one-line summary.

    *well_progress* receives ``(well, done, total)`` for one well's share of the
    conversion, quantised to whole percentage steps so a well holding thousands
    of planes emits at most 101 updates rather than one per plane.

    Raises :class:`pipeline.czi_explode.CziExplodeCancelled` if *is_cancelled*
    fires, :class:`~datasets.czi_import.czi_import_compute.CziImportError` when
    the destination cannot be used, and ``ValueError`` for an unsound source or
    an inconsistent mapping — all of them before anything is written.
    """
    src, dest = Path(src), Path(dest)

    # Checked here rather than only in the page, because the page is one of two
    # ways in: a queued job reaches ``convert`` through ``start_from_queue``
    # without the field hint ever being consulted. A conversion writes
    # ``<dest>/in/<WELL>.zip`` and overwrites each archive it produces, so a
    # destination already holding a plate ends up holding two of them with
    # nothing in it saying so — and the ``out/`` measurements beside them then
    # describe inputs that are no longer there. A mismatched resume marker is
    # the same failure by a subtler route: two mappings write identical
    # filenames from different images, so finishing one run with another run's
    # settings stitches one folder from two.
    problem = destination_problem(dest, src, params)
    if problem:
        raise CziImportError(f"{dest}: {problem}")

    files = source_files(src, params)
    # Blank means the source names the experiment, and for a split-scene set
    # that is the *folder* — ``explode_czi`` decides it the same way. Naming a
    # multi-file plate after whichever member sorted first would stamp one
    # scene's well into every archive on the plate.
    experiment = params.experiment
    if not experiment and len(files) == 1:
        experiment = strip_czi_suffix(src.name)

    # A conversion that stopped part-way leaves whole wells behind — each
    # archive is published atomically, so what is there is finished and correct.
    # Finishing the rest is only safe when the settings match the run that wrote
    # them, since two mappings produce the same filenames from different images;
    # the marker is what records them. Written before the first archive and
    # dropped on success, so an interrupted run is the only state it describes.
    resuming = resume_state(dest, src, params).resumable
    write_run_marker(dest, src, params)

    dials: dict[str, Callable[[int, int], None]] = {}

    def _well_progress(well: str, done: int, total: int) -> None:
        # One WellFraction per well, created on that well's first plane. A
        # single slice: unlike a copy tool there is only one pass over a well
        # here, so nothing needs fusing — what is wanted is the dedup.
        report = dials.get(well)
        if report is None:
            report = WellFraction(well_progress, well, 1).slice_progress()
            dials[well] = report
        report(done, total)

    result = explode_czi(
        src,
        dest / "in",
        files=files,
        well_source=params.well_source,
        grid_rows=params.grid_rows,
        grid_cols=params.grid_cols,
        origin_well=params.origin or "A01",
        direction=params.direction,
        serpentine=params.serpentine,
        experiment=experiment,
        fovs_per_well=params.fovs_per_well,
        timepoint_stride=params.timepoint_stride,
        well_subset=params.well_subset,
        workers=params.workers,
        log_fn=log,
        progress_fn=progress,
        well_progress_fn=None if well_progress is None else _well_progress,
        should_abort=is_cancelled,
        resume=resuming,
    )
    # Record what built this folder before dropping the in-progress
    # marker, so a later run adding the wells this one skipped can tell
    # its own dataset from a stranger's plate.
    write_done_marker(dest, src, params)
    clear_run_marker(dest)
    channels = ", ".join(result.channels) if result.channels else "?"
    kept = (f" ({len(result.wells_resumed)} kept from an interrupted run)"
            if result.wells_resumed else "")
    return (
        f"Wrote {result.images_written} image(s) for {len(result.wells)} well(s)"
        f"{kept} (channels: {channels}) into {dest.name}/in — open it in Analyze "
        f"to process."
    )
