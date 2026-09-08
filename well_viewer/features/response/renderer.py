"""Drawing a response curve — pure matplotlib over what ``collect`` decided.

Reads a :class:`~well_viewer.features.response.collect.ResponseData` and paints
it. Every choice about *what* is in that data was made in ``collect``; the only
decisions here are about the axes.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from well_viewer.domain.response_colors import (
    response_color_mode, series_styles,
)
from well_viewer.features.response.collect import empty_reason, omission_caption
from well_viewer.features.response.response_compute import (
    curve_y, ec50_text, spans_enough_decades, zero_position,
)

_logger = logging.getLogger("well_viewer")

def draw_response(ctx, ax, data, *, log_x: bool | None = None,
                  log_base: float = 10.0,
                  title: str = "", ylabel: str = "",
                  variable_chosen: bool = True,
                  timepoints_chosen: bool = True,
                  fits: dict | None = None) -> None:
    """Paint *data* onto *ax*.

    *log_x* ``None`` asks the data — on once the positive values span two
    decades. Offering it from the data rather than defaulting to linear is the
    difference between a dose series that opens readable and one every user has
    to fix by hand before they can see anything.

    *log_base* is the base that axis is drawn in, from the Properties panel. It
    does not move the points relative to one another — every base is the same
    axis up to a constant factor — so what it changes is the spacing the eye
    reads the gaps against: the minor ticks, and how far off the scale the
    zero-dose column sits, which is one step of *this* base below the lowest
    real dose rather than always a decade.

    **The caption is drawn whenever there is one**, including on an otherwise
    perfect-looking plot. It is the only place a saved figure carries what it
    left out; the status line does not travel with the file.

    **A figure with nothing on it says why.** Bare axes are indistinguishable
    from a broken tab, and they are the same answer to six different mistakes;
    ``collect.empty_reason`` picks the sentence and this draws it in place of
    the axes rather than beside them.

    *fits* is ``{timepoint: FitResult}``. A fit that did not converge draws **no
    line at all** and puts ``EC50 — (no fit)`` in its legend entry: a dashed
    line through the points, or the optimiser's last iterate printed as a
    number, would both assert a shape the data did not support. Why each one
    was refused goes under the axes, where it travels with a saved figure.
    """
    ax.clear()
    # The time correction, resolved once and threaded down. Every hour this
    # figure prints — the legend entries, the no-fit caption and the omission
    # caption — is corrected from this one read, so no two surfaces of one
    # figure can name the same curve differently. The title is the caller's and
    # goes through ``time_rescale.figure_timepoints_label``, which carries the
    # marker; nothing below repeats it.
    #
    # Read defensively for the same reason ``rescale_for`` itself is: this
    # renderer is handed a bare stand-in ctx — and ``None`` — by tests that are
    # about the axis and not about the clock. Resolved above the empty-state
    # return because that path draws the omission caption too, and that caption
    # names hours whenever a member had no control at one.
    from well_viewer.domain.time_rescale import rescale_for as _rescale_for
    _rescale = _rescale_for(
        getattr(getattr(ctx, "state", None), "plot", None))
    reason = empty_reason(data, variable_chosen=variable_chosen,
                          timepoints_chosen=timepoints_chosen)
    if reason:
        _draw_empty_state(ax, reason, omission_caption(data, rescale=_rescale))
        ax._aw_force_xlog = False
        from well_viewer.core.plot_style import apply_ax_style
        apply_ax_style(ax, title, "")
        return
    positives = [p.x for c in data.curves for p in c.points if p.x > 0]
    use_log = spans_enough_decades(positives) if log_x is None else bool(log_x)
    # Where a zero (or negative) dose is drawn on a log axis. One step of the
    # axis's own base below the lowest real dose, so the gap reads as "off the
    # scale" rather than as a measured point that happens to be small — and so
    # that it stays one tick-step wide whatever base the axis is drawn in.
    off_scale_x = zero_position(positives, base=log_base) if use_log else None

    marks = _curve_styles(ctx, data)
    drew_off_scale = False
    for curve in data.curves:
        if _draw_one_curve(ax, data, curve, fits, marks, rescale=_rescale,
                           use_log=use_log, off_scale_x=off_scale_x):
            drew_off_scale = True

    if use_log and positives:
        # Through the shared helper rather than ``ax.set_xscale`` directly, so
        # the base this figure was drawn in is recorded on the axis. The restyle
        # that follows every redraw asks for the same scale and the same base
        # and therefore leaves the axis alone — which is what keeps the dose
        # ticks below. Setting the scale here by hand left it unstamped, the
        # restyle rebuilt the scale, and the doses came back as decades.
        from well_viewer.core.plot_style import apply_axis_log_scale
        apply_axis_log_scale(ax, "x", True, log_base)
        _tick_the_doses(ax, positives,
                        off_scale_x if drew_off_scale else None,
                        _labels_the_user_typed(data))
    # ``_aw_force_xlog`` pins *whether* the axis is logarithmic, the way the
    # scatter tab pins its own log/log: ``apply_export_style_prefs`` re-reads
    # the shared ``x_log`` on every restyle and would otherwise reset an axis
    # whose scale this tab chose from the data. It does not pin the base, which
    # is the user's on this tab as on every other.
    ax._aw_force_xlog = bool(use_log and positives)

    unit = f" ({data.unit})" if data.unit else ""
    ax.set_xlabel(f"{data.variable_name}{unit}" if data.variable_name else "")
    # Also when there is only one curve, because a single curve's legend entry
    # is where its EC50 is written: suppressing it would drop the number the
    # fit exists to produce.
    if len([c for c in data.curves if c.points]) > 1 or fits:
        ax.legend(fontsize="small", title=data.series_name or None)

    caption = " · ".join(
        part for part in (omission_caption(data, rescale=_rescale),
                          _fit_caption(fits, data, rescale=_rescale))
        if part)
    if caption:
        # Under the axes rather than in the title: it is a note about the
        # figure's completeness, not what the figure is of, and a reader
        # scanning titles across a gallery should not have to parse it.
        #
        # Coloured from the theme's muted ink and tagged with ``follows_theme``,
        # because neither happens by default and both are needed. Untagged and
        # uncoloured it took matplotlib's black: legible on the publication
        # theme, invisible on the dark screen one, and untouched by the repaint
        # that flips every other piece of chrome — ``repaint_theme_ink`` walks
        # only the artists carrying that tag.
        from well_viewer.core.plot_style import follows_theme, tokens_for
        follows_theme(
            ax.text(0.0, -0.18, caption, transform=ax.transAxes,
                    fontsize="small", va="top", ha="left", wrap=True,
                    color=tokens_for(ax)[2]),
            muted=True,
        )

    from well_viewer.core.plot_style import apply_ax_style
    apply_ax_style(ax, title, ylabel)


def _scaled_tp(value, rescale) -> str:
    """One timepoint, corrected and formatted the way the whole app formats one.

    Every place this tab prints an hour goes through here — the legend entry,
    the no-fit caption, and (via ``time_rescale.figure_timepoints_label``) the
    title — so the four surfaces of one figure cannot name the same curve two
    different ways. With no correction in force it is byte-identical to the
    ``%g`` these sites used to write.

    The *marker* is deliberately not here. It belongs once per figure, on the
    title, rather than on every legend entry beside every number.
    """
    from well_viewer.domain.time_rescale import format_scaled

    return format_scaled(value, rescale) or f"{value:g}"


def _errorbar_colour(container) -> str | None:
    """The colour matplotlib gave an ``errorbar``, so the fit can match it.

    Read back rather than chosen: taking the next colour from the cycle would
    paint the fit in the *following* curve's colour, which on a two-timepoint
    figure means each fitted line is drawn in the other timepoint's shade.
    """
    with_lines = getattr(container, "lines", None) or ()
    if with_lines and getattr(with_lines[0], "get_color", None):
        return with_lines[0].get_color()
    return None


def _draw_fit_line(ax, fit, xs, use_log: bool, colour) -> None:
    """A smooth fitted curve across the doses, and no further.

    Bounded by the doses tested rather than by the axes: a line running out to
    the edge of the view asserts a response at concentrations nobody gave, and
    the off-scale column at the left would have the curve passing through the
    axis break as though the vehicle were a dose.
    """
    import numpy as np

    lo, hi = min(xs), max(xs)
    if lo <= 0 or hi <= lo:
        return
    grid = (np.logspace(np.log10(lo), np.log10(hi), 200) if use_log
            else np.linspace(lo, hi, 200))
    ys = curve_y(fit, grid)
    if ys is None:
        return
    ax.plot(grid, ys, linestyle="-", linewidth=1.4, alpha=0.9, color=colour,
            zorder=1)


def _fit_caption(fits: dict | None, data=None, *, rescale=None) -> str:
    """Why each refused fit was refused, for under the axes.

    In the caption as well as the legend because the legend has room for
    ``(no fit)`` and not for the sentence, and "no fit" without a reason is the
    thing that sends a user to re-run a plate.

    *fits* is keyed by ``(timepoint, series id)``, so each sentence names the
    curve it belongs to. With two series refused for different reasons, a
    caption saying only ``t=24`` twice would leave a reader unable to tell
    which curve either sentence was about.
    """
    labels = {c.key: c.series_label for c in getattr(data, "curves", ())}
    refused = [(key, f.reason) for key, f in sorted((fits or {}).items())
               if not f.ok and f.reason]
    if not refused:
        return ""
    return " ".join(f"No fit at {_curve_name(key, labels, rescale=rescale)}: {reason}"
                    for key, reason in refused)


def _curve_name(key, labels: dict, *, rescale=None) -> str:
    """``t=24`` or ``HeLa t=24`` — enough to pick the curve out of the legend.

    The hour is the corrected one, because this sentence names a curve *by* its
    legend entry: printing a raw hour here would point at an entry that does
    not exist on the figure.
    """
    timepoint, _series_id = key
    series = labels.get(key) or ""
    stamp = f"t={_scaled_tp(timepoint, rescale)}"
    return f"{series} {stamp}" if series else stamp


def _draw_empty_state(ax, reason: str, caption: str) -> None:
    """The reason, centred, with the axes furniture taken away.

    The ticks and spines go because an empty pair of axes with a sentence
    floating in it reads as a plot that failed to draw; without them it reads
    as a message, which is what it is. The omission caption still goes
    underneath when there is one — it lists *which* groups were dropped, which
    the one-line reason above deliberately does not.

    Both sentences take their colour from the theme and carry the ink tag, for
    the reason ``draw_response``'s caption does: a bare ``ax.text`` is black
    whatever the card is set to, and ``repaint_theme_ink`` walks only the
    tagged artists, so an untagged one stays black through every later flip.
    On the screen theme that is a message the user cannot read at all, and the
    empty state is the one drawing whose entire job is to be read.
    """
    from well_viewer.core.plot_style import follows_theme, tokens_for
    _bg, fg, muted, _grid, _spine = tokens_for(ax)
    follows_theme(
        ax.text(0.5, 0.5, reason, transform=ax.transAxes, ha="center",
                va="center", fontsize="medium", wrap=True, color=fg))
    if caption:
        follows_theme(
            ax.text(0.5, 0.30, caption, transform=ax.transAxes, ha="center",
                    va="top", fontsize="small", wrap=True, color=muted),
            muted=True)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlabel("")


def _labels_the_user_typed(data) -> dict[float, str]:
    """``{x: the label as typed}``, for the doses that agree on one.

    ``ResponsePoint.label`` carries the value's label as the user wrote it —
    ``0.30``, ``1e-3``, ``10 µM`` — for exactly this: a tick that says what
    they typed rather than what the parse produced. Nothing read it, so the
    axis was relabelled from the parsed float and a series typed as a dilution
    came back reformatted.

    A dose is dropped from the map when two values parse to the same number and
    were typed differently (``0.3`` and ``0.30``): one tick cannot say both, so
    it says the number, which is at least true of both.
    """
    seen: dict[float, str] = {}
    dropped: set[float] = set()
    for curve in data.curves:
        for point in curve.points:
            text = (point.label or "").strip()
            if not text:
                continue
            x = float(point.x)
            if x in seen and seen[x] != text:
                dropped.add(x)
            seen.setdefault(x, text)
    return {x: text for x, text in seen.items() if x not in dropped}


def _tick_the_doses(ax, positives, off_scale_x: float | None,
                    typed: dict[float, str] | None = None) -> None:
    """One tick per dose, plus the off-scale column, and nothing else.

    matplotlib's log locator ticks the decades — 1, 10, 100 — which on a
    0.1/0.3/1/3/10 series labels two of the five points and invents three
    positions no well was given. A dose series is a short list of values the
    experimenter chose, so those are the ticks.

    Labelled with what the experimenter typed where that is unambiguous (see
    :func:`_labels_the_user_typed`), falling back to the parsed number.

    The off-scale column gets a dotted rule between it and the axis proper.
    That break is what makes the zero-dose point honest: without it the point
    sits where the axis says a real concentration is, one decade below the
    lowest dose, and a reader takes it for one.
    """
    import numpy as np

    typed = typed or {}
    doses = sorted(set(positives))
    ticks = list(doses)
    labels = [typed.get(d) or _tick_label(d) for d in doses]
    if off_scale_x is not None:
        ax.axvline(float(np.sqrt(off_scale_x * doses[0])),
                   linestyle=":", linewidth=0.8, alpha=0.6)
        ticks = [off_scale_x, *ticks]
        # The column stands for the zero-dose points, which sit at their own
        # x; the label they carry is the one the user typed for that value.
        labels = [typed.get(0.0) or "0", *labels]
        ax.set_xlim(left=off_scale_x / 2.0)
    else:
        ax.set_xlim(left=doses[0] / 2.0)
    ax.set_xlim(right=doses[-1] * 2.0)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    # The minor locator keeps drawing unlabelled decade subdivisions between
    # the doses, which on a break-and-column axis reads as data.
    ax.set_xticks([], minor=True)


def _tick_label(value: float) -> str:
    """A dose tick without the exponent noise ``1e-01`` for 0.1."""
    if value == 0:
        return "0"
    return f"{value:g}"


#: Dash patterns for the timepoints on one figure, in ascending order. Solid
#: first, so the common single-timepoint case is an ordinary solid curve and
#: nothing about the figure changes until a second timepoint is ticked.
_TIMEPOINT_DASHES = ("-", "--", "-.", ":")


def _timepoint_styles(data) -> dict[float, str] | None:
    """``{timepoint: linestyle}``, or ``None`` to leave matplotlib's default.

    ``None`` rather than a dict of solids when there is nothing to distinguish:
    passing an explicit ``linestyle`` on a single-timepoint figure would be a
    no-op that still has to be read and reasoned about at the call site.

    Past the fourth timepoint the patterns repeat. Four overlaid timepoints per
    series is already at the edge of readable, and inventing a fifth pattern
    that nobody can name is worse than a repeat the legend still disambiguates.
    """
    timepoints = sorted({c.timepoint for c in data.curves if c.points})
    if len(timepoints) < 2:
        return None
    return {t: _TIMEPOINT_DASHES[i % len(_TIMEPOINT_DASHES)]
            for i, t in enumerate(timepoints)}


def _curve_styles(ctx, data) -> "_CurveStyles":
    """Both halves of how this figure is inked, decided together.

    One function rather than two calls at the call site, because the two halves
    are one decision: the dash is a cue the *series* may spend exactly when the
    *timepoint* is not spending it, and that is derived here, once, from the
    same ``_timepoint_styles`` result the pair is built from. Passed as an
    argument instead, the call site could hand over a value that disagrees with
    the dashes beside it — and it would draw correctly anyway, because
    ``for_curve`` applies the timepoint's dash last and would overwrite the
    series' claim. A wrong answer that happens to be discarded is the worst kind
    to leave reachable: nothing goes red, and the next reader of
    ``series_styles`` has no way to know the claim was never honoured.
    """
    dashes = _timepoint_styles(data)
    return _CurveStyles(_series_styles(ctx, data, dash_is_spent=dashes is not None),
                        dashes)


def _series_styles(ctx, data, *, dash_is_spent: bool) -> dict | None:
    """``{series id: LineTraceStyle}``, or ``None`` to leave the defaults alone.

    The ids come from the curve order, which ``collect`` already put in the
    series variable's own declared value order — so the inks follow the order
    the user arranged their values in rather than the order the wells happened
    to be walked. A series drawn at two timepoints gets **one** style across
    both, which is the whole point of splitting the two encodings.

    ``None`` in the ``"series"`` mode when there is nothing to distinguish (no
    series variable, or one value): the curve then takes matplotlib's own cycle
    exactly as it always did, and passing a dict of one colour would be a no-op
    the call site still has to read.

    **Never ``None`` in grey mode.** The whole request there is "take the colour
    out of this figure", and returning ``None`` for a lone curve would leave it
    in C0 blue — the request refused on the one figure where it is most
    visible. ``domain/response_colors.series_styles`` is what decides that; the
    branch is stated there rather than here so the two entry points cannot
    answer it differently.

    *dash_is_spent* is whether the timepoint is using the dash on **this**
    figure — ``_timepoint_styles`` draws none at all below two timepoints — and
    grey mode gets the dash as a third cue whenever it is not. Passed in rather
    than recomputed so the two cannot disagree about a figure they are both
    describing.
    """
    ids = []
    for curve in data.curves:
        if curve.points and curve.series_id not in ids:
            ids.append(curve.series_id)
    if not ids:
        return None
    mode = response_color_mode(getattr(getattr(ctx, "state", None), "plot", None))
    if mode != "grey" and (not data.series_name or len(ids) < 2):
        return None
    from matplotlib import pyplot as plt

    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color") or ["C0"]
    return series_styles(mode, ids, cycle, dash_is_spent=dash_is_spent)


def _curve_label(data, curve, *, rescale=None) -> str:
    """The legend entry: the series, the timepoint, or both.

    Only what distinguishes this curve from the others on the figure. A single
    timepoint drawn per series makes ``t=24`` on every entry pure noise, and a
    single series makes its name the same on every entry — so each half is
    included only when it varies.
    """
    parts = []
    if curve.series_label and data.series_name:
        parts.append(curve.series_label)
    if len({c.timepoint for c in data.curves if c.points}) > 1 or not parts:
        parts.append(f"t={_scaled_tp(curve.timepoint, rescale)}")
    return " · ".join(parts)


#: The marker every curve draws when nothing else has an opinion. In grey mode
#: the series owns the shape, so this is what the ``"series"`` mode uses and
#: what an unsplit figure keeps.
_DEFAULT_MARKER = "o"


class _CurveStyles(NamedTuple):
    """How a curve is inked: the series' style, and the dash for its timepoint.

    A pair rather than two loose arguments so the two encodings travel together
    — they are one decision about how the figure is read, and splitting them
    across a signature is how one of them ends up applied and the other
    forgotten. Either half is ``None`` when there is nothing to distinguish,
    and the curve then takes matplotlib's own cycle exactly as it always did.

    The dash belongs to the timepoint whenever there is more than one on the
    figure. Below that nothing is using it, and grey mode takes it as a third
    cue for the series — so the dash is *somebody's* data in both modes and in
    every configuration, which is what the tagging below relies on. See
    ``domain/response_colors.response_grey_cues``.
    """

    styles: dict | None
    dashes: dict | None

    def for_curve(self, curve) -> dict:
        """The ``errorbar`` keywords this curve should be drawn with."""
        style: dict = {"marker": _DEFAULT_MARKER}
        trace = None if self.styles is None else self.styles.get(curve.series_id)
        if trace is not None:
            style["color"] = trace.color
            if trace.marker is not None:
                # Face and edge explicitly, in both fills: half the grey cycle
                # is the same six shapes open, and matplotlib spells an open
                # marker as a face of "none" with a real edge — an open marker
                # with no edge is an invisible one.
                style["marker"] = trace.marker
                style["markerfacecolor"] = trace.face
                style["markeredgecolor"] = trace.color
            if trace.linestyle is not None:
                # Only ever set when the timepoint is not using the dash — the
                # style carries one exactly then, and the branch below would
                # otherwise overwrite it.
                style["linestyle"] = trace.linestyle
        if self.dashes is not None:
            style["linestyle"] = self.dashes[curve.timepoint]
        return style

    def tag(self, container) -> None:
        """Tell the export-style pass which of this curve's cues carry data.

        ``core/plot_style`` stamps the Properties sidebar's marker shape, marker
        fill and line style over every artist on every redraw, and each of the
        three has to be opted out of separately. This figure spends its dash on
        the timepoint in **both** colour modes — so that flag is unconditional,
        and without it one pref pattern replaced four timepoints' worth of
        dashes while the legend went on naming all four. The two marker flags
        are grey mode's, where the shape and the fill together say which series
        a curve is.
        """
        lines = list(getattr(container, "lines", ()) or ())
        artists = [ln for ln in lines[:1] if ln is not None]
        if not artists:
            return
        styles = (self.styles or {}).values()
        marker_is_data = any(t.marker is not None for t in styles)
        dash_is_data = self.dashes is not None or any(
            t.linestyle is not None for t in styles)
        for artist in artists:
            if dash_is_data:
                artist._aw_linestyle_is_data = True
            if marker_is_data:
                artist._aw_marker_is_data = True
                artist._aw_marker_face_is_data = True


def _draw_one_curve(ax, data, curve, fits, marks: _CurveStyles, *,
                    use_log: bool, off_scale_x: float | None,
                    rescale=None) -> bool:
    """Draw *curve* and its fit. Returns whether it put anything off-scale.

    Split out of :func:`draw_response` to keep that function under the
    complexity ratchet, which only ever moves down — and because "how one curve
    is drawn" is a whole thought: its points, the ones that fall off a log
    axis, its legend entry and its fitted line all belong together, and the
    caller only needs to know whether the off-scale column was used.
    """
    if not curve.points:
        return False
    on_axis = [p for p in curve.points if not (use_log and p.x <= 0)]
    off = [p for p in curve.points if use_log and p.x <= 0]

    label = _curve_label(data, curve, rescale=rescale)
    fit = (fits or {}).get(curve.key)
    if fit is not None:
        # The EC50 rides in the legend entry rather than in a box of its own,
        # so that two overlaid curves cannot get their numbers swapped by a
        # reader: the number sits against the swatch of the curve it came from.
        label = f"{label} · {ec50_text(fit, data.unit)}"

    if on_axis:
        line = ax.errorbar([p.x for p in on_axis], [p.y for p in on_axis],
                           yerr=[p.err for p in on_axis],
                           capsize=3, label=label,
                           **marks.for_curve(curve))
        marks.tag(line)
        if fit is not None and fit.ok:
            _draw_fit_line(ax, fit, [p.x for p in on_axis], use_log,
                           _errorbar_colour(line))

    if off and off_scale_x is not None:
        # Same colour as its own curve would be too clever: this point is
        # deliberately not on the curve, and joining it with a line would
        # assert a slope across a break in the axis.
        # The colour is the curve's own here and only here. The comment above
        # is about the *line*, not the ink: what must not be asserted is a
        # slope across the axis break, and there is none. Letting this take the
        # next entry of matplotlib's cycle instead put a stray colour on a
        # figure the user had just asked to be drawn in greys, which is the one
        # mode where a reader cannot tell decoration from data.
        off_colour = marks.for_curve(curve).get("color")
        ax.errorbar([off_scale_x] * len(off), [p.y for p in off],
                    yerr=[p.err for p in off], fmt="o", capsize=3,
                    markerfacecolor="none",
                    **({} if off_colour is None else {"color": off_colour}))
        return True
    return False
