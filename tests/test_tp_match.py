"""Audit S2 — the shared relative timepoint matcher. Fractional timepoints
round-tripped through a ``:.4g`` combo label must match (an absolute 1e-6
tolerance missed them, so Statistics / aggregation returned "no data" while
fold-change matched); distinct timepoints must still stay separate.
"""
from __future__ import annotations

import numpy as np

from review.domain.tp_match import tp_close, tp_close_mask


def test_whole_numbers_match_and_distinguish():
    assert tp_close(24, 24)
    assert not tp_close(24, 48)
    assert not tp_close(0.5, 1.5)


def test_fractional_4g_roundtrip_matches_where_abs_1e6_failed():
    orig = 2 / 3                       # 0.666666…
    parsed = float(f"{orig:.4g}")      # 0.6667 — as a combo label round-trips
    assert not (abs(orig - parsed) < 1e-6), "abs 1e-6 would have missed this"
    assert tp_close(orig, parsed)


def test_mask_matches_fractional_only_at_target():
    orig = 2 / 3
    parsed = float(f"{orig:.4g}")
    arr = np.array([0.0, orig, 24.0])
    assert tp_close_mask(arr, parsed).tolist() == [False, True, False]


def test_mask_distinguishes_adjacent_whole_timepoints():
    arr = np.array([0.0, 24.0, 48.0])
    assert tp_close_mask(arr, 24.0).tolist() == [False, True, False]
