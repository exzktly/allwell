"""Typed application event bus.

A ``QObject`` carrying the small, fixed catalogue of cross-cutting signals the
viewer needs. Each signal carries the relevant *state object* — never ``app`` —
so a subscriber cannot reach back into the god-object through the bus.

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

    The catalogue is deliberately capped at these four: an event bus that
    accumulates dozens of signals has just become a new god-object. It is also
    load-bearing — every signal here has a real emitter and a real subscriber.
    A fifth, ``redraw_requested``, had neither: it was documented, frozen in
    place by the guard below, and cited as evidence that the bus is
    load-bearing, while nothing in the app ever emitted or listened for it. A
    signal nobody sends is not a seam, it is a claim about the design that the
    code does not support.
    """

    channel_changed = Signal(object)       # ChannelState
    selection_changed = Signal(object)     # SelectionState
    dataset_loaded = Signal(object)        # DatasetState
    plot_options_changed = Signal(object)  # PlotState
