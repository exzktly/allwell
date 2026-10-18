"""Group-statistics memoization cache — core (not feature) state.

``domain/stats_compute`` keys every group aggregation result here, and those
results back every bar / line / scatter redraw. The cache is therefore needed
whenever *any* plot renders, independent of whether the Statistics tab exists —
so it lives in core, not on the Statistics feature's ``StatsState`` slice. (This
also keeps ``domain/`` from depending on a ``features/`` state class.)

Two stores, invalidated differently:

``cache``
    Results — a well's aggregate at one threshold, a group's replicate
    statistics. Small, unbounded, and cleared by
    ``selection_controller.invalidate_stats_cache`` on every edit that could
    move a number.

``populations``
    The *threshold-free* inputs those results are computed from: per well, the
    included, gated cells' values grouped by timepoint
    (``review/domain/well_population.py``), and the per-cell arrays behind a CDF
    curve. Building one costs a read of the well from disk; on a plate larger
    than the row cache that read is most of what a redraw costs (a threshold
    edit on a 60-well plate of 1.5M-cell wells spent about 50 s re-reading and
    re-filtering wells whose filtered cells had not changed). So this store is
    **bounded by bytes** and **kept across edits that cannot change a
    population** — the fraction-on threshold, the SEM toggle, the FOV-replicate
    toggle — through ``selection_controller.invalidate_threshold_results``.
    Every other edit clears it. Its keys carry everything a population is a
    function of except the row contents' projector settings, which is why a
    plain ``invalidate_stats_cache`` still clears it. The store itself — the
    budget and the eviction — is ``review/data/population_store.py``; this
    module holds the fields and their defaults only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from review.data.population_store import PopulationStore


@dataclass
class StatsCacheState:
    """The group-statistics result cache and the threshold-free population store."""

    cache: dict = field(default_factory=dict)
    populations: PopulationStore = field(default_factory=PopulationStore)

    def invalidate(self, *, keep_populations: bool = False) -> None:
        """Drop every result; drop the populations too unless *keep_populations*.

        *keep_populations* is for an edit that provably changes no population:
        one that moves only the threshold, the spread kind or the replicate
        unit, none of which a population is a function of.
        """
        self.cache.clear()
        if not keep_populations:
            self.populations.clear()
