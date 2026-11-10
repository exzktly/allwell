"""A plot whose redraw failed says so on the plot, instead of keeping the drawing from before.

A redraw that raises — most often a ``MemoryError`` on a machine that has run short — used to be caught, logged and
reported on the status line, and the figure was left as it was: the previous drawing, made under settings the
controls no longer show, on a canvas that looked exactly as it does after a redraw that worked. The status line is
one slot that the next message overwrites, so within a second nothing on screen said the plot was stale. That is the
silent substitution CLAUDE.md §7 forbids: the software knew the figure did not match and did not say so where the
figure is read.

:func:`show_redraw_failed` covers every figure on the failed tab's page with one sentence naming the failure and
saying the plot is not current, laid over the figure rather than replacing it; the first draw after the figure's own
artists change lifts it, so the next redraw that works shows through and nothing else has to clear it. It is called
by the places a redraw failure is caught for a tab it can name:

* ``review.domain.fold_change_scopes._call_redraw`` — every registered redraw scope (each plot leaf, by its
  ``tab_names``);
* ``review.core.plot_orchestrator.redraw`` — Line Graphs, Distribution and Heat Map in the shared fan-out;
* ``review.features.nanowell.controller.redraw`` — the Nanowells page, which most callers reach inside a ``swallow``;
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
    """Cover *canvas*'s figure with *sentence* until the figure is drawn afresh.

    An opaque Axes over the whole figure, not ``fig.clear()``: Heat Map and Response Curves keep the Axes they
    were built with in the widget registry and draw into it on every redraw, and a cleared figure no longer holds
    it — every later redraw would then draw into an Axes nothing shows, and the tab would stay on this sentence
    for the rest of the session. The cover is lifted by the first draw after the figure's own artists change
    (:func:`_lift_if_redrawn`), which is what any redraw that worked does, whether it clears the figure, clears an
    Axes or builds new ones.
    """
    from review.core.plot_style import follows_theme, tokens_for

    fig = canvas.figure
    _lift(canvas)
    cover = fig.add_axes((0.0, 0.0, 1.0, 1.0), label=FAILED_GID)
    cover.set_zorder(1e6)
    bg, fg, _muted, _grid, _spine = tokens_for(cover)
    cover.set_facecolor(bg)
    cover.set_xticks([])
    cover.set_yticks([])
    for spine in cover.spines.values():
        spine.set_visible(False)
    follows_theme(cover.text(0.5, 0.5, sentence, transform=cover.transAxes, ha="center", va="center",
                             fontsize="medium", wrap=True, color=fg, gid=FAILED_GID))
    seen = _signature(fig, cover)
    cid = canvas.mpl_connect("draw_event", lambda _event: _lift_if_redrawn(canvas, cover, seen))
    _COVERS[id(fig)] = (cover, cid)
    canvas.draw_idle()


#: ``id(figure) -> (cover Axes, draw_event connection)`` for each figure currently covered.
_COVERS: dict[int, tuple] = {}


def _signature(fig, cover) -> tuple:
    """Which artists the figure holds, the cover aside — what any redraw replaces."""
    return (tuple((id(ax), tuple(id(c) for c in ax.get_children())) for ax in fig.axes if ax is not cover),
            tuple(id(t) for t in fig.texts))


def _lift_if_redrawn(canvas, cover, seen) -> None:
    fig = canvas.figure
    if cover not in fig.axes:                     # cleared by a redraw that rebuilt the figure
        _lift(canvas)
        return
    if _signature(fig, cover) != seen:            # drawn afresh underneath: show it
        _lift(canvas)
        canvas.draw_idle()


def _lift(canvas) -> None:
    """Take *canvas*'s cover away, if it has one."""
    fig = canvas.figure
    entry = _COVERS.pop(id(fig), None)
    if entry is None:
        return
    cover, cid = entry
    canvas.mpl_disconnect(cid)
    if cover in fig.axes:
        cover.remove()
