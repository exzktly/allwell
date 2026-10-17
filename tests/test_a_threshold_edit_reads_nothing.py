"""A threshold edit is answered from memory; an edit that can change which cells count is not.

On a plate larger than the row cache, every threshold edit used to read each
well from disk again (twice) and re-filter it: about 50 s for one edit on a
60-well plate of 1.5M-cell wells, for numbers whose filtered cells had not
changed. Each well's threshold-free population is now kept
(``review/domain/well_population.py``) in a byte-bounded store that
``selection_controller.invalidate_threshold_results`` leaves alone.

These tests hold both halves on the narrow-read fixture with every verdict
layer on: after a threshold edit the aggregate and the replicate extras equal
the whole-frame answer at the new threshold with **no read**; after any other
invalidation, or a change to any component of the population's key, the well
is read again.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from review.data import data_loading as dl
from tests.test_replicate_extras_read_narrowly import (  # noqa: F401 - the fixture
    TIMES, _count_reads, _expected, ctx,
)
from tests.test_narrow_rows import WELL, _ratio_scatter_gating

pytest.importorskip("pyarrow.parquet")

VAL = "mcherry_mean_intensity"
GATES = {"cy5": 6.0}


def _direct_aggregate(ctx, threshold, *, val_col=VAL, gates=GATES, on_col=None):
    ctx.state.dataset.cache.clear()
    full = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    return dl.aggregate_with_threshold_df(
        full, threshold=threshold, use_sem=False, val_col=val_col, fluor_gates=gates,
        ratios=ctx.state.channel.ratio_index, on_col=on_col)


def _agg(ctx, threshold, **kw):
    from review.domain.stats_compute import aggregate_well_cached

    kw.setdefault("val_col", VAL)
    kw.setdefault("fluor_gates", GATES)
    return list(aggregate_well_cached(ctx, WELL, threshold=threshold, use_sem=False, **kw))


def _reps(ctx, threshold, t):
    from review.domain.stats_compute import group_replicate_stats

    return group_replicate_stats(ctx, [WELL], t, use_fov=False, val_col=VAL,
                                 threshold=threshold, fluor_gates=GATES)


def _same(a, b) -> bool:
    return len(a) == len(b) and all(
        all((isinstance(x, float) and math.isnan(x) and math.isnan(y)) or x == y
            for x, y in zip(pa, pb)) for pa, pb in zip(a, b))


def test_a_threshold_edit_reads_nothing_and_answers_the_new_threshold(ctx, monkeypatch):
    from review.core.selection_controller import invalidate_threshold_results

    ctx.state.dataset.cache.clear()
    calls = _count_reads(monkeypatch)
    _agg(ctx, 250.0)
    for t in TIMES:
        _reps(ctx, 250.0, t)
    assert len(calls) == 1, f"the first draw should read the well once, read {len(calls)}"

    invalidate_threshold_results(ctx)
    calls.clear()
    got = _agg(ctx, 400.0)
    reps = {t: _reps(ctx, 400.0, t) for t in TIMES}
    assert calls == [], f"a threshold edit read the well {len(calls)} time(s)"

    want = _direct_aggregate(ctx, 400.0)
    assert _same(got, want), (got, want)
    assert not _same(got, _direct_aggregate(ctx, 250.0)), "the threshold moved nothing; this measures nothing"
    for t in TIMES:
        (rep,) = reps[t]
        median, mean_all = _expected(ctx, t, 400.0, VAL, GATES, None)
        assert (rep.median, rep.mean_all) == pytest.approx((median, mean_all), rel=0, abs=0, nan_ok=True)


def test_any_other_invalidation_reads_again(ctx, monkeypatch):
    """QC, crop, focus and field exclusions change verdicts without bumping ``rows_version``."""
    from review.core.selection_controller import invalidate_stats_cache

    ctx.state.dataset.cache.clear()
    calls = _count_reads(monkeypatch)
    _agg(ctx, 250.0)
    invalidate_stats_cache(ctx)
    calls.clear()
    _agg(ctx, 400.0)
    assert len(calls) == 1, "a plain invalidation kept a population an edit may have changed"


# ``on_col`` is decided by the same state ``_gate_cache_sig`` signs, so the
# "on_col" case also passes with ``on_col`` alone dropped from the key; it is
# kept because the key must decline when thresholding by gate is switched on.
@pytest.mark.parametrize("change", ["val_col", "gates", "on_col", "rows_version", "ratio"])
def test_each_component_of_the_population_key_declines(ctx, monkeypatch, change):
    from review.core.selection_controller import invalidate_threshold_results
    from tests.test_narrow_rows import RATIO

    ctx.state.dataset.cache.clear()
    calls = _count_reads(monkeypatch)
    kw: dict = {"val_col": RATIO.key()} if change == "ratio" else {}
    _agg(ctx, 250.0, **kw)
    invalidate_threshold_results(ctx)
    calls.clear()
    on_col = None
    if change == "val_col":
        kw = {"val_col": "gfp_mean_intensity"}
    elif change == "gates":
        kw = {"fluor_gates": {"cy5": 9.0}}
    elif change == "on_col":
        ctx.state.cell_gating.applied_gating = _ratio_scatter_gating()
        ctx.state.cell_gating.scatter_gates = [
            {**_ratio_scatter_gating()["thresh_gates"][0], "name": "T"}]
        ctx.state.cell_gating.use_scatter_thresh = True
        on_col = "Gate_On"
    elif change == "rows_version":
        ctx.state.dataset.note_rows_changed()
    elif change == "ratio":
        import dataclasses
        redefined = dataclasses.replace(RATIO, numerator_metric="median_intensity")
        assert redefined.key() == RATIO.key(), "the key must stay the same for this to test the signature"
        ctx.state.channel.ratio_index = {RATIO.key(): redefined}
    got = _agg(ctx, 250.0, **kw)
    assert len(calls) == 1, f"changing {change} was served a remembered population"
    if change in ("val_col", "gates", "on_col"):
        assert _same(got, _direct_aggregate(ctx, 250.0, val_col=kw.get("val_col", VAL),
                                            gates=kw.get("fluor_gates", GATES), on_col=on_col))


def test_a_per_field_aggregate_after_a_plain_one_is_the_direct_one(ctx, monkeypatch):
    """A population built without field labels is rebuilt once when per-field spreads are asked for."""
    ctx.state.dataset.cache.clear()
    calls = _count_reads(monkeypatch)
    _agg(ctx, 250.0)
    got = _agg(ctx, 250.0, per_fov_spread=True)
    again = _agg(ctx, 300.0, per_fov_spread=True)
    assert len(calls) == 2, f"expected one rebuild for the field labels, read {len(calls)}"
    ctx.state.dataset.cache.clear()
    full = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    for threshold, have in ((250.0, got), (300.0, again)):
        want = dl.aggregate_with_threshold_df(
            full, threshold=threshold, use_sem=False, val_col=VAL, fluor_gates=GATES,
            per_fov_spread=True, ratios=ctx.state.channel.ratio_index)
        assert _same(have, want), (have, want)


def test_the_store_is_bounded_by_bytes():
    import numpy as np

    from review.core.stats_cache_state import PopulationStore

    store = PopulationStore(budget_bytes=10_000)
    for i in range(5):
        store[i] = np.zeros(500)          # 4,000 bytes each
    assert len(store) == 2 and store.held_bytes <= 10_000
    assert 4 in store and 3 in store and 0 not in store
    big = np.zeros(5_000)                 # larger than the budget alone
    store["big"] = big
    assert store.get("big") is big, "the newest entry is always kept"


#: The edits that move only the threshold or how a spread is reported, and so
#: keep the populations. Each must call ``invalidate_threshold_results`` and not
#: the plain invalidation, or the redraw after it reads every well again.
_THRESHOLD_ONLY_EDITS = {
    "review/features/home/cell_gating/controller.py": (
        "cell_gating_on_threshold_frac_on_change", "cell_gating_on_threshold_count_on_change"),
    "review/core/plot_toggles.py": ("toggle_sem", "toggle_fov_replicates"),
}


def test_the_threshold_only_edits_keep_the_populations():
    from tests._repo_scan import parse_source

    root = Path(__file__).resolve().parents[1]
    for rel, names in _THRESHOLD_ONLY_EDITS.items():
        tree = parse_source(root / rel)
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in names:
            called = {c.func.id for c in ast.walk(funcs[name])
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            assert "invalidate_threshold_results" in called, f"{rel}::{name} drops the populations"
            assert "invalidate_stats_cache" not in called, f"{rel}::{name} drops the populations"
