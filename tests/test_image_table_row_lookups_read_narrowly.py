"""The Image Table's per-cell row lookups read a well narrowly, once, and keep the answer.

Drawing cell outlines asks, per rendered cell, which nucleus ids the analysis
drops in that frame (``data/inclusion.excluded_nids_for_frame``). It used to
read the well's whole frame through ``get_rows`` — about a second for a
1.5M-cell well, pushed into the row cache (evicting a well some plot was using)
— and remember the answer on that frame, so a well the cache did not keep was
read again for the next cell. The answer is now built from a narrow read and
remembered by the context, keyed on the inclusion counter and ``rows_version``.

The fixture is the narrow-read one with every verdict layer on, so a narrow
read that lost a verdict's input would draw a dropped cell as kept.
"""

from __future__ import annotations

import pytest

from review.data import data_loading as dl
from tests.test_replicate_extras_read_narrowly import _count_reads, ctx  # noqa: F401 - the fixture
from tests.test_narrow_rows import WELL

pytest.importorskip("pyarrow.parquet")


def _reference(ctx) -> dict:
    """The map the old whole-frame build made, from the whole frame."""
    from domain.image_identity import frame_token_series

    ctx.state.dataset.cache.clear()
    full = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    mask = dl.df_included_mask(full)
    fov_n, tp_n = frame_token_series(full)
    out: dict = {}
    for fov, tp, nid in zip(fov_n[~mask], tp_n[~mask], full["nucleus_id"][~mask]):
        out.setdefault((str(fov), str(tp)), set()).add(int(nid))
    return {k: frozenset(v) for k, v in out.items()}


def test_an_unheld_well_is_read_narrowly_once_and_matches_the_whole_frame(ctx, monkeypatch):
    from review.data.inclusion import excluded_nids_by_frame, excluded_nids_for_frame

    want = _reference(ctx)
    assert sum(len(v) for v in want.values()) > 1, "nothing is excluded; this measures nothing"
    calls = _count_reads(monkeypatch)
    got = excluded_nids_by_frame(ctx, WELL)
    assert dict(got) == want
    for (fov, tp), ids in want.items():
        assert excluded_nids_for_frame(ctx, WELL, fov, tp) == ids
    assert WELL not in ctx.state.dataset.cache, "the lookup pushed the whole frame into the row cache"
    assert len(calls) == 1 and calls[0] is not None and "m0" not in calls[0], calls


def test_an_inclusion_edit_reads_again(ctx, monkeypatch):
    from review.core.selection_controller import invalidate_stats_cache
    from review.data.inclusion import excluded_nids_by_frame

    excluded_nids_by_frame(ctx, WELL)
    calls = _count_reads(monkeypatch)
    excluded_nids_by_frame(ctx, WELL)
    assert calls == []
    invalidate_stats_cache(ctx)         # every inclusion edit calls this
    excluded_nids_by_frame(ctx, WELL)
    assert len(calls) == 1
    ctx.state.dataset.note_rows_changed()   # a gating pass
    excluded_nids_by_frame(ctx, WELL)
    assert len(calls) == 2
