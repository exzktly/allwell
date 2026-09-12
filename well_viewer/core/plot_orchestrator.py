"""Plot/export orchestration helpers for WellViewerApp."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox
from well_viewer.core.ui_helpers import refresh_plot_empty_states
from well_viewer.core.plot_style import apply_ax_style, apply_export_style_to_current
from ui import theme
from core.safe import swallowed



def _qt_file_filter_from_filetypes(filetypes) -> str:
    """Convert list-of-(label, pattern) tuples to a Qt file dialog filter string."""
    parts: list[str] = []
    for label, pattern in filetypes:
        if isinstance(pattern, (list, tuple)):
            pat = " ".join(pattern)
        else:
            pat = str(pattern)
        parts.append(f"{label} ({pat})")
    return ";;".join(parts) if parts else "All files (*)"


def redraw(
    ctx,
    *,
    lineplot_redraw,
    apply_ax_style,
    all_fluor_values,
    all_fluor_values_filtered,
    warn,
) -> None:
    """Single fan-out for the "default scope" plot tabs.

    Drives line graphs, distribution, and heat map — every plot that
    re-renders in response to a channel / metric / threshold change.
    Bar / scatter / scatter-aggregate live outside this fan-out and are
    invoked through the fold-change scope registry (``redraw_bars``,
    ``scatter_redraw_active``, ``redraw_scatter_agg_orchestrate``)
    because they consume scope-specific state (the bar timepoint
    slider, the scatter-axis property combos, etc.) and only redraw
    when their tab is the visible scope.

    Audit M6 was the split-brain: distribution + heatmap dispatch used
    to live in `runtime_app._redraw` *after* the orchestrator call, so
    a new tab added to either path could be missed. Both are now here.
    The runtime_app shim becomes a thin wrapper.
    """
    import logging
    _logger = logging.getLogger("well_viewer")

    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL
    metric_label = METRIC_KEY_TO_LABEL.get(
        ctx.state.channel.active_metric, "Mean Intensity"
    )

    # 1. Line plot — always present (eager-built); the entry point that
    # established the orchestrator's name.
    lineplot_redraw(
        ctx,
        apply_ax_style=apply_ax_style,
        all_fluor_values=all_fluor_values,
        all_fluor_values_filtered=all_fluor_values_filtered,
        warn=warn,
        metric_label=metric_label,
    )

    # 2 and 3. Distribution and Heat Map — lazy-built, so guard on the
    # registered canvas handle (``register`` stamps ``app._distribution_canvas``
    # too, so this is the exact ctx-native equivalent of the old ``hasattr``
    # guard) AND on the tab being on screen.
    #
    # The canvas guard alone asks whether the tab was ever *built*, not whether
    # anyone is looking at it. Once built it stays built, so a user who had
    # visited both tabs paid for all three figures on every selection change for
    # the rest of the session — and a 96-series figure is over a second of
    # matplotlib each.
    #
    # A figure skipped here is marked dirty rather than forgotten. Both tabs are
    # registered redraw scopes, and ``flush_dirty_scopes`` — already wired into
    # the tab-change handler — redraws a dirty scope the moment its tab is
    # shown; each also carries an ``on_activate`` hook that runs this same
    # fan-out on show, by which time it *is* visible and does draw. Two routes
    # to the same repaint, and no third flag: reusing the existing dirty set is
    # what keeps a figure from being clean by one mechanism's reckoning and
    # dirty by another's.
    from well_viewer.domain.fold_change_scopes import visible_scope_names
    try:
        _visible = visible_scope_names(ctx.window) or None
        _dirty = ctx.state.plot.fc_dirty_scopes
    except Exception:
        _visible, _dirty = None, None
    # ``or None`` is doing real work. ``visible_scope_names`` swallows its own
    # lookup failures and answers with an empty set, so "no tab is visible" and
    # "I cannot tell which tab is visible" arrive identically — and treating the
    # empty set as an answer deferred BOTH figures indefinitely for a window
    # whose tab lookup throws, or in a build where the scope registry was never
    # populated. That is failing closed while a comment claimed it failed open.
    #
    # Unknown now means draw everything that is built, which is the old
    # behaviour: a redundant redraw costs time, a skipped one costs a figure
    # that disagrees with the plate, and those are not the same mistake. The
    # cost of the conflation is small and named — when genuinely no scope is
    # visible (a non-plot tab in front), both figures are redrawn rather than
    # deferred. Their own ``on_activate`` hooks would have redrawn them on the
    # way back anyway.

    for _scope, _tab, _canvas in (
        ("distribution", "Distribution", "_distribution_canvas"),
        ("heatmap", "Heat Map", "_heatmap_canvas"),
    ):
        if not ctx.widgets.has(_canvas):
            continue
        if _visible is not None and _scope not in _visible:
            _dirty.add(_scope)
            continue
        try:
            ctx.features.redraw(ctx, _tab)
            if _dirty is not None:
                _dirty.discard(_scope)
        except Exception:
            _logger.exception("%s redraw failed", _tab)


def save_matplotlib_fig(ctx, fig, default_name: str, *, plot_bg: str) -> None:
    app = ctx.window  # transitional bridge (A3)
    import matplotlib as _mpl

    initial_dir = str(app._app_state.dataset.data_dir) if app._app_state.dataset.data_dir else ""
    initial_path = str(Path(initial_dir) / default_name) if initial_dir else default_name
    filter_str = _qt_file_filter_from_filetypes(app._FIG_FILETYPES)

    out, _selected = QFileDialog.getSaveFileName(
        app,
        "Save figure",
        initial_path,
        filter_str,
    )
    if not out:
        return

    fmt = Path(out).suffix.lstrip(".").lower() or "png"
    # rc_context restores *every* rcParam it touched on exit — including on an
    # exception — so a savefig that raises can't leak a mutated svg/ps.fonttype
    # into later plots (audit X17). Only the format-specific overrides go in.
    overrides: dict = {}
    if fmt == "svg":
        overrides["svg.fonttype"] = "none"
    elif fmt == "eps":
        overrides["ps.fonttype"] = 42
    try:
        kw: dict = dict(bbox_inches="tight", facecolor=plot_bg, format=fmt)
        if fmt == "png":
            kw["dpi"] = 300
        with _mpl.rc_context(overrides):
            fig.savefig(out, **kw)
        app._set_status(f"Figure saved → {Path(out).name}")
    except Exception as exc:
        QMessageBox.critical(app, "Save failed", str(exc))


def _launch_editor_or_save(ctx, fig, default_name: str, *, plot_bg: str, canvas=None) -> None:
    # Phase 15.2: the in-tab ExportStyleSidebar was retired in favour of the
    # v2 Properties rail (which live-binds the same _export_style_prefs).
    # Save-figure now goes straight to the file dialog; the rail handles
    # styling. ``canvas`` is unused but kept in the signature so callers
    # don't need to change.
    del canvas
    save_matplotlib_fig(ctx, fig, default_name, plot_bg=plot_bg)


def save_line_figure(ctx, *, plot_bg: str) -> None:
    app = ctx.window  # transitional bridge (A3)
    _launch_editor_or_save(ctx, app._line_fig, "line_graphs.png", plot_bg=plot_bg, canvas=getattr(app, "_line_canvas", None))


def save_bar_figure(ctx, *, plot_bg: str) -> None:
    app = ctx.window  # transitional bridge (A3)
    tp = app._bar_tp_cb.currentText().replace(".", "_")
    _launch_editor_or_save(ctx, app._bar_fig, f"bar_t{tp}.png", plot_bg=plot_bg, canvas=getattr(app, "_bar_canvas", None))


def redraw_app(ctx) -> None:
    # A8 (D-core): fully ctx-native — the old god-object unwrap bridge is gone.
    # The empty-state flip is a widget-tree walk (``ctx.window``), the render
    # fan-out threads ``ctx`` (``redraw``/``refresh_plot_empty_states`` both take
    # ctx now), and the line figure / export-style sidebars are read off the
    # widget registry instead of the god-object.
    #
    # Keep the EmptyState/figure stacks in sync with the current data
    # state before any matplotlib work. When no data is loaded the plot
    # is hidden behind the EmptyState page, so we can skip the actual
    # redraw — but ``redraw`` still tolerates the empty state via its
    # own no-data branch.
    from well_viewer.data.data_loading import _all_fluor_values, _all_fluor_values_filtered

    refresh_plot_empty_states(ctx.window)
    # ``redraw`` fans out across the default-scope tabs (line /
    # distribution / heat map). Bar / scatter / scatter-aggregate are
    # scope-specific and re-render through their own per-tab shims. The
    # line renderer is resolved from the feature registry (not imported) so
    # core never names the lineplot feature; ``redraw()`` calls it with its
    # full keyword signature, so pass the raw callable (not the 1-arg shim).
    _line = ctx.features.get("Line Graphs")
    _line_redraw = _line.redraw() if (_line and _line.redraw) else (lambda *a, **k: None)
    redraw(
        ctx,
        lineplot_redraw=_line_redraw,
        apply_ax_style=apply_ax_style,
        all_fluor_values=_all_fluor_values,
        all_fluor_values_filtered=_all_fluor_values_filtered,
        warn=theme.Colors.warn,
    )
    apply_export_style_to_current(
        ctx,
        ctx.widgets.get_or_none("_line_fig"),
        ctx.widgets.get_or_none("_line_canvas"),
    )

    # Keep the Export Configurator's line-order lists in sync with the
    # current selection / replicate sets when the sidebar is open.
    for sb in (ctx.widgets.get_or_none("_export_style_sidebars") or {}).values():
        try:
            if getattr(sb, "_is_line_fig", lambda: False)():
                sb._refresh_line_order_lists()
        except Exception:
            swallowed()  # was: pass (silent except)



def redraw_every_scope(ctx) -> None:
    """Repaint every plot: the shared scopes, then the rest through the coalescer.

    **The one fan-out for "something changed that every plot draws".** The error
    band, the replicate unit, the active Property, a replicate-group edit — each
    changes the number on every figure, and each used to reach the figures
    through a list of tab names written when there were fewer tabs. A leaf
    missing from one of those lists does not fail loudly: it keeps drawing the
    previous answer until something else repaints it, which on the tab the user
    is looking at is a plot of the wrong data that looks exactly like a plot of
    the right data.

    ``redraw_app`` covers the three scopes registered with the same callable —
    Line, Distribution and Heat Map — and ``skip`` keeps the coalescer from
    drawing them a second time; every other registered scope is redrawn if its
    tab is on screen and marked dirty otherwise, which is the defer-until-viewed
    contract every other cross-tab setting already follows.
    """
    from well_viewer.core.scope_registry import redraw_app_scope
    from well_viewer.domain.fold_change_scopes import redraw_scopes_or_defer

    redraw_app(ctx)
    redraw_scopes_or_defer(ctx.window, skip=redraw_app_scope)


def _switch_to_batch_export_tab(ctx, mode: str) -> None:
    """Focus the Batch Export notebook tab and select ``mode`` on its builder."""
    nb = getattr(ctx.window, "_notebook", None)
    if nb is not None:
        nb.setCurrentByName("Batch Export")
    set_mode = ctx.runtime.batch_export.set_mode
    if set_mode is not None:
        set_mode(mode)


def open_bar_batch_export_shim(ctx) -> None:
    """Switch Batch Export tab to the inline bar-plot export builder."""
    if not ctx.state.dataset.well_paths:
        QMessageBox.warning(ctx.window, "No data", "Load data before opening Bar Batch Export.")
        return
    _switch_to_batch_export_tab(ctx, "bar")



def open_scatter_cells_batch_export_shim(ctx) -> None:
    """Switch Batch Export tab to the inline scatter-cells export builder."""
    if not ctx.state.dataset.well_paths:
        QMessageBox.warning(ctx.window, "No data", "Load data before opening Scatter Cells Batch Export.")
        return
    _switch_to_batch_export_tab(ctx, "scatter_cells")



def open_scatter_agg_batch_export_shim(ctx) -> None:
    """Switch Batch Export tab to the inline aggregate-scatter export builder."""
    if not ctx.state.dataset.well_paths:
        QMessageBox.warning(ctx.window, "No data", "Load data before opening Scatter Aggregate Batch Export.")
        return
    _switch_to_batch_export_tab(ctx, "scatter_agg")



def open_response_batch_export_shim(ctx) -> None:
    """Switch Batch Export tab to the inline response-curve export builder."""
    if not ctx.state.dataset.well_paths:
        QMessageBox.warning(ctx.window, "No data",
                            "Load data before opening Response Batch Export.")
        return
    _switch_to_batch_export_tab(ctx, "response")


def open_batch_export_shim(ctx) -> None:
    if not ctx.state.dataset.well_paths:
        QMessageBox.warning(ctx.window, "No data", "Load data before opening Batch Export.")
        return
    _switch_to_batch_export_tab(ctx, "line")

