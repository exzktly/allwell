"""A plot whose redraw failed says so on the plot, instead of keeping the drawing from before.

A redraw that raises — most often a ``MemoryError`` on a machine that has run short — used to be caught, logged and
reported on the status line, and the figure was left as it was: the previous drawing, made under settings the
controls no longer show, on a canvas that looked exactly as it does after a redraw that worked. The status line is
one slot that the next message overwrites, so within a second nothing on screen said the plot was stale. That is the
silent substitution CLAUDE.md §7 forbids: the software knew the figure did not match and did not say so where the
figure is read.

:func:`show_redraw_failed` replaces every figure on the failed tab's page with one sentence naming the failure and
saying the plot is not current. The next redraw that works draws over it, so nothing has to clear it. It is called
by the places a redraw failure is caught for a tab it can name:

* ``review.domain.fold_change_scopes._call_redraw`` — every registered redraw scope (each plot leaf, by its
  ``tab_names``);
* ``review.core.plot_orchestrator.redraw`` — Distribution and Heat Map in the shared fan-out;
* ``review.core.selection_controller`` — Scatter Plot after a group edit;
* ``review.core.background_read`` — a view whose wells were read off the window's thread, when the drawing of the
  result fails (the caller names its tab).

The pages are found through ``_tab_frames`` (``core/centre_view.build_centre``), the title → page mapping the centre
view builds every page into, so a canvas is found by where it is shown rather than by a roster of canvas names.
Every step is guarded: this runs after something has already gone wrong, possibly for want of memory.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

_logger = logging.getLogger("review")

#: The artist id of the sentence, so a test — or a later draw that wants to know — can find it.
FAILED_GID = "plot-redraw-failed"


def failure_sentence(exc: BaseException) -> str:
    """What the plot says: that it is not current, why, and what to do."""
    if isinstance(exc, MemoryError):
        why = "the computer ran out of memory"
    else:
        why = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return (f"This plot could not be drawn ({why}).\n"
            "What was drawn before is not shown, because it no longer matches the settings.\n"
            "Change a setting or come back to this tab to try again.")


def show_redraw_failed(ctx, titles: Iterable[str], exc: BaseException) -> int:
    """Replace every figure on the pages titled *titles* with :func:`failure_sentence`; return how many."""
    frames = _tab_frames(ctx)
    shown = 0
    for title in dict.fromkeys(titles or ()):
        frame = frames.get(title)
        if frame is None:
            continue
        for canvas in _canvases(frame):
            try:
                _blank(canvas, failure_sentence(exc))
                shown += 1
            except Exception:
                _logger.exception("could not mark the %s plot as not drawn", title)
    return shown


def _tab_frames(ctx) -> dict:
    widgets = getattr(ctx, "widgets", None)
    get = getattr(widgets, "get_or_none", None)
    frames = get("_tab_frames") if callable(get) else None
    return frames if isinstance(frames, dict) else {}


def _canvases(frame) -> list:
    try:
        import shiboken6
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    except ImportError:
        return []
    if not shiboken6.isValid(frame):
        return []
    return [c for c in frame.findChildren(FigureCanvasQTAgg) if shiboken6.isValid(c)]


def _blank(canvas, sentence: str) -> None:
    from review.core.plot_style import follows_theme, tokens_for

    fig = canvas.figure
    fig.clear()
    ax = fig.add_subplot(111)
    _bg, fg, _muted, _grid, _spine = tokens_for(ax)
    follows_theme(ax.text(0.5, 0.5, sentence, transform=ax.transAxes, ha="center", va="center",
                          fontsize="medium", wrap=True, color=fg, gid=FAILED_GID))
    ax.set_axis_off()
    canvas.draw_idle()
