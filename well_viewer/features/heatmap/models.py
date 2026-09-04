"""Heatmap layout data model.

A ``HeatmapLayout`` is an arbitrary R×C grid that maps each cell to zero or
more well tokens. Layouts are decoupled from the physical 8×12 plate so
users can rearrange wells for visual clarity (dose-response strips,
factorial designs, replicate collapses, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterable

from well_viewer.data.data_loading import parse_well_token


@dataclass
class HeatmapLayout:
    name: str
    rows: int
    cols: int
    # (row, col) -> list of well tokens. Missing keys = empty cell.
    cells: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    row_labels: list[str] | None = None
    col_labels: list[str] | None = None
    # The tick indices whose label the **user typed** (the canvas double-click
    # rename). ``row_labels`` alone cannot answer that question: it holds the
    # plate default's ``A``–``H`` and a hand-typed name in the same slot, and a
    # user is perfectly entitled to type ``A``. The distinction is load-bearing
    # because the Heat Map also derives tick names from the sample map, and a
    # derived name may replace a *default* while never replacing a name the
    # user wrote — a rename that redraws as something else is a control that
    # does not work.
    row_labels_typed: list[int] = field(default_factory=list)
    col_labels_typed: list[int] = field(default_factory=list)

    def label_typed(self, kind: str, index: int) -> bool:
        """Whether the user named tick *index* on the ``"row"`` / ``"col"`` axis."""
        typed = self.row_labels_typed if kind == "row" else self.col_labels_typed
        return int(index) in set(typed or ())

    def mark_label_typed(self, kind: str, index: int, typed: bool = True) -> None:
        """Record (or clear) that the user named tick *index* on that axis.

        Clearing is what an emptied rename dialog does: the label falls back to
        the grid's own, which is a default again and may be spoken for by the
        sample map once more.
        """
        store = self.row_labels_typed if kind == "row" else self.col_labels_typed
        index = int(index)
        if typed:
            if index not in store:
                store.append(index)
                store.sort()
        elif index in store:
            store.remove(index)

    # ── basic editing helpers ────────────────────────────────────────────────

    def assigned_wells(self) -> list[str]:
        out: list[str] = []
        seen: set = set()
        for wells in self.cells.values():
            for w in wells:
                if w not in seen:
                    seen.add(w)
                    out.append(w)
        return out

    def assign(self, row: int, col: int, wells: Iterable[str]) -> None:
        clean = [str(w).strip() for w in wells if str(w).strip()]
        if not clean:
            self.cells.pop((row, col), None)
        else:
            self.cells[(row, col)] = clean

    def resize(self, new_rows: int, new_cols: int) -> list[str]:
        """Resize the grid in place. Returns wells that were dropped."""
        new_rows = max(1, int(new_rows))
        new_cols = max(1, int(new_cols))
        dropped: list[str] = []
        new_cells: dict[tuple[int, int], list[str]] = {}
        for (r, c), wells in self.cells.items():
            if 0 <= r < new_rows and 0 <= c < new_cols:
                new_cells[(r, c)] = list(wells)
            else:
                dropped.extend(wells)
        self.rows = new_rows
        self.cols = new_cols
        self.cells = new_cells
        # A marker for a tick the grid no longer has would come back to life,
        # attached to a different row, the next time the grid grew.
        self.row_labels_typed = [i for i in self.row_labels_typed if i < new_rows]
        self.col_labels_typed = [i for i in self.col_labels_typed if i < new_cols]
        return dropped

    def transpose(self) -> None:
        """Swap rows ↔ columns in place: cells, dimensions, and labels."""
        self.cells = {(c, r): list(wells) for (r, c), wells in self.cells.items()}
        self.rows, self.cols = int(self.cols), int(self.rows)
        self.row_labels, self.col_labels = self.col_labels, self.row_labels
        # The markers follow their labels across the swap. Left behind they
        # would protect whichever ticks now sit at those indices on the other
        # axis, which is a rename the user never made.
        self.row_labels_typed, self.col_labels_typed = (
            self.col_labels_typed, self.row_labels_typed)

    def reorder_rows(self, src: int, dst: int) -> None:
        """Move row *src* to position *dst* (insert-at semantics)."""
        if src == dst or not (0 <= src < self.rows and 0 <= dst < self.rows):
            return
        order = list(range(self.rows))
        popped = order.pop(src)
        order.insert(dst, popped)
        new_cells: dict[tuple[int, int], list[str]] = {}
        for new_r, old_r in enumerate(order):
            for c in range(self.cols):
                if (old_r, c) in self.cells:
                    new_cells[(new_r, c)] = list(self.cells[(old_r, c)])
        self.cells = new_cells
        if self.row_labels:
            while len(self.row_labels) < self.rows:
                self.row_labels.append(str(len(self.row_labels) + 1))
            self.row_labels = [self.row_labels[old_r] for old_r in order]
        # A marker names a position, so a move has to renumber it with the
        # label it belongs to — otherwise dragging a renamed row leaves the
        # protection behind on whatever row took its place.
        self.row_labels_typed = sorted(
            new_r for new_r, old_r in enumerate(order)
            if old_r in set(self.row_labels_typed or ()))

    def reorder_cols(self, src: int, dst: int) -> None:
        """Move column *src* to position *dst* (insert-at semantics)."""
        if src == dst or not (0 <= src < self.cols and 0 <= dst < self.cols):
            return
        order = list(range(self.cols))
        popped = order.pop(src)
        order.insert(dst, popped)
        new_cells: dict[tuple[int, int], list[str]] = {}
        for r in range(self.rows):
            for new_c, old_c in enumerate(order):
                if (r, old_c) in self.cells:
                    new_cells[(r, new_c)] = list(self.cells[(r, old_c)])
        self.cells = new_cells
        if self.col_labels:
            while len(self.col_labels) < self.cols:
                self.col_labels.append(str(len(self.col_labels) + 1))
            self.col_labels = [self.col_labels[old_c] for old_c in order]
        self.col_labels_typed = sorted(
            new_c for new_c, old_c in enumerate(order)
            if old_c in set(self.col_labels_typed or ()))

    # ── serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rows": int(self.rows),
            "cols": int(self.cols),
            "cells": [
                {"row": int(r), "col": int(c), "wells": list(wells)}
                for (r, c), wells in sorted(self.cells.items())
            ],
            "row_labels": list(self.row_labels) if self.row_labels else None,
            "col_labels": list(self.col_labels) if self.col_labels else None,
            "row_labels_typed": [int(i) for i in self.row_labels_typed],
            "col_labels_typed": [int(i) for i in self.col_labels_typed],
        }

    @classmethod
    def from_dict(cls, data: dict) -> HeatmapLayout:
        rows = int(data.get("rows", 1) or 1)
        cols = int(data.get("cols", 1) or 1)
        cells: dict[tuple[int, int], list[str]] = {}
        for entry in data.get("cells", []) or []:
            try:
                r = int(entry.get("row", -1))
                c = int(entry.get("col", -1))
            except (TypeError, ValueError):
                continue
            if not (0 <= r < rows and 0 <= c < cols):
                continue
            wells = [str(w).strip() for w in (entry.get("wells", []) or []) if str(w).strip()]
            if wells:
                cells[(r, c)] = wells
        row_labels = data.get("row_labels")
        col_labels = data.get("col_labels")

        def _typed(key: str, limit: int) -> list[int]:
            """The in-range tick indices under *key*, deduplicated and sorted.

            A layout saved before this key existed has none, which is the right
            answer for it: nothing in that file distinguishes a typed label
            from the grid's own, so treating them all as typed would freeze
            every plate default against the sample map, and treating none as
            typed loses at most the protection on a label the user can re-type.
            """
            raw = data.get(key)
            if not isinstance(raw, list):
                return []
            out: set[int] = set()
            for entry in raw:
                try:
                    i = int(entry)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < limit:
                    out.add(i)
            return sorted(out)

        return cls(
            name=str(data.get("name", "") or "").strip() or "layout",
            rows=rows,
            cols=cols,
            cells=cells,
            row_labels=list(row_labels) if isinstance(row_labels, list) and row_labels else None,
            col_labels=list(col_labels) if isinstance(col_labels, list) and col_labels else None,
            row_labels_typed=_typed("row_labels_typed", rows),
            col_labels_typed=_typed("col_labels_typed", cols),
        )


def layouts_from_dict(data: object) -> list[HeatmapLayout]:
    if not isinstance(data, list):
        return []
    out: list[HeatmapLayout] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(HeatmapLayout.from_dict(entry))
        except (TypeError, ValueError):
            continue
    return out


# ── default plate layout (8x12) ──────────────────────────────────────────────

_PLATE_NAME = "Plate (default)"
_PLATE_ROW_LABELS = list("ABCDEFGH")
_PLATE_COL_LABELS = [f"{i:d}" for i in range(1, 13)]


def make_plate_layout(well_tokens: Iterable[str]) -> HeatmapLayout:
    """Synthesize the default 8×12 plate layout from a list of well tokens."""
    cells: dict[tuple[int, int], list[str]] = {}
    for tok in well_tokens:
        rc = parse_well_token(tok)
        if rc is None:
            continue
        r, c = rc
        if 0 <= r < 8 and 0 <= c < 12:
            cells.setdefault((r, c), []).append(tok)
    return HeatmapLayout(
        name=_PLATE_NAME,
        rows=8,
        cols=12,
        cells=cells,
        row_labels=list(_PLATE_ROW_LABELS),
        col_labels=list(_PLATE_COL_LABELS),
    )


PLATE_DEFAULT_NAME = _PLATE_NAME
