"""Home's focus tallies read each well narrowly, and count what the whole frame counts.

Tracker R5 (``_Docs/Markdowns/REREAD_TRACKER.md``, measured as E5). Home paints
the plate's "needs review" markers from ``per_well_focus_tallies`` and the Cell
Gating panel states ``plate_focus_counts``; both are remembered per well under
``rows_version``, which a gating pass bumps once per well, so after every gating
change each well is counted again. That recount read the whole frame — 8 full
reads, 8.0 s on an 8-well plate of 1.5M-cell wells. ``focus_gate._every_well_narrow``
reads the focus column through ``narrow_rows``, which adds every verdict layer's
inputs, so ``candidate_rows`` sees the inclusion the whole frame would.

The fixture is the every-layer one from ``tests/test_narrow_rows.py`` with the
focus column rewritten so the counts depend on every layer: each cell a sibling
layer excludes carries a soft or a missing focus value, so a verdict that failed
open on the narrow frame would add to ``soft``, to ``unmeasurable`` or to
``total``. One kept cell is soft and one kept cell has no value, so neither
count is zero by construction.
"""

from __future__ import annotations

import numpy as np
import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import (
    CROP_CELL, CURATED_CELL, FOCUS_CELL, GATED_CELL, LAYERS, QC_CELL, RATIO, WELL, _frame, _switch_on,
)

pytest.importorskip("pyarrow.parquet")

#: A cell no layer excludes, whose outline could not be measured.
KEPT_UNMEASURED = 6


def _focus_frame():
    df = _frame()
    df.loc[FOCUS_CELL, "qc_edge_focus"] = 0.01        # soft, and counted: the tally ignores Focus_Pass
    for cell in (QC_CELL, GATED_CELL, CURATED_CELL, 1):   # soft, but a sibling layer excludes each
        df.loc[cell, "qc_edge_focus"] = 0.02
    for cell in (CROP_CELL, 29):                       # unmeasurable, but excluded (crop, field exclusion)
        df.loc[cell, "qc_edge_focus"] = np.nan
    df.loc[KEPT_UNMEASURED, "qc_edge_focus"] = np.nan
    return df


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.parquet_cache import read_cached
    from review.data.well_repository import WellRepository
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext
    from tests.test_narrow_rows import _field_quality_block

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    path = tmp_path / f"{WELL}.csv"
    _focus_frame().to_csv(path, index=False)
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {WELL: path}
    state.dataset.pipeline_info = _field_quality_block()
    state.dataset.fov_integrity = {}
    state.channel.ratio_index = {RATIO.key(): RATIO}
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    dl.read_well_frame(path)
    assert read_cached(path) is not None
    for layer in LAYERS:
        _switch_on(c, layer)
    c.state.dataset.cache.clear()
    return c


def _whole_frame(ctx, compute):
    """*compute* on the whole frame — the path the narrow read replaces."""
    ctx.state.dataset.cache.clear()
    full = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    return compute(full)


def test_the_plate_counts_are_the_whole_frame_s(ctx):
    from review.data.focus_gate import focus_counts, focus_params, plate_focus_counts

    want = _whole_frame(ctx, lambda df: focus_counts(df, focus_params(ctx)))
    # 32 cells; the six sibling layers exclude twelve; the cut leaves FOCUS_CELL
    # soft and KEPT_UNMEASURED unmeasured. Every excluded cell is soft or
    # unmeasured, so any verdict failing open moves a number.
    assert want == (1, 1, 20)
    assert plate_focus_counts(ctx) == want
    assert WELL not in ctx.state.dataset.cache, "the whole frame was read into the row cache"


def test_the_well_tallies_are_the_whole_frame_s(ctx):
    from review.data.focus_gate import candidate_rows, focus_values, per_well_focus_tallies
    from review.domain.well_composite import tally_focus

    def tally(df):
        vals = focus_values(candidate_rows(df))
        return tally_focus([None if v != v else float(v) for v in vals], 0.1)

    want = _whole_frame(ctx, tally)
    assert want == tally_focus([0.5] * 18 + [0.01, None], 0.1), want
    assert per_well_focus_tallies(ctx, 0.1) == {WELL: want}


def test_with_the_focus_gate_off_the_column_is_still_read(ctx):
    """Home tallies against the cut whether or not the gate excludes anything,
    and with the gate off no verdict declares the focus column: the tally's own
    request is then the only thing that reads it."""
    import dataclasses

    from review.data.focus_gate import focus_counts, focus_params, gate_is_on, plate_focus_counts

    ctx.state.qc.params = dataclasses.replace(ctx.state.qc.params, exclude_soft_cells=False)
    assert not gate_is_on(ctx)
    want = _whole_frame(ctx, lambda df: focus_counts(df, focus_params(ctx)))
    assert want == (1, 1, 20)
    assert plate_focus_counts(ctx) == want


def test_a_different_cut_is_a_different_tally(ctx):
    from review.data.focus_gate import per_well_focus_tallies
    from review.domain.well_composite import tally_focus

    values = [0.5] * 18 + [0.01, None]
    assert tally_focus(values, 0.1) != tally_focus(values, 0.005), "the two cuts tally alike; this measures nothing"
    assert per_well_focus_tallies(ctx, 0.1)[WELL] == tally_focus(values, 0.1)
    assert per_well_focus_tallies(ctx, 0.005)[WELL] == tally_focus(values, 0.005)


def test_one_narrow_read_per_well_until_the_rows_change(ctx, monkeypatch):
    from review.data import parquet_cache
    from review.data.focus_gate import per_well_focus_tallies, plate_focus_counts

    full_reads: list = []
    narrow: list = []
    real_full, real_cached = dl.read_well_frame, parquet_cache.read_cached
    monkeypatch.setattr(dl, "read_well_frame", lambda path: full_reads.append(path) or real_full(path))
    monkeypatch.setattr(parquet_cache, "read_cached",
                        lambda path, columns=None: narrow.append(columns) or real_cached(path, columns=columns))

    per_well_focus_tallies(ctx, 0.1)
    per_well_focus_tallies(ctx, 0.1)
    plate_focus_counts(ctx)
    plate_focus_counts(ctx)
    assert len(narrow) == 2, f"expected one read for the tallies and one for the counts, got {len(narrow)}"
    ctx.state.dataset.note_rows_changed()         # what a gating pass does, once per well
    per_well_focus_tallies(ctx, 0.1)
    assert len(narrow) == 3, "a change to the rows did not invalidate the memo"
    assert full_reads == [], "a whole frame was parsed"
    assert all(cols is not None and "m0" not in cols for cols in narrow), f"a read was not narrow: {narrow}"
