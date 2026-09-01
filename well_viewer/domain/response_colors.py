"""Response-curve colour mode — one ink per series, or greys told apart twice.

The third sibling of ``domain/bar_colors.py`` and ``domain/line_colors.py``, and
like them a different rule, because the figure it serves has a different number
of encodings already spoken for.

A Response Curves panel carries **two** at once: the colour says which series a
curve belongs to, and the dash says which timepoint it was measured at. The Line
tab's grey mode identifies a trace three ways — shade, dash and marker — and
taking the dash here would overwrite the timepoint with the series, silently, on
a figure whose legend goes on naming both. So this mode identifies a series by
**shade and marker only**, and leaves the dash to the data that already owns it.

The shade and the marker come from ``line_colors`` rather than from a second
cycle of their own: which greys stay apart on both grounds, and which marker
shapes stay apart at three points, are answers this app should have once. What
is local here is the decision about the dash.

The default mode is ``"series"`` rather than ``"plate"`` — the two other tabs'
default is the well's or the group's plate-picker colour, and this one's is not.
A curve is a *series*, which is a value of the Series variable pooled across
however many wells hold it, so there is no plate colour it could take; the
colours come from matplotlib's own property cycle in the order the user arranged
that variable's values. Calling the mode "Plate" on the Properties sidebar would
name a thing the figure does not do.
"""

from __future__ import annotations

from well_viewer.domain.line_colors import LineTraceStyle, grey_trace_style

#: The two values ``PlotState.response_color_mode`` may hold. ``"series"`` is
#: the default and reproduces the historical behaviour exactly.
RESPONSE_COLOR_MODES = ("series", "grey")


def response_color_mode(plot) -> str:
    """The effective mode for *plot* (a ``PlotState``), defaulting to ``"series"``.

    Anything unrecognised reads as ``"series"``, so an unknown value degrades to
    the colours the app has always drawn rather than to a blank or arbitrary
    one — the same bargain the two sibling modules strike, and for the same
    reasons: a state object from an older build has no such field, and
    ``persistence.view_state`` validates the on-disk value against
    :data:`RESPONSE_COLOR_MODES` before assigning, so an edited
    ``persistence.json`` is dropped rather than stored.
    """
    mode = str(getattr(plot, "response_color_mode", "series") or "series")
    return mode if mode in RESPONSE_COLOR_MODES else "series"


def grey_series_style(index: int) -> LineTraceStyle:
    """The grey mode's style for the *index*-th series on a figure.

    :func:`line_colors.grey_trace_style` with the dash dropped, because the dash
    on this figure means the timepoint. Its shade and marker cycles are coprime
    with each other, so the pair is still unique across the first twelve series
    even without the third cue — twelve being what the Line tab's legend names
    individually, and more than a response figure is ever readable with.
    """
    style = grey_trace_style(index)
    return LineTraceStyle(color=style.color, marker=style.marker,
                          filled=style.filled, linestyle=None)


def series_styles(mode: str, series_ids, palette) -> dict[str, LineTraceStyle]:
    """``{series id: style}`` for the ids on one figure, under the named *mode*.

    *series_ids* is the order the curves are drawn in, which ``collect`` already
    put in the series variable's own declared value order, and *palette* is the
    colour cycle the ``"series"`` mode inks from — passed in because choosing it
    means reading matplotlib's rcParams, which this module stays clear of.

    Callers do not branch on the mode; they ask for the styles and draw them, so
    a surface that forgets to check cannot silently keep the old behaviour while
    another honours the new one. Takes the mode string rather than the state
    object for the same reason ``line_colors.trace_style_for_mode`` does.

    **Grey mode styles a lone series too.** The ``"series"`` mode leaves a
    figure with nothing to distinguish alone — one curve, or none split — and
    the caller draws matplotlib's default; grey mode cannot do that, because
    the whole request is "take the colour out of this figure", and one curve
    left in C0 blue is the request refused on the one figure where it is most
    visible.
    """
    ids = list(series_ids)
    if str(mode or "series") != "grey":
        colours = list(palette) or ["C0"]
        return {sid: LineTraceStyle(color=colours[i % len(colours)])
                for i, sid in enumerate(ids)}
    return {sid: grey_series_style(i) for i, sid in enumerate(ids)}
