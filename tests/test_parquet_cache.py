"""The per-well Parquet cache: identical frames, honest invalidation, no verdicts.

Three properties, and they fail in three very different ways.

**Equality.** A cache that returns a subtly different frame is the worst
outcome available here — every number in the app is computed from these frames,
and a dtype that came back ``object`` instead of ``category``, or a blank that
came back ``""`` instead of ``NaN``, changes results without changing anything
visible. So the cached frame is compared against the CSV parse cell for cell and
dtype for dtype, including the two cases ``load_well_csv``'s ``na_values``
handling exists for: an empty cell, and a cell holding the literal text ``NA``.

**Invalidation.** The pipeline rewrites these CSVs — an incremental run merges
new rows into an existing well. A cache that survived that would serve the old
plate's numbers under the new plate's name.

**No projected columns.** This is the one mistake here that produces silently
wrong analysis rather than a crash, and it has its own guard below.
"""

from __future__ import annotations

import csv
import time

import pytest

pytest.importorskip("pyarrow")

from pandas.testing import assert_frame_equal  # noqa: E402

from review.data import parquet_cache as pc  # noqa: E402
from review.data.data_loading import load_well_csv, read_well_frame  # noqa: E402

_TEXT = ["experiment", "channel", "well", "fov", "timepoint", "filename"]
_NUM = ["timepoint_hours", "area_px", "gfp_mean_intensity", "shape_perimeter"]


def _write_plate(path, rows=400, extra=()):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_TEXT + _NUM)
        for i in range(rows):
            writer.writerow(
                ["exp1", "GFP", "A01", str(i % 4 + 1), f"{(i % 6) * 4}h",
                 f"plate1_GFP_A01_f{i % 4 + 1}_{(i % 6) * 4}h.tif",
                 float((i % 6) * 4), 50.0 + i % 7, 100.0 + i, 20.0],
            )
        # The two cases the reader's na handling exists for: a blank cell, and a
        # cell holding the literal text NA. Both must survive the round trip.
        fh.write("exp1,GFP,A01,1,24h,x.tif,24.0,,NA,20.0\n")
        for line in extra:
            fh.write(line)
    return path


class _StubCtx:
    """The three attributes ``load_and_project`` reaches for, and nothing else.

    A real ``ViewerContext`` needs a built app; this path touches
    ``state.dataset.well_paths``, ``state.dataset.cache`` and
    ``state.dataset.fov_integrity`` and no more, so a stub keeps the guard in
    the fast lane.
    """

    class _Dataset:
        def __init__(self):
            self.well_paths: dict = {}
            self.cache: dict = {}
            self.fov_integrity: dict = {}

    class _State:
        def __init__(self):
            self.dataset = _StubCtx._Dataset()

    def __init__(self, csv_path=None):
        self.state = _StubCtx._State()
        if csv_path is not None:
            self.state.dataset.well_paths["A01"] = csv_path


@pytest.fixture
def plate(tmp_path):
    return _write_plate(tmp_path / "measurements_A01.csv")


def test_the_second_read_does_not_parse_the_csv_again(plate, monkeypatch):
    """The whole point of the feature, asserted where it can actually fail.

    Every other test in this file asserts a property of a **miss** — that the
    frame equals the CSV parse, that a rewritten CSV invalidates, that a corrupt
    or absent or unwritable cache degrades — and a ``read_cached`` that returned
    ``None`` unconditionally satisfies all of them. It was tried: one line, and
    the full fast lane stayed green at 7,278 passed. A feature that can be
    deleted without a test noticing is decorated, not tested (§7.7 #2).

    So this counts parses, not frames. Two reads of one well must parse once.
    """
    import review.data.data_loading as dl

    parses = {"n": 0}
    real = dl.load_well_csv

    def counting(path):
        parses["n"] += 1
        return real(path)

    monkeypatch.setattr(dl, "load_well_csv", counting)
    first = dl.read_well_frame(plate)
    assert parses["n"] == 1, "the first read did not parse — the fixture is wrong"
    second = dl.read_well_frame(plate)

    assert parses["n"] == 1, (
        f"two reads of one well parsed the CSV {parses['n']} times. The cache "
        "is being written and never read, which costs the disk space and "
        "returns none of the speed."
    )
    assert_frame_equal(first, second)


