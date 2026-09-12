"""Scatter-mode (cells/aggregate) batch export panel."""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ui import theme

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from review.features.batch_export._common import (
    BarGroup,
    WELL_COLORS,
    _groups_with_loaded_wells,
    unique_export_stem,
)
from review.features.batch_export._panels import Panel
from review.features.batch_export.base_panel import BatchExportPanel, SkippedJob
from review.core.plot_annotations import (
    value_label_settings as _value_label_settings,
)
from core.safe import swallowed
# ── ctx-native god-method forms (A8 / Epic-A14 panel conversion) ─────────────
from review.core.lazy import lazy as _lazy
from widgets.separator import build_hline_separator
from review.core.channel_state_controller import (
    get_fluor_gate as _get_fluor_gate,
    ratio_label_for as _ratio_label_for,
)
from review.data.data_loading import get_rows as _get_rows
# Lazy for the same reason every other scatter symbol here is: a profile
# built without the Scatter feature must still import this panel.
_annotate_agg_points = _lazy("review.features.scatter._annotations",
                             "annotate_agg_points")
_col_for_scatter_axis = _lazy("review.features.scatter.controller", "col_for_scatter_axis")
# The Scatter tab's own point + gate renderers, reused by the batch figure — a
# call, not an import statement, so they stay off the deferred-import budget.
_render_scatter_points = _lazy("review.features.scatter.gating", "render_scatter_points")
_draw_gates = _lazy("review.features.scatter.gating", "draw_gates")
_log1p_axis_suffix = _lazy("review.features.scatter._layout", "log1p_axis_suffix")
_compose_scatter_agg_stat = _lazy("review.features.scatter.controller", "compose_scatter_agg_stat")
# The tab's own whisker rule: a spread that was never computed draws no cap.
_whiskers_or_none = _lazy("review.features.scatter.controller", "whiskers_or_none")


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScatterAxes:
    """What one batch run plots, read once and then fixed for the whole run.

    ``_run_batch_jobs`` pumps ``QApplication.processEvents()`` between jobs so
    the progress bar can paint, which leaves every combo on the panel live for
    the duration of the export. Each job used to re-read the axis combos, so
    changing the X channel or its metric after the first group produced one
    output directory, under one manifest, holding figures and CSVs plotted
    against two different columns — with nothing in either file naming which.
    The panel is asked once, before the first job, and the answer is passed
    down.

    ``mode`` is carried for completeness rather than as a fix: it is assigned in
    ``__init__`` and never reassigned, so unlike the combos it cannot drift
    mid-run. Keeping it here means the stem, the status line and the branch that
    picks a job all read one value rather than three separate live lookups of a
    thing the run is not allowed to change.

    Only the fields belonging to ``mode`` are filled; the others stay ``""``.
    """

    mode: str
    #: cells mode — the resolved CSV column / ratio key per axis, and the bare
    #: channel token each was derived from (which selects the fluor gate).
    col_x: str = ""
    col_y: str = ""
    ch_x_base: str = ""
    ch_y_base: str = ""
    #: aggregate mode — the ``Mean Fluorescence GFP (Total Intensity)``-style
    #: stat string each axis resolves to.
    stat_x: str = ""
    stat_y: str = ""


