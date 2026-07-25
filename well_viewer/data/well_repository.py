"""WellRepository — the row-access facade over ``DatasetState`` + ``get_rows``.

A thin, Qt-free seam so feature/controller code depends on a small repository
API instead of reaching into ``ctx.state.dataset.cache`` directly. It owns no
new behaviour (roadmap B2 is an interface seam, no logic move): every method
delegates to the existing ``data_loading`` functions or the ``DatasetState``
aggregate root.

``DatasetState`` is the aggregate root (paths + row cache + derived timepoint /
FOV lists + ``pipeline_info``); the repository is the read/query port over it.
Because the lazy-load path (``get_rows``) runs the registered cache projectors,
which read the wider ``ctx.state`` (QC params, crop region), the repository holds
the ``ViewerContext`` and threads it to ``get_rows`` — it is *not* merely a
wrapper over the bare ``DatasetState``.

Reached as ``ctx.state.repo`` (attached at app construction) — deliberately on
``AppState`` rather than as a new ``ViewerContext`` accessor, so it doesn't touch
the frozen service-accessor surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from well_viewer.data.data_loading import get_all_timepoints, get_rows

if TYPE_CHECKING:  # pragma: no cover
    from well_viewer.core.dataset_state import DatasetState


class WellRepository:
    """Read/query port over the currently-open dataset's per-well rows."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    @property
    def dataset(self) -> DatasetState:
        """The underlying ``DatasetState`` aggregate root."""
        return self._ctx.state.dataset

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
