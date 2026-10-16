"""The cell pool answers what numpy answers over the same cells (tracker F3a).

Both backends — DuckDB, and the numpy arrays used without it — are held to numpy
itself, value for value: the quantiles bit for bit (DuckDB's own
``quantile_cont`` is not, which is why the pool interpolates for itself), the
counts, ranges and histograms exactly. The sample is held to what it promises:
every row when the pool is no larger than the sample, in order; otherwise a
proportional share per group, every row a real cell with its real values, the
same rows on every call.
"""

from __future__ import annotations

import numpy as np
import pytest

from review.data import cell_pool
from review.data.cell_pool import CellPool, _allocate

BACKENDS = ["numpy", pytest.param("duckdb", marks=pytest.mark.skipif(
    cell_pool._duckdb() is None, reason="duckdb is not installed"))]


def _fill(backend, sizes=None, seed=0):
    """A pool of groups whose values differ in scale, so a quantile taken from
    the wrong group, or from the whole pool, comes out visibly different."""
    rng = np.random.default_rng(seed)
    sizes = sizes or {"ctrl": 1201, "drug": 333, "tiny": 3}
    pool = CellPool(["v", "w"], backend=backend)
    truth: dict[str, dict[str, list]] = {}
    for g_i, (group, n) in enumerate(sizes.items()):
        for w_i, well in enumerate((f"{group}_A", f"{group}_B")):
            m = n // 2 if w_i == 0 else n - n // 2
            rows = np.sort(rng.choice(5000, m, replace=False))
            v = rng.standard_normal(m) * 10 ** g_i
            w = rng.uniform(0, 1, m)
            tp = rng.choice([0.0, 24.0, 48.0], m)
            pool.add(group, well, rows, tp, {"v": v, "w": w})
            t = truth.setdefault(group, {"well": [], "row": [], "tp": [], "v": [], "w": []})
            t["well"] += [well] * m
            t["row"] += rows.tolist()
            t["tp"] += tp.tolist()
            t["v"] += v.tolist()
            t["w"] += w.tolist()
    return pool, truth


@pytest.mark.parametrize("backend", BACKENDS)
def test_quantiles_are_numpys_bit_for_bit(backend):
    pool, truth = _fill(backend)
    qs = [0, 0.05, 0.25, 0.5, 0.75, 0.95, 1, 0.3333, 0.999]
    with pool:
        everything = np.concatenate([truth[g]["v"] for g in truth])
        assert pool.quantiles("v", qs).tolist() == np.quantile(everything, qs).tolist()
        for group in truth:
            assert pool.quantiles("v", qs, group).tolist() == np.quantile(truth[group]["v"], qs).tolist()
        assert pool.quantiles("tp", [0.5], "drug").tolist() == np.quantile(truth["drug"]["tp"], [0.5]).tolist()


def test_duckdbs_own_quantile_is_not_numpys():
    """Why the pool interpolates for itself: on the same cells DuckDB's
    ``quantile_cont`` rounds differently. If this ever starts passing, the
    order-statistics detour in ``CellPool.quantiles`` is no longer needed."""
    duckdb = pytest.importorskip("duckdb")
    import pyarrow as pa

    rng = np.random.default_rng(1)
    con = duckdb.connect()
    qs = [0.05, 0.25, 0.5, 0.75, 0.95, 0.333]
    differ = 0
    for n in (2, 3, 7, 100, 1001):
        v = rng.standard_normal(n) * rng.choice([1e-6, 1.0, 1e4])
        con.register("t", pa.table({"v": v}))
        got = con.execute(f"SELECT quantile_cont(v, {qs}) FROM t").fetchone()[0]
        differ += sum(a != b for a, b in zip(got, np.quantile(v, qs)))
    assert differ > 0


