"""Drawing a response curve — pure matplotlib over what ``collect`` decided.

Reads a :class:`~well_viewer.features.response.collect.ResponseData` and paints
it. Every choice about *what* is in that data was made in ``collect``; the only
decisions here are about the axes.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from well_viewer.domain.line_colors import LineTraceStyle
from well_viewer.domain.response_colors import (
    DEFAULT_RESPONSE_COLOR_MODE, series_style_for_mode,
)
from well_viewer.features.response.collect import empty_reason, omission_caption
from well_viewer.features.response.response_compute import (
    curve_y, ec50_text, spans_enough_decades, zero_position,
)

_logger = logging.getLogger("well_viewer")

def draw_response(ctx, ax, data, *, log_x: bool | None = None,
                  log_base: float = 10.0,
                  color_mode: str = "colour",
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

    *color_mode* is Properties → Colours. ``"colour"`` is the palette this tab
    has always drawn; ``"grey"`` gives each series a shade and a marker from
    ``domain/response_colors``. **The timepoint keeps the dash in both**, so
    switching the mode does not re-encode half the figure.

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
    reason = empty_reason(data, variable_chosen=variable_chosen,
                          timepoints_chosen=timepoints_chosen)
    if reason:
        _draw_empty_state(ax, reason, omission_caption(data))
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

    marks = _CurveStyles(_series_styles(data, color_mode),
                         _timepoint_styles(data))
    drew_off_scale = False
    for curve in data.curves:
        if _draw_one_curve(ax, data, curve, fits, marks,
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
        part for part in (omission_caption(data), _fit_caption(fits, data))
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


def _fit_caption(fits: dict | None, data=None) -> str:
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
    return " ".join(f"No fit at {_curve_name(key, labels)}: {reason}"
                    for key, reason in refused)


def _curve_name(key, labels: dict) -> str:
    """``t=24`` or ``HeLa t=24`` — enough to pick the curve out of the legend."""
    timepoint, _series_id = key
    series = labels.get(key) or ""
    return f"{series} t={timepoint:g}" if series else f"t={timepoint:g}"


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


def _series_ids(data) -> list[str]:
    """The drawn series, in curve order.

    ``collect`` has already put the curves in the series variable's own declared
    value order, so this follows the order the user arranged their values in
    rather than the order the wells happened to be walked.
    """
    ids: list[str] = []
    for curve in data.curves:
        if curve.points and curve.series_id not in ids:
            ids.append(curve.series_id)
    return ids


def _series_styles(data, color_mode: str) -> dict[str, LineTraceStyle] | None:
    """``{series id: style}``, or ``None`` to leave matplotlib's own cycle.

    A series drawn at two timepoints gets **one** style across both, which is
    the whole point of splitting the two encodings: colour (and, in grey mode,
    the marker) says which condition, the dash says which moment.

    ``None`` in the colour mode when there is nothing to tell apart — no series
    variable, or one series — because passing an explicit colour there would
    pin the single curve to the palette's first entry where it used to take
    matplotlib's cycle. **Grey mode still returns a style there**, and must: the
    point of the mode is that the figure carries no colour, and a lone curve
    left on the cycle would come back blue.
    """
    ids = _series_ids(data)
    split = bool(data.series_name and len(ids) > 1)
    if str(color_mode or DEFAULT_RESPONSE_COLOR_MODE) == "grey":
        return {sid: series_style_for_mode("grey", i, None)
                for i, sid in enumerate(ids or [""])}
    if not split:
        return None
    from matplotlib import pyplot as plt

    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color") or ["C0"]
    return {sid: series_style_for_mode("colour", i, cycle[i % len(cycle)])
            for i, sid in enumerate(ids)}


def _curve_label(data, curve) -> str:
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
        parts.append(f"t={curve.timepoint:g}")
    return " · ".join(parts)


class _CurveStyles(NamedTuple):
    """How a curve is inked: the series' style, and the timepoint's dash.

    A pair rather than two loose arguments so the two encodings travel together
    — they are one decision about how the figure is read, and splitting them
    across a signature is how one of them ends up applied and the other
    forgotten. Either half is ``None`` when there is nothing to distinguish,
    and the curve then takes matplotlib's own cycle exactly as it always did.
    """

    series: dict | None
    dashes: dict | None

    def for_curve(self, curve) -> dict:
        """The ``errorbar`` keywords this curve should be drawn with."""
        style: dict = {}
        mark = self.series.get(curve.series_id) if self.series else None
        if mark is None and self.series:
            # Grey mode on a figure with no series split keys its one style on
            # the empty id; a curve whose id is anything else still has to be
            # inked, or the mode would leave colour on the figure it exists to
            # take off.
            mark = next(iter(self.series.values()))
        if mark is not None:
            style["color"] = mark.color
            if mark.marker is not None:
                style["marker"] = mark.marker
                style["markerfacecolor"] = mark.face
                style["markeredgecolor"] = mark.color
        if self.dashes is not None:
            style["linestyle"] = self.dashes[curve.timepoint]
        # The circle this tab has always drawn, unless the mode named a shape.
        # Defaulted here rather than passed at the call site, so the call site
        # cannot supply one too — which it did, and matplotlib rejects the
        # duplicate keyword outright rather than picking a winner.
        style.setdefault("marker", "o")
        return style

    def tag(self, curve, artists) -> None:
        """Say which of *artists*' properties are data rather than style.

        ``apply_export_style_prefs`` runs after every redraw and overrides the
        marker shape, the marker face and the dash pattern from the Properties
        prefs — each unless the artist says that property is carrying meaning.

        **Both cues on this tab are carrying meaning, and neither said so.** The
        timepoint dash was reset on the very first restyle, so a figure of two
        timepoints came back drawn as two solid lines: the encoding was applied,
        then silently removed, on every redraw. Grey mode's markers would go the
        same way, face first — every open marker back to filled, which is half
        the cycle no longer distinguishing anything.
        """
        mark = self.series.get(curve.series_id) if self.series else None
        for artist in artists or ():
            if self.dashes is not None:
                artist._aw_linestyle_is_data = True
            if mark is not None and mark.marker is not None:
                artist._aw_marker_is_data = True
                artist._aw_marker_face_is_data = True


def _draw_one_curve(ax, data, curve, fits, marks: _CurveStyles, *,
                    use_log: bool, off_scale_x: float | None) -> bool:
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

    label = _curve_label(data, curve)
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
        marks.tag(curve, getattr(line, "lines", None) and line.lines[:1])
        if fit is not None and fit.ok:
            _draw_fit_line(ax, fit, [p.x for p in on_axis], use_log,
                           _errorbar_colour(line))

    if off and off_scale_x is not None:
        # Same colour as its own curve would be too clever: this point is
        # deliberately not on the curve, and joining it with a line would
        # assert a slope across a break in the axis.
        ax.errorbar([off_scale_x] * len(off), [p.y for p in off],
                    yerr=[p.err for p in off], fmt="o", capsize=3,
                    markerfacecolor="none")
        return True
    return False
