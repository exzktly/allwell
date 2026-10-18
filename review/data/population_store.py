"""The threshold-free population store: a byte-bounded, thread-safe LRU.

``StatsCacheState.populations`` (``review/core/stats_cache_state.py``) holds one
of these. A population is what a group statistic is computed from — per well,
the included, gated cells' values grouped by timepoint
(``review/domain/well_population.py``), the per-cell arrays behind a CDF curve,
the per-frame excluded nucleus ids — and building one costs a read of the well,
so the store keeps as many as its byte budget allows, evicting the least
recently used. The budget mirrors the row cache's (``review/data/row_cache.py``):
an environment override in MiB, else a share of physical memory.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict

_logger = logging.getLogger("review")

#: Share of physical memory the population store may hold by default. A
#: population is about one float per included cell (about a tenth of the well's
#: parsed frame), so this holds every well of a plate whose frames would need
#: several times the row cache's share.
DEFAULT_SHARE = 0.125
#: The budget never defaults below this.
MIN_DEFAULT_BYTES = 256 * 2**20
#: Environment override, in MiB.
BUDGET_ENV = "ALLWELL_POPULATION_CACHE_MB"


def default_budget_bytes() -> int:
    """The environment override, else :data:`DEFAULT_SHARE` of physical memory."""
    from review.data.row_cache import physical_memory_bytes

    raw = os.environ.get(BUDGET_ENV, "").strip()
    if raw:
        try:
            mib = float(raw)
        except ValueError:
            _logger.warning("%s=%r is not a number of MiB; using the default budget", BUDGET_ENV, raw)
        else:
            if mib > 0:
                return int(mib * 2**20)
            _logger.warning("%s=%r is not positive; using the default budget", BUDGET_ENV, raw)
    total = physical_memory_bytes()
    if not total:
        return MIN_DEFAULT_BYTES
    return max(MIN_DEFAULT_BYTES, int(total * DEFAULT_SHARE))


def _nbytes(value) -> int:
    """What *value* holds: its ``nbytes`` when it reports one, else a nominal 1 KiB."""
    n = getattr(value, "nbytes", None)
    try:
        return int(n) if n is not None else 1024
    except (TypeError, ValueError):
        return 1024


class PopulationStore:
    """Key → threshold-free population, least recently used evicted past a byte budget.

    Thread-safe: the redraws that fill it run on the GUI thread, but batch
    export and the Statistics worker may read it from elsewhere. At least one
    entry is always kept, so a single population larger than the budget is
    still served to the redraw that built it.
    """

    def __init__(self, budget_bytes: int | None = None) -> None:
        self._items: OrderedDict = OrderedDict()
        self._sizes: dict = {}
        self._held = 0
        self._lock = threading.RLock()
        self._budget = budget_bytes
        self.evictions = 0

    @property
    def budget_bytes(self) -> int:
        # Resolved on first use rather than at construction, so an app state
        # built at import time does not read the environment before a test or
        # the launcher has set it.
        if self._budget is None:
            self._budget = default_budget_bytes()
        return self._budget

    def set_budget(self, budget_bytes: int) -> None:
        with self._lock:
            self._budget = int(budget_bytes)
            self._evict(keep=None)

    @property
    def held_bytes(self) -> int:
        with self._lock:
            return self._held

    def get(self, key, default=None):
        with self._lock:
            if key not in self._items:
                return default
            self._items.move_to_end(key)
            return self._items[key]

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __setitem__(self, key, value) -> None:
        size = _nbytes(value)
        with self._lock:
            if key in self._items:
                self._held -= self._sizes.get(key, 0)
            self._items[key] = value
            self._items.move_to_end(key)
            self._sizes[key] = size
            self._held += size
            self._evict(keep=key)

    def __getitem__(self, key):
        with self._lock:
            value = self._items[key]
            self._items.move_to_end(key)
            return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._sizes.clear()
            self._held = 0

    def _evict(self, keep) -> None:
        budget = self.budget_bytes
        while self._held > budget and len(self._items) > 1:
            victim = next((k for k in self._items if k != keep), None)
            if victim is None:
                break
            del self._items[victim]
            self._held -= self._sizes.pop(victim, 0)
            self.evictions += 1
