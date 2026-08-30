"""A Parquet cache beside each per-well CSV, and the fingerprint that invalidates it.

**The CSV stays the source of truth.** The pipeline writes it, users open it in
Excel, and every tool in the app reads it. This is a cache of the *parsed* frame,
written once and read many times, and a miss of any kind simply falls back to
parsing the CSV.

Why it is worth a dependency, measured through
:func:`data_loading.read_well_frame` itself rather than through a bare
``read_parquet``. One well of 120,000 rows x 37 columns, with the long
pipeline-style filename in every row that makes a real CSV expensive to parse —
32.2 MB of CSV, 22.5 MB of Parquet:

=========================  =========  ========
read                       time       speedup
=========================  =========  ========
CSV parse                   3,068 ms        --
cache hit, all columns         51 ms     60.6x
cache hit, 6 columns            7 ms    415.8x
=========================  =========  ========

A 96-well plate of that size spends five minutes parsing text on every open; the
same plate reads in about five seconds, and a plot that wants six of
thirty-seven columns never materialises the other thirty-one.

The gain scales with the well, so a small plate sees less of it: the same
measurement at 40,000 rows is 13x and at 5,000 rows 7x. Nothing here is worse
than the CSV path at any size.

**The memory figure is deliberately not quoted.** ``tracemalloc`` reports the
CSV parse at 148.9 MB of Python allocations and the cached read at under 1 MB,
but Arrow allocates its buffers outside the Python allocator, so that second
number measures the instrument rather than the read. What is honest to say is
that the CSV path's allocations are gone; what the Arrow path costs instead has
not been measured here.

**The cache is written from the frame the CSV parsed to, never from a projected
one.** ``load_and_project`` writes ``QC_Pass`` / ``Crop_Pass`` / ``Included``
into the cached frame *after* the read, from parameters the user can change at
any time. Caching a projected frame would bake one session's QC verdict onto
disk and serve it to the next session under different thresholds — a wrong
number with no way to notice. ``tests/test_parquet_cache_is_not_projected.py``
holds that as a guard, because it is the one mistake here that produces silently
wrong analysis rather than a crash.

**pyarrow is optional at runtime.** Every entry point answers "no" when it is
absent, so an install without it — the pipeline-only one, or a partial
environment — reads CSVs exactly as it always did. Nothing in ``pipeline/`` or
``services/`` may import this module.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

_logger = logging.getLogger("well_viewer.parquet_cache")

#: Where a well's cached frame lives, relative to its CSV. A subdirectory, and
#: dot-prefixed. Both halves were checked rather than assumed: every scan that
#: enumerates a dataset's per-well CSVs is a non-recursive ``out_dir.glob(
#: "*.csv")`` (``load_controller``, ``dataset_paths``, ``validate_compute``,
#: ``pipeline.completeness``), which a subdirectory cannot match whatever it
#: holds; the recursive scans in the app are over the *input* side and filter on
#: image extensions. The dot is the second line of defence, in the same spirit
#: as ``.allwell-transit``, and is what ``load_controller``'s own
#: ``startswith(".")`` filter would catch if the cache were ever flattened.
CACHE_DIRNAME = ".aw_parquet"

#: Parquet key-value metadata key holding the source CSV's fingerprint.
_FINGERPRINT_KEY = b"aw_source_fingerprint"

#: Set once a missing engine has been reported, so a 96-well load says it once.
_engine_warned = False


def _pyarrow() -> ModuleType | None:
    """``pyarrow.parquet``, or ``None`` when it is not installed.

    Imported here rather than at module scope so importing this module costs
    nothing, and so an environment without pyarrow degrades to the CSV path
    instead of failing to start.
    """
    global _engine_warned
    try:
        import pyarrow.parquet as pq
        return pq
    except ImportError:
        if not _engine_warned:
            _engine_warned = True
            _logger.info(
                "pyarrow is not installed, so per-well frames are parsed from "
                "CSV on every load. Installing it makes that read roughly 77x "
                "faster; nothing else changes."
            )
        return None


def cache_path(csv_path: Path) -> Path:
    """The cached frame's path for *csv_path*."""
    csv_path = Path(csv_path)
    return csv_path.parent / CACHE_DIRNAME / f"{csv_path.stem}.parquet"


