"""A well's read costs about one frame, and gives the frame it always gave (re-read tracker R8).

Reading one well used to cost several times the frame it produced. Measured in a
fresh process per read on a 1.5M-row, 399 MiB well (``.csv.gz``), peak resident
memory over the process's starting size:

* **CSV**, 2,788 MiB at 10.2 s, of which 2,380 MiB was still resident after the
  read. ``pyarrow.csv.read_csv`` keeps every block's parsed text until the end
  of the file in case its type inference must re-convert a column (Arrow's pool
  peaked at 1,657 MiB for a 514 MiB table); the conversion to pandas allocated
  the consolidated frame beside the whole table; ``rename`` copied the frame;
  and Arrow's allocator kept all of it after it was freed.
* **Parquet**, 1,169 MiB, all of it still resident afterwards: ``read_table``
  pre-buffered the whole file beside the table it decoded to, and the
  conversion again allocated the frame beside the table.

After: CSV 892 MiB at 7.8 s (509 MiB resident afterwards), Parquet 587 MiB
(437 MiB). The changes, each in ``review/data/data_loading.py`` or
``review/data/parquet_cache.py``: the CSV is read block by block
(``_streamed_csv_read``), whole-file only when a later block changes a column's
type; Arrow tables become frames through ``parquet_cache.table_to_frame``
(``self_destruct`` + ``split_blocks``, every column made writable, the Arrow pool
released around it); Parquet is read without pre-buffering; the rename is in
place; and FOV labels are normalised once per distinct label.

None of that is allowed to change the frame. **These tests compare each read
against the old one in the same tree** — the reference is the same function
with the old pieces patched back in: the whole-file ``read_csv``, the default
``to_pandas()``, the per-row FOV normalisation, and a plain ``read_table``. The
comparison is of everything a caller can see: values, dtypes, categories and
their order, column order and labels, the index, and that every column can be
written in place. The fixtures carry what real well CSVs carry: empty, ``-1``,
padded, decimal and opaque field labels, NaNs, mixed-case headers, a text column
worth storing as categories and one that is not, stray text in a numeric column,
a boolean column, and a column that is empty for the first blocks and numeric
after them (which the streamed read must decline).

The memory claim itself is held by :func:`test_a_read_costs_about_one_frame`,
which measures Arrow's pool and the process's resident memory in a fresh
process and fails on the old code.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pyarrow.csv")

from review.data import data_loading as dl  # noqa: E402
from review.data import parquet_cache as pc  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

#: Rows in the identity fixtures: enough for the CSV to span several of the
#: streamed reader's 1 MiB blocks, so its first-block type inference is tested
#: against later blocks rather than against the whole file.
_ROWS = 24_000


def _messy_well(path: Path, *, late_numbers: bool, rows: int = _ROWS) -> Path:
    """A well CSV carrying real-data mess; *late_numbers* adds a column that is
    blank for the first five eighths of the rows and numeric after them."""
    rng = np.random.default_rng(3)
    n = rows
    fov = rng.integers(1, 12, n).astype(str).astype(object)
    for label, share in (("", 0.03), ("-1", 0.03), (" 4 ", 0.01), ("3.0", 0.01), ("02", 0.01), ("F001", 0.01)):
        fov[rng.random(n) < share] = label
    hours = rng.choice([0, 24, 48], n)
    gfp = rng.lognormal(5, 1, n)
    gfp[rng.random(n) < 0.02] = np.nan
    stray = rng.normal(size=n).astype(object)
    stray[4] = "abc"            # in the first block, so both readers read the column as text
    frame = pd.DataFrame({
        "Well": "A01",
        "FOV": fov,
        "timepoint": [f"{h}h" for h in hours],
        "Channel": "DAPI",                                      # one value: stored as categories
        "filename": [f"plate_A01_f{f}_t{h}h_{i}.tif" for i, (f, h) in enumerate(zip(fov, hours))],  # unique: not
        "experiment": np.where(rng.random(n) < 0.01, "NA", "exp1"),
        "nucleus_id": np.arange(1, n + 1),
        "GFP_mean_intensity": gfp,
        "area_px": rng.integers(50, 400, n).astype(float),
        "shape_perimeter": rng.uniform(10, 80, n),
        "flag": np.where(rng.random(n) < 0.5, "True", "False"),
        "stray": stray,
    })
    for i in range(24):                    # a real well is mostly measurement columns
        frame[f"m{i}"] = rng.normal(size=n)
    if late_numbers:
        late = rng.normal(size=n).astype(object)
        late[:n * 5 // 8] = ""
        frame["late"] = late
    text = frame.to_csv(index=False, na_rep="")
    if path.suffix == ".gz":
        with gzip.open(path, "wt", newline="") as fh:
            fh.write(text)
    else:
        path.write_text(text)
    return path


def _reference_csv_read(path: Path, monkeypatch) -> pd.DataFrame:
    """``load_well_csv`` with the old read path patched back in."""
    with monkeypatch.context() as m:
        m.setattr(dl, "_streamed_csv_read", lambda *_a, **_k: None)
        m.setattr(pc, "table_to_frame", lambda table: table.to_pandas())
        m.setattr(dl, "_normalize_fov_by_value", dl.normalize_fov_series)
        return dl.load_well_csv(path)


def _unwritable_columns(frame: pd.DataFrame) -> list[str]:
    out = []
    for position, name in enumerate(frame.columns):
        backing = getattr(frame.iloc[:, position].array, "_ndarray", None)
        if backing is not None and not backing.flags.writeable:
            out.append(name)
    return out


def _assert_same_frame(got: pd.DataFrame, expected: pd.DataFrame) -> None:
    assert list(got.columns) == list(expected.columns), "column order or labels differ"
    assert got.columns.dtype == expected.columns.dtype
    assert type(got.index) is type(expected.index) and got.index.equals(expected.index), "index differs"
    assert list(got.dtypes) == list(expected.dtypes), "dtypes differ"
    for name in got.columns:
        if isinstance(got[name].dtype, pd.CategoricalDtype):
            assert list(got[name].cat.categories) == list(expected[name].cat.categories), (
                f"{name}: categories or their order differ")
    pd.testing.assert_frame_equal(got, expected, check_exact=True, check_categorical=True,
                                  check_column_type=True, check_index_type=True)
    assert _unwritable_columns(got) == [], (
        f"{_unwritable_columns(got)} cannot be written in place; the app's frames are "
        "(curation writes Included with .loc), and the old read's always could")


@pytest.mark.parametrize("suffix", [".csv", ".csv.gz"])
@pytest.mark.parametrize("late_numbers", [False, True], ids=["streamed", "declined"])
def test_the_csv_read_gives_the_old_frame(tmp_path, monkeypatch, suffix, late_numbers):
    """The new CSV read equals the old one, on the streamed path and on its fallback.

    The wrong implementation this is built to catch: a streamed read that keeps
    its first block's types when a later block does not fit them — the
    ``declined`` fixture's ``late`` column would come back as nulls, or as the
    wrong type, rather than as the whole-file read's floats. Also: a conversion
    that drops or reorders categories, a column order or index that moves, and
    zero-copy columns left read-only.
    """
    path = _messy_well(tmp_path / f"A01{suffix}", late_numbers=late_numbers)
    taken = []
    real = dl._streamed_csv_read

    def spy(*args, **kwargs):
        table = real(*args, **kwargs)
        taken.append(table is not None)
        return table

    monkeypatch.setattr(dl, "_streamed_csv_read", spy)
    got = dl.load_well_csv(path)
    assert taken == [not late_numbers], (
        f"the streamed read was tried and returned a table: {taken} (expected "
        f"{[not late_numbers]}); the fixture does not exercise the path it is named for")
    expected = _reference_csv_read(path, monkeypatch)
    assert "fov" in got.columns and isinstance(got["channel"].dtype, pd.CategoricalDtype)
    assert got["filename"].dtype == object, "the fixture's filename column should stay text"
    _assert_same_frame(got, expected)


def test_the_parquet_read_gives_the_old_frame(tmp_path):
    """``read_cached`` equals a plain ``read_table().to_pandas()`` of the same file,
    whole and for a column subset, and its frames can be written in place.

    The file is one row group, so pyarrow hands its null-free numeric columns
    and its category codes over without copying — read-only — which is the case
    the writability half exists for. Wrong implementation it catches: dropping
    the copy of those columns in ``table_to_frame`` (curation's write then
    raises ``assignment destination is read-only``).
    """
    import pyarrow.parquet as pq

    path = _messy_well(tmp_path / "A01.csv", late_numbers=False)
    dl.read_well_frame(path)                       # writes the cache
    cached = pc.cache_path(path)
    assert pq.ParquetFile(cached).metadata.num_row_groups == 1

    got = pc.read_cached(path)
    _assert_same_frame(got, pq.read_table(cached).to_pandas())
    columns = ["fov", "gfp_mean_intensity", "channel", "Included", "not_a_column"]
    subset = pc.read_cached(path, columns=columns)
    _assert_same_frame(subset, pq.read_table(cached, columns=columns[:-1]).to_pandas())

    # The writes the app makes into a held frame.
    got.loc[got.index[:5], "Included"] = 0
    got.loc[got.index[:5], "channel"] = "DAPI"
    assert got["Included"].iloc[:5].tolist() == [0] * 5


def test_fov_labels_are_normalised_as_before():
    """Normalising each distinct label once gives the per-row normalisation's Series.

    Wrong implementation it catches: spreading the normalised labels back in
    the wrong order (``uniques`` against ``codes``), or losing the index or name.
    """
    labels = pd.Series(["", "-1", " 4 ", "3.0", "02", "F001", "1", "", "7", np.nan, "-1"],
                       index=range(10, 21), name="fov", dtype=object)
    _assert_series = pd.testing.assert_series_equal
    _assert_series(dl._normalize_fov_by_value(labels), dl.normalize_fov_series(labels), check_exact=True)


_MEASURE = textwrap.dedent("""
    import json, os, sys, threading, time
    sys.path.insert(0, sys.argv[1])
    from pathlib import Path
    import pyarrow as pa
    from review.data import data_loading as dl, parquet_cache as pc

    def rss():
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")

    # Sampled rather than read from ru_maxrss, which Linux carries across exec
    # from the process that started this one — here, the test runner.
    high = [0]
    done = threading.Event()

    def sample():
        while not done.is_set():
            high[0] = max(high[0], rss())
            time.sleep(0.001)

    path, kind = Path(sys.argv[2]), sys.argv[3]
    before = rss()
    sampler = threading.Thread(target=sample)
    sampler.start()
    frame = dl.load_well_csv(path) if kind == "csv" else pc.read_cached(path)
    done.set()
    sampler.join()
    resident = rss() - before
    peak = max(high[0], rss()) - before
    pool_peak = pa.default_memory_pool().max_memory()
    import pyarrow.parquet as pq
    print(json.dumps({"pool_peak": pool_peak, "resident": resident, "peak": peak,
                      "frame": int(frame.memory_usage(deep=True).sum()),
                      "table": pq.read_table(pc.cache_path(path)).nbytes}))
