"""The line plot's CDF panel reads each well narrowly, and draws the whole frame's values.

Tracker R4 (``_Docs/Markdowns/REREAD_TRACKER.md``). ``cdf_values_cached`` used to
hand the filter ``get_rows`` — the whole frame, from disk for every well the
bounded row cache does not hold — for a filter that reads a handful of columns:
16 of the 165 full reads one open of an 8-well plate of 1.5M-cell wells made.
``stats_compute._cdf_rows`` now reads the value column, ``area_px`` and the gated
channels through ``narrow_rows``, which adds every verdict layer's inputs.

The fixture is the two-timepoint, every-layer-on one from
``tests/test_replicate_extras_read_narrowly.py``: each verdict layer excludes
cells no other layer does (``tests/test_narrow_rows.py`` proves it layer by
layer), so a narrow read that lost one verdict's input would draw cells the
whole frame excludes, and the arrays would differ.
"""

from __future__ import annotations

import numpy as np
import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import RATIO, WELL
from tests.test_replicate_extras_read_narrowly import _count_reads, ctx  # noqa: F401 — the fixture

pytest.importorskip("pyarrow.parquet")


def _whole_frame_values(ctx, val_col, gates):
    """The filter on the whole frame — the path the narrow read replaces."""
    ctx.state.dataset.cache.clear()
    full = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    return dl._all_fluor_values_filtered(full, val_col=val_col, fluor_gates=gates,
                                         ratios=ctx.state.channel.ratio_index)


def _cdf(ctx, val_col, gates, compute=None):
    from review.domain.stats_compute import cdf_values_cached

    return cdf_values_cached(ctx, WELL, val_col=val_col, fluor_gates=gates,
                             compute=compute or dl._all_fluor_values_filtered)


@pytest.mark.parametrize("val_col", ["mcherry_mean_intensity", RATIO.key()])
def test_the_cdf_values_are_the_whole_frame_s(ctx, val_col):
    gates = {"cy5": 6.0}
    want = _whole_frame_values(ctx, val_col, gates)
    ctx.state.stats_cache.invalidate()
    ctx.state.dataset.cache.clear()
    got = _cdf(ctx, val_col, gates)
    assert WELL not in ctx.state.dataset.cache, "the whole frame was read into the row cache"
    np.testing.assert_array_equal(got, want)
    # The every-layer fixture is what makes the equality mean something: of the
    # 64 cells (32 per timepoint), the verdicts and the cy5 gate leave 45, so a
    # verdict that failed open on the narrow frame would add cells to it.
    assert want.size == 45, want.size


def test_without_its_gate_channel_the_answer_would_differ(ctx):
    """The gate channel is the caller's own column, not a verdict's: a read that
    dropped it would return an empty array (the filter refuses a gate on a
    channel the frame lacks), so the gate must change the answer here."""
    assert _whole_frame_values(ctx, "mcherry_mean_intensity", {"cy5": 6.0}).size != \
        _whole_frame_values(ctx, "mcherry_mean_intensity", {}).size
    ctx.state.stats_cache.invalidate()
    ctx.state.dataset.cache.clear()
    np.testing.assert_array_equal(_cdf(ctx, "mcherry_mean_intensity", {"cy5": 6.0}),
                                  _whole_frame_values(ctx, "mcherry_mean_intensity", {"cy5": 6.0}))


def test_one_narrow_read_per_well_and_none_on_a_repeat(ctx, monkeypatch):
    ctx.state.stats_cache.invalidate()
    ctx.state.dataset.cache.clear()
    full_reads: list = []
    real = dl.read_well_frame
    monkeypatch.setattr(dl, "read_well_frame", lambda path: full_reads.append(path) or real(path))
    calls = _count_reads(monkeypatch)
    _cdf(ctx, "mcherry_mean_intensity", {"cy5": 6.0})
    _cdf(ctx, "mcherry_mean_intensity", {"cy5": 6.0})
    assert full_reads == [], "a whole frame was parsed"
    assert len(calls) == 1, f"expected one narrow read, got {len(calls)}"
    assert calls[0] is not None and "m0" not in calls[0], f"the read was not narrow: {calls[0]}"


def test_with_the_qc_layer_off_the_area_column_is_still_read(ctx):
    """``area_px`` is the filter's own input — it drops cells with no area —
    and with the QC layer on it is also a verdict's. Off, only the filter
    names it, and a read without it returns nothing at all."""
    import dataclasses

    ctx.state.qc.params = dataclasses.replace(ctx.state.qc.params, enabled=False)
    want = _whole_frame_values(ctx, "mcherry_mean_intensity", {"cy5": 6.0})
    assert want.size > 45, "the QC layer was still excluding its cell"
    ctx.state.stats_cache.invalidate()
    ctx.state.dataset.cache.clear()
    np.testing.assert_array_equal(_cdf(ctx, "mcherry_mean_intensity", {"cy5": 6.0}), want)


def test_a_filter_whose_inputs_are_unknown_gets_the_whole_frame(ctx):
    """Only ``_all_fluor_values_filtered``'s inputs are enumerated; any other
    filter could read a column nobody named, which a narrow frame would lack."""
    seen: list = []

    def other(df, **_kw):
        seen.append(set(df.columns))
        return np.empty(0)

    ctx.state.stats_cache.invalidate()
    ctx.state.dataset.cache.clear()
    _cdf(ctx, "mcherry_mean_intensity", {}, compute=other)
    assert seen and "m0" in seen[0], "a filter with undeclared inputs was handed a narrow frame"
