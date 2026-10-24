"""The Analyze form's **Nanowells** section: its widgets, their rules, and the Test detection button.

One section of the form in its own module, like :mod:`analyze.qc_section` and :mod:`analyze.schema_section`,
because ``analyze/tab.py`` has no headroom and ``analyze/form.py`` very little. What the fields mean and what
makes them wrong is Qt-free, in :mod:`analyze.domain.nanowell_fields`; the argv they become is
``services.pipeline_service._emit_nanowell_flags``. The widgets land on the ``AnalyzeTab`` under
``self._nanowell_*``, which is what ``analyze/pinfo.py`` restores by name and what the job-edit round trip
reads back.

Turning the section on is a statement about the whole plate (every well is processed as tiled arrays, not as
fields), so nothing here is enabled by default, and the section's last control is the check that makes the
statement safe: **Test detection** runs registration and detection on one well at one timepoint, off the GUI
thread, and shows the addresses land before anyone starts a plate.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QLabel, QLineEdit, QPushButton, QSizePolicy, QWidget,
)

from analyze.domain.nanowell_fields import (
    CELLPOSE_MODEL_CHOICES, CUSTOM_FORMAT, DEFAULTS, GEOMETRY_CHOICES, LEGACY_CELLPOSE_MODEL, PLATE_FORMAT_CHOICES,
    LEGACY_SEGMENT_ON, REGISTRATION_FROM_KEY, SEGMENT_ON_CHOICES,
    SCAN_CHOICES,
    nanowell_enabled, nanowell_option_errors,
)

_REGISTRATION_TIP = (
    "An earlier nanowell run's output folder whose tile registration this run reuses, so a second analysis with "
    "other segmentation or measurement settings skips registering the tiles. A timepoint's stored registration is "
    "used only when it was made from exactly this run's inputs (the same images, acquisition record and plate "
    "geometry); any other is registered afresh. Blank: only the output folder's own earlier registration is "
    "reused, the same way. Runs on a remote host register every tile.")

#: field key -> (row label, edit attribute, tooltip). The edits' placeholders are the pipeline's defaults.
_FIELDS = (
    ("nanowell_cell_diameter_um", "Cell diameter (µm)", "_nanowell_cell_diameter_edit",
     "Typical cell diameter. It sets the scale the label-free segmenter looks for cells at."),
    ("nanowell_margin_um", "Margin (µm)", "_nanowell_margin_edit",
     "How far inside a tile a nanowell must lie for that tile to own it, and so for its cells to be counted "
     "from that tile. Blank: a tenth of the nanowell diameter."),
    ("nanowell_square_edge_deg", "Square edge angle (°)", "_nanowell_square_edit",
     "Angle of the central square nanowell's edges to a lattice row. It is how a lattice fitted a sixth of a "
     "turn off is caught. 0 is assumed: the square is cut parallel to a row."),
    ("nanowell_occluded_below", "Occlusion threshold", "_nanowell_occluded_edit",
     "A nanowell visible in less than this share (0 to 1) is occluded, and carries its state but no cell count."),
    ("nanowell_clipped_below", "Clipping threshold", "_nanowell_clipped_edit",
     "A nanowell imaged in less than this share (0 to 1) of its disk, because the edge of the tiles cuts it, is "
     "clipped, and carries its state but no cell count."),
)


#: The fields of :data:`_FIELDS` that set the cell count, built with the count's other settings in the Segmentation
#: section (:meth:`AnalyzeNanowellSectionMixin._build_nanowell_count_rows`) rather than here.
_COUNT_FIELDS = ("nanowell_cell_diameter_um",)


#: A custom format's geometry (tracker A7), shown only while the format is Custom.
_CUSTOM_FIELDS = (
    ("nanowell_diameter_um", "Nanowell diameter (µm)", "_nanowell_diameter_edit",
     "The nanowells' diameter on this plate."),
    ("nanowell_pitch_um", "Lattice pitch (µm)", "_nanowell_pitch_edit",
     "Centre-to-centre distance between neighbouring nanowells. A starting value: the run measures the pitch on "
     "the tiles, from half to twice this."),
)

#: The tile grid for a dataset with no acquisition record (tracker P15, A8). The scan order is a combo, apart.
_GRID_FIELDS = (
    ("nanowell_grid", "Tile grid", "_nanowell_grid_edit",
     "Only for a dataset with no acquisition record: the tiles of a well as ROWSxCOLUMNS (e.g. 5x5). Fields are "
     "taken in the order of their names, numerically, along the scan order. A record, when there is one, wins."),
    ("nanowell_overlap_percent", "Tile overlap (%)", "_nanowell_overlap_edit",
     "Only with a tile grid: the overlap set in the acquisition software."),
    ("nanowell_pixel_size_um", "Pixel size (µm)", "_nanowell_pixel_edit",
     "Only with a tile grid: the images' pixel size."),
)


def settings_from_opts(opts: dict):
    """The pipeline's ``NanowellSettings`` for the form's run options (blank numeric fields take the defaults)."""
    from pipeline.nanowell_run import NanowellSettings

    def number(key: str, default: float | None) -> float | None:
        text = str(opts.get(key) or "").strip()
        return float(text) if text else default

    plate_format = str(opts["nanowell_format"])
    custom = ((number("nanowell_pitch_um", None), number("nanowell_diameter_um", None))
              if plate_format == CUSTOM_FORMAT else None)
    return NanowellSettings(
        plate_format, number("nanowell_margin_um", None), number("nanowell_cell_diameter_um", 15.0),
        number("nanowell_square_edge_deg", 0.0), number("nanowell_occluded_below", 0.5),
        number("nanowell_clipped_below", 0.5), custom,
        str(opts.get("nanowell_geometry") or GEOMETRY_CHOICES[0][0]),
        str(opts.get("nanowell_cellpose_model") or CELLPOSE_MODEL_CHOICES[0][0]),
        str(opts.get("nanowell_segment_on") or SEGMENT_ON_CHOICES[0][0]),
        # No control on the form: the run takes the command line's default (crops), and Test detection the same.
        str(opts.get("nanowell_segment_scope") or "nanowells"))


class AnalyzeNanowellSectionMixin:
    """The Nanowells section's builders and rules. Binds to the ``AnalyzeTab``."""

    def _build_nanowell_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Nanowells", collapsible=True, collapsed=True,
            info="Analyse tiled phase images of a nanowell array: each well at each timepoint is processed as one "
                 "unit (every tile together), nanowells are addressed on the lattice and measured label-free, with "
                 "fluorescence where present. Needs the dataset's acquisition record (written by the converters) "
                 "and a Phase channel. Use Test detection before a plate-wide run.")
        row = self._row(sec, "Mode")
        self._nanowell_cb = QCheckBox("Nanowell arrays", row)
        self._nanowell_cb.setToolTip("Process each well at each timepoint as a tiled array of nanowells.")
        row.layout().addWidget(self._nanowell_cb)
        row.layout().addStretch(1)

        row = self._row(sec, "Plate format")
        self._nanowell_format_cb = QComboBox(row)
        for value, label in PLATE_FORMAT_CHOICES:
            self._nanowell_format_cb.addItem(label, userData=value)
        self._nanowell_format_cb.setToolTip("Fixes the lattice pitch and the nanowell diameter the array is found with "
                                            "(with an inferred array geometry, what is measured is compared with).")
        row.layout().addWidget(self._nanowell_format_cb)
        row.layout().addStretch(1)

        self._nanowell_field_rows = [row.parentWidget()]
        row = self._row(sec, "Array geometry", tooltip=(
            "Hexagonal lattice in a circle: the array is found at the plate format's diameter, as a hexagonal "
            "lattice inside a circular outline. Infer from the image: the nanowells are found by their contrast "
            "(light wells on a dark lattice) and the lattice kind (hexagonal or square), pitch, orientation and the "
            "array's outline are read from where they are; the format's pitch and diameter are only compared with "
            "what is measured, and a disagreement is reported."))
        self._nanowell_geometry_cb = QComboBox(row)
        for value, label in GEOMETRY_CHOICES:
            self._nanowell_geometry_cb.addItem(label, userData=value)
        # As the scan combo: sized to a short prefix, so the longest label does not widen the narrow sidebar.
        self._nanowell_geometry_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._nanowell_geometry_cb.setMinimumContentsLength(12)
        row.layout().addWidget(self._nanowell_geometry_cb)
        row.layout().addStretch(1)
        self._nanowell_field_rows.append(row.parentWidget())
        self._build_nanowell_count_rows(sec)
        self._nanowell_custom_rows = []
        for key, label, attr, tip in _CUSTOM_FIELDS:
            self._nanowell_custom_rows.append(self._nanowell_edit_row(sec, key, label, attr, tip))
        for key, label, attr, tip in _FIELDS:
            if key not in _COUNT_FIELDS:                # the count's own rows sit with segmentation (above)
                self._nanowell_field_rows.append(self._nanowell_edit_row(sec, key, label, attr, tip))
        row = self._row(sec, "Scan order", tooltip="Only with a tile grid: where the stage started and how it ran.")
        self._nanowell_scan_cb = QComboBox(row)
        for value in SCAN_CHOICES:
            self._nanowell_scan_cb.addItem(value.replace("-", " "), userData=value)
        # "bottom right column serpentine" is wider than the sidebar's field column: the closed combo elides
        # it, the list shows it whole, and the tooltip names the current choice.
        self._nanowell_scan_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._nanowell_scan_cb.setMinimumContentsLength(12)
        self._nanowell_scan_cb.currentIndexChanged.connect(
            lambda _i: self._nanowell_scan_cb.setToolTip(self._nanowell_scan_cb.currentText()))
        row.layout().addWidget(self._nanowell_scan_cb, 1)
        grid_rows = [self._nanowell_edit_row(sec, *spec) for spec in _GRID_FIELDS[:1]]
        grid_rows.append(row.parentWidget())
        grid_rows += [self._nanowell_edit_row(sec, *spec) for spec in _GRID_FIELDS[1:]]
        self._nanowell_field_rows += grid_rows
        self._nanowell_field_rows.append(self._build_nanowell_registration_row(sec))
        self._nanowell_format_cb.currentIndexChanged.connect(lambda _i: self._refresh_nanowell_custom())

        row = self._row(sec, "Check")
        self._nanowell_preview_btn = QPushButton("Test detection…", row)
        self._nanowell_preview_btn.setProperty("variant", "secondary")
        self._nanowell_preview_btn.setToolTip(
            "Run registration and detection on one well at one timepoint and show the montage with every nanowell "
            "labelled, before starting a plate-wide run.")
        self._nanowell_preview_btn.clicked.connect(lambda _=False: self._on_nanowell_preview())
        row.layout().addWidget(self._nanowell_preview_btn)
        row.layout().addStretch(1)
        self._nanowell_status = QLabel("", sec)
        self._nanowell_status.setObjectName("Muted")
        self._nanowell_status.setWordWrap(True)
        sec.layout().addWidget(self._nanowell_status)
        self._nanowell_field_rows.append(row.parentWidget())
        self._nanowell_previous_method: int | None = None
        self._nanowell_logged: set[str] = set()
        if self._nanowell_label_free_index() < 0:
            # A build without Cellpose (a profile that leaves it out) cannot segment label-free, which is the
            # only segmentation nanowell mode has: say so instead of offering a mode that cannot run.
            self._nanowell_cb.setEnabled(False)
            self._nanowell_status.setText("Nanowell mode needs label-free (Cellpose) segmentation, which this build "
                                          "does not include.")
        self._nanowell_cb.toggled.connect(self._refresh_nanowell_enabled)
        self._refresh_nanowell_enabled()

    def _build_nanowell_registration_row(self, sec) -> QWidget:
        """The folder an earlier run's registration is reused from: a path and a Browse button."""
        row = self._row(sec, "Reuse registration", tooltip=_REGISTRATION_TIP)
        layout = row.layout()
        self._nanowell_registration_edit = QLineEdit(row)
        self._nanowell_registration_edit.setPlaceholderText("(the output folder's own)")
        self._nanowell_registration_edit.setMinimumWidth(0)
        self._nanowell_registration_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self._nanowell_registration_edit, 1)
        self._nanowell_registration_btn = QPushButton("Browse…", row)
        self._nanowell_registration_btn.setProperty("variant", "secondary")
        self._nanowell_registration_btn.clicked.connect(lambda _=False: self._browse_nanowell_registration())
        layout.addWidget(self._nanowell_registration_btn)
        return row.parentWidget()

    def _browse_nanowell_registration(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select an earlier nanowell run's output folder", self._nanowell_registration_edit.text().strip(),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks)
        if folder:
            self._nanowell_registration_edit.setText(folder)

    def _build_nanowell_count_rows(self, sec: QWidget) -> None:
        """The cell count's own settings — its Cellpose model, what that model is given, and the cell size — in the
        Segmentation section's label-free page (``seg_params._nanowell_count_box``), which shows them in nanowell
        mode in place of the settings the count does not read. In a form without that page they stay here."""
        box = getattr(self, "_nanowell_count_box", None) or sec
        heading = QLabel("Nanowell mode: the cell count in each nanowell segments with these.", box)
        heading.setObjectName("Muted")
        heading.setWordWrap(True)
        box.layout().addWidget(heading)
        row = self._row(box, "Cellpose model", tooltip=(
            "The model the cell count segments with. cpsam: Cellpose-SAM, Cellpose 4's model, which the app "
            "installs. cyto3: Cellpose 3's generalist. livecell_cp3: the Cellpose 3 model trained on phase-contrast "
            "images of eight cell lines (LIVECell). cyto3 and livecell_cp3 run only where Cellpose 3 is installed; "
            "on Cellpose 4 the run refuses them by name rather than segmenting with another model. Compare them on "
            "your plate with Test detection, whose crops show the run's own segmentation. Weights download on first "
            "use."))
        self._nanowell_model_cb = QComboBox(row)
        for value, label in CELLPOSE_MODEL_CHOICES:
            self._nanowell_model_cb.addItem(label, userData=value)
        self._nanowell_model_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._nanowell_model_cb.setMinimumContentsLength(12)
        row.layout().addWidget(self._nanowell_model_cb)
        row.layout().addStretch(1)
        self._nanowell_field_rows.append(row.parentWidget())
        row = self._row(box, "Segment on", tooltip=(
            "What the cell count's model is given. Raw image: each tile as it was imaged — what Cellpose's models, "
            "Cellpose-SAM's among them, were trained on. A cell found on a nanowell's rim is still not counted: only "
            "a cell whose centre lies inside a nanowell's interior is. Rim-removed: each nanowell divided by the "
            "well's empty-nanowell picture, which takes the rim away but is a ratio image no model was trained on, "
            "and stretches the faint noise of an empty nanowell. Confluence is measured on the rim-removed image "
            "either way. Compare the two on one well with Test detection, whose crops show the image the model was "
            "given."))
        self._nanowell_segment_on_cb = QComboBox(row)
        for value, label in SEGMENT_ON_CHOICES:
            self._nanowell_segment_on_cb.addItem(label, userData=value)
        self._nanowell_segment_on_cb.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._nanowell_segment_on_cb.setMinimumContentsLength(12)
        row.layout().addWidget(self._nanowell_segment_on_cb)
        row.layout().addStretch(1)
        self._nanowell_field_rows.append(row.parentWidget())
        for key, label, attr, tip in _FIELDS:
            if key in _COUNT_FIELDS:
                self._nanowell_field_rows.append(self._nanowell_edit_row(box, key, label, attr, tip))

    def _nanowell_edit_row(self, sec, key, label, attr, tip):
        r = self._row(sec, label, tooltip=tip)
        edit = self._entry(r, "", width=70)
        edit.setPlaceholderText(DEFAULTS[key] or "default")
        setattr(self, attr, edit)
        r.layout().addStretch(1)
        return r.parentWidget()

    def _refresh_nanowell_custom(self) -> None:
        """The diameter and pitch rows belong to a custom format only: shown for it, hidden otherwise."""
        custom = self._nanowell_format_cb.currentData() == CUSTOM_FORMAT
        for row in self._nanowell_custom_rows:
            row.setVisible(custom)

    def _refresh_nanowell_enabled(self, *_: object) -> None:
        """The controls below the mode checkbox mean nothing while it is off, so they are disabled, not hidden."""
        on = self._nanowell_cb.isChecked()
        self._nanowell_format_cb.setEnabled(on)
        self._nanowell_geometry_cb.setEnabled(on)
        self._nanowell_model_cb.setEnabled(on)
        self._nanowell_segment_on_cb.setEnabled(on)
        # The count's rows live on the Segmentation section's label-free page: shown in nanowell mode, and the
        # page's own note (and its pixel diameter, which the count does not read) hidden then.
        box = getattr(self, "_nanowell_count_box", None)
        if box is not None:
            box.setVisible(on)
            self._labelfree_note.setVisible(not on)
            self._sync_cellpose_diameter_row()
            self._collapse_seg_param_stack()
        for _key, _label, attr, _tip in _FIELDS + _CUSTOM_FIELDS + _GRID_FIELDS:
            getattr(self, attr).setEnabled(on)
        self._nanowell_scan_cb.setEnabled(on)
        self._nanowell_registration_edit.setEnabled(on)
        self._nanowell_registration_btn.setEnabled(on)
        self._nanowell_preview_btn.setEnabled(on)
        self._nanowell_pin_segmentation(on)
        self._refresh_nanowell_custom()

    def _nanowell_label_free_index(self) -> int:
        return self._seg_method_cb.findData("cellpose_labelfree")

    def _nanowell_pin_segmentation(self, on: bool) -> None:
        """Nanowell mode segments label-free whatever the selector says, so the selector says so and is locked.

        The pipeline sets the method itself; the form's own checks (a StarDist tile size, a watershed
        cytoplasm channel) would otherwise refuse a run over a setting the run ignores. The user's choice is
        put back when the mode is switched off.
        """
        index = self._nanowell_label_free_index()
        if on and index >= 0:
            if self._nanowell_previous_method is None:
                self._nanowell_previous_method = self._seg_method_cb.currentIndex()
            self._seg_method_cb.setCurrentIndex(index)
            self._seg_method_cb.setEnabled(False)
        elif not on and self._nanowell_previous_method is not None:
            self._seg_method_cb.setCurrentIndex(self._nanowell_previous_method)
            self._seg_method_cb.setEnabled(True)
            self._nanowell_previous_method = None

    def _nanowell_option_values(self) -> dict:
        """The section's run options: every key present, the format empty while the mode is off."""
        on = self._nanowell_cb.isChecked()
        out = {"nanowell_format": self._nanowell_format_cb.currentData() if on else ""}
        for key, _label, attr, _tip in _FIELDS + _CUSTOM_FIELDS + _GRID_FIELDS:
            out[key] = getattr(self, attr).text().strip()
        out["nanowell_scan"] = self._nanowell_scan_cb.currentData() or SCAN_CHOICES[0]
        out["nanowell_geometry"] = self._nanowell_geometry_cb.currentData() or GEOMETRY_CHOICES[0][0]
        out["nanowell_cellpose_model"] = self._nanowell_model_cb.currentData() or CELLPOSE_MODEL_CHOICES[0][0]
        out["nanowell_segment_on"] = self._nanowell_segment_on_cb.currentData() or SEGMENT_ON_CHOICES[0][0]
        out[REGISTRATION_FROM_KEY] = self._nanowell_registration_edit.text().strip()
        return out

    def _apply_nanowell_settings(self, d: dict) -> None:
        """Restore the section from argparse-style keys (a sidecar's ``execution_options``, or a job's ``opts``).

        An absent ``nanowell_format`` key leaves the section alone (the sidecar predates it); a present one,
        even ``None``, is a statement about this dataset, so the mode is switched off for a dataset that
        was not analysed as arrays rather than left on from the previous one.
        """
        if "nanowell_format" not in d or not hasattr(self, "_nanowell_cb"):
            return
        plate_format = str(d.get("nanowell_format") or "").strip()
        index = self._nanowell_format_cb.findData(plate_format)
        known = index >= 0 and self._nanowell_cb.isEnabled()
        # The method the dataset states is the one to put back when the mode goes off, not the one the user
        # had before switching it on: the restore already set the selector, and switching the mode off below
        # would otherwise overwrite it (roll-up review X7).
        stated = self._seg_method_cb.findData(d.get("segmentation_method"))
        if self._nanowell_previous_method is not None and stated >= 0 and stated != self._nanowell_label_free_index():
            self._nanowell_previous_method = stated
        self._nanowell_cb.setChecked(known)
        if known:
            self._nanowell_pin_segmentation(True)   # already on: the restore moved the locked selector
        self._nanowell_status.setText(
            "" if known or not plate_format else
            f"This dataset was analysed with plate format {plate_format!r}, which this version does not know: "
            "nanowell mode is off.")
        if known:
            self._nanowell_format_cb.setCurrentIndex(index)
        for key, _label, attr, _tip in _FIELDS + _CUSTOM_FIELDS + _GRID_FIELDS:
            # A key the dataset does not state is blank, not whatever the previous dataset left in the box.
            value = d.get(key)
            getattr(self, attr).setText("" if value is None else f"{value:.12g}" if isinstance(value, float) else str(value))
        scan = self._nanowell_scan_cb.findData(str(d.get("nanowell_scan") or SCAN_CHOICES[0]))
        self._nanowell_scan_cb.setCurrentIndex(max(scan, 0))
        # Absent or None is a run that did not choose: the format's hexagonal lattice in a circle.
        geometry = self._nanowell_geometry_cb.findData(str(d.get("nanowell_geometry") or GEOMETRY_CHOICES[0][0]))
        self._nanowell_geometry_cb.setCurrentIndex(max(geometry, 0))
        # A run that named no model ran cyto3, and shows it; a new form starts on the default.
        named = d.get("nanowell_cellpose_model") if "nanowell_cellpose_model" in d else CELLPOSE_MODEL_CHOICES[0][0]
        model = self._nanowell_model_cb.findData(str(named or LEGACY_CELLPOSE_MODEL))
        self._nanowell_model_cb.setCurrentIndex(max(model, 0))
        # The same for the segmenter's input: a run that named none segmented the rim-removed image.
        named = d.get("nanowell_segment_on") if "nanowell_segment_on" in d else SEGMENT_ON_CHOICES[0][0]
        self._nanowell_segment_on_cb.setCurrentIndex(
            max(self._nanowell_segment_on_cb.findData(str(named or LEGACY_SEGMENT_ON)), 0))
        self._nanowell_registration_edit.setText(str(d.get(REGISTRATION_FROM_KEY) or ""))
        self._refresh_nanowell_custom()

    def _nanowell_validation_error(self, *, check_input: bool, for_run: bool = True) -> str | None:
        """What stops a nanowell run before it starts, or ``None``: bad fields, no Phase channel, no record, no tiles.

        The checks that read the input (the acquisition record, the tiles, the overlap) need a folder that
        exists and a schema detection is not about to replace, so *check_input* gates them, as for the other
        sections' validators.
        """
        opts = self._collect_run_options()
        if not nanowell_enabled(opts):
            return None
        problems = nanowell_option_errors(opts, need_phase=not self._auto_detect_on())
        # Only for a run: Test detection runs on this computer whatever host the run would go to (X30).
        remote = for_run and self._remote_cb.currentData() is not None
        if remote and self._incremental_cb.isChecked():
            from services.remote_run_state import NANOWELL_REMOTE_INCREMENTAL_REFUSAL

            problems.append(NANOWELL_REMOTE_INCREMENTAL_REFUSAL)
        if remote and str(opts.get(REGISTRATION_FROM_KEY) or "").strip():
            self._nanowell_log_once("Reuse registration names a folder on this computer, which a remote host "
                                    "cannot read: the host registers every tile itself (the positions are the same).")
        if not problems and check_input:
            from analyze.nanowell_preflight import nanowell_input_report

            problems, warnings = nanowell_input_report(self._input_edit.text().strip(), opts)
            for warning in warnings:                # said, not refused: the run goes on, so say it where the run logs
                self._nanowell_log_once(warning)
        return "\n".join(problems) if problems else None

    def _nanowell_log_once(self, warning: str) -> None:
        """Log a finding that does not stop the run, once per distinct finding rather than once per validation."""
        if warning not in self._nanowell_logged:
            self._nanowell_logged.add(warning)
            self._log_line(f"Nanowells: {warning}", "WARNING")

    def _on_nanowell_preview(self) -> None:
        """Open the Test detection window for the form's current input and settings."""
        from analyze.nanowell_preview import open_preview

        open_preview(self)
