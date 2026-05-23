"""Shared matplotlib axis styling.

Extracted from ``well_viewer.runtime_app`` so plot controllers and export
panels can apply the canonical axis look without importing the GUI monolith.

When the axes belongs to a figure inside a ``widgets.PlotCard``, the styling
honours the card's active ``plotTheme()`` — ``"publication"`` (white bg,
``theme.CPub`` ink — the canonical / exported state) or ``"screen"`` (the dark
token set, a per-card live preview). When the axes is on a bare figure (no
card), falls back to the legacy ``ui.theme`` constants (publication-style).
"""

from __future__ import annotations

from ui.theme import PLOT_BG, PLOT_GRD, PLOT_SPN, PLOT_TXT, TXT_PRI


def tokens_for(ax) -> tuple:
    """Return ``(bg, title_fg, muted_fg, grid, spine)`` for *ax*'s figure.

    Resolution order:
      1. the active ``PlotCard.plotTheme()`` via ``ax.figure._plot_card`` (the
         back-ref ``PlotCard.__init__`` sets);
      2. a fallback that infers ``"screen"`` vs ``"publication"`` from the
         figure's *current* facecolor (``setPlotTheme`` sets it to the right
         value before emitting, so this works even if the back-ref is missing);
      3. the legacy ``ui.theme`` constants (publication-style) for bare figures.
    """
    fig = getattr(ax, "figure", None)
    if fig is None:
        return PLOT_BG, TXT_PRI, PLOT_TXT, PLOT_GRD, PLOT_SPN

    mode = None
    card = getattr(fig, "_plot_card", None)
    if card is not None:
        try:
            mode = card.plotTheme()
        except Exception:
            mode = None
    if mode is None:
        # Infer from the figure facecolor (set by PlotCard.setPlotTheme before
        # plotThemeChanged fires, so it tracks the user's most recent toggle).
        try:
            rgba = fig.get_facecolor()
            brightness = sum(float(c) for c in rgba[:3])
            mode = "publication" if brightness > 1.5 else "screen"
        except Exception:
            mode = "publication"
    try:
        from widgets.plot_card import plot_tokens
        return plot_tokens(mode)
    except Exception:
        return PLOT_BG, TXT_PRI, PLOT_TXT, PLOT_GRD, PLOT_SPN


# Internal alias (was the original name).
_tokens_for = tokens_for


def apply_ax_style(ax, title: str, ylabel: str) -> None:
    """Apply the standard plot style to *ax*."""
    bg, title_fg, muted_fg, grid, spine = tokens_for(ax)
    ax.set_facecolor(bg)
    fig = getattr(ax, "figure", None)
    if fig is not None:
        fig.set_facecolor(bg)
    for sp in ax.spines.values():
        sp.set_color(spine)
        sp.set_linewidth(0.8)
    ax.tick_params(colors=muted_fg, labelsize=8)
    ax.xaxis.label.set_color(muted_fg)
    ax.yaxis.label.set_color(muted_fg)
    ax.set_title(title, color=title_fg, fontsize=9, fontweight="bold", pad=6)
    ax.set_ylabel(ylabel, fontsize=8, labelpad=5, color=muted_fg)
    ax.grid(True, color=grid, linewidth=0.7, linestyle="-")
