"""Application state container.

``AppState`` is a plain aggregate of the typed domain-state objects — not a
mega-dataclass with behaviour (that would be the god-object with a decorator).
Each domain object owns its own invariants; ``AppState`` only holds them so
``WellViewerApp`` can construct and pass them as a unit.

It grows one field per phase of the runtime_app teardown — ``channel`` and
``selection`` so far; ``dataset``, ``plot``, … follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.state.channel import ChannelState
from well_viewer.state.selection import SelectionState


@dataclass
class AppState:
    """Aggregate of the viewer's typed domain-state objects."""

    channel: ChannelState = field(default_factory=ChannelState)
    selection: SelectionState = field(default_factory=SelectionState)
