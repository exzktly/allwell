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
    QButtonGroup,
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
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from well_viewer.features.batch_export._common import (
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
from well_viewer.features.batch_export.base_panel import BatchExportPanel
from well_viewer.features.batch_export.well_grid_button import _WellGridButton


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

        # \u2500\u2500 Layout options (multi-tp figure + group-by + pool-replicates) \u2500\u2500
        # These mirror the on-tab toggles; defaults preserve the legacy
        # batch behaviour (one figure per (group, tp), rsets pooled).
        opts_row = QHBoxLayout()
        opts_row.setContentsMargins(12, 4, 12, 4)
        self._multi_tp_cb = QCheckBox("Single figure spans selected timepoints")
        self._multi_tp_cb.setChecked(False)
        opts_row.addWidget(self._multi_tp_cb)
        opts_row.addStretch(1)
        layout.addLayout(opts_row)

        layout_row = QHBoxLayout()
        layout_row.setContentsMargins(12, 0, 12, 4)
        layout_row.addWidget(QLabel("When spanning timepoints \u2014 cluster by:"))
        self._group_mode_cb = QComboBox()
        self._group_mode_cb.addItem("Well")
        self._group_mode_cb.addItem("Timepoint")
        layout_row.addWidget(self._group_mode_cb)
        layout_row.addStretch(1)
        layout.addLayout(layout_row)

        pool_row = QHBoxLayout()
        pool_row.setContentsMargins(12, 0, 12, 4)
        self._pool_reps_cb = QCheckBox("Pool replicate-set wells into one bar")
        self._pool_reps_cb.setChecked(True)
        pool_row.addWidget(self._pool_reps_cb)
        pool_row.addStretch(1)
        layout.addLayout(pool_row)

        # ── Display mode (bars / beeswarm / violin) ────────────────────
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(12, 4, 12, 0)
        mode_row.addWidget(QLabel("Mean panel display:"))
        self._display_mode_grp = QButtonGroup(self)
        self._mode_bar_rb = QRadioButton("Bars")
        self._mode_bar_rb.setChecked(True)
        self._mode_swarm_rb = QRadioButton("Beeswarm")
        self._mode_violin_rb = QRadioButton("Violin")
        for rb in (self._mode_bar_rb, self._mode_swarm_rb, self._mode_violin_rb):
            self._display_mode_grp.addButton(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        # ── Violin smoothing slider (5–200, default 100; matches on-tab) ─
        smooth_row = QHBoxLayout()
        smooth_row.setContentsMargins(12, 0, 12, 4)
        smooth_row.addWidget(QLabel("Violin smoothing:"))
        self._violin_smooth_slider = QSlider(Qt.Horizontal)
        self._violin_smooth_slider.setRange(5, 200)
        self._violin_smooth_slider.setValue(100)
        self._violin_smooth_slider.setFixedWidth(120)
        smooth_row.addWidget(self._violin_smooth_slider)
        self._violin_smooth_label = QLabel("1.00")
        smooth_row.addWidget(self._violin_smooth_label)
        smooth_row.addStretch(1)
        layout.addLayout(smooth_row)
        self._violin_smooth_slider.valueChanged.connect(
            lambda v: self._violin_smooth_label.setText(f"{v/100.0:.2f}")
        )

        # Gate the cluster-by combo on multi-tp mode; gate the smoothing
        # slider on whether the violin mode is selected.
        def _refresh_layout_widgets() -> None:
            multi = self._multi_tp_cb.isChecked()
            self._group_mode_cb.setEnabled(multi)
            in_violin = self._mode_violin_rb.isChecked()
            self._violin_smooth_slider.setEnabled(in_violin)
            self._violin_smooth_label.setEnabled(in_violin)
        self._multi_tp_cb.toggled.connect(lambda _=False: _refresh_layout_widgets())
        self._display_mode_grp.buttonToggled.connect(lambda *_: _refresh_layout_widgets())
        _refresh_layout_widgets()

        sep3 = QFrame(); sep3.setFrameShape(QFrame.HLine)
        layout.addWidget(sep3)

        info = QLabel(
            "Each group produces:\n"
            "  \u2022 One bar figure per timepoint (or one figure spanning all\n"
            "    selected timepoints when the toggle above is on)\n"
            "  \u2022 One CSV row per member at each timepoint\n\n"
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
        use_sem = self._app._app_state.plot.use_sem
        band_lbl = "SEM" if use_sem else "SD"
        fmt = self._fmt_cb.currentText()
        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = self._fc_state()
        fc_active = fc_vs_ctrl or fc_vs_t0
        # Layout options collected once here so each job sees a stable
        # snapshot of the panel state.
        multi_tp_figure = bool(self._multi_tp_cb.isChecked())
        group_mode = "well" if self._group_mode_cb.currentText() == "Well" else "timepoint"
        pool_replicates = bool(self._pool_reps_cb.isChecked())
        # Display mode (bars / beeswarm / violin) + violin bandwidth.
        if self._mode_violin_rb.isChecked():
            display_mode = "violin"
        elif self._mode_swarm_rb.isChecked():
            display_mode = "beeswarm"
        else:
            display_mode = "bar"
        violin_bw = float(self._violin_smooth_slider.value()) / 100.0

        # Each job is either (group, [tp_str]) for a single-tp figure (the
        # legacy path) or (group, [tp1, tp2, ...]) for a multi-tp figure.
        if multi_tp_figure and len(selected_tps) > 1:
            jobs = [(grp, list(selected_tps)) for grp in groups_with_data]
        else:
            jobs = [(grp, [tp_str]) for grp in groups_with_data for tp_str in selected_tps]

        def _progress(job, step: int, total: int) -> str:
            grp, tp_strs = job
            tp_lbl = tp_strs[0] if len(tp_strs) == 1 else f"[{','.join(tp_strs)}]"
            return f"'{grp.name}' @ t={tp_lbl}h  ({step}/{total})"

        def _run_job(job) -> Optional[str]:
            grp, tp_strs = job
            safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
            try:
                target_ts = [float(s) for s in tp_strs]
            except ValueError:
                return f"{grp.name} t={tp_strs}: invalid timepoint"

            if len(tp_strs) == 1:
                safe_tp = tp_strs[0].replace(".", "_").replace("-", "m")
                base = out_dir / f"bar_{safe_grp}_t{safe_tp}"
            else:
                base = out_dir / f"bar_{safe_grp}_t_multi"

            try:
                from well_viewer.features.barplot.controller import (
                    FC_STATE_OFF, collect_bar_items_for_group,
                )
                from well_viewer.features.export.service import (
                    _well_labels_map, attach_bar_fold_change_columns,
                    bar_fold_change_fieldnames,
                    bar_metric_fieldnames, bar_metric_row,
                    well_name_for, well_names_joined,
                )
                _val_col = self._export_val_col_for("_bar_channel_cb")
                _ch = _ch_selected
                _metric = self._selected_export_metric_key("_bar_channel_cb")
                _well_labels = _well_labels_map(self._app)
                _fluor_gates = self._app._get_all_fluor_gates()
                _per_fov_spread = self._app._use_fov_spread_active()

                panel_fc_state = (fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0)
                items_raw = collect_bar_items_for_group(
                    self._app, grp, target_ts,
                    val_col=_val_col, threshold=threshold,
                    use_sem=use_sem, per_fov_spread=_per_fov_spread,
                    fc_state=FC_STATE_OFF,
                    fluor_gates=_fluor_gates,
                    pool_replicates=pool_replicates,
                    group_mode=group_mode,
                )
                if fc_active:
                    items_fc = collect_bar_items_for_group(
                        self._app, grp, target_ts,
                        val_col=_val_col, threshold=threshold,
                        use_sem=use_sem, per_fov_spread=_per_fov_spread,
                        fc_state=panel_fc_state,
                        fluor_gates=_fluor_gates,
                        pool_replicates=pool_replicates,
                        group_mode=group_mode,
                    )
                    fc_by_key_tp = {(it.key, it.tp): it for it in items_fc}
                else:
                    fc_by_key_tp = {}

                # Build a (key → member metadata) map keyed by either rset
                # name (pooled) or individual well (expanded).
                _meta_by_key: Dict[str, tuple] = {}
                for rset in grp.members:
                    valid = [w for w in rset.wells if w in self._app._app_state.dataset.well_paths]
                    if not valid:
                        continue
                    if pool_replicates:
                        _meta_by_key[rset.name] = (
                            "replicate_set", ";".join(valid), len(valid),
                        )
                    else:
                        for w in valid:
                            _meta_by_key[w] = (
                                f"replicate_set_member:{rset.name}", w, 1,
                            )
                for w in grp.solo_wells:
                    if w in self._app._app_state.dataset.well_paths and w not in _meta_by_key:
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
                    item_tp_str = f"{item.tp:.4g}"
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
                        ch=_ch, metric=_metric, tp_str=item_tp_str,
                        threshold=threshold, band_lbl=band_lbl,
                    ))
                    fc_match = fc_by_key_tp.get((item.key, item.tp)) if fc_active else None
                    if fc_match is not None:
                        attach_bar_fold_change_columns(
                            row, fc_mean=fc_match.mean, fc_spread=fc_match.spread,
                            fc_has=fc_match.has_mean, band_lbl=band_lbl,
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
                return f"{grp.name} t={tp_strs} CSV: {exc}"

            try:
                with self._app_val_col_scope(_val_col):
                    fig = self._render_bar_group_figure(
                        grp, target_ts, tp_strs, threshold, use_sem, band_lbl,
                        fc_state=panel_fc_state,
                        pool_replicates=pool_replicates,
                        group_mode=group_mode,
                        display_mode=display_mode,
                        violin_bw=violin_bw,
                    )
                self._save_figure(fig, Path(str(base) + f".{fmt}"), fmt)
            except Exception as exc:
                return f"{grp.name} t={tp_strs} figure: {exc}"
            return None

        self._run_batch_jobs(
            jobs=jobs,
            progress_text_fn=_progress,
            run_job_fn=_run_job,
            success_text=f"\u2713 {len(groups_with_data)}g \u00d7 {len(selected_tps)}t \u2192 {out_dir.name}/",
            status_text=f"Bar batch export complete \u2192 {out_dir}",
        )

    def _render_bar_group_figure(
        self, grp: BarGroup, target_t, tp_str,
        threshold: float, use_sem: bool, band_lbl: str,
        *,
        fc_state: tuple = (False, "", False),
        pool_replicates: bool = True,
        group_mode: str = "well",
        display_mode: str = "bar",
        violin_bw: float = 1.0,
    ):
        from matplotlib.figure import Figure as _Figure
        from well_viewer.domain import fold_change as _fc
        from well_viewer.features.barplot.controller import collect_bar_items_for_group

        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
        fc_active = fc_vs_ctrl or fc_vs_t0
        # Normalise the (target_t, tp_str) inputs into the multi-shape the
        # collector expects. Both legacy single-tp calls (float, str) and
        # the new multi-tp calls (list, list) work.
        if isinstance(target_t, (list, tuple)):
            target_ts = [float(t) for t in target_t]
            tp_strs = list(tp_str) if isinstance(tp_str, (list, tuple)) else [str(tp_str)]
        else:
            target_ts = [float(target_t)]
            tp_strs = [str(tp_str)] if not isinstance(tp_str, (list, tuple)) else list(tp_str)
        tp_title = tp_strs[0] if len(tp_strs) == 1 else f"[{', '.join(tp_strs)}]"
        fig = _Figure(figsize=(8, 10), dpi=300, facecolor=PLOT_BG)
        ax_mean = fig.add_subplot(3, 1, 1)
        ax_frac = fig.add_subplot(3, 1, 2)
        ax_n = fig.add_subplot(3, 1, 3)
        fig.subplots_adjust(hspace=0.55, top=0.94, bottom=0.10,
                            left=0.13, right=0.97)
        fig.suptitle(f"{grp.name}  \u2014  t = {tp_title} h",
                     fontsize=10, fontweight="bold", color=get_color("PLOT_TXT"), y=0.97)
        _ch = (self._selected_export_channel() or self._app._app_state.channel.active_channel).upper()
        from well_viewer.data.metric_labels import (
            METRIC_KEY_TO_LABEL as _MLB,
            threshold_qualifier, fraction_title,
            events_per_fov_title, events_total_title,
        )
        _metric_key = self._selected_export_metric_key("_bar_channel_cb")
        _metric_label = _MLB.get(_metric_key, "Mean Intensity")
        _thr_q = threshold_qualifier(_metric_key)
        _fc_suffix = _fc.fold_change_suffix(
            fc_vs_ctrl, fc_vs_t0, fc_ctrl_lbl,
        ) if fc_active else ""
        # The mean panel's title varies by display mode; the bar path
        # below preserves the legacy "\u00b1 SD/SEM" suffix, beeswarm /
        # violin paths describe the distribution instead.
        if display_mode == "violin":
            apply_ax_style(
                ax_mean,
                f"{_ch} {_metric_label} distribution (violin, bw={violin_bw:.2f}){_fc_suffix}",
                f"{_ch} {_metric_label}{_fc_suffix}",
            )
        elif display_mode == "beeswarm":
            apply_ax_style(
                ax_mean,
                f"{_ch} {_metric_label} per cell{_thr_q}{_fc_suffix}",
                f"{_ch} {_metric_label}{_fc_suffix}",
            )
        else:
            apply_ax_style(ax_mean,
                           f"{_ch} {_metric_label}{_thr_q} \u00b1 {band_lbl}{_fc_suffix}",
                           f"{_ch} {_metric_label}{_fc_suffix}")
        apply_ax_style(ax_frac, fraction_title(_metric_key), "Fraction")
        if self._app._use_fov_spread_active():
            apply_ax_style(
                ax_n, events_per_fov_title(_metric_key, band_lbl),
                "N(spots)/FOV" if _metric_key == "smfish_count" else "N(above)/FOV",
            )
        else:
            apply_ax_style(
                ax_n, events_total_title(_metric_key),
                "N(spots)" if _metric_key == "smfish_count" else "N(above)",
            )
        ax_frac.set_ylim(-0.05, 1.05)

        if display_mode in ("violin", "beeswarm"):
            self._render_per_cell_panels(
                fig=fig, ax_mean=ax_mean, ax_frac=ax_frac, ax_n=ax_n,
                grp=grp, target_ts=target_ts, tp_strs=tp_strs,
                threshold=threshold, use_sem=use_sem, band_lbl=band_lbl,
                fc_state=fc_state,
                pool_replicates=pool_replicates,
                group_mode=group_mode,
                display_mode=display_mode,
                violin_bw=violin_bw,
            )
            return fig

        draw_items = collect_bar_items_for_group(
            self._app, grp, target_ts,
            val_col=self._export_val_col_for("_bar_channel_cb"),
            threshold=threshold, use_sem=use_sem,
            per_fov_spread=self._app._use_fov_spread_active(),
            fc_state=fc_state,
            pool_replicates=pool_replicates,
            group_mode=group_mode,
        )
        if not draw_items:
            return fig

        # Restore replicate-set display labels for legacy single-tp pooled
        # mode (the batch collector emits bare rset.name; preserve the
        # prior batch-figure convention of using ``_replicate_display_label``).
        # In multi-tp mode the collector already produces the desired short
        # display (e.g. "t=0") so we leave it alone.
        if len(target_ts) == 1 and pool_replicates:
            rset_display_by_name = {
                r.name: self._app._replicate_display_label(r)
                for r in grp.members
            }
            for item in draw_items:
                item.display = rset_display_by_name.get(item.key, item.display)

        _bar_render_items(
            ax_mean=ax_mean,
            ax_frac=ax_frac,
            ax_n=ax_n,
            use_groups=pool_replicates and len(target_ts) == 1,
            items=draw_items,
            threshold=threshold,
            warn_color=WARN,
            border_color="#333",
            placeholder_color=_CLR_PLACEHOLDER,
            disabled_well_color=_CLR_DISABLED_WELL,
            err_bar_color=_CLR_ERR_BAR,
        )
        return fig

    def _render_per_cell_panels(
        self, *, fig, ax_mean, ax_frac, ax_n,
        grp: BarGroup, target_ts: List[float], tp_strs: List[str],
        threshold: float, use_sem: bool, band_lbl: str,
        fc_state: tuple,
        pool_replicates: bool,
        group_mode: str,
        display_mode: str,
        violin_bw: float,
    ) -> None:
        """Draw the violin / beeswarm distribution panels for a batch group.

        Reuses the on-tab ``draw_violin`` / ``draw_beeswarm`` /
        ``draw_per_well_n_bars`` renderers so the batch output matches
        what the tab would produce. Constructs the per-column
        ``(well, color, label, tp)`` arrays from the group's rsets /
        solo wells, honouring the same pool-replicates and group-mode
        layout used by ``collect_bar_items_for_group``.
        """
        from well_viewer.features.barplot.controller import cluster_x_for_group_keys
        from well_viewer.features.barplot.renderer import (
            draw_beeswarm,
            draw_per_well_n_bars,
            draw_violin,
        )
        from well_viewer.domain.plate_layout import WELL_COLORS as _WELL_COLORS

        # Build the per-column "base" (well + colour + cluster + per-bar label).
        base_wells: List[str] = []
        base_colors: List[str] = []
        base_full: List[str] = []  # disambiguating identity (cluster header)
        if pool_replicates:
            for ri, rset in enumerate(grp.members):
                color = _WELL_COLORS[ri % len(_WELL_COLORS)]
                valid = [w for w in rset.wells if w in self._app._app_state.dataset.well_paths]
                for w in valid:
                    base_wells.append(w)
                    base_colors.append(color)
                    base_full.append(f"{self._app._well_display_label(w)} [{rset.name}]")
            for w in grp.solo_wells:
                if w not in self._app._app_state.dataset.well_paths:
                    continue
                color = _WELL_COLORS[len(base_wells) % len(_WELL_COLORS)]
                base_wells.append(w)
                base_colors.append(color)
                base_full.append(self._app._well_display_label(w))
        else:
            seen: set = set()
            for ri, rset in enumerate(grp.members):
                color = _WELL_COLORS[ri % len(_WELL_COLORS)]
                for w in rset.wells:
                    if w in seen or w not in self._app._app_state.dataset.well_paths:
                        continue
                    seen.add(w)
                    base_wells.append(w)
                    base_colors.append(color)
                    base_full.append(f"{self._app._well_display_label(w)} [{rset.name}]")
            for w in grp.solo_wells:
                if w in seen or w not in self._app._app_state.dataset.well_paths:
                    continue
                seen.add(w)
                color = _WELL_COLORS[len(base_wells) % len(_WELL_COLORS)]
                base_wells.append(w)
                base_colors.append(color)
                base_full.append(self._app._well_display_label(w))

        if not base_wells:
            return

        # Materialise the (well \u00d7 tp) columns + cluster keys (single-line
        # labels: the cluster header carries the disambiguating identity).
        plot_wells: List[str] = []
        plot_colors: List[str] = []
        plot_labels: List[str] = []
        plot_tps: List[float] = []
        group_keys: List[str] = []
        multi_tp = len(target_ts) > 1
        if group_mode == "timepoint":
            for t, t_str in zip(target_ts, tp_strs):
                for w, c, full in zip(base_wells, base_colors, base_full):
                    plot_wells.append(w)
                    plot_colors.append(c)
                    plot_labels.append(full)
                    plot_tps.append(float(t))
                    group_keys.append(t_str if multi_tp else "")
        else:
            for w, c, full in zip(base_wells, base_colors, base_full):
                for t, t_str in zip(target_ts, tp_strs):
                    plot_wells.append(w)
                    plot_colors.append(c)
                    plot_labels.append(f"t={t_str}" if multi_tp else full)
                    plot_tps.append(float(t))
                    group_keys.append(full if multi_tp else "")

        xs = cluster_x_for_group_keys(group_keys)
        tp_str_combined = ", ".join(tp_strs)
        if display_mode == "violin":
            draw_violin(
                self._app, ax_mean, ax_frac,
                plot_wells, plot_colors, plot_labels,
                plot_tps, tp_str_combined, threshold,
                xs=xs, group_keys=group_keys,
                bw=violin_bw,
            )
        else:
            draw_beeswarm(
                self._app, ax_mean, ax_frac,
                plot_wells, plot_colors, plot_labels,
                plot_tps, tp_str_combined, threshold,
                xs=xs, group_keys=group_keys,
            )
        if ax_n is not None:
            draw_per_well_n_bars(
                self._app, ax_n,
                plot_wells, plot_colors, plot_labels,
                plot_tps, threshold,
                xs=xs, group_keys=group_keys,
            )

