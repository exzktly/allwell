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


def _old_frame_rows(ctx, fov, tp):
    """The selection both per-frame lookups made before: every row's key, then one frame."""
    from review.features.image_browser.review_image.collect import review_row_keys_series

    ctx.state.dataset.cache.clear()
    df = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    keys = review_row_keys_series(ctx, df)
    sel = keys.map(lambda k: k[0] == fov and k[1] == tp and k[2] != "").to_numpy(dtype=bool)
    return df.loc[sel], keys.loc[sel]


@pytest.mark.parametrize("fov, tp", [("1", "0"), ("2", "24"), ("3", "0")])
def test_the_frame_s_rows_are_the_old_selection_read_narrowly(ctx, monkeypatch, fov, tp):
    from review.features.image_browser.review_image.collect import _frame_rows

    want_rows, want_keys = _old_frame_rows(ctx, fov, tp)
    assert len(want_rows), "the frame is empty; this measures nothing"
    calls = _count_reads(monkeypatch)
    rows, keys = _frame_rows(ctx, WELL, fov, tp, ())
    assert list(keys) == list(want_keys)
    assert list(rows.index) == list(want_rows.index)
    for col in ("Included", "QC_Pass", "Crop_Pass", "Gate_Pass"):
        if col in want_rows.columns:
            assert rows[col].tolist() == want_rows[col].tolist(), col
    assert len(calls) == 1 and "m0" not in calls[0], "the frame lookup read the whole well"


@pytest.mark.parametrize("fov", ["2", "3", "4"])   # one excluding layer each: debris, crop, focus
def test_the_exclusion_reasons_and_threshold_map_match_the_whole_well_answer(ctx, monkeypatch, fov):
    import numpy as np

    from review.features.image_browser.review_image import collect

    mask = np.array([[0, 1, 2], [3, 4, 0]], dtype=np.int32)
    got_reasons = collect.review_build_exclusion_reasons(ctx, mask, WELL, fov, "0")
    got_thresh = collect.review_build_threshold_map(ctx, mask, WELL, fov, "0", channel="gfp")
    # The same two calls with the old whole-well selection patched back in.
    monkeypatch.setattr(collect, "_frame_rows",
                        lambda c, w, f, t, want: _old_frame_rows(c, f, t))
    want_reasons = collect.review_build_exclusion_reasons(ctx, mask, WELL, fov, "0")
    want_thresh = collect.review_build_threshold_map(ctx, mask, WELL, fov, "0", channel="gfp")
    assert got_reasons == want_reasons
    assert any(want_reasons.values()) and not all(want_reasons.values()), \
        "every or no cell is excluded; this measures nothing"
    assert got_thresh == want_thresh
    assert set(want_thresh.values()) == {True, False}, "the threshold splits nothing"


def test_a_frame_drawn_again_reads_nothing_until_an_edit(ctx, monkeypatch):
    import numpy as np

    from review.core.selection_controller import invalidate_stats_cache
    from review.features.image_browser.review_image import collect

    mask = np.array([[1, 2], [3, 4]], dtype=np.int32)
    first = collect.review_build_exclusion_reasons(ctx, mask, WELL, "2", "0")
    calls = _count_reads(monkeypatch)
    assert collect.review_build_exclusion_reasons(ctx, mask, WELL, "2", "0") == first
    assert calls == [], "the same frame, drawn again, was read again"
    invalidate_stats_cache(ctx)
    collect.review_build_exclusion_reasons(ctx, mask, WELL, "2", "0")
    assert len(calls) == 1, "an edit that can change a verdict was served the remembered rows"
