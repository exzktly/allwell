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

from well_viewer.core.safe import swallow
from well_viewer.domain.sample_variables import NAME_JOIN

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

#: What the curves plot on y. ``MEAN`` is the channel's active property;
#: ``FRACTION`` is the fraction of cells above the ON cutoff, which
#: ``compute_rep_stats`` already returns beside the mean and which this tab
#: used to discard. For a dose response the fraction is often the readout the
#: experiment is actually about, and an EC50 fitted to it is well defined.
Y_MEAN = "mean"
Y_FRACTION = "fraction"
Y_METRICS = (Y_MEAN, Y_FRACTION)

#: Series buckets for members the series variable cannot place. Named rather
#: than dropped, and kept apart from each other: "these wells disagree about
#: Cell line" and "these wells have no Cell line" have different fixes, the
#: same distinction ``omitted`` already draws on the x axis.
_MIXED_SERIES = "\x00mixed"
_NO_SERIES = "\x00none"

#: What joins two splitting variables' value ids into one curve key. A
#: control character rather than a punctuation mark, because a value id is the
#: user's and could hold anything printable — two variables' ids running
#: together under a separator one of them contains would silently merge two
#: curves into one.
_SERIES_SEP = "\x00+"


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
    #: ``(member, timepoint)`` for a point whose control resolved fine but
    #: whose value there is **zero**, so a ratio is undefined. Kept apart from
    #: ``control_gaps`` because the fixes are opposites: that one means "point
    #: this member at a control", this one means "your control has nothing
    #: above the cutoff, so a fold change against it cannot be formed — switch
    #: the operation to subtract, or plot the raw fraction". A control with a
    #: fraction of 0.0 is the *commonest* control in a fraction-above-threshold
    #: experiment, and reporting it as a missing control sends the user to fix
    #: an assignment that is already correct.
    zero_control: list[tuple[str, float]] = field(default_factory=list)
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
    #: ``Y_MEAN`` or ``Y_FRACTION`` — what the curves plot. Carried so the axis
    #: label and the caption describe the numbers actually drawn; an axis
    #: labelled with the mean above a curve of fractions is the one thing a
    #: figure must never do.
    y_metric: str = Y_MEAN
    #: What one replicate was, for the caption: ``"replicate"`` normally,
    #: ``"FOV"`` with the Spread: FOV toggle on. A point with no error bar is
    #: explained by a different fix in each case, and the caption used to name
    #: only the first.
    #: What one replicate is for every spread in this figure: ``well`` or
    #: ``fov`` (``stats_compute.replicate_unit``'s vocabulary, shared with the
    #: other tabs' exports). Rendered as ``FOV`` where a sentence wants it.
    replicate_unit: str = "well"
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


def y_metric_for(draws_fraction: bool) -> str:
    """Which quantity the curves plot: :data:`Y_FRACTION` or :data:`Y_MEAN`.

    A function rather than two constants a caller picks between, so the tab and
    Batch Export cannot disagree about which Property means "plot the fraction"
    — and so a caller outside this feature asks a question instead of importing
    a pair of string constants it would then compare for itself.
    """
    return Y_FRACTION if draws_fraction else Y_MEAN


