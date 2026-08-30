"""What a response curve is made of, decided without a figure.

One curve per timepoint, one point per value of a numeric sample variable, with
a spread across the replicates that value pools. Pure: it takes a ctx and
returns dataclasses, so every rule below is driven by a fast-lane test rather
than by looking at a plot.

**The x value comes from F's store, not from a field of this tab's own.** A
private numeric field per curve would be a second place to say what a well was
dosed with, and the two would disagree the first time either was edited.

**What has no x is named, never dropped.** matplotlib's default for a point with
no x is to draw nothing, which is a silent substitution: a plate of eight groups
drawn as five looks exactly like a plate of five. Every omission comes back in
:class:`ResponseData.omitted` with the reason, and the caller is expected to put
it on the axes, in the CSV and on the status line — three places, because the
figure gets saved and the status line does not travel with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Why a member could not be given an x. Each is a different mistake with a
#: different fix, so they are distinguished rather than pooled into "no value".
NO_VALUE = "no value"
MIXED_VALUE = "wells disagree"
NOT_NUMERIC = "value is not a number"


@dataclass(frozen=True)
class ResponsePoint:
    """One member at one timepoint: its x, its mean and its spread."""

    member: str
    x: float
    y: float
    err: float
    n: int
    #: The value's label as the user typed it (``"10"``, ``"vehicle"``), for a
    #: tick that says what they wrote rather than what it parsed to.
    label: str = ""


@dataclass(frozen=True)
class ResponseCurve:
    """One timepoint's points, in ascending x."""

    timepoint: float
    points: tuple[ResponsePoint, ...] = ()


@dataclass
class ResponseData:
    """Everything a response figure draws, and everything it had to leave out."""

    variable_name: str = ""
    unit: str = ""
    curves: list[ResponseCurve] = field(default_factory=list)
    #: ``(member, reason)`` — one row per member that could not be placed.
    omitted: list[tuple[str, str]] = field(default_factory=list)
    #: Members whose x is a value that parses to no number: a `vehicle` row in a
    #: dose series. Kept apart from the rest because they are the user's own
    #: deliberate entry rather than a gap, and a caption that lumps them in
    #: reads as an error report about a plate that is fine.
    non_numeric: list[str] = field(default_factory=list)
    #: How many members were given an x. Recorded rather than inferred, because
    #: "no member had a dose" and "every member had a dose but none was measured
    #: at the chosen timepoint" both arrive here as zero points, and they have
    #: nothing to do with each other: the first is fixed on the Variables tab,
    #: the second by picking a different timepoint.
    placed: int = 0

    def has_points(self) -> bool:
        return any(c.points for c in self.curves)


def omission_caption(data: ResponseData) -> str:
    """The sentence the axes carry. ``""`` when nothing was left out.

    On the figure and not only in the status line, because a saved figure
    outlives the session that drew it and a reader months later has no way to
    learn that three groups were dropped.
    """
    parts = []
    if data.omitted:
        names = ", ".join(m for m, _ in data.omitted[:3])
        more = f" and {len(data.omitted) - 3} more" if len(data.omitted) > 3 else ""
        parts.append(f"{len(data.omitted)} omitted: no {data.variable_name} "
                     f"({names}{more})")
    if data.non_numeric:
        names = ", ".join(data.non_numeric[:3])
        parts.append(f"{len(data.non_numeric)} off-scale: {names}")
    return " · ".join(parts)


def empty_reason(data: ResponseData, *, variable_chosen: bool = True,
                 timepoints_chosen: bool = True) -> str:
    """Why this figure has nothing on it, and what to do about it. ``""`` if it has.

    Empty axes are the worst possible answer to any of the causes below. They
    look identical to a plate that genuinely measured nothing, they look
    identical to a broken tab, and none of them tells a user which of the six
    quite different mistakes they made — a variable not picked, a timepoint not
    ticked, values not filled, values filled inconsistently, values that are not
    numbers, or a timepoint at which nothing was measured. Each has its own fix
    and its own sentence, and each names the place the fix is made.
    """
    if data.has_points():
        return ""
    if not variable_chosen:
        return "Pick a Number variable under X to plot against."
    name = data.variable_name or "that variable"
    if not timepoints_chosen:
        return "No timepoints selected — tick at least one under Timepoints."
    if data.placed:
        return (f"Nothing was measured at the selected timepoints. "
                f"{data.placed} group(s) have a {name} value; pick a timepoint "
                f"they were imaged at.")
    if not data.omitted and not data.non_numeric:
        return ("No groups to plot — select wells on the plate, or define "
                "replicate groups on Home.")
    if data.non_numeric and not data.omitted:
        return (f"No group's {name} is a number — every value is off-scale. "
                f"Give the values numbers on Home → Variables.")
    reasons = {reason for _member, reason in data.omitted}
    if reasons == {NO_VALUE}:
        return (f"No group has a {name} value — fill it onto the plate on "
                f"Home → Variables.")
    if reasons == {MIXED_VALUE}:
        return (f"Every group's wells disagree about {name}, and one group "
                f"cannot hold two values. Check the fill on Home → Variables.")
    return (f"No group could be placed on {name} — the caption below says why "
            f"for each. Fix them on Home → Variables.")


