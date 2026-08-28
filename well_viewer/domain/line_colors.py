"""Line-plot colour mode — plate-rank colours, or greys told apart by marker.

The sibling of ``domain/bar_colors.py``, and deliberately not the same rule.
The Bar plot's "Grey" mode collapses every column onto one ink because a bar is
already identified by the tick label under it, so the colour was decoration. A
trace has no tick label. Give every line the same grey and the panel becomes a
tangle with only a legend to unpick it, which is worse than the plate colours
the mode was meant to replace.

So this mode identifies a trace three ways at once:

* a **shade** from :data:`theme.Colors.plot_grey_ramp`,
* a **dash pattern** from :data:`LINE_STYLES`, and
* a **marker** — shape and fill — from :data:`LINE_MARKERS`.

**All three advance on every trace**, so neighbouring traces differ in all
three and no two of the twelve the legend names look alike. An earlier draft
cycled the marker fastest and moved the shade only when the markers wrapped,
which names more traces before a pair repeats — and looked wrong the moment it
was drawn, because a realistic selection of nine traces came out in nine
identical greys. The extra period bought nothing a reader could see: the
on-screen legend names at most ``SCREEN_LEGEND_ENTRIES`` (12) traces
individually, so 12 is the number that has to be distinct.

The three counts are 5, 4 and 12, and the first two are chosen coprime with
each other and with 12 so no pair of cues ever locks in step — a shade and a
dash that advanced together would be one cue wearing two coats. Their least
common multiple is 60, and the marker alone is unique across the first 12, so
the triple is distinct for every trace the legend names and for four more
cycles after that.

The shade is the fast, approximate cue; the dash and the marker are the ones a
reader can rely on, and neither costs anything in contrast. That division is
why the ramp can span as widely as it does — the arithmetic is written out
beside the token, and the short version is that the two grounds fail at
opposite ends, so no shade is ever weak on both.

Both surfaces that draw these panels read it — the Line Graphs tab and the
Batch Export line figure, which render through the same
``lineplot.controller.render_line_series``.

The CDF panel takes the shade **and** the marker, spaced along the curve rather
than at every vertex: it is the one panel with no error bars and thousands of
points, so without a marker a grey CDF of ten wells would be ten curves nobody
could tell apart — the failure this whole module exists to avoid, arrived at by
omission.

Qt-free and matplotlib-free: it returns marker *tokens*, and the renderer is
what hands them to matplotlib.
"""

from __future__ import annotations

from dataclasses import dataclass

import theme

#: The two values ``PlotState.line_color_mode`` may hold. ``"plate"`` is the
#: default and reproduces the historical behaviour exactly.
LINE_COLOR_MODES = ("plate", "grey")

#: Marker shapes for the grey mode, in cycle order. Chosen to stay apart at the
#: 3–4 point markers these panels draw: a round, a square, a triangle, a
#: diamond, an inverted triangle and a plus. Deliberately no ``*`` or ``.`` —
#: the star loses its points and the dot is a small circle at this size, so
#: each would read as one of the shapes already in the list.
LINE_MARKER_SHAPES = ("o", "s", "^", "D", "v", "P")

#: The full cycle: every shape filled, then every shape open. Fill is a second
#: axis rather than a third set of shapes because an open marker of a shape
#: already in use is unmistakable next to its filled twin, while a seventh and
#: eighth shape would not be.
LINE_MARKERS = tuple(
    (shape, filled)
    for filled in (True, False)
    for shape in LINE_MARKER_SHAPES
)

#: Dash patterns for the grey mode, in cycle order. Four, which is coprime with
#: the five shades and divides the twelve markers — so the dash and the shade
#: never advance in lockstep, while the dash repeats four times across one
#: marker cycle rather than aliasing onto it.
#:
#: These are the patterns that stay apart at the 1.8–2 point lines these panels
#: draw. A denser dash (``(0, (3, 1))`` and the like) reads as solid at this
#: weight, and there is nothing to gain by adding one that does.
LINE_STYLES = ("-", "--", "-.", ":")


