"""The Nanowells page's state: what the user chose (persisted) and the data computed from the dataset (not).

Qt-free and stdlib-only. The persisted subset is :meth:`NanowellState.to_config`; :meth:`apply_config` accepts
only values it recognises, so a block written by another version, or edited by hand, cannot put the page in
a state its controls cannot show.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BASELINES = ("earliest", "first_measurable")
AXES = ("time", "wells")            # what the Distribution view's x-axis carries
VIEWS = ("distribution", "trajectories", "array_map", "position", "array_qc")


@dataclass
class NanowellState:
    # ── user configuration (persisted) ──────────────────────────────────────
    view: str = "distribution"              # the page's default is the Distribution beeswarm (§3.10.3)
    metric: str = "n"
    baseline: str = "earliest"
    axis: str = "time"
    well: str = ""                          # the well the array map, trajectories and position views show
    timepoint: str = ""                     # the array map's timepoint; "" = the earliest
    selected_sites: list = field(default_factory=list)  # [[well, q, r], ...] — not a plate selection

    # ── computed from the dataset (never persisted) ─────────────────────────
    data: object = None                     # collect.NanowellData, or None for a dataset with no nanowells
    metrics: object = None                  # series_compute.site_metrics(data, baseline)
    load_error: str = ""

    def reset_data(self) -> None:
        self.data, self.metrics, self.load_error = None, None, ""

    def to_config(self) -> dict:
        return {"view": self.view, "metric": self.metric, "baseline": self.baseline, "axis": self.axis,
                "well": self.well, "timepoint": self.timepoint,
                "selected_sites": [list(s) for s in self.selected_sites]}

    def apply_config(self, config: dict) -> None:
        """Take each recognised value from *config*; anything else keeps its default."""
        fresh = NanowellState()
        self.view = config.get("view") if config.get("view") in VIEWS else fresh.view
        self.baseline = config.get("baseline") if config.get("baseline") in BASELINES else fresh.baseline
        self.axis = config.get("axis") if config.get("axis") in AXES else fresh.axis
        for name in ("metric", "well", "timepoint"):
            value = config.get(name)
            setattr(self, name, value if isinstance(value, str) else getattr(fresh, name))
        sites = []
        for item in config.get("selected_sites") or []:
            if (isinstance(item, (list, tuple)) and len(item) == 3 and isinstance(item[0], str)
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in item[1:])):
                sites.append([item[0], int(item[1]), int(item[2])])
        self.selected_sites = sites
