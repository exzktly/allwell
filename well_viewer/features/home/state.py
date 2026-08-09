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

    ``pinned_legend_state`` is the plate legend row the user clicked to isolate,
    or ``""``. Kept here rather than on the widget so that switching away and
    back does not silently drop a filter the user is still reading through.
    """

    last_rendered_for: str = ""
    pinned_legend_state: str = ""
    #: Wells the user selected from Home's plate, so the selection they made
    #: here survives a trip to another page and back.
    last_plate_selection: set = field(default_factory=set)
