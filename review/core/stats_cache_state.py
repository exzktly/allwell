"""Group-statistics memoization cache — core (not feature) state.

``domain/stats_compute`` keys every group aggregation result here, and those
results back every bar / line / scatter redraw. The cache is therefore needed
whenever *any* plot renders, independent of whether the Statistics tab exists —
so it lives in core, not on the Statistics feature's ``StatsState`` slice. (This
also keeps ``domain/`` from depending on a ``features/`` state class.)

Invalidated by ``selection_controller.invalidate_stats_cache`` whenever group
definitions / gating / thresholds change.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StatsCacheState:
    """The group-statistics result cache."""

    cache: dict = field(default_factory=dict)

    def invalidate(self) -> None:
        self.cache.clear()
