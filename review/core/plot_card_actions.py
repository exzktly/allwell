"""Plot-card export actions (copy-to-clipboard / save-figure) — ``ctx``-based.

These two toolbar actions used to be closures stamped on the ``ReviewApp``
god-object (``app._copy_active_card_as_svg`` / ``app._save_active_card_figure``),
and every plot feature's builder reached back through the god-object to wire its
Copy / Save buttons to them (A8). They now live here as plain functions taking
the ``ViewerContext``: the active plot card is resolved through the widget
registry (``ctx.widgets``), and the only remaining god-object touches are the
two genuinely app/window-level UI services — the status bar (``_set_status``)
and the Save-As dialog (``_save_matplotlib_fig``) — kept as explicit, named
escape hatches in one place instead of scattered across six builders.

Qt-free at import time; the clipboard helper is imported lazily.
"""

from __future__ import annotations

from contextlib import contextmanager

from core.safe import swallowed

#: Cards whose on-screen figure is drawn with deliberate shortcuts — a sampled
#: CDF curve, a legend naming only the first few traces — because a plate-wide
#: selection is otherwise illegible and slow to paint. Capturing one of these
#: has to repaint it at full fidelity first, because the Export menu saves the
#: very figure object on screen rather than re-rendering onto its own axes the
#: way the batch exporter does.
_BUDGETED_CARDS = frozenset({"_line_card"})


@contextmanager
def _captured_at_full_fidelity(ctx, card_attr: str | None):
    """Repaint *card_attr* without its screen budgets for the duration.

    A figure saved at 300 dpi, copied into a vector editor, or filed in the
    gallery is read at a resolution where a thinned curve and an abbreviated
    legend both matter — and a degraded artifact that looks exactly like a
    complete one is the failure this whole path exists to avoid. The flag lives
    on ``PlotState`` rather than being threaded through the renderer's
    arguments because the capture happens two layers above the call that draws.

    Restored in a ``finally`` and repainted back, so an export that raises
    cannot leave the tab showing the slow figure for the rest of the session.
    Cards with no budgets skip both repaints entirely.
    """
    if card_attr not in _BUDGETED_CARDS:
        yield
        return
    from review.core.plot_orchestrator import redraw_app

    plot = ctx.state.plot
    saved = getattr(plot, "export_full_fidelity", False)
    plot.export_full_fidelity = True
    try:
        redraw_app(ctx)
        yield
    finally:
        plot.export_full_fidelity = saved
        with swallowed_repaint():
            redraw_app(ctx)


@contextmanager
def swallowed_repaint():
    """A failed restore repaint must not mask the export's own outcome."""
    try:
        yield
    except Exception:
        swallowed()

# (widget-registry name, default Save-As filename) for each plot card, in
# visible-priority order — the first *visible* card wins.
#
# This is the single roster of plot cards in the pane. It is a hand-written
# allowlist rather than something derived from the feature registry, and
# ARCHITECTURE §5.3 records what that costs: a new plot tab missing from it
# silently loses the global Copy / Save actions, which is exactly what shipped
# for Spatial and Multivariate. It used to be written out twice — the shell's
# Screen/Publication toggle carried its own copy of the same eight names — so
# the roster now has one home and `allwell.py` imports `PLOT_CARD_ATTRS` from
# here.
_CARDS = (
    ("_line_card", "lines.png"),
    ("_bar_card", "bars.png"),
    ("_scatter_card", "scatter.png"),
    ("_scatter_agg_card", "scatter_agg.png"),
    ("_distribution_card", "distribution.png"),
    ("_heatmap_card", "heatmap.png"),
    ("_sp_card", "spatial.png"),
    ("_mv_card", "multivariate.png"),
    ("_response_card", "response.png"),
)

#: Widget-registry names of every plot card, for callers that act on all of
#: them rather than on the active one (the shell's Screen/Publication toggle).
PLOT_CARD_ATTRS: tuple[str, ...] = tuple(attr for attr, _ in _CARDS)