@dataclass(frozen=True)
class LineTraceStyle:
    """How one trace is drawn: its colour, and how its points are marked.

    ``marker`` of ``None`` means "keep whatever marker the panel already uses"
    — that is the plate mode, where the three aggregate panels distinguish
    themselves with a circle, a square and a triangle and the trace is
    identified by colour alone. In grey mode it is the trace that owns the
    marker, so the same shape appears on every panel: a reader matching a curve
    across the Value and Fraction panels is matching the marker, and a shape
    that changed between them would break exactly that.
    """

    color: str
    marker: str | None = None
    filled: bool = True
    #: The dash pattern, or ``None`` to keep whatever the panel already draws.
    #: ``None`` is the plate mode, where every trace is solid and the colour is
    #: the identity; a dash there would be a distinction standing for nothing.
    linestyle: str | None = None

    @property
    def face(self) -> str:
        """The marker's face colour — ``"none"`` for an open marker.

        matplotlib spells a hollow marker as a face of ``"none"`` with a real
        edge, so the edge stays :attr:`color` in both fills; an open marker
        with no edge is an invisible one.
        """
        return self.color if self.filled else "none"


def line_color_mode(plot) -> str:
    """The effective mode for *plot* (a ``PlotState``), defaulting to ``"plate"``.

    Anything unrecognised reads as ``"plate"``, so an unknown value degrades to
    the colours the app has always drawn rather than to a blank or arbitrary
    one — the same bargain ``bar_colors.bar_color_mode`` strikes, and for the
    same reason: a state object from an older build has no such field, and
    ``view_state`` validates the on-disk value against :data:`LINE_COLOR_MODES`
    before assigning, so an edited ``persistence.json`` is dropped rather than
    stored.
    """
    mode = str(getattr(plot, "line_color_mode", "plate") or "plate")
    return mode if mode in LINE_COLOR_MODES else "plate"


def grey_trace_style(index: int) -> LineTraceStyle:
    """The grey mode's style for the *index*-th trace of a plot.

    Shade, dash and marker all advance with the index, so neighbouring traces
    differ in all three and the triple is unique for the first
    ``len(LINE_MARKERS)`` — twelve — traces. That is the number the screen
    legend names individually, so it is the number that has to be distinct;
    past it both cycle, and the legend is already reporting how many it has
    stopped naming.

    The index is the trace's position in the drawn list, so it is stable for a
    given plot and changes when the user reorders — which is correct: the
    marker names *this figure's* first trace, and the legend beside it says
    which well that is.
    """
    ramp = tuple(theme.Colors.plot_grey_ramp)
    shape, filled = LINE_MARKERS[index % len(LINE_MARKERS)]
    shade = ramp[index % len(ramp)]
    dash = LINE_STYLES[index % len(LINE_STYLES)]
    return LineTraceStyle(color=shade, marker=shape, filled=filled,
                          linestyle=dash)


def trace_style_for_mode(mode: str, index: int,
                         plate_color: str) -> LineTraceStyle:
    """How the *index*-th trace is drawn under the named *mode*.

    In ``"plate"`` mode this is the trace's own plate-rank colour and no marker
    opinion, which is byte-for-byte what the renderer drew before the mode
    existed. Callers do not branch on the mode; they ask for a style and draw
    it, so a surface that forgets to check cannot silently keep the old
    behaviour while another honours the new one.

    Takes the mode string rather than the state object because the renderer
    has no state: ``render_line_series`` serves the Batch Export line figure as
    well as the tab, and only the caller knows which ctx to read. Anything
    unrecognised reads as ``"plate"``, so the two entry points cannot answer a
    junk value differently.
    """
    if str(mode or "plate") != "grey":
        return LineTraceStyle(color=plate_color)
    return grey_trace_style(index)


def trace_style(plot, index: int, plate_color: str) -> LineTraceStyle:
    """:func:`trace_style_for_mode` with the mode read off *plot*.

    The state-object spelling every other resolver in ``domain/`` uses. It
    delegates rather than repeating the branch, so there is one answer to "what
    does grey mode draw" no matter which door a caller comes in by.
    """
    return trace_style_for_mode(line_color_mode(plot), index, plate_color)
