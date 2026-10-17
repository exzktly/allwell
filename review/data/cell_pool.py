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
``ALLWELL_CELL_POOL_MB``), in a database file inside a private spill directory,
so the pool stays inside that limit however many cells are selected: rows past
it are compressed into that file rather than evicted raw to DuckDB's temp
directory, whose cap of 90% of the free disk a large selection once exhausted.
The file has a cap of its own in that cap's place: :meth:`CellPool.add` refuses
a well whose rows would take the volume below :data:`DISK_RESERVE` (a tenth) of
what was free when the pool opened, so a large selection never drives the disk
to zero under every other writer on it. A pool that runs out of room — that cap,
or the disk filling anyway (DuckDB then raises ``TransactionException`` at a
commit or ``FatalException`` at a checkpoint, the second invalidating the
database) — raises :class:`CellPoolFullError`, naming the directory and its free
space. A failure inside DuckDB also closes the pool: it then answers as an empty
one, keeps the error on :attr:`CellPool.failure`, and refuses every later add
with it, rather than raising "database has been invalidated" from each question
a figure asks. Each spill directory carries its owner's pid
(:data:`OWNER_FILE`), and the first pool a process opens removes the
directories of processes that no longer exist — what a crash or a force-quit
mid-load leaves behind. Without DuckDB, the same interface holds
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

import glob
import logging
import os
import shutil
import tempfile
import time
import weakref
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

#: The share of the free disk (measured when a pool opens) a pool leaves free: the cap DuckDB's own temp directory
#: had (``max_temp_directory_size``, 90% of the free space), applied to the database file that replaced it.
DISK_RESERVE = 0.10

#: The spill directories' prefix under the system temp directory, and the file in each naming its owner's pid.
SPILL_PREFIX = "allwell-cellpool-"
OWNER_FILE = "owner.pid"
#: A spill directory with no owner file is swept only once it is this old (seconds): a pool marks its directory
#: just after creating it, and one caught between the two must not be swept from under it.
UNMARKED_STALE_S = 24 * 3600

