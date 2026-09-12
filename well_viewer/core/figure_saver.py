"""Figure-output services extracted from ``WellViewerApp`` (A5).

``FigureService`` bundles the two figure-output surfaces the app exposes to
feature code:

- ``save(fig, default_name)`` — the Save-As dialog that writes a Matplotlib
  figure to disk, honouring the shared file-type filter and active dataset dir.
  Already ctx-native: it drives the ``plot_orchestrator.save_matplotlib_fig``
  orchestrator directly.
- ``open_style_panel(plot_key)`` — toggles the floating Export-Style sidebar for
  a plot tab's figure. Genuinely window-widget-coupled (it reads the tab's
  figure/canvas handles off the main window), so it forwards to ``ctx.window``.

The context holds this as the ``ctx.figures`` collaborator, replacing the
``ctx.save_figure_dialog`` / ``ctx.open_export_style_panel`` frozen accessors so
``ViewerContext`` no longer needs a method per surface.
"""

from __future__ import annotations


class FigureService:
    """Figure Save-As + Export-Style panel, held on the context as ``ctx.figures``."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def save(self, fig, default_name: str) -> None:
        """Open the Save-As dialog for *fig*, honouring the shared file-type
        filter and the active dataset directory."""
        from ui import theme
        from well_viewer.core.plot_orchestrator import save_matplotlib_fig
        save_matplotlib_fig(self._ctx, fig, default_name, plot_bg=theme.CPub.bg)

    def open_style_panel(self, plot_key: str) -> None:
        """Toggle the figure-properties (export-style) side panel for *plot_key*."""
        self._ctx.features.call(
            "export_style.open_panel", self._ctx.window, plot_key)
