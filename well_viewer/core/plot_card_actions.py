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
_CARDS = (
    ("_line_card", "lines.png"),
    ("_bar_card", "bars.png"),
    ("_scatter_card", "scatter.png"),
    ("_scatter_agg_card", "scatter_agg.png"),
    ("_distribution_card", "distribution.png"),
    ("_heatmap_card", "heatmap.png"),
)


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
    ctx.set_status(status)


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
        ctx.save_figure_dialog(fig, default)
    except Exception:
        swallowed()  # was: pass (silent except)
