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
    QFormLayout,
    QLabel,
    QHBoxLayout,
    QLineEdit,
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
from widgets.spin_box import SpinBox

# Nanowells is a removable feature: every symbol comes through a lazy proxy, so a build without it still imports
# this module (the tab does not offer the mode there — ``tab_view._offered_modes``).
_draw_view = _lazy("review.features.nanowell.controller", "draw_view")
_resolve_metric = _lazy("review.features.nanowell.controller", "resolve_metric")
_batch_array_layers = _lazy("review.features.nanowell.controller", "batch_array_layers")
_wells_summary = _lazy("review.features.nanowell.distribution_compute", "wells_summary")
_metric_labels = _lazy("review.features.nanowell.distribution_compute", "metric_labels")
_metric_column = _lazy("review.features.nanowell.distribution_compute", "metric_column")
_match_channel = _lazy("review.features.nanowell.distribution_compute", "match_channel")
_moving_window_for = _lazy("review.features.nanowell.distribution_compute", "moving_window_for")
_moving_max = _lazy("review.features.nanowell.moving_compute", "max_window")
_baseline_window_for = _lazy("review.features.nanowell.moving_compute", "baseline_window_for")
_base_value = _lazy("review.features.nanowell.moving_compute", "base_value")
_format_cut = _lazy("review.features.nanowell.distribution_compute", "format_cut")
_parse_threshold = _lazy("review.features.nanowell.controller", "parse_threshold")
_load_nanowell_data = _lazy("review.features.nanowell.collect", "load_nanowell_data")
_site_metrics = _lazy("review.features.nanowell.series_compute", "site_metrics")
_gate_tally = _lazy("review.features.nanowell.controller", "gate_tally")
_gate_note = _lazy("review.features.nanowell.cell_gate_compute", "note")
_focus_note = _lazy("review.domain.nanowell_rim_focus", "note")
_focus_rule = _lazy("review.features.nanowell.controller", "focus_rule")
_baseline_at = _lazy("review.features.nanowell.series_compute", "baseline_at")
_render_message = _lazy("review.features.nanowell.renderer", "render_message")
_apply_subset = _lazy("review.features.nanowell.subset_compute", "apply")
_subset_note = _lazy("review.features.nanowell.subset_compute", "note")
_no_subset = _lazy("review.features.nanowell.subset_compute", "Subset")

_logger = logging.getLogger("review")

_FIELD_LABEL_W = 110
#: Rows of the metric list shown before it scrolls; the list holds every page metric (eleven).
_METRIC_ROWS = 6
#: The list's frame is drawn above its first row and below its last.
_FRAME_EDGES = 2

