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

**What a read costs.** ``tracemalloc`` cannot answer this — Arrow allocates
outside the Python allocator, so it reports a cached read at under 1 MB — so it
is measured as resident memory, sampled from ``/proc/self/statm`` every 2 ms in a
fresh process per read, on a 1.5M-row well of 38 columns whose frame is 399 MiB
(435 MB of ``.csv.gz``). Figures are over the process's size before the read:

=====================  =========  =================  ========
read                   peak       resident after     time
=====================  =========  =================  ========
cache hit              587 MiB    437 MiB            0.5 s
CSV parse (a miss)     892 MiB    509 MiB            7.8 s
=====================  =========  =================  ========

They were 1,169 / 1,169 MiB and 2,788 / 2,380 MiB before three changes, each
of which leaves the frame identical: :func:`read_cached` reads without
``pre_buffer`` (which held the whole compressed file beside the table it
decoded to), every Arrow table becomes a frame through :func:`table_to_frame`,
and the CSV is read block by block (``data_loading._streamed_csv_read``).
A miss also writes the cache, which converts the frame back to Arrow; with that
the read measured 995 MiB at its peak.
``tests/test_a_read_costs_about_one_frame.py`` holds both the identity and the
cost.

**The cache is written from the frame the CSV parsed to, never from a projected
one.** ``load_and_project`` writes ``QC_Pass`` / ``Crop_Pass`` / ``Gate_Pass``
into the cached frame *after* the read, from parameters the user can change at
any time. Caching a projected frame would bake one session's QC verdict onto
disk and serve it to the next session under different thresholds — a wrong
number with no way to notice.
``tests/test_parquet_cache.py::test_the_cache_is_written_before_any_verdict_is_projected``
holds that as a guard, because it is the one mistake here that produces silently
wrong analysis rather than a crash. ``Included`` is deliberately not in that
list: ``load_well_csv`` itself adds it (defaulting to 1 when the CSV has no such
column), so it belongs to the parse and is cached with the rest of it.

**A dataset folder that cannot be written keeps its cache on this machine.**
A read-only share, or a Windows-formatted (NTFS) drive on a Mac, which macOS
mounts read-only, cannot hold a ``.aw_parquet`` folder. Before, such a dataset
was parsed in full on every open — on a USB hard disk, minutes for a plate that
opens in seconds once cached. :func:`cache_dir` sends those writes to
``row-cache/`` in the app's folder instead (``util.state_dir.local_row_cache_dir``,
one folder per dataset folder, named for a hash of its path), and says so once
per folder in the log. It decides by asking whether the folder can be written,
and also switches when a write there fails with an ``OSError``, because a
volume can refuse writes its permission bits allow. Reads look in the dataset's
own ``.aw_parquet`` first and then in the local folder (:func:`read_paths`), so
a read-only share that someone else already cached is still read from its own
copy, and a cache in either place is used whenever its fingerprint matches.
The storage button offers the local copies for clearing (``core.local_storage``).

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

from core.safe import swallow
from util.csv_gz import csv_stem, existing_csv
from util.state_dir import local_row_cache_dir

_logger = logging.getLogger("review.parquet_cache")

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

#: Whether Arrow may run a well's decode and its conversion to pandas on its own C++ thread pool. **No**: every
#: Arrow step of a well's load — the Parquet decode (:func:`read_cached`), the CSV parse
#: (``data_loading._arrow_read``) and the conversion (:func:`table_to_frame`) — runs on the calling worker's thread.
#:
#: Reported: a Review session reading a 61-well plate from a network drive ended in ``Fatal Python error:
#: Aborted`` while a worker was inside ``pyarrow.parquet.read_table`` for the Cell Gating tally, and faulthandler
#: labelled none of the three Python threads ``Current thread`` — the abort was raised on a thread Python does not
#: own, and Arrow's pool threads are the only native work in that path. On the calling thread, an abort here would
#: name its own Python stack in the boot log, and an allocation failure becomes a ``MemoryError`` on a thread that
#: can catch it instead of a process-wide terminate. The conversion's ``self_destruct`` is marked EXPERIMENTAL by
#: pyarrow itself, which is one more reason not to run it across a pool. Measured on a 120,000-row x 37-column well
#: with 4 CPUs (pyarrow 22): a cache hit about the same (56 ms threaded, 57 ms here, conversion included), a CSV
#: parse 102 -> 229 ms (median of 3 to 5 reads; a CSV is parsed only on a cache miss).
#:
#: A single-threaded Parquet decode on its own costs memory, not only time: :func:`_read_table` says how it reads
#: one column at a time to keep the pool's peak at about one table.
#:
#: What it covers, counted in a fresh process: a cache hit — the path the abort was in — starts no thread at all
#: (with the pool it started three). A CSV parse, which happens only on a cache miss, still starts Arrow's I/O
#: read-ahead threads (one for ``read_csv``, two for the streamed ``open_csv``) whatever ``use_threads`` says: they
#: read the file's blocks, the parse itself runs here. ``tests/test_parquet_cache.py`` holds the cache-hit count.
ARROW_THREADS = False

