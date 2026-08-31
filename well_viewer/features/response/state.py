"""Response Curves per-feature state.

Which timepoints are drawn is this tab's own choice and nobody else's: the
shared ``ctx.state.plot.focus_timepoint`` is *read* to seed it and never
written, because writing it moves Heat Map and Distribution to whatever this
tab happened to open on.

The fit settings live here for the same reason. A model choice is about this
figure, and a 4PL pinned to a top of 100 on a normalised assay would be
meaningless on the raw intensities the Bar tab draws.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.features.response.response_compute import FIT_NONE


@dataclass
class ResponseState:
    """This tab's timepoint selection and its fit settings."""

    #: ``{timepoint_string: shown}``, the same shape ``BarPlotState`` uses.
    #:
    #: Empty means "not populated yet", and the controller then seeds it from
    #: the shared focus timepoint — **one** timepoint, not all of them. A
    #: twelve-timepoint plate opened as twelve overlapping curves per condition
    #: is not a plot anyone can read, and a user who wants that can ask for it.
    #:
    #: A populated dict with everything off means "show nothing", which is a
    #: state the user chose and is left alone.
    tp_selections: dict[str, bool] = field(default_factory=dict)

    #: The variable whose values split the members into separate curves, by
    #: **id**, or ``""`` for one curve per timepoint. An id rather than a name
    #: or a combo index, for the reason ``view_state_io`` records about the x
    #: variable: a name is renamed and an index moves when another variable is
    #: added, and either would restore the figure against a different variable
    #: while looking as though nothing had gone wrong.
    series_variable_id: str = ""

    #: ``FIT_NONE`` / ``FIT_4PL`` / ``FIT_HILL``. Off by default: a fitted line
    #: through points is an assertion about the shape of the response, and the
    #: user should be the one making it rather than finding it already drawn.
    fit_model: str = FIT_NONE

    #: A plateau the experiment already knows — a normalised assay whose top is
    #: 100, a background-subtracted one whose bottom is 0. ``None`` leaves the
    #: parameter free. Pinned rather than bounded to a narrow range, so it costs
    #: a degree of freedom rather than consuming one.
    #:
    #: **Shared across every curve on the figure**, which is the point: two
    #: timepoints fitted with independently floating plateaus produce two EC50s
    #: that are not comparable, and comparing them is the only reason to draw
    #: both.
    fit_top: float | None = None
    fit_bottom: float | None = None