#: The views a batch run can draw, as the page names them. The Array view is drawn as its lattice map — the
#: nanowells at their lattice places, filled by the metric (``ArrayLayers.preset("map")``): its image layers read a
#: montage or the raw tiles per (well, timepoint) and are a check of one acquisition, not a figure for a folder.
#: The page's Extremes and Identity views are not offered. Identity draws no metric — every figure here is one per
#: ticked metric — and it is one figure per (well, timepoint) against that well's reference; two of its four modes
#: (Blink, Side by side) exist only as interaction. ``draw_view(view="identity")`` draws its Distance map from the
#: sidecar on disk when asked without the page's inputs, so offering it later is a roster entry and a per-timepoint
#: loop, not a second implementation.
VIEWS = (("distribution", "Distribution"), ("array", "Array map"), ("trajectories", "Trajectories"),
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
            "What a fold change is measured from: the well's earliest timepoint, each nanowell's first "
            "measurable one, or one timepoint of the dataset for every nanowell (a well without it has no fold "
            "change).")
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
        # The fields hold the bare number; the label beside each says what it counts (no text inside the box,
        # which Qt's spin box refuses to type over).
        row.addWidget(QLabel("Moving average (timepoints, 1 = off)"))
        self._moving_spin = SpinBox()
        self._moving_spin.setRange(1, _moving_max())       # 1 is off, as on the page
        self._moving_spin.setToolTip("Distribution and Trajectories: each nanowell's value as the mean over this "
                                     "many timepoints ending at it, as the page's Moving average control. The "
                                     "summary CSV's moving_average column says the window each row used.")
        row.addWidget(self._moving_spin)
        row.addWidget(QLabel("Baseline average (timepoints, 1 = off)"))
        self._baseline_moving_spin = SpinBox()
        self._baseline_moving_spin.setRange(1, _moving_max())
        self._baseline_moving_spin.setToolTip(
            "With the moving average on, a fold change's baseline as the mean over this many timepoints ending at "
            "the baseline timepoint, as the page's Baseline average control. Not used with the Earliest or First "
            "measurable baseline, which have no measured timepoint before them. The summary CSV's baseline_moving_average column says the window each "
            "row used.")
        row.addWidget(self._baseline_moving_spin)
        row.addStretch(1)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "Subset:", min_width=_FIELD_LABEL_W)
        self._subset_box = QCheckBox("Use the page's subset")
        self._subset_box.setChecked(True)
        self._subset_box.setToolTip("Draw and summarise only the nanowells in the Nanowells page's Subset — its "
                                    "starting cells, metric, position and imaging-field conditions — as the page "
                                    "does. Off: every nanowell. Build the subset on the page.")
        self._subset_label = QLabel("")
        self._subset_label.setObjectName("InfoHint")
        self._subset_label.setWordWrap(True)
        self._subset_box.toggled.connect(lambda _on: self._seed_subset())
        row.addWidget(self._subset_box)
        row.addWidget(self._subset_label, 1)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "Metrics:", min_width=_FIELD_LABEL_W)
        self._metric_list = QListWidget()
        self._metric_list.setToolTip("One figure per ticked metric. Cell fluorescence and nanowell fluorescence use the channel "
                                     "chosen in the app's channel selector; each figure's axis names the "
                                     "background they were measured above.")
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

        # One "responding above" cut per ticked metric, and every ticked metric must have one (tracker I9): the
        # summary CSV's fraction responding is computed at it, and there is no default cut to fall back on.
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "Responding above:", min_width=_FIELD_LABEL_W)
        self._threshold_host = QWidget()
        self._threshold_form = QFormLayout(self._threshold_host)
        self._threshold_form.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._threshold_host, 1)
        layout.addLayout(row)
        self._threshold_edits: dict = {}
        self._threshold_text: dict = {}
        self._metric_list.itemChanged.connect(lambda _item: self._rebuild_thresholds())
        self._rebuild_thresholds()
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

    def _rebuild_thresholds(self) -> None:
        """A cut field for each ticked metric. One left empty is seeded from the page's cut for it on the shared
        channel — again each time the panel is shown, since the page's cut may be set after the panel is built —
        and what was typed survives a metric being unticked and ticked again."""
        for key, edit in self._threshold_edits.items():
            self._threshold_text[key] = edit.text()
        while self._threshold_form.rowCount():
            self._threshold_form.removeRow(0)
        self._threshold_edits = {}
        labels = _metric_labels()
        for key in self._selected_metrics():
            edit = QLineEdit(self._threshold_text.get(key) or self._page_threshold(key))
            edit.setPlaceholderText("required")
            edit.setToolTip(f"A nanowell counts as responding on {labels[key]} when its value is above this.")
            self._threshold_edits[key] = edit
            self._threshold_form.addRow(labels[key], edit)

    def _page_threshold(self, metric: str) -> str:
        """The Nanowells page's cut for *metric* on the shared channel, as text, or ``""``."""
        st = self._ctx.state.feature_slice("nanowell")
        data = getattr(st, "data", None)
        try:
            channel = _match_channel(self._ctx.state.channel.active_channel, data.channels) if data else None
            cut = getattr(st, "thresholds", {}).get(_metric_column(metric, channel))
        except ValueError:                               # a channel metric with no channel to resolve
            return ""
        return "" if cut is None else _format_cut(cut)

    def _thresholds(self) -> tuple[dict, list]:
        """``(metric -> cut, the ticked metrics without a readable cut)``."""
        cuts, missing = {}, []
        for key, edit in self._threshold_edits.items():
            value = _parse_threshold(edit.text())
            if value is None:
                missing.append(key)
            else:
                cuts[key] = value
        return cuts, missing

    def _refresh_view_controls(self) -> None:
        """Each view's controls enabled only where they reach its figure, as on the page."""
        view = self._view_cb.currentData()
        self._axis_cb.setEnabled(view == "distribution")
        self._cohort_box.setEnabled(view in ("distribution", "trajectories"))
        self._moving_spin.setEnabled(view in ("distribution", "trajectories"))
        self._baseline_moving_spin.setEnabled(view in ("distribution", "trajectories"))
        self._tp_cb.setEnabled(view in ("distribution", "array", "position"))

    def _refresh_timepoints(self) -> None:
        """Latest and Earliest, then the dataset's own timepoints when the Nanowells page has read them — for the
        Timepoint control and, after its two rules, for the Baseline control."""
        previous = self._tp_cb.currentData()
        previous_baseline = self._baseline_cb.currentData()
        self._tp_cb.clear()
        self._tp_cb.addItem("Latest", LATEST)
        self._tp_cb.addItem("Earliest", EARLIEST)
        self._baseline_cb.clear()
        for key, text in BASELINES:
            self._baseline_cb.addItem(text, key)
        st = self._ctx.state.feature_slice("nanowell")
        data = getattr(st, "data", None)
        for tp in (data.timepoints() if data is not None else []):
            self._tp_cb.addItem(str(tp), str(tp))
            self._baseline_cb.addItem(f"Timepoint {tp}", _baseline_at(tp))
        index = self._tp_cb.findData(previous) if previous else -1
        self._tp_cb.setCurrentIndex(max(index, 0))
        index = self._baseline_cb.findData(previous_baseline) if previous_baseline else -1
        self._baseline_cb.setCurrentIndex(max(index, 0))

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_timepoints()
        self._rebuild_thresholds()
        self._seed_subset()

    def _seed_subset(self) -> None:
        """Say which subset a run will use: the page's, read each time the panel is shown and again at the run."""
        subset = self._subset()
        self._subset_label.setText(subset.label() if subset.is_active() else
                                   "every nanowell" if self._subset_box.isChecked() else
                                   "every nanowell (the page's subset is not used)")

    def _subset(self):
        """The subset the run draws with: the page's while the box is ticked, otherwise none."""
        st = self._ctx.state.feature_slice("nanowell")
        subset = getattr(st, "subset", None)
        return subset if self._subset_box.isChecked() and subset is not None else _no_subset()

    # ── the run summary ──────────────────────────────────────────────────
    _SUMMARY_FIGURE_LEAD = "One figure per ticked metric"
    _SUMMARY_FIGURE_NOTE = "the chosen view, as the Nanowells page draws it (the Array map: one per well)"
    _SUMMARY_CSV_LINE = ("One CSV of each well's summary per timepoint and metric — median, quartiles, the "
                         "fraction responding above each metric's cut, nanowells used and excluded by reason")

    def _extra_summary_signals(self) -> list:
        return [self._view_cb.currentIndexChanged, self._metric_list.itemChanged]

    def _summary_footnotes(self) -> list[str]:
        return ["Wells are the replicate unit: a replicate set's wells are drawn side by side, as on the page.",
                "Cell fluorescence and nanowell fluorescence use the channel chosen in the app's channel selector; "
                "each figure's axis names the background they were measured above."]

    # ── the run ──────────────────────────────────────────────────────────
    def _selected_metrics(self) -> list[str]:
        return [self._metric_list.item(i).data(Qt.UserRole) for i in range(self._metric_list.count())
                if self._metric_list.item(i).checkState() == Qt.Checked]

    def _run_settings(self) -> dict:
        """Every control, read once."""
        return {"view": self._view_cb.currentData(), "axis": self._axis_cb.currentData(),
                "baseline": self._baseline_cb.currentData(), "timepoint": self._tp_cb.currentData(),
                "cohort": self._cohort_box.isChecked(), "metrics": self._selected_metrics(),
                "moving": self._moving_spin.value() if self._moving_spin.value() >= 2 else 0,
                "baseline_moving": (self._baseline_moving_spin.value()
                                    if self._baseline_moving_spin.value() >= 2 else 0),
                "fmt": self._fmt_cb.currentText(), "channel": self._ctx.state.channel.active_channel,
                "thresholds": self._thresholds()[0], "subset": self._subset()}

    def _load(self, baseline: str, wells) -> tuple | None:
        """``(data, metrics, gate_note)`` for the open dataset's *wells* at *baseline*, or ``None`` with the user
        told why.

        The Nanowells page reads only the wells it draws, so its tables are used only when they hold every one of
        *wells* that has a table; otherwise exactly *wells* are read here. The cells Home's Cell Gating leaves out
        are taken out of the counts and brightness exactly as on the page (``cell_gate_compute``), and
        *gate_note* is the sentence saying so (``""`` when it leaves nothing out), followed by the rim focus rule's
        sentence: the nanowells left out as out of focus are left out under the page's own rule
        (``review.domain.nanowell_rim_focus``, the QC layer's switch), so an export never counts a nanowell the page
        shows as soft."""
        data_dir = self._ctx.state.dataset.data_dir
        st = self._ctx.state.feature_slice("nanowell")
        data = getattr(st, "data", None)
        loaded_for = getattr(st, "loaded_for", None) or (None,)      # None until the page has loaded (X16)
        covers = data is not None and set(wells) & set(data.available) <= set(data.wells)
        if not covers or loaded_for[0] != str(data_dir or ""):
            try:
                data = _load_nanowell_data(Path(data_dir), wells=sorted(set(wells))) if data_dir else None
            except (ValueError, OSError) as exc:            # NanowellSchemeError is a ValueError
                QMessageBox.warning(self, "Nanowell tables unreadable",
                                    f"The nanowell tables could not be read, so nothing was exported:\n\n{exc}")
                return None
        if data is None:
            QMessageBox.warning(self, "No nanowell tables",
                                "This dataset has no nanowell tables (out/nanowells/), so there is nothing to "
                                "export in this mode.")
            return None
        if not len(data.sites):
            # Only the batch's wells are read, so none of them having a table leaves no site at all, and the
            # metrics cannot be computed over nothing (they raised KeyError: 'state').
            QMessageBox.warning(self, "No nanowell tables",
                                "None of the groups' wells has a nanowell table (out/nanowells/), so there is "
                                "nothing to export in this mode.")
            return None
        gate = _gate_tally(self._ctx, data)
        rule = _focus_rule(self._ctx)
        metrics = _site_metrics(data, baseline, gate, rule)
        note = " ".join(filter(None, [_gate_note(gate), _focus_note(metrics, rule, wells)]))
        return data, metrics, note

    def _run_batch(self) -> None:
        if self._run_is_refused():
            return
        settings = self._run_settings()
        if not settings["metrics"]:
            QMessageBox.warning(self, "No metrics", "Tick at least one metric.")
            return
        _cuts, missing = self._thresholds()
        if missing:
            labels = _metric_labels()
            QMessageBox.warning(self, "Responding above not set",
                                "Set a number in Responding above for each ticked metric — the summary's fraction "
                                "responding is computed at it, and there is no default:\n\n"
                                + "\n".join(labels[m] for m in missing))
            return
        groups = [g for g in self._groups_for_export() if g.wells]
        if not groups:
            QMessageBox.warning(self, "No groups", "Define at least one non-empty group.")
            return
        out_dir = self._resolve_out_dir()
        if out_dir is None:
            return
        loaded = self._load(settings["baseline"], {w for g in groups for w in g.wells})
        if loaded is None:
            return
        data, metrics, gate_note = loaded
        settings = {**settings, "gate_note": gate_note}
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
        a selection, kept to the wells with a nanowell table. The page's *Exclude* list is not applied: it is the
        page's own choice of wells and leaves the well in the rest of Review (the manual says so), and a batch group
        is the wells the user put in it (tracker L16, declined)."""
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
            note = " ".join(filter(None, [note, settings.get("gate_note", "")]))
            # The cut was set for the ticked metric. When another is drawn in its place (a channel metric with no
            # channel), applying it there would count against a number chosen for something else.
            cut = settings["thresholds"].get(metric) if drawn == metric else None
            targets = wells if view == "array" else [None]
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
                                             "is no map to draw.", gid="nw-array-missing")
                    else:
                        _draw_view(fig, view, metrics, data, groups=groups, metric=drawn, channel=channel,
                                   axis=settings["axis"], timepoint=self._timepoint_for(data, wells, settings),
                                   control="", baseline=settings["baseline"], cohort=settings["cohort"],
                                   moving=settings["moving"], baseline_moving=settings["baseline_moving"],
                                   well=well or "", well_timepoint=shown if well else "",
                                   note=note, tableless=tableless, responding_above=cut,
                                   subset=settings["subset"])
                    self._figure_view = {"view": view, "metric": drawn, "axis": settings["axis"],
                                         "baseline": settings["baseline"], "cohort": settings["cohort"],
                                         "moving": settings["moving"],
                                         "baseline_moving": settings["baseline_moving"],
                                         "well": well or "", "timepoint": shown,
                                         "subset": settings["subset"]}
                    if view == "array":                  # a gallery record restores the layers it was drawn with
                        self._figure_view["layers"] = _batch_array_layers()
                    self._save_figure(fig, self._out_path(write_dir, f"{stem}.{fmt}"), fmt, group=grp,
                                      title=f"{grp.name} — {labels[drawn]}" + (f" — {well}" if well else ""))
                except Exception as exc:
                    return f"{grp.name} {metric} figure: {exc}"
            # The summary is of the nanowells the figure drew: the Subset first, as on the page.
            kept, filtered = _apply_subset(metrics, settings["subset"], settings["baseline"], wells, data)
            said = " ".join(filter(None, [_subset_note(filtered) if filtered is not None else "", note]))
            over_time = view in ("distribution", "trajectories")
            moving = _moving_window_for(drawn, settings["moving"]) if over_time else 0
            column = _metric_column(drawn, channel)
            base_moving = (_baseline_window_for(moving, settings["baseline_moving"], settings["baseline"])
                           if _base_value(column) is not None else 0)
            table = (_wells_summary(kept, wells, drawn, channels=data.channels, channel=channel,
                                    cohort=settings["cohort"] and over_time, responding_above=cut, moving=moving,
                                    baseline_moving=base_moving)
                     if filtered is None or filtered.kept else None)
            if table is not None:
                tables.append(table.assign(metric=drawn, channel=channel or "", requested_metric=metric,
                                           group=grp.name, note=said, responding_above=cut,
                                           moving_average=moving or None,
                                           baseline_moving_average=base_moving or None))
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
        frame["subset"] = settings["subset"].label() if settings["subset"].is_active() else ""
        rows = [frame] + [pd.DataFrame([{"group": grp.name, "well": w, "note": "no nanowell table"}])
                          for w in tableless]
        out = pd.concat(rows, ignore_index=True)
        for column in ("n_sites", "n_used", "tp_order", "moving_average",
                       "baseline_moving_average"):                                # counts stay counts
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