def _named_card(ctx, card_attr: str):
    """Return ``(card, default_filename)`` for the card registered as *card_attr*."""
    for attr, default in _CARDS:
        if attr == card_attr:
            return ctx.widgets.get_or_none(attr), default
    return ctx.widgets.get_or_none(card_attr), None


def _active_card(ctx, card_attr: str | None = None):
    """Return ``(card, default_filename)`` for the card to act on.

    With *card_attr* the answer is that card, whatever else is on screen. That
    is what a tab-local Export menu wants and it is the only correct answer for
    one: in Split View two cards are visible at once, so "first visible" gave
    Bar Plots' own "Copy SVG" the line figure. ``save_card_to_gallery`` had
    been fixed to resolve its card by name; its two siblings in the same menu
    had not, and this file's own docstring described the hazard while fixing
    one of the three.

    Without *card_attr* it falls back to the first visible card, which is right
    for a single global toolbar action that belongs to no tab. Returns
    ``(None, None)`` when nothing resolves.
    """
    if card_attr:
        card, default = _named_card(ctx, card_attr)
        return (card, default) if card is not None else (None, None)
    for attr, default in _CARDS:
        card = ctx.widgets.get_or_none(attr)
        if card is None or not card.isVisible():
            continue
        return card, default
    return None, None


def copy_active_card_as_svg(ctx, card_attr: str | None = None) -> None:
    """Copy a card's figure to the clipboard, preferring vector.

    *card_attr* names the card; every tab-local Export menu passes its own, so
    the item copies that tab's figure even when another card is also visible.

    On macOS the figure is written natively to ``NSPasteboard`` so Illustrator
    pastes true editable vector; elsewhere a Qt clipboard payload is used (see
    ``copy_figure_to_clipboard``). The figure is captured in publication mode.
    """
    card, _ = _active_card(ctx, card_attr)
    if card is None:
        return
    fig = getattr(card, "_figure", None) or getattr(card, "figure", None)
    canvas = getattr(card, "_canvas", None)
    if fig is None and canvas is not None:
        fig = getattr(canvas, "figure", None)
    if fig is None:
        return
    prev_theme = getattr(card, "_plot_theme", "screen")
    with _captured_at_full_fidelity(ctx, card_attr):
        try:
            if hasattr(card, "setPlotTheme") and prev_theme != "publication":
                card.setPlotTheme("publication")
            # A build without the export-style library says so rather than
            # reporting a copy that never happened.
            status = ctx.features.query(
                "export_style.copy_figure", fig,
                default="Copying a figure is not available in this build.")
        finally:
            if (hasattr(card, "setPlotTheme")
                    and getattr(card, "_plot_theme", "screen") != prev_theme):
                try:
                    card.setPlotTheme(prev_theme)
                except Exception:
                    swallowed()  # was: pass (silent except)
    # Status bar is a genuine app/window-level service — via the named accessor.
    ctx.notify.set_status(status)


def save_active_card_figure(ctx, card_attr: str | None = None) -> None:
    """Open a Save-As dialog for a plot card's figure.

    *card_attr* names the card — see :func:`_active_card`.

    Uses the app's ``_save_matplotlib_fig`` helper so every renderer honours the
    same file-type filter and the active dataset directory as the starting
    location.
    """
    card, default = _active_card(ctx, card_attr)
    if card is None:
        return
    fig = getattr(card, "_figure", None) or getattr(card, "figure", None)
    if fig is None:
        return
    # Save-As dialog is a genuine app/window-level service — via the named accessor.
    with _captured_at_full_fidelity(ctx, card_attr):
        try:
            ctx.figures.save(fig, default)
        except Exception:
            swallowed()  # was: pass (silent except)


def save_card_to_gallery(ctx, card_attr: str) -> None:
    """Trigger *card_attr*'s whole-figure Saved-Plots capture directly.

    The same rule as its two siblings, which now also take a ``card_attr``: an
    Export dropdown's items are tab-local and must capture *that* tab's figure
    even when another tab's card is visible too (Split View).
    """
    card = ctx.widgets.get_or_none(card_attr)
    if card is None or not hasattr(card, "saveFigureToGallery"):
        return
    with _captured_at_full_fidelity(ctx, card_attr):
        card.saveFigureToGallery()
