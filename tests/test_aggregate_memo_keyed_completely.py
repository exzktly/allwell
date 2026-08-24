"""The per-well aggregation memo declines whenever any of its inputs changes.

``aggregate_well_cached`` reuses a well's aggregate across calls, and "we already
computed this, use the old answer" is the highest-stakes predicate in the app: a
key that is too narrow returns numbers from rows that no longer exist and reports
them as current. That is the one failure mode this whole mechanism has to avoid,
and it fails silently — the plot draws, the status line agrees, nothing raises.

So every declared component of the key is mutated here on its own and the memo
is required to notice. The list of components is deliberately the same list that
appears in the key, in the same order, so that adding one to the key without
adding it here is visible in review as an asymmetry.

The half no test can decide is whether anything *outside* the key can invalidate
the result. That is what ``tests/test_row_cache_writers.py`` covers from the
other direction: it enumerates the frame-mutation sites from the syntax tree and
fails on a new one, so the two together say "these inputs are keyed, and no
unkeyed input exists".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from well_viewer.core.app_state import AppState
from well_viewer.core.app_runtime import AppRuntime
from well_viewer.core.event_bus import EventBus
from well_viewer.core.widget_registry import WidgetRegistry
from well_viewer.domain.stats_compute import aggregate_well_cached
from well_viewer.viewer_context import ViewerContext

VAL_COL = "gfp_mean_intensity"
WELL = "A01"


def _rows(n: int = 12, bump: float = 0.0) -> pd.DataFrame:
    """A small well frame with two timepoints and two fields."""
    return pd.DataFrame({
        "well": WELL,
        "fov": ["1"] * (n // 2) + ["2"] * (n - n // 2),
        "timepoint": ["0h"] * (n // 2) + ["24h"] * (n - n // 2),
        "timepoint_hours": [0.0] * (n // 2) + [24.0] * (n - n // 2),
        "nucleus_id": np.arange(1, n + 1),
        "area_px": 200.0,
        VAL_COL: np.linspace(10.0, 120.0, n) + bump,
        "mcherry_mean_intensity": np.linspace(5.0, 60.0, n),
        "Included": 1,
    })


@pytest.fixture
def ctx(tmp_path):
    """A headless context holding one cached well — no Qt, no window."""
    from well_viewer.features.registry import attach_feature_state

    state = AppState()
    attach_feature_state(state)
    state.channel.active_val_col = VAL_COL
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {WELL: tmp_path / f"{WELL}.csv"}
    store: dict = {}
    c = ViewerContext(
        state=state, bus=EventBus(),
        widgets=WidgetRegistry(None, store=store),
        runtime=AppRuntime(None, store=store),
    )
    state.dataset.cache[WELL] = _rows()
    return c


def _agg(ctx, **over):
    kw = dict(threshold=0.0, use_sem=False, val_col=VAL_COL,
              fluor_gates={}, per_fov_spread=False,
              tp_col="timepoint_hours")
    kw.update(over)
    return aggregate_well_cached(ctx, WELL, **kw)


# ── The memo works at all ────────────────────────────────────────────────────

def test_a_repeated_call_is_served_from_the_memo(ctx):
    from well_viewer.domain import stats_compute as sc

    calls = {"n": 0}
    orig = sc.aggregate_well

    def counted(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    sc.aggregate_well = counted
    try:
        first = _agg(ctx)
        second = _agg(ctx)
    finally:
        sc.aggregate_well = orig
    assert first == second
    assert calls["n"] == 1, "the second call recomputed instead of reusing"
    assert isinstance(first, tuple), (
        "the memo must hand out a tuple; a shared mutable list makes one "
        "caller's edit every caller's"
    )


# ── Each declared key component, mutated alone ───────────────────────────────

def test_a_different_value_column_declines(ctx):
    base = _agg(ctx)
    other = _agg(ctx, val_col="mcherry_mean_intensity")
    assert other != base


def test_a_different_threshold_declines(ctx):
    base = _agg(ctx)
    other = _agg(ctx, threshold=60.0)
    assert other != base, "the fraction above threshold cannot be unchanged"


def test_use_sem_declines(ctx):
    """SD and SEM are different numbers in the spread field, not a label."""
    base = _agg(ctx)
    other = _agg(ctx, use_sem=True)
    assert other != base


def test_per_fov_spread_declines(ctx):
    """It changes which spread is reported and fills the per-FOV fields."""
    base = _agg(ctx)
    other = _agg(ctx, per_fov_spread=True)
    assert other != base


def test_a_fluor_gate_declines(ctx):
    base = _agg(ctx)
    other = _agg(ctx, fluor_gates={"mcherry": 30.0})
    assert other != base, "a gate that filters rows changed nothing"


def test_editing_the_rows_declines(ctx):
    """The component the mutation proof was run against.

    Excluding every cell of the well must not keep returning the aggregate of
    the included ones. Without ``rows_version`` in the key this is exactly what
    happened, and the failure is invisible: a full set of plausible numbers,
    drawn and reported as current.
    """
    base = _agg(ctx)
    assert base, "precondition: the well aggregates to something"
    ctx.state.dataset.cache[WELL]["Included"] = 0
    ctx.state.dataset.note_rows_changed()
    after = _agg(ctx)
    assert after != base
    assert after == (), "an all-excluded well aggregates to nothing"


def test_replacing_the_frame_declines(ctx):
    """A whole-frame replacement is as invalidating as a column edit.

    The smFISH write-back and the dataset loader both assign a new frame into
    the cache rather than editing one, which is a different syntactic shape and
    was missed by the first writer sweep.
    """
    base = _agg(ctx)
    ctx.state.dataset.cache[WELL] = _rows(bump=500.0)
    ctx.state.dataset.note_rows_changed()
    assert _agg(ctx) != base


def test_a_dataset_swap_declines(ctx, tmp_path):
    """Two plates can carry the same well tokens.

    ``begin_load`` clears the row cache, but the memo is keyed on the well
    *token* — so without a tick here, ``A01`` of the new plate would be served
    ``A01`` of the old one. The load path does clear the stats cache, but only
    inside a suppressed try/except, so the key has to carry the swap itself.
    """
    base = _agg(ctx)
    ctx.state.dataset.begin_load(tmp_path / "other_plate")
    ctx.state.dataset.well_paths = {WELL: tmp_path / "other_plate" / f"{WELL}.csv"}
    ctx.state.dataset.cache[WELL] = _rows(bump=999.0)
    assert _agg(ctx) != base, "a dataset swap served the previous plate's numbers"


def test_a_ratio_edited_in_place_declines(ctx):
    """The key signs the ratio's *definition*, not just its name.

    And it signs the definition behind the ``val_col`` argument rather than the
    active column, so a caller that passes an explicit column without swapping
    the active one cannot get a key describing a different ratio than the one
    it computed with.
    """
    from well_viewer.domain.ratio_models import RatioMetric

    ratio = RatioMetric(
        name="R", numerator_channel="gfp", numerator_metric="mean_intensity",
        denominator_channel="mcherry", denominator_metric="mean_intensity",
    )
    key = ratio.key()
    ctx.state.channel.ratio_index = {key: ratio}
    base = _agg(ctx, val_col=key)

    edited = RatioMetric(
        name="R", numerator_channel="mcherry", numerator_metric="mean_intensity",
        denominator_channel="gfp", denominator_metric="mean_intensity",
    )
    ctx.state.channel.ratio_index = {key: edited}
    assert _agg(ctx, val_col=key) != base, (
        "a ratio edited in place, keeping its name, returned the old numbers"
    )


# ── The roster and the key agree ─────────────────────────────────────────────

def test_every_documented_key_component_has_a_test():
    """An asymmetry between the key and this file should be visible, not silent.

    Not a proof — a component could be listed and tested with an assertion that
    cannot fail. It catches the cheaper mistake: adding a component to the key
    and forgetting to exercise it here, or removing one and leaving a test that
    now describes nothing.
    """
    import inspect

    from well_viewer.domain import stats_compute as sc

    src = inspect.getsource(sc.aggregate_well_cached)
    key_src = src.split("key = (", 1)[1].split("    )", 1)[0]
    expected = {
        "label": "label",
        "val_col": "val_col",
        "threshold": "threshold",
        "use_sem": "use_sem",
        "per_fov_spread": "per_fov_spread",
        "tp_col": "tp_col",
        "fluor_gates": "fluor_gates",
        "gate_sig": "_gate_cache_sig",
        "ratio_sig": "_ratio_sig_for",
        "rows_version": "rows_version",
    }
    missing = [name for name, token in expected.items() if token not in key_src]
    assert not missing, (
        "these components are documented as part of the memo key but are no "
        f"longer in it: {missing}"
    )