""")


@pytest.mark.skipif(not Path("/proc/self/statm").exists(), reason="reads resident memory from /proc")
def test_a_read_costs_about_one_frame(tmp_path):
    """Arrow's peak, the process's peak, and what stays resident are about one frame per read.

    Measured in a fresh process per read, because peaks are high-water marks of
    the process. Arrow's pool peak is exact — it counts Arrow's own allocations —
    and is stated against the well's Arrow table (41.2 MiB here). The process's
    peak and what is resident after the read are what the operating system sees,
    stated against the pandas frame (48.4 MiB). On this fixture, 150,000 rows:

    ========================  ==========  ==========  ==========  ==========
    ratio                     CSV old     CSV new     Parquet     Parquet
                                                      old         new
    ========================  ==========  ==========  ==========  ==========
    pool peak / table         3.6         1.5         2.3         1.5-1.6
    process peak / frame      6.2-6.4     2.5         3.2         2.5
    resident after / frame    6.1         1.6         3.2         1.4
    ========================  ==========  ==========  ==========  ==========

    At 1.5M rows the new ratios are lower still (a CSV peak of 2.2x the frame,
    Parquet 1.5x), since a fixed overhead weighs less there. Each bound sits
    between the new figure and the figure of the one change it is there to
    catch, measured by putting that change back:

    * the whole-file ``read_csv`` — CSV pool 3.6 (and the old read as a whole,
      CSV peak 6.2);
    * the default ``to_pandas()`` in ``table_to_frame`` — CSV pool 2.35, CSV
      peak 3.1, Parquet pool 1.88;
    * Parquet's ``pre_buffer`` left on — Parquet pool 2.28;
    * the Arrow pool never released — Parquet resident 2.9.

    Each of those passes the identity tests above, which is why this one exists.
    """
    path = _messy_well(tmp_path / "A01.csv.gz", late_numbers=False, rows=150_000)
    dl.read_well_frame(path)                       # the cache the Parquet read needs
    script = tmp_path / "measure.py"
    script.write_text(_MEASURE)
    results = {}
    for kind in ("csv", "parquet"):
        done = subprocess.run([sys.executable, str(script), str(REPO), str(path), kind],
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr[-2000:]
        results[kind] = json.loads(done.stdout.strip().splitlines()[-1])
    report = {k: {"pool_peak / table": round(v["pool_peak"] / v["table"], 2),
                  "peak / frame": round(v["peak"] / v["frame"], 2),
                  "resident / frame": round(v["resident"] / v["frame"], 2)} for k, v in results.items()}
    assert report["csv"]["pool_peak / table"] < 2.0, report
    assert report["parquet"]["pool_peak / table"] < 1.75, report
    assert report["csv"]["peak / frame"] < 3.5, report
    assert report["csv"]["resident / frame"] < 3.0, report
    assert report["parquet"]["resident / frame"] < 2.3, report