def omission_caption(data: ResponseData, *, rescale=None) -> str:
    """The sentence the axes carry. ``""`` when nothing was left out.

    On the figure and not only in the status line, because a saved figure
    outlives the session that drew it and a reader months later has no way to
    learn that three groups were dropped.

    *rescale* corrects the hours this sentence names. It names them to identify
    a curve — "A01 t=24 without a control" points at a legend entry — so a raw
    hour here would point at an entry the figure does not carry. Left ``None``
    the numbers are the filename's, byte-identical to what this wrote before,
    which is what the callers with no plot state to read want.
    """
    from well_viewer.domain.time_rescale import format_scaled

    def _tp(value) -> str:
        return format_scaled(value, rescale) or f"{value:g}"

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
        names = ", ".join(f"{m} t={_tp(t)}" for m, t in data.control_gaps[:3])
        more = (f" and {len(data.control_gaps) - 3} more"
                if len(data.control_gaps) > 3 else "")
        parts.append(f"{len(data.control_gaps)} without a control "
                     f"({names}{more})")
    if data.zero_control:
        names = ", ".join(f"{m} t={_tp(t)}" for m, t in data.zero_control[:3])
        more = (f" and {len(data.zero_control) - 3} more"
                if len(data.zero_control) > 3 else "")
        parts.append(f"{len(data.zero_control)} not divisible: the control "
                     f"reads 0 there ({names}{more}) — use subtract, or plot "
                     f"the raw value")
    if data.solo_spread:
        # ``fov`` is the file's word; ``FOV`` is the sentence's. "Add another
        # well" and "add another field" are different fixes, which is why the
        # caption names the unit at all.
        unit = "FOV" if data.replicate_unit == "fov" else data.replicate_unit
        parts.append(f"{data.solo_spread} point(s) with no error bar: "
                     f"a single {unit} has no spread")
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
                    series_id: str | tuple[str, ...] = "", y_metric: str = Y_MEAN,
                    *, val_col: str | None = None) -> ResponseData:
    """Build every curve for *variable_id* at *timepoints*.

    *series_id* names the variable — or, as a tuple, the **variables** — whose
    values split the members into separate curves: one line per (timepoint,
    value, …). Without any, the figure is what it always was, one line per
    timepoint. A member a splitting variable does not resolve to a single value
    is put on its own named curve rather than dropped or folded into a
    neighbour, because a member silently joining the wrong condition is the
    failure this whole split exists to end.

    Two of them is the shape a plate needs when it varies two things besides
    the dose: *Group by* is the outer split and this tab's own *Series* the
    inner, so two cell lines in two media are four curves per timepoint and the
    legend says which is which. The pair arrives ordered and already filtered
    by the caller (``plot_members.response_split_variables``), so this function
    does not decide which control outranks which — it draws what it is given,
    in the order it is given.

    Members come from ``plot_members.response_members``. With no *Group by* in
    force that is the same ``plot_replicate_sets`` seam every other tab pools
    through, so this tab cannot disagree with the Bar tab about what a member
    is; with no replicate set defined it is one member per well lit on the
    plate, which is the fallback the Bar and Line tabs already make, since a
    sample variable is assigned per well and so a lone well carries its own
    dose. Under a *Group by* it is the **cross** of the axis variable with what
    splits the curves — one member per (dose, cell line) — because a member
    pooling every dose of a cell line has no position on a dose axis at all.

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
    from well_viewer.domain.stats_compute import (
        compute_rep_per_fov_stats, compute_rep_stats,
        replicate_unit as _replicate_unit,
    )

    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return ResponseData()

    data = ResponseData(variable_name=variable.name, unit=variable.unit)
    # A split on the x variable would draw one curve per dose, each holding a
    # single point — every line one point long, which is not a curve. Filtered
    # here as well as in the caller because the headless and export paths call
    # this directly with whatever they hold.
    split_ids = (series_id,) if isinstance(series_id, str) else tuple(series_id)
    series_variables = [v for v in (vset.by_id(sid) for sid in split_ids
                                    if sid and sid != variable_id)
                        if v is not None]
    well_paths = ctx.state.dataset.well_paths or {}
    # The tab's own column when it has one, so the ON cutoff, the stats and
    # the axis label all describe the same measurement. ``None`` keeps the
    # shared one, which is what every headless and export caller passes.
    column = val_col or ctx.state.channel.active_val_col
    # ``channel=None`` so the cutoff is derived from *this* column. Passing the
    # shared ``active_channel`` made ``on_cutoff_for_column`` skip its own
    # split entirely and read the shared channel's ThreshFracOn: with the tab
    # on RFP and the band on GFP, every plotted fraction was "the share of
    # cells whose RFP intensity clears GFP's cutoff" — a plausible curve, a
    # wrong number, and nothing on the figure saying so.
    threshold = on_cutoff_for_column(ctx, column)
    use_sem = bool(getattr(ctx.state.plot, "use_sem", True))
    # The Display popover's "Spread: FOV" toggle, which this tab **built and
    # never read**: it always reduced across wells, so a member of one well had
    # one replicate and no spread, and the figure drew a point with no error
    # bar under a control the user had switched on. Both producers return
    # ``(mean, mean_err, frac, frac_err, ...)`` in their first four positions;
    # only what counts as a replicate differs.
    per_fov = bool(getattr(ctx.state.plot, "use_fov_replicates", False))
    stats_for = compute_rep_per_fov_stats if per_fov else compute_rep_stats
    # The canonical spelling, from the one rule the plots reduce by, so the
    # CSV's ``spread_unit`` cell reads the same word here as it does in the
    # Heat Map and scatter-aggregate files and as ``replicate_unit``
    # does on Bar and Line. One well is enough for a unit to exist — the
    # figure-wide statement does not depend on how many any one member has —
    # and the caption below renders it for prose rather than storing a second
    # word for the same fact.
    data.replicate_unit = _replicate_unit(1, per_fov)
    data.y_metric = y_metric if y_metric in Y_METRICS else Y_MEAN

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
    placed = _place_on_x(vset, variable, variable_id, members, well_paths,
                         series_variables, data)
    data.placed = len(placed)

    # By x, so each curve's own points come out ascending; the series split
    # below preserves this order within every curve it produces.
    placed.sort(key=lambda row: row[1])
    targets = [float(t) for t in (timepoints or [])]
    normalise = _member_normaliser(ctx, data, targets, threshold, use_sem,
                                   per_fov, column)

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
        # Was the spread defined *before* normalisation, per timepoint? The
        # cause split below used to ask ``n <= 1`` against the **well** count,
        # which stops being the replicate count the moment Spread: FOV is on —
        # so a two-well member whose per-FOV spread was undefined got blamed on
        # a control it may not even have. The raw spread answers the question
        # directly, in either mode, and needs no second definition of what a
        # replicate is.
        raw_spread_defined: dict[float, bool] = {}
        for target_t in targets:
            mean, err, frac, frac_err = stats_for(
                ctx, rset, target_t, threshold, use_sem, val_col=column)[:4]
            drawn = frac if data.y_metric == Y_FRACTION else mean
            drawn_err = frac_err if data.y_metric == Y_FRACTION else err
            raw_spread_defined[float(target_t)] = _is_number(drawn_err)
            if not _is_number(drawn):
                # No sample at this timepoint. Not a zero — a member that was
                # not measured then — so it leaves a gap in the curve rather
                # than a point on the floor.
                continue
            # The **real** fraction in the fraction slots, not the ``0.0`` that
            # used to sit there. Nothing read it while the tab only ever drew
            # means, but a hardcoded zero for something never computed is §7's
            # own example of the bug — and it becomes live the moment the y
            # metric can be the fraction, because ``normalize_pts`` divides
            # index 3 by the control's *fraction* series. Putting the fraction
            # in the mean slot instead would have divided it by the control's
            # mean intensity: a ratio of two different quantities, drawn under
            # a fold-change label with nothing to say so.
            raw.append((target_t, _num_or_nan(mean), _spread_or_zero(err),
                        _num_or_nan(frac), 0, 0, _spread_or_zero(frac_err),
                        0.0, 0.0))
        # The member's loaded wells, which is what ``ResponsePoint.n`` has
        # always meant. Deliberately **not** relabelled as a replicate count:
        # under Spread: FOV the replicates are the pooled FOVs and this is not
        # that number, so the CSV carries ``spread_unit`` beside it rather than
        # letting a reader reconstruct SD as ``SEM x sqrt(n)`` from two columns
        # that count different things. Deriving the FOV count here would mean a
        # second trip through the data layer from a collector that is otherwise
        # pure, and a second definition of "replicate" beside
        # ``stats_compute.replicate_unit``.
        n = sum(1 for w in rset.wells if w in well_paths)
        for point in normalise(raw, rset.name):
            target_t = point[0]
            # Read back the slot the metric is drawn from, so normalisation is
            # applied to the quantity on the axis rather than to whichever one
            # happened to be first in the tuple.
            if data.y_metric == Y_FRACTION:
                mean, err = point[3], point[6]
            else:
                mean, err = point[1], point[2]
            if not _is_number(mean):
                # Normalisation blanked it: the control resolved to nothing, or
                # had no sample at this timepoint. Named, because a curve that
                # simply stops at its third dose looks like a plate that was
                # never dosed higher.
                if _control_reads_zero(normalise, rset.name, float(target_t),
                                       data.y_metric):
                    data.zero_control.append((rset.name, float(target_t)))
                else:
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
                if not raw_spread_defined.get(float(target_t), True):
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
    order = _series_order(series_variables, series_labels)
    # The same separator the labels below are joined with, from the same
    # constant: a legend titled "Cell line · Medium" over entries reading
    # "HeLa · DMEM" is one sentence, and the two drifting apart would show.
    data.series_name = NAME_JOIN.join(v.name for v in series_variables)
    data.curves = [
        ResponseCurve(timepoint=t, series_id=sid,
                      series_label=series_labels.get(sid, ""),
                      points=tuple(by_curve.get((t, sid), ())))
        for t in targets for sid in order
        if not series_variables or by_curve.get((t, sid))
    ]
    if not series_variables:
        # No series chosen: exactly the one curve per timepoint this tab has
        # always drawn, empty ones included, so "nothing was measured then"
        # still reaches ``empty_reason`` as a curve with no points.
        data.curves = [ResponseCurve(timepoint=t,
                                     points=tuple(by_curve.get((t, ""), ())))
                       for t in targets]
    return data


def _place_on_x(vset, variable, variable_id, members, well_paths,
                series_variables, data: ResponseData) -> list[tuple]:
    """Which members get an x, and why each of the rest does not.

    Returns ``[(rset, x, label, series_key, series_label)]`` and records every
    refusal on *data* as it goes. Split out of :func:`collect_response` to keep
    that function under the complexity ratchet, which only moves down — and
    because deciding a member's position on the axis is a whole thought,
    separate from measuring it at each timepoint.

    x is resolved once per member, not once per (member, timepoint): a member's
    dose does not depend on when it was measured, and resolving it inside the
    timepoint loop would report the same omission once per curve.
    """
    from well_viewer.domain.sample_variables import MIXED, roll_up

    placed: list[tuple] = []
    for rset in members:
        wells = [w for w in (getattr(rset, "wells", None) or [])
                 if w in well_paths]
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
                       *_series_of(vset, series_variables, wells)))
    return placed


def _series_of(vset, variables, wells) -> tuple[str, str]:
    """``(curve key, legend label)`` for a member under *variables*.

    ``("", "")`` when nothing splits the curves. With more than one the key and
    the label are each variable's answer joined — ``"hela|dmem"`` keyed,
    ``"HeLa · DMEM"`` read — so a curve stays one string to the renderer, the
    fit dict and the legend, and two layers cost no new shape.

    A member whose wells disagree about one of them, or that holds a well the
    variable never reached, gets its own named bucket rather than being
    dropped: the x axis can refuse a member because a point needs a number, but
    a split is only a grouping, and dropping a fully dosed member for want of a
    cell-line label would take real data off the figure over an annotation gap.
    """
    from well_viewer.domain.sample_variables import MIXED, roll_up

    if not variables:
        return "", ""
    keys, labels = [], []
    for variable in variables:
        value_id = roll_up(vset, variable.id, wells) if wells else None
        value = (variable.value_by_id(value_id)
                 if value_id is not None and value_id is not MIXED else None)
        if value_id is MIXED:
            keys.append(_MIXED_SERIES)
            labels.append(f"{variable.name}: mixed")
        elif value is None:
            keys.append(_NO_SERIES)
            labels.append(f"no {variable.name}")
        else:
            keys.append(str(value_id))
            labels.append(value.label)
    return _SERIES_SEP.join(keys), NAME_JOIN.join(labels)


def _series_order(variables, seen: dict[str, str]) -> list[str]:
    """Curve keys in the variables' own declared value order, oddments last.

    The product of the declared values, outermost variable slowest — the order
    a user put their values in is the order their legend and their colours
    follow, which is the same rule the value list on the Variables tab is a
    list for. Only combinations a member actually landed in are kept, so a
    plate where one cell line never got the top dose has no empty curve for it.

    Oddments — a member whose wells disagree, or that the variable never
    reached — come last, in the order they were placed, which is ascending x.
    They are named rather than dropped for the reason :func:`_series_of` gives.
    """
    from itertools import product

    if not variables:
        return [""]
    declared = [_SERIES_SEP.join(ids)
                for ids in product(*([v.id for v in var.values]
                                     for var in variables))]
    known = set(declared)
    return ([key for key in declared if key in seen]
            + [key for key in seen if key not in known])


def _is_number(value) -> bool:
    """A real, finite number. ``None`` and ``NaN`` are both "not computed".

    Delegates to ``domain.numeric.is_number`` rather than spelling
    ``isinstance(value, (int, float))`` here. That spelling accepts
    ``numpy.float64`` (which subclasses ``float``) and rejects
    ``numpy.float32`` (which does not) — the exact divergence §7.5 #8 records,
    where a shared helper's extra isinstance check turned a float32 spread into
    no error bar at all in the most numerically sensitive module in the app.
    The producers cast through ``float()`` today so nothing reaches here as a
    float32, but this function now gates the drawn **fraction** as well as the
    mean, and a latent narrow predicate on a widened path is worth one line.
    """
    from well_viewer.domain.numeric import is_number
    return is_number(value)


def _member_normaliser(ctx, data: ResponseData, targets, threshold, use_sem,
                       per_fov_spread: bool = False, val_col: str = ""):
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
        ctx, threshold=threshold,
        val_col=val_col or ctx.state.channel.active_val_col,
        # Matched to the members' own producer. Hardcoded ``False`` meant that
        # with Spread: FOV on, a per-FOV numerator was normalised against a
        # cross-well denominator — two different poolings in one ratio.
        use_sem=use_sem, per_fov_spread=per_fov_spread,
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

    # Carried on the closure rather than returned alongside it: every caller
    # wants the function, and only the shortfall reporting wants the
    # denominators. ``op_ctrl`` rides along because "the control reads zero" is
    # only a problem for a ratio — subtracting zero is perfectly well defined.
    _normalise.denominators = denominators
    _normalise.op_ctrl = keywords["op_ctrl"]
    return _normalise


def _num_or_nan(value) -> float:
    """*value* as a float, or NaN — never a stand-in zero.

    §7's rule at the point it bites: "not computed" and "computed as zero" are
    different facts, and a fraction of 0.0 is a real, meaningful measurement
    (nothing above the cutoff). Writing 0.0 for a fraction that was never
    computed would draw a curve sitting on the floor of the axis and reading as
    a plate where the treatment killed everything.
    """
    return float(value) if _is_number(value) else float("nan")


def _spread_or_zero(value) -> float:
    """A spread as a float: ``None`` becomes 0.0, and **NaN stays NaN**.

    The NaN is load-bearing and must survive to the caller, which counts it as
    a point with no error bar and captions why. An earlier version of this
    helper flattened every non-number to 0.0 and took that distinction with it:
    a lone replicate's undefined spread arrived as a defined spread of zero,
    which draws as a zero-length error bar — a measurement of perfect agreement
    — and stopped being counted at all. That is §7's computed-as-zero sin, and
    it went in behind a docstring claiming the opposite.

    ``None`` alone becomes 0.0, which is what the original ``float(err or 0.0)``
    did and what ``normalize_pts`` expects for "no spread supplied".
    """
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _control_reads_zero(normalise, member: str, target_t: float,
                        y_metric: str) -> bool:
    """Whether this member's control resolved but reads 0 at *target_t*.

    The two ways a normalised point comes back blank look identical once it is
    NaN, and they have opposite fixes — "point this member at a control" versus
    "your control has nothing above the cutoff". Asking the denominator which
    it was is the only way to tell, and reporting the wrong one sends the user
    to change a control assignment that is already correct.

    Only for a ratio. ``subtract`` handles a zero denominator perfectly well,
    which is exactly what ``fold_change._baseline_usable`` records about the
    same number on the t0 axis.
    """
    denominators = getattr(normalise, "denominators", None)
    if denominators is None or getattr(normalise, "op_ctrl", "") != "ratio":
        return False
    with swallow("ask the control denominator what it held"):
        _ref, outcome, stats, frac, _n = denominators.series_for(member)
        if not outcome:
            return False
        series = frac if y_metric == Y_FRACTION else stats
        value = (series or {}).get(target_t)
        # ``series_for`` yields ``{t: (value, spread)}``; a control that never
        # resolved yields empty dicts, which is ``control_gaps``, not this.
        if isinstance(value, (tuple, list)) and value:
            value = value[0]
        return _is_number(value) and float(value) == 0.0
    return False
