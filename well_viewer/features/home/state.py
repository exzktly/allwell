"""Home's own state slice.

Qt-free, stdlib-only — the page's numbers all come from
``ctx.state.dataset.pipeline_info`` and are recomputed on show, so the only
thing worth holding here is what the *user* did to the page itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HomeState:
    """What Home remembers between visits.

    ``last_rendered_for`` is the dataset directory the page was last painted
    for. The page rebuilds its content on every activation, and a rebuild that
    re-runs for the same dataset is wasted work on a page the user is likely to
    pass through often.

    """

    last_rendered_for: str = ""
    #: Which layer of the Sample map tab is showing — Groups, Variables or Well
    #: Labels. On typed state rather than read back off the ``SegmentedControl``
    #: that emitted the change: the widget updates its own index *before* it
    #: emits, so a handler asking the control which layer is current is told the
    #: **incoming** one, and every "what was I looking at a moment ago" question
    #: silently answered "the layer I am moving to". That is what made a layer
    #: switch drop the selection it was supposed to carry.
    sample_map_layer: str = ""
    #: Wells the user selected from Home's plate, so the selection they made
    #: here survives a trip to another page and back.
    last_plate_selection: set = field(default_factory=set)
