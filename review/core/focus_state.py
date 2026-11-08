"""The focused cell: the one cell the user is looking at, across every surface.

A Scatter point, a Cell Browser tile, a nucleus clicked in Segmentation and a
Review CSV row each name one cell. Before this slice each surface kept its own
idea of "the cell" (Segmentation a bare nucleus number with no well, the Scatter
popup its own address), so a cell found in one place could not be found again
in another. ``FocusState.cell`` is the one answer; ``review/core/cell_focus.py``
sets it and the ``focused_cell_changed`` bus event tells every surface that
draws it.

Qt-free: imports only stdlib (state-package import contract, ARCHITECTURE §5.9).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusedCell:
    """A cell as the image lookup addresses it: the dataset's own tokens.

    ``fov`` and ``tp`` are kept as the rows spell them (``F001``, ``24h``),
    because that is what the frame loader is handed; comparisons go through the
    shared normalisers (``cell_focus.same_frame``), so ``24h`` and ``24`` match.
    """

    well: str
    fov: str
    tp: str
    nucleus_id: int

    @property
    def caption(self) -> str:
        return " · ".join(p for p in (self.well, self.fov, self.tp, f"#{self.nucleus_id}") if p)


@dataclass
class FocusState:
    """The focused cell, or ``None``. Cleared when a dataset is opened."""

    cell: FocusedCell | None = None
