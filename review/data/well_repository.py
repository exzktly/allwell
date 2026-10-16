"""WellRepository — the row-access facade over ``DatasetState`` + ``get_rows``.

A thin, Qt-free seam so feature/controller code depends on a small repository
API instead of reaching into ``ctx.state.dataset.cache`` directly. It owns no
new behaviour (roadmap B2 is an interface seam, no logic move): every method
delegates to the existing ``data_loading`` functions or the ``DatasetState``
aggregate root.

``DatasetState`` is the aggregate root (paths + row cache + derived timepoint /
FOV lists + ``pipeline_info``); the repository is the read/query port over it,
plus the one write entry point — :meth:`WellRepository.load_well`, which the
dataset loader's bulk pass uses so its wells enter the cache by the same route a
lazy read builds them.
Because the lazy-load path (``get_rows``) runs the registered cache projectors,
which read the wider ``ctx.state`` (QC params, crop region), the repository holds
the ``ViewerContext`` and threads it to ``get_rows`` — it is *not* merely a
wrapper over the bare ``DatasetState``.

Reached as ``ctx.state.repo`` (attached at app construction) — deliberately on
``AppState`` rather than as a new ``ViewerContext`` accessor, so it doesn't touch
the frozen service-accessor surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from review.data.data_loading import (
    get_all_timepoints, get_rows, load_and_project,
)

if TYPE_CHECKING:  # pragma: no cover
    from review.core.dataset_state import DatasetState
    from review.data.well_summary import WellSummary


class WellRepository:
    """Read/query port over the currently-open dataset's per-well rows, plus the
    load entry point that puts them there."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    @property
    def dataset(self) -> DatasetState:
        """The underlying ``DatasetState`` aggregate root."""
        return self._ctx.state.dataset

    def load_well(self, label: str, path: Path | None = None) -> pd.DataFrame:
        """Read *label*'s CSV into the row cache, projected and recorded.

        The eager entry point, for a caller that knows it wants the well now —
        the dataset loader's bulk pass over a freshly opened plate. Delegates to
        :func:`data_loading.load_and_project`, which is also what :meth:`rows`
        reaches on a cache miss, so eager and lazy loads cannot drift apart in
        which layers they apply. *path* registers the CSV location at the same
        time, which is what a first pass over a new dataset needs.
        """
        return load_and_project(self._ctx, label, path)

    def open_well(self, label: str, path: Path, *, load_rows: bool) -> WellSummary:
        """Register *label*'s CSV for an opening dataset and record its summary.

        What the dataset loader calls for every well. With *load_rows* the well's
        frame is read into the row cache as :meth:`load_well` does — the loader
        asks for that while the plate still fits the row-cache budget, so a plate
        that fits opens exactly as it always did — and its summary is taken from
        that frame when none is stored. Without it, nothing is read but the
        summary (``review/data/well_summary.py``): the stored one, else a column
        read of the Parquet cache, else a parse. Either way the FOV-label verdict
        is recorded, and the well's rows are one ``get_rows`` away.
        """
        from review.data.data_loading import record_fov_integrity
        from review.data.parquet_cache import source_fingerprint
        from review.data.well_summary import read_summary, summarize_frame, well_summary, write_summary

        dataset = self.dataset
        dataset.well_paths[label] = path
        if load_rows:
            summary = read_summary(path)
            fingerprint = source_fingerprint(path)
            frame = load_and_project(self._ctx, label, path)
            if summary is None:
                summary = summarize_frame(frame)
                write_summary(path, summary, fingerprint)
        else:
            summary = well_summary(path)
            record_fov_integrity(self._ctx, label, summary.fov_verdict)
        dataset.summaries[label] = summary
        return summary

    def rows(self, label: str) -> pd.DataFrame:
        """The measurement frame for well *label*, loading + projecting lazily
        (delegates to :func:`data_loading.get_rows`)."""
        return get_rows(self._ctx, label)

    def rows_or_none(self, label: str) -> pd.DataFrame | None:
        """The cached frame for *label* if already loaded, else ``None`` — a
        cache-only lookup with **no** lazy load or projection (the explicit,
        named form of the direct ``dataset.cache.get(label)`` reads B3 routes
        through here)."""
        return self._ctx.state.dataset.cache.get(label)

    def labels(self) -> list[str]:
        """Well tokens known to the dataset (whether or not yet cached)."""
        return list(self._ctx.state.dataset.well_paths)

    def all_timepoints(self) -> list[float]:
        """Canonical numeric timepoint list (delegates to
        :func:`data_loading.get_all_timepoints`)."""
        return get_all_timepoints(self._ctx)

    def all_fovs(self) -> list[str]:
        """Canonical FOV list off the dataset's derived cache."""
        return list(self._ctx.state.dataset.all_fovs_cache)

    @property
    def has_data(self) -> bool:
        return self._ctx.state.dataset.has_data
