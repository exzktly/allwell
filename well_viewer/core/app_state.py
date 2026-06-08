"""Application state container.

``AppState`` is a plain aggregate of the typed domain-state objects — not a
mega-dataclass with behaviour (that would be the god-object with a decorator).
Each domain object owns its own invariants; ``AppState`` only holds them so
``WellViewerApp`` can construct and pass them as a unit.

It grows one field per phase of the runtime_app teardown — ``channel``,
``selection``, ``dataset``, ``plot``, ``stats``, ``review_image``,
``preview``, and ``bar`` (feature-local Bar Plots tab state).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.core.channel_state import ChannelState
from well_viewer.core.dataset_state import DatasetState
from well_viewer.core.plot_state import PlotState
from well_viewer.features.barplot.state import BarPlotState
from well_viewer.features.image_table.state import ImageTableState
from well_viewer.features.review_image.state import PreviewState, ReviewImageState
from well_viewer.core.selection_state import SelectionState
from well_viewer.features.stats.state import StatsState


@dataclass
class AppState:
    """Aggregate of the viewer's typed domain-state objects."""

    channel: ChannelState = field(default_factory=ChannelState)
    selection: SelectionState = field(default_factory=SelectionState)
    dataset: DatasetState = field(default_factory=DatasetState)
    plot: PlotState = field(default_factory=PlotState)
    stats: StatsState = field(default_factory=StatsState)
    review_image: ReviewImageState = field(default_factory=ReviewImageState)
    preview: PreviewState = field(default_factory=PreviewState)
    bar: BarPlotState = field(default_factory=BarPlotState)
    image_table: ImageTableState = field(default_factory=ImageTableState)