def source_fingerprint(csv_path: Path) -> str | None:
    """``"<size>:<mtime_ns>"`` for *csv_path*, or ``None`` when it cannot be read.

    Size and modification time rather than a content hash: the file is tens of
    megabytes and this runs once per well per load, while the thing being
    guarded against is the pipeline rewriting the CSV — which changes both.
    """
    try:
        st = os.stat(csv_path)
    except OSError:
        return None
    return f"{st.st_size}:{st.st_mtime_ns}"


def read_cached(csv_path: Path) -> pd.DataFrame | None:
    """The cached frame for *csv_path*, or ``None`` for a miss of any kind.

    A miss is never an error: no engine, no cache file, a fingerprint that does
    not match the CSV beside it, or a file that will not parse all mean the same
    thing to the caller — parse the CSV. Only the last is worth a log line, and
    it is logged at debug because a corrupt cache repairs itself on the next
    write.

    **Whole frames only, deliberately.** Parquet is columnar and a subset read
    is the larger win — six of thirty-seven columns measured 7 ms against 51 ms
    on a 120,000-row well — but there is nowhere to put a partial frame. The row
    cache (``ctx.state.dataset.cache``) holds one frame per well and fifty-six
    ``get_rows`` call sites read whatever column they need from it; a projected
    frame in there answers a later caller's column with a ``KeyError`` at best
    and a silently-empty plot at worst, which is §7's shortfall that looks like
    a result. No read path in the app bypasses that cache, so no caller can take
    a subset safely today, and a ``columns`` parameter with no caller is a seam
    for an implementation that has not arrived. It comes back when a consumer
    exists that does not populate the row cache — the query layer a DuckDB
    engine would need is the obvious one.
    """
    pq = _pyarrow()
    if pq is None:
        return None
    path = cache_path(csv_path)
    if not path.exists():
        return None
    want = source_fingerprint(csv_path)
    if want is None:
        return None
    try:
        meta = pq.read_schema(path).metadata or {}
        if meta.get(_FINGERPRINT_KEY, b"").decode() != want:
            return None  # the CSV was rewritten; the cache describes the old one
        return pq.read_table(path).to_pandas()
    except Exception as exc:  # noqa: BLE001 - any unreadable cache is just a miss
        _logger.debug("parquet cache unreadable for %s (%s); parsing the CSV",
                      csv_path.name, exc)
        return None


def write_cache(csv_path: Path, frame) -> bool:
    """Cache *frame* for *csv_path*. Returns whether it landed.

    *frame* must be the CSV's own parse — see this module's docstring.

    Written through a staging file in the destination directory and an
    ``os.replace``, the way every other file this app rewrites is: a half-written
    cache read as a frame would be a plate whose numbers are missing rows, and a
    crash mid-write must leave the previous cache or none.

    A failure is logged once and returns False. A read-only output volume is an
    ordinary state — the run journal already treats it as one — and it must cost
    the user a slower load, never their dataset.
    """
    pq = _pyarrow()
    if pq is None:
        return False
    fingerprint = source_fingerprint(csv_path)
    if fingerprint is None:
        return False
    path = cache_path(csv_path)
    tmp = path.with_name(f"__aw_tmp_{path.name}.pid{os.getpid()}")
    try:
        import pyarrow as pa

        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        # The fingerprint goes *beside* whatever Arrow already put in the
        # schema, never in place of it: ``replace_schema_metadata`` replaces the
        # whole mapping, so passing a bare dict would drop pandas' block. Note
        # what that block is and is not responsible for — the categorical dtypes
        # survive without it, because Arrow encodes a category as a
        # ``DictionaryType`` in the schema proper and ``to_pandas`` reads it
        # back from there. Stripping it measured identical on a real per-well
        # frame. It is carried because it is what reconstructs an index and the
        # dtypes Arrow cannot express on its own, and this frame is not the only
        # shape that will ever reach here.
        meta = dict(table.schema.metadata or {})
        meta[_FINGERPRINT_KEY] = fingerprint.encode()
        pq.write_table(table.replace_schema_metadata(meta), tmp)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001 - a cache that cannot be written is not fatal
        _logger.warning("could not cache %s as Parquet (%s); the CSV is still "
                        "the source and will be parsed on every load",
                        csv_path.name, exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
