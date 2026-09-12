"""Cluster-header normalization: a group label that only duplicates a single
bar's own tick (e.g. one pooled replicate group per a single timepoint) must be
dropped so it doesn't render atop the Mean panel overlapping the figure title.

Qt-free — exercises the pure ``drop_redundant_cluster_keys`` helper and the
``clustered_x_positions`` layout. A cluster header is drawn for each distinct
non-empty ``group_key`` (what ``cluster_centers`` iterates), so the surviving
group-key set is the set of headers.
"""

from __future__ import annotations

from review.features.barplot.collect import (
    BarItem,
    clustered_x_positions,
    drop_redundant_cluster_keys,
)


def _item(display: str, group_key: str) -> BarItem:
    return BarItem(
        key=display, display=display, color="#888888",
        mean=1.0, spread=0.0, has_mean=True,
        frac=0.5, frac_spread=0.0, has_frac=True,
        n_above=1.0, n_above_spread=0.0,
        tp=1.0, group_key=group_key,
    )


def _headers(items):
    return {it.group_key for it in items if it.group_key}


def test_single_timepoint_pooled_groups_get_no_headers():
    # One pooled group per single timepoint: each item is its own cluster and
    # its display already equals the group name → header is redundant.
    items = [_item("CTRL", "CTRL"), _item("TREAT", "TREAT")]
    drop_redundant_cluster_keys(items)
    assert _headers(items) == set()                 # no headers drawn
    assert clustered_x_positions(items) == [0.0, 1.0]  # and no spurious gap


def test_multibar_cluster_keeps_header():
    # Single timepoint, unpooled replicate set: two wells share one group → a
    # real multi-bar cluster whose header (the rset name) is meaningful.
    items = [_item("A01", "CTRL"), _item("A02", "CTRL")]
    drop_redundant_cluster_keys(items)
    assert _headers(items) == {"CTRL"}


def test_multi_timepoint_singleton_keeps_header():
    # Multi-timepoint group-by-well: display is the timepoint, not the group
    # name, so even a one-bar cluster's header still carries the identity.
    items = [_item("t=1", "CTRL")]
    drop_redundant_cluster_keys(items)
    assert _headers(items) == {"CTRL"}
