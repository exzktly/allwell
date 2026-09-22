"""Qt-free executor for the ND2 converter.

Writes the per-plane TIFs into ``<destination>/in/`` by delegating to
:func:`pipeline.nd2_explode.explode_nd2`, threading the page's log, progress
and cancellation through. All the pixel work lives in the pipeline modules;
this one applies the converter's conventions (the ``dest/in`` layout, the
experiment token taken from the filename, the resume marker) and formats the
closing summary.

It also owns the *quantising* of per-well progress. The exploder reports one
tick per plane written; a well of a few thousand planes would be a few
thousand queued Qt signals and plate repaints, so each well's ticks are folded
into a single monotonic 0 → 100 sweep by
:class:`datasets.core.containers.WellFraction` — the same class every other
Datasets tool uses for the same job, so a dial rises the same way whichever
tool is filling it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from datasets.core.containers import WellFraction
from datasets.nd2_import.nd2_import_compute import (
    ConversionParams,
    Nd2ImportError,
    clear_run_marker,
    destination_problem,
    resume_state,
    strip_nd2_suffix,
    write_run_marker,
)
from pipeline.nd2_explode import explode_nd2


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

    *well_progress* receives ``(well, done, total)`` for one well's share of
    the conversion, quantised to whole percentage steps so a well holding
    thousands of planes emits at most 101 updates rather than one per plane.

    Raises :class:`pipeline.nd2_explode.Nd2ExplodeCancelled` if *is_cancelled*
    fires, :class:`~datasets.nd2_import.nd2_import_compute.Nd2ImportError` when
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
        raise Nd2ImportError(f"{dest}: {problem}")

    # Blank means the file names the experiment. A constant default would give
    # every plate converted here the same token, so two of them concatenated
    # later would be indistinguishable — and the filename is right there.
    experiment = params.experiment or strip_nd2_suffix(src.name)

    # A conversion that stopped part-way leaves whole wells behind — each
    # archive is published atomically, so what is there is finished and
    # correct. Finishing the rest is only safe when the settings match the run
    # that wrote them, since two mappings produce the same filenames from
    # different images; the marker is what records them. Written before the
    # first archive and dropped on success, so an interrupted run is the only
    # state it describes.
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

    result = explode_nd2(
        src,
        dest / "in",
        well_source=params.well_source,
        grid_rows=params.grid_rows,
        grid_cols=params.grid_cols,
        origin_well=params.origin or "A01",
        direction=params.direction,
        serpentine=params.serpentine,
        experiment=experiment,
        fovs_per_well=params.fovs_per_well,
        timepoint_stride=params.timepoint_stride,
        workers=params.workers,
        log_fn=log,
        progress_fn=progress,
        well_progress_fn=None if well_progress is None else _well_progress,
        should_abort=is_cancelled,
        resume=resuming,
    )
    clear_run_marker(dest)
    channels = ", ".join(result.channels) if result.channels else "?"
    kept = (f" ({len(result.wells_resumed)} kept from an interrupted run)"
            if result.wells_resumed else "")
    return (
        f"Wrote {result.images_written} image(s) for {len(result.wells)} well(s)"
        f"{kept} (channels: {channels}) into {dest.name}/in — open it in Analyze "
        f"to process."
    )
