"""Plot-card export actions (copy-to-clipboard / save-figure) — ``ctx``-based.

These two toolbar actions used to be closures stamped on the ``WellViewerApp``
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

from well_viewer.core.safe import swallowed

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
)

#: Widget-registry names of every plot card, for callers that act on all of
#: them rather than on the active one (the shell's Screen/Publication toggle).
PLOT_CARD_ATTRS: tuple[str, ...] = tuple(attr for attr, _ in _CARDS)


def _active_card(ctx):
    """Return ``(card, default_filename)`` for the first visible plot card, or
    ``(None, None)`` when none is visible. Cards resolve through the registry."""
    for attr, default in _CARDS:
        card = ctx.widgets.get_or_none(attr)
        if card is None or not card.isVisible():
            continue
        return card, default
    return None, None


def copy_active_card_as_svg(ctx) -> None:
    """Copy the active card's figure to the clipboard, preferring vector.

    On macOS the figure is written natively to ``NSPasteboard`` so Illustrator
    pastes true editable vector; elsewhere a Qt clipboard payload is used (see
    ``copy_figure_to_clipboard``). The figure is captured in publication mode.
    """
    from well_viewer.features.export.style import copy_figure_to_clipboard

    card, _ = _active_card(ctx)
    if card is None:
        return
    fig = getattr(card, "_figure", None) or getattr(card, "figure", None)
    canvas = getattr(card, "_canvas", None)
    if fig is None and canvas is not None:
        fig = getattr(canvas, "figure", None)
    if fig is None:
        return
    prev_theme = getattr(card, "_plot_theme", "screen")
    try:
        if hasattr(card, "setPlotTheme") and prev_theme != "publication":
            card.setPlotTheme("publication")
        status = copy_figure_to_clipboard(fig)
    finally:
        if (hasattr(card, "setPlotTheme")
                and getattr(card, "_plot_theme", "screen") != prev_theme):
            try:
                card.setPlotTheme(prev_theme)
            except Exception:
                swallowed()  # was: pass (silent except)
    # Status bar is a genuine app/window-level service — via the named accessor.
    ctx.notify.set_status(status)


def save_active_card_figure(ctx) -> None:
    """Open a Save-As dialog for the active visible plot card's figure.

    Uses the app's ``_save_matplotlib_fig`` helper so every renderer honours the
    same file-type filter and the active dataset directory as the starting
    location.
    """
    card, default = _active_card(ctx)
    if card is None:
        return
    fig = getattr(card, "_figure", None) or getattr(card, "figure", None)
    if fig is None:
        return
    # Save-As dialog is a genuine app/window-level service — via the named accessor.
    try:
        ctx.figures.save(fig, default)
    except Exception:
        swallowed()  # was: pass (silent except)


def save_card_to_gallery(ctx, card_attr: str) -> None:
    """Trigger *card_attr*'s whole-figure Saved-Plots capture directly.

    Unlike :func:`copy_active_card_as_svg`/:func:`save_active_card_figure`,
    which resolve the "first visible" card via :func:`_active_card` (fine for
    a single global toolbar action), an Export dropdown's "Save to Saved
    Plots" item is tab-local — it must always capture *that* tab's own
    figure, even if another tab's card happens to also be visible (Split
    View), so it resolves the named card directly instead.
    """
    card = ctx.widgets.get_or_none(card_attr)
    if card is not None and hasattr(card, "saveFigureToGallery"):
        card.saveFigureToGallery()
