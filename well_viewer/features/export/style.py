"""Export-style profile management + figure export pipeline.

The right-side sidebar widget that hosts the editor lives in
``well_viewer.views.export_style_sidebar_view`` so the GUI portion is
swappable. This module owns profile lookups (``EXPORT_PROFILES``) and
``launch_export_editor``; the figure render / clipboard-write pipeline moved to
``widgets.fig_clipboard`` (re-exported below).

The neutral figure-styling engine (``DEFAULT_EXPORT_STYLE_PREFS``,
``apply_export_style_prefs``, ``apply_export_style_to_current``) moved to
``well_viewer.core.plot_style`` (T2) so every plot feature can import it
*downward* instead of reaching sideways into this export feature — that
sideways edge had formed a scatter↔export import cycle. The names are
re-exported below so existing
``from well_viewer.features.export.style import …`` call sites keep working.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import (  # noqa: F401  (re-export for compat)
    DEFAULT_EXPORT_STYLE_PREFS,
    _ensure_export_style_prefs,
    apply_export_style_prefs,
    apply_export_style_to_current,
)


# The figure→clipboard pipeline moved to ``widgets/fig_clipboard.py`` (T3) so the
# ``widgets/`` layer (PlotCard's per-axis "copy this panel") can share it without a
# widget importing ``well_viewer.features.*`` (guardrail G8). Re-exported here so
# existing ``from well_viewer.features.export.style import copy_figure_to_clipboard``
# call sites (e.g. core/centre_view.py) keep working — same pattern as the
# plot_style re-exports above.
from widgets.fig_clipboard import (  # noqa: F401 (re-export for back-compat)
    _macos_pasteboard_write,
    _png_to_tiff,
    _render_figure,
    axis_tight_bbox_inches,
    copy_figure_to_clipboard,
)


EXPORT_PROFILES = {
    "Custom": {},
    "Illustrator SVG": {"format": "svg", "layout_tight": True},
    "High-res PNG": {"format": "png", "layout_tight": True},
    "Print PDF": {"format": "pdf", "layout_tight": True},
    "Helvetica 22": {
        "axis_label_size": 22,
        "tick_label_size": 22,
        "title_size": 22,
        "legend_font_size": 22,
    },
}


def _ensure_custom_export_profiles(win) -> dict:
    if win._ctx.runtime.export.custom_profiles is None:
        win._ctx.runtime.export.custom_profiles = {}
    return win._ctx.runtime.export.custom_profiles


def _get_all_profile_names(win) -> list[str]:
    custom = _ensure_custom_export_profiles(win)
    return [*EXPORT_PROFILES.keys(), *custom.keys()]


def ensure_custom_export_profiles_ctx(ctx) -> dict:
    """ctx-native form of :func:`_ensure_custom_export_profiles`.

    Reads/creates the session custom-profile dict through
    ``ctx.runtime.export.custom_profiles`` (backed by the same
    ``app._export_style_custom_profiles`` attribute) so feature panels list and
    apply export profiles without naming the god-object.
    """
    profiles = ctx.runtime.export.custom_profiles
    if profiles is None:
        profiles = {}
        ctx.runtime.export.custom_profiles = profiles
    return profiles


def get_all_profile_names_ctx(ctx) -> list[str]:
    """ctx-native form of :func:`_get_all_profile_names`."""
    custom = ensure_custom_export_profiles_ctx(ctx)
    return [*EXPORT_PROFILES.keys(), *custom.keys()]


class _ExportEditorSession:
    """Handed back to the caller as the open editor's handle.

    ``sidebar`` is written and never read inside this module; it is the
    session's whole payload, for a caller that wants the widget.
    """

    def __init__(self, sidebar) -> None:
        self.sidebar = sidebar


def _resolve_export_dock(win, fig) -> QWidget | None:
    """Return the pre-allocated right-side dock widget for a given figure, or None."""
    mapping = (
        ("_line_fig", "_line_export_dock"),
        ("_bar_fig", "_bar_export_dock"),
        ("_scatter_fig", "_scatter_export_dock"),
        ("_scatter_agg_fig", "_scatter_agg_export_dock"),
        ("_heatmap_fig", "_heatmap_export_dock"),
        ("_distribution_fig", "_distribution_export_dock"),
        ("_sp_fig", "_spatial_export_dock"),
        ("_mv_fig", "_mv_export_dock"),
        ("_response_fig", "_response_export_dock"),
    )
    for fig_attr, dock_attr in mapping:
        if getattr(win, fig_attr, None) is fig:
            return getattr(win, dock_attr, None)
    return None


def launch_export_editor(win, fig, default_name: str, *, plot_bg: str = "",
                          canvas=None) -> _ExportEditorSession | None:
    try:
        # Lazy import: the view module imports symbols defined above, so a
        # top-level import would create a circular dependency.
        from well_viewer.features.export.sidebar_view import ExportStyleSidebar

        dock = _resolve_export_dock(win, fig)
        parent = dock if dock is not None else (canvas.parent() if canvas is not None else win)
        if not hasattr(win, "_export_style_sidebars"):
            # Register the sidebar map in the widget registry (stamps
            # ``win._export_style_sidebars`` too) so the ctx-native redraw
            # orchestrator reaches it via ``ctx.widgets`` instead of the
            # god-object. Fall back to a plain attribute if no ctx is wired
            # (bare test doubles).
            reg = getattr(getattr(win, "_ctx", None), "widgets", None)
            if reg is not None:
                reg.register("_export_style_sidebars", {})
            else:
                win._export_style_sidebars = {}
        key = id(fig)
        sb = win._export_style_sidebars.get(key)
        if sb is None:
            sb = ExportStyleSidebar(win, parent, fig, canvas, default_name=default_name)
            win._export_style_sidebars[key] = sb

            if dock is not None and dock.layout() is not None:
                dock.layout().addWidget(sb)
                # Pin the dock container's width to the SAME constant the
                # sidebar uses for its own setFixedWidth — ``sizeHint()``
                # can lag the fixed-width on the first show (which left
                # the container narrower than its child, clipping content
                # against the window edge). Importing the constant here
                # keeps the two sides in lock-step.
                from well_viewer.features.export.sidebar_view import (
                    EXPORT_STYLE_PANEL_WIDTH as _PANEL_W,
                )
                host = getattr(dock, "_dock_host", None)
                if host is not None and hasattr(host, "set_overlay_dock"):
                    host.set_overlay_dock(dock, _PANEL_W)
            elif canvas is not None:
                canvas_parent = canvas.parentWidget()
                if canvas_parent is not None:
                    parent_layout = canvas_parent.layout()
                    if parent_layout is not None and hasattr(parent_layout, "addWidget"):
                        parent_layout.addWidget(sb)

        if dock is not None:
            from well_viewer.features.export.sidebar_view import (
                EXPORT_STYLE_PANEL_WIDTH as _PANEL_W,
            )
            # Show the sidebar as the dock's sole visible panel — this also
            # hides the Scatter cell viewer if it was sharing this dock, so the
            # two slide-outs stay mutually exclusive (one at a time).
            from well_viewer.core.ui_helpers import mount_widget_in_plot_dock
            mount_widget_in_plot_dock(dock, sb, _PANEL_W)
        sb.show()
        sb.raise_()
        # Apply the (already-persisted) prefs to the figure once. Use the
        # direct entry point rather than ``sb._on_fields_changed()`` — the
        # latter also copies every widget value back onto ``_export_style_prefs``
        # (a redundant no-op right after the widgets were initialised from
        # those same prefs) and is wired to every widget's change signal, so
        # routing the open path through it risks extra restyle+redraw passes
        # on a heavy (tall, 3-panel) bar figure.
        try:
            apply_export_style_to_current(win._ctx, fig, canvas)
        except Exception:
            swallowed()  # was: pass (silent except)
        # Re-populate the line-order lists so they reflect the current rep-set
        # / well selection each time the panel is opened.
        try:
            sb._refresh_line_order_lists()
        except Exception:
            swallowed()  # was: pass (silent except)
        # Re-seed the segments that write straight to ``PlotState``. The sidebar
        # is built once per figure and cached for the life of the process (it is
        # only ever *hidden* on a tab change), so those widgets read their state
        # exactly once — while ``view_state._apply_bar`` writes the same fields
        # on every dataset load and ``view_state.reset`` writes them again. A
        # segment left showing the previous dataset's mode is not merely
        # cosmetic: re-selecting the segment a ``SegmentedControl`` already
        # believes is current emits nothing, so the control does nothing until
        # the user clicks the *other* option first.
        try:
            sb._resync_plot_state_segments()
        except Exception:
            swallowed()  # was: pass (silent except)
        # And the fields backed by the figure's stored preferences, for the
        # same reason one layer over: a dataset load restores the axis-pref
        # store and a dataset *switch* drops every axis limit from it, neither
        # of which the cached panel hears about. A panel still showing the
        # previous plate's limits would write them back onto the new one on the
        # next edit, since ``_persist`` writes every field.
        try:
            sb._resync_pref_fields()
        except Exception:
            swallowed()  # was: pass (silent except)
        return _ExportEditorSession(sb)
    except Exception as exc:
        QMessageBox.warning(win if isinstance(win, QWidget) else None,
                            "Export editor unavailable", str(exc))
        return None


def open_export_style_panel(win, plot_key: str) -> None:
    """Open the floating Export Style sidebar for ``plot_key``'s figure.

    Each plot tab (line / bar / scatter cells / scatter agg / distribution /
    heatmap / spatial / multivariate / response) pre-allocates a right-side dock
    when its view is built. This routes the per-card "▸" style button to the
    ExportStyleSidebar mounted in that dock.

    **This roster is the contract, and nothing in the app enforced it.** A tab
    reaches its Properties panel by handing ``ui_helpers.make_properties_button``
    a key, and a key that is absent here falls to the status line and the panel
    simply never opens — the Response tab shipped that way, its Properties
    button inert for the whole of its existence. The button is built from a
    literal at the call site and looked up by a literal here, so the two can
    disagree forever without a name to grep or an import to break.
    **There are two rosters here, and both have to carry a tab.** This one maps
    the key to its figure; ``_resolve_export_dock`` maps that figure to the
    pre-allocated right-side dock the sidebar mounts into. Adding a row here
    and not there is not a half-fix that half-works — ``launch_export_editor``
    takes its ``dock is None`` fallback and appends the sidebar into the plot
    card's own layout, so the panel opens somewhere no other tab puts it, with
    nothing red anywhere. That is exactly what happened when Response Curves
    was added to this mapping alone.

    ``tests/test_properties_panel_keys.py`` closes both seams: it reads every
    key any tab passes to ``make_properties_button`` and fails on one this
    mapping does not carry, and it fails on a figure here that
    ``_resolve_export_dock`` does not name — or on a dock it names that no tab
    registers.
    """
    mapping = {
        "line":          ("_line_fig",         "_line_canvas",         "line_figure"),
        "bar":           ("_bar_fig",          "_bar_canvas",          "bar_figure"),
        "scatter_cells": ("_scatter_fig",      "_scatter_canvas",      "scatter_figure"),
        "scatter_agg":   ("_scatter_agg_fig",  "_scatter_agg_canvas",  "scatter_agg_figure"),
        "distribution":  ("_distribution_fig", "_distribution_canvas", "distribution_figure"),
        "heatmap":       ("_heatmap_fig",      "_heatmap_canvas",      "heatmap_figure"),
        "spatial":       ("_sp_fig",           "_sp_canvas",           "spatial_figure"),
        "multivariate":  ("_mv_fig",           "_mv_canvas",           "multivariate_figure"),
        "response":      ("_response_fig",     "_response_canvas",     "response_figure"),
    }
    spec = mapping.get(plot_key)
    if spec is None:
        win._set_status(f"Unknown export panel: {plot_key}")
        return
    fig_attr, canvas_attr, default_name = spec
    fig = getattr(win, fig_attr, None)
    canvas = getattr(win, canvas_attr, None)
    if fig is None:
        win._set_status("Plot is not ready yet.")
        return
    # If a sidebar for this figure is already open, toggle it off so
    # the button behaves as an open/close affordance.
    existing = (getattr(win, "_export_style_sidebars", {}) or {}).get(id(fig))
    if existing is not None and existing.isVisible():
        try:
            existing._close_dock()
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    from ui import theme
    from well_viewer.features.export.style import launch_export_editor
    launch_export_editor(win, fig, default_name, plot_bg=theme.CPub.bg, canvas=canvas)

