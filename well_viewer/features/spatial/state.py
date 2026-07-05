"""Spatial (physical-space) analysis tab state.

User configuration + a transient cache of the last computed result so the
renderer repaints without recomputing. Qt-free — pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEVEL_CELL = "cell"
LEVEL_COLONY = "colony"
LEVEL_INTRA = "intra"
LEVEL_SUMMARY = "summary"
LEVELS = (LEVEL_CELL, LEVEL_COLONY, LEVEL_INTRA, LEVEL_SUMMARY)

VIZ_SPATIAL_MAP = "spatial_map"
VIZ_NND_HISTOGRAM = "nnd_histogram"
VIZ_RIPLEY = "ripley_l"
VIZ_COLONY_SIZE = "colony_size"
VIZ_COLONY_METRIC_DIST = "colony_metric_dist"
VIZ_RADIAL_PROFILE = "radial_profile"
VIZ_BOUNDARY_INTERIOR = "boundary_interior"
VIZ_SUMMARY_PANEL = "summary_panel"

VIZ_BY_LEVEL = {
    LEVEL_CELL: (VIZ_SPATIAL_MAP, VIZ_NND_HISTOGRAM, VIZ_RIPLEY),
    LEVEL_COLONY: (VIZ_SPATIAL_MAP, VIZ_COLONY_SIZE, VIZ_COLONY_METRIC_DIST),
    LEVEL_INTRA: (VIZ_SPATIAL_MAP, VIZ_RADIAL_PROFILE, VIZ_BOUNDARY_INTERIOR),
    LEVEL_SUMMARY: (VIZ_SUMMARY_PANEL,),
}

METHOD_DBSCAN = "dbscan"
METHOD_SINGLE_LINKAGE = "single_linkage"

SUMMARY_DISPLAY_HEATMAP = "heatmap"
SUMMARY_DISPLAY_LINE = "line"
SUMMARY_DISPLAYS = (SUMMARY_DISPLAY_HEATMAP, SUMMARY_DISPLAY_LINE)

# Vizs that do NOT consume the per-cell Metric picker. The tab greys the
# picker on these so the UI stops promising an effect that never happens.
# Everything else (spatial map on any level, per-colony distribution,
# radial profile, boundary/interior, summary panel) uses the metric.
METRIC_IGNORED_VIZS = frozenset({
    VIZ_NND_HISTOGRAM,
    VIZ_RIPLEY,
    VIZ_COLONY_SIZE,
})


@dataclass
class SpatialState:
    """Spatial-analysis tab state.

    ``metric_col`` is the per-cell CSV column driving colour/summary in the
    active viz. ``last_*`` is a transient cache the controller fills and the
    renderer reads.
    """

    # ── user configuration (persisted) ──────────────────────────────────────
    level: str = LEVEL_CELL
    viz: str = VIZ_SPATIAL_MAP
    metric_col: str = ""                     # empty = colour by colony/density fallback
    timepoint: float | None = None            # None = default / first available

    colony_method: str = METHOD_DBSCAN
    colony_eps_mult: float = 3.0             # ε = eps_mult · median(NND)
    colony_min_samples: int = 4
    boundary_pct: float = 20.0               # bottom-N% of d_edge = boundary

    show_hulls: bool = True
    show_density_kde: bool = False

    # Summary-panel (LEVEL_SUMMARY) options.
    summary_metric_key: str = "nnd_median"       # FrameSummary field name
    summary_display: str = SUMMARY_DISPLAY_HEATMAP

    # Per-scale metric toggles (skip compute entirely when unchecked).
    enabled_metrics: dict = field(default_factory=lambda: {
        "nnd": True, "knn_mean": True, "local_density": True,
        "colony_size": True, "hull_area": True, "packing_density": True,
        "d_centroid": True, "d_edge": True, "r_over_R": True,
    })

    # ── transient computed cache (never persisted) ───────────────────────────
    last_result: dict = field(default_factory=dict)
    # Per-frame levels (Cell / Colony / Intra-colony) compute one payload per
    # selected well and facet them across a grid of panels. ``frames`` holds
    # those per-well payloads; ``frames_truncated`` counts wells dropped by the
    # panel cap so the renderer can note them. ``last_result`` stays the home
    # of the Summary-level matrix (a single figure, not faceted).
    frames: list = field(default_factory=list)
    frames_truncated: int = 0
    last_error: str = ""
    n_cells: int = 0
    active_well: str = ""

    def reset_result(self) -> None:
        self.last_result = {}
        self.frames = []
        self.frames_truncated = 0
        self.last_error = ""
        self.n_cells = 0

    def to_config(self) -> dict:
        return {
            "level": self.level,
            "viz": self.viz,
            "metric_col": self.metric_col,
            "timepoint": self.timepoint,
            "colony_method": self.colony_method,
            "colony_eps_mult": float(self.colony_eps_mult),
            "colony_min_samples": int(self.colony_min_samples),
            "boundary_pct": float(self.boundary_pct),
            "show_hulls": bool(self.show_hulls),
            "show_density_kde": bool(self.show_density_kde),
            "enabled_metrics": dict(self.enabled_metrics),
            "summary_metric_key": self.summary_metric_key,
            "summary_display": self.summary_display,
        }

    def apply_config(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        for key in self.to_config():
            if key in data and data[key] is not None:
                setattr(self, key, data[key])
        self.colony_eps_mult = float(self.colony_eps_mult)
        self.colony_min_samples = int(self.colony_min_samples)
        self.boundary_pct = float(self.boundary_pct)
        if not isinstance(self.enabled_metrics, dict):
            self.enabled_metrics = {}
