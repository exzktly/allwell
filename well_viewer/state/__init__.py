"""Typed domain-state package for the ``WellViewerApp`` teardown.

This package is the key unlock of the runtime_app refactor: it is import-safe
for every controller. Modules here import only stdlib, numpy/pandas, Qt-free
``well_viewer`` models (``ratio_models``, …), and ``PySide6.QtCore`` for the
event bus — never ``runtime_app``, ``views``, or ``tabs``. A guardrail test
(``tests/test_guardrails.py``) enforces that boundary, so controllers can
depend on state objects with no import cycle — dissolving the justification
``ARCHITECTURE.md`` §5.5 gives for the god-object.

State objects are not anaemic data bags: each owns the invariant-enforcing
methods for its domain. ``AppState`` merely aggregates them.
"""

from well_viewer.state.app_state import AppState
from well_viewer.state.channel import ChannelState
from well_viewer.state.dataset import DatasetState
from well_viewer.state.event_bus import EventBus
from well_viewer.state.plot import PlotState
from well_viewer.state.selection import SelectionState
from well_viewer.state.stats import StatsState

__all__ = [
    "AppState", "ChannelState", "DatasetState", "EventBus", "PlotState",
    "SelectionState", "StatsState",
]
