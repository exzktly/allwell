"""The dataset caches share one budget and keep a floor of free memory (``review/data/memory_budget.py``).

Each cache used to size itself as its own share of physical memory, blind to the others and to what was free: on
an 8 GB machine the three could claim about 4.2 GB between them, and an edit's whole-column copies then failed with
a ``MemoryError``. These tests hold the two rules that replaced that — one set of shares, and eviction beyond the
budget whenever free memory is below the floor — against each cache, with the machine's memory figures patched so
the right and the wrong behaviour differ by a whole well.

The suite runs with the floor off (``tests/conftest.py``), because how much memory is free depends on the other
workers; every test here that needs the floor sets it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.data import image_discovery as idisc
from review.data import memory_budget as mb
from review.data import population_store as ps
from review.data import row_cache as rc

GIB = 2**30
MIB = 2**20


@pytest.fixture
def machine(monkeypatch):
    """An 8 GiB machine with a 100 MiB floor; ``machine(available)`` sets how much is free."""
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: 8 * GIB)
    monkeypatch.setattr(rc, "physical_memory_bytes", lambda: 8 * GIB)
    monkeypatch.setenv(mb.FLOOR_ENV, "100")
    free = {"bytes": 8 * GIB}
    monkeypatch.setattr(mb, "available_memory_bytes", lambda: free["bytes"])

    def set_available(n: int) -> None:
        free["bytes"] = n

    return set_available


def _frame(n: int) -> pd.DataFrame:
    return pd.DataFrame({"v": np.arange(n, dtype=float)})


# ── one set of shares ────────────────────────────────────────────────────────


def test_every_cache_takes_its_default_from_the_one_set_of_shares(monkeypatch):
    for env in (rc.BUDGET_ENV, ps.BUDGET_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: 8 * GIB)
    monkeypatch.setattr(rc, "physical_memory_bytes", lambda: 8 * GIB)
    rows, pops, pixels = rc.default_budget_bytes(), ps.default_budget_bytes(), idisc._default_max_decode_bytes()
    assert rows == 2 * GIB
    assert pops == int(8 * GIB * 0.10)
    assert pixels == int(8 * GIB * 0.10)
    # Together they plan on the stated total and no more: on 8 GiB that is 3.6 GiB, where the three separate
    # shares (a quarter, an eighth and 15%) came to 4.2 GiB.
    assert rows + pops + pixels == int(8 * GIB * mb.TOTAL_SHARE) == int(3.6 * GIB)
    assert mb.TOTAL_SHARE == pytest.approx(sum(mb.SHARES.values()))


def test_the_pixel_budget_reads_physical_memory_on_every_platform(monkeypatch):
    """``os.sysconf`` is absent on Windows, where the pixel budget used to fall back to a fixed 1 GiB."""
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: 16 * GIB)
    assert idisc._default_max_decode_bytes() == int(16 * GIB * 0.10)
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: 64 * GIB)
    assert idisc._default_max_decode_bytes() == 2 * GIB          # the cap
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: None)
    assert idisc._default_max_decode_bytes() == 512 * MIB        # unreadable: the minimum


# ── the floor ────────────────────────────────────────────────────────────────


def test_the_floor_is_a_share_of_memory_never_below_a_gib(monkeypatch):
    monkeypatch.delenv(mb.FLOOR_ENV, raising=False)
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: 8 * GIB)
    assert mb.free_floor_bytes() == int(8 * GIB * 0.15)
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: 4 * GIB)
    assert mb.free_floor_bytes() == GIB
    monkeypatch.setattr(mb, "physical_memory_bytes", lambda: None)
    assert mb.free_floor_bytes() == GIB


def test_the_shortfall_is_the_distance_below_the_floor(machine):
    machine(30 * MIB)
    assert mb.shortfall_bytes() == 70 * MIB
    machine(100 * MIB)
    assert mb.shortfall_bytes() == 0
    machine(5 * GIB)
    assert mb.shortfall_bytes() == 0


def test_an_unreadable_free_figure_or_a_zero_floor_means_no_shortfall(machine, monkeypatch):
    monkeypatch.setattr(mb, "available_memory_bytes", lambda: None)
    assert mb.shortfall_bytes() == 0
    monkeypatch.setattr(mb, "available_memory_bytes", lambda: 0)
    monkeypatch.setenv(mb.FLOOR_ENV, "0")
    assert mb.shortfall_bytes() == 0


@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="reads /proc/meminfo")
def test_free_memory_is_read_on_linux():
    available, total = mb.available_memory_bytes(), mb.physical_memory_bytes()
    assert available is not None and total is not None and 0 < available <= total


# ── each cache gives memory back below the floor ─────────────────────────────


def test_the_row_cache_evicts_beyond_its_budget_while_memory_is_short(machine):
    cache = rc.RowCache(budget_bytes=100 * GIB)                  # the budget alone would never evict
    for label in ("A1", "A2", "A3"):
        cache[label] = _frame(1000)
    one = rc.frame_bytes(_frame(1000))
    # Short by a little more than one frame: the oldest two go — one frame would not cover it — and the frame just
    # stored and the one before it are kept.
    machine(100 * MIB - (one + 1))
    cache["A4"] = _frame(1000)
    assert cache.keys() == ["A3", "A4"]
    assert cache.evictions == 2


def test_the_row_cache_keeps_everything_while_memory_is_not_short(machine):
    cache = rc.RowCache(budget_bytes=100 * GIB)
    for label in ("A1", "A2", "A3", "A4"):
        cache[label] = _frame(1000)
    assert cache.keys() == ["A1", "A2", "A3", "A4"]
    assert cache.evictions == 0


def test_the_row_cache_never_gives_back_a_pinned_frame(machine):
    cache = rc.RowCache(budget_bytes=100 * GIB)
    cache["A1"] = _frame(1000)
    cache["A2"] = _frame(1000)
    with cache.pinned("A1"):
        machine(0)                                                # short by the whole floor
        cache["A3"] = _frame(1000)
        assert cache.keys() == ["A1", "A3"]


def test_the_population_store_evicts_beyond_its_budget_while_memory_is_short(machine):
    store = ps.PopulationStore(budget_bytes=100 * GIB)
    for key in ("p1", "p2", "p3"):
        store[key] = np.zeros(1000)
    machine(100 * MIB - (8000 + 1))
    store["p4"] = np.zeros(1000)
    assert ["p1" in store, "p2" in store, "p3" in store, "p4" in store] == [False, False, True, True]
    assert store.evictions == 2


def test_the_pixel_cache_evicts_beyond_its_budget_while_memory_is_short(machine):
    cache = idisc._DecodedArrayCache(max_entries=100, max_bytes=100 * GIB)
    for key in ("f1", "f2", "f3"):
        cache.put((key,), np.zeros(1000))
    machine(100 * MIB - (8000 + 1))
    cache.put(("f4",), np.zeros(1000))
    assert [cache.get((k,)) is not None for k in ("f1", "f2", "f3", "f4")] == [False, False, True, True]
