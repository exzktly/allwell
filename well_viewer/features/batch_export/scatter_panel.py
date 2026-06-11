"""Scatter-mode (cells/aggregate) batch export panel."""

from __future__ import annotations

import csv
import re
from contextlib import contextmanager
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
    WELL_COLORS,
    _groups_with_loaded_wells,
)
from well_viewer.features.batch_export.base_panel import BatchExportPanel
from well_viewer.core.safe import swallowed


class ScatterBatchExportPanel(BatchExportPanel):
    """Batch exporter for Scatter Plot (cells/aggregate)."""

    def __init__(
        self,
        app,
        parent: Optional[QWidget] = None,
        *,
        scatter_mode: str = "cells",
        use_sidebar_groups: bool = False,
    ) -> None:
        self._scatter_mode = "aggregate" if scatter_mode == "aggregate" else "cells"
        super().__init__(app, parent, use_sidebar_groups=use_sidebar_groups)

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

    def _init_timepoint_dropdown(self) -> None:
        timepoints = sorted(set(self._app._app_state.dataset.all_timepoints_cache or []))
        if not timepoints:
            import pandas as _pd
            tps: set = set()
            for lbl in self._app._app_state.dataset.well_paths:
                df = self._app._get_rows(lbl)
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
            getattr(self._app, "_label_to_channel_key", None) or {}
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
                    self._app._app_state.channel.active_metric,
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
                        self._app._app_state.channel.smfish_channels or []
                    )
                    item.setEnabled(smf_ok)
        finally:
            prop_cb.blockSignals(blocked)

    def _scatter_channel_options(self) -> List[str]:
        """Real fluor channels + ratio labels — same source the per-tab
        scatter plot uses so the batch UI tracks the plot tabs exactly."""
        channels = list(self._app._app_state.channel.fluor_channels) if self._app._app_state.channel.fluor_channels else ["gfp"]
        options: List[str] = list(channels)
        ratio_labels: List[str] = []
        if hasattr(self._app, "_ratio_dropdown_labels"):
            try:
                ratio_labels = list(self._app._ratio_dropdown_labels() or [])
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
        if hasattr(self._app, "_col_for_scatter_axis"):
            return self._app._col_for_scatter_axis(channel, metric_key)
        return f"{channel}_{metric_key}"

    def _scatter_axis_stat(self, ch_cb: QComboBox, prop_cb: QComboBox) -> str:
        """Build the legacy ``Mean Fluorescence GFP (Total Intensity)``-style
        stat string for the agg pathway, using the app's composer when
        available so the produced string round-trips through
        ``scatter_controller.parse_statistic``."""
        channel = str(ch_cb.currentText() or "")
        metric_label = str(prop_cb.currentText() or "Mean Intensity")
        if hasattr(self._app, "_compose_scatter_agg_stat"):
            return self._app._compose_scatter_agg_stat(channel, metric_label)
        return f"Mean Fluorescence {channel.upper()}"

    def _run_batch(self) -> None:
        groups_with_data = _groups_with_loaded_wells(
            self._groups_for_export(), self._app._app_state.dataset.well_paths,
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
        if split_by_tp:
            jobs = [(grp, tp) for grp in groups_with_data for tp in timepoints]

            def _progress(job, step: int, total: int) -> str:
                grp, tp_h = job
                return f"'{grp.name}' @ t={tp_h:.1f}h ({step}/{total})"

            def _run_job(job) -> Optional[str]:
                grp, tp_h = job
                safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
                safe_tp = f"{tp_h:.1f}".replace(".", "_")
                stem = f"scatter_{self._scatter_mode}_{safe_grp}_t{safe_tp}"
                csv_path = out_dir / f"{stem}.csv"
                fig_path = out_dir / f"{stem}.{fmt}"
                if self._scatter_mode == "cells":
                    return self._run_scatter_cells_job(grp, tp_h, csv_path, fig_path, fmt)
                return self._run_scatter_agg_job(grp, tp_h, csv_path, fig_path, fmt)
        else:
            jobs = list(groups_with_data)

            def _progress(job, step: int, total: int) -> str:
                grp = job
                return f"'{grp.name}' all selected tps ({step}/{total})"

            def _run_job(job) -> Optional[str]:
                grp = job
                safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
                stem = f"scatter_{self._scatter_mode}_{safe_grp}_all_tps"
                csv_path = out_dir / f"{stem}.csv"
                fig_path = out_dir / f"{stem}.{fmt}"
                if self._scatter_mode == "cells":
                    return self._run_scatter_cells_multi_tp_job(grp, timepoints, csv_path, fig_path, fmt)
                return self._run_scatter_agg_multi_tp_job(grp, timepoints, csv_path, fig_path, fmt)

        self._run_batch_jobs(
            jobs=jobs,
            progress_text_fn=_progress,
            run_job_fn=_run_job,
            success_text=f"\u2713 {len(jobs)} group(s) \u2192 {out_dir.name}/",
            status_text=f"Scatter batch export ({self._scatter_mode}): {len(jobs)} group(s) \u2192 {out_dir}",
        )

    @contextmanager
    def _app_group_scope(self, grp: "BarGroup"):
        """Temporarily make the app's selection state describe just *grp*: one
        selection per replicate-set member, plus the group's solo wells as the
        per-well selection. The scatter_controller collectors read
        ``app._rep_sets_active()`` / ``app._app_state.selection.selected_wells`` (both derived from
        ``app._app_state.selection.selections``), so we swap ``_selections`` for the duration."""
        from well_viewer.domain.selections_model import make_selection
        app = self._app
        saved = (app._app_state.selection.selections, app._app_state.selection.current_selection_id, app._app_state.selection.selected_wells)
        try:
            used_names: set = set()
            used_ids: set = set()
            app._app_state.selection.selections = [
                make_selection(name=m.name, wells=list(m.wells), source="rep_set",
                               used_names=used_names, used_ids=used_ids,
                               fallback_color_idx=i)
                for i, m in enumerate(grp.members)
            ]
            app._app_state.selection.current_selection_id = None
            app._app_state.selection.selected_wells = set(grp.solo_wells)
            yield
        finally:
            (app._app_state.selection.selections, app._app_state.selection.current_selection_id, app._app_state.selection.selected_wells) = saved

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
                self._app, col_x, col_y, tp_h,
                well_colors=WELL_COLORS,
                fluor_gate_x=self._app._get_fluor_gate(ch_x_base),
                fluor_gate_y=self._app._get_fluor_gate(ch_y_base),
            )
        if not scatter_data:
            return f"{grp.name}: no scatter-cells data at t={tp_h:.1f}h"
        try:
            from well_viewer.features.export.service import _well_labels_map, well_name_for
            _well_labels = _well_labels_map(self._app)
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
            def _draw(ax):
                for label, data in scatter_data.items():
                    ax.scatter(data["x"], data["y"], label=label, color=data["color"],
                               alpha=0.6, s=26, edgecolors="none")
            self._export_scatter_figure(
                _draw,
                xlabel=col_x, ylabel=col_y,
                title=f"{grp.name} \u2014 Scatter Cells (t={tp_h:.1f}h)",
                fig_path=fig_path, fmt=fmt,
            )
        except Exception as exc:
            return f"{grp.name} scatter-cells figure: {exc}"
        return None

    def _run_scatter_agg_job(
        self, grp: BarGroup, tp_h: float, csv_path: Path, fig_path: Path, fmt: str,
    ) -> Optional[str]:
        from well_viewer.features.scatter.controller import collect_scatter_agg_data as _collect_scatter_agg_data
        stat_x = self._scatter_axis_stat(self._sc_agg_x_cb, self._sc_agg_metric_x_cb)
        stat_y = self._scatter_axis_stat(self._sc_agg_y_cb, self._sc_agg_metric_y_cb)
        with self._app_group_scope(grp):
            scatter_data = _collect_scatter_agg_data(
                self._app, stat_x, stat_y, [tp_h],
                well_colors=WELL_COLORS,
            )
        if not scatter_data:
            return f"{grp.name}: no scatter-aggregate data at t={tp_h:.1f}h"
        try:
            from well_viewer.features.export.service import (
                _well_labels_map, _well_paths_keys, well_name_for,
            )
            _well_labels = _well_labels_map(self._app)
            _well_paths = _well_paths_keys(self._app)
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
            self._export_scatter_figure(
                _draw,
                xlabel=stat_x, ylabel=stat_y,
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
                    self._app, col_x, col_y, tp_h,
                    well_colors=WELL_COLORS,
                    fluor_gate_x=self._app._get_fluor_gate(ch_x_base),
                    fluor_gate_y=self._app._get_fluor_gate(ch_y_base),
                )
            from well_viewer.features.export.service import _well_labels_map, well_name_for
            _well_labels = _well_labels_map(self._app)
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
            def _draw(ax):
                for tp_h, label, data in combined_series:
                    ax.scatter(data["x"], data["y"],
                               label=f"{label} @ t={tp_h:.1f}h",
                               alpha=0.55, s=24)
            self._export_scatter_figure(
                _draw,
                xlabel=self._scatter_axis_col(self._sc_cells_x_cb, self._sc_cells_metric_x_cb),
                ylabel=self._scatter_axis_col(self._sc_cells_y_cb, self._sc_cells_metric_y_cb),
                title=f"{grp.name} \u2014 Scatter Cells (all selected tps)",
                fig_path=fig_path, fmt=fmt, legend_fontsize=7,
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
                self._app, stat_x, stat_y, sorted(timepoints),
                well_colors=WELL_COLORS,
            )
        if not scatter_data:
            return f"{grp.name}: no scatter-aggregate data for selected timepoints"
        try:
            from well_viewer.features.export.service import (
                _well_labels_map, _well_paths_keys, well_name_for,
            )
            _well_labels = _well_labels_map(self._app)
            _well_paths = _well_paths_keys(self._app)
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
            self._export_scatter_figure(
                _draw,
                xlabel=stat_x, ylabel=stat_y,
                title=f"{grp.name} \u2014 Scatter Aggregate (all selected tps)",
                fig_path=fig_path, fmt=fmt, legend_fontsize=7,
            )
        except Exception as exc:
            return f"{grp.name} scatter-aggregate figure: {exc}"
        return None

