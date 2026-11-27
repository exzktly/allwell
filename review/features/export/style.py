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


def _style_park(win) -> QWidget:
    """Where a figure's style panel lives while the inspector is not showing
    it: a hidden child of the window. The inspector borrows the panel from
    here and returns it here."""
    reg = getattr(getattr(win, "_ctx", None), "widgets", None)
    park = reg.get_or_none("_style_park") if reg is not None else getattr(win, "_style_park", None)
    from shiboken6 import isValid
    if park is None or not isValid(park):
        park = QWidget(win if isinstance(win, QWidget) else None)
        park.setObjectName("StylePark")
        park.hide()
        if reg is not None:
            reg.register("_style_park", park)
        else:
            win._style_park = park
    return park


def _sidebars(win) -> dict:
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
    return win._export_style_sidebars


def ensure_sidebar(win, fig, default_name: str, canvas=None):
    """The style panel of *fig*, built on first use and kept for the life of
    the window. It is built in the window's park, hidden; the inspector
    borrows it as its Style tab."""
    # Lazy import: the view module imports symbols defined above, so a
    # top-level import would create a circular dependency.
    from review.features.export.properties_view import ExportStyleSidebar
    from shiboken6 import isValid

    sidebars = _sidebars(win)
    key = id(fig)
    sb = sidebars.get(key)
    if sb is not None and not isValid(sb):
        # A deleted panel cannot be shown again, so build a fresh one.
        _LOG.info("Style panel for %s was deleted; building a new one", default_name)
        sb = None
    if sb is None:
        sb = ExportStyleSidebar(win, _style_park(win), fig, canvas, default_name=default_name)
        sb.hide()
        sidebars[key] = sb
    return sb


def prepare_sidebar(win, sb, fig, canvas) -> None:
    """Bring a cached panel up to date with the figure, just before it is shown.

    The panel is built once per figure and only ever hidden, so each of these
    re-reads something that can have changed while it was out of sight."""
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
    # is built once per figure and cached for the life of the process, so those
    # widgets read their state exactly once — while ``view_state._apply_bar``
    # writes the same fields on every dataset load and ``view_state.reset``
    # writes them again. A segment left showing the previous dataset's mode is
    # not merely cosmetic: re-selecting the segment a ``SegmentedControl``
    # already believes is current emits nothing, so the control does nothing
    # until the user clicks the *other* option first.
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


def launch_export_editor(win, fig, default_name: str, *, plot_bg: str = "",
                          canvas=None) -> _ExportEditorSession | None:
    """Open the inspector's Style tab on *fig*'s panel."""
    try:
        from review.core import inspector_service
        sb = ensure_sidebar(win, fig, default_name, canvas)
        prepare_sidebar(win, sb, fig, canvas)
        ctx = getattr(win, "_ctx", None)
        if ctx is not None:
            inspector_service.open_tab_with(ctx, "style", sb, fill=True)
        return _ExportEditorSession(sb)
    except Exception as exc:
        QMessageBox.warning(win if isinstance(win, QWidget) else None,
                            "Export editor unavailable", str(exc))
        return None


def style_sidebar(win, plot_key: str) -> QWidget | None:
    """The style panel of *plot_key*'s figure, brought up to date and ready to
    be shown — or ``None`` for an unknown key or a figure not drawn yet. This
    is what the inspector's Style tab holds (``review/core/style_tab.py``)."""
    return open_export_style_panel(win, plot_key, show=False)


def open_export_style_panel(win, plot_key: str, *, show: bool = True) -> QWidget | None:
    """Toggle the inspector's Style tab for ``plot_key``'s figure — or, with
    ``show=False``, only build that figure's panel and return it.

    A plot page reaches its style panel by handing
    ``ui_helpers.make_style_button`` a key, and the roster below maps that
    key to the figure. **This roster is the contract, and nothing in the app
    enforced it**: a key absent from it falls to the status line and the panel
    never opens — the Response tab shipped that way, its button inert for the
    whole of its existence. The button is built from a literal at the call site
    and looked up by a literal here, so the two can disagree forever without a
    name to grep or an import to break.
    ``tests/test_properties_panel_keys.py`` closes it: it reads every key any
    tab passes to ``make_style_button`` and fails on one this mapping
    does not carry.
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
    spec = mapping.get(plot_key)
    if not show:
        if spec is None:
            _refuse(win, plot_key, f"Unknown export panel: {plot_key}")
            return None
        fig_attr, canvas_attr, default_name = spec
        fig = getattr(win, fig_attr, None)
        if fig is None:
            return None
        canvas = getattr(win, canvas_attr, None)
        sb = ensure_sidebar(win, fig, default_name, canvas)
        prepare_sidebar(win, sb, fig, canvas)
        return sb
    # Everything below is reached from a button's ``clicked`` slot, where an
    # exception reaches only stderr — which a double-clicked app does not have.
    # A Style click that does nothing must say why in the log drawer.
    with swallow(f"open the Properties panel for {plot_key!r}", level=logging.WARNING, logger=_LOG):
        _toggle_export_style_panel(win, plot_key, spec)
    return None


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
    # The button is an open/close affordance: a panel the inspector is showing
    # closes.
    from review.core import inspector_service
    existing = (getattr(win, "_export_style_sidebars", {}) or {}).get(id(fig))
    ctx = getattr(win, "_ctx", None)
    if existing is not None and ctx is not None and inspector_service.shows_root(ctx, "style", existing):
        inspector_service.close(ctx)
        return
    from ui import theme
    session = launch_export_editor(win, fig, default_name, plot_bg=theme.CPub.bg, canvas=canvas)
    if session is not None:
        from PySide6.QtCore import QTimer
        # After the layout has placed the panel: what the user sees is only known then.
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

