"""The per-well CSV read stays typed and categorical, measured rather than asserted.

Two memory wins live in ``load_well_csv`` and nothing held either of them.

Reading the whole file with ``dtype=str`` makes pandas allocate one consolidated
object block of every column at once and fill it with a Python string per cell,
including for the columns that are numbers and are about to be converted back.
On a 537,393-row x 37-column plate that is a 152 MiB pointer array plus roughly
16.6 million short strings, and it is where such a plate died with ``Unable to
allocate 152 MiB``. ARCHITECTURE.md carries the peak-RSS figures for the two
read shapes; they are not restated here, because a measurement written down in
three places is a measurement that will disagree with itself in two of them.

And ``_categorize_text_columns`` converts a text column whose distinct values
are under half its rows, which on a real plate takes the frame from 327 MiB to
135 MiB — steady-state memory in the row cache, held for every well of every
open dataset.

``tracemalloc`` does see this: it separates the two read shapes by 1.8x on the
fixture below, which is what makes a ceiling meaningful rather than decorative.
The ceiling is a ratio against the file's own size, so it does not encode the
fixture's absolute size, and it is loose enough that only the regression itself
crosses it.
"""

from __future__ import annotations

import csv
import tracemalloc

import pytest

#: Rows in the synthetic plate. Large enough that the allocation difference is
#: well clear of interpreter noise, small enough to build in about a second.
ROWS = 15_000

#: Peak Python allocations, as a multiple of the CSV's size on disk. The typed
#: read measures ~4.6x on this fixture and the ``dtype=str`` read ~8.2x, so this
#: sits between them with room on both sides.
PEAK_RATIO_CEILING = 6.5

_TEXT_COLS = ["experiment", "channel", "well", "fov", "timepoint", "filename"]


@pytest.fixture(scope="module")
def plate_csv(tmp_path_factory):
    """A plate-shaped CSV: 37 columns, six of them text, five of them repeating.

    Five of the text columns hold one distinct value — what a real plate looks
    like, and what the profiling behind the categorical conversion found:
    ``experiment`` and ``channel`` holding one value apiece and costing 30 MiB
    each. ``filename`` is the exception on a real plate and the exception here,
    varying across fields and timepoints: 384 distinct values in 15,000 rows, a
    share of 0.026. That is the column that makes the fixture able to see
    ``_CATEGORY_MAX_UNIQUE_SHARE`` at all — with one value in every column, the
    threshold could be lowered by three orders of magnitude and both tests here
    would stay green.
    """
    path = tmp_path_factory.mktemp("plate") / "measurements_A01.csv"
    numeric = [f"m{i}" for i in range(37 - len(_TEXT_COLS))]
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_TEXT_COLS + numeric)
        for row in range(ROWS):
            writer.writerow(
                ["exp1", "GFP", "A01", "1", "24h",
                 f"plate_A01_f{row % 16}_{row % 24}h.tif"]
                + [f"{(row * 7919 % 100000) / 100:.3f}" for _ in numeric]
            )
    return path


def _peak_mb(fn):
    tracemalloc.start()
    try:
        result = fn()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak / 1e6, result


def test_the_read_does_not_allocate_a_string_per_cell(plate_csv):
    from well_viewer.data.data_loading import load_well_csv

    size_mb = plate_csv.stat().st_size / 1e6
    peak_mb, frame = _peak_mb(lambda: load_well_csv(plate_csv))

    assert len(frame) == ROWS, "the fixture did not load — the ceiling proves nothing"
    ratio = peak_mb / size_mb
    assert ratio <= PEAK_RATIO_CEILING, (
        f"reading a {size_mb:.1f} MB plate peaked at {peak_mb:.1f} MB "
        f"({ratio:.1f}x the file). The read is allocating a Python string per "
        "cell again — on a real 537k-row plate that is where the load died."
    )


def test_the_text_columns_come_back_as_categories(plate_csv):
    """The steady-state half: 327 MiB to 135 MiB on a real plate.

    A ratio ceiling cannot see this — the conversion happens after the read, so
    peak allocation barely moves — which is why it is asserted directly.
    """
    from well_viewer.data.data_loading import load_well_csv

    frame = load_well_csv(plate_csv)
    plain = [c for c in _TEXT_COLS
             if c in frame.columns and str(frame[c].dtype) != "category"]
    assert not plain, (
        f"{plain} came back as object rather than category. Each one costs a "
        "Python string per row in the row cache, for every well of every open "
        "dataset."
    )


def test_a_text_column_that_does_not_repeat_is_left_alone():
    """The other side of ``_CATEGORY_MAX_UNIQUE_SHARE``, which no fixture reaches.

    A category is a dictionary plus an integer code per row. When almost every
    value is distinct the dictionary is the same size as the column and the
    codes are pure overhead, so the conversion is refused above half. Nothing
    else in this file crosses that line — the plate fixture's most varied text
    column sits at a share of 0.026 — so this asserts it directly, on the
    smallest frame that can show it.
    """
    pd = pytest.importorskip("pandas")
    from well_viewer.data.data_loading import (
        _CATEGORY_MAX_UNIQUE_SHARE,
        _categorize_text_columns,
    )

    n = 100
    frame = pd.DataFrame({
        "well": ["A01"] * n,
        "filename": [f"img_{i}.tif" for i in range(n)],
    })
    assert frame["filename"].nunique() / n > _CATEGORY_MAX_UNIQUE_SHARE
    _categorize_text_columns(frame)

    assert str(frame["well"].dtype) == "category", (
        "a text column with one distinct value in 100 rows was left as object"
    )
    assert str(frame["filename"].dtype) == "object", (
        "a text column whose values are all distinct was converted to a "
        "category. The category dictionary is then as large as the column "
        "itself and every row carries an integer code on top of it."
    )