#: The per-well summaries (``review/data/well_summary.py``) live in the same directory.
_SUMMARY_SUFFIX = ".summary.json"

#: Parquet key-value metadata key holding the source CSV's fingerprint.
_FINGERPRINT_KEY = b"aw_source_fingerprint"

#: Bumped whenever :func:`data_loading.load_well_csv` would produce a different
#: frame from the same bytes.
#:
#: **The thing being reused is not the CSV — it is the parse.** The file's size
#: and mtime describe the input; they say nothing about the function that turned
#: it into a frame, and that function is not stable. It decides which columns are
#: text (``_STRING_COLS``), which become categories
#: (``_CATEGORY_MAX_UNIQUE_SHARE``), how per-channel measurement columns are
#: renamed, how empty and ``-1`` FOVs are normalised, that ``Included`` defaults
#: to 1, and it back-fills ``shape_circularity`` for datasets written before that
#: column existed. Every one of those has changed at least once.
#:
#: Without this component, adding a derived column to ``load_well_csv`` means an
#: untouched CSV keeps serving the frame from before it — the column simply is
#: not there, the dropdown that offers it does not appear, and a plot keyed on it
#: draws nothing. No error, no log line: §7's degraded output that is
#: indistinguishable from good output, arrived at by omission.
#:
#: ``tests/test_parquet_cache.py::test_a_changed_parser_invalidates_every_cache``
#: is what makes this a rule rather than a note: it pins the number against a
#: hash of ``load_well_csv``'s own source and the constants it reads, so editing
#: the parser without bumping this fails.
#: 2: the parse moved to ``pyarrow.csv`` (``data_loading._read_well_table``), whose
#: floats are correctly rounded where pandas' default parser was not.
PARSER_VERSION = 2

#: Set once a missing engine has been reported, so a 96-well load says it once.
_engine_warned = False

#: Dataset folders whose cache is kept on this machine (:func:`cache_dir`), by
#: the folder's path, so the decision and its log line are made once per folder.
_local_dirs: dict[str, Path] = {}

#: Exception type names already reported by :func:`write_cache`, so a plate whose
#: output volume is read-only says so once rather than once per well per open.
_write_warned: set[str] = set()


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
                "CSV on every load. Installing it makes that read roughly 60x "
                "faster on a large well; nothing else changes."
            )
        return None


def release_arrow_memory() -> None:
    """Return the memory Arrow's allocator holds but no longer uses to the system.

    Arrow allocates through its own pool (mimalloc in the pinned wheel), which
    keeps freed pages for reuse rather than returning them, and pandas and numpy
    allocate through the C library and cannot use them. So the transient of an
    Arrow read — the CSV reader's retained blocks, a Parquet read's buffers, the
    table a frame was converted from — stays resident after the read and sits
    underneath everything that follows. Measured on a 1.5M-row, 399 MiB well: a
    CSV read left 1,865 MiB resident and 629 MiB after this call; a Parquet read
    left 1,172 MiB and 418 MiB.

    pyarrow documents the call as possibly expensive, since the pages it returns
    must be faulted in again by the next allocation. Once or twice per well read,
    against a read of a second or more, it is not measurable. A no-op without
    pyarrow.
    """
    try:
        import pyarrow as pa
    except ImportError:
        return
    pa.default_memory_pool().release_unused()


