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

from well_viewer.data import parquet_cache as pc  # noqa: E402
from well_viewer.data.data_loading import load_well_csv, read_well_frame  # noqa: E402

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


def test_a_subset_read_returns_only_those_columns(plate):
    """The half of the win a plot feels: six of thirty-seven columns."""
    read_well_frame(plate)
    want = ["Included", "area_px", "timepoint_hours"]
    subset = pc.read_cached(plate, columns=want)
    assert subset is not None and list(subset.columns) == want
    assert_frame_equal(subset, load_well_frame_subset(plate, want))


def load_well_frame_subset(path, columns):
    return load_well_csv(path)[columns]


# ── the guard: what must never reach the cache ──────────────────────────────


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
    import well_viewer.data.cache_projectors as cp
    import well_viewer.data.data_loading as dl

    seen = {}
    real = pc.write_cache

    def spy(csv_path, frame):
        seen["columns"] = list(frame.columns)
        return real(csv_path, frame)

    monkeypatch.setattr(pc, "write_cache", spy)

    def project_qc(ctx, label):
        ctx.state.dataset.cache[label]["QC_Pass"] = 1

    monkeypatch.setattr(cp, "_PROJECTORS", [project_qc])
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


def test_read_well_frame_is_the_only_door_onto_a_wells_rows():
    """A second call site that skips the cache is a well parsed every time.

    Worse, a call site that caches a frame it has already projected reintroduces
    the defect above, so the number of places allowed to touch this is one.

    Both spellings count: a bare ``load_well_csv(...)`` and an attribute call
    through the module (``dl.load_well_csv(...)``), which is how the two
    converted call sites were written before this.
    """
    import ast

    from tests._repo_scan import parse_source, python_sources

    offenders = []
    for path in python_sources():
        if path.name in {"data_loading.py", "parquet_cache.py"}:
            continue
        if path.parts and path.parts[-2] == "tests":
            continue
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "load_well_csv":
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, (
        "these call load_well_csv directly and so never see the cache:\n  "
        + "\n  ".join(offenders)
        + "\nGo through data_loading.read_well_frame."
    )
