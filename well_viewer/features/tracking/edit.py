"""Track editing operations with undo (Qt-free, §D / PR5c).

Manual corrections to a well's tracks operate directly on the three additive
columns (``track_id`` / ``track_parent_id`` / ``tree_id``) of the per-cell
DataFrame — the same columns the engines write and the plots/persistence read.
:class:`TrackEditor` wraps a DataFrame with the edit verbs (relabel, merge,
delete, split, assign/clear division) and a snapshot-based undo stack, so the
Editor sub-tab and any test exercise the exact same logic.
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from well_viewer.data.data_loading import parse_timepoint_hours
from .model import TRACK_ID_COL, TRACK_PARENT_COL, TREE_ID_COL, TRACK_COLUMNS
from .engines.base import UNTRACKED


def _tp_key(tp: str):
    h = parse_timepoint_hours(str(tp))
    return (0, h) if h is not None else (1, str(tp))


class TrackEditor:
    """Stateful editor over a well DataFrame's track columns, with undo."""

    def __init__(self, df: pd.DataFrame, *, tp_col: str = "timepoint",
                 max_undo: int = 50) -> None:
        self.df = df
        self.tp_col = tp_col
        self._undo: "List[dict]" = []
        self._max_undo = max_undo
        for col in TRACK_COLUMNS:
            if col not in df.columns:
                df[col] = UNTRACKED

    # ── undo plumbing ────────────────────────────────────────────────────────

    def _snapshot(self) -> None:
        self._undo.append({c: self.df[c].copy() for c in TRACK_COLUMNS})
        if len(self._undo) > self._max_undo:
            self._undo.pop(0)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        snap = self._undo.pop()
        for c, series in snap.items():
            self.df[c] = series
        return True

    # ── helpers ──────────────────────────────────────────────────────────────

    def track_ids(self) -> "List[int]":
        ids = pd.to_numeric(self.df[TRACK_ID_COL], errors="coerce")
        return sorted({int(v) for v in ids.dropna() if int(v) != UNTRACKED})

    def _mask(self, track_id: int):
        return pd.to_numeric(self.df[TRACK_ID_COL], errors="coerce") == int(track_id)

    def _next_track_id(self) -> int:
        ids = self.track_ids()
        return (max(ids) + 1) if ids else 1

    # ── edit verbs (each pushes one undo frame) ──────────────────────────────

    def relabel(self, old_id: int, new_id: int) -> int:
        """Change every cell of *old_id* to *new_id* (returns rows changed)."""
        mask = self._mask(old_id)
        n = int(mask.sum())
        if n:
            self._snapshot()
            self.df.loc[mask, TRACK_ID_COL] = int(new_id)
            # tree/parent references following the renamed track move with it
            self.df.loc[self._tree_mask(old_id), TREE_ID_COL] = int(new_id)
            self.df.loc[self._parent_mask(old_id), TRACK_PARENT_COL] = int(new_id)
        return n

    def _tree_mask(self, tid: int):
        return pd.to_numeric(self.df[TREE_ID_COL], errors="coerce") == int(tid)

    def _parent_mask(self, tid: int):
        return pd.to_numeric(self.df[TRACK_PARENT_COL], errors="coerce") == int(tid)

    def merge(self, into_id: int, from_id: int) -> int:
        """Merge *from_id* into *into_id* (returns rows moved)."""
        if into_id == from_id:
            return 0
        mask = self._mask(from_id)
        n = int(mask.sum())
        if n:
            self._snapshot()
            self.df.loc[mask, TRACK_ID_COL] = int(into_id)
            self.df.loc[self._tree_mask(from_id), TREE_ID_COL] = int(into_id)
            self.df.loc[self._parent_mask(from_id), TRACK_PARENT_COL] = int(into_id)
        return n

    def delete(self, track_id: int) -> int:
        """Untrack every cell of *track_id* (track_id → -1)."""
        mask = self._mask(track_id)
        n = int(mask.sum())
        if n:
            self._snapshot()
            self.df.loc[mask, TRACK_ID_COL] = UNTRACKED
            self.df.loc[mask, TRACK_PARENT_COL] = UNTRACKED
            self.df.loc[mask, TREE_ID_COL] = UNTRACKED
        return n

    def split(self, track_id: int, at_timepoint: str) -> Optional[int]:
        """Split *track_id* into two at *at_timepoint*: cells at that timepoint
        and later become a new track. Returns the new track id (or None)."""
        mask = self._mask(track_id)
        if not mask.any():
            return None
        cut = _tp_key(at_timepoint)
        later = mask & self.df[self.tp_col].map(lambda t: _tp_key(t) >= cut)
        before = mask & ~later
        if not later.any() or not before.any():
            return None  # need cells on both sides of the cut for a real split
        new_id = self._next_track_id()
        self._snapshot()
        self.df.loc[later, TRACK_ID_COL] = new_id
        self.df.loc[later, TRACK_PARENT_COL] = UNTRACKED
        self.df.loc[later, TREE_ID_COL] = new_id
        return new_id

    def assign_division(self, parent_id: int, child_id: int) -> bool:
        """Mark *child_id* as a division child of *parent_id* (lineage edge)."""
        if parent_id == child_id or not self._mask(child_id).any() or not self._mask(parent_id).any():
            return False
        self._snapshot()
        parent_tree = pd.to_numeric(
            self.df.loc[self._mask(parent_id), TREE_ID_COL], errors="coerce")
        tree = int(parent_tree.iloc[0]) if len(parent_tree) else int(parent_id)
        self.df.loc[self._mask(child_id), TRACK_PARENT_COL] = int(parent_id)
        self.df.loc[self._mask(child_id), TREE_ID_COL] = tree
        return True

    def clear_division(self, child_id: int) -> bool:
        """Remove the lineage parent of *child_id* (it becomes its own root)."""
        mask = self._mask(child_id)
        if not mask.any():
            return False
        self._snapshot()
        self.df.loc[mask, TRACK_PARENT_COL] = UNTRACKED
        self.df.loc[mask, TREE_ID_COL] = int(child_id)
        return True