def table_to_frame(table) -> pd.DataFrame:
    """Convert a well's Arrow *table* to pandas at about one frame's cost, consuming it.

    **The table must not be used after this call.** ``self_destruct=True`` frees
    each column's Arrow buffers as soon as that column is converted, and pyarrow
    states that touching the table afterwards crashes the process. Every caller
    passes a table it built for this call and holds no other reference to.

    The default conversion consolidates — one 2-D block per dtype, allocated
    whole while the whole table is still alive — so its peak is the table plus
    the frame. ``split_blocks=True`` makes one block per column instead, which is
    what lets ``self_destruct`` free a column before the next is allocated; either
    option alone measured no lower than the default (1,172 MiB and 858 MiB over
    the process's starting size on a 399 MiB frame read from Parquet), both
    together 585 MiB. The frame's values, dtypes, categories and their order,
    column order and index are the same as the default conversion's
    (``tests/test_a_read_costs_about_one_frame.py``); only its internal block
    layout differs, and that has two costs, both measured on pandas 2.2.3:

    * pandas never consolidates a held frame in place, but a **deep copy** of a
      split frame consolidates the copy, and peaks at about three times what it
      copies (348 MiB for a 119 MiB frame, against 119 MiB consolidated); so does
      ``assign``. Taking many columns at once with ``to_numpy`` costs twice. The
      one deep copy of a well's rows in the app, the cell-scope export's slice,
      is shallow for that reason (``export/cell_scope_compute.build_chunk``).
      Column reads, boolean ``.loc`` selection, ``groupby`` and ``concat`` cost
      the same either way.
    * adding a column to a frame holding more than 100 numpy blocks raises
      pandas' ``PerformanceWarning`` ("highly fragmented"). A well with over a
      hundred numeric columns would show it once per adding site; it is a
      warning about speed, and the per-block overhead it describes is
      microseconds against a million-row column.

    **Every column comes back writable.** With one block per column pyarrow hands
    a single-chunk numeric column without nulls, and a category column's codes,
    to pandas without copying — as a *read-only* view of the Arrow buffer — and
    the app writes into its frames in place (curation's
    ``df.loc[mask, "Included"] = …`` raised ``assignment destination is
    read-only``). Those columns are copied one at a time here, which costs what
    the default conversion spent on them anyway, one column at a time instead of
    all at once.

    The pool is released before and after: before, so the transient that built
    the table (the CSV reader's or the Parquet decoder's) is not resident under
    the frame being allocated; after, so the table's buffers are not resident
    under everything that follows.
    """
    release_arrow_memory()
    frame = table.to_pandas(self_destruct=True, split_blocks=True, use_threads=ARROW_THREADS)
    del table
    for position in range(frame.shape[1]):
        column = frame.iloc[:, position]
        # ``_ndarray`` is the backing array of both a plain numpy column and a
        # Categorical (its codes); a numpy-backed column has no other form.
        backing = getattr(column.array, "_ndarray", None)
        if backing is not None and not backing.flags.writeable:
            frame.isetitem(position, column.copy())
    release_arrow_memory()
    return frame


def own_cache_dir(csv_dir) -> Path:
    """The ``.aw_parquet`` folder beside the CSVs in *csv_dir*, whether or not it can be written."""
    return Path(csv_dir) / CACHE_DIRNAME


def _can_write(csv_dir: Path) -> bool:
    own = own_cache_dir(csv_dir)
    return os.access(own if own.is_dir() else csv_dir, os.W_OK)


def keep_locally(csv_dir: Path, why: str) -> Path:
    """Send *csv_dir*'s cache writes to this machine from now on, saying so once; return the folder."""
    key = str(csv_dir)
    local = _local_dirs.get(key)
    if local is None:
        local = local_row_cache_dir(csv_dir)
        _local_dirs[key] = local
        _logger.warning("%s cannot be written (%s), so the fast copies of its CSVs are kept on this "
                        "machine instead, in %s. Opening it again stays fast.", csv_dir, why, local)
    return local


def cache_dir(csv_dir) -> Path:
    """Where the caches of the CSVs in *csv_dir* are written.

    Their own ``.aw_parquet`` folder, unless the dataset folder cannot be
    written; then a folder on this machine (module docstring). Decided once
    per folder per session.
    """
    csv_dir = Path(csv_dir)
    local = _local_dirs.get(str(csv_dir))
    if local is not None:
        return local
    if _can_write(csv_dir):
        return own_cache_dir(csv_dir)
    return keep_locally(csv_dir, "it is read-only")


