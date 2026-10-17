"""The gating pass reads a well the row cache does not hold narrowly, and counts it the same.

Tracker R9 (``_Docs/Markdowns/REREAD_TRACKER.md``). For an unheld well the pass
used to read the whole frame, write ``Included`` / ``Gate_Pass`` / ``Gate_On``
into it, and let the cache evict it — a write nothing reads, because the gating
projector applies the recorded configuration to any later read. It now reads
the well through ``narrow_rows``, which carries every verdict layer's inputs.

The fixture is the narrow-read one with every verdict layer on, so a narrow
read that lost a layer's input would count that layer's excluded cell as
included, and the pass's own intensity floor excludes one cell, so a read
that lost the gated channel's column would count it.
"""

from __future__ import annotations

import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import LAYERS, WELL, _switch_on, ctx  # noqa: F401

pytest.importorskip("pyarrow.parquet")


def _run_pass(ctx, monkeypatch):  # noqa: F811
    """Run the real pass synchronously; return the count it computed for the well."""
    from review.features.home.cell_gating import controller as cg

    counts = []
    real = cg.apply_fluor_gating

    def spy(df, **config):
        counts.append(real(df, **config))
        return counts[-1]

    monkeypatch.setattr(cg, "apply_fluor_gating", spy)
    # An intensity floor that excludes the fixture's gated cell (gfp 1.0).
    ctx.state.channel.fluor_channels = ["gfp", "cy5", "mcherry"]
    ctx.state.cell_gating.fluor_gates = {"gfp": 10.0}
    ctx.state.cell_gating.use_scatter_gates = False
    ctx.state.cell_gating.use_scatter_thresh = False
    cg.GatingWorker(ctx)._run_inner()
    monkeypatch.setattr(cg, "apply_fluor_gating", real)
    assert len(counts) == 1
    return counts[0]


def _reads(monkeypatch) -> dict:
    from review.data import parquet_cache

    seen = {"full": 0, "columns": []}
    real_full, real_cached = dl.read_well_frame, parquet_cache.read_cached

    def full(*a, **k):
        seen["full"] += 1
        return real_full(*a, **k)

    def cached(path, columns=None):
        seen["columns"].append(columns)
        return real_cached(path, columns=columns)

    monkeypatch.setattr(dl, "read_well_frame", full)
    monkeypatch.setattr(parquet_cache, "read_cached", cached)
    return seen


def test_an_unheld_well_is_counted_from_a_narrow_read_as_the_whole_frame_counts_it(ctx, qtbot, monkeypatch):  # noqa: F811
    for layer in LAYERS:
        _switch_on(ctx, layer)

    dl.get_rows(ctx, WELL)                          # held: the pass gates the whole frame in place
    held_count = _run_pass(ctx, monkeypatch)

    ctx.state.dataset.cache.clear()                 # unheld: the reporter's plate
    seen = _reads(monkeypatch)
    unheld_count = _run_pass(ctx, monkeypatch)

    assert unheld_count == held_count
    assert 0 < held_count < 32, "the gates and layers changed nothing; equal counts would mean nothing"
    assert seen["full"] == 0, "an unheld well was read whole"
    assert seen["columns"] and all(cols is not None and "m0" not in cols for cols in seen["columns"]), (
        "the read was not narrow")
    assert WELL not in ctx.state.dataset.cache, "the pass put a well the cache did not hold into it"


def test_a_later_read_of_an_unheld_well_carries_the_gates(ctx, qtbot, monkeypatch):  # noqa: F811
    """What the old whole-frame write was for, still true: the projector gates
    the next read with the configuration the pass recorded."""
    for layer in LAYERS:
        _switch_on(ctx, layer)
    dl.get_rows(ctx, WELL)
    _run_pass(ctx, monkeypatch)
    gated_in_place = dl.get_rows(ctx, WELL)[["Included", "Gate_Pass", "Gate_On"]].copy()

    ctx.state.dataset.cache.clear()
    _run_pass(ctx, monkeypatch)
    ctx.state.dataset.cache.clear()
    reread = dl.get_rows(ctx, WELL)[["Included", "Gate_Pass", "Gate_On"]]
    assert reread.reset_index(drop=True).equals(gated_in_place.reset_index(drop=True))


def test_curation_is_reasserted_on_held_wells_and_left_to_the_projector_for_the_rest(ctx, monkeypatch):  # noqa: F811
    """The end of the pass (and the dataset load) re-assert manual curation
    over every curated well. For a well the cache does not hold that was a
    whole read into a frame the cache then evicted; the curation projector
    applies the overrides on the next read anyway. The cached answers of such a
    well are still invalidated."""
    from tests.test_narrow_rows import CURATED_CELL
    from review.data.curation import apply_curation_to_cache

    _switch_on(ctx, "curation")
    ctx.state.dataset.cache.clear()
    seen = _reads(monkeypatch)
    before = ctx.state.dataset.rows_version
    apply_curation_to_cache(ctx)
    assert seen["full"] == 0 and seen["columns"] == [], "an unheld curated well was read"
    assert ctx.state.dataset.rows_version > before, "the unheld well's remembered answers were not invalidated"
    assert int(dl.get_rows(ctx, WELL)["Included"].iloc[CURATED_CELL]) == 0, (
        "the next read did not carry the override")

    held = dl.get_rows(ctx, WELL)
    held.loc[held.index[CURATED_CELL], "Included"] = 1   # a pass rewrote it from thresholds
    apply_curation_to_cache(ctx)
    assert int(held["Included"].iloc[CURATED_CELL]) == 0, "a held well was not re-asserted"
