"""Application state container.

``AppState`` is a plain aggregate of the typed domain-state objects — not a
mega-dataclass with behaviour (that would be the god-object with a decorator).
Each domain object owns its own invariants; ``AppState`` only holds them so
``WellViewerApp`` can construct and pass them as a unit.

It holds the **core** state slices as fields (``channel``, ``selection``,
``dataset``, ``plot``, ``image_view``). **Feature-owned** slices (``bar``,
``stats``, ``review_image``, ``preview``, ``image_table``, ``playground``) are
*not* fields here — that would force ``core`` to import every feature's state
class and break startup when a feature is removed. They are attached at app
construction by ``features.registry.attach_feature_state`` (the import-inversion
seam), so a feature dropped from the ``FEATURES`` list simply contributes no
slice. ``__getattr__`` turns access to an absent feature slice into a clear
"feature disabled" error instead of a bare ``AttributeError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.core.channel_state import ChannelState
from well_viewer.core.dataset_state import DatasetState
from well_viewer.core.image_view_state import ImageViewState
from well_viewer.core.plot_state import PlotState
from well_viewer.core.selection_state import SelectionState
from well_viewer.core.stats_cache_state import StatsCacheState

# Feature-owned slices and the feature that contributes each — used only to
# produce a helpful error when a disabled feature's slice is accessed.
_FEATURE_STATE_OWNERS = {
    "bar": "Bar Plots",
    "stats": "Statistics",
    "review_image": "Image Browser",
    "preview": "Image Browser",
    "image_table": "Image Browser",
    "playground": "Image Browser",
}


@dataclass
class AppState:
    """Aggregate of the viewer's typed domain-state objects.

    Core slices are fields; feature slices are attached post-construction by
    ``features.registry.attach_feature_state``.
    """

    channel: ChannelState = field(default_factory=ChannelState)
    selection: SelectionState = field(default_factory=SelectionState)
    dataset: DatasetState = field(default_factory=DatasetState)
    plot: PlotState = field(default_factory=PlotState)
    image_view: ImageViewState = field(default_factory=ImageViewState)
    # Group-statistics memoization cache — core, not the Statistics feature's
    # slice (it backs every plot's aggregation; see StatsCacheState).
    stats_cache: StatsCacheState = field(default_factory=StatsCacheState)

    def feature_slice(self, name: str):
        """Return the feature-owned slice *name*, or ``None`` when its feature is
        disabled (descriptor not in ``features.registry.FEATURES``).

        Core code that may run without a feature reads its slice through this
        instead of attribute access, which raises (see ``__getattr__``). Feature
        slices are attached via ``setattr`` so they live in ``__dict__``; a
        disabled feature's slice is simply absent → ``None``.
        """
        return self.__dict__.get(name)

    def __getattr__(self, name: str):
        # Only reached when normal lookup fails — i.e. a feature slice that was
        # never attached because its feature is disabled.
        owner = _FEATURE_STATE_OWNERS.get(name)
        if owner is not None:
            raise AttributeError(
                f"AppState.{name} is unavailable because the {owner!r} feature "
                f"is disabled (its descriptor is not in features.registry.FEATURES). "
                f"Code reaching this slice should be gated on that feature."
            )
        raise AttributeError(name)
