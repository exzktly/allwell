"""The gating pass gates the wells the row cache holds and reads no other well.

For a well the cache does not hold, the pass used to read the frame (whole,
then narrowly), write ``Included`` / ``Gate_Pass`` / ``Gate_On`` into it and let
the cache evict it — a write nothing read, because the gating projector applies
the recorded configuration to the next read of the well, and a count used only
by the debug log. It now skips such a well without reading it.

The fixture is the narrow-read one with every verdict layer on, and the pass's
intensity floor excludes one cell, so "the next read carries the gates" is a
claim with a cell that differs.
"""

from __future__ import annotations

import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import LAYERS, WELL, _switch_on, ctx  # noqa: F401

pytest.importorskip("pyarrow.parquet")


def _run_pass(ctx) -> None:  # noqa: F811
    """Run the real pass synchronously, with an intensity floor that excludes the gated cell."""
    from review.features.home.cell_gating import controller as cg

    ctx.state.channel.fluor_channels = ["gfp", "cy5", "mcherry"]
    ctx.state.cell_gating.fluor_gates = {"gfp": 10.0}
    ctx.state.cell_gating.use_scatter_gates = False
    ctx.state.cell_gating.use_scatter_thresh = False
    cg.GatingWorker(ctx)._run_inner()


def _reads(monkeypatch) -> list:
    from review.data import parquet_cache

    seen: list = []
    real_full, real_cached = dl.read_well_frame, parquet_cache.read_cached

    def full(*a, **k):
        seen.append("whole")
        return real_full(*a, **k)

    def cached(path, columns=None):
        seen.append(columns)
        return real_cached(path, columns=columns)

    monkeypatch.setattr(dl, "read_well_frame", full)
    monkeypatch.setattr(parquet_cache, "read_cached", cached)
    return seen


def test_an_unheld_well_is_not_read_and_its_next_read_carries_the_gates(ctx, qtbot, monkeypatch):  # noqa: F811
    for layer in LAYERS:
        _switch_on(ctx, layer)
    dl.get_rows(ctx, WELL)                          # held: gated in place
    _run_pass(ctx)
    gated_in_place = dl.get_rows(ctx, WELL)[["Included", "Gate_Pass", "Gate_On"]].copy()
    assert gated_in_place["Included"].sum() < len(gated_in_place), "the floor excluded nothing"

    ctx.state.dataset.cache.clear()                 # unheld: the reporter's plate
    seen = _reads(monkeypatch)
    before = ctx.state.dataset.rows_version
    _run_pass(ctx)
    assert seen == [], f"the pass read a well the cache does not hold: {seen}"
    assert WELL not in ctx.state.dataset.cache
    assert ctx.state.dataset.rows_version > before, "nothing remembered of the well was invalidated"

    monkeypatch.undo()
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
    assert seen == [], "an unheld curated well was read"
    assert ctx.state.dataset.rows_version > before, "the unheld well's remembered answers were not invalidated"
    monkeypatch.undo()
    assert int(dl.get_rows(ctx, WELL)["Included"].iloc[CURATED_CELL]) == 0, (
        "the next read did not carry the override")

    held = dl.get_rows(ctx, WELL)
    held.loc[held.index[CURATED_CELL], "Included"] = 1   # a pass rewrote it from thresholds
    apply_curation_to_cache(ctx)
    assert int(held["Included"].iloc[CURATED_CELL]) == 0, "a held well was not re-asserted"