def fit_curves(data: ResponseData, model: str, *, fixed_top: float | None = None,
               fixed_bottom: float | None = None) -> dict:
    """``{timepoint: FitResult}`` — one fit per curve, all on the same terms.

    **The model and the pinned plateaus are shared across every curve**, which
    is the whole point of drawing more than one: two timepoints fitted with
    independently floating tops produce two EC50s that cannot be compared, and
    comparing them is the only reason to overlay them.

    A curve that cannot be fitted still gets an entry. An absent key and a
    refused fit look identical to a caller that used ``.get()``, and the
    refusals are the half that has to reach the legend.
    """
    from well_viewer.features.response.response_compute import (
        FIT_NONE, fit_response,
    )
    if model == FIT_NONE:
        return {}
    return {
        curve.timepoint: fit_response(
            [p.x for p in curve.points], [p.y for p in curve.points],
            model=model, fixed_top=fixed_top, fixed_bottom=fixed_bottom)
        for curve in data.curves
    }


def collect_response(ctx, variable_id: str, timepoints) -> ResponseData:
    """Build every curve for *variable_id* at *timepoints*.

    Members come from ``plot_members.plot_replicate_sets`` — the same seam every
    other tab pools through — so a plate grouped by `Cell line` draws one curve
    set per cell line against `Dose`, and this tab cannot disagree with the Bar
    tab about what a member is.

    The x of a member is the value **all** of its wells share. A member whose
    wells disagree, or that holds a well with no value, gets no point: it is
    recorded in :attr:`ResponseData.omitted` with which of the two it was,
    because "you filled these inconsistently" and "you have not filled these"
    have different fixes.

    A value the user typed that is not a number — a `vehicle` row — is neither
    plotted at an invented x nor silently dropped. It goes in
    :attr:`ResponseData.non_numeric` for the caller to draw off-scale, which is
    the only honest place for a dose that is not on the scale.
    """
    from well_viewer.core.channel_state_controller import on_cutoff_for_column
    from well_viewer.domain.plot_members import plot_replicate_sets
    from well_viewer.domain.sample_variables import MIXED, roll_up
    from well_viewer.domain.stats_compute import compute_rep_stats

    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return ResponseData()

    data = ResponseData(variable_name=variable.name, unit=variable.unit)
    well_paths = ctx.state.dataset.well_paths or {}
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)
    use_sem = bool(getattr(ctx.state.plot, "use_sem", True))

    members = list(plot_replicate_sets(ctx))
    # x is resolved once per member, not once per (member, timepoint): a
    # member's dose does not depend on when it was measured, and resolving it
    # inside the loop would report the same omission once per curve.
    placed: list[tuple[object, float, str]] = []
    for rset in members:
        wells = [w for w in (getattr(rset, "wells", None) or []) if w in well_paths]
        value_id = roll_up(vset, variable_id, wells) if wells else None
        if value_id is MIXED:
            data.omitted.append((rset.name, MIXED_VALUE))
            continue
        if value_id is None:
            data.omitted.append((rset.name, NO_VALUE))
            continue
        value = variable.value_by_id(value_id)
        if value is None or value.number is None:
            data.non_numeric.append(rset.name)
            continue
        placed.append((rset, float(value.number), value.label))
    data.placed = len(placed)

    placed.sort(key=lambda row: row[1])
    for target_t in [float(t) for t in (timepoints or [])]:
        points = []
        for rset, x, label in placed:
            mean, err, _frac, _frac_err = compute_rep_stats(
                ctx, rset, target_t, threshold, use_sem)
            if mean is None:
                # No sample at this timepoint. Not a zero — a member that was
                # not measured then — so it leaves a gap in the curve rather
                # than a point on the floor.
                continue
            n = sum(1 for w in rset.wells if w in well_paths)
            points.append(ResponsePoint(member=rset.name, x=x, y=float(mean),
                                        err=float(err or 0.0), n=n, label=label))
        data.curves.append(ResponseCurve(timepoint=target_t,
                                         points=tuple(points)))
    return data
