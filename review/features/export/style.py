"""Export-style profile management + figure export pipeline.

The right-side sidebar widget that hosts the editor lives in
``review.features.export.properties_view`` so the GUI portion is swappable. This
module owns profile lookups (``EXPORT_PROFILES``) and opening the editor.

Two things it does not own, and callers import them from their owners:

* the neutral figure-styling engine (``DEFAULT_EXPORT_STYLE_PREFS``,
  ``apply_export_style_prefs``, ``apply_export_style_to_current``) lives in
  ``review.core.plot_style`` so every plot feature imports it *downward*
  instead of reaching sideways into this export feature;
* the figure render / clipboard-write pipeline lives in
  ``widgets.fig_clipboard`` so ``widgets.plot_card`` can share it.

Nothing is re-exported here; ``tests/test_plotting_facades_are_frozen.py``
keeps it that way.
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox, QWidget
from core.safe import swallow, swallowed
from review.core.plot_style import apply_export_style_to_current


_LOG = logging.getLogger(__name__)


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

    ``sidebar`` is the session's whole payload: the panel widget, which the
    Properties toggle reads to report where the panel landed.
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
        ("_nw_fig", "_nanowell_export_dock"),
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
        from review.features.export.properties_view import ExportStyleSidebar

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
        from shiboken6 import isValid
        if sb is not None and not isValid(sb):
            # Its dock was torn down with the page it lived on; a deleted panel
            # cannot be shown again, so build a fresh one.
            _LOG.info("Properties panel for %s was deleted; building a new one", default_name)
            sb = None
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
                from review.features.export.properties_view import (
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
            from review.features.export.properties_view import (
                EXPORT_STYLE_PANEL_WIDTH as _PANEL_W,
            )
            # Show the sidebar as the dock's sole visible panel — this also
            # hides the Scatter cell viewer if it was sharing this dock, so the
            # two slide-outs stay mutually exclusive (one at a time).
            from review.core.ui_helpers import mount_widget_in_plot_dock
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
        # And the controls offered, for a figure whose view changed while the
        # panel was closed (``plot_style.mark_style_view`` re-gates only the
        # panels open at the time).
        try:
            sb._apply_facets()
        except Exception:
            swallowed()
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
        "nanowell":      ("_nw_fig",           "_nw_canvas",           "nanowell_figure"),
        "multivariate":  ("_mv_fig",           "_mv_canvas",           "multivariate_figure"),
        "response":      ("_response_fig",     "_response_canvas",     "response_figure"),
    }
    # Everything below is reached from a button's ``clicked`` slot, where an
    # exception reaches only stderr — which a double-clicked app does not have.
    # A Properties click that does nothing must say why in the log drawer.
    with swallow(f"open the Properties panel for {plot_key!r}", level=logging.WARNING, logger=_LOG):
        _toggle_export_style_panel(win, plot_key, mapping.get(plot_key))


def _refuse(win, plot_key: str, message: str) -> None:
    """Say on the status line *and* in the log why the Properties panel did not open."""
    _LOG.warning("Properties panel for %r not opened: %s", plot_key, message)
    win._set_status(message)


def _is_on_screen(widget) -> bool:
    """Whether *widget* is alive, shown, and has at least one pixel the user can see.

    ``isVisible`` alone is not that: it is true of a widget whose every ancestor is
    shown even when a layout has left it zero-sized or clipped outside its parent.
    """
    from shiboken6 import isValid
    if widget is None or not isValid(widget) or not widget.isVisible():
        return False
    return not widget.visibleRegion().isEmpty()


def _toggle_export_style_panel(win, plot_key: str, spec) -> None:
    if spec is None:
        _refuse(win, plot_key, f"Unknown export panel: {plot_key}")
        return
    fig_attr, canvas_attr, default_name = spec
    fig = getattr(win, fig_attr, None)
    canvas = getattr(win, canvas_attr, None)
    if fig is None:
        _refuse(win, plot_key, "Plot is not ready yet.")
        return
    # The button is an open/close affordance: a panel the user can see closes.
    # One that is "visible" to Qt but has no pixel on screen is re-mounted
    # instead — closing it would make the click do nothing the user can see,
    # and the next click would only re-open it in the same unseen place.
    existing = (getattr(win, "_export_style_sidebars", {}) or {}).get(id(fig))
    if _is_on_screen(existing):
        try:
            existing._close_dock()
        except Exception:
            swallowed()  # was: pass (silent except)
        return
    from ui import theme
    session = launch_export_editor(win, fig, default_name, plot_bg=theme.CPub.bg, canvas=canvas)
    if session is not None:
        from PySide6.QtCore import QTimer
        # After the layout has placed the dock: what the user sees is only known then.
        QTimer.singleShot(0, lambda: _report_placement(win, plot_key, session.sidebar))


def _report_placement(win, plot_key: str, sidebar) -> None:
    """Log where a just-opened Properties panel landed, and warn when nobody can see it."""
    from shiboken6 import isValid
    if sidebar is None or not isValid(sidebar):
        _LOG.warning("Properties panel for %r was deleted as it opened", plot_key)
        return
    top_left = sidebar.mapToGlobal(sidebar.rect().topLeft())
    where = (f"x={top_left.x()} y={top_left.y()} {sidebar.width()}x{sidebar.height()}, "
             f"window {win.width()}x{win.height()}")
    screen = sidebar.screen()
    screen_rect = screen.availableGeometry() if screen is not None else None
    on_monitor = screen_rect is None or screen_rect.intersects(
        sidebar.rect().translated(top_left))
    if _is_on_screen(sidebar) and on_monitor:
        _LOG.info("Properties panel for %r opened (%s)", plot_key, where)
        return
    dock = sidebar.parentWidget()
    _LOG.warning("Properties panel for %r opened but is not on screen (%s; screen %s; shown=%s, "
                 "dock shown=%s)", plot_key, where,
                 None if screen_rect is None else f"{screen_rect.width()}x{screen_rect.height()}",
                 sidebar.isVisible(), dock.isVisible() if dock is not None else None)

