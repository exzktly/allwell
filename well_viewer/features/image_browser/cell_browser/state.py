"""Cell Browser feature-local state (Qt-free dataclass)."""

from __future__ import annotations

from dataclasses import dataclass, field

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

    criteria: list[Criterion] = field(default_factory=list)
    timepoints: list[float] = field(default_factory=list)   # empty = all
    fovs: list[str] = field(default_factory=list)           # empty = all
    sort_keys: list[SortKey] = field(default_factory=list)  # empty = natural order
    channels_on: list[str] = field(default_factory=list)    # channel tokens shown
    show_boundaries: bool = True
    use_tophat: bool = False   # False = raw image, True = corrected (top-hat)
    # Caption each crop with the active channel's smFISH spot count (N9). The
    # count is read into CellDescriptor.spot_count at collect time.
    label_spot_count: bool = False
    # Source cells from the active scatter gate(s) instead of the criteria (N1).
    # When True, collect_cells uses the scatter gate inclusion mask and the
    # criteria editor is disabled (the gate is the numeric filter).
    scatter_gate_mode: bool = False
    page_size: int = DEFAULT_PAGE_SIZE
    cells: list[CellDescriptor] = field(default_factory=list)
    loaded: int = 0

    @property
    def remaining(self) -> int:
        return max(0, len(self.cells) - self.loaded)
