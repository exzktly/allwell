"""Scatter-mode (cells/aggregate) batch export panel."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from well_viewer.features.batch_export._common import (
    BarGroup,
    PLOT_BG,
    WELL_COLORS,
    get_color,
    _groups_with_loaded_wells,
    unique_export_stem,
)
from well_viewer.features.batch_export.base_panel import BatchExportPanel
from well_viewer.core.safe import swallowed
# ── ctx-native god-method forms (A8 / Epic-A14 panel conversion) ─────────────
from well_viewer.core.lazy import lazy as _lazy
from well_viewer.core.channel_state_controller import (
    get_fluor_gate as _get_fluor_gate,
    ratio_label_for as _ratio_label_for,
)
from well_viewer.data.data_loading import get_rows as _get_rows
_col_for_scatter_axis = _lazy("well_viewer.features.scatter.controller", "col_for_scatter_axis")
# The Scatter tab's own point + gate renderers, reused by the batch figure — a
# call, not an import statement, so they stay off the deferred-import budget.
_render_scatter_points = _lazy("well_viewer.features.scatter.gating", "render_scatter_points")
_draw_gates = _lazy("well_viewer.features.scatter.gating", "draw_gates")
_log1p_axis_suffix = _lazy("well_viewer.features.scatter._layout", "log1p_axis_suffix")
_compose_scatter_agg_stat = _lazy("well_viewer.features.scatter.controller", "compose_scatter_agg_stat")


class ScatterBatchExportPanel(BatchExportPanel):
    """Batch exporter for Scatter Plot (cells/aggregate)."""

    # Scatter exports have no error-band / per-FOV spread controls.
    _INCLUDES_SPREAD = False

    def __init__(
        self,
        ctx,
        parent: Optional[QWidget] = None,
        *,
        scatter_mode: str = "cells",
        use_sidebar_groups: bool = False,
    ) -> None:
        self._scatter_mode = "aggregate" if scatter_mode == "aggregate" else "cells"
        super().__init__(ctx, parent, use_sidebar_groups=use_sidebar_groups)

    def _compute_persist_key(self) -> str:
        return "scatter_agg" if self._scatter_mode == "aggregate" else "scatter_cells"

    def _build_output_panel(self, layout: QVBoxLayout) -> None:
        title = ("OUTPUT SETTINGS \u2014 SCATTER CELLS"
                 if self._scatter_mode == "cells"
                 else "OUTPUT SETTINGS \u2014 SCATTER AGGREGATE")
        self._build_output_header_and_io(layout, title=title)

        # X / Y selection rows. Each axis carries its own Channel +
        # Property pair so the batch export mirrors the per-tab plot UI
        # (including ratio channels, which collapse the Property combo to
        # ``Calculated Val``). The agg variant adds ``Fraction above
        # threshold`` to the Property options so the per-well aggregation
        # mode is still reachable.
        if self._scatter_mode == "cells":
            self._sc_cells_x_cb, self._sc_cells_metric_x_cb = self._build_axis_row(
                layout, axis_label="X", include_fraction=False,
            )
            self._sc_cells_y_cb, self._sc_cells_metric_y_cb = self._build_axis_row(
                layout, axis_label="Y", include_fraction=False,
            )
            self._init_scatter_cells_axes()
        else:
            self._sc_agg_x_cb, self._sc_agg_metric_x_cb = self._build_axis_row(
                layout, axis_label="X", include_fraction=True,
            )
            self._sc_agg_y_cb, self._sc_agg_metric_y_cb = self._build_axis_row(
                layout, axis_label="Y", include_fraction=True,
            )
            self._init_scatter_agg_axes()

        self._build_timepoints_section(layout)
        self._init_timepoint_dropdown()

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(12, 0, 12, 4)
        self._scatter_split_tp_cb = QCheckBox(
            "Separate file per selected timepoint (otherwise combine all selected timepoints per group)"
        )
        self._scatter_split_tp_cb.setChecked(True)
        mode_row.addWidget(self._scatter_split_tp_cb)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self._build_scatter_display_options(layout)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        info = QLabel(
            "Each group \u00d7 timepoint produces one scatter figure and one CSV.\n"
            "Groups are selected with the same Batch Export group picker."
        )
        info.setWordWrap(True)
        info.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(info)

        self._build_run_row(layout, button_text="\u25b6  Run Scatter Batch Export")

    def _build_scatter_display_options(self, layout: QVBoxLayout) -> None:
        """Display-option controls mirroring the on-tab Scatter plot.

        Cells mode exposes the full set (density shading, X/Y log, overlay vs
        separate-axes faceting, colour-by-time, show gates); the aggregate mode
        exposes X/Y log. Defaults match the on-tab scatter (log axes on, gates
        shown, overlay). Values are read at export time and passed straight into
        the tab's own ``render_scatter_points`` / ``draw_gates`` (single source
        of truth), so a batch figure looks like the tab."""
        row = QHBoxLayout()
        row.setContentsMargins(12, 0, 12, 4)
        if self._scatter_mode == "cells":
            self._sc_density_cb = QCheckBox("Color by density")
            self._sc_xlog_cb = QCheckBox("X log"); self._sc_xlog_cb.setChecked(True)
            self._sc_ylog_cb = QCheckBox("Y log"); self._sc_ylog_cb.setChecked(True)
            self._sc_color_tp_cb = QCheckBox("Color by time")
            self._sc_gates_cb = QCheckBox("Show gates"); self._sc_gates_cb.setChecked(True)
            for w in (self._sc_density_cb, self._sc_xlog_cb, self._sc_ylog_cb,
                      self._sc_color_tp_cb, self._sc_gates_cb):
                row.addWidget(w)
            row.addWidget(QLabel("Layout:"))
            self._sc_layout_cb = QComboBox()
            self._sc_layout_cb.addItems(["Overlay", "Separate axes"])
            self._sc_layout_cb.setToolTip(
                "Overlay draws every group on one axis; Separate axes stacks one "
                "panel per group (also forced when Color by time is on).")
            row.addWidget(self._sc_layout_cb)
        else:
            self._sc_agg_xlog_cb = QCheckBox("X log")
            self._sc_agg_ylog_cb = QCheckBox("Y log")
            row.addWidget(self._sc_agg_xlog_cb)
            row.addWidget(self._sc_agg_ylog_cb)
        row.addStretch(1)
        layout.addLayout(row)

    def _init_timepoint_dropdown(self) -> None:
        timepoints = sorted(set(self._ctx.state.dataset.all_timepoints_cache or []))
        if not timepoints:
            import pandas as _pd
            tps: set = set()
            for lbl in self._ctx.state.dataset.well_paths:
                df = _get_rows(self._ctx, lbl)
                if df is None or "timepoint_hours" not in df.columns:
                    continue
                tp = _pd.to_numeric(df["timepoint_hours"], errors="coerce").dropna().unique()
                tps.update(float(t) for t in tp)
            timepoints = sorted(tps)
        tp_vals = [f"{tp:.1f}" for tp in timepoints] if timepoints else ["0.0"]
        self._tp_lb.clear()
        for tp in tp_vals:
            self._tp_lb.addItem(QListWidgetItem(tp))
        self._tp_select_all()

    def _build_axis_row(
        self, layout: QVBoxLayout, *, axis_label: str, include_fraction: bool,
    ) -> tuple:
        """Add a "{axis}: Channel <combo>  Property <combo>" row.

        The Property combo gets full ``METRIC_ORDER`` plus an optional
        ``Fraction above threshold`` entry; it dynamically collapses to
        ``Calculated Val`` when the channel resolves to a ratio key,
        matching the per-tab scatter UI behaviour.

        Returns ``(channel_combo, property_combo)``.
        """
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        lbl = QLabel(f"{axis_label}:")
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        row.addWidget(lbl)

        ch_cb = QComboBox()
        ch_cb.setMinimumWidth(140)
        row.addWidget(QLabel("Channel:"))
        row.addWidget(ch_cb)

        prop_cb = QComboBox()
        prop_cb.setMinimumWidth(140)
        row.addWidget(QLabel("Property:"))
        row.addWidget(prop_cb)
        row.addStretch(1)
        layout.addLayout(row)

        # Stash both on the panel so `_refresh_scatter_axis_property` can
        # find them, then wire the channel→property refresh signal.
        ch_cb._paired_property_cb = prop_cb  # type: ignore[attr-defined]
        ch_cb._include_fraction = include_fraction  # type: ignore[attr-defined]
        ch_cb.currentIndexChanged.connect(
            lambda _i, _c=ch_cb: self._refresh_scatter_axis_property(_c)
        )
        return ch_cb, prop_cb

    def _refresh_scatter_axis_property(self, ch_cb: QComboBox) -> None:
        """Reshape the Property combo paired with ``ch_cb`` based on its
        currently-selected channel (ratio → ``Calculated Val``, real
        channel → full intensity set, optionally + ``Fraction above
        threshold`` for the agg axis)."""
        from well_viewer.data.metric_labels import (
            METRIC_ORDER, METRIC_KEY_TO_LABEL, CALCULATED_VAL_LABEL,
        )
        from well_viewer.domain.ratio_models import is_ratio_key as _is_ratio_key
        prop_cb = getattr(ch_cb, "_paired_property_cb", None)
        if prop_cb is None:
            return
        include_fraction = bool(getattr(ch_cb, "_include_fraction", False))
        channel = str(ch_cb.currentText() or "")
        ratio_key = (
            self._ctx.state.channel.label_to_channel_key
        ).get(channel)
        is_ratio = bool(ratio_key and _is_ratio_key(ratio_key))

        prev = str(prop_cb.currentText() or "")
        blocked = prop_cb.blockSignals(True)
        try:
            prop_cb.clear()
            if is_ratio:
                prop_cb.addItem(CALCULATED_VAL_LABEL)
                prop_cb.setCurrentIndex(0)
                prop_cb.setEnabled(True)
                return
            items = list(METRIC_ORDER)
            if include_fraction:
                items.append("Fraction above threshold")
            prop_cb.addItems(items)
            prop_cb.setEnabled(True)
            if prev in items:
                prop_cb.setCurrentText(prev)
            else:
                fallback = METRIC_KEY_TO_LABEL.get(
                    self._ctx.state.channel.active_metric,
                    "Mean Intensity",
                )
                idx = prop_cb.findText(fallback)
                if idx >= 0:
                    prop_cb.setCurrentIndex(idx)
            # Grey out smFISH Count when the channel isn't smFISH.
            sm_idx = prop_cb.findText("smFISH Count")
            if sm_idx >= 0:
                model = prop_cb.model()
                item = model.item(sm_idx) if model is not None else None
                if item is not None:
                    smf_ok = channel in (
                        self._ctx.state.channel.smfish_channels or []
                    )
                    item.setEnabled(smf_ok)
        finally:
            prop_cb.blockSignals(blocked)

    def _scatter_channel_options(self) -> List[str]:
        """Real fluor channels + ratio labels — same source the per-tab
        scatter plot uses so the batch UI tracks the plot tabs exactly."""
        channels = list(self._ctx.state.channel.fluor_channels) if self._ctx.state.channel.fluor_channels else ["gfp"]
        options: List[str] = list(channels)
        ratio_labels: List[str] = []
        try:
            ratio_labels = [
                _ratio_label_for(self._ctx, r)
                for r in (self._ctx.state.channel.ratio_metrics or [])
            ]
        except Exception:
            ratio_labels = []
        for r in ratio_labels:
            if r and r not in options:
                options.append(r)
        return options or ["gfp"]

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Refresh the X / Y channel options so newly-defined ratios show
        # up without restarting the panel.
        try:
            if self._scatter_mode == "cells" and hasattr(self, "_sc_cells_x_cb"):
                options = self._scatter_channel_options()
                for cb in (self._sc_cells_x_cb, self._sc_cells_y_cb):
                    prev = cb.currentText()
                    blocked = cb.blockSignals(True)
                    try:
                        cb.clear()
                        cb.addItems(options)
                        if prev in options:
                            cb.setCurrentText(prev)
                    finally:
                        cb.blockSignals(blocked)
                    self._refresh_scatter_axis_property(cb)
            elif self._scatter_mode == "aggregate" and hasattr(self, "_sc_agg_x_cb"):
                options = self._scatter_channel_options()
                for cb in (self._sc_agg_x_cb, self._sc_agg_y_cb):
                    prev = cb.currentText()
                    blocked = cb.blockSignals(True)
                    try:
                        cb.clear()
                        cb.addItems(options)
                        if prev in options:
                            cb.setCurrentText(prev)
                    finally:
                        cb.blockSignals(blocked)
                    self._refresh_scatter_axis_property(cb)
        except Exception:
            swallowed()  # was: pass (silent except)

    def _init_scatter_cells_axes(self) -> None:
        options = self._scatter_channel_options()
        for cb in (self._sc_cells_x_cb, self._sc_cells_y_cb):
            cb.clear()
            cb.addItems(options)
        self._sc_cells_x_cb.setCurrentText(options[0])
        self._sc_cells_y_cb.setCurrentText(options[1] if len(options) > 1 else options[0])
        # Trigger property-combo refresh so each axis picks up the right
        # metric set for its starting channel.
        self._refresh_scatter_axis_property(self._sc_cells_x_cb)
        self._refresh_scatter_axis_property(self._sc_cells_y_cb)

    def _init_scatter_agg_axes(self) -> None:
        options = self._scatter_channel_options()
        for cb in (self._sc_agg_x_cb, self._sc_agg_y_cb):
            cb.clear()
            cb.addItems(options)
        self._sc_agg_x_cb.setCurrentText(options[0])
        self._sc_agg_y_cb.setCurrentText(options[1] if len(options) > 1 else options[0])
        self._refresh_scatter_axis_property(self._sc_agg_x_cb)
        self._refresh_scatter_axis_property(self._sc_agg_y_cb)

    def _scatter_metric_key(self, prop_cb: QComboBox) -> str:
        """Map the Property combo's current label to an intensity-metric key.

        Returns one of: ``mean_intensity``, ``total_intensity``,
        ``max_intensity``, ``min_intensity``, ``std_intensity``,
        ``smfish_count``, ``fraction_above_threshold`` (agg only),
        ``calculated_val`` (ratio path — caller is expected to resolve the
        column via ``_col_for_scatter_axis`` instead).
        """
        from well_viewer.data.metric_labels import (
            METRIC_LABEL_TO_KEY, CALCULATED_VAL_LABEL,
        )
        label = str(prop_cb.currentText() or "")
        if label == CALCULATED_VAL_LABEL:
            return "calculated_val"
        if label == "Fraction above threshold":
            return "fraction_above_threshold"
        return METRIC_LABEL_TO_KEY.get(label, "mean_intensity")

    def _scatter_axis_col(self, ch_cb: QComboBox, prop_cb: QComboBox) -> str:
        """Resolve a (channel combo, property combo) pair to a CSV column
        / ratio key, via ``app._col_for_scatter_axis``."""
        channel = str(ch_cb.currentText() or "")
        metric_key = self._scatter_metric_key(prop_cb)
        return _col_for_scatter_axis(self._ctx, channel, metric_key)

    def _scatter_axis_stat(self, ch_cb: QComboBox, prop_cb: QComboBox) -> str:
        """Build the legacy ``Mean Fluorescence GFP (Total Intensity)``-style
        stat string for the agg pathway, using the app's composer when
        available so the produced string round-trips through
        ``scatter_controller.parse_statistic``."""
        channel = str(ch_cb.currentText() or "")
        metric_label = str(prop_cb.currentText() or "Mean Intensity")
        return _compose_scatter_agg_stat(self._ctx, channel, metric_label)

    def _run_batch(self) -> None:
        groups_with_data = _groups_with_loaded_wells(
            self._groups_for_export(), self._ctx.state.dataset.well_paths,
        )
        if not groups_with_data:
            QMessageBox.warning(self, "No groups", "Define at least one non-empty group.")
            return
        out_dir = self._resolve_out_dir()
        if out_dir is None:
            return
        selected_tps = self._selected_tps()
        if not selected_tps:
            QMessageBox.warning(self, "No timepoint", "Select a valid timepoint.")
            return
        timepoints: List[float] = []
        for tp_str in selected_tps:
            try:
                timepoints.append(float(tp_str))
            except ValueError:
                continue
        if not timepoints:
            QMessageBox.warning(self, "No timepoint", "Select at least one valid timepoint.")
            return
        fmt = self._fmt_cb.currentText()
        split_by_tp = self._scatter_split_tp_cb.isChecked()
        used_stems: set = set()  # uniquify output stems across groups
        if split_by_tp:
            jobs = [(grp, tp) for grp in groups_with_data for tp in timepoints]

            def _progress(job, step: int, total: int) -> str:
                grp, tp_h = job
                return f"'{grp.name}' @ t={tp_h:.1f}h ({step}/{total})"

            def _run_job(job, write_dir) -> Optional[str]:
                grp, tp_h = job
                safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
                safe_tp = f"{tp_h:.1f}".replace(".", "_")
                stem = unique_export_stem(
                    f"scatter_{self._scatter_mode}_{safe_grp}_t{safe_tp}", used_stems)
                # Stage into write_dir (audit X3); committed to out_dir on success.
                csv_path = write_dir / f"{stem}.csv"
                fig_path = write_dir / f"{stem}.{fmt}"
                if self._scatter_mode == "cells":
                    return self._run_scatter_cells_job(grp, tp_h, csv_path, fig_path, fmt)
                return self._run_scatter_agg_job(grp, tp_h, csv_path, fig_path, fmt)
        else:
            jobs = list(groups_with_data)

            def _progress(job, step: int, total: int) -> str:
                grp = job
                return f"'{grp.name}' all selected tps ({step}/{total})"

            def _run_job(job, write_dir) -> Optional[str]:
                grp = job
                safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
                stem = unique_export_stem(
                    f"scatter_{self._scatter_mode}_{safe_grp}_all_tps", used_stems)
                # Stage into write_dir (audit X3); committed to out_dir on success.
                csv_path = write_dir / f"{stem}.csv"
                fig_path = write_dir / f"{stem}.{fmt}"
                if self._scatter_mode == "cells":
                    return self._run_scatter_cells_multi_tp_job(grp, timepoints, csv_path, fig_path, fmt)
                return self._run_scatter_agg_multi_tp_job(grp, timepoints, csv_path, fig_path, fmt)

        self._run_batch_jobs(
            jobs=jobs,
            progress_text_fn=_progress,
            run_job_fn=_run_job,
            success_text=f"\u2713 {len(jobs)} group(s) \u2192 {out_dir.name}/",
            status_text=f"Scatter batch export ({self._scatter_mode}): {len(jobs)} group(s) \u2192 {out_dir}",
            out_dir=out_dir,
        )

    def _run_scatter_cells_job(
        self, grp: BarGroup, tp_h: float, csv_path: Path, fig_path: Path, fmt: str,
    ) -> Optional[str]:
        from well_viewer.features.scatter.controller import collect_scatter_data as _collect_scatter_data
        col_x = self._scatter_axis_col(self._sc_cells_x_cb, self._sc_cells_metric_x_cb)
        col_y = self._scatter_axis_col(self._sc_cells_y_cb, self._sc_cells_metric_y_cb)
        with self._app_group_scope(grp):
            ch_x_base = self._sc_cells_x_cb.currentText().split(" ")[0]
            ch_y_base = self._sc_cells_y_cb.currentText().split(" ")[0]
            scatter_data = _collect_scatter_data(
                self._ctx, col_x, col_y, tp_h,
                well_colors=WELL_COLORS,
                fluor_gate_x=_get_fluor_gate(self._ctx, ch_x_base),
                fluor_gate_y=_get_fluor_gate(self._ctx, ch_y_base),
            )
        if not scatter_data:
            return f"{grp.name}: no scatter-cells data at t={tp_h:.1f}h"
        try:
            from well_viewer.features.export.service import _well_labels_map, well_name_for
            _well_labels = _well_labels_map(self._ctx)
            rows: List[dict] = []
            for label, data in scatter_data.items():
                for x, y, meta in zip(data["x"], data["y"], data["metadata"]):
                    well, filename, nuclear_id, _row_idx = meta
                    rows.append({
                        "group": grp.name, "member": label,
                        "well": well,
                        "well_name": well_name_for(well, _well_labels),
                        "timepoint_h": f"{tp_h:.4f}",
                        "x": f"{x:.8g}", "y": f"{y:.8g}",
                        "filename": filename, "nucleus_id": nuclear_id,
                    })
            with open(csv_path, "w", newline="") as fh:
                fieldnames = ["group", "member", "well", "well_name", "timepoint_h", "x", "y", "filename", "nucleus_id"]
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            return f"{grp.name} scatter-cells CSV: {exc}"
        try:
            with self._app_group_scope(grp):
                self._render_scatter_cells_figure(
                    scatter_data, col_x=col_x, col_y=col_y, tps_list=[tp_h],
                    title=f"{grp.name} \u2014 Scatter Cells (t={tp_h:.1f}h)",
                    fig_path=fig_path, fmt=fmt,
                )
        except Exception as exc:
            return f"{grp.name} scatter-cells figure: {exc}"
        return None

    def _render_scatter_cells_figure(
        self, scatter_data, *, col_x: str, col_y: str, tps_list, title: str,
        fig_path: Path, fmt: str,
    ) -> None:
        """Render a per-cell scatter figure by reusing the tab's own
        ``render_scatter_points`` (+ ``draw_gates``), honouring the panel's
        density / X-Y log / colour-by-time / overlay-vs-faceted / show-gates
        options. One axis for Overlay, one axis per group for Separate axes
        (also forced by Color by time). Must run inside ``_app_group_scope`` so
        the gate/geometry lookups see the group's selections."""
        from matplotlib.figure import Figure as _Figure

        density_on = self._sc_density_cb.isChecked()
        x_log = self._sc_xlog_cb.isChecked()
        y_log = self._sc_ylog_cb.isChecked()
        color_by_tp = self._sc_color_tp_cb.isChecked()
        show_gates = self._sc_gates_cb.isChecked()
        separate = self._sc_layout_cb.currentText().startswith("Separate")
        prefs = self._ctx.state.export_prefs.style_prefs or {}
        marker_size = float(prefs.get("marker_size", 5.0) or 5.0)
        ink = get_color("TXT_MUT")

        items = list(scatter_data.items())
        faceted = (separate or color_by_tp) and len(items) > 1
        xlabel = col_x + _log1p_axis_suffix(x_log)
        ylabel = col_y + _log1p_axis_suffix(y_log)

        n_panels = len(items) if faceted else 1
        fig = _Figure(figsize=(8, max(6, 3 * n_panels) if faceted else 6),
                      dpi=300, facecolor=PLOT_BG)

        def _panel(ax, data_dict) -> None:
            _ipts, legend_handles = _render_scatter_points(
                self._ctx, ax, data_dict,
                density_on=density_on, x_log=x_log, y_log=y_log,
                marker_size=marker_size, color_by_tp=color_by_tp,
            )
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)
            if legend_handles:
                ax.legend(handles=legend_handles, loc="best", fontsize=8,
                          framealpha=0.0, facecolor="none")
            elif ax.get_legend_handles_labels()[0]:
                ax.legend(loc="best", fontsize=8, framealpha=0.0, facecolor="none")
            if show_gates:
                _draw_gates(self._ctx, ax, data_dict, tps_list, ink, handles=False)

        # render_scatter_points reads color_by_tp via the param; draw_gates reads
        # gates_hidden + axes_cols off ctx. Swap those for the render and restore
        # so a batch export never disturbs the live Scatter tab's state.
        _rt = self._ctx.runtime.scatter
        _saved = (_rt.axes_cols, _rt.gate_render, _rt.metadata,
                  self._ctx.state.scatter.gates_hidden,
                  self._ctx.state.scatter.color_by_tp)
        try:
            self._ctx.state.scatter.gates_hidden = not show_gates
            self._ctx.state.scatter.color_by_tp = color_by_tp
            _rt.axes_cols = (col_x, col_y)
            if faceted:
                axes = list(fig.subplots(n_panels, 1, sharex=True, sharey=True))
                for ax, (label, data) in zip(axes, items):
                    _panel(ax, {label: data})
                axes[-1].set_xlabel(xlabel)
                fig.suptitle(title, fontsize=11, fontweight="bold",
                             color=get_color("PLOT_TXT"))
            else:
                ax = fig.add_subplot(1, 1, 1)
                _panel(ax, scatter_data)
                ax.set_xlabel(xlabel)
                ax.set_title(title, color=get_color("PLOT_TXT"))
            self._save_figure(fig, fig_path, fmt)
        finally:
            (_rt.axes_cols, _rt.gate_render, _rt.metadata,
             self._ctx.state.scatter.gates_hidden,
             self._ctx.state.scatter.color_by_tp) = _saved

    def _apply_agg_log_scale(self, ax) -> None:
        """Apply the aggregate panel's X/Y log toggles to *ax* (log10(v+1),
        the same transform the on-tab scatter uses)."""
        from well_viewer.core.plot_style import apply_axis_log_scale
        if getattr(self, "_sc_agg_xlog_cb", None) and self._sc_agg_xlog_cb.isChecked():
            ax._aw_force_xlog = True
            ax._aw_log_plus1 = True
            apply_axis_log_scale(ax, "x", True)
        if getattr(self, "_sc_agg_ylog_cb", None) and self._sc_agg_ylog_cb.isChecked():
            ax._aw_force_ylog = True
            ax._aw_log_plus1 = True
            apply_axis_log_scale(ax, "y", True)

    def _agg_axis_labels(self, stat_x: str, stat_y: str) -> "tuple[str, str]":
        x_on = bool(getattr(self, "_sc_agg_xlog_cb", None) and self._sc_agg_xlog_cb.isChecked())
        y_on = bool(getattr(self, "_sc_agg_ylog_cb", None) and self._sc_agg_ylog_cb.isChecked())
        return stat_x + _log1p_axis_suffix(x_on), stat_y + _log1p_axis_suffix(y_on)

    def _run_scatter_agg_job(
        self, grp: BarGroup, tp_h: float, csv_path: Path, fig_path: Path, fmt: str,
    ) -> Optional[str]:
        from well_viewer.features.scatter.controller import collect_scatter_agg_data as _collect_scatter_agg_data
        stat_x = self._scatter_axis_stat(self._sc_agg_x_cb, self._sc_agg_metric_x_cb)
        stat_y = self._scatter_axis_stat(self._sc_agg_y_cb, self._sc_agg_metric_y_cb)
        with self._app_group_scope(grp):
            scatter_data = _collect_scatter_agg_data(
                self._ctx, stat_x, stat_y, [tp_h],
                well_colors=WELL_COLORS,
            )
        if not scatter_data:
            return f"{grp.name}: no scatter-aggregate data at t={tp_h:.1f}h"
        try:
            from well_viewer.features.export.service import (
                _well_labels_map, _well_paths_keys, well_name_for,
            )
            _well_labels = _well_labels_map(self._ctx)
            _well_paths = _well_paths_keys(self._ctx)
            rows: List[dict] = []
            for data in scatter_data.values():
                lbl = data.get("label", "")
                rows.append({
                    "group": grp.name,
                    "label": lbl,
                    "well_name": well_name_for(
                        str(lbl).split("_tp")[0], _well_labels,
                        well_paths=_well_paths, strict=True,
                    ),
                    "timepoint_h": f"{float(data.get('timepoint', tp_h)):.4f}",
                    "x": f"{float(data['x'][0]):.8g}",
                    "y": f"{float(data['y'][0]):.8g}",
                    "x_err": f"{float(data['x_err'][0]):.8g}",
                    "y_err": f"{float(data['y_err'][0]):.8g}",
                })
            with open(csv_path, "w", newline="") as fh:
                fieldnames = ["group", "label", "well_name", "timepoint_h", "x", "y", "x_err", "y_err"]
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            return f"{grp.name} scatter-aggregate CSV: {exc}"
        try:
            def _draw(ax):
                for data in scatter_data.values():
                    ax.errorbar(
                        data["x"], data["y"],
                        xerr=data["x_err"], yerr=data["y_err"],
                        label=data.get("label", ""),
                        color=data["color"], marker=data.get("marker", "o"),
                        linestyle="none", capsize=5, alpha=0.75,
                    )
                self._apply_agg_log_scale(ax)
            _xl, _yl = self._agg_axis_labels(stat_x, stat_y)
            self._export_scatter_figure(
                _draw,
                xlabel=_xl, ylabel=_yl,
                title=f"{grp.name} \u2014 Scatter Aggregate (t={tp_h:.1f}h)",
                fig_path=fig_path, fmt=fmt,
            )
        except Exception as exc:
            return f"{grp.name} scatter-aggregate figure: {exc}"
        return None

    def _run_scatter_cells_multi_tp_job(
        self, grp: BarGroup, timepoints: List[float],
        csv_path: Path, fig_path: Path, fmt: str,
    ) -> Optional[str]:
        from well_viewer.features.scatter.controller import collect_scatter_data as _collect_scatter_data
        combined_rows: List[dict] = []
        combined_series: List[tuple] = []
        for tp_h in sorted(timepoints):
            col_x = self._scatter_axis_col(self._sc_cells_x_cb, self._sc_cells_metric_x_cb)
            col_y = self._scatter_axis_col(self._sc_cells_y_cb, self._sc_cells_metric_y_cb)
            with self._app_group_scope(grp):
                ch_x_base = self._sc_cells_x_cb.currentText().split(" ")[0]
                ch_y_base = self._sc_cells_y_cb.currentText().split(" ")[0]
                scatter_data = _collect_scatter_data(
                    self._ctx, col_x, col_y, tp_h,
                    well_colors=WELL_COLORS,
                    fluor_gate_x=_get_fluor_gate(self._ctx, ch_x_base),
                    fluor_gate_y=_get_fluor_gate(self._ctx, ch_y_base),
                )
            from well_viewer.features.export.service import _well_labels_map, well_name_for
            _well_labels = _well_labels_map(self._ctx)
            for label, data in scatter_data.items():
                combined_series.append((tp_h, label, data))
                for x, y, meta in zip(data["x"], data["y"], data["metadata"]):
                    well, filename, nuclear_id, _row_idx = meta
                    combined_rows.append({
                        "group": grp.name,
                        "timepoint_h": f"{tp_h:.4f}",
                        "member": label,
                        "well": well,
                        "well_name": well_name_for(well, _well_labels),
                        "x": f"{x:.8g}", "y": f"{y:.8g}",
                        "filename": filename, "nucleus_id": nuclear_id,
                    })
        if not combined_rows:
            return f"{grp.name}: no scatter-cells data for selected timepoints"
        try:
            with open(csv_path, "w", newline="") as fh:
                fieldnames = ["group", "timepoint_h", "member", "well", "well_name", "x", "y", "filename", "nucleus_id"]
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(combined_rows)
        except OSError as exc:
            return f"{grp.name} scatter-cells CSV: {exc}"
        try:
            col_x = self._scatter_axis_col(self._sc_cells_x_cb, self._sc_cells_metric_x_cb)
            col_y = self._scatter_axis_col(self._sc_cells_y_cb, self._sc_cells_metric_y_cb)
            with self._app_group_scope(grp):
                # One collection over all selected timepoints so the figure has
                # per-point tp arrays (Color by time) and proper per-group
                # entries (faceting) \u2014 the CSV above keeps its per-tp rows.
                ch_x_base = self._sc_cells_x_cb.currentText().split(" ")[0]
                ch_y_base = self._sc_cells_y_cb.currentText().split(" ")[0]
                fig_data = _collect_scatter_data(
                    self._ctx, col_x, col_y, sorted(timepoints),
                    well_colors=WELL_COLORS,
                    fluor_gate_x=_get_fluor_gate(self._ctx, ch_x_base),
                    fluor_gate_y=_get_fluor_gate(self._ctx, ch_y_base),
                )
                self._render_scatter_cells_figure(
                    fig_data, col_x=col_x, col_y=col_y, tps_list=sorted(timepoints),
                    title=f"{grp.name} \u2014 Scatter Cells (all selected tps)",
                    fig_path=fig_path, fmt=fmt,
                )
        except Exception as exc:
            return f"{grp.name} scatter-cells figure: {exc}"
        return None

    def _run_scatter_agg_multi_tp_job(
        self, grp: BarGroup, timepoints: List[float],
        csv_path: Path, fig_path: Path, fmt: str,
    ) -> Optional[str]:
        from well_viewer.features.scatter.controller import collect_scatter_agg_data as _collect_scatter_agg_data
        stat_x = self._scatter_axis_stat(self._sc_agg_x_cb, self._sc_agg_metric_x_cb)
        stat_y = self._scatter_axis_stat(self._sc_agg_y_cb, self._sc_agg_metric_y_cb)
        with self._app_group_scope(grp):
            scatter_data = _collect_scatter_agg_data(
                self._ctx, stat_x, stat_y, sorted(timepoints),
                well_colors=WELL_COLORS,
            )
        if not scatter_data:
            return f"{grp.name}: no scatter-aggregate data for selected timepoints"
        try:
            from well_viewer.features.export.service import (
                _well_labels_map, _well_paths_keys, well_name_for,
            )
            _well_labels = _well_labels_map(self._ctx)
            _well_paths = _well_paths_keys(self._ctx)
            rows: List[dict] = []
            for data in scatter_data.values():
                lbl = data.get("label", "")
                rows.append({
                    "group": grp.name,
                    "label": lbl,
                    "well_name": well_name_for(
                        str(lbl).split("_tp")[0], _well_labels,
                        well_paths=_well_paths, strict=True,
                    ),
                    "timepoint_h": f"{float(data.get('timepoint', float('nan'))):.4f}",
                    "x": f"{float(data['x'][0]):.8g}",
                    "y": f"{float(data['y'][0]):.8g}",
                    "x_err": f"{float(data['x_err'][0]):.8g}",
                    "y_err": f"{float(data['y_err'][0]):.8g}",
                })
            with open(csv_path, "w", newline="") as fh:
                fieldnames = ["group", "label", "well_name", "timepoint_h", "x", "y", "x_err", "y_err"]
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            return f"{grp.name} scatter-aggregate CSV: {exc}"
        try:
            def _draw(ax):
                for data in scatter_data.values():
                    ax.errorbar(
                        data["x"], data["y"],
                        xerr=data["x_err"], yerr=data["y_err"],
                        label=data.get("label", ""),
                        marker=data.get("marker", "o"),
                        linestyle="none", capsize=5, alpha=0.75,
                    )
                self._apply_agg_log_scale(ax)
            _xl, _yl = self._agg_axis_labels(stat_x, stat_y)
            self._export_scatter_figure(
                _draw,
                xlabel=_xl, ylabel=_yl,
                title=f"{grp.name} \u2014 Scatter Aggregate (all selected tps)",
                fig_path=fig_path, fmt=fmt, legend_fontsize=7,
            )
        except Exception as exc:
            return f"{grp.name} scatter-aggregate figure: {exc}"
        return None