def test_a_hit_is_served_from_the_file_and_not_from_memory(plate):
    """``read_cached`` itself answers, on a process that never wrote the cache.

    The test above patches ``load_well_csv`` and so proves the *door* skips the
    parse; this proves the thing on disk is readable at all. Together they are
    what a `return None` in ``read_cached`` fails.
    """
    read_well_frame(plate)
    assert pc.read_cached(plate) is not None, (
        "the cache was written and cannot be read back, so every load is a miss"
    )


def test_a_changed_parser_invalidates_every_cache():
    """``PARSER_VERSION`` must move when ``load_well_csv`` would parse differently.

    The reuse key was ``"<size>:<mtime_ns>"`` of the CSV, which describes the
    *input* and says nothing about the function that turns it into a frame — and
    that function is not stable: it has gained a typed read, categorical text
    columns, and a ``shape_circularity`` back-fill, any one of which changes the
    frame produced from bytes that never moved. Without a version component, an
    untouched CSV keeps serving the frame from before the change, the new column
    simply is not there, and nothing says so.

    Pinned against a hash of the parser's own source rather than by memory,
    because "remember to bump the constant" is exactly the instruction that is
    not followed. If this fails, read the diff to ``load_well_csv`` and its
    constants: if the frame it produces can differ, bump ``PARSER_VERSION`` and
    update the hash here; if it genuinely cannot (a comment, a log line), update
    the hash alone and say which in the commit.
    """
    import hashlib
    import inspect

    from review.data import data_loading as dl

    material = "".join([
        inspect.getsource(dl.load_well_csv),
        inspect.getsource(dl._categorize_text_columns),
        inspect.getsource(dl._string_column_dtypes),
        inspect.getsource(dl._read_well_table),
        inspect.getsource(dl._arrow_read),
        repr((dl._PANDAS_TRUE, dl._PANDAS_FALSE)),
        repr(sorted(dl._STRING_COLS)),
        repr(dl._CATEGORY_MAX_UNIQUE_SHARE),
    ])
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]

    assert (pc.PARSER_VERSION, digest) == (2, "464090683a5abd18"), (
        "load_well_csv or a constant it reads has changed. Every cached frame "
        "on every user's disk was produced by the old one, and the fingerprint "
        "does not know that. Bump PARSER_VERSION so they all invalidate, then "
        f"put the new digest here: {digest}"
    )


def test_the_fingerprint_is_taken_before_the_parse(plate, monkeypatch):
    """A CSV rewritten *during* its own parse must not be cached under the new file.

    The read is three seconds on a real well and an Analyze run or a remote
    fetch can be writing into the same ``out/`` throughout. Stamping the file's
    state afterwards records the new file's identity against the old file's
    rows: the cache then looks fresh and is wrong on every read afterwards,
    which is §7's cardinal case reached with no bug in the parse at all.
    """
    import review.data.data_loading as dl

    real = dl.load_well_csv

    def parse_then_rewrite(path):
        frame = real(path)
        # Whatever wrote this well finishes while we were reading it.
        _write_plate(path, rows=420)
        return frame

    monkeypatch.setattr(dl, "load_well_csv", parse_then_rewrite)
    stale = dl.read_well_frame(plate)

    assert not pc.cache_path(plate).exists(), (
        "the frame parsed from the old contents was cached under the new "
        "file's fingerprint, so every later read serves rows that no longer "
        "describe the CSV beside them"
    )
    # And the next read, on the settled file, sees the new contents.
    monkeypatch.setattr(dl, "load_well_csv", real)
    fresh = dl.read_well_frame(plate)
    assert len(fresh) > len(stale)