@pytest.mark.parametrize("backend", BACKENDS)
def test_counts_ranges_and_histograms_are_exact(backend, monkeypatch):
    monkeypatch.setattr(cell_pool, "SCAN_CHUNK_ROWS", 50)      # the histogram spans many chunks
    pool, truth = _fill(backend)
    with pool:
        edges = np.linspace(-30, 30, 41)
        for group in truth:
            v = np.asarray(truth[group]["v"])
            assert pool.count(group) == v.size
            assert pool.value_range("v", group) == (v.min(), v.max())
            assert pool.histogram("v", edges, group).tolist() == np.histogram(v, edges)[0].tolist()
        assert pool.count() == sum(len(t["v"]) for t in truth.values())
        assert pool.groups() == list(truth)


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_scan_returns_every_cell_in_the_order_it_was_added(backend):
    pool, truth = _fill(backend)
    with pool:
        for group in truth:
            chunks = list(pool.scan(["v", "tp", "row", "well"], group, chunk_rows=97))
            if len(truth[group]["v"]) > 97:
                assert len(chunks) > 1, "one chunk; the chunking is not exercised"
            got = {k: np.concatenate([c[k] for c in chunks]).tolist() for k in ("v", "tp", "row", "well")}
            assert got == {k: truth[group][k] for k in ("v", "tp", "row", "well")}


def test_a_large_pool_is_scanned_in_insertion_order():
    """Large enough for DuckDB to scan in parallel, where a table without an
    ``ORDER BY`` comes back in whatever order its threads finish."""
    pytest.importorskip("duckdb")
    rng = np.random.default_rng(2)
    with CellPool(["v"], backend="duckdb") as pool:
        expected = []
        for i in range(8):
            v = rng.standard_normal(150_000)
            pool.add("g", f"W{i}", np.arange(v.size), np.zeros(v.size), {"v": v})
            expected.append(v)
        got = np.concatenate([c["v"] for c in pool.scan(["v"], "g", chunk_rows=100_000)])
        assert np.array_equal(got, np.concatenate(expected))


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_pool_no_larger_than_the_sample_returns_every_cell_in_order(backend):
    pool, truth = _fill(backend)
    with pool:
        got = pool.sample(10_000)
        for group in truth:
            s = got[group]
            assert (s.well.tolist(), s.row.tolist(), s.values["v"].tolist()) == (
                truth[group]["well"], truth[group]["row"], truth[group]["v"])
            assert s.total == len(truth[group]["v"])


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_sample_is_proportional_real_and_repeatable(backend):
    pool, truth = _fill(backend)
    with pool:
        got = pool.sample(200, seed=4)
        sizes = {g: len(s) for g, s in got.items()}
        assert sizes == {"ctrl": 156, "drug": 43, "tiny": 1}
        for group, s in got.items():
            cells = {(w, r): (v, t) for w, r, v, t in zip(truth[group]["well"], truth[group]["row"],
                                                          truth[group]["v"], truth[group]["tp"])}
            for w, r, v, t in zip(s.well, s.row, s.values["v"], s.tp):
                assert cells[(w, r)] == (v, t), "a sampled row does not carry its own cell's values"
            assert len(set(zip(s.well, s.row))) == len(s), "a cell was drawn twice"
            order = {cell: i for i, cell in enumerate(zip(truth[group]["well"], truth[group]["row"]))}
            positions = [order[cell] for cell in zip(s.well, s.row)]
            assert positions == sorted(positions), "the sample is not in the order the cells were added"
        again = pool.sample(200, seed=4)
        assert all(got[g].row.tolist() == again[g].row.tolist() for g in got)
        other = pool.sample(200, seed=5)
        assert any(got[g].row.tolist() != other[g].row.tolist() for g in got)


