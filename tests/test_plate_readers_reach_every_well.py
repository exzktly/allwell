"""A reader that means the plate reaches every well, held or not (tracker M3).

The row cache is bounded, so iterating it — or looking a well up in it without
loading — sees the wells that happen to be in memory. Each test here builds a
three-well plate on disk, sets a budget that holds one well, makes sure the well
the assertion depends on has been **evicted**, and then checks the reader's
answer against the one the whole plate gives. Each assertion names a number
that a held-only reader gets wrong.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pipeline.field_quality_record import FOCUS_COLUMN
from review.core.dataset_state import DatasetState
from review.core.stats_cache_state import StatsCacheState
from review.data.data_loading import get_rows, held_wells_note
from review.data.row_cache import frame_bytes
from review.data.well_repository import WellRepository
from review.domain.qc_params import QCParams
from review.features.registry import build_feature_registry

# Per well: A01 is sharp, B02 has three soft cells of four, C03 one of four.
_FOCUS = {"A01": [0.30, 0.31, 0.32, 0.33], "B02": [0.01, 0.02, 0.03, 0.30], "C03": [0.02, 0.30, 0.31, 0.32]}
_TIMEPOINTS = {"A01": [0, 0, 24, 24], "B02": [0, 0, 48, 48], "C03": [0, 0, 72, 72]}


def _write(tmp_path, label):
    path = tmp_path / f"{label}.csv"
    n = len(_FOCUS[label])
    pd.DataFrame({
        "well": [label] * n,
        "fov": ["1"] * n,
        "timepoint": [str(t) for t in _TIMEPOINTS[label]],
        "timepoint_hours": _TIMEPOINTS[label],
        "nucleus_id": [str(i + 1) for i in range(n)],
        "area_px": [100.0] * n,
        "shape_centroid_row": np.linspace(10, 40, n),
        "shape_centroid_col": np.linspace(10, 40, n),
        "gfp_mean_intensity": np.linspace(1, 9, n),
        FOCUS_COLUMN: _FOCUS[label],
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def ctx(tmp_path):
    dataset = DatasetState()
    for label in _FOCUS:
        dataset.well_paths[label] = _write(tmp_path, label)
    obj = SimpleNamespace(
        state=SimpleNamespace(
            dataset=dataset,
            stats_cache=StatsCacheState(),
            review_image=SimpleNamespace(override_version=0),
            qc=SimpleNamespace(params=QCParams(focus_min=0.10)),
            channel=SimpleNamespace(ratio_index={}),
        ),
        runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={})),
    )
    obj.state.repo = WellRepository(obj)
    build_feature_registry()
    cache = dataset.cache
    cache.set_budget(frame_bytes(get_rows(obj, "C03")) + 1)
    return obj


def _evict(ctx, *labels):
    """Read every other well, so the budget of one drops *labels*."""
    for other in ctx.state.dataset.well_paths:
        if other not in labels:
            get_rows(ctx, other)
    held = set(ctx.state.dataset.cache.keys())
    assert not held & set(labels), f"{labels} still held ({held}); the test would measure nothing"


def test_statistics_reads_an_evicted_well_rather_than_calling_it_empty(ctx):
    from review.domain.stats_compute import _safe_rows

    _evict(ctx, "A01")
    rows = _safe_rows(ctx, "A01")
    assert rows is not None and len(rows) == 4


def test_the_note_says_when_a_count_covers_part_of_the_plate(ctx):
    _evict(ctx, "A01")
    assert held_wells_note(ctx) == " — counted over 1 of 3 wells in memory"
    ctx.state.dataset.cache.set_budget(10**9)
    for label in ctx.state.dataset.well_paths:
        get_rows(ctx, label)
    assert held_wells_note(ctx) == ""


def test_the_focus_panel_counts_the_whole_plate(ctx):
    from review.data.focus_gate import plate_focus_counts

    _evict(ctx, "B02")
    assert plate_focus_counts(ctx) == (4, 0, 12)


def test_the_plate_marker_reaches_an_evicted_well(ctx):
    from review.data.focus_gate import wells_needing_review

    _evict(ctx, "B02", "C03")
    assert sorted(wells_needing_review(ctx)) == ["B02", "C03"]


def test_the_focus_curve_of_an_evicted_well_is_drawn(ctx):
    from review.data.focus_gate import well_focus_distribution

    _evict(ctx, "B02")
    assert well_focus_distribution(ctx, "B02").values == (0.01, 0.02, 0.03, 0.30)


def test_spatial_draws_an_evicted_well(ctx):
    from review.features.spatial.collect import collect_spatial_frame

    _evict(ctx, "A01")
    # Two cells at A01's first timepoint (0 h); a cache-only read drew none.
    assert collect_spatial_frame(ctx, "A01")["n_cells"] == 2


def test_spatial_timepoints_span_the_plate(ctx):
    from review.features.spatial.collect import available_timepoints

    _evict(ctx, "A01", "B02")
    assert available_timepoints(ctx) == [0.0, 24.0, 48.0, 72.0]


def test_the_spatial_matrix_walks_evicted_wells(ctx):
    from review.features.spatial.collect import collect_summary_matrix

    _evict(ctx, "A01", "B02")
    payload = collect_summary_matrix(ctx, ["A01", "B02", "C03", "Z99"], [0.0, 24.0])
    assert payload["wells"] == ["A01", "B02", "C03"]
    assert payload["summaries"][0][1] is not None, "A01 has two cells at 24 h"


# ── the memo that keeps "every well" from meaning "read every well every time"


def test_a_plate_value_is_read_once_until_the_rows_change(ctx, monkeypatch):
    from review.data import data_loading, parquet_cache
    from review.data.focus_gate import plate_focus_counts

    # Every read from disk, whole or narrow: the counts read each well through
    # ``narrow_rows``, which parses the CSV the first time (``read_well_frame``)
    # and reads the Parquet cache's columns after that (``read_cached``).
    # Counting only the first would see a re-read as no read at all.
    reads: list = []
    real = data_loading.read_well_frame
    real_cached = parquet_cache.read_cached
    monkeypatch.setattr(data_loading, "read_well_frame", lambda path: reads.append(path) or real(path))
    monkeypatch.setattr(parquet_cache, "read_cached",
                        lambda path, columns=None: reads.append(path) or real_cached(path, columns=columns))

    plate_focus_counts(ctx)
    first = len(reads)
    assert first >= 2, "the evicted wells were not read; the plate was not reached"
    plate_focus_counts(ctx)
    assert len(reads) == first, "the second count re-read the plate"

    ctx.state.dataset.note_rows_changed()
    plate_focus_counts(ctx)
    assert len(reads) > first, "a change to the rows did not invalidate the memo"

    settled = len(reads)
    ctx.state.stats_cache.invalidate()
    plate_focus_counts(ctx)
    assert len(reads) > settled, "clearing the statistics cache (a verdict change) did not invalidate it"


def test_the_included_count_says_it_covers_part_of_the_plate(ctx):
    """The live count is taken over the held frames by design; what it must
    not do is print that count as if it were the plate's."""
    from review.features.home.cell_gating.controller import cell_gating_refresh_included_count

    shown: list[str] = []
    ctx.runtime.cell_gating = SimpleNamespace(
        included_count=SimpleNamespace(setText=shown.append), funnel_label=None, last_counts=None)
    _evict(ctx, "A01", "B02")
    cell_gating_refresh_included_count(ctx)
    assert shown[-1].startswith("Included for analysis: 4 / 4 cells")
    assert shown[-1].endswith(" — counted over 1 of 3 wells in memory")


def test_the_tabs_timepoint_menus_read_the_plate_once(ctx, monkeypatch):
    """Distribution, Heat Map, Multivariate and Statistics ask on every tab
    activation and channel switch (tracker M6): the plate is read once."""
    from review.data import data_loading

    _evict(ctx, "A01", "B02")
    assert data_loading.get_all_timepoints(ctx) == [0.0, 24.0, 48.0, 72.0]
    monkeypatch.setattr(data_loading, "read_well_frame",
                        lambda path: pytest.fail(f"{path.name} was read again"))
    assert data_loading.get_all_timepoints(ctx) == [0.0, 24.0, 48.0, 72.0]
