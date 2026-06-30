"""Plot/export orchestration helpers for WellViewerApp."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox
from well_viewer.core.ui_helpers import refresh_plot_empty_states
from well_viewer.core.plot_style import apply_ax_style, apply_export_style_to_current
from ui.theme import WARN
from well_viewer.core.safe import swallowed



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
    app,
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
        app._app_state.channel.active_metric, "Mean Intensity"
    )

    # 1. Line plot — always present (eager-built); the entry point that
    # established the orchestrator's name.
    lineplot_redraw(
        app,
        apply_ax_style=apply_ax_style,
        all_fluor_values=all_fluor_values,
        all_fluor_values_filtered=all_fluor_values_filtered,
        warn=warn,
        metric_label=metric_label,
    )

    # 2. Distribution — lazy-built, so guard on the canvas attribute.
    if hasattr(app, "_distribution_canvas"):
        try:
            from well_viewer.features.distribution.controller import redraw_distribution
            redraw_distribution(app)
        except Exception:
            _logger.exception("Distribution redraw failed")

    # 3. Heat Map — lazy-built; same guard.
    if hasattr(app, "_heatmap_canvas"):
        try:
            from well_viewer.features.heatmap.controller import redraw_heatmap
            redraw_heatmap(app)
        except Exception:
            _logger.exception("Heat map redraw failed")


def save_matplotlib_fig(app, fig, default_name: str, *, plot_bg: str) -> None:
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
    orig_svg = _mpl.rcParams.get("svg.fonttype", "path")
    orig_ps = _mpl.rcParams.get("ps.fonttype", 3)
    try:
        if fmt == "svg":
            _mpl.rcParams["svg.fonttype"] = "none"
        elif fmt == "eps":
            _mpl.rcParams["ps.fonttype"] = 42
        kw: dict = dict(bbox_inches="tight", facecolor=plot_bg, format=fmt)
        if fmt == "png":
            kw["dpi"] = 300
        fig.savefig(out, **kw)
        app._set_status(f"Figure saved → {Path(out).name}")
    except Exception as exc:
        QMessageBox.critical(app, "Save failed", str(exc))
    finally:
        _mpl.rcParams["svg.fonttype"] = orig_svg
        _mpl.rcParams["ps.fonttype"] = orig_ps


def _launch_editor_or_save(app, fig, default_name: str, *, plot_bg: str, canvas=None) -> None:
    # Phase 15.2: the in-tab ExportStyleSidebar was retired in favour of the
    # v2 Properties rail (which live-binds the same _export_style_prefs).
    # Save-figure now goes straight to the file dialog; the rail handles
    # styling. ``canvas`` is unused but kept in the signature so callers
    # don't need to change.
    del canvas
    save_matplotlib_fig(app, fig, default_name, plot_bg=plot_bg)


def save_line_figure(app, *, plot_bg: str) -> None:
    _launch_editor_or_save(app, app._line_fig, "line_graphs.png", plot_bg=plot_bg, canvas=getattr(app, "_line_canvas", None))


def save_bar_figure(app, *, plot_bg: str) -> None:
    tp = app._bar_tp_cb.currentText().replace(".", "_")
    _launch_editor_or_save(app, app._bar_fig, f"bar_t{tp}.png", plot_bg=plot_bg, canvas=getattr(app, "_bar_canvas", None))


def save_scatter_figure(app, *, plot_bg: str) -> None:
    ch_x = app._scatter_ch_x_cb.currentText()
    ch_y = app._scatter_ch_y_cb.currentText()
    tp = app._scatter_tp_cb.currentText().replace(".", "_")
    _launch_editor_or_save(
        app,
        app._scatter_fig,
        f"scatter_{ch_x}_vs_{ch_y}_t{tp}.png",
        plot_bg=plot_bg,
        canvas=getattr(app, "_scatter_canvas", None),
    )


def save_scatter_agg_figure(app, *, plot_bg: str) -> None:
    stat_x = app._scatter_agg_stat_x_cb.currentText()
    stat_y = app._scatter_agg_stat_y_cb.currentText()

    selected_timepoints: list[float] = []
    tp_checks = getattr(app, "_scatter_agg_tp_checks", None)
    if tp_checks:
        for tp_str, widget in tp_checks.items():
            try:
                if widget.isChecked():
                    selected_timepoints.append(float(tp_str))
            except Exception:
                swallowed()  # was: pass (silent except)
        selected_timepoints.sort()

    if selected_timepoints:
        tp_range = f"t{min(selected_timepoints):.1f}-{max(selected_timepoints):.1f}".replace(".", "_")
    else:
        tp_range = "no_tp"

    stat_x_safe = stat_x.replace(" ", "_").lower()
    stat_y_safe = stat_y.replace(" ", "_").lower()

    _launch_editor_or_save(
        app,
        app._scatter_agg_fig,
        f"scatter_agg_{stat_x_safe}_vs_{stat_y_safe}_{tp_range}.png",
        plot_bg=plot_bg,
        canvas=getattr(app, "_scatter_agg_canvas", None),
    )


def redraw_app(app) -> None:
    # Keep the EmptyState/figure stacks in sync with the current data
    # state before any matplotlib work. When no data is loaded the plot
    # is hidden behind the EmptyState page, so we can skip the actual
    # redraw — but ``redraw`` still tolerates the empty state via its
    # own no-data branch.
    from well_viewer.features.lineplot.controller import redraw_line_plots
    from well_viewer.data.data_loading import _all_fluor_values, _all_fluor_values_filtered

    refresh_plot_empty_states(app)
    # ``redraw`` fans out across the default-scope tabs (line /
    # distribution / heat map). Bar / scatter / scatter-aggregate are
    # scope-specific and re-render through their own per-tab shims.
    redraw(
        app,
        lineplot_redraw=redraw_line_plots,
        apply_ax_style=apply_ax_style,
        all_fluor_values=_all_fluor_values,
        all_fluor_values_filtered=_all_fluor_values_filtered,
        warn=WARN,
    )
    apply_export_style_to_current(app, app._line_fig, getattr(app, "_line_canvas", None))

    # Keep the Export Configurator's line-order lists in sync with the
    # current selection / replicate sets when the sidebar is open.
    for sb in (getattr(app, "_export_style_sidebars", {}) or {}).values():
        try:
            if getattr(sb, "_is_line_fig", lambda: False)():
                sb._refresh_line_order_lists()
        except Exception:
            swallowed()  # was: pass (silent except)



def open_bar_batch_export_shim(app) -> None:
    """Switch Batch Export tab to the inline bar-plot export builder."""
    if not app._app_state.dataset.well_paths:
        QMessageBox.warning(app, "No data", "Load data before opening Bar Batch Export.")
        return
    if hasattr(app, "_notebook"):
        app._notebook.setCurrentByName("Batch Export")
    if hasattr(app, "_batch_export_set_mode"):
        app._batch_export_set_mode("bar")



def open_scatter_cells_batch_export_shim(app) -> None:
    """Switch Batch Export tab to the inline scatter-cells export builder."""
    if not app._app_state.dataset.well_paths:
        QMessageBox.warning(app, "No data", "Load data before opening Scatter Cells Batch Export.")
        return
    if hasattr(app, "_notebook"):
        app._notebook.setCurrentByName("Batch Export")
    if hasattr(app, "_batch_export_set_mode"):
        app._batch_export_set_mode("scatter_cells")



def open_scatter_agg_batch_export_shim(app) -> None:
    """Switch Batch Export tab to the inline aggregate-scatter export builder."""
    if not app._app_state.dataset.well_paths:
        QMessageBox.warning(app, "No data", "Load data before opening Scatter Aggregate Batch Export.")
        return
    if hasattr(app, "_notebook"):
        app._notebook.setCurrentByName("Batch Export")
    if hasattr(app, "_batch_export_set_mode"):
        app._batch_export_set_mode("scatter_agg")



def open_batch_export_shim(app) -> None:
    if not app._app_state.dataset.well_paths:
        QMessageBox.warning(app, "No data", "Load data before opening Batch Export.")
        return
    if hasattr(app, "_notebook"):
        app._notebook.setCurrentByName("Batch Export")
    if hasattr(app, "_batch_export_set_mode"):
        app._batch_export_set_mode("line")