class ScatterBatchExportPanel(BatchExportPanel):
    """Batch exporter for Scatter Plot (cells/aggregate)."""

    # Scatter exports have no error-band / per-FOV spread controls.
    _INCLUDES_SPREAD = False

    _SOURCE_TAB = "Scatter Plot"

    #: One logical panel. A faceted cells run draws one axes per group, but
    #: they are all the same plot of the same two columns, so they take one
    #: range and one scale between them rather than a row of controls each.
    _PANELS = (Panel("plot", "Plot"),)
    _X_ROW_LABEL = "X range:"
    _Y_ROW_LABEL = "Y range:"
    _AXES_FOLLOW_HEADER = False
    #: This mode's own ``X log`` / ``Y log`` checkboxes below apply
    #: ``log10(v + 1)`` so cells at zero stay on the figure, which the generic
    #: combo's plain log would drop. One control per axis, and the one kept is
    #: the one that is not lossy.
    _AXES_SCALE_OFFERED = False
    _SHARE_X = ()
    #: One panel, so the X row governs it. ``None`` rather than naming the key,
    #: so a mode that renames its panel does not silently stop taking X.
    _X_PANELS = None
    _CELL_SCOPE = "review.features.scatter.cell_scope_compute"

    def _cell_scope_kwargs(self, *, val_col=None, timepoints=None) -> dict:
        """This run's axes and timepoints, not the Scatter tab's combos.

        Both modes plot columns the panel resolved once at the top of the run
        (:class:`ScatterAxes`), and neither of them is what the tab's own
        ``_scatter_ch_x_cb`` holds. The cells scope also wants the channel
        *entries* rather than only the resolved columns, because the per-axis
        fluor floors are keyed on the channel.
        """
        axes = self._run_scatter_axes
        kwargs: dict = {}
        if timepoints is not None:
            kwargs["timepoints"] = tuple(float(t) for t in timepoints)
        if axes is None:
            return kwargs
        if axes.mode == "cells":
            kwargs.update(entry_x=self._sc_cells_x_cb.currentText(),
                          entry_y=self._sc_cells_y_cb.currentText(),
                          col_x=axes.col_x, col_y=axes.col_y)
        else:
            kwargs.update(
                entry_x=self._sc_agg_x_cb.currentText(),
                metric_x=self._scatter_metric_key(self._sc_agg_metric_x_cb))
        return kwargs

    @property
    def _CELL_SCOPE_FN(self) -> str:
        """The aggregate page has its own scope, and it is a different plot.

        Different markers, a different gate layer and a different timepoint
        resolution — ``agg_cell_scope``'s own docstring spells out why it is
        deliberately not derived from the per-cell one. Reading the per-cell
        scope for an aggregate run wrote a file whose block said
        ``# plot: scatter_cells`` over the wrong axes.
        """
        return "cell_scope" if self._scatter_mode == "cells" else "agg_cell_scope"

    def _mode_key(self) -> str:
        """``scatter_cells`` / ``scatter_agg`` — this class is two modes.

        Per mode rather than per class, because the two write different figures
        from different columns: one deck named for the class would leave a user
        who exported both with the aggregate deck only, silently overwritten
        over the cells one.
        """
        return f"scatter_{self._scatter_mode}"

    def __init__(
        self,
        ctx,
        parent: QWidget | None = None,
        *,
        scatter_mode: str = "cells",
        use_sidebar_groups: bool = False,
    ) -> None:
        self._scatter_mode = "aggregate" if scatter_mode == "aggregate" else "cells"
        super().__init__(ctx, parent, use_sidebar_groups=use_sidebar_groups)

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

        # After the two axis rows: the AXES block's "X:" is a range on the
        # channel those rows just chose, and above them the panel showed two
        # rows called "X:" that meant different things. See
        # ``_AXES_FOLLOW_HEADER``.
        self._build_output_tail(layout)

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

        layout.addWidget(build_hline_separator(None))

        self._build_summary_label(layout)
        self._build_run_row(layout, button_text="\u25b6  Run Scatter Batch Export")

    # \u2500\u2500 the run summary \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    _SUMMARY_FIGURE_NOTE = ""
    _SUMMARY_CSV_LINE = "One CSV of the points it drew"

    def _summary_figure_lead(self) -> str:
        """The split toggle decides how many figures a group gets."""
        if self._scatter_split_tp_cb.isChecked():
            return "One scatter figure per selected timepoint"
        return "One scatter figure spanning every selected timepoint"

    def _extra_summary_signals(self) -> list:
        return [self._scatter_split_tp_cb.toggled]

    def _summary_footnotes(self) -> list[str]:
        return ["Groups are selected with the same Batch Export group picker."]

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
        ch_cb.setMinimumWidth(theme.Metrics.combo_min_w)
        row.addWidget(QLabel("Channel:"))
        row.addWidget(ch_cb)

        prop_cb = QComboBox()
        prop_cb.setMinimumWidth(theme.Metrics.combo_min_w)
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
        from review.data.metric_labels import (
            METRIC_ORDER, METRIC_KEY_TO_LABEL, CALCULATED_VAL_LABEL,
            FRACTION_LABEL,
        )
        from review.domain.ratio_models import is_ratio_key as _is_ratio_key
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
                items.append(FRACTION_LABEL)
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
                    # Case-insensitive: this combo holds the displayed label
                    # ("GFP") while ``smfish_channels`` holds normalised tokens.
                    from review.domain.metric_registry import (
                        channel_supports_smfish,
                    )
                    item.setEnabled(channel_supports_smfish(
                        channel, self._ctx.state.channel.smfish_channels,
                    ))
        finally:
            prop_cb.blockSignals(blocked)

    def _scatter_channel_options(self) -> list[str]:
        """Real fluor channels + ratio labels — same source the per-tab
        scatter plot uses so the batch UI tracks the plot tabs exactly."""
        channels = list(self._ctx.state.channel.fluor_channels) if self._ctx.state.channel.fluor_channels else ["gfp"]
        options: list[str] = list(channels)
        ratio_labels: list[str] = []
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
        from review.data.metric_labels import (
            METRIC_LABEL_TO_KEY, CALCULATED_VAL_LABEL,
            FRACTION_LABEL, FRACTION_METRIC_KEY,
        )
        label = str(prop_cb.currentText() or "")
        if label == CALCULATED_VAL_LABEL:
            return "calculated_val"
        if label == FRACTION_LABEL:
            return FRACTION_METRIC_KEY
        return METRIC_LABEL_TO_KEY.get(label, "mean_intensity")

    def _warn_if_live_gates_ignored(self) -> None:
        """Say so when the Scatter tab is filtering by gate and this batch is not.

        Batch export deliberately does not inherit the tab's live "Apply gates"
        toggle — a batch job that silently changed its population because of a
        checkbox left on in another tab would be worse. But the *other* silence
        is just as bad: with the toggle on, the tab's figure and this batch's
        figure are drawn from different sets of cells and nothing said so. This
        is the announcement, not a behaviour change.
        """
        from review.features.scatter.collect import scatter_points_gated
        if scatter_points_gated(self._ctx):
            _logger.warning(
                "Batch export: the Scatter tab is filtering its points by gate "
                "geometry, but batch figures are exported from the unfiltered "
                "cloud — they will not match the tab's plot cell for cell.",
            )

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

    def _snapshot_scatter_axes(self) -> ScatterAxes:
        """Read the axis controls once, for the whole of one batch run.

        See :class:`ScatterAxes`. Called before the first job, never inside one.
        """
        if self._scatter_mode == "cells":
            return ScatterAxes(
                mode="cells",
                col_x=self._scatter_axis_col(
                    self._sc_cells_x_cb, self._sc_cells_metric_x_cb),
                col_y=self._scatter_axis_col(
                    self._sc_cells_y_cb, self._sc_cells_metric_y_cb),
                ch_x_base=self._sc_cells_x_cb.currentText().split(" ")[0],
                ch_y_base=self._sc_cells_y_cb.currentText().split(" ")[0],
            )
        return ScatterAxes(
            mode="aggregate",
            stat_x=self._scatter_axis_stat(
                self._sc_agg_x_cb, self._sc_agg_metric_x_cb),
            stat_y=self._scatter_axis_stat(
                self._sc_agg_y_cb, self._sc_agg_metric_y_cb),
        )

    def _run_batch(self) -> None:
        if self._run_is_refused():
            return
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
        timepoints: list[float] = []
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
        # Read once, here: everything below runs with the event loop pumping
        # between jobs, so anything read inside a job is whatever the user has
        # left the panel on by then.
        axes = self._snapshot_scatter_axes()
        used_stems: set = set()  # uniquify output stems across groups
        if split_by_tp:
            jobs = [(grp, tp) for grp in groups_with_data for tp in timepoints]

            def _progress(job, step: int, total: int) -> str:
                grp, tp_h = job
                return f"'{grp.name}' @ t={tp_h:.1f}h ({step}/{total})"

            def _run_job(job, write_dir) -> str | None:
                grp, tp_h = job
                safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
                safe_tp = f"{tp_h:.1f}".replace(".", "_")
                stem = unique_export_stem(
                    f"scatter_{axes.mode}_{safe_grp}_t{safe_tp}", used_stems)
                # Stage into write_dir (audit X3); committed to out_dir on success.
                csv_path = self._out_path(write_dir, f"{stem}.csv")
                fig_path = self._out_path(write_dir, f"{stem}.{fmt}")
                if axes.mode == "cells":
                    err = self._run_scatter_cells_job(
                        grp, tp_h, csv_path, fig_path, fmt, axes)
                else:
                    err = self._run_scatter_agg_job(
                        grp, tp_h, csv_path, fig_path, fmt, axes)
                if err is None:
                    self._write_cell_rows_for(grp, write_dir, stem)
                return err
        else:
            jobs = list(groups_with_data)

            def _progress(job, step: int, total: int) -> str:
                grp = job
                return f"'{grp.name}' all selected tps ({step}/{total})"

            def _run_job(job, write_dir) -> str | None:
                grp = job
                safe_grp = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
                stem = unique_export_stem(
                    f"scatter_{axes.mode}_{safe_grp}_all_tps", used_stems)
                # Stage into write_dir (audit X3); committed to out_dir on success.
                csv_path = self._out_path(write_dir, f"{stem}.csv")
                fig_path = self._out_path(write_dir, f"{stem}.{fmt}")
                if axes.mode == "cells":
                    err = self._run_scatter_cells_multi_tp_job(
                        grp, timepoints, csv_path, fig_path, fmt, axes)
                else:
                    err = self._run_scatter_agg_multi_tp_job(
                        grp, timepoints, csv_path, fig_path, fmt, axes)
                if err is None:
                    self._write_cell_rows_for(grp, write_dir, stem)
                return err

        self._run_batch_jobs(
            jobs=jobs,
            progress_text_fn=_progress,
            run_job_fn=_run_job,
            success_text=f"\u2713 {len(jobs)} group(s)",
            status_text=f"Scatter batch export ({axes.mode}): {len(jobs)} group(s)",
            out_dir=out_dir,
        )

    def _tp_figure_label(self, tp_h: float) -> str:
        """The timepoint as a scatter figure's title should name it.

        A scatter figure has no time axis — x and y are both metrics — so the
        title is the only place the timepoint appears, and it has to carry the
        rescale marker itself. Without this a single batch run wrote a bar figure
        titled ``t = 4 h`` beside a scatter figure titled ``t=1.0h`` for the same
        data.

        The progress line and the "no data at t=…" messages deliberately do not
        use this: those name a *job* the user picked out of the timepoint list,
        and that list is unscaled, so restating it in rescaled units would point
        at an entry they never saw. A failed job also writes no figure, so there
        is nothing for the message to disagree with.
        """
        from review.domain.time_rescale import figure_timepoint_label
        from review.domain.time_rescale import rescale_for as _time_rescale_for
        return figure_timepoint_label(tp_h, _time_rescale_for(self._ctx.state.plot))

    def _run_scatter_cells_job(
        self, grp: BarGroup, tp_h: float, csv_path: Path | None, fig_path: Path | None, fmt: str,
        axes: ScatterAxes,
    ) -> str | None:
        from review.features.scatter.controller import collect_scatter_data as _collect_scatter_data
        self._warn_if_live_gates_ignored()
        # *axes*, not the combos: the run's definition of X and Y was fixed
        # before the first job (see :class:`ScatterAxes`).
        col_x, col_y = axes.col_x, axes.col_y
        with self._app_group_scope(grp):
            scatter_data = _collect_scatter_data(
                self._ctx, col_x, col_y, tp_h,
                well_colors=WELL_COLORS,
                fluor_gate_x=_get_fluor_gate(self._ctx, axes.ch_x_base),
                fluor_gate_y=_get_fluor_gate(self._ctx, axes.ch_y_base),
            )
        if not scatter_data:
            # ``SkippedJob``, not a plain string: the runner discards the
            # entire staged export for any error, so reporting "this group
            # produced nothing" down that channel deletes every *other*
            # group's files too. Bar and line were converted and these four
            # were not, which is the worse half — one function now held two
            # meanings for the same return and neither looked wrong.
            return SkippedJob(f"{grp.name}: no scatter-cells data at t={tp_h:.1f}h")
        try:
            from review.features.export.service import figure_names_map, well_name_for
            _well_labels = figure_names_map(self._ctx)
            rows: list[dict] = []
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
            if csv_path is not None:
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
                    title=(f"{grp.name} \u2014 Scatter Cells "
                           f"({self._tp_figure_label(tp_h)})"),
                    fig_path=fig_path, fmt=fmt, group=grp,
                )
        except Exception as exc:
            return f"{grp.name} scatter-cells figure: {exc}"
        return None

    def _render_scatter_cells_figure(
        self, scatter_data, *, col_x: str, col_y: str, tps_list, title: str,
        fig_path: Path | None, fmt: str, group: BarGroup | None,
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
        ink = theme.Colors.text_muted

        items = list(scatter_data.items())
        faceted = (separate or color_by_tp) and len(items) > 1
        xlabel = col_x + _log1p_axis_suffix(x_log)
        ylabel = col_y + _log1p_axis_suffix(y_log)

        n_panels = len(items) if faceted else 1
        fig = _Figure(figsize=(8, max(6, 3 * n_panels) if faceted else 6),
                      dpi=300, facecolor=theme.CPub.bg)

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
                             color=theme.CPub.text)
            else:
                ax = fig.add_subplot(1, 1, 1)
                _panel(ax, scatter_data)
                ax.set_xlabel(xlabel)
                ax.set_title(title, color=theme.CPub.text)
            self._save_figure(fig, fig_path, fmt, group=group, title=title)
        finally:
            (_rt.axes_cols, _rt.gate_render, _rt.metadata,
             self._ctx.state.scatter.gates_hidden,
             self._ctx.state.scatter.color_by_tp) = _saved

    def _apply_agg_log_scale(self, ax) -> None:
        """Apply the aggregate panel's X/Y log toggles to *ax* (log10(v+1),
        the same transform the on-tab scatter uses)."""
        from review.core.plot_style import apply_axis_log_scale
        if getattr(self, "_sc_agg_xlog_cb", None) and self._sc_agg_xlog_cb.isChecked():
            ax._aw_force_xlog = True
            ax._aw_log_plus1 = True
            apply_axis_log_scale(ax, "x", True)
        if getattr(self, "_sc_agg_ylog_cb", None) and self._sc_agg_ylog_cb.isChecked():
            ax._aw_force_ylog = True
            ax._aw_log_plus1 = True
            apply_axis_log_scale(ax, "y", True)

    def _agg_axis_labels(self, stat_x: str, stat_y: str) -> tuple[str, str]:
        x_on = bool(getattr(self, "_sc_agg_xlog_cb", None) and self._sc_agg_xlog_cb.isChecked())
        y_on = bool(getattr(self, "_sc_agg_ylog_cb", None) and self._sc_agg_ylog_cb.isChecked())
        return stat_x + _log1p_axis_suffix(x_on), stat_y + _log1p_axis_suffix(y_on)

    def _run_scatter_agg_job(
        self, grp: BarGroup, tp_h: float, csv_path: Path | None, fig_path: Path | None, fmt: str,
        axes: ScatterAxes,
    ) -> str | None:
        from review.features.scatter.controller import collect_scatter_agg_data as _collect_scatter_agg_data
        stat_x, stat_y = axes.stat_x, axes.stat_y
        with self._app_group_scope(grp):
            scatter_data = _collect_scatter_agg_data(
                self._ctx, stat_x, stat_y, [tp_h],
                well_colors=WELL_COLORS,
            )
        if not scatter_data:
            # ``SkippedJob``, not a plain string: the runner discards the
            # entire staged export for any error, so reporting "this group
            # produced nothing" down that channel deletes every *other*
            # group's files too. Bar and line were converted and these four
            # were not, which is the worse half — one function now held two
            # meanings for the same return and neither looked wrong.
            return SkippedJob(f"{grp.name}: no scatter-aggregate data at t={tp_h:.1f}h")
        try:
            from review.features.export.service import (
                figure_names_map, _well_paths_keys, spread_cell,
                spread_unit_for, well_name_for,
            )
            from review.data.metric_labels import spread_label
            _well_labels = figure_names_map(self._ctx)
            _well_paths = _well_paths_keys(self._ctx)
            # Same two columns the on-tab aggregate CSV carries, because it is
            # the same statistic: without them an ``x_err`` says neither which
            # interval it is nor what it is over.
            _band = spread_label(bool(self._ctx.state.plot.use_sem)).lower()
            _unit = spread_unit_for(self._ctx)
            rows: list[dict] = []
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
                    # ``spread_cell``: an undefined spread — a group of one
                    # replicate — used to reach the file as the literal ``nan``.
                    f"x_{_band}": spread_cell(data['x_err'][0], ".8g"),
                    f"y_{_band}": spread_cell(data['y_err'][0], ".8g"),
                    "spread_unit": _unit,
                })
            if csv_path is not None:
                with open(csv_path, "w", newline="") as fh:
                    fieldnames = ["group", "label", "well_name", "timepoint_h",
                                  "x", "y", f"x_{_band}", f"y_{_band}",
                                  "spread_unit"]
                    writer = csv.DictWriter(fh, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
        except OSError as exc:
            return f"{grp.name} scatter-aggregate CSV: {exc}"
        try:
            _labels = _value_label_settings(self._ctx)

            def _draw(ax):
                for data in scatter_data.values():
                    ax.errorbar(
                        data["x"], data["y"],
                        xerr=_whiskers_or_none(data["x_err"]),
                        yerr=_whiskers_or_none(data["y_err"]),
                        label=data.get("label", ""),
                        color=data["color"], marker=data.get("marker", "o"),
                        linestyle="none", capsize=5, alpha=0.75,
                    )
                self._apply_agg_log_scale(ax)
                # The exported figure carries the same overlay as the tab it
                # was set on — after the log scale, since the headroom the
                # labels need is measured against the limits in force.
                _annotate_agg_points(_labels, ax, scatter_data)
            _xl, _yl = self._agg_axis_labels(stat_x, stat_y)
            self._export_scatter_figure(
                _draw,
                xlabel=_xl, ylabel=_yl,
                title=(f"{grp.name} \u2014 Scatter Aggregate "
                       f"({self._tp_figure_label(tp_h)})"),
                fig_path=fig_path, fmt=fmt, group=grp,
            )
        except Exception as exc:
            return f"{grp.name} scatter-aggregate figure: {exc}"
        return None

    def _run_scatter_cells_multi_tp_job(
        self, grp: BarGroup, timepoints: list[float],
        csv_path: Path | None, fig_path: Path | None, fmt: str,
        axes: ScatterAxes,
    ) -> str | None:
        from review.features.scatter.controller import collect_scatter_data as _collect_scatter_data
        combined_rows: list[dict] = []
        combined_series: list[tuple] = []
        self._warn_if_live_gates_ignored()
        # One definition of X and Y across every timepoint of this job as well
        # as across the run's jobs: the loop below is itself long enough for the
        # combos to move under it.
        col_x, col_y = axes.col_x, axes.col_y
        for tp_h in sorted(timepoints):
            with self._app_group_scope(grp):
                scatter_data = _collect_scatter_data(
                    self._ctx, col_x, col_y, tp_h,
                    well_colors=WELL_COLORS,
                    fluor_gate_x=_get_fluor_gate(self._ctx, axes.ch_x_base),
                    fluor_gate_y=_get_fluor_gate(self._ctx, axes.ch_y_base),
                )
            from review.features.export.service import figure_names_map, well_name_for
            _well_labels = figure_names_map(self._ctx)
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
            # ``SkippedJob``, not a plain string: the runner discards the
            # entire staged export for any error, so reporting "this group
            # produced nothing" down that channel deletes every *other*
            # group's files too. Bar and line were converted and these four
            # were not, which is the worse half — one function now held two
            # meanings for the same return and neither looked wrong.
            return SkippedJob(f"{grp.name}: no scatter-cells data for selected timepoints")
        try:
            if csv_path is not None:
                with open(csv_path, "w", newline="") as fh:
                    fieldnames = ["group", "timepoint_h", "member", "well", "well_name", "x", "y", "filename", "nucleus_id"]
                    writer = csv.DictWriter(fh, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(combined_rows)
        except OSError as exc:
            return f"{grp.name} scatter-cells CSV: {exc}"
        try:
            # Still ``axes``: this second collection feeds the FIGURE while the
            # CSV above was written from the loop, so re-reading the combos here
            # put a figure and its own CSV on different columns inside one job.
            with self._app_group_scope(grp):
                # One collection over all selected timepoints so the figure has
                # per-point tp arrays (Color by time) and proper per-group
                # entries (faceting) \u2014 the CSV above keeps its per-tp rows.
                fig_data = _collect_scatter_data(
                    self._ctx, col_x, col_y, sorted(timepoints),
                    well_colors=WELL_COLORS,
                    fluor_gate_x=_get_fluor_gate(self._ctx, axes.ch_x_base),
                    fluor_gate_y=_get_fluor_gate(self._ctx, axes.ch_y_base),
                )
                self._render_scatter_cells_figure(
                    fig_data, col_x=col_x, col_y=col_y, tps_list=sorted(timepoints),
                    title=f"{grp.name} \u2014 Scatter Cells (all selected tps)",
                    fig_path=fig_path, fmt=fmt, group=grp,
                )
        except Exception as exc:
            return f"{grp.name} scatter-cells figure: {exc}"
        return None

    def _run_scatter_agg_multi_tp_job(
        self, grp: BarGroup, timepoints: list[float],
        csv_path: Path | None, fig_path: Path | None, fmt: str,
        axes: ScatterAxes,
    ) -> str | None:
        from review.features.scatter.controller import collect_scatter_agg_data as _collect_scatter_agg_data
        stat_x, stat_y = axes.stat_x, axes.stat_y
        with self._app_group_scope(grp):
            scatter_data = _collect_scatter_agg_data(
                self._ctx, stat_x, stat_y, sorted(timepoints),
                well_colors=WELL_COLORS,
            )
        if not scatter_data:
            # ``SkippedJob``, not a plain string: the runner discards the
            # entire staged export for any error, so reporting "this group
            # produced nothing" down that channel deletes every *other*
            # group's files too. Bar and line were converted and these four
            # were not, which is the worse half — one function now held two
            # meanings for the same return and neither looked wrong.
            return SkippedJob(f"{grp.name}: no scatter-aggregate data for selected timepoints")
        try:
            from review.features.export.service import (
                figure_names_map, _well_paths_keys, spread_cell,
                spread_unit_for, well_name_for,
            )
            from review.data.metric_labels import spread_label
            _well_labels = figure_names_map(self._ctx)
            _well_paths = _well_paths_keys(self._ctx)
            # Same two columns the on-tab aggregate CSV carries, because it is
            # the same statistic: without them an ``x_err`` says neither which
            # interval it is nor what it is over.
            _band = spread_label(bool(self._ctx.state.plot.use_sem)).lower()
            _unit = spread_unit_for(self._ctx)
            rows: list[dict] = []
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
                    # ``spread_cell``: an undefined spread — a group of one
                    # replicate — used to reach the file as the literal ``nan``.
                    f"x_{_band}": spread_cell(data['x_err'][0], ".8g"),
                    f"y_{_band}": spread_cell(data['y_err'][0], ".8g"),
                    "spread_unit": _unit,
                })
            if csv_path is not None:
                with open(csv_path, "w", newline="") as fh:
                    fieldnames = ["group", "label", "well_name", "timepoint_h",
                                  "x", "y", f"x_{_band}", f"y_{_band}",
                                  "spread_unit"]
                    writer = csv.DictWriter(fh, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
        except OSError as exc:
            return f"{grp.name} scatter-aggregate CSV: {exc}"
        try:
            _labels = _value_label_settings(self._ctx)

            def _draw(ax):
                for data in scatter_data.values():
                    ax.errorbar(
                        data["x"], data["y"],
                        xerr=_whiskers_or_none(data["x_err"]),
                        yerr=_whiskers_or_none(data["y_err"]),
                        label=data.get("label", ""),
                        # ``color`` is the whole point of the multi-timepoint
                        # figure: ``collect_scatter_agg_data`` paints each point
                        # with its timepoint's position on the viridis gradient,
                        # which is what makes a time course readable. Omitting it
                        # let matplotlib assign its own cycle per series, so the
                        # colours in the exported figure said nothing \u2014 and said
                        # something different from the same plot on the tab and
                        # from the single-timepoint export beside it.
                        color=data["color"],
                        marker=data.get("marker", "o"),
                        linestyle="none", capsize=5, alpha=0.75,
                    )
                self._apply_agg_log_scale(ax)
                _annotate_agg_points(_labels, ax, scatter_data)
            _xl, _yl = self._agg_axis_labels(stat_x, stat_y)
            self._export_scatter_figure(
                _draw,
                xlabel=_xl, ylabel=_yl,
                title=f"{grp.name} \u2014 Scatter Aggregate (all selected tps)",
                fig_path=fig_path, fmt=fmt, group=grp, legend_fontsize=7,
            )
        except Exception as exc:
            return f"{grp.name} scatter-aggregate figure: {exc}"
        return None

