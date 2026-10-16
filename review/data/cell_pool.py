"""A pool of a selection's included cells, held in bounded memory (tracker F3a).

The per-cell views — Scatter, Distribution, the bar chart's beeswarm and violin,
Cell Gating's CDFs — draw individual cells, so a selection of most of a plate of
1.1M-cell wells asks them for tens of millions of values. Two different things
are asked of those values, and they need different answers:

* **The numbers a figure states** — a count, a median, a fraction, a gate's
  percentage — are statements about *every* included cell, and must be computed
  over every one of them.
* **The points a figure draws** cannot usefully be tens of millions, and are
  drawn from a *stated* sample once the selection is larger than a view's cap.

A :class:`CellPool` holds the included cells once, so both can be answered from
it without a view keeping them in Python. Its rows are added one well at a time
by the view, **after** the view's own Python filter — the inclusion verdicts
stay in Python (tracker Q5) — and hold the group, the well, the row's index in
the well's frame, the timepoint and the view's value columns.

**Backends.** With DuckDB installed (tracker Q6) the rows live in an in-process
DuckDB table under a memory limit (:data:`DEFAULT_MEMORY_MB`, overridden by
``ALLWELL_CELL_POOL_MB``) and a spill directory, so the pool stays inside that
limit however many cells are selected. Without it, the same interface holds
numpy arrays — what the views held before, so a missing engine costs memory
and nothing else. :func:`backend_name` says which is in use;
``ALLWELL_CELL_POOL=numpy`` forces the second.

**What each question is answered by, and why.** Every answer equals what numpy
gives over the same cells — ``tests/test_cell_pool.py`` holds both backends to
it value for value:

* :meth:`CellPool.quantiles` takes the two order statistics around each
  quantile from DuckDB and interpolates them with numpy's own linear rule
  (:func:`_numpy_linear`). DuckDB's ``quantile_cont`` rounds its interpolation
  differently — it differed from ``np.quantile`` in 35 of 280 values measured —
  and a median that moves in the sixteenth digit between a small selection and
  a large one is a difference nobody could explain.
* :meth:`CellPool.scan` streams the rows in chunks, so a statistic whose rule is
  the app's own Python — the tolerant timepoint match, a gate's box — is
  computed by that same Python, a chunk at a time, rather than re-expressed in
  SQL where it could drift.
* :meth:`CellPool.sample` draws a proportional sample per group: each group's
  share of the sample is its share of the cells (largest remainder, and at least
  one cell for a group that has any), chosen by a seeded hash of each row's
  insertion number, so the same selection draws the same cells every time and
  the rows come back in the order they were added. When the pool holds no more
  than the sample asks for, every row is returned, in order — so below a view's
  cap the view draws exactly what it drew before.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from types import ModuleType

import numpy as np

_logger = logging.getLogger("review")

#: The DuckDB memory limit a pool runs under, in MiB, unless
#: ``ALLWELL_CELL_POOL_MB`` says otherwise. Measured on 26.5M included cells
#: (24 wells of 1.1M): loading them and answering a sample, a 2D density and
#: pooled quantiles took 6.2 s at a 1.4 GiB process peak with this limit.
DEFAULT_MEMORY_MB = 512

#: Rows handed back per chunk by :meth:`CellPool.scan`.
SCAN_CHUNK_ROWS = 1 << 20

_engine_warned = False


def _duckdb() -> ModuleType | None:
    """``duckdb``, or ``None`` when it is not installed or switched off."""
    global _engine_warned
    if os.environ.get("ALLWELL_CELL_POOL", "").strip().lower() == "numpy":
        return None
    try:
        import duckdb
        return duckdb
    except ImportError:
        if not _engine_warned:
            _engine_warned = True
            _logger.info("duckdb is not installed, so the per-cell views pool a selection's cells "
                         "in memory; large selections cost memory in proportion to their size")
        return None


def backend_name() -> str:
    """``"duckdb"`` or ``"numpy"`` — the backend a new :class:`CellPool` would use."""
    return "duckdb" if _duckdb() is not None else "numpy"


def _numpy_linear(sorted_at, n: int, qs) -> np.ndarray:
    """``np.quantile(values, qs)`` from the order statistics alone.

    *sorted_at* maps a 0-based rank to the value at that rank. This is numpy's
    own linear method (``numpy.lib.function_base._quantile`` with its
    ``"linear"`` entry: virtual index ``(n - 1) * q``, gamma its fractional part,
    and ``_lerp``'s two-sided interpolation), written out so that only the
    ranks it needs are ever fetched.
    """
    q = np.asanyarray(qs, dtype=float)
    virtual = (n - 1) * q
    prev = np.floor(virtual)
    gamma = np.asanyarray(virtual - prev)
    prev_i = np.where(virtual >= n - 1, n - 1, np.where(virtual < 0, 0, prev)).astype(np.intp)
    next_i = np.where(virtual >= n - 1, n - 1, np.where(virtual < 0, 0, prev + 1)).astype(np.intp)
    ranks = sorted(set(prev_i.tolist()) | set(next_i.tolist()))
    values = sorted_at(ranks)
    a = np.array([values[r] for r in prev_i.tolist()], dtype=float)
    b = np.array([values[r] for r in next_i.tolist()], dtype=float)
    diff = np.subtract(b, a)
    out = np.asanyarray(np.add(a, diff * gamma))
    np.subtract(b, diff * (1 - gamma), out=out, where=gamma >= 0.5)
    return out


def _allocate(n: int, sizes: dict[str, int]) -> dict[str, int]:
    """Split a sample of *n* across groups in proportion to *sizes*.

    Largest remainder, so the parts sum to exactly *n*; a group with any cells
    gets at least one, so no group vanishes from a figure for being small.
    """
    total = sum(sizes.values())
    live = {g: s for g, s in sizes.items() if s > 0}
    if total <= n:
        return dict(sizes)
    exact = {g: n * s / total for g, s in live.items()}
    parts = {g: max(1, int(e)) for g, e in exact.items()}
    order = sorted(live, key=lambda g: exact[g] - int(exact[g]), reverse=True)
    i = 0
    while sum(parts.values()) < n and order:
        g = order[i % len(order)]
        if parts[g] < live[g]:
            parts[g] += 1
        i += 1
    while sum(parts.values()) > n:
        g = max(parts, key=lambda k: parts[k])
        parts[g] -= 1
    return {g: parts.get(g, 0) for g in sizes}


@dataclass
class Sampled:
    """One group's sampled rows, in the order they were added to the pool."""

    well: np.ndarray
    row: np.ndarray
    tp: np.ndarray
    values: dict[str, np.ndarray] = field(default_factory=dict)
    #: How many cells the group holds in the pool — what the sample was drawn from.
    total: int = 0

    def __len__(self) -> int:
        return int(self.row.size)


class CellPool:
    """A selection's included cells. See the module docstring."""

    def __init__(self, columns: Sequence[str], *, backend: str | None = None):
        self.columns = list(columns)
        self._groups: list[str] = []
        self._wells: list[str] = []
        self._sizes: dict[str, int] = {}
        self._seq = 0
        duckdb = None if backend == "numpy" else _duckdb()
        if backend == "duckdb" and duckdb is None:
            raise RuntimeError("duckdb is not available")
        self.backend = "duckdb" if duckdb is not None else "numpy"
        self._con = None
        self._spill: str | None = None
        self._parts: list[dict[str, np.ndarray]] = []
        if duckdb is not None:
            self._spill = tempfile.mkdtemp(prefix="allwell-cellpool-")
            mb = int(os.environ.get("ALLWELL_CELL_POOL_MB") or DEFAULT_MEMORY_MB)
            self._con = duckdb.connect(config={"memory_limit": f"{mb}MB", "temp_directory": self._spill})
            cols = "".join(f", c{i} DOUBLE" for i in range(len(self.columns)))
            self._con.execute(f"CREATE TABLE cells (seq BIGINT, grp INTEGER, well INTEGER, rn BIGINT, tp DOUBLE{cols})")

    # ── lifetime ────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None
        if self._spill is not None:
            shutil.rmtree(self._spill, ignore_errors=True)
            self._spill = None
        self._parts = []

    def __enter__(self) -> CellPool:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── filling ─────────────────────────────────────────────────────────────

    def add(self, group: str, well: str, rows, tp, values: dict[str, np.ndarray]) -> None:
        """Add one well's included cells to *group*.

        *rows* are the cells' indices in the well's frame (what a click maps
        back through), *tp* their timepoints, *values* one array per pool
        column. The values must be finite: they are the cells the view has
        already filtered, and a NaN would sort past every number in DuckDB and
        before none in numpy.
        """
        rows = np.asarray(rows, dtype=np.int64)
        n = int(rows.size)
        if n == 0:
            if group not in self._sizes:
                self._groups.append(group)
                self._sizes[group] = 0
            return
        cols = []
        for name in self.columns:
            arr = np.asarray(values[name], dtype=float)
            if arr.size != n:
                raise ValueError(f"{name}: {arr.size} values for {n} rows")
            if not np.isfinite(arr).all():
                raise ValueError(f"{name}: non-finite values reached the pool")
            cols.append(arr)
        if group not in self._sizes:
            self._groups.append(group)
            self._sizes[group] = 0
        if well not in self._wells:
            self._wells.append(well)
        grp = self._groups.index(group)
        wid = self._wells.index(well)
        seq = np.arange(self._seq, self._seq + n, dtype=np.int64)
        self._seq += n
        self._sizes[group] += n
        tp = np.asarray(tp, dtype=float)
        if self._con is None:
            self._parts.append({"seq": seq, "grp": np.full(n, grp, dtype=np.int32),
                                "well": np.full(n, wid, dtype=np.int32), "rn": rows, "tp": tp,
                                **{f"c{i}": c for i, c in enumerate(cols)}})
            return
        import pyarrow as pa

        table = pa.table({"seq": seq, "grp": np.full(n, grp, dtype=np.int32),
                          "well": np.full(n, wid, dtype=np.int32), "rn": rows, "tp": tp,
                          **{f"c{i}": c for i, c in enumerate(cols)}})
        self._con.register("__aw_part", table)
        try:
            self._con.execute("INSERT INTO cells SELECT * FROM __aw_part")
        finally:
            self._con.unregister("__aw_part")

    # ── questions ───────────────────────────────────────────────────────────

    def groups(self) -> list[str]:
        """The groups, in the order they were first added."""
        return list(self._groups)

    def count(self, group: str | None = None) -> int:
        if group is None:
            return int(sum(self._sizes.values()))
        return int(self._sizes.get(group, 0))

    def _col(self, column: str) -> str:
        if column == "tp":
            return "tp"
        return f"c{self.columns.index(column)}"

    def _where(self, group: str | None) -> tuple[str, list]:
        if group is None:
            return "", []
        return " WHERE grp = ?", [self._groups.index(group)]

    def _numpy(self, keys: Sequence[str], group: str | None) -> dict[str, np.ndarray]:
        if not self._parts:
            return {k: np.array([]) for k in keys}
        out = {k: np.concatenate([p[k] for p in self._parts]) for k in (*keys, "grp")}
        if group is not None:
            keep = out["grp"] == self._groups.index(group)
            out = {k: v[keep] for k, v in out.items()}
        return out

    def quantiles(self, column: str, qs, group: str | None = None) -> np.ndarray:
        """``np.quantile`` of *column* over every cell of *group* (or the pool); NaN when empty."""
        qs = np.asanyarray(qs, dtype=float)
        n = self.count(group)
        if n == 0:
            return np.full(qs.shape, np.nan)
        col = self._col(column)
        if self._con is None:
            return np.quantile(self._numpy([col], group)[col], qs)
        where, params = self._where(group)

        def at(ranks):
            marks = ", ".join(str(int(r)) for r in ranks)
            got = self._con.execute(
                f"SELECT rk, v FROM (SELECT {col} AS v, row_number() OVER (ORDER BY {col}) - 1 AS rk "
                f"FROM cells{where}) WHERE rk IN ({marks})", params).fetchall()
            return {int(r): float(v) for r, v in got}

        return _numpy_linear(at, n, qs)

    def value_range(self, column: str, group: str | None = None) -> tuple[float, float] | None:
        """``(min, max)`` of *column* over every cell, or ``None`` when there are none."""
        if self.count(group) == 0:
            return None
        col = self._col(column)
        if self._con is None:
            v = self._numpy([col], group)[col]
            return float(v.min()), float(v.max())
        where, params = self._where(group)
        lo, hi = self._con.execute(f"SELECT min({col}), max({col}) FROM cells{where}", params).fetchone()
        return float(lo), float(hi)

    def scan(self, columns: Sequence[str], group: str | None = None,
             chunk_rows: int | None = None) -> Iterator[dict[str, np.ndarray]]:
        """Every cell of *group* (or the pool), in insertion order, a chunk at a time.

        Each chunk maps the requested pool columns (and ``"tp"``, ``"row"``,
        ``"well"`` and ``"group"`` when asked) to arrays. For a statistic whose
        rule is the app's own Python, applied chunk by chunk.
        """
        chunk_rows = chunk_rows or SCAN_CHUNK_ROWS
        names = {"row": "rn", "well": "well", "group": "grp"}
        cols = [names.get(c) or self._col(c) for c in columns]
        if self._con is None:
            data = self._numpy(["seq", *cols], group)
            order = np.argsort(data["seq"], kind="stable")
            n = order.size
            for start in range(0, n, chunk_rows):
                idx = order[start:start + chunk_rows]
                yield self._decode(columns, cols, {c: data[c][idx] for c in cols})
            return
        where, params = self._where(group)
        result = self._con.execute(f"SELECT {', '.join(cols)} FROM cells{where} ORDER BY seq", params)
        reader = result.fetch_record_batch(chunk_rows)
        for batch in reader:
            yield self._decode(columns, cols, {c: batch.column(i).to_numpy(zero_copy_only=False)
                                               for i, c in enumerate(cols)})

    def _decode(self, columns, cols, raw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        out = {}
        for name, col in zip(columns, cols, strict=True):
            arr = raw[col]
            if name == "well":
                arr = np.array([self._wells[i] for i in arr], dtype=object)
            elif name == "group":
                arr = np.array([self._groups[i] for i in arr], dtype=object)
            out[name] = arr
        return out

    def histogram(self, column: str, edges, group: str | None = None) -> np.ndarray:
        """``np.histogram(values, edges)[0]`` over every cell, accumulated chunk by chunk."""
        edges = np.asarray(edges, dtype=float)
        counts = np.zeros(max(edges.size - 1, 0), dtype=np.int64)
        for chunk in self.scan([column], group):
            counts += np.histogram(chunk[column], edges)[0]
        return counts

    def distinct(self, column: str, group: str | None = None) -> np.ndarray:
        """The distinct values of *column* over every cell of *group* (or the pool), sorted."""
        if self.count(group) == 0:
            return np.array([])
        col = self._col(column)
        if self._con is None:
            return np.unique(self._numpy([col], group)[col])
        where, params = self._where(group)
        got = self._con.execute(f"SELECT DISTINCT {col} FROM cells{where} ORDER BY 1", params).fetchall()
        return np.array([v for (v,) in got], dtype=float)

    def count_box(self, xcol: str, ycol: str, box: dict, group: str | None = None,
                  tps: Sequence[float] | None = None) -> int:
        """How many cells of *group* lie inside *box*, optionally only at the timepoints *tps*.

        The box is ``{"x0", "x1", "y0", "y1"}`` with ``x0 <= x1`` and ``y0 <= y1``,
        and its edges are inclusive — the same four comparisons as the scatter's
        ``_in_gate``, which SQL evaluates identically on finite doubles. *tps*
        are values exactly as the pool holds them (see :meth:`distinct`), so a
        tolerant match is decided by the caller's own rule, not here.
        """
        if self.count(group) == 0 or (tps is not None and len(tps) == 0):
            return 0
        x, y = self._col(xcol), self._col(ycol)
        if self._con is None:
            data = self._numpy([x, y, "tp"], group)
            keep = ((data[x] >= box["x0"]) & (data[x] <= box["x1"])
                    & (data[y] >= box["y0"]) & (data[y] <= box["y1"]))
            if tps is not None:
                keep &= np.isin(data["tp"], np.asarray(tps, dtype=float))
            return int(keep.sum())
        where, params = self._where(group)
        clause = (f"{x} >= ? AND {x} <= ? AND {y} >= ? AND {y} <= ?")
        params = [*params, float(box["x0"]), float(box["x1"]), float(box["y0"]), float(box["y1"])]
        if tps is not None:
            clause += f" AND tp IN ({', '.join('?' for _ in tps)})"
            params += [float(t) for t in tps]
        sql = f"SELECT count(*) FROM cells{where}{' AND ' if where else ' WHERE '}{clause}"
        return int(self._con.execute(sql, params).fetchone()[0])

    def count_at(self, group: str | None = None, tps: Sequence[float] | None = None) -> int:
        """How many cells of *group* there are, optionally only at the exact timepoints *tps*."""
        if tps is None:
            return self.count(group)
        if self.count(group) == 0 or len(tps) == 0:
            return 0
        if self._con is None:
            data = self._numpy(["tp"], group)
            return int(np.isin(data["tp"], np.asarray(tps, dtype=float)).sum())
        where, params = self._where(group)
        clause = f"tp IN ({', '.join('?' for _ in tps)})"
        sql = f"SELECT count(*) FROM cells{where}{' AND ' if where else ' WHERE '}{clause}"
        return int(self._con.execute(sql, [*params, *(float(t) for t in tps)]).fetchone()[0])

    def sample(self, n: int, *, seed: int = 0) -> dict[str, Sampled]:
        """A proportional sample of *n* cells, per group; every cell when the pool holds no more."""
        parts = _allocate(n, self._sizes)
        out: dict[str, Sampled] = {}
        for group in self._groups:
            k = parts.get(group, 0)
            gi = self._groups.index(group)
            cols = ["rn", "well", "tp", *(f"c{i}" for i in range(len(self.columns)))]
            if self._con is None:
                data = self._numpy(["seq", *cols], group)
                order = np.argsort(data["seq"], kind="stable")
                data = {c: v[order] for c, v in data.items()}
                if k < self._sizes[group]:
                    keys = _hash(data["seq"], seed)
                    keep = np.sort(np.argsort(keys, kind="stable")[:k])
                    data = {c: v[keep] for c, v in data.items()}
            else:
                pick = (f"SELECT * FROM cells WHERE grp = {gi}" if k >= self._sizes[group] else
                        f"SELECT * FROM (SELECT * FROM cells WHERE grp = {gi} "
                        f"ORDER BY hash(seq + {int(seed)} * 1000003) LIMIT {int(k)})")
                table = self._con.execute(f"SELECT {', '.join(['seq', *cols])} FROM ({pick}) ORDER BY seq").arrow()
                data = {c: table.column(c).to_numpy(zero_copy_only=False) for c in ["seq", *cols]}
            out[group] = Sampled(
                well=np.array([self._wells[i] for i in data["well"]], dtype=object),
                row=np.asarray(data["rn"], dtype=np.int64),
                tp=np.asarray(data["tp"], dtype=float),
                values={name: np.asarray(data[f"c{i}"], dtype=float) for i, name in enumerate(self.columns)},
                total=self._sizes[group],
            )
        return out


def cells_upper_bound(ctx, wells) -> int:
    """An upper bound on how many cells *wells* hold: their row counts.

    From the per-well summaries an open records, so it reads nothing; a well
    with no summary counts its held frame, or is read. A view compares this with
    its cap to decide, before reading anything, whether a selection can be
    drawn whole — every included cell is a row, so a selection whose rows fit
    under the cap fits whatever the filters remove.
    """
    from review.data.data_loading import get_rows

    dataset = ctx.state.dataset
    summaries = getattr(dataset, "summaries", None) or {}
    cache = getattr(dataset, "cache", None) or {}
    total = 0
    for well in dict.fromkeys(wells):
        summary = summaries.get(well)
        if summary is not None:
            total += int(summary.n_rows)
            continue
        frame = cache.get(well) if well in cache else None
        if frame is None:
            frame = get_rows(ctx, well)
        total += 0 if frame is None else len(frame)
    return total


def _hash(seq: np.ndarray, seed: int) -> np.ndarray:
    """The numpy backend's sampling key: a seeded, well-mixed permutation of *seq*."""
    x = (seq.astype(np.uint64) + np.uint64(seed) * np.uint64(1000003)) * np.uint64(0x9E3779B97F4A7C15)
    x ^= x >> np.uint64(31)
    x *= np.uint64(0xBF58476D1CE4E5B9)
    x ^= x >> np.uint64(29)
    return x