def test_the_cached_frame_equals_the_csv_parse(plate):
    direct = load_well_csv(plate)
    miss = read_well_frame(plate)      # populates the cache
    hit = read_well_frame(plate)       # reads it back

    assert pc.cache_path(plate).exists(), "nothing was cached"
    assert_frame_equal(direct, miss)
    assert_frame_equal(direct, hit)


def test_the_text_columns_come_back_as_categories(plate):
    """What carries this is Arrow's schema, not the pandas metadata block.

    Worth knowing before reading this as a guard on ``write_cache``'s metadata
    merge: a category is a ``DictionaryType`` in the Arrow schema proper, so
    stripping pandas' block entirely leaves every test in this file green. The
    merge is there so ``replace_schema_metadata`` does not *destroy* that block,
    not because the dtypes below depend on it.
    """
    """The categorical dtypes are a real memory win and Parquet can lose them."""
    read_well_frame(plate)
    hit = read_well_frame(plate)
    plain = [c for c in _TEXT if str(hit[c].dtype) != "category"]
    assert not plain, f"{plain} lost their category dtype through the cache"


def test_a_rewritten_csv_invalidates_the_cache(plate):
    """An incremental run merges rows into an existing well's CSV."""
    first = read_well_frame(plate)
    time.sleep(0.01)
    with plate.open("a") as fh:
        fh.write("exp2,GFP,A01,1,0h,y.tif,0.0,50.0,1.0,20.0\n")
    second = read_well_frame(plate)
    assert len(second) == len(first) + 1, (
        "the cache was served for a CSV that has been rewritten since"
    )


def test_a_missing_engine_is_a_miss_and_not_a_failure(plate, monkeypatch):
    """An install without pyarrow reads CSVs exactly as it always did."""
    monkeypatch.setattr(pc, "_pyarrow", lambda: None)
    frame = read_well_frame(plate)
    assert_frame_equal(frame, load_well_csv(plate))
    assert not pc.cache_path(plate).exists()


