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

    #: The variable on the x axis, by **id**, mirroring the tab's X combo.
    #:
    #: The combo is where the user picks it, but a widget is not readable from
    #: ``domain/``, and the axis variable is exactly what the member seam needs:
    #: under a *Group by*, this tab pools into the **cross** of the axis with
    #: what splits its curves, so ``plot_members`` has to know what the axis is.
    #: Kept in step by ``controller.refresh_x_combo``, which is the one place
    #: the combo's selection is decided.
    x_variable_id: str = ""

    #: The variables whose values split the members into separate curves, by
    #: **id**, outermost first; empty for one curve per timepoint.
    #:
    #: A **list**, because one split is not enough for the plates this tab is
    #: for: a dose series run across cell lines *and* RNAs is one curve per
    #: (cell line, RNA), and asking the user to spend the plate-wide *Group by*
    #: on the second one makes a tab-local question into a global setting. The
    #: order is the order the curves are labelled and sorted in.
    #:
    #: Ids rather than names or combo indices, for the reason ``view_state_io``
    #: records about the x variable: a name is renamed and an index moves when
    #: another variable is added, and either would restore the figure against a
    #: different variable while looking as though nothing had gone wrong.
    series_variable_ids: list[str] = field(default_factory=list)

    #: The channel this tab measures, and the Property on it — **this tab's
    #: own**, as the aggregate Scatter tab's are, not the shared plotting
    #: band's. Response owns them so its Property list can carry ``Fraction
    #: above threshold``: that entry is not a column, and putting it in the
    #: shared combo would offer Bar, Line, Distribution and Heat Map a metric
    #: their collectors resolve to ``f"{channel}_{metric}"`` and cannot read.
    #: Empty means "follow the shared channel", which is what a tab that has
    #: never been touched should do.
    #:
    #: Stored as the combo **labels**, which is what the shared
    #: ``populate_metric_combo`` produces and what ``METRIC_LABEL_TO_KEY``
    #: consumes — so this tab holds no second vocabulary of its own.
    channel: str = ""
    metric_label: str = ""

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
