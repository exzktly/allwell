"""Response Curves per-feature state.

One field. Which timepoints are drawn is this tab's own choice and nobody
else's: the shared ``ctx.state.plot.focus_timepoint`` is *read* to seed it and
never written, because writing it moves Heat Map and Distribution to whatever
this tab happened to open on.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResponseState:
    """``{timepoint_string: shown}``, the same shape ``BarPlotState`` uses.

    Empty means "not populated yet", and the controller then seeds it from the
    shared focus timepoint — **one** timepoint, not all of them. A twelve-
    timepoint plate opened as twelve overlapping curves per condition is not a
    plot anyone can read, and a user who wants that can ask for it.

    A populated dict with everything off means "show nothing", which is a state
    the user chose and is left alone.
    """

    tp_selections: dict[str, bool] = field(default_factory=dict)
