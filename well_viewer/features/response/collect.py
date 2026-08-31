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

# Module scope, not function-local: ``response_compute`` imports nothing from
# here, so there is no cycle to work around — and a deferred feature import
# that is not breaking a cycle is one the budget guard counts for nothing.
from well_viewer.features.response.response_compute import FIT_NONE, fit_response

#: Why a member could not be given an x. Each is a different mistake with a
#: different fix, so they are distinguished rather than pooled into "no value".
NO_VALUE = "no value"
MIXED_VALUE = "wells disagree"
#: Not a member of ``omitted``: a value that will not parse as a number is a
#: *placed* member whose x is unusable, so it rides in ``ResponseData.non_numeric``
#: and is captioned separately. Named here anyway because the roster is what a
#: reader consults to learn the three states apart, and one of them living
#: somewhere else is worth saying rather than leaving as an absence.
NOT_NUMERIC = "value is not a number"

#: Series buckets for members the series variable cannot place. Named rather
#: than dropped, and kept apart from each other: "these wells disagree about
#: Cell line" and "these wells have no Cell line" have different fixes, the
#: same distinction ``omitted`` already draws on the x axis.
_MIXED_SERIES = "\x00mixed"
_NO_SERIES = "\x00none"


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
    """One line on the figure: one timepoint of one series, in ascending x.

    A curve used to be a timepoint and nothing else, so every member landed on
    the same line. Two cell lines each given a dose series were drawn as one
    line doubling back at every dose — a zig-zag that reads as a curve, with a
    single legend entry and, with a fit on, one EC50 fitted across both. That
    is §7's silent substitution: a figure that looks like a plot of one
    condition when it is a plot of two.
    """

    timepoint: float
    points: tuple[ResponsePoint, ...] = ()
    #: The series variable's value id this curve draws, or ``""`` when no
    #: series variable is chosen and the figure is one line per timepoint.
    series_id: str = ""
    #: That value as the user typed it (``"HeLa"``), for the legend.
    series_label: str = ""

    @property
    def key(self) -> tuple[float, str]:
        """What identifies this curve — and what a fit is filed under.

        ``timepoint`` alone was the fit key. With series active that collides:
        two series at one timepoint would share a single ``FitResult``, so one
        curve would be drawn with the other's fitted line and EC50 — the same
        wrong-number-presented-confidently failure the fit refusals exist to
        avoid.
        """
        return (self.timepoint, self.series_id)


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
    #: The fold-change suffix the y axis carries (``"÷ control"``), or ``""``.
    #: Read from the shared state rather than stored per tab: fold change is one
    #: setting across the plotting pages, and a Response curve labelled raw
    #: while the Bar tab beside it is normalised would be the same number
    #: meaning two things on one screen.
    normalised: str = ""
    #: ``(member, timepoint)`` for a point the control could not be resolved
    #: for. Named rather than dropped: a curve missing its top dose because the
    #: vehicle had no sample at that timepoint looks exactly like a curve that
    #: was never dosed that high.
    control_gaps: list[tuple[str, float]] = field(default_factory=list)
    #: How many drawn points have an **undefined** spread — a normalisation
    #: whose denominator had no error to propagate. Counted rather than shown
    #: as zero, because an error bar of zero length reads as a measurement of
    #: perfect agreement.
    undefined_spread: int = 0

    #: Points with no error bar because the member has one replicate — a
    #: separate count from ``undefined_spread``, which is the normalisation
    #: losing a spread the member did have. Two causes, two fixes, so two
    #: numbers rather than one sentence guessing between them.
    solo_spread: int = 0
    #: Set when the log x axis was chosen from the doses rather than asked for.
    #: The Properties panel's ``x_log`` checkbox is bound to the stored
    #: preference, and an unset preference reads as *off* — so on every dose
    #: series that spans two decades the axis is logarithmic while the control
    #: says it is not. Seeding the preference to make them agree would spend the
    #: distinction ``figure_pref_is_set`` exists for: once set, the axis can
    #: never re-derive from a plate with different doses. Saying which it is,
    #: and where to override it, keeps both.
    scale_notice: str = ""
    #: Set when the plate-wide "pool replicates" switch is off. This tab has
    #: no un-pooled mode: a dose is a property of a condition, and one point
    #: per well at the same x is a scatter of replicates rather than a curve.
    #: Said on the figure rather than obeyed silently, because the Bar tab a
    #: click away *is* drawing one bar per well and the two would disagree with
    #: nothing on either to explain it.
    pooled_notice: str = ""
    #: Set when the shared ``vs t0`` axis was asked for and could not be
    #: applied here: with one timepoint drawn, every point is its own baseline
    #: and the whole figure would be a flat line at 1.0.
    t0_declined: str = ""
    #: The series variable's name (``"Cell line"``), or ``""`` when the figure
    #: is one curve per timepoint. Carried so the legend and the empty state can
    #: name it without the renderer reaching back into ctx for it.
    series_name: str = ""
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
    # Split by reason, not pooled. ``data.omitted`` carries ``(member, reason)``
    # with reason in {NO_VALUE, MIXED_VALUE}, and the reason used to be
    # discarded here — so every omission was captioned "no Dose", including the
    # ones whose wells *disagree* about Dose. Two different mistakes with two
    # different fixes, which is what this module's own roster comment says they
    # are, and ``empty_reason`` already branches on them correctly. This is the
    # caption that reaches the axes and travels with a saved figure, so it is
    # the one that most needs to be right.
    for reason, phrase in ((NO_VALUE, f"no {data.variable_name}"),
                           (MIXED_VALUE, f"wells disagree about {data.variable_name}")):
        members = [m for m, r in data.omitted if r == reason]
        if not members:
            continue
        names = ", ".join(members[:3])
        more = f" and {len(members) - 3} more" if len(members) > 3 else ""
        parts.append(f"{len(members)} omitted: {phrase} ({names}{more})")
    if data.non_numeric:
        names = ", ".join(data.non_numeric[:3])
        parts.append(f"{len(data.non_numeric)} off-scale: {names}")
    if data.control_gaps:
        names = ", ".join(f"{m} t={t:g}" for m, t in data.control_gaps[:3])
        more = (f" and {len(data.control_gaps) - 3} more"
                if len(data.control_gaps) > 3 else "")
        parts.append(f"{len(data.control_gaps)} without a control "
                     f"({names}{more})")
    if data.solo_spread:
        parts.append(f"{data.solo_spread} point(s) with no error bar: "
                     f"a single replicate has no spread")
    if data.undefined_spread:
        parts.append(f"{data.undefined_spread} point(s) with no error bar: "
                     f"the control had no spread to propagate")
    if data.t0_declined:
        parts.append(data.t0_declined)
    if data.pooled_notice:
        parts.append(data.pooled_notice)
    if data.scale_notice:
        parts.append(data.scale_notice)
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

    Keyed by ``curve.key`` — ``(timepoint, series_id)`` — rather than by the
    timepoint. Two series drawn at one timepoint are two curves with two EC50s,
    and a timepoint-keyed dict would give the second silently to the first.
    """
    if model == FIT_NONE:
        return {}
    return {
        curve.key: fit_response(
            [p.x for p in curve.points], [p.y for p in curve.points],
            model=model, fixed_top=fixed_top, fixed_bottom=fixed_bottom,
            x_name=(data.variable_name or "x"))
        for curve in data.curves
    }


def collect_response(ctx, variable_id: str, timepoints,
                    series_id: str = "") -> ResponseData:
    """Build every curve for *variable_id* at *timepoints*.

    *series_id* names a second variable that splits the members into separate
    curves — one line per (timepoint, series value). Without it the figure is
    what it always was, one line per timepoint. A member the series variable
    does not resolve to a single value is put on its own ``unassigned`` curve
    rather than dropped or folded into a neighbour, because a member silently
    joining the wrong condition is the failure this whole split exists to end.

    Members come from ``plot_members.response_members`` — over the same
    ``plot_replicate_sets`` seam every other tab pools through — so a plate
    grouped by `Cell line` draws one curve set per cell line against `Dose`, and
    this tab cannot disagree with the Bar tab about what a member is. With no
    replicate set defined it is one member per well lit on the plate, which is
    the fallback the Bar and Line tabs already make: a sample variable is
    assigned per well, so a lone well carries its own dose.

    The x of a member is the value **all** of its wells share. A member whose
    wells disagree, or that holds a well with no value, gets no point: it is
    recorded in :attr:`ResponseData.omitted` with which of the two it was,
    because "you filled these inconsistently" and "you have not filled these"
    have different fixes.

    A value the user typed that is not a number — a `vehicle` row — is neither
    plotted at an invented x nor silently dropped. It goes in
    :attr:`ResponseData.non_numeric`, and the figure's caption names those
    members and counts them, which is the only honest place for a dose that is
    not on the scale.

    It is **not** drawn. This said "for the caller to draw off-scale", and no
    caller does: ``renderer.draw_response`` never reads ``non_numeric``, and the
    only off-scale drawing is the zero-dose position for numeric ``x <= 0``.
    Naming and counting is a defensible answer — a `vehicle` has no position on
    a dose axis at all, unlike a zero — but it is a different answer from the
    one this described, and a docstring is worth what the branch it describes is
    worth.
    """
    from well_viewer.core.channel_state_controller import on_cutoff_for_column
    from well_viewer.domain.plot_members import response_members
    from well_viewer.domain.sample_variables import MIXED, roll_up
    from well_viewer.domain.stats_compute import compute_rep_stats

    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return ResponseData()

    data = ResponseData(variable_name=variable.name, unit=variable.unit)
    # A series on the x variable would draw one curve per dose, each holding a
    # single point — every line one point long, which is not a curve.
    series_variable = (vset.by_id(series_id)
                       if series_id and series_id != variable_id else None)
    well_paths = ctx.state.dataset.well_paths or {}
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col,
        channel=ctx.state.channel.active_channel)
    use_sem = bool(getattr(ctx.state.plot, "use_sem", True))

    members = list(response_members(ctx))
    # Only where there is something to pool. The notice exists because the Bar
    # tab a click away *is* drawing one bar per well with this switch off, and
    # the two figures would disagree with nothing on either to explain it. With
    # no replicate set defined both tabs draw one member per lit well, so they
    # agree — and a figure whose points are single wells captioned "replicates
    # pooled" states something that did not happen.
    if not bool(getattr(ctx.state.plot, "bar_pool_replicates", True)) and any(
            len(getattr(rset, "wells", ()) or ()) > 1 for rset in members):
        data.pooled_notice = "replicates pooled — this tab has no per-well mode"
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
        placed.append((rset, float(value.number), value.label,
                       *_series_of(vset, series_variable, rset, wells)))
    data.placed = len(placed)

    # By x, so each curve's own points come out ascending; the series split
    # below preserves this order within every curve it produces.
    placed.sort(key=lambda row: row[1])
    targets = [float(t) for t in (timepoints or [])]
    normalise = _member_normaliser(ctx, data, targets, threshold, use_sem)

    # A member at a time, not a timepoint at a time. ``vs t0`` divides a
    # member's whole series by its own baseline, so the series has to exist
    # before it can be normalised — collecting timepoint-by-timepoint would
    # hand the normaliser one point at a time and every point would be its own
    # baseline.
    # Keyed by (timepoint, series id) — the curve, not the timepoint.
    by_curve: dict[tuple[float, str], list] = {}
    series_labels: dict[str, str] = {}
    for rset, x, label, series_key, series_label in placed:
        series_labels.setdefault(series_key, series_label)
        raw = []
        for target_t in targets:
            mean, err, _frac, _frac_err = compute_rep_stats(
                ctx, rset, target_t, threshold, use_sem)
            if not _is_number(mean):
                # No sample at this timepoint. Not a zero — a member that was
                # not measured then — so it leaves a gap in the curve rather
                # than a point on the floor.
                continue
            raw.append((target_t, float(mean), float(err or 0.0),
                        0.0, 0, 0, 0.0, 0.0, 0.0))
        n = sum(1 for w in rset.wells if w in well_paths)
        for point in normalise(raw, rset.name):
            target_t, mean, err = point[0], point[1], point[2]
            if not _is_number(mean):
                # Normalisation blanked it: the control resolved to nothing, or
                # had no sample at this timepoint. Named, because a curve that
                # simply stops at its third dose looks like a plate that was
                # never dosed higher.
                data.control_gaps.append((rset.name, float(target_t)))
                continue
            if not _is_number(err):
                # Counted by *cause*, because the two have different fixes and
                # the caption used to name only one of them — the one that
                # cannot fire. A member with a single replicate has no spread to
                # begin with (``compute_rep_stats`` returns NaN), and with fold
                # change off the identity normaliser passes that straight
                # through; the fix is another replicate. A member with several
                # wells that still comes back without a spread lost it in
                # normalisation, and the fix is the control. The old sentence
                # blamed the control in both cases, so a plate with no control
                # at all was told its control had no spread to propagate.
                if n <= 1:
                    data.solo_spread += 1
                else:
                    data.undefined_spread += 1
            by_curve.setdefault((float(target_t), series_key), []).append(
                ResponsePoint(
                    member=rset.name, x=x, y=float(mean),
                    err=float(err) if _is_number(err) else float("nan"),
                    n=n, label=label))

    # Every (timepoint, series) the plate offers, in a stable order: timepoints
    # ascending, and within one timepoint the series in the variable's own
    # declared order. Not sorted alphabetically — the order a user put their
    # values in is the order their legend and their colours should follow, the
    # same rule the value list on the Variables tab is a list for.
    order = _series_order(series_variable, series_labels)
    data.series_name = series_variable.name if series_variable is not None else ""
    data.curves = [
        ResponseCurve(timepoint=t, series_id=sid,
                      series_label=series_labels.get(sid, ""),
                      points=tuple(by_curve.get((t, sid), ())))
        for t in targets for sid in order
        if not series_id or by_curve.get((t, sid))
    ]
    if not series_id:
        # No series chosen: exactly the one curve per timepoint this tab has
        # always drawn, empty ones included, so "nothing was measured then"
        # still reaches ``empty_reason`` as a curve with no points.
        data.curves = [ResponseCurve(timepoint=t,
                                     points=tuple(by_curve.get((t, ""), ())))
                       for t in targets]
    return data


def _series_of(vset, variable, rset, wells) -> tuple[str, str]:
    """``(series key, label)`` for *rset* under *variable*.

    ``("", "")`` when no series variable is chosen. A member whose wells
    disagree, or that holds a well the variable never reached, gets its own
    named bucket rather than being dropped: the x axis can refuse such a member
    because a point needs a number, but a *series* is only a grouping, and
    dropping a fully dosed member for want of a cell-line label would remove
    real data from the figure over an annotation gap.
    """
    from well_viewer.domain.sample_variables import MIXED, roll_up

    if variable is None:
        return "", ""
    value_id = roll_up(vset, variable.id, wells) if wells else None
    if value_id is MIXED:
        return _MIXED_SERIES, f"{variable.name}: mixed"
    if value_id is None:
        return _NO_SERIES, f"no {variable.name}"
    value = variable.value_by_id(value_id)
    if value is None:
        return _NO_SERIES, f"no {variable.name}"
    return str(value_id), value.label


def _series_order(variable, seen: dict[str, str]) -> list[str]:
    """Series keys in the variable's own value order, oddments last."""
    if variable is None:
        return [""]
    declared = [v.id for v in variable.values if v.id in seen]
    extra = [k for k in (_MIXED_SERIES, _NO_SERIES) if k in seen]
    return declared + extra


