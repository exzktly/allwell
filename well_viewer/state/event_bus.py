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
    they need and nothing more — never ``app``.

    Catalogue contract (runtime_app teardown, Phase 2):

    - ``channel_changed`` — the active fluorescent channel / metric changed;
      payload ``ChannelState``. Fold-change *threshold* changes fold into this
      signal rather than carrying a separate one.
    - ``selection_changed`` — the well / group selection changed; payload
      ``SelectionState``.
    - ``dataset_loaded`` — a dataset finished loading; payload ``DatasetState``.
    - ``plot_options_changed`` — a plot option (fold-change mode, error bars,
      ordering, …) changed; payload ``PlotState``.
    - ``redraw_requested`` — an explicit request to redraw a named scope;
      payload the scope name (``"bar"`` / ``"line"`` / …).

    The catalogue is deliberately capped at these five: an event bus that
    accumulates dozens of signals has just become a new god-object. Status-bar
    messages are *not* here — ``well_viewer/status_signal.py`` owns those.
    """

    channel_changed = Signal(object)       # ChannelState
    selection_changed = Signal(object)     # SelectionState
    dataset_loaded = Signal(object)        # DatasetState
    plot_options_changed = Signal(object)  # PlotState
    redraw_requested = Signal(str)         # scope name
