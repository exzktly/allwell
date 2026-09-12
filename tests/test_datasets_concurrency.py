"""Qt-free tests for datasets.core.concurrency.pick_workers — the shared
worker-count heuristic Import Loose Folder, Concatenate, Subset, and Merge
Plates all use for their per-well thread pools.
"""

from __future__ import annotations

from datasets.core.concurrency import pick_workers


def test_zero_units_returns_one():
    assert pick_workers(0, 0) == 1
    assert pick_workers(4, 0) == 1


def test_auto_caps_at_unit_count():
    assert pick_workers(0, 1) == 1
    assert pick_workers(0, 3) <= 3


def test_auto_caps_at_eight():
    assert pick_workers(0, 1000) <= 8


def test_explicit_request_capped_at_unit_count():
    assert pick_workers(4, 2) == 2


def test_explicit_request_honoured_below_cap():
    assert pick_workers(2, 10) == 2


def test_explicit_request_never_below_one():
    assert pick_workers(-1, 10) >= 1
