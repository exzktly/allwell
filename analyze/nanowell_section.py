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

from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QWidget

from analyze.domain.nanowell_fields import (
    DEFAULTS, PLATE_FORMAT_CHOICES, nanowell_enabled, nanowell_option_errors,
)

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
     "A nanowell imaged in less than this share (0 to 1) of its disk, at the edge of the tiles, the array or the "
     "well, is clipped, and carries its state but no cell count."),
)


def settings_from_opts(opts: dict):
    """The pipeline's ``NanowellSettings`` for the form's run options (blank numeric fields take the defaults)."""
    from pipeline.nanowell_run import NanowellSettings

    def number(key: str, default: float | None) -> float | None:
        text = str(opts.get(key) or "").strip()
        return float(text) if text else default

    return NanowellSettings(
        str(opts["nanowell_format"]), number("nanowell_margin_um", None), number("nanowell_cell_diameter_um", 15.0),
        number("nanowell_square_edge_deg", 0.0), number("nanowell_occluded_below", 0.5),
        number("nanowell_clipped_below", 0.5))


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
        self._nanowell_format_cb.setToolTip("Fixes the lattice pitch and the nanowell diameter the array is found with.")
        row.layout().addWidget(self._nanowell_format_cb)
        row.layout().addStretch(1)

        self._nanowell_field_rows = [row.parentWidget()]
        for key, label, attr, tip in _FIELDS:
            r = self._row(sec, label, tooltip=tip)
            edit = self._entry(r, "", width=70)
            edit.setPlaceholderText(DEFAULTS[key] or "default")
            setattr(self, attr, edit)
            r.layout().addStretch(1)
            self._nanowell_field_rows.append(r.parentWidget())

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
        self._nanowell_cb.toggled.connect(self._refresh_nanowell_enabled)
        self._refresh_nanowell_enabled()

    def _refresh_nanowell_enabled(self, *_: object) -> None:
        """The controls below the mode checkbox mean nothing while it is off, so they are disabled, not hidden."""
        on = self._nanowell_cb.isChecked()
        self._nanowell_format_cb.setEnabled(on)
        for _key, _label, attr, _tip in _FIELDS:
            getattr(self, attr).setEnabled(on)
        self._nanowell_preview_btn.setEnabled(on)

    def _nanowell_option_values(self) -> dict:
        """The section's run options: every key present, the format empty while the mode is off."""
        on = self._nanowell_cb.isChecked()
        out = {"nanowell_format": self._nanowell_format_cb.currentData() if on else ""}
        for key, _label, attr, _tip in _FIELDS:
            out[key] = getattr(self, attr).text().strip()
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
        self._nanowell_cb.setChecked(bool(plate_format))
        index = self._nanowell_format_cb.findData(plate_format)
        if index >= 0:
            self._nanowell_format_cb.setCurrentIndex(index)
        for key, _label, attr, _tip in _FIELDS:
            if key in d:
                value = d.get(key)
                getattr(self, attr).setText("" if value is None else f"{value:g}" if isinstance(value, float) else str(value))

    def _nanowell_validation_error(self, *, check_input: bool) -> str | None:
        """What stops a nanowell run before it starts, or ``None``: bad fields, no Phase channel, no record, no tiles.

        The checks that read the input (the acquisition record, the tiles, the overlap) need a folder that
        exists and a schema detection is not about to replace, so *check_input* gates them, as for the other
        sections' validators.
        """
        opts = self._collect_run_options()
        if not nanowell_enabled(opts):
            return None
        problems = nanowell_option_errors(opts)
        if not problems and check_input:
            from analyze.nanowell_preflight import nanowell_input_report

            problems, warnings = nanowell_input_report(self._input_edit.text().strip(), opts)
            for warning in warnings:                # said, not refused: the run goes on, so say it where the run logs
                self._log_line(f"Nanowells: {warning}", "WARNING")
        return "\n".join(problems) if problems else None

    def _on_nanowell_preview(self) -> None:
        """Open the Test detection window for the form's current input and settings."""
        from analyze.nanowell_preview import open_preview

        open_preview(self)
