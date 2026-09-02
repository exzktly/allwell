"""Response-curve batch export panel.

One dose-response figure and one CSV per export group, from the same collector
and the same renderer the Response Curves tab draws with
(``features/response/collect.collect_response`` and
``features/response/renderer.draw_response``). Reused rather than
re-implemented, for the reason the line mode reuses ``render_line_series``: a
batch twin that draws its own version of a figure drifts from the tab the first
time either is fixed, and the drift is invisible — both figures look like
response curves.

**A group is scoped, not filtered.** ``collect_response`` takes its members from
``plot_members.response_members``, which reads the application's live replicate
sets. ``BatchExportPanel._app_group_scope`` swaps those for the export group's
own members for the duration of one job, so each figure is the curve the tab
would draw if that group were the only thing selected — including the fallback
to one member per lit well, and including the crossing a *Group by* performs.

**What this mode does not carry, and why.** Fold change reaches a response curve
through ``domain.fold_change``'s shared state, read inside the collector, the
same state the Bar and Line tabs share; unlike the other batch modes there is no
panel-local copy to configure, so this panel offers no fold-change controls and
says where the setting lives instead. A control that does not reach the export
it appears to configure is worse than no control: the export is wrong and the
panel says it is right.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import theme

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from well_viewer.core.lazy import lazy as _lazy
from well_viewer.core.ui_helpers import build_hline_separator
from well_viewer.features.batch_export._common import (
    BarGroup,
    _groups_with_loaded_wells,
    _logger as _shared_logger,
    unique_export_stem,
)
from well_viewer.features.batch_export.base_panel import BatchExportPanel, SkippedJob

# Response Curves is a removable feature, so every symbol comes through a lazy
# proxy — a call, not an ``import`` statement, so a build without the feature
# still imports this module. The Batch Export tab does not offer the mode at all
# in such a build (``tab_view._offered_modes``), so these are never resolved
# there.
_collect_response = _lazy("well_viewer.features.response.collect",
                          "collect_response")
_fit_curves = _lazy("well_viewer.features.response.collect", "fit_curves")
_draw_response = _lazy("well_viewer.features.response.renderer", "draw_response")
_measurement_title = _lazy("well_viewer.features.response.controller",
                           "response_measurement_title")
_y_label = _lazy("well_viewer.features.response.controller", "response_y_label")
_fit_rows = _lazy("well_viewer.features.response.controller",
                  "response_fit_rows")
_fit_model_labels = _lazy("well_viewer.features.response.controller",
                          "fit_model_labels")
_blank_if_undefined = _lazy("well_viewer.features.response.controller",
                            "blank_if_undefined")
_y_metric_for = _lazy("well_viewer.features.response.collect", "y_metric_for")

_logger = logging.getLogger("well_viewer")

#: Pixel geometry for this panel's controls, named rather than written inline
#: (ARCHITECTURE §8).
_FIELD_LABEL_W = 110
_VAR_COMBO_MIN_W = 180
_BOUND_EDIT_W = 90

#: What the Series combo says when the figure is one curve per timepoint.
_NO_SERIES = "None"

#: The three answers to "is the x axis logarithmic", and what each passes to
#: ``draw_response``. ``None`` is the renderer's own "ask the data", which turns
#: the axis on once the positive doses span two decades — the same rule the tab
#: applies to an untouched preference. Offered explicitly rather than inherited
#: from the tab's per-figure preference, because a batch figure has no figure on
#: screen for that preference to be attached to.
_LOG_X_CHOICES = (
    ("Auto (from the doses)", None),
    ("On", True),
    ("Off", False),
)


def _parse_bound(text: str) -> float | None:
    """A pinned plateau, or ``None`` for a blank or unreadable field.

    Unreadable frees the parameter rather than keeping a previous value, which
    is what ``controller.set_fit_bound`` does for the tab's own fields: a user
    who typed ``1OO`` and got a curve fitted to a top of 100 would have no way
    to tell the figure was not answering the question they asked.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class ResponseBatchExportPanel(BatchExportPanel):
    """Batch exporter for Response Curves."""

    _SOURCE_TAB = "Response Curves"

    #: The error band and the per-FOV replicate unit both reach the curves —
    #: ``collect_response`` reads ``use_sem`` and ``use_fov_replicates`` off the
    #: shared plot state and picks its stats producer from the second — so both
    #: controls belong here and both do something.
    _INCLUDES_SPREAD = True

    #: See the module docstring: fold change reaches this collector through the
    #: shared state, so a panel-local control would configure nothing.
    _INCLUDES_FOLD_CHANGE = False

    _CHANNEL_COMBO_ATTRS = (
        "_response_channel_cb", "_line_channel_cb", "_bar_channel_cb",
    )

    def _mode_key(self) -> str:
        return "response"

    def _fold_change_control_refs(self) -> list:
        """The **live** shared controls, not this panel's construction snapshot.

        ``collect_response`` normalises through ``fold_change.fold_change_state``,
        which reads ``ctx.state.plot`` at the moment of the call — so the
        control this export normalises by is whatever the plotting band holds
        when Run is pressed, not what it held when this panel was first built.
        The base class's snapshot is right for every mode that carries its own
        Control combo and wrong for this one, which carries none: a control set
        on the band *after* opening this mode would be absent from the group
        scope, ``resolve_control_wells`` would resolve it to no wells, and every
        point would drop out of a figure whose axis label still says it was
        normalised.

        Read at scope-entry time rather than snapshotted at the top of the run:
        one job's scope and the collector inside it must agree about the
        control, and they are microseconds apart. What a user changes mid-run is
        the same hazard the rest of this panel snapshots against — but the
        collector cannot be made to read a snapshot, so the honest answer is to
        make the scope read what the collector reads.
        """
        plot = self._ctx.state.plot
        return [plot.fc_control, *(plot.fc_control_map or {}).values()]

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_output_panel(self, layout: QVBoxLayout) -> None:
        self._build_output_header_and_io(
            layout, title="OUTPUT SETTINGS — RESPONSE CURVES")
        # ``include_fraction``: the fraction above the ON cutoff is often the
        # readout a dose response is actually about, and an EC50 fitted to it is
        # well defined — which is why the tab offers it and why an export that
        # could not would be the tab's figure minus its point.
        self._build_channel_row(layout, attr="_response_channel_cb",
                                include_fraction=True)

        self._build_variable_rows(layout)
        self._build_fit_rows(layout)

        layout.addWidget(build_hline_separator(None))

        self._build_timepoints_section(layout)
        self._init_timepoints()

        layout.addWidget(build_hline_separator(None))

        info = QLabel(
            "Each export group produces:\n"
            "  • One response figure — a curve per timepoint (and per "
            "series value, when one is chosen)\n"
            "  • One CSV of the drawn points, the members that could not "
            "be placed, and each curve's fit\n\n"
            "A point is one member of the group at one dose; the x value is the "
            "one every well of that member shares.\n"
            "Fold change follows the shared plotting setting, not this panel — "
            "set it on the plotting band."
        )
        info.setWordWrap(True)
        info.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(info)

        self._build_run_row(layout, button_text="▶  Run Response Batch Export")
        layout.addStretch(1)

    def _build_variable_rows(self, layout: QVBoxLayout) -> None:
        """The x variable, the series split, and the x axis scale."""
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "X variable:", min_width=_FIELD_LABEL_W)
        self._x_var_cb = QComboBox()
        self._x_var_cb.setMinimumWidth(_VAR_COMBO_MIN_W)
        self._x_var_cb.setToolTip(
            "The Number sample variable the curves are plotted against.\n"
            "Text variables group and colour perfectly well but have nowhere to "
            "sit on a numeric axis, so they are not offered here.")
        row.addWidget(self._x_var_cb)

        self._add_bold_label(row, "Series:")
        self._series_var_cb = QComboBox()
        self._series_var_cb.setMinimumWidth(_VAR_COMBO_MIN_W)
        self._series_var_cb.setToolTip(
            "A second variable whose values split each timepoint into separate "
            "curves — one line per (timepoint, value).\n"
            "Without it, two conditions dosed on one plate are drawn as a "
            "single line doubling back at every dose.")
        row.addWidget(self._series_var_cb)
        row.addStretch(1)
        layout.addLayout(row)
        self._x_var_cb.currentIndexChanged.connect(
            lambda _i: self._refresh_series_combo())
        self._refresh_variable_combos()

        scale_row = QHBoxLayout()
        scale_row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(scale_row, "X scale:", min_width=_FIELD_LABEL_W)
        self._log_x_cb = QComboBox()
        for label, _choice in _LOG_X_CHOICES:
            self._log_x_cb.addItem(label)
        self._log_x_cb.setToolTip(
            "Auto turns the axis logarithmic once the positive doses span two "
            "decades, which is the rule the tab applies to a figure nobody has "
            "set the scale on.")
        scale_row.addWidget(self._log_x_cb)
        scale_row.addStretch(1)
        layout.addLayout(scale_row)

    def _build_fit_rows(self, layout: QVBoxLayout) -> None:
        """The fit model and its pinned plateaus, shared by every curve."""
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "Fit:", min_width=_FIELD_LABEL_W)
        self._fit_model_cb = QComboBox()
        self._fit_models: list[str] = []
        for label, model in _fit_model_labels():
            self._fit_model_cb.addItem(label)
            self._fit_models.append(model)
        self._fit_model_cb.setToolTip(
            "Off by default: a fitted line through points is an assertion about "
            "the shape of the response, and it should be the user who makes "
            "it.\nA curve that cannot be fitted draws no line and says why, on "
            "the figure and in the CSV.")
        row.addWidget(self._fit_model_cb)

        row.addWidget(QLabel("Top:"))
        self._fit_top_edit = QLineEdit()
        self._fit_top_edit.setFixedWidth(_BOUND_EDIT_W)
        self._fit_top_edit.setPlaceholderText("free")
        row.addWidget(self._fit_top_edit)
        row.addWidget(QLabel("Bottom:"))
        self._fit_bottom_edit = QLineEdit()
        self._fit_bottom_edit.setFixedWidth(_BOUND_EDIT_W)
        self._fit_bottom_edit.setPlaceholderText("free")
        row.addWidget(self._fit_bottom_edit)
        row.addStretch(1)
        layout.addLayout(row)

    def _init_timepoints(self) -> None:
        """Fill the timepoint list from the dataset, everything selected.

        Everything, unlike the tab — which opens on the shared focus timepoint
        alone because twelve overlapping curves per condition is not a plot
        anyone can read. A batch run writes one *file* per group and the reader
        is choosing what to export rather than what to look at, so the default
        that loses least is all of them; the All / None buttons above the list
        are one click either way.
        """
        from well_viewer.core.timepoint_sync import plottable_timepoints

        self._tp_lb.clear()
        try:
            timepoints = [float(t) for t in plottable_timepoints(self._ctx)]
        except Exception:
            _logger.exception("Response batch export: reading timepoints failed")
            timepoints = []
        for tp in sorted(set(timepoints)):
            self._tp_lb.addItem(QListWidgetItem(f"{tp:g}"))
        self._tp_select_all()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Variables and timepoints both arrive with a dataset, which is
        # routinely opened after this panel was built. Refreshed on the way in
        # for the same reason the channel combos are: a panel offering the
        # variables of the previous dataset exports against a variable this
        # plate does not have, and the empty figure blames the user's fill.
        self._refresh_variable_combos()
        if self._tp_lb.count() == 0:
            self._init_timepoints()

    # ── variables ────────────────────────────────────────────────────────
    def _variable_set(self):
        return getattr(self._ctx.state.selection, "variables", None)

    def _numeric_variables(self) -> list:
        """The plate's Number variables — the only ones with an axis."""
        from well_viewer.domain.sample_variables import NUMBER

        vset = self._variable_set()
        return [v for v in (getattr(vset, "variables", ()) or [])
                if getattr(v, "kind", "") == NUMBER]

    def _all_variables(self) -> list:
        vset = self._variable_set()
        return list(getattr(vset, "variables", ()) or [])

    def _refresh_variable_combos(self) -> None:
        """Repopulate X and Series, keeping the user's pick where it survives.

        By **id**, held as item data. A name is renamed and an index moves the
        moment another variable is defined, and either would silently re-point
        the export at a different variable while the combo went on reading the
        same word.
        """
        combo = getattr(self, "_x_var_cb", None)
        if combo is None:
            return
        previous = combo.currentData()
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            for variable in self._numeric_variables():
                combo.addItem(variable.name, variable.id)
            index = combo.findData(previous) if previous else -1
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(blocked)
        self._refresh_series_combo()

    def _refresh_series_combo(self) -> None:
        """Offer every variable but the one on the x axis.

        Excluded because a series on the axis variable draws one curve per dose
        with a single point on each — every line one point long, which is not a
        curve. ``collect_response`` filters it out anyway; offering it here
        would be a control whose only effect is to be ignored.
        """
        combo = getattr(self, "_series_var_cb", None)
        if combo is None:
            return
        on_x = self._x_var_cb.currentData() if self._x_var_cb is not None else ""
        previous = combo.currentData()
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(_NO_SERIES, "")
            for variable in self._all_variables():
                if variable.id == on_x:
                    continue
                combo.addItem(variable.name, variable.id)
            index = combo.findData(previous) if previous else -1
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(blocked)

    # ── the run ──────────────────────────────────────────────────────────
    def _selected_log_x(self) -> bool | None:
        index = self._log_x_cb.currentIndex()
        if 0 <= index < len(_LOG_X_CHOICES):
            return _LOG_X_CHOICES[index][1]
        return None

    def _selected_fit(self) -> tuple[str, float | None, float | None]:
        """``(model, fixed_top, fixed_bottom)`` from this panel's own controls.

        The out-of-range fallback is ``self._fit_models[0]`` rather than a
        ``FIT_NONE`` imported here: the roster comes from the tab's own
        ``fit_model_labels()``, which is offer-ordered with "no fit" first
        because a fitted line through points is an assertion the user should be
        the one making. ``test_the_fit_model_offer_matches_the_tab_s`` pins that
        ordering, so the assumption is held rather than trusted.
        """
        index = self._fit_model_cb.currentIndex()
        model = (self._fit_models[index]
                 if 0 <= index < len(self._fit_models) else self._fit_models[0])
        return (model,
                _parse_bound(self._fit_top_edit.text()),
                _parse_bound(self._fit_bottom_edit.text()))

    def _run_batch(self) -> None:
        groups_with_data = _groups_with_loaded_wells(
            self._groups_for_export(), self._ctx.state.dataset.well_paths,
        )
        if not groups_with_data:
            QMessageBox.warning(self, "No groups",
                                "Define at least one non-empty group.")
            return
        variable_id = str(self._x_var_cb.currentData() or "")
        if not variable_id:
            QMessageBox.warning(
                self, "No x variable",
                "A response curve needs a Number sample variable on its x "
                "axis. Define one on Home → Sample Map → Variables and "
                "fill it in for the wells you are exporting.")
            return
        timepoints: list[float] = []
        for text in self._selected_tps():
            try:
                timepoints.append(float(text))
            except ValueError:
                continue
        if not timepoints:
            QMessageBox.warning(self, "No timepoints",
                                "Select at least one timepoint.")
            return
        out_dir = self._resolve_out_dir()
        if out_dir is None:
            return
        self._log_sample_definitions_snapshot(groups_with_data)

        # Every control read once, here, before the first job — the runner pumps
        # ``processEvents()`` between jobs, so anything read inside one is
        # whatever the user has left the panel on by the time it runs. A run
        # exports one configuration or, if the user moves a control, still one:
        # the one that was on screen when they pressed the button.
        fmt = self._fmt_cb.currentText()
        channel = (self._selected_export_channel()
                   or self._ctx.state.channel.active_channel or "")
        metric_key = self._selected_export_metric_key("_response_channel_cb")
        metric_label = self._selected_metric_label()
        val_col = self._export_val_col_for("_response_channel_cb")
        series_id = str(self._series_var_cb.currentData() or "")
        log_x = self._selected_log_x()
        fit_model, fit_top, fit_bottom = self._selected_fit()
        use_sem = bool(self._ctx.state.plot.use_sem)
        draws_fraction = self._draws_fraction(metric_key)
        variable_name = self._variable_name(variable_id)
        used_stems: set = set()

        def _progress(grp: BarGroup, step: int, total: int) -> str:
            return f"Exporting '{grp.name}' ({step}/{total})…"

        def _run_group(grp: BarGroup, write_dir: Path) -> str | None:
            safe = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
            safe = unique_export_stem(f"response_{safe}", used_stems)
            csv_path = write_dir / f"{safe}.csv"
            fig_path = write_dir / f"{safe}.{fmt}"

            # The group's own members, and the panel's own column. Both scopes,
            # because ``collect_response`` reads the live replicate sets through
            # ``plot_members.response_members`` and its fold-change control
            # resolves against the same list.
            with self._app_group_scope(grp), self._app_val_col_scope(val_col):
                try:
                    data = _collect_response(
                        self._ctx, variable_id, timepoints,
                        series_id, self._y_metric(draws_fraction),
                        val_col=val_col)
                except Exception as exc:
                    return f"{grp.name} response data: {exc}"
                fits = _fit_curves(data, fit_model, fixed_top=fit_top,
                                   fixed_bottom=fit_bottom)
                try:
                    self._write_csv(csv_path, grp, data, fits)
                except OSError as exc:
                    return f"{grp.name} CSV: {exc}"
                if not data.has_points():
                    # Nothing drawn, so nothing is written — including the
                    # figure, which would otherwise save as empty axes under a
                    # title naming a group, beside a CSV of nothing, and be
                    # reported as a success. ``SkippedJob`` rather than an error
                    # string: the runner discards the whole staged export for
                    # any error, so reporting this as one would delete every
                    # other group's output too.
                    self._discard_empty_csv(csv_path)
                    return SkippedJob(
                        f"{grp.name}: no response points — nothing exported")
                try:
                    fig = self._render_group_figure(
                        grp, data, fits,
                        channel=channel, metric_key=metric_key,
                        metric_label=metric_label,
                        draws_fraction=draws_fraction, use_sem=use_sem,
                        variable_name=variable_name, timepoints=timepoints,
                        log_x=log_x)
                    self._save_figure(fig, fig_path, fmt,
                                      group=grp, title=grp.name,
                                      val_col=val_col)
                except Exception as exc:
                    return f"{grp.name} figure: {exc}"

            _shared_logger.info("Batch: wrote %s and %s",
                                csv_path.name, fig_path.name)
            return None

        self._run_batch_jobs(
            jobs=groups_with_data,
            progress_text_fn=_progress,
            run_job_fn=_run_group,
            success_text=f"✓ {len(groups_with_data)} group(s) → {out_dir.name}/",
            status_text=(f"Response batch export: {len(groups_with_data)} "
                         f"group(s) → {out_dir}"),
            out_dir=out_dir,
        )

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _discard_empty_csv(path: Path) -> None:
        """Remove the file written for a job that turned out to have no points.

        A CSV of nothing left beside no figure is the shortfall wearing the
        shape of an export: the directory holds a file for the group, so a
        reader counting files finds the run complete. The skip note says what
        happened, and the folder should not contradict it.
        """
        try:
            Path(path).unlink()
        except OSError:
            _logger.warning(
                "Response batch export: could not remove the empty CSV %s", path)

    def _selected_metric_label(self) -> str:
        combo = getattr(self, "_response_channel_cb_metric", None)
        if combo is None:
            return "Mean Intensity"
        return str(combo.currentText() or "Mean Intensity")

    def _draws_fraction(self, metric_key: str) -> bool:
        from well_viewer.data.metric_labels import FRACTION_METRIC_KEY

        return metric_key == FRACTION_METRIC_KEY

    def _y_metric(self, draws_fraction: bool) -> str:
        return _y_metric_for(draws_fraction)

    def _variable_name(self, variable_id: str) -> str:
        vset = self._variable_set()
        variable = vset.by_id(variable_id) if vset is not None else None
        return str(getattr(variable, "name", "") or "")

    def _figure_title(self, grp: BarGroup, data, *, channel: str,
                      metric_key: str, metric_label: str,
                      draws_fraction: bool, use_sem: bool,
                      variable_name: str, timepoints: list[float]) -> str:
        """The group, then the same three clauses the tab's title carries.

        The measurement with its spread, the timepoints, the x variable — in
        that order and in those words, assembled by the tab's own
        ``response_measurement_title`` rather than spelled again here. A reader
        comparing an exported figure with the tab's should not have to work out
        that two differently-worded titles describe the same plate.

        The group's name leads, because that is what distinguishes this figure
        from the others in the folder; on the tab there is only ever one.
        """
        from well_viewer.data.metric_labels import timepoint_title

        parts = [grp.name, _measurement_title(
            data, channel=channel, metric_label=metric_label,
            metric_key=metric_key, draws_fraction=draws_fraction,
            use_sem=use_sem)]
        when = timepoint_title(timepoints,
                               ", ".join(f"{t:g}" for t in timepoints))
        if when:
            parts.append(when)
        if variable_name:
            parts.append(f"vs {variable_name}")
        return " — ".join(part for part in parts if part)

    def _render_group_figure(self, grp: BarGroup, data, fits, *,
                             channel: str, metric_key: str, metric_label: str,
                             draws_fraction: bool, use_sem: bool,
                             variable_name: str, timepoints: list[float],
                             log_x: bool | None):
        """The tab's own renderer over this group's data.

        ``draw_response`` puts the omission caption, the fit refusals and the
        empty state on the axes itself — which is the point of reusing it: a
        saved figure outlives the session that drew it, and a batch figure is
        *only* ever read later, so it is exactly the figure that must carry what
        it left out rather than leaving it in a status line.
        """
        from matplotlib.figure import Figure as _Figure
        from well_viewer.core.plot_style import DEFAULT_LOG_BASE

        fig = _Figure(figsize=(8, 6), dpi=300, facecolor=theme.CPub.bg)
        ax = fig.add_subplot(1, 1, 1)
        _draw_response(
            self._ctx, ax, data,
            log_x=log_x, log_base=DEFAULT_LOG_BASE,
            title=self._figure_title(
                grp, data, channel=channel, metric_key=metric_key,
                metric_label=metric_label, draws_fraction=draws_fraction,
                use_sem=use_sem, variable_name=variable_name,
                timepoints=timepoints),
            ylabel=_y_label(data, channel=channel, metric_label=metric_label),
            variable_chosen=True, timepoints_chosen=bool(timepoints),
            fits=fits)
        return fig

    def _write_csv(self, csv_path: Path, grp: BarGroup, data, fits) -> None:
        """The drawn points, **the members that were left out**, and the fits.

        The omissions are rows, not a footnote. A CSV of eight groups that lists
        five is the same silent substitution as a figure of eight that draws
        five, and a spreadsheet is exactly where someone counts rows and
        believes the total. Same columns as the tab's own Export CSV, with
        ``group`` in front: one file shape for one kind of figure, so a folder
        of batch CSVs concatenates and a tab export drops straight in.
        """
        header = ["group", "member", "series", "timepoint", "x", "x_label",
                  "y", "err", "n", "spread_unit", "reason"]
        unit = data.replicate_unit
        rows = [
            [grp.name, p.member, c.series_label, f"{c.timepoint:g}", f"{p.x:g}",
             p.label, f"{p.y:g}", _blank_if_undefined(p.err), str(p.n), unit, ""]
            for c in data.curves for p in c.points
        ]
        rows += [[grp.name, member, "", "", "", "", "", "", "", "", reason]
                 for member, reason in data.omitted]
        rows += [[grp.name, member, "", "", "", "", "", "", "", "",
                  "value is not a number"]
                 for member in data.non_numeric]
        # The fit rows come from the tab's own builder, so an EC50 lands in the
        # same column here as it does there — then take the group in front like
        # every other row in the file.
        rows += [[grp.name, *row] for row in _fit_rows(data, fits)]
        with open(csv_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)


def build_response_batch_panel(ctx, parent: QWidget | None = None):
    """Construct the panel. A function so the tab builder needs no import."""
    return ResponseBatchExportPanel(ctx, parent, use_sidebar_groups=False)