def cache_path(csv_path: Path) -> Path:
    """Where the cached frame for *csv_path* is written (:func:`cache_dir`)."""
    csv_path = Path(csv_path)
    return cache_dir(csv_path.parent) / f"{csv_stem(csv_path.name)}.parquet"


def read_paths(csv_path: Path, suffix: str = ".parquet") -> list[Path]:
    """The places a cache file for *csv_path* is looked for, in order: the dataset's own folder, then this machine's.

    *suffix* names the file kind (``.parquet``, or ``.summary.json`` for
    ``well_summary``). The caller still checks each file's fingerprint; a file
    in either place that matches is current.
    """
    csv_path = Path(csv_path)
    name = f"{csv_stem(csv_path.name)}{suffix}"
    own = own_cache_dir(csv_path.parent) / name
    written = cache_dir(csv_path.parent) / name
    if written == own:
        return [own]
    return [own, written]


def _current_cache(pq, csv_path: Path, want: str) -> tuple[Path, object] | None:
    """The first of :func:`read_paths` whose fingerprint is *want*, with its schema; ``None`` when none is."""
    for path in read_paths(csv_path):
        if not path.exists():
            continue
        try:
            schema = pq.read_schema(path)
        except Exception as exc:  # noqa: BLE001 - an unreadable cache is a miss
            _logger.debug("parquet schema unreadable for %s (%s)", path, exc)
            continue
        if (schema.metadata or {}).get(_FINGERPRINT_KEY, b"").decode() == want:
            return path, schema
    return None


def source_fingerprint(csv_path: Path) -> str | None:
    """``"<size>:<mtime_ns>:v<PARSER_VERSION>"``, or ``None`` if it cannot be read.

    Size and modification time rather than a content hash: the file is tens of
    megabytes and this runs once per well per load, while the thing being
    guarded against is the pipeline rewriting the CSV — which changes both.
    :data:`PARSER_VERSION` covers the other half of the reuse decision, which is
    that the same bytes do not always parse to the same frame.

    **One blind spot, named rather than papered over.** A filesystem whose mtime
    resolution is one second — HFS+, and several common SMB and NFS mount
    configurations, which is exactly where a shared microscopy plate tends to
    live — can record a rewrite that lands inside the same second, with the same
    byte count, as no change at all. It is not reachable on ext4 or APFS
    (measured: 2,000 back-to-back rewrites produced 2,000 distinct
    ``st_mtime_ns``, the smallest positive gap being 65 µs). Where it is
    reachable, :func:`write_cache`'s re-stat closes the window this app can
    actually open — a rewrite *during* a parse — and what remains is a rewrite
    that both starts and finishes between two reads inside one second.
    """
    try:
        st = os.stat(csv_path)
    except OSError:
        return None
    return f"{st.st_size}:{st.st_mtime_ns}:v{PARSER_VERSION}"


def cached_column_names(csv_path: Path) -> list[str] | None:
    """The Parquet cache's column names, when a current cache exists; read from the schema alone."""
    pq = _pyarrow()
    want = source_fingerprint(csv_path)
    if pq is None or want is None:
        return None
    found = _current_cache(pq, Path(csv_path), want)
    if found is None:
        return None
    return [n for n in found[1].names if not n.startswith("__index_level_")]