_engine_warned = False
_swept = False


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
        self._finalizer = None
        #: Why the pool stopped holding cells, when a DuckDB failure closed it; ``None`` otherwise.
        self.failure: CellPoolError | None = None
        self._reserve = 0
        if duckdb is not None:
            sweep_stale_spills()
            self._spill = tempfile.mkdtemp(prefix=SPILL_PREFIX)
            with open(os.path.join(self._spill, OWNER_FILE), "w", encoding="ascii") as handle:
                handle.write(f"{os.getpid()}\n")
            self._reserve = int(DISK_RESERVE * _free_bytes(self._spill))
            mb = int(os.environ.get("ALLWELL_CELL_POOL_MB") or DEFAULT_MEMORY_MB)
            # A database *file* in the spill directory, not ``:memory:``. An
            # in-memory table that outgrows the memory limit is evicted to the
            # temp directory uncompressed, and DuckDB caps that directory at
            # 90% of the disk's free space when the pool opens
            # (``max_temp_directory_size``) — so a large selection on a nearly
            # full disk failed mid-load with "Out of Memory Error: failed to
            # offload data block (2.2 GiB/2.2 GiB used)". A file-backed table is
            # compressed at each checkpoint and evicted to its own file, which
            # that cap does not govern: 26.4M cells of four values took 797 MB
            # on disk this way against 2.7 GB of temp files in memory, and
            # loaded faster. The temp directory is still there for the sorts a
            # quantile runs.
            self._con = duckdb.connect(os.path.join(self._spill, "cells.duckdb"),
                                       config={"memory_limit": f"{mb}MB",
                                               "temp_directory": os.path.join(self._spill, "tmp")})
            # A pool nobody closed — dropped by an exception, or held until the
            # app exits — still releases its connection and removes its spill
            # directory, when it is collected or at interpreter exit.
            self._finalizer = weakref.finalize(self, _release, self._con, self._spill)
            cols = "".join(f", c{i} DOUBLE" for i in range(len(self.columns)))
            self._con.execute(f"CREATE TABLE cells (seq BIGINT, grp INTEGER, well INTEGER, rn BIGINT, tp DOUBLE{cols})")

    # ── lifetime ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Release the pool. A closed pool holds no cells and answers as an empty one.

        Answering as empty rather than with the counts it used to hold matters to
        a caller that kept an object reading it (a scatter group's gate
        population): a count above zero beside a box count of zero would print
        "0.0%" for a gate holding cells. Empty, the reading is "no cells".
        """
        if self._finalizer is not None:
            self._finalizer()
        self._con = None
        self._spill = None
        self._parts = []
        self._sizes = {g: 0 for g in self._sizes}

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
        if self.failure is not None:
            raise self.failure
        # The group and well are listed only once their rows are in
        # (``_counted``), so a refused first well leaves no empty group behind;
        # their numbers here are the positions they will take.
        grp = self._groups.index(group) if group in self._sizes else len(self._groups)
        wid = self._wells.index(well) if well in self._wells else len(self._wells)
        seq = np.arange(self._seq, self._seq + n, dtype=np.int64)
        tp = np.asarray(tp, dtype=float)
        if self._con is None:
            self._parts.append({"seq": seq, "grp": np.full(n, grp, dtype=np.int32),
                                "well": np.full(n, wid, dtype=np.int32), "rn": rows, "tp": tp,
                                **{f"c{i}": c for i, c in enumerate(cols)}})
            self._counted(group, well, n)
            return
        # The rows' raw size, which the WAL holds before a checkpoint compresses them.
        need = n * 8 * (5 + len(cols))
        if _free_bytes(self._spill) - need < self._reserve:
            raise CellPoolFullError(self._spill, self.count(), None, reason=(
                f"{need / 2**20:.1f} MB more would leave less than a tenth of the free space it started with "
                f"({self._reserve * 10 / 2**30:.1f} GiB)"))
        import pyarrow as pa

        table = pa.table({"seq": seq, "grp": np.full(n, grp, dtype=np.int32),
                          "well": np.full(n, wid, dtype=np.int32), "rn": rows, "tp": tp,
                          **{f"c{i}": c for i, c in enumerate(cols)}})
        duckdb = duckdb_errors()
        self._con.register("__aw_part", table)
        try:
            self._con.execute("INSERT INTO cells SELECT * FROM __aw_part")
        except duckdb.Error as exc:
            # Any DuckDB failure here: a full disk is a TransactionException at
            # the commit or a FatalException at the checkpoint, and the second
            # invalidates the database for every later query. So the pool closes
            # and answers as empty from now on, keeping the reason.
            held = self.count()
            failure = (CellPoolFullError(self._spill, held, exc) if _out_of_room(exc, self._spill, self._reserve)
                       else CellPoolError(f"the cell pool's database failed after {held:,} cells: {exc}"))
            self.close()
            self.failure = failure
            raise failure from exc
        finally:
            if self._con is not None:
                self._con.unregister("__aw_part")
        self._counted(group, well, n)

    def _counted(self, group: str, well: str, n: int) -> None:
        """List *group* and *well* and count *n* rows — only once the rows are in, so the names and the counts
        describe the rows the pool actually holds."""
        if group not in self._sizes:
            self._groups.append(group)
            self._sizes[group] = 0
        if well not in self._wells:
            self._wells.append(well)
        self._seq += n
        self._sizes[group] += n

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

    def sample(self, n: int, *, seed: int = 0, per_group: bool = False) -> dict[str, Sampled]:
        """A proportional sample of *n* cells, per group; every cell when the pool holds no more.

        With *per_group*, *n* is each group's own cap instead: a group of no
        more than *n* cells is returned whole, a larger one sampled to *n* —
        for a figure whose columns are drawn side by side rather than pooled.
        """
        parts = ({g: min(n, size) for g, size in self._sizes.items()} if per_group
                 else _allocate(n, self._sizes))
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


def duckdb_errors() -> ModuleType:
    """The ``duckdb`` module, for its exception classes; only called with a live connection."""
    import duckdb
    return duckdb


class CellPoolError(RuntimeError):
    """The pool's database failed while cells were being added; the pool is closed and answers as empty."""


