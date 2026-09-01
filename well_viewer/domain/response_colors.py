"""Response-curve colour mode — the series palette, or greys told apart twice.

The third of the tab colour-mode modules, beside ``domain/bar_colors.py`` and
``domain/line_colors.py``, and like those two it exists because *what grey
means* is not the same on every plot.

A bar is named by the tick label under it, so the Bar tab's grey mode collapses
every column onto one ink and loses nothing. A line has no such label, so the
Line tab's grey mode gives each trace a shade **and** a dash **and** a marker.
A response curve is a line — but this tab has already spent the dash: it draws
**colour for the series and the dash for the timepoint**, which is what lets a
reader see that two curves are the same condition at different moments.

So grey mode here takes the two cues the tab has free — the **shade** and the
**marker** (shape and fill) — and leaves the dash where it is. The timepoint
goes on being the dash in both modes, which means a figure keeps meaning the
same thing when the mode is switched; a grey mode that reassigned the dash
would silently re-encode the other half of the figure.

The shade and marker cycles are :mod:`well_viewer.domain.line_colors`'s own,
imported rather than re-derived. Two modules choosing "the greys" independently
is two answers to one question, and the one that is not being looked at is the
one that drifts.

Qt-free and matplotlib-free: it returns marker *tokens*, and the renderer hands
them to matplotlib.
"""

from __future__ import annotations

from dataclasses import replace

from well_viewer.domain.line_colors import LineTraceStyle, grey_trace_style

#: The two values ``PlotState.response_color_mode`` may hold.
#:
#: ``"colour"`` rather than the Bar and Line tabs' ``"plate"``, because it is
#: not the plate: a response curve is one value of a *sample variable*, not one
#: well, so its default colour comes from the figure's palette in the order the
#: user arranged that variable's values. Naming the mode "Plate" here would be
#: a label that reads as a claim about where the colour came from, and the
#: claim would be false.
RESPONSE_COLOR_MODES = ("colour", "grey")

#: What an unset or unrecognised mode reads as.
DEFAULT_RESPONSE_COLOR_MODE = "colour"


def response_color_mode(plot) -> str:
    """The effective mode for *plot* (a ``PlotState``), defaulting to ``"colour"``.

    Anything unrecognised reads as the default, so an older state object with no
    such field, or a hand-edited ``persistence.json``, degrades to the colours
    the tab has always drawn rather than to a blank or arbitrary one — the same
    bargain the bar and line resolvers strike.
    """
    mode = str(getattr(plot, "response_color_mode", DEFAULT_RESPONSE_COLOR_MODE)
               or DEFAULT_RESPONSE_COLOR_MODE)
    return mode if mode in RESPONSE_COLOR_MODES else DEFAULT_RESPONSE_COLOR_MODE


def series_style_for_mode(mode: str, index: int,
                          palette_color: str | None) -> LineTraceStyle:
    """How the *index*-th series is drawn under the named *mode*.

    In ``"colour"`` mode this is *palette_color* and no marker or dash opinion,
    which is byte-for-byte what the renderer drew before the mode existed —
    ``None`` included, which is the tab's "one series, so nothing to tell
    apart" case and leaves matplotlib's own cycle alone.

    In ``"grey"`` mode it is the Line tab's shade and marker for that index,
    **with the dash dropped**: on this tab the dash is the timepoint's, and a
    style that carried one would be claiming an encoding it does not own.

    Today's only caller reads the colour and the marker and takes the dash from
    the timepoint, so a leaked dash could not reach the axes through it — this
    is the style object telling the truth about itself rather than a guard
    standing between a bug and the screen. That distinction is worth stating,
    because a reader who takes it for the latter will not add the assertion the
    day a second caller starts reading the field.

    Takes the mode string rather than the state object because the renderer has
    no state; anything unrecognised reads as the default, so no caller can
    answer a junk value differently from another.
    """
    if str(mode or DEFAULT_RESPONSE_COLOR_MODE) != "grey":
        return LineTraceStyle(color=palette_color or "")
    return replace(grey_trace_style(index), linestyle=None)


def series_style(plot, index: int, palette_color: str | None) -> LineTraceStyle:
    """:func:`series_style_for_mode` with the mode read off *plot*.

    The state-object spelling the other two resolvers offer, so there is one
    answer to "what does grey mode draw here" whichever door a caller uses.
    """
    return series_style_for_mode(response_color_mode(plot), index, palette_color)
