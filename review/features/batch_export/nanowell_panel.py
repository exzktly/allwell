"""Nanowells batch export panel (tracker I4).

For every export group, the chosen Nanowells view for each chosen metric — one image file each — and one CSV of
the per-well summary behind them. The figures are drawn by the page's own composition
(``features/nanowell/controller.draw_view``), and the summary is the page's own
(``distribution_compute.wells_summary``), reused rather than re-implemented for the reason the Response mode
gives: a batch twin that draws its own version of a figure drifts from the page the first time either is
fixed, and the drift is invisible.

**What a group is here.** An export group's replicate sets stay replicate sets and its solo wells stand as
their own groups — the grouping the page itself draws (``controller.replicate_groups``). Only wells with a
nanowell table are drawn; a group's other wells are named on the figure (the page's "selected without a
nanowell table" note) and in the CSV, never silently dropped. The **Array map** draws one well, so it writes
one figure per well of the group.

**What is read once, for the whole run.** The dataset's nanowell tables and the metrics at the chosen baseline
are computed once, before the first job; every control is read once too (the runner pumps events between
jobs). The fold change of a nanowell metric is against its own baseline, chosen here, not the plotting band's
control, so the panel offers no fold-change or spread controls.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from review.core.lazy import lazy as _lazy
from review.features.batch_export._common import (
    BarGroup,
    _logger as _shared_logger,
    unique_export_stem,
)
from review.features.batch_export._panels import Panel
from review.features.batch_export.base_panel import BatchExportPanel, SkippedJob
from ui import theme
from widgets.separator import build_hline_separator

# Nanowells is a removable feature: every symbol comes through a lazy proxy, so a build without it still imports
# this module (the tab does not offer the mode there — ``tab_view._offered_modes``).
_draw_view = _lazy("review.features.nanowell.controller", "draw_view")
_resolve_metric = _lazy("review.features.nanowell.controller", "resolve_metric")
_wells_summary = _lazy("review.features.nanowell.distribution_compute", "wells_summary")
_metric_labels = _lazy("review.features.nanowell.distribution_compute", "metric_labels")
_load_nanowell_data = _lazy("review.features.nanowell.collect", "load_nanowell_data")
_site_metrics = _lazy("review.features.nanowell.series_compute", "site_metrics")
_render_message = _lazy("review.features.nanowell.renderer", "render_message")

_logger = logging.getLogger("review")

_FIELD_LABEL_W = 110
#: Rows of the metric list shown before it scrolls; the list holds every page metric (eleven).
_METRIC_ROWS = 6
#: The list's frame is drawn above its first row and below its last.
_FRAME_EDGES = 2

#: The views a batch run can draw, as the page names them. Array QC is the page's alone: it reads a montage per
#: (well, timepoint) and is a check of one acquisition, not a figure for a folder.
VIEWS = (("distribution", "Distribution"), ("array_map", "Array map"), ("trajectories", "Trajectories"),
         ("position", "Position"))
AXES = (("time", "Time"), ("wells", "Wells"))
BASELINES = (("earliest", "Earliest timepoint"), ("first_measurable", "First measurable"))
#: The timepoint Position, *Along x: Wells* and the Array map are drawn at, when no one timepoint is chosen.
LATEST, EARLIEST = "__latest__", "__earliest__"


class NanowellBatchExportPanel(BatchExportPanel):
    """Batch exporter for the Nanowells page."""

    _SOURCE_TAB = "Nanowells"
    _PANELS = (Panel("plot", "Plot"),)
    _SHARE_X = ()
    _X_PANELS = None
    _AXES_FOLLOW_HEADER = False
    _INCLUDES_SPREAD = False
    _INCLUDES_FOLD_CHANGE = False
    _CHANNEL_COMBO_ATTRS = ()

    def _mode_key(self) -> str:
        return "nanowell"

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_output_panel(self, layout: QVBoxLayout) -> None:
        # No AXES block: a Nanowells figure is several panels the page lays out itself, and a limit typed for "the"
        # axis would govern whichever one came first. The base class reads these as "no axis control".
        self._x_min_edit = self._x_max_edit = self._x_scale_cb = None
        # No raw per-cell CSV either: a nanowell's cells are in the run's own cell table, and the figure's numbers
        # are the summary CSV this mode always writes.
        self._cell_rows_cb = None
        self._build_output_header_and_io(layout, title="OUTPUT SETTINGS — NANOWELLS")

        self._view_cb = self._combo_row(layout, "View:", VIEWS,
                                        "The Nanowells view each figure draws, as the page draws it.")
        self._axis_cb = self._combo_row(layout, "Along x:", AXES,
                                        "Distribution only: timepoints along the axis, or wells at one timepoint.")
        self._baseline_cb = self._combo_row(
            layout, "Baseline:", BASELINES,
            "What a fold change is measured from: the well's earliest timepoint, or each nanowell's first "
            "measurable one.")
        self._tp_cb = self._combo_row(
            layout, "Timepoint:", (),
            "Position, Along x: Wells and the Array map draw one timepoint. Latest and Earliest are each well's.")
        self._refresh_timepoints()
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "", min_width=_FIELD_LABEL_W)
        self._cohort_box = QCheckBox("Fixed cohort")
        self._cohort_box.setToolTip("Distribution and Trajectories: only the nanowells with a value at every "
                                    "timepoint of their well, as the page's Fixed cohort box.")
        row.addWidget(self._cohort_box)
        row.addStretch(1)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "Metrics:", min_width=_FIELD_LABEL_W)
        self._metric_list = QListWidget()
        self._metric_list.setToolTip("One figure per ticked metric. Brightness and site signal use the channel "
                                     "chosen in the app's channel selector.")
        for key, label in _metric_labels().items():
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if key == "n" else Qt.Unchecked)
            self._metric_list.addItem(item)
        self._metric_list.setMaximumHeight(self._metric_list.sizeHintForRow(0) * _METRIC_ROWS
                                           + _FRAME_EDGES * self._metric_list.frameWidth())
        row.addWidget(self._metric_list, 1)
        layout.addLayout(row)
        self._view_cb.currentIndexChanged.connect(lambda _i: self._refresh_view_controls())
        self._refresh_view_controls()

        layout.addWidget(build_hline_separator(None))
        self._build_summary_label(layout)
        self._build_run_row(layout, button_text="▶  Run Nanowells Batch Export")
        layout.addStretch(1)

    def _combo_row(self, layout: QVBoxLayout, label: str, choices, tip: str) -> QComboBox:
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, label, min_width=_FIELD_LABEL_W)
        combo = QComboBox()
        for key, text in choices:
            combo.addItem(text, key)
        combo.setToolTip(tip)
        row.addWidget(combo)
        row.addStretch(1)
        layout.addLayout(row)
        return combo

    def _refresh_view_controls(self) -> None:
        """Each view's controls enabled only where they reach its figure, as on the page."""
        view = self._view_cb.currentData()
        self._axis_cb.setEnabled(view == "distribution")
        self._cohort_box.setEnabled(view in ("distribution", "trajectories"))
        self._tp_cb.setEnabled(view in ("distribution", "array_map", "position"))

    def _refresh_timepoints(self) -> None:
        """Latest and Earliest, then the dataset's own timepoints when the Nanowells page has read them."""
        previous = self._tp_cb.currentData()
        self._tp_cb.clear()
        self._tp_cb.addItem("Latest", LATEST)
        self._tp_cb.addItem("Earliest", EARLIEST)
        st = self._ctx.state.feature_slice("nanowell")
        data = getattr(st, "data", None)
        for tp in (data.timepoints() if data is not None else []):
            self._tp_cb.addItem(str(tp), str(tp))
        index = self._tp_cb.findData(previous) if previous else -1
        self._tp_cb.setCurrentIndex(max(index, 0))

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_timepoints()

    # ── the run summary ──────────────────────────────────────────────────
    _SUMMARY_FIGURE_LEAD = "One figure per ticked metric"
    _SUMMARY_FIGURE_NOTE = "the chosen view, as the Nanowells page draws it (the Array map: one per well)"
    _SUMMARY_CSV_LINE = ("One CSV of each well's summary per timepoint and metric — median, quartiles, nanowells "
                         "used and excluded by reason")

    def _extra_summary_signals(self) -> list:
        return [self._view_cb.currentIndexChanged, self._metric_list.itemChanged]

    def _summary_footnotes(self) -> list[str]:
        return ["Wells are the replicate unit: a replicate set's wells are drawn side by side, as on the page.",
                "Brightness and site signal use the channel chosen in the app's channel selector."]

    # ── the run ──────────────────────────────────────────────────────────
    def _selected_metrics(self) -> list[str]:
        return [self._metric_list.item(i).data(Qt.UserRole) for i in range(self._metric_list.count())
                if self._metric_list.item(i).checkState() == Qt.Checked]

    def _run_settings(self) -> dict:
        """Every control, read once."""
        return {"view": self._view_cb.currentData(), "axis": self._axis_cb.currentData(),
                "baseline": self._baseline_cb.currentData(), "timepoint": self._tp_cb.currentData(),
                "cohort": self._cohort_box.isChecked(), "metrics": self._selected_metrics(),
                "fmt": self._fmt_cb.currentText(), "channel": self._ctx.state.channel.active_channel}

    def _load(self, baseline: str) -> tuple | None:
        """``(data, metrics)`` for the open dataset at *baseline*, or ``None`` with the user told why."""
        data_dir = self._ctx.state.dataset.data_dir
        st = self._ctx.state.feature_slice("nanowell")
        data = getattr(st, "data", None)
        if data is None or getattr(st, "loaded_for", (None,))[0] != str(data_dir or ""):
            try:
                data = _load_nanowell_data(Path(data_dir)) if data_dir else None
            except (ValueError, OSError) as exc:            # NanowellSchemeError is a ValueError
                QMessageBox.warning(self, "Nanowell tables unreadable",
                                    f"The nanowell tables could not be read, so nothing was exported:\n\n{exc}")
                return None
        if data is None:
            QMessageBox.warning(self, "No nanowell tables",
                                "This dataset has no nanowell tables (out/nanowells/), so there is nothing to "
                                "export in this mode.")
            return None
        return data, _site_metrics(data, baseline)

    def _run_batch(self) -> None:
        if self._run_is_refused():
            return
        settings = self._run_settings()
        if not settings["metrics"]:
            QMessageBox.warning(self, "No metrics", "Tick at least one metric.")
            return
        groups = [g for g in self._groups_for_export() if g.wells]
        if not groups:
            QMessageBox.warning(self, "No groups", "Define at least one non-empty group.")
            return
        out_dir = self._resolve_out_dir()
        if out_dir is None:
            return
        loaded = self._load(settings["baseline"])
        if loaded is None:
            return
        data, metrics = loaded
        self._log_sample_definitions_snapshot(groups)
        used_stems: set = set()

        def _progress(grp: BarGroup, step: int, total: int) -> str:
            return f"Exporting '{grp.name}' ({step}/{total})…"

        def _run_group(grp: BarGroup, write_dir: Path) -> str | None:
            return self._export_group(grp, write_dir, data, metrics, settings, used_stems)

        self._run_batch_jobs(jobs=groups, progress_text_fn=_progress, run_job_fn=_run_group,
                             success_text=f"✓ {len(groups)} group(s)",
                             status_text=f"Nanowells batch export: {len(groups)} group(s)", out_dir=out_dir)

    def _figure_groups(self, grp: BarGroup, have: set) -> dict:
        """``group -> wells`` for the figure: the export group's replicate sets and solo wells, as the page draws
        a selection, kept to the wells with a nanowell table."""
        out = {}
        for rset in grp.members:
            members = [w for w in rset.wells if w in have]
            if members:
                out[rset.name] = members
        for well in grp.solo_wells:
            if well in have and not any(well in m for m in out.values()):
                out[well] = [well]
        return out

    def _export_group(self, grp: BarGroup, write_dir: Path, data, metrics, settings: dict,
                      used_stems: set) -> str | None:
        have = set(data.wells)
        groups = self._figure_groups(grp, have)
        tableless = [w for w in grp.wells if w not in have]
        if not groups:
            return SkippedJob(f"{grp.name}: none of its wells has a nanowell table — nothing exported")
        safe = unique_export_stem("nanowell_" + re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name), used_stems)
        view, fmt = settings["view"], settings["fmt"]
        wells = [w for members in groups.values() for w in members]
        tables, labels = [], _metric_labels()
        for metric in settings["metrics"]:
            drawn, channel, note = _resolve_metric(view, metric, settings["channel"], data.channels)
            targets = wells if view == "array_map" else [None]
            for well in targets:
                stem = f"{safe}_{metric}" + (f"_{well}" if well else "")
                fig = self._new_figure()
                shown = self._timepoint_for(data, [well] if well else wells, settings)
                try:
                    if well and shown not in data.timepoints(well):
                        # The page's map falls back to the well's first timepoint; a batch run was asked for this
                        # one, so the figure says the well was not imaged then rather than drawing another, or
                        # the map's "no addressed nanowells" placeholder, which would be a different claim.
                        _render_message(fig, f"{well} has no timepoint {shown}: it was not imaged then, so there "
                                             "is no map to draw.", gid="nw-map-missing")
                    else:
                        _draw_view(fig, view, metrics, data, groups=groups, metric=drawn, channel=channel,
                                   axis=settings["axis"], timepoint=self._timepoint_for(data, wells, settings),
                                   control="", baseline=settings["baseline"], cohort=settings["cohort"],
                                   well=well or "", well_timepoint=shown if well else "",
                                   note=note, tableless=tableless)
                    self._figure_view = {"view": view, "metric": drawn, "axis": settings["axis"],
                                         "baseline": settings["baseline"], "cohort": settings["cohort"],
                                         "well": well or "", "timepoint": shown}
                    self._save_figure(fig, self._out_path(write_dir, f"{stem}.{fmt}"), fmt, group=grp,
                                      title=f"{grp.name} — {labels[drawn]}" + (f" — {well}" if well else ""))
                except Exception as exc:
                    return f"{grp.name} {metric} figure: {exc}"
            table = _wells_summary(metrics, wells, drawn, channels=data.channels, channel=channel,
                                   cohort=settings["cohort"] and view in ("distribution", "trajectories"))
            tables.append(table.assign(metric=drawn, channel=channel or "", requested_metric=metric,
                                       group=grp.name, note=note))
        try:
            self._write_summary(self._out_path(write_dir, f"{safe}_summary.csv"), tables, grp, tableless,
                                settings)
        except OSError as exc:
            return f"{grp.name} CSV: {exc}"
        _shared_logger.info("Batch: finished %s", safe)
        return None

    def _capture_group_params(self, grp, val_col):
        """The base capture, taken with the Nanowells page's choices set to this figure's.

        ``capture_params`` snapshots the page's live state (``view_state``), which holds whatever view and metric
        the page was left on; captured as-is, every record in the gallery would restore to the page's figure
        rather than to the one beside it. Set for the capture and put back, so the page is not moved by a run.
        """
        st = self._ctx.state.feature_slice("nanowell")
        chosen = getattr(self, "_figure_view", None)
        if st is None or not chosen:
            return super()._capture_group_params(grp, val_col)
        saved = {key: getattr(st, key) for key in chosen}
        try:
            for key, value in chosen.items():
                setattr(st, key, value)
            return super()._capture_group_params(grp, val_col)
        finally:
            for key, value in saved.items():
                setattr(st, key, value)

    @staticmethod
    def _timepoint_for(data, wells, settings: dict) -> str:
        """The timepoint drawn: the one chosen, or the latest / earliest any of *wells* has."""
        chosen = settings["timepoint"]
        if chosen not in (LATEST, EARLIEST):
            return str(chosen)
        tps = [tp for tp in data.timepoints() if any(tp in data.timepoints(w) for w in wells)]
        if not tps:
            return ""
        return tps[0] if chosen == EARLIEST else tps[-1]

    def _new_figure(self):
        from matplotlib.figure import Figure

        return Figure(figsize=(10, 6.5), dpi=300, facecolor=theme.CPub.bg)

    def _write_summary(self, path, tables, grp: BarGroup, tableless, settings: dict) -> None:
        """One row per (metric, well, timepoint), the exclusions spelled out per reason, and a row per well of the
        group with no nanowell table — the omission is a row, not an absence."""
        import pandas as pd

        if path is None:
            return
        frame = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
        if len(frame):
            frame["excluded"] = ["; ".join(f"{reason}: {n}" for reason, n in sorted(d.items())) for d in
                                 frame["excluded"]]
        frame["baseline"] = settings["baseline"]
        rows = [frame] + [pd.DataFrame([{"group": grp.name, "well": w, "note": "no nanowell table"}])
                          for w in tableless]
        out = pd.concat(rows, ignore_index=True)
        for column in ("n_sites", "n_used", "tp_order"):       # counts stay counts beside the rows with none
            if column in out.columns:
                out[column] = out[column].astype("Int64")
        lead = ["group", "metric", "channel", "well", "timepoint", "time_h", "baseline"]
        out = out[[c for c in lead if c in out.columns] + [c for c in out.columns if c not in lead]]
        tmp = Path(str(path) + ".part")
        out.to_csv(tmp, index=False)
        tmp.replace(path)


def build_nanowell_batch_panel(ctx, parent: QWidget | None = None):
    """Construct the panel. A function so the tab builder needs no import."""
    return NanowellBatchExportPanel(ctx, parent, use_sidebar_groups=False)
