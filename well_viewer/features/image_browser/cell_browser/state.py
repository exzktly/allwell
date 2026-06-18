"""Cell Browser feature-local state (Qt-free dataclass)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from well_viewer.features.image_browser.cell_browser.collect import (
    CellDescriptor,
    Criterion,
    SortKey,
)

#: Cells loaded per "page" before the user must click "Load next N".
DEFAULT_PAGE_SIZE = 50
#: Per-cell thumbnail edge (px) in the grid.
THUMB_PX = 110
#: Hard ceiling on cells materialised at once (memory guard).
MAX_TOTAL = 5000


@dataclass
class CellBrowserState:
    """Live state for the Cell Browser tab.

    ``criteria`` / ``timepoints`` / ``fovs`` define the filter; ``sort_keys``
    defines the gallery order; ``cells`` is the last collected result;
    ``loaded`` tracks how many of them have thumbnails so "Load next N" knows
    where to resume.
    """

    criteria: List[Criterion] = field(default_factory=list)
    timepoints: List[float] = field(default_factory=list)   # empty = all
    fovs: List[str] = field(default_factory=list)           # empty = all
    sort_keys: List[SortKey] = field(default_factory=list)  # empty = natural order
    channels_on: List[str] = field(default_factory=list)    # channel tokens shown
    show_boundaries: bool = True
    page_size: int = DEFAULT_PAGE_SIZE
    cells: List[CellDescriptor] = field(default_factory=list)
    loaded: int = 0

    @property
    def remaining(self) -> int:
        return max(0, len(self.cells) - self.loaded)
