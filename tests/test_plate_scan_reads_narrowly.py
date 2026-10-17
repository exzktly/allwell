"""``plate_scan.per_well`` / ``every_well`` read narrowly when given ``columns=``.

Tracker R3 (``_Docs/Markdowns/REREAD_TRACKER.md``). Without ``columns`` a
plate-wide scan reads each well through ``get_rows`` — the whole frame, put in
the row cache — and that is unchanged. With them the well is read through
``narrow_rows``: the named columns, the verdict layers' declared inputs and the
identity columns, projected, never cached. These tests hold the seam itself;
each caller that passes ``columns=`` proves its own answer equal to the
whole-frame one (``tests/test_timepoint_and_range_scans_read_narrowly.py``).
"""

from __future__ import annotations

import pytest

from review.data import data_loading as dl
from review.data.plate_scan import every_well, per_well
from tests.test_narrow_rows import WELL, _frame

pytest.importorskip("pyarrow.parquet")


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.well_repository import WellRepository
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    path = tmp_path / f"{WELL}.csv"
    _frame().to_csv(path, index=False)
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {WELL: path}
    state.dataset.fov_integrity = {}
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    dl.read_well_frame(path)                  # fills the Parquet cache
    state.dataset.cache.clear()
    return c


def _columns_seen(ctx, **kw) -> set:
    seen: list = []
    per_well(ctx, "probe", WELL, None, lambda df: seen.append(set(df.columns)) or len(df), **kw)
    return seen[0]


def test_without_columns_the_whole_frame_is_read_and_held(ctx):
    cols = _columns_seen(ctx)
    assert {"m0", "m9", "mcherry_mean_intensity"} <= cols
    assert WELL in ctx.state.dataset.cache


def test_with_columns_only_those_and_the_identity_columns_are_read(ctx):
    """A ``per_well`` that ignored ``columns`` would hand the compute ``m0``."""
    cols = _columns_seen(ctx, columns=("mcherry_mean_intensity",))
    assert "mcherry_mean_intensity" in cols and "timepoint_hours" in cols
    assert not {"m0", "gfp_mean_intensity"} & cols, cols
    assert WELL not in ctx.state.dataset.cache, "a narrow scan put the frame in the row cache"


def test_a_narrow_answer_is_remembered(ctx, monkeypatch):
    from review.data import parquet_cache

    reads: list = []
    real = parquet_cache.read_cached
    monkeypatch.setattr(parquet_cache, "read_cached",
                        lambda path, columns=None: reads.append(columns) or real(path, columns=columns))
    first = every_well(ctx, "probe", None, len, columns=("mcherry_mean_intensity",))
    again = every_well(ctx, "probe", None, len, columns=("mcherry_mean_intensity",))
    assert first == again == {WELL: 32}
    assert len(reads) == 1 and reads[0] is not None, reads