def test_an_unwritable_cache_directory_costs_speed_and_nothing_else(plate, monkeypatch):
    """A read-only output volume is an ordinary state, not a failed load."""
    def refuse(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(pc.Path, "mkdir", refuse)
    assert pc.write_cache(plate, load_well_csv(plate)) is False
    assert_frame_equal(read_well_frame(plate), load_well_csv(plate))


def test_a_corrupt_cache_is_a_miss(plate):
    read_well_frame(plate)
    pc.cache_path(plate).write_bytes(b"not a parquet file")
    assert_frame_equal(read_well_frame(plate), load_well_csv(plate))


def test_the_cache_is_written_before_any_verdict_is_projected(plate, monkeypatch):
    """``QC_Pass`` / ``Crop_Pass`` / ``Gate_Pass`` are recomputed per session.

    They are written onto the frame by ``load_and_project``'s projector loop,
    from parameters the user changes at will. A cache holding one session's
    verdicts would serve them to the next session under different thresholds,
    and every count and fraction downstream would be wrong with nothing to
    notice it by.

    Driven through ``load_and_project`` with a projector registered, rather than
    through ``read_well_frame`` alone: ``read_well_frame`` has no projector loop,
    so asserting on it proves only that a function which cannot add a verdict
    column did not add one. The mistake this guards against is moving the write
    one line down, past the loop — which is a mistake in ``load_and_project``,
    and only a test that runs that loop can see it.
    """
    import review.data.cache_projectors as cp
    import review.data.data_loading as dl

    seen = {}
    real = pc.write_cache

    def spy(csv_path, frame, fingerprint=None):
        seen["columns"] = list(frame.columns)
        return real(csv_path, frame, fingerprint)

    monkeypatch.setattr(pc, "write_cache", spy)

    def project_qc(ctx, label):
        ctx.state.dataset.cache[label]["QC_Pass"] = 1

    monkeypatch.setattr(cp, "_PROJECTORS", [(project_qc, None)])
    ctx = _StubCtx(plate)
    frame = dl.load_and_project(ctx, "A01", path=plate)

    assert "columns" in seen, "write_cache was never reached"
    assert "QC_Pass" in frame.columns, (
        "the projector did not run — this guard proves nothing unless the "
        "loop it is watching actually fires"
    )
    projected = {"QC_Pass", "Crop_Pass", "Gate_Pass"}
    leaked = projected.intersection(seen["columns"])
    assert not leaked, (
        f"{sorted(leaked)} reached the Parquet cache. Those are recomputed from "
        "user-editable parameters every session; caching them serves one "
        "session's verdicts to the next."
    )


def load_well_csv_call_sites(trees) -> list[str]:
    """Every module that calls ``load_well_csv``, however the name got there.

    Shared with ``tests/test_guards_bite.py``, which hands it synthetic files
    carrying each evasion and requires it to see them.

    Three spellings count, because all three were reachable: the bare call, the
    attribute call through the module (``dl.load_well_csv(path)``), and an
    aliased import (``from ... import load_well_csv as _read``) followed by a
    call on the alias. The alias is the shape CLAUDE.md §7.5 already names as a
    known evasion, and the first version of this guard matched on the call name
    alone and let it through.
    """
    import ast

    offenders = []
    for path, tree in trees:
        aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "load_well_csv" and alias.asname:
                        aliases.add(alias.asname)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "load_well_csv" or name in aliases:
                offenders.append(f"{path}:{node.lineno}")
    return offenders


def test_read_well_frame_is_the_only_door_onto_a_wells_rows():
    """A second call site that skips the cache is a well parsed every time.

    Worse, a call site that caches a frame it has already projected reintroduces
    the verdict defect above, so the number of places allowed to touch this is
    one.

    The two exempt modules are matched by their **path**, not their basename: a
    future ``features/<x>/data_loading.py`` anywhere in the tree would otherwise
    be exempt from a rule it has no business being exempt from.
    """
    from tests._repo_scan import parse_source, python_sources

    exempt = {"review/data/data_loading.py", "review/data/parquet_cache.py"}
    trees = [(path, parse_source(path)) for path in python_sources()
             if not str(path).replace("\\", "/").endswith(tuple(exempt))]
    offenders = load_well_csv_call_sites(trees)
    assert not offenders, (
        "these call load_well_csv directly and so never see the cache:\n  "
        + "\n  ".join(offenders)
        + "\nGo through data_loading.read_well_frame."
    )


# ── what reclaims the disk ──────────────────────────────────────────────────

def test_a_cache_whose_csv_is_gone_is_removed(plate, tmp_path):
    """Nothing else ever would: the entry is only reached through its CSV's name.

    A renamed or deleted well leaves a Parquet file no code path can read or
    overwrite, and the cache is 66% of a CSV's size on a plate-shaped well.
    """
    read_well_frame(plate)
    cached = pc.cache_path(plate)
    assert cached.exists()

    plate.unlink()
    assert pc.prune(tmp_path) == 1
    assert not cached.exists()


def test_a_cache_whose_csv_is_still_there_is_kept(plate, tmp_path):
    """Not an eviction policy. A plate opened once a year is exactly the one
    whose five-minute parse this exists to save, and "old" is not a property
    the pruner can read."""
    read_well_frame(plate)
    assert pc.prune(tmp_path) == 0
    assert pc.cache_path(plate).exists()


def test_an_abandoned_staging_file_is_swept(plate, tmp_path):
    """Left by a process killed between the write and the ``os.replace``.

    Dead by definition: a live write replaces its own staging file within
    milliseconds, and another live process's carries that process's pid.
    """
    read_well_frame(plate)
    orphan = pc.cache_path(plate).with_name("__aw_tmp_measurements_A01.parquet.pid999")
    orphan.write_bytes(b"half a file")

    assert pc.prune(tmp_path) == 1
    assert not orphan.exists()
    assert pc.cache_path(plate).exists(), "the sweep took a live cache with it"


def test_pruning_a_directory_with_no_cache_is_not_an_error(tmp_path):
    assert pc.prune(tmp_path / "never opened") == 0
