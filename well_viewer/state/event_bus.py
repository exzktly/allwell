"""Typed application event bus.

A ``QObject`` carrying the small, fixed catalogue of cross-cutting signals the
viewer needs. Each signal carries the relevant *state object* — never ``app`` —
so a subscriber cannot reach back into the god-object through the bus. Modelled
on the existing Qt-free pub-sub in ``well_viewer/status_signal.py``.

The catalogue is deliberately capped: an event bus that accumulates dozens of
signals has just become a new god-object. Keep it to the cross-cutting few.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    """The viewer's cross-cutting signal catalogue.

    Signal payloads are domain-state objects (typed ``object`` here because the
    state classes are introduced phase by phase). Subscribers receive the state
    they need and nothing more.
    """

    channel_changed = Signal(object)       # ChannelState
    threshold_changed = Signal(object)     # ChannelState
    dataset_loaded = Signal(object)        # DatasetState   (Phase 3)
    selection_changed = Signal(object)     # SelectionState (Phase 2)
    plot_options_changed = Signal(object)  # PlotState      (Phase 4)
    redraw_requested = Signal(str)         # scope name
    status_message = Signal(str)
