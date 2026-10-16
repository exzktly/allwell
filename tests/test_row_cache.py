"""The row cache is bounded, and bounding it changes no number (tracker M1).

``review/data/row_cache.RowCache`` replaces the unbounded ``dict`` that held every
well of an open dataset — 19 GB for a 96-well plate of 1.1M-cell wells. These
tests hold the two halves of the claim: the held bytes stay under the budget, and
a well evicted and read again is the same frame, with every projected layer on it.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.core.dataset_state import DatasetState
from review.data import row_cache as rc
from review.data.data_loading import df_included_mask, get_rows
from review.data.row_cache import RowCache, frame_bytes
from review.data.well_repository import WellRepository
from review.features.registry import build_feature_registry


def _frame(rows: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"x": rng.random(rows), "y": rng.random(rows)})


def test_the_held_bytes_stay_under_the_budget_and_the_least_recent_well_goes():
    one = frame_bytes(_frame(1000))
    cache = RowCache(budget_bytes=int(one * 2.5))
    for label in ("A01", "A02", "A03"):
        cache[label] = _frame(1000)
    assert cache.keys() == ["A02", "A03"]
    assert cache.held_bytes == 2 * one <= cache.budget_bytes
    assert cache.evictions == 1


def test_a_read_makes_a_well_recent():
    one = frame_bytes(_frame(1000))
    cache = RowCache(budget_bytes=int(one * 2.5))
    cache["A01"] = _frame(1000)
    cache["A02"] = _frame(1000)
    cache["A01"]                      # A02 is now the least recently used
    cache["A03"] = _frame(1000)
    assert cache.keys() == ["A01", "A03"]


def test_a_well_larger_than_the_budget_is_still_held_alone():
    cache = RowCache(budget_bytes=10)
    cache["A01"] = _frame(1000)
    cache["A02"] = _frame(1000)
    assert cache.keys() == ["A02"]
    cache.remeasure("A02")            # re-measuring must not evict the only frame either
    with cache.pinned("A02"):
        pass                          # nor must unpinning it
    assert cache.keys() == ["A02"]


def test_a_pinned_well_is_not_evicted_while_it_is_worked_on():
    one = frame_bytes(_frame(1000))
    cache = RowCache(budget_bytes=int(one * 1.5))
    cache["A01"] = _frame(1000)
    with cache.pinned("A01"):
        cache["A02"] = _frame(1000)
        assert "A01" in cache, "evicted while pinned: its projectors would find nothing to write to"
    assert cache.keys() == ["A02"], "unpinning puts the cache back under its budget"


def test_iterating_is_a_snapshot_and_does_not_reorder():
    cache = RowCache(budget_bytes=10**9)
    cache["A01"] = _frame(10)
    cache["A02"] = _frame(10)
    for label, _frame_ in cache.items():
        cache[label + "x"] = _frame(10)   # mutating mid-walk must not break the walk
    for label in cache:
        cache[label + "y"] = _frame(10)   # nor iterating the labels themselves
    assert list(cache)[:2] == ["A01", "A02"]


def test_a_read_on_another_thread_cannot_break_a_walk():
    cache = RowCache(budget_bytes=frame_bytes(_frame(100)) * 5)
    for i in range(5):
        cache[f"W{i}"] = _frame(100)
    stop = threading.Event()
    errors: list[BaseException] = []

    def churn():
        i = 0
        while not stop.is_set():
            cache[f"N{i % 50}"] = _frame(100)
            cache.get(f"W{i % 5}")
            i += 1

    worker = threading.Thread(target=churn)
    worker.start()
    try:
        for _ in range(2000):
            try:
                for _label, frame in cache.items():
                    assert len(frame) == 100
                sum(1 for _ in cache)
            except BaseException as exc:  # noqa: BLE001 — the assertion below reports it
                errors.append(exc)
                break
    finally:
        stop.set()
        worker.join()
    assert errors == []


def test_clear_empties_the_count_too():
    cache = RowCache(budget_bytes=1)
    cache["A01"] = _frame(10)
    cache["A02"] = _frame(10)
    cache.clear()
    assert (len(cache), cache.held_bytes, cache.evictions) == (0, 0, 0)


@pytest.mark.parametrize("value, expected", [("256", 256 * 2**20), ("0.5", 2**19)])
def test_the_budget_can_be_set_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setenv(rc.BUDGET_ENV, value)
    assert RowCache().budget_bytes == expected


@pytest.mark.parametrize("value", ["lots", "-3", "0"])
def test_a_bad_budget_falls_back_to_the_default(monkeypatch, value):
    monkeypatch.setattr(rc, "physical_memory_bytes", lambda: 16 * 2**30)
    monkeypatch.setenv(rc.BUDGET_ENV, value)
    assert RowCache().budget_bytes == 4 * 2**30


def test_the_default_is_a_share_of_memory_with_a_floor(monkeypatch):
    monkeypatch.delenv(rc.BUDGET_ENV, raising=False)
    monkeypatch.setattr(rc, "physical_memory_bytes", lambda: 16 * 2**30)
    assert RowCache().budget_bytes == 4 * 2**30
    monkeypatch.setattr(rc, "physical_memory_bytes", lambda: None)
    assert RowCache().budget_bytes == rc.MIN_DEFAULT_BYTES


def test_physical_memory_is_read_on_this_platform():
    assert (rc.physical_memory_bytes() or 0) > 2**28


def test_the_dataset_state_holds_a_bounded_cache():
    assert isinstance(DatasetState().cache, RowCache)


# ── through the real load path ────────────────────────────────────────────


def _write_well(tmp_path, label, rows=400):
    path = tmp_path / f"{label}.csv"
    pd.DataFrame({
        "well": [label] * rows,
        "fov": ["1"] * rows,
        "timepoint": ["0"] * rows,
        "nucleus_id": [str(i + 1) for i in range(rows)],
        "Included": [1] * rows,
        "MeanIntensity": np.linspace(1.0, 100.0, rows),
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def ctx(tmp_path):
    """The real ``DatasetState``, repository and registered projectors; no Qt."""
    dataset = DatasetState()
    for label in ("A01", "A02", "A03"):
        dataset.well_paths[label] = _write_well(tmp_path, label)
    obj = SimpleNamespace(
        state=SimpleNamespace(
            dataset=dataset,
            review_image=SimpleNamespace(override_version=0),
            qc=SimpleNamespace(params=None),
        ),
        runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={})),
    )
    obj.state.repo = WellRepository(obj)
    build_feature_registry()
    return obj


def test_an_evicted_well_comes_back_with_the_user_s_curation_on_it(ctx):
    """The budget holds one well; reading the others evicts A01, and reading it
    again must re-apply the manual exclusions rather than return the CSV's rows."""
    ctx.runtime.review.included_overrides = {("A01", "1", "0", str(n)): "0" for n in range(1, 41)}
    first = get_rows(ctx, "A01")
    expected = df_included_mask(first).copy()
    assert int(expected.sum()) == 360

    ctx.state.dataset.cache.set_budget(frame_bytes(first) + 1)
    get_rows(ctx, "A02")
    get_rows(ctx, "A03")
    assert "A01" not in ctx.state.dataset.cache, "the fixture did not evict; the test measures nothing"

    again = get_rows(ctx, "A01")
    assert again is not first
    pd.testing.assert_series_equal(df_included_mask(again), expected)
    assert ctx.state.dataset.cache.held_bytes <= ctx.state.dataset.cache.budget_bytes


def test_the_budget_counts_the_projected_columns(ctx):
    frame = get_rows(ctx, "A01")
    assert ctx.state.dataset.cache._sizes["A01"] == frame_bytes(frame)


def test_a_well_is_held_while_its_projectors_run(ctx, monkeypatch):
    """A read elsewhere — the gating worker's thread — can store another well
    mid-projection. Unpinned, that evicts the well being projected, the next
    projector finds nothing under its label, and the frame is returned with a
    verdict missing: read as fully included, with nothing to say so."""
    from review.data import data_loading as dl

    cache = ctx.state.dataset.cache
    cache.set_budget(1)

    def another_read(ctx_, label):
        cache["B99"] = _frame(10)

    def later_layer(ctx_, label):
        frame = ctx_.state.dataset.cache.get(label)
        if frame is not None:
            frame["Later_Pass"] = 0

    monkeypatch.setattr(dl, "iter_cache_projectors", lambda: [another_read, later_layer])
    frame = dl.load_and_project(ctx, "A01")
    assert "Later_Pass" in frame.columns