def test_the_allocation_sums_to_the_sample_and_keeps_every_group():
    assert _allocate(10, {"a": 990, "b": 9, "c": 1}) == {"a": 8, "b": 1, "c": 1}
    assert _allocate(100, {"a": 50, "b": 50}) == {"a": 50, "b": 50}
    assert _allocate(7, {"a": 3, "b": 0, "c": 30}) == {"a": 1, "b": 0, "c": 6}


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_non_finite_value_is_refused(backend):
    with CellPool(["v"], backend=backend) as pool:
        with pytest.raises(ValueError, match="non-finite"):
            pool.add("g", "A01", [0, 1], [0.0, 0.0], {"v": np.array([1.0, np.nan])})


@pytest.mark.parametrize("backend", BACKENDS)
def test_an_empty_group_answers_without_raising(backend):
    with CellPool(["v"], backend=backend) as pool:
        pool.add("g", "A01", [], [], {"v": np.array([])})
        assert pool.groups() == ["g"] and pool.count("g") == 0
        assert np.isnan(pool.quantiles("v", [0.5], "g")).all()
        assert pool.value_range("v", "g") is None
        assert len(pool.sample(10)["g"]) == 0


def test_the_spill_directory_is_removed_on_close():
    pytest.importorskip("duckdb")
    import os

    pool = CellPool(["v"], backend="duckdb")
    spill = pool._spill
    assert spill and os.path.isdir(spill)
    pool.close()
    assert not os.path.exists(spill)


def test_the_numpy_backend_can_be_forced(monkeypatch):
    monkeypatch.setenv("ALLWELL_CELL_POOL", "numpy")
    assert cell_pool.backend_name() == "numpy"
    with CellPool(["v"]) as pool:
        assert pool.backend == "numpy"


@pytest.mark.parametrize("backend", BACKENDS)
def test_box_counts_are_the_scatter_gates_own_comparisons(backend):
    """The gate share a figure prints, counted in the pool, equals counting the
    same cells with the scatter's ``_in_gate`` — edges inclusive, per timepoint."""
    from review.features.scatter.gating import _in_gate

    pool, truth = _fill(backend)
    with pool:
        for group in truth:
            v, w, tp = (np.asarray(truth[group][k]) for k in ("v", "w", "tp"))
            # Edges placed exactly on cell values, so inclusive and exclusive differ.
            box = {"x0": float(np.sort(v)[len(v) // 4]), "x1": float(np.sort(v)[3 * len(v) // 4]),
                   "y0": float(np.sort(w)[len(w) // 5]), "y1": float(np.sort(w)[-1])}
            assert pool.count_box("v", "w", box, group) == int(_in_gate(v, w, box).sum())
            for t in (0.0, 24.0):
                assert pool.count_box("v", "w", box, group, tps=[t]) == int(_in_gate(v[tp == t], w[tp == t], box).sum())
                assert pool.count_at(group, [t]) == int((tp == t).sum())
            assert pool.distinct("tp", group).tolist() == sorted(set(truth[group]["tp"]))
            assert pool.count_box("v", "w", box, group, tps=[]) == 0


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_per_group_cap_samples_each_group_to_it(backend):
    pool, truth = _fill(backend)
    with pool:
        got = pool.sample(100, per_group=True)
        assert {g: len(s) for g, s in got.items()} == {"ctrl": 100, "drug": 100, "tiny": 3}
        assert got["tiny"].row.tolist() == truth["tiny"]["row"]


def test_a_pool_nobody_closed_still_removes_its_spill_directory():
    """Dropped by an exception, or held until exit: the directory must not stay behind."""
    pytest.importorskip("duckdb")
    import gc
    import os

    pool = CellPool(["v"], backend="duckdb")
    spill = pool._spill
    del pool
    gc.collect()
    assert not os.path.exists(spill)


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_closed_pool_answers_as_empty(backend):
    pool, _truth = _fill(backend)
    pool.close()
    assert pool.count("ctrl") == 0 and pool.count() == 0
    assert pool.count_at("ctrl", [0.0]) == 0
    assert pool.count_box("v", "w", {"x0": -1e9, "x1": 1e9, "y0": -1e9, "y1": 1e9}, "ctrl") == 0