def read_cached(csv_path: Path, columns: list[str] | None = None) -> pd.DataFrame | None:
    """The cached frame for *csv_path*, or ``None`` for a miss of any kind.

    *columns* reads only those (the ones the file has; absent names are
    skipped). Only for a consumer that never puts the frame in the row cache —
    see "Whole frames for the row cache" below; ``well_summary`` is the one.

    A miss is never an error: no engine, no cache file, a fingerprint that does
    not match the CSV beside it, or a file that will not parse all mean the same
    thing to the caller — parse the CSV. Only the last is worth a log line, and
    it is logged at debug because a corrupt cache repairs itself on the next
    write.

    **Whole frames for the row cache, deliberately.** Parquet is columnar and a
    subset read is the larger win — six of thirty-seven columns measured 7 ms
    against 51 ms on a 120,000-row well — but the row cache
    (``ctx.state.dataset.cache``) has nowhere to put a partial frame. It holds
    one frame per well and every ``get_rows`` caller reads whatever column it
    needs from it; a partial frame there answers a later caller's column with a
    ``KeyError`` at best and a silently-empty plot at worst, and the verdict
    projectors fail open on a missing input column, so a QC metric left out
    would read as every cell passing. So ``read_well_frame`` never passes
    *columns*. The per-well summaries (``review/data/well_summary.py``) do: they
    read the few columns a dataset's open needs and never enter the row cache.
    """
    pq = _pyarrow()
    if pq is None:
        return None
    want = source_fingerprint(csv_path)
    if want is None:
        return None
    # A cache whose fingerprint differs describes an older CSV and is passed over.
    found = _current_cache(pq, Path(csv_path), want)
    if found is None:
        return None
    path, schema = found
    try:
        subset = None
        if columns is not None:
            present = set(schema.names)
            subset = [c for c in columns if c in present]
        # ``pre_buffer=False``: the default coalesces and reads every column
        # chunk's compressed bytes into memory ahead of decoding them, which on
        # a single local file buys nothing and holds the whole file beside the
        # table it decodes to. See "What a read costs" in the module docstring.
        return table_to_frame(_read_table(pq, path, subset))
    except Exception as exc:  # noqa: BLE001 - any unreadable cache is just a miss
        _logger.debug("parquet cache unreadable for %s (%s); parsing the CSV",
                      csv_path.name, exc)
        return None


def _read_table(pq, path: Path, columns):
    """The cache file's table (*columns* of it, or all), decoded on this thread (:data:`ARROW_THREADS`).

    **One column at a time.** A single-threaded ``read_table`` measured an Arrow pool peak of 4.05x the table on the
    150,000-row fixture of ``tests/test_a_read_costs_about_one_frame.py`` (1.55x with the pool), and so did a
    single-threaded read of each row group; reading each column on its own, single-threaded, peaked at 1.12x and
    took the same time as the threaded read (57 against 56 ms on a 120,000-row x 37-column well, conversion
    included). The table is the one ``read_table`` returns — the same columns, types, values and schema metadata."""
    import pyarrow as pa

    if ARROW_THREADS:
        return pq.read_table(path, columns=columns, pre_buffer=False)
    handle = pq.ParquetFile(path, pre_buffer=False)
    schema = handle.schema_arrow
    names = list(schema.names) if columns is None else list(columns)
    fields = [schema.field(name) for name in names]
    arrays = [handle.read(columns=[name], use_threads=False).column(0) for name in names]
    return pa.table(arrays, schema=pa.schema(fields, metadata=schema.metadata))


def write_cache(csv_path: Path, frame, fingerprint: str | None = None) -> bool:
    """Cache *frame* for *csv_path*. Returns whether it landed.

    *frame* must be the CSV's own parse — see this module's docstring.

    **Pass the fingerprint taken before the parse.** Reading a 120,000-row well
    takes three seconds, and stamping the file's state as it is *afterwards*
    records the new file's identity against the old file's rows if anything
    rewrote the CSV in between — an Analyze run or a remote fetch writing into
    an ``out/`` that Review is part-way through loading, which on a 96-well cold
    load is a window minutes wide. The cache then looks fresh and is wrong on
    every subsequent read, for ever: §7's cardinal case, reached without a bug
    anywhere in the parse.

    So the caller stats first and hands the answer here, and this re-stats and
    **declines** when the two disagree — a cache not written costs one parse
    next time, where a cache written wrongly costs the user's numbers. Omitting
    the argument keeps the old behaviour and is only right when the frame did
    not come from a read of this file.

    Written through a staging file in the destination directory and an
    ``os.replace``, the way every other file this app rewrites is: a half-written
    cache read as a frame would be a plate whose numbers are missing rows, and a
    crash mid-write must leave the previous cache or none. The staging name
    carries the pid so two processes cannot collide on it; a process killed
    between the write and the replace leaves one behind, which the next
    successful write for that well removes.

    A failure is logged once per session per reason, and returns False. A
    read-only output volume is an ordinary state — the run journal already treats
    it as one — and it must cost the user a slower load, never their dataset.
    """
    pq = _pyarrow()
    if pq is None:
        return False
    current = source_fingerprint(csv_path)
    if current is None:
        return False
    if fingerprint is not None and fingerprint != current:
        _logger.info(
            "%s changed while it was being parsed, so the parsed frame is not "
            "cached — it describes the previous contents. The next load will "
            "read the new file.", csv_path.name)
        return False
    fingerprint = current
    csv_path = Path(csv_path)
    path = cache_path(csv_path)
    try:
        import pyarrow as pa

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
        table = table.replace_schema_metadata(meta)
        try:
            _write_through_staging(pq, table, path)
        except OSError as exc:
            if path.parent != own_cache_dir(csv_path.parent):
                raise
            # The folder said it could be written and the volume refused anyway
            # (a read-only mount whose permission bits allow writing): keep this
            # folder's cache on this machine from here on, and write it there.
            path = keep_locally(csv_path.parent, f"{type(exc).__name__}: {exc}") / path.name
            _write_through_staging(pq, table, path)
        return True
    except Exception as exc:  # noqa: BLE001 - a cache that cannot be written is not fatal
        # Once per session per reason, not once per well. A read-only output
        # volume fails identically for all 96 wells of a plate, on every open;
        # 96 identical WARNINGs in the log drawer is how a real message gets
        # missed, and the sentence is the same one every time.
        reason = type(exc).__name__
        if reason not in _write_warned:
            _write_warned.add(reason)
            _logger.warning("could not cache %s as Parquet (%s); the CSV is "
                            "still the source and will be parsed on every "
                            "load. Further failures of this kind are not "
                            "repeated.", csv_path.name, exc)
        return False


