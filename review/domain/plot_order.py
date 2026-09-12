"""Applying a saved drag order to a list of things to draw.

The Line tab, the Scatter tab and the per-sample fold-change editor all need
to walk the same members in the same order the plot draws them, and the order
is a saved list of keys rather than a property of the members themselves. That
made this function the one piece of Line-tab code other features imported —
``features/scatter/collect.py`` reached into ``features/lineplot/controller``
for it, which is a feature importing a feature. It is pure list manipulation
with no Qt and no knowledge of what a member is, so it belongs here.
"""

from __future__ import annotations


def apply_order(items, saved_order, key):
    """Reorder ``items`` so entries matching ``saved_order`` come first.

    Items whose ``key(item)`` appears in ``saved_order`` are emitted in the
    saved order; the remainder follow in their natural (input) order. An empty
    or missing saved_order is a no-op (returns items unchanged).
    """
    if not saved_order:
        return list(items)
    items = list(items)
    by_key: dict[str, list[int]] = {}  # str-key -> indices into items
    for idx, it in enumerate(items):
        try:
            k = str(key(it))
        except Exception:
            k = ""
        by_key.setdefault(k, []).append(idx)
    # Track which item *indices* have already been emitted. id()-based
    # dedup was unsafe for small interned strings or any items the
    # iterator might compare equal — distinct list positions could
    # share an id, producing spurious dedup.
    seen_indices: set[int] = set()
    out: list = []
    for k in saved_order:
        bucket = by_key.get(str(k))
        if not bucket:
            continue
        for idx in bucket:
            out.append(items[idx])
            seen_indices.add(idx)
    for idx, it in enumerate(items):
        if idx not in seen_indices:
            out.append(it)
    return out
