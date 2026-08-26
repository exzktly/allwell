"""Bar-plot colour mode — plate-rank colours, or one neutral grey.

The Bar plot colours each column by the plate-picker colour of the well or
replicate group behind it: the plate *is* the legend, and a reader who knows
which well is which colour can read the figure without the tick labels. That
is the right default, and it is wrong for a figure whose columns are already
named on the axis and whose colours would then be decoration competing with
the data — a paper figure, most often.

So the Properties sidebar offers two modes, and this module is the single
place that turns the mode into a colour. Both halves of the Bar plot read it:
the bar renderer (``features/barplot/controller.render_bar_items``) and the
per-cell renderers (violin / beeswarm / the events panel beside them), plus
the Batch Export bar panel, which draws the same figures for a whole plate.

Deliberately *not* applied to the placeholder columns a missing measurement
draws, nor to the error bars: those two say "this value does not exist" and
"this is the spread", and a colour mode is a statement about the data ink
only. Collapsing them into the same grey would make an absent bar and a real
one look alike, which is the one thing the plot must never do.
"""

from __future__ import annotations

from collections.abc import Sequence

import theme

#: The two values ``PlotState.bar_color_mode`` may hold. ``"plate"`` is the
#: default and reproduces the historical behaviour exactly.
BAR_COLOR_MODES = ("plate", "grey")


def bar_color_mode(plot) -> str:
    """The effective mode for *plot* (a ``PlotState``), defaulting to ``"plate"``.

    Anything unrecognised — a hand-edited persistence file, an older state
    object without the field — reads as ``"plate"``, so an unknown value
    degrades to the colours the app has always drawn rather than to a blank
    or arbitrary one.
    """
    mode = str(getattr(plot, "bar_color_mode", "plate") or "plate")
    return mode if mode in BAR_COLOR_MODES else "plate"


def mono_bar_color(plot) -> str | None:
    """The single ink every bar takes in ``"grey"`` mode, else ``None``.

    ``None`` means "leave the per-column colours alone" — callers treat it as
    the plate-colour mode rather than as a failure, so a caller that forgets
    to check draws the default rather than an invisible figure.
    """
    return theme.Colors.plot_mono if bar_color_mode(plot) == "grey" else None


def apply_bar_color_mode(plot, colors: Sequence[str]) -> list[str]:
    """*colors* with every entry replaced by the mono ink in ``"grey"`` mode.

    A new list either way, and one of the same length, so a caller can zip it
    against the well / label sequences it was built beside without checking
    which mode is active.
    """
    mono = mono_bar_color(plot)
    if mono is None:
        return list(colors)
    return [mono] * len(colors)