def _write_through_staging(pq, table, path: Path) -> None:
    """Write *table* to *path* through a staging file and ``os.replace``; the staging file never outlives a failure."""
    tmp = path.with_name(f"__aw_tmp_{path.name}.pid{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    except BaseException:
        with swallow("remove the abandoned Parquet staging file"):
            if tmp.exists():
                tmp.unlink()
        raise


def prune(csv_dir) -> int:
    """Drop cache files with nothing left to cache, under *csv_dir*. Returns how many.

    The cache costs real disk — on a plate-shaped well of 20,000 rows the
    Parquet is 66% of the CSV's size, so a 96-well plate of that shape adds
    about 400 MB beside 600 MB of CSVs. That is the price of the feature and is
    stated rather than swept away; what is *not* acceptable is the part nobody
    can reclaim.

    Two kinds of file have no owner and are removed here:

    * a ``<well>.parquet`` whose ``<well>.csv`` is gone — the well was renamed
      or deleted, and nothing will ever read or overwrite that entry again;
    * a ``<well>.summary.json`` (``review/data/well_summary.py``) in the same case;
    * a ``__aw_tmp_*`` staging file, left by a process killed between the write
      and the ``os.replace``. Every one of them is dead by definition: a live
      write holds its own name and replaces it within milliseconds, and a
      *different* live process's staging file carries that process's pid, so
      the worst case of removing one is that a write in flight fails and falls
      back to parsing the CSV. That is the cache's ordinary failure, not a
      user-visible one.

    Deliberately **not** an eviction policy. Nothing here removes a cache whose
    CSV is still present, however old, because "old" is not a property this can
    read: a plate opened once a year is exactly the one whose five-minute parse
    the cache exists to save.

    Every failure is skipped rather than raised — this runs on the load path and
    a directory it cannot tidy must never cost the user their dataset.
    """
    removed = 0
    for directory in (own_cache_dir(csv_dir), local_row_cache_dir(Path(csv_dir))):
        removed += _prune_one(Path(csv_dir), directory)
    return removed


def _prune_one(csv_dir: Path, directory: Path) -> int:
    """:func:`prune` for one cache folder of *csv_dir* — its own, or the one on this machine."""
    removed = 0
    if not directory.is_dir():
        return 0
    with swallow(f"prune the Parquet cache under {directory}"):
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            orphan = (entry.name.startswith("__aw_tmp_")
                      or (entry.suffix == ".parquet"
                          and existing_csv(Path(csv_dir) / f"{entry.stem}.csv") is None)
                      or (entry.name.endswith(_SUMMARY_SUFFIX)
                          and existing_csv(Path(csv_dir) / f"{entry.name[:-len(_SUMMARY_SUFFIX)]}.csv")
                          is None))
            if not orphan:
                continue
            with swallow(f"remove the orphaned cache file {entry.name}"):
                entry.unlink()
                removed += 1
    if removed:
        _logger.info("removed %d orphaned Parquet cache file(s) from %s",
                     removed, directory)
    return removed
