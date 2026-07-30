"""Qt-free executor for the OME-TIFF converter.

Writes the de-interleaved per-plane TIFs into ``<destination>/in/`` by delegating
to :func:`pipeline.ome_explode.explode_ome_tiff`, threading the page's log,
progress and cancellation through. All the heavy pixel work lives in
``pipeline.ome_explode``; this module just applies the converter's conventions
(``dest/in`` layout, experiment token from the filename) and formats the summary.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from datasets.ome_import.ome_import_compute import ConversionParams, strip_ome_suffix
from pipeline.ome_explode import explode_ome_tiff


def convert(
    src: Path | str,
    dest: Path | str,
    params: ConversionParams,
    *,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """De-interleave *src* into ``<dest>/in`` and return a one-line summary.

    Raises :class:`pipeline.ome_explode.OmeExplodeCancelled` if *is_cancelled*
    fires, and :class:`ValueError` for an inconsistent grid / FOVs / frame-order
    (already surfaced earlier by ``plan_conversion``, re-checked here defensively).
    """
    src, dest = Path(src), Path(dest)
    experiment = params.experiment or strip_ome_suffix(src.name)
    result = explode_ome_tiff(
        src,
        dest / "in",
        grid_rows=params.grid_rows,
        grid_cols=params.grid_cols,
        origin_well=params.origin or "A01",
        direction=params.direction,
        experiment=experiment,
        fovs_per_well=params.fovs_per_well,
        frame_order=params.frame_order,
        workers=params.workers,
        log_fn=log,
        progress_fn=progress,
        should_abort=is_cancelled,
    )
    channels = ", ".join(result.channels) if result.channels else "?"
    return (
        f"Wrote {result.images_written} image(s) for {len(result.wells)} well(s) "
        f"(channels: {channels}) into {dest.name}/in — open it in Analyze to "
        f"process."
    )
