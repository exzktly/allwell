"""Multivariate (higher-dimensional analysis) tab state.

The user's configuration for the tab plus a transient cache of the most recent
computed result so the renderer can repaint from a snapshot without recomputing.
Qt-free: imports only stdlib (the ``last_*`` numeric payloads are plain objects
filled by the controller). Mirrors ``features/stats/state.py`` — fields, plus a
couple of trivial helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Mode / method identifiers, kept as module constants so the controller,
# renderer, and tab_view agree on the strings.
MODE_CORRELATION = "correlation"
MODE_DIMRED = "dimred"
MODE_CLUSTERING = "clustering"
MODE_FACET = "facet"
MODES = (MODE_CORRELATION, MODE_DIMRED, MODE_CLUSTERING, MODE_FACET)


@dataclass
class MultivariateState:
    """Higher-dimensional analysis tab state.

    ``feature_cols`` are the CSV columns forming the analysis matrix (chosen in
    the feature picker); ``timepoints`` restricts the cells (empty = all). The
    ``last_*`` block is the transient result of the most recent ``mv_recompute``
    — never persisted, only cached so the renderer repaints without recomputing.
    """

    # ── user configuration (persisted) ──────────────────────────────────────
    mode: str = MODE_CORRELATION
    feature_cols: list[str] = field(default_factory=list)
    timepoints: list[float] = field(default_factory=list)   # [] = all timepoints
    standardize: bool = True                # z-score before PCA / clustering

    corr_view: str = "heatmap"              # "heatmap" | "splom"
    dimred_method: str = "pca"              # "pca" | "umap" | "tsne"
    color_by: str = "group"                 # "group" | "well" | "timepoint" | "cluster"

    cluster_method: str = "kmeans"          # "kmeans" | "hierarchical"
    cluster_count: int = 4
    linkage_method: str = "ward"

    facet_row: str = "well"                 # factor key: well | timepoint
    facet_col: str = "timepoint"
    facet_value_col: str = ""               # feature column aggregated per cell
    facet_agg: str = "mean"                 # mean | median | count | std

    # ── transient computed cache (written by controller, read by renderer) ───
    last_result: dict = field(default_factory=dict)
    last_error: str = ""                    # user-facing message; renderer shows it
    n_cells: int = 0

    def reset_result(self) -> None:
        self.last_result = {}
        self.last_error = ""
        self.n_cells = 0

    def to_config(self) -> dict:
        """The persisted subset (config only — never the transient result)."""
        return {
            "mode": self.mode,
            "feature_cols": list(self.feature_cols),
            "timepoints": list(self.timepoints),
            "standardize": bool(self.standardize),
            "corr_view": self.corr_view,
            "dimred_method": self.dimred_method,
            "color_by": self.color_by,
            "cluster_method": self.cluster_method,
            "cluster_count": int(self.cluster_count),
            "linkage_method": self.linkage_method,
            "facet_row": self.facet_row,
            "facet_col": self.facet_col,
            "facet_value_col": self.facet_value_col,
            "facet_agg": self.facet_agg,
        }

    def apply_config(self, data: dict) -> None:
        """Restore the persisted subset from *data* (missing keys keep defaults)."""
        if not isinstance(data, dict):
            return
        for key in self.to_config():
            if key in data and data[key] is not None:
                setattr(self, key, data[key])
        # Normalize numeric fields that JSON may have widened.
        self.timepoints = [float(t) for t in (self.timepoints or [])]
        self.cluster_count = int(self.cluster_count)