def _is_number(value) -> bool:
    """A real, finite float. ``None`` and ``NaN`` are both "not computed"."""
    import math
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _member_normaliser(ctx, data: ResponseData, targets, threshold, use_sem):
    """A ``(points, member_name) -> points`` closure over the shared state.

    **The same code path the Line tab normalises through**, not a second
    implementation of the arithmetic. `fold_change`'s helpers have been
    diverged once before by a caller that reimplemented "the same" division
    with one extra `isinstance` check, which turned a ``numpy.float32`` spread
    into no error bar at all — in the most numerically sensitive module in the
    app. Building AggPoint-shaped tuples and handing them to
    ``normalize_member_pts`` costs nine zeros per point and removes that whole
    class of divergence.

    ``vs t0`` is declined, not applied, when fewer than two timepoints are
    drawn: with one, every point is its own baseline and the figure is a flat
    line at 1.0. Declining it is recorded on the data so the figure can say so
    — silently ignoring a toggle the user can see is on would be worse than
    drawing the flat line.
    """
    from dataclasses import replace

    from well_viewer.core.channel_state_controller import get_all_fluor_gates
    from well_viewer.domain import fold_change as fc_module
    from well_viewer.domain.fold_change_pairing import (
        control_denominators, normalize_member_pts,
    )
    from well_viewer.domain.fold_change_series_cache import ControlSeriesCache
    from well_viewer.domain import plot_members as members

    fc = fc_module.fold_change_state(ctx)
    if fc.vs_t0_on and len(targets) < 2:
        data.t0_declined = (
            "vs t0 needs two timepoints — drawn raw at this one")
        fc = replace(fc, vs_t0_on=False)
    if not fc.active:
        return lambda points, _name: points

    data.normalised = fc_module.fold_change_suffix_for(ctx, "response")
    cache = ControlSeriesCache(
        ctx, threshold=threshold, val_col=ctx.state.channel.active_val_col,
        use_sem=use_sem, per_fov_spread=False,
        fluor_gates=get_all_fluor_gates(ctx))
    denominators = control_denominators(
        ctx, cache, fc,
        members.plotted_member_keys(ctx, "response") if fc.per_member else (),
        scope_wells=members.member_wells(ctx, "response"),
        member_rsets=members.member_replicate_sets(ctx))
    keywords = dict(
        fc=fc, n_per_fov=False,
        baseline_tp=fc_module.parse_baseline_tp(ctx),
        op_ctrl=fc_module.parse_fc_op_ctrl(ctx),
        op_t0=fc_module.parse_fc_op_t0(ctx))

    def _normalise(points, name):
        return normalize_member_pts(points, denominators, name, **keywords)

    return _normalise
