"""Dataset domain state.

Everything that belongs to "the data directory currently open": its paths, the
per-well row cache, the canonical timepoint / FOV lists derived from that cache,
the ``pipeline_info.json`` sidecar, and the lazily-read persistence document.

The timepoint / FOV rebuilds are genuine dataset behaviour and live here as
methods — they were ``WellViewerApp._rebuild_all_*_cache`` before.

Qt-free: imports only stdlib, pandas, and the Qt-free ``data_loading`` module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from well_viewer.data.data_loading import parse_timepoint_hours
from well_viewer.data.image_resolver import normalize_numeric_token

# Local alias for the shared numeric-token normaliser: a raw FOV token collapses
# to ``%g`` form so ``1``, ``01`` and ``1.0`` index the same field of view.
_norm_fov = normalize_numeric_token


@dataclass
class DatasetState:
    """The currently-open dataset.

    ``cache`` maps well token → measurement ``DataFrame``; ``well_paths`` maps
    well token → source CSV path. ``all_timepoints_cache`` / ``all_fovs_cache``
    are derived from ``cache`` + ``pipeline_info`` and kept canonical by the
    rebuild methods so every tab's timepoint / FOV menu agrees.
    """

    data_dir: Path | None = None
    in_dir: Path | None = None
    tmp_dir: Path | None = None

    well_paths: dict[str, Path] = field(default_factory=dict)
    cache: dict[str, pd.DataFrame] = field(default_factory=dict)

    all_timepoints_cache: list[float] = field(default_factory=list)
    all_fovs_cache: list[str] = field(default_factory=list)

    pipeline_info: dict = field(default_factory=dict)
    tok_to_label: dict[str, str] = field(default_factory=dict)
    well_labels: dict[str, str] = field(default_factory=dict)
    fov_tp_extractor: Callable[[str], tuple[str, str]] | None = None
    persistence_doc: object = None

    # Dataset-wide crop rectangle (row0, col0, row1, col1) in source-mask pixels,
    # or None. Owned by features/crop; cells whose centroid falls outside are
    # excluded at the df_included_mask chokepoint via the Crop_Pass column.
    crop_region: tuple[int, int, int, int] | None = None

    # ── status ───────────────────────────────────────────────────────────────

    @property
    def has_data(self) -> bool:
        return bool(self.well_paths)

    @property
    def well_count(self) -> int:
        return len(self.well_paths)

    # ── load lifecycle ───────────────────────────────────────────────────────

    def begin_load(self, data_dir: Path) -> None:
        """Reset the per-dataset caches for a switch to *data_dir*.

        The dataset identity is changing, so the well paths, the row cache, and
        the cached persistence document are all dropped — the load pass and the
        rebuild methods below repopulate them. Cleared in place so any view
        still holding a reference sees the empty collections.
        """
        self.data_dir = data_dir
        self.persistence_doc = None
        self.well_paths.clear()
        self.cache.clear()
        # Well labels are per-dataset (persisted in the new dataset's
        # pipeline_info sample_definitions block). The loader merges them in
        # additively and early-returns when the new dataset has no block, so
        # clear here — else the previous dataset's labels leak into this one.
        self.well_labels.clear()
        # A new dataset has no crop until its persisted region (if any) loads.
        self.crop_region = None

    def rebuild_timepoints(self) -> None:
        """Recompute the canonical numeric timepoint list.

        Merged from two sources so every tab that reads ``all_timepoints_cache``
        agrees: the loaded CSV rows (``timepoint_hours`` or parsed ``timepoint``
        strings) and ``pipeline_info``'s ``available_timepoints``.
        """
        all_tps: set[float] = set()
        for df in self.cache.values():
            if df is None or df.empty:
                continue
            if "timepoint_hours" in df.columns:
                tp_num = pd.to_numeric(df["timepoint_hours"], errors="coerce")
                all_tps.update(float(t) for t in tp_num.dropna().unique())
                missing = tp_num.isna()
            else:
                missing = pd.Series(True, index=df.index)
            if "timepoint" in df.columns and missing.any():
                for s in df.loc[missing, "timepoint"].fillna("").astype(str).unique():
                    parsed = parse_timepoint_hours(s)
                    if parsed is not None:
                        all_tps.add(parsed)
        for tp in (self.pipeline_info or {}).get("available_timepoints", []) or []:
            parsed = parse_timepoint_hours(str(tp))
            if parsed is not None:
                all_tps.add(parsed)
        self.all_timepoints_cache = sorted(all_tps)

    def rebuild_fovs(self) -> None:
        """Recompute the canonical FOV list from the row cache + pipeline_info.

        A dataset with rows but no ``fov`` column collapses to a single FOV
        ``"1"`` so the FOV menus are never empty.
        """
        all_fovs: set[str] = set()
        for df in self.cache.values():
            if df is None or df.empty or "fov" not in df.columns:
                continue
            for raw in df["fov"].fillna("").astype(str).unique():
                fov = _norm_fov(raw)
                if fov:
                    all_fovs.add(fov)
        for fov in (self.pipeline_info or {}).get("available_fovs", []) or []:
            fov_norm = _norm_fov(fov)
            if fov_norm:
                all_fovs.add(fov_norm)
        if not all_fovs and self.well_paths:
            all_fovs.add("1")

        def _sort_key(token: str) -> tuple[int, float, str]:
            try:
                return (0, float(token), token)
            except ValueError:
                return (1, 0.0, token)

        self.all_fovs_cache = sorted(all_fovs, key=_sort_key)