class CellPoolFullError(CellPoolError):
    """The pool ran out of room: its disk cap reached, or the disk itself full.

    Raised from :meth:`CellPool.add` in place of DuckDB's own error, whose
    wording ("Out of Memory", "Failed to commit", "Failed to create
    checkpoint") points anywhere but at the disk under the system temp
    directory. The message names that directory, its free space, and how many
    cells were held.
    """

    def __init__(self, spill: str | None, held: int, cause: BaseException | None, *, reason: str = ""):
        free = "unknown"
        if spill:
            try:
                free = f"{_free_bytes(spill) / 2**30:.1f} GiB"
            except OSError as exc:
                _logger.debug("cell pool free-space probe failed (%s)", exc)
        super().__init__(
            f"the cell pool ran out of room after {held:,} cells: it keeps "
            f"{os.environ.get('ALLWELL_CELL_POOL_MB') or DEFAULT_MEMORY_MB} MB in memory and "
            f"spills the rest to {os.path.dirname(spill or '') or 'the temp directory'}, "
            f"which has {free} free. Free disk space there, point TMPDIR at a larger "
            f"disk, or select fewer wells. ({reason or cause})")


def _free_bytes(path: str) -> int:
    """Free bytes on the volume holding *path*."""
    return int(shutil.disk_usage(path).free)


#: What DuckDB says when a write found no room: the disk full, or a file at its size limit.
_NO_ROOM = ("no space left", "file too large", "disk quota", "out of memory", "failed to offload")


def _out_of_room(exc: BaseException, spill: str | None, reserve: int) -> bool:
    """Whether a DuckDB failure during an add was the pool running out of room rather than anything else."""
    duckdb = duckdb_errors()
    if isinstance(exc, (duckdb.OutOfMemoryException, duckdb.IOException)):
        return True
    if any(phrase in str(exc).lower() for phrase in _NO_ROOM):
        return True
    try:
        return spill is not None and _free_bytes(spill) <= reserve
    except OSError as probe:
        _logger.debug("cell pool free-space probe failed (%s)", probe)
        return False


def _release(con, spill: str | None) -> None:
    """Close *con* and remove *spill* — a pool's cleanup, run once by its finalizer."""
    try:
        con.close()
    except Exception as exc:  # noqa: BLE001 - a connection already gone is released
        _logger.debug("cell pool connection close failed (%s)", exc)
    if spill:
        shutil.rmtree(spill, ignore_errors=True)
        if os.path.exists(spill):
            # A file still open elsewhere (on Windows, a scan nobody finished) keeps it; the next
            # process's sweep removes it once this one has exited.
            _logger.info("cell pool spill directory %s could not be removed yet", spill)


def _pid_alive(pid: int) -> bool:
    """Whether a process *pid* exists. Errs towards ``True``: a directory is swept only when its owner is gone."""
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() != 87                      # ERROR_INVALID_PARAMETER: no such process
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return not ok or code.value == 259                      # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                                                 # it exists, owned by someone else
    return True


def sweep_stale_spills() -> list[str]:
    """Remove the spill directories of pools whose process no longer exists; the paths removed.

    A pool closed or collected removes its own directory; one killed with its process (a crash, a force-quit
    mid-load) leaves it, with the compressed table inside, and nothing else would ever remove it. A directory is
    swept only when its :data:`OWNER_FILE` names a process that is gone, or, with no owner file, once it is
    older than :data:`UNMARKED_STALE_S`. Runs once per process, from the first pool it opens.
    """
    global _swept
    if _swept:
        return []
    _swept = True
    removed = []
    for path in glob.glob(os.path.join(tempfile.gettempdir(), SPILL_PREFIX + "*")):
        try:
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            owner = os.path.join(path, OWNER_FILE)
            if os.path.exists(owner):
                with open(owner, encoding="ascii") as handle:
                    text = handle.read().strip()
                if not text.isdigit() or _pid_alive(int(text)):
                    continue
            elif time.time() - os.path.getmtime(path) < UNMARKED_STALE_S:
                continue
            shutil.rmtree(path)
            removed.append(path)
        except OSError as exc:
            _logger.debug("cell pool sweep skipped %s (%s)", path, exc)
    if removed:
        _logger.info("removed %d cell pool spill director%s left by processes that have exited",
                     len(removed), "y" if len(removed) == 1 else "ies")
    return removed


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
