"""Bar-mode batch export panel."""

from __future__ import annotations

import copy
import csv
import json
import math
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from well_viewer.batch_export._common import (
    PLOT_BG,
    PLOT_SPN,
    WARN,
    get_color,
    BarGroup,
    _bar_render_items,
    _extract_well_token,
    _PLATE_COLS,
    _PLATE_ROWS,
    WELL_COLORS,
    apply_ax_style,
    ask_name_dialog,
    btn_card,
    btn_danger,
    btn_primary,
    btn_secondary,
    _clear_layout_helper,
    _groups_with_loaded_wells,
    _logger,
    _CLR_DANGER,
    _CLR_SUCCESS_DARK,
    _CLR_PLACEHOLDER,
    _CLR_DISABLED_WELL,
    _CLR_ERR_BAR,
    _CLR_WHITE,
)
from well_viewer.batch_export.base_panel import BatchExportPanel
from well_viewer.batch_export.well_grid_button import _WellGridButton


class BarBatchExportPanel(BatchExportPanel):
    """Bar-plot batch export — same group editor + timepoint multi-select."""

    def __init__(
        self,
        app,
        parent: Optional[QWidget] = None,
        *,
        use_sidebar_groups: bool = False,
    ) -> None:
        super().__init__(app, parent, use_sidebar_groups=use_sidebar_groups)

    def _build_output_panel(self, layout: QVBoxLayout) -> None:
        self._build_output_header_and_io(layout)
        self._build_channel_row(layout, attr="_bar_channel_cb")

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        self._build_timepoints_section(layout)

        self._app._update_bar_tp_menu()
        tp_vals = [self._app._bar_tp_cb.itemText(i)
                   for i in range(self._app._bar_tp_cb.count())]
        for tp in tp_vals:
            if tp and tp != "\u2014":
                self._tp_lb.addItem(QListWidgetItem(tp))
        self._tp_select_all()

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        layout.addWidget(sep2)

        info = QLabel(
            "Each group \u00d7 timepoint produces:\n"
            "  \u2022 One bar figure (one bar per replicate set or well)\n"
            "  \u2022 One CSV row per member at that timepoint\n\n"
            + ("Groups come from Sample Definitions in the left sidebar.\n"
               if self._use_sidebar_groups
               else "Groups are defined in the left panel.\n")
            + "SD/SEM is computed within each ReplicateSet only."
        )
        info.setWordWrap(True)
        info.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(info)

        self._build_run_row(layout)

    def _run_batch(self) -> None:
        groups_with_data = _groups_with_loaded_wells(
            self._groups_for_export(), self._app._app_state.dataset.well_paths,
        )
        if not groups_with_data:
            msg = (
                "Define at least one non-empty group in Sample Definitions."
                if self._use_sidebar_groups
                else "Define at least one non-empty group."
            )
            QMessageBox.warning(self, "No groups", msg)
            return
        self._log_sample_definitions_snapshot(groups_with_data)

        selected_tps = self._selected_tps()
        if not selected_tps:
            QMessageBox.warning(self, "No timepoints", "Select at least one timepoint.")
            return

        out_dir = self._resolve_out_dir()
        if out_dir is None:
            return

        _ch_selected = self._selected_export_channel() or self._app._app_state.channel.active_channel
        # Threshold lookup keys by the canonical channel/ratio key, not
        # the dropdown's display label — translate ratio labels first so
        # the bar plot draws against the user-configured ThreshFracOn
        # instead of the 50.0 default.
        threshold = self._app._get_thresh_frac_on(
            self._resolved_channel_key(_ch_selected)
        )
        use_sem = self._app._use_sem
        band_lbl = "SEM" if use_sem else "SD"
        fmt = self._fmt_cb.currentText()
        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = self._fc_state()
        fc_active = fc_vs_ctrl or fc_vs_t0
        jobs = [(grp, tp_str) for grp in groups_with_data for tp_str in selected_tps]

        def _progress(job, step: int, total: int) -> str:
            grp, tp_str = job
            return f"'{grp.name}' @ t={tp_str}h  ({step}/{total})"

        def _run_job(job) -> Optional[str]:
            grp, tp_str = job
            safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
            try:
                target_t = float(tp_str)
            except ValueError:
                return f"{grp.name} t={tp_str}: invalid timepoint"

            safe_tp = tp_str.replace(".", "_").replace("-", "m")
            base = out_dir / f"bar_{safe_grp}_t{safe_tp}"

            try:
                from well_viewer.barplot_controller import (
                    FC_STATE_OFF, collect_bar_items_for_group,
                )
                from well_viewer.export_service import (
                    _well_labels_map, attach_bar_fold_change_columns,
                    bar_fold_change_fieldnames,
                    bar_metric_fieldnames, bar_metric_row,
                    well_name_for, well_names_joined,
                )
                # Resolve the panel's (Channel, Property) selection into a
                # column / ratio key — independent of the plot-tab state.
                _val_col = self._export_val_col_for("_bar_channel_cb")
                _ch = _ch_selected
                _metric = self._selected_export_metric_key("_bar_channel_cb")
                _well_labels = _well_labels_map(self._app)
                _cell_area_threshold = self._app._get_cell_area_threshold()
                _fluor_gates = self._app._get_all_fluor_gates()
                _per_fov_spread = self._app._use_fov_spread_active()

                panel_fc_state = (fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0)
                items_raw = collect_bar_items_for_group(
                    self._app, grp, target_t,
                    val_col=_val_col, threshold=threshold,
                    use_sem=use_sem, per_fov_spread=_per_fov_spread,
                    fc_state=FC_STATE_OFF,
                    cell_area_threshold=_cell_area_threshold,
                    fluor_gates=_fluor_gates,
                )
                if fc_active:
                    items_fc = collect_bar_items_for_group(
                        self._app, grp, target_t,
                        val_col=_val_col, threshold=threshold,
                        use_sem=use_sem, per_fov_spread=_per_fov_spread,
                        fc_state=panel_fc_state,
                        cell_area_threshold=_cell_area_threshold,
                        fluor_gates=_fluor_gates,
                    )
                    fc_by_key = {it.key: it for it in items_fc}
                else:
                    fc_by_key = {}

                # Build a (key → member metadata) map so each row can pick
                # up its wells / member_type without a second pass.
                _meta_by_key: Dict[str, tuple] = {}
                for rset in grp.members:
                    valid = [w for w in rset.wells if w in self._app._app_state.dataset.well_paths]
                    if valid:
                        _meta_by_key[rset.name] = (
                            "replicate_set", ";".join(valid), len(valid),
                        )
                for w in grp.solo_wells:
                    if w in self._app._app_state.dataset.well_paths:
                        _meta_by_key[w] = ("solo_well", w, 1)

                rows_csv: List[dict] = []
                for item in items_raw:
                    meta = _meta_by_key.get(item.key)
                    if meta is None:
                        continue
                    member_type, wells_str, n_wells = meta
                    if member_type == "replicate_set":
                        well_names_str = well_names_joined(wells_str, _well_labels)
                    else:
                        well_names_str = well_name_for(wells_str, _well_labels)
                    row = {
                        "group": grp.name,
                        "member": item.key,
                        "member_type": member_type,
                        "wells": wells_str,
                        "well_names": well_names_str,
                        "n_wells": n_wells,
                    }
                    row.update(bar_metric_row(
                        mean=item.mean, spread=item.spread,
                        frac=item.frac, frac_spread=item.frac_spread,
                        has=item.has_mean,
                        n_above=float(item.n_above),
                        n_above_spread=float(item.n_above_spread),
                        ch=_ch, metric=_metric, tp_str=tp_str,
                        threshold=threshold, band_lbl=band_lbl,
                    ))
                    if fc_active and item.key in fc_by_key:
                        fc_item = fc_by_key[item.key]
                        attach_bar_fold_change_columns(
                            row, fc_mean=fc_item.mean, fc_spread=fc_item.spread,
                            fc_has=fc_item.has_mean, band_lbl=band_lbl,
                            vs_control=fc_vs_ctrl, vs_t0=fc_vs_t0,
                            control_label=fc_ctrl_lbl,
                        )
                    rows_csv.append(row)
                if rows_csv:
                    fnames = (
                        ["group", "member", "member_type",
                         "wells", "well_names", "n_wells"]
                        + bar_metric_fieldnames(_ch, _metric, band_lbl)
                    )
                    if fc_active:
                        fnames += bar_fold_change_fieldnames(band_lbl)
                    with open(str(base) + ".csv", "w", newline="") as fh:
                        wrt = csv.DictWriter(fh, fieldnames=fnames)
                        wrt.writeheader()
                        wrt.writerows(rows_csv)
            except Exception as exc:
                return f"{grp.name} t={tp_str} CSV: {exc}"

            try:
                # Helpers consulted during the render may read
                # ``app._app_state.channel.active_val_col`` directly — swap it for the
                # panel's selection so a ratio or non-MFI batch draws
                # the right curves.
                with self._app_val_col_scope(_val_col):
                    fig = self._render_bar_group_figure(
                        grp, target_t, tp_str, threshold, use_sem, band_lbl,
                        fc_state=panel_fc_state,
                    )
                self._save_figure(fig, Path(str(base) + f".{fmt}"), fmt)
            except Exception as exc:
                return f"{grp.name} t={tp_str} figure: {exc}"
            return None

        self._run_batch_jobs(
            jobs=jobs,
            progress_text_fn=_progress,
            run_job_fn=_run_job,
            success_text=f"\u2713 {len(groups_with_data)}g \u00d7 {len(selected_tps)}t \u2192 {out_dir.name}/",
            status_text=f"Bar batch export complete \u2192 {out_dir}",
        )

    def _render_bar_group_figure(
        self, grp: BarGroup, target_t: float, tp_str: str,
        threshold: float, use_sem: bool, band_lbl: str,
        *,
        fc_state: tuple = (False, "", False),
    ):
        from matplotlib.figure import Figure as _Figure
        from well_viewer import fold_change as _fc
        from well_viewer.barplot_controller import collect_bar_items_for_group

        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
        fc_active = fc_vs_ctrl or fc_vs_t0
        fig = _Figure(figsize=(8, 10), dpi=300, facecolor=PLOT_BG)
        ax_mean = fig.add_subplot(3, 1, 1)
        ax_frac = fig.add_subplot(3, 1, 2)
        ax_n = fig.add_subplot(3, 1, 3)
        fig.subplots_adjust(hspace=0.55, top=0.94, bottom=0.10,
                            left=0.13, right=0.97)
        fig.suptitle(f"{grp.name}  \u2014  t = {tp_str} h",
                     fontsize=10, fontweight="bold", color=get_color("PLOT_TXT"), y=0.97)
        _ch = (self._selected_export_channel() or self._app._app_state.channel.active_channel).upper()
        from well_viewer.metric_labels import METRIC_KEY_TO_LABEL as _MLB
        _metric_key = self._selected_export_metric_key("_bar_channel_cb")
        _metric_label = _MLB.get(_metric_key, "Mean Intensity")
        _fc_suffix = _fc.fold_change_suffix(
            fc_vs_ctrl, fc_vs_t0, fc_ctrl_lbl,
        ) if fc_active else ""
        apply_ax_style(ax_mean,
                       f"{_ch} {_metric_label} (above threshold) \u00b1 {band_lbl}{_fc_suffix}",
                       f"{_ch} {_metric_label}{_fc_suffix}")
        apply_ax_style(ax_frac, "Fraction of Cells Above Threshold", "Fraction")
        if self._app._use_fov_spread_active():
            apply_ax_style(ax_n, f"Mean events above threshold per FOV ± {band_lbl}", "N(above)/FOV")
        else:
            apply_ax_style(ax_n, "Events above threshold (N)", "N(above)")
        ax_frac.set_ylim(-0.05, 1.05)

        draw_items = collect_bar_items_for_group(
            self._app, grp, target_t,
            val_col=self._export_val_col_for("_bar_channel_cb"),
            threshold=threshold, use_sem=use_sem,
            per_fov_spread=self._app._use_fov_spread_active(),
            fc_state=fc_state,
        )
        # Restore replicate-set display labels (the batch collector emits
        # the bare rset.name; preserve the prior batch-figure convention
        # of using ``_replicate_display_label`` for the visible x-tick).
        rset_display_by_name = {
            r.name: self._app._replicate_display_label(r)
            for r in grp.members
        }
        for item in draw_items:
            item.display = rset_display_by_name.get(item.key, item.display)

        if not draw_items:
            return fig

        _bar_render_items(
            ax_mean=ax_mean,
            ax_frac=ax_frac,
            ax_n=ax_n,
            use_groups=True,
            items=draw_items,
            threshold=threshold,
            warn_color=WARN,
            border_color="#333",
            placeholder_color=_CLR_PLACEHOLDER,
            disabled_well_color=_CLR_DISABLED_WELL,
            err_bar_color=_CLR_ERR_BAR,
        )
        return fig

