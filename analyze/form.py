"""Analyze-tab form construction (Qt widget builders), split from analyze/tab.py.

``AnalyzeTab`` mixes in :class:`AnalyzeTabFormMixin` to inherit the ``_build_*``
section builders verbatim — they still bind to ``self`` exactly as before, so
this is a pure relocation (no behaviour change), trimming the 1.8k-line god-class
down to its run / validation / log-poll core. The schema vocabulary constants
live here too (the form reads them) and are re-exported by ``analyze.tab``.

Restoring a prior run's settings from its ``pipeline_info.json`` sidecar is its
own cohesive job and lives in ``analyze/pinfo.py``; the form's own chrome
(``_section``/``_row``/``_entry``/``_info_icon``/``reveal_section``) lives in
``analyze/form_chrome.py``; the per-method segmentation params, thresholds and
hints in ``analyze/seg_params.py``; the Image correction & QC section in
``analyze/qc_section.py``. This mixin inherits them all, so every
name stays reachable on the form exactly where callers expect it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from analyze.advanced_section import AnalyzeAdvancedSectionMixin
from analyze.form_chrome import AnalyzeFormChromeMixin
from analyze.storage_options import storage_options
from analyze.well_scope import AnalyzeRunScopeMixin, well_scope_options
from analyze.domain.fields import (  # noqa: F401 — re-exported for analyze.tab
    _INT_FIELDS,
    _numeric_option_errors,
)
from analyze.pinfo import AnalyzePipelineInfoMixin
from analyze.nanowell_hook import nanowell_section_mixin
from analyze.qc_section import AnalyzeQCSectionMixin
from analyze.run_footer import AnalyzeRunFooterMixin
from analyze.schema_section import AnalyzeSchemaSectionMixin
from analyze.section_summaries import AnalyzeSectionSummaryMixin
from analyze.seg_params import AnalyzeSegParamsMixin

# ---------------------------------------------------------------------------
# Schema vocabulary — shared with process_microscopy and well_plate_zipper via
# util.schema, so the Analyze form's field picker offers exactly the tokens the
# pipeline parses. This module previously kept its own copy "in sync with
# process_microscopy.py" by hand, which is the arrangement util.schema exists
# to replace. The picker widget itself is shared with the Datasets pane's Zip
# Loose Folder page — see widgets/schema_picker.py.
# ---------------------------------------------------------------------------
from util.schema import SCHEMA_FIELDS, DEFAULT_SCHEMA, DEFAULT_SEP  # noqa: F401 (re-export)
from util.well_token import canonical_well_label, is_valid_well_name


#: The nanowell section, or a stand-in that is nothing, by the build's profile (``analyze.nanowell_hook``).
AnalyzeNanowellSectionMixin = nanowell_section_mixin()


class AnalyzeTabFormMixin(AnalyzeFormChromeMixin, AnalyzeAdvancedSectionMixin,
                          AnalyzeNanowellSectionMixin, AnalyzeSectionSummaryMixin,
                          AnalyzePipelineInfoMixin, AnalyzeQCSectionMixin,
                          AnalyzeRunFooterMixin, AnalyzeRunScopeMixin,
                          AnalyzeSchemaSectionMixin,
                          AnalyzeSegParamsMixin):
    """The Analyze-tab Qt form builders. Mixed into ``AnalyzeTab`` (with
    ``QWidget``); every method binds to the ``AnalyzeTab`` instance ``self``."""

    def _build_form(self, parent: QWidget) -> None:
        """The form, one section per question a run asks, in the order a user
        answers them: what data, how its files are named, which channels, how
        to segment, how to correct, what to measure, which wells, and the
        rarely-changed rest. The run controls are not a section: they are the
        footer pinned under the form (``analyze.run_footer``)."""
        self._build_banner_strips(parent)
        self._build_data_section(parent)
        self._build_schema_section(parent)
        self._build_channels_section(parent)
        self._build_segmentation_section(parent)
        self._refresh_schema_preview()
        # Shown only for a nanowell plate (Plate type, in Channels).
        self._build_nanowell_section(parent)
        self._build_image_correction_section(parent)
        self._build_measurements_section(parent)
        self._build_auto_threshold_section(parent)
        self._build_run_scope_section(parent)
        self._build_advanced_section(parent)
        # Shown only while a remote is chosen in the run footer.
        self._build_remote_options_section(parent)
        parent.layout().addStretch(1)
        self._normalise_field_widths(parent)
        self._snapshot_form_defaults()

    def _build_data_section(self, parent: QWidget) -> None:
        """The input folder, what it resolves to, and the one shortcut that
        fills the whole form: the settings of a previous run."""
        sec = self._section(parent, "Data")

        # Restore a prior run's settings (input path included) from its
        # pipeline_info.json — to re-run a failed run or append new data. First
        # in the section, above the folder it sets (as asked for), and inside
        # it rather than floating above every section as it used to.
        self._open_pinfo_btn = QPushButton("Use settings from a previous run…", sec)
        self._open_pinfo_btn.setProperty("variant", "secondary")
        self._open_pinfo_btn.setToolTip(
            "Open a run's pipeline_info.json and fill the whole form from it, "
            "input folder included.")
        self._open_pinfo_btn.clicked.connect(self._open_pipeline_info)
        pin_row = QWidget(sec)
        prl = QHBoxLayout(pin_row)
        prl.setContentsMargins(0, 0, 0, 4)
        prl.addWidget(self._open_pinfo_btn)
        prl.addStretch(1)
        sec.layout().addWidget(pin_row)

        in_lbl = QLabel("Input folder", sec)
        in_lbl.setObjectName("Muted")
        in_lbl.setWordWrap(True)
        sec.layout().addWidget(in_lbl)

        in_pick = QWidget(sec)
        ipl = QHBoxLayout(in_pick)
        ipl.setContentsMargins(0, 0, 0, 4)
        ipl.setSpacing(4)
        self._input_edit = QLineEdit(in_pick)
        self._input_edit.setReadOnly(True)
        self._input_edit.setPlaceholderText("(not set)")
        self._input_edit.setMinimumWidth(0)
        self._input_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ipl.addWidget(self._input_edit, 1)
        # Never disabled: choosing a folder is what lets the schema be detected,
        # so a lock on the folder until the schema was valid pointed each half
        # of the form at the other. A schema problem stops Run instead.
        self._browse_btn = QPushButton("Browse…", in_pick)
        self._browse_btn.setProperty("variant", "secondary")
        self._browse_btn.clicked.connect(self._browse_input)
        ipl.addWidget(self._browse_btn)
        sec.layout().addWidget(in_pick)

        # Where the run writes, once the input resolves to an output folder:
        # "—" on its own said nothing, so the row is hidden until then.
        self._output_row = QWidget(sec)
        orl = QHBoxLayout(self._output_row)
        orl.setContentsMargins(0, 0, 0, 0)
        orl.setSpacing(4)
        out_cap = QLabel("Output to", self._output_row)
        out_cap.setObjectName("Muted")
        orl.addWidget(out_cap, 0, Qt.AlignTop)
        self._output_lbl = QLabel("—", self._output_row)
        self._output_lbl.setWordWrap(True)
        self._output_lbl.setMinimumWidth(0)
        orl.addWidget(self._output_lbl, 1)
        sec.layout().addWidget(self._output_row)
        self._output_row.setVisible(False)

        self._layout_lbl = QLabel("", sec)
        self._layout_lbl.setWordWrap(True)
        sec.layout().addWidget(self._layout_lbl)
        self._set_status(self._layout_lbl, "", quiet="")


        self._input_edit.textChanged.connect(lambda _: self._refresh_output())

    def _build_channels_section(self, parent: QWidget) -> None:
        """What the plate is, and its channels: one row each, with its role."""
        sec = self._section(parent, "Channels")
        # Standard plate / Nanowell array, from the nanowell section when the
        # build has one (``analyze.nanowell_section``).
        plate_type = getattr(self, "_build_plate_type_row", None)
        if callable(plate_type):
            plate_type(sec)

        fl_header = self._subheading(
            sec, "Channels in the filenames",
            info="One row per channel name as it appears in the filenames. "
                 "Every channel is quantified; mark smFISH channels for spot "
                 "counting, and at most one Phase (brightfield) channel, which "
                 "skips correction and is what Label-free segments.")
        self._fluor_header = fl_header  # for an OME-mode hide nothing performs yet

        self._fluor_frame = QWidget(sec)
        self._fluor_frame_layout = QVBoxLayout(self._fluor_frame)
        self._fluor_frame_layout.setContentsMargins(0, 0, 0, 0)
        self._fluor_frame_layout.setSpacing(2)
        sec.layout().addWidget(self._fluor_frame)
        self._fluor_add_row("GFP")

        add_btn = QPushButton("+ Add channel", sec)
        add_btn.setProperty("variant", "secondary")
        add_btn.setFixedWidth(self._ADD_BTN_W)
        add_btn.clicked.connect(lambda _=False: self._fluor_add_row())
        sec.layout().addWidget(add_btn)
        self._fluor_add_btn = add_btn  # for an OME-mode hide nothing performs yet

    def _build_segmentation_section(self, parent: QWidget) -> None:
        """The method, the channel it segments, and the method's own settings."""
        sec = self._section(parent, "Segmentation")

        seg_row = self._row(sec, "Method")
        self._seg_method_cb = QComboBox(seg_row)
        # Cellpose is a removable, torch-backed segmenter (profiles/full.toml). A
        # profile that doesn't list "Cellpose" (e.g. the StarDist-only thin build)
        # ships no torch/cellpose, so its methods can't run — drop them from the
        # picker instead of offering a choice that would fail at pipeline start.
        from core.profile import active_profile
        _prof = active_profile()
        _cellpose_ok = _prof.features is None or "Cellpose" in _prof.features
        for _value, _label in self._SEG_METHODS:
            if not _cellpose_ok and _value.startswith("cellpose"):
                continue
            self._seg_method_cb.addItem(_label, userData=_value)
        self._seg_method_cb.currentIndexChanged.connect(
            lambda _i: self._seg_method_cb.setToolTip(self._seg_method_cb.currentText()))
        self._seg_method_cb.setToolTip(self._seg_method_cb.currentText())
        seg_row.layout().addWidget(self._seg_method_cb, 1)
        # The method's "use when…" line sits under the picker it describes. It
        # turns into the segmentation error, in the error style, while the
        # method cannot run.
        self._segmentation_hint_lbl = QLabel("", sec)
        self._segmentation_hint_lbl.setObjectName("Muted")
        self._segmentation_hint_lbl.setWordWrap(True)
        sec.layout().addWidget(self._segmentation_hint_lbl)
        self._seg_method_cb.currentIndexChanged.connect(lambda _: self._refresh_segmentation_hints())

        nuc_row = self._row(sec, "Segmentation channel")
        # The whole row, not the field cell ``_row`` returns: the label lives in
        # the cell's parent, so hiding the cell alone (Label-free mode) left
        # "Segmentation channel" floating beside nothing.
        self._nuclear_row_widget = nuc_row.parentWidget()
        self._nuclear_token_edit = self._entry(nuc_row, "NIR", width=160)
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_schema_preview())
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())

        # Per-method parameter pages + the shared StarDist thresholds
        # (analyze.seg_params.AnalyzeSegParamsMixin).
        self._build_seg_param_stack(sec)
        self._refresh_segmentation_hints()

    def _build_auto_threshold_section(self, parent: QWidget) -> None:
        """Auto-threshold — every threshold-related control in one place.

        Three controls, in the order they apply: the checkbox drives the
        pipeline's ``--no_auto_threshold``; the method combo picks how the cut is
        chosen; the two well fields name the controls the second method compares.

        The method is an explicit choice rather than being inferred from whether
        the wells are filled, so switching back to Otsu does not lose them.
        Collapsed by default, like the other optional sections.
        """
        # The switch sits in the section header, so whether thresholds will be
        # calculated can be read — and changed — with the section folded.
        self._auto_threshold_cb = QCheckBox("On")
        self._auto_threshold_cb.setChecked(True)
        self._auto_threshold_cb.setToolTip(
            "Calculate cell-gating thresholds when the run finishes. On "
            "(default): a per-channel threshold is written with the dataset. "
            "Off: none are written and Cell Gating starts from its own defaults.")
        sec = self._section(
            parent, "Auto-threshold",
            collapsible=True, collapsed=True,
            switch=self._auto_threshold_cb,
            summary=self._auto_threshold_summary,
            info="After a run finishes, All-Well can pick a default 'on' "
                 "threshold for each channel and store it with the dataset, so "
                 "the Cell Gating tab opens with sensible values. Otsu (the "
                 "default) splits cell signal from background across the whole "
                 "dataset. Control wells instead take the cut that best "
                 "separates a well you know is ON from one you know is OFF, "
                 "which is usually the better answer because it is anchored to "
                 "biology rather than to pixel statistics. The segmentation "
                 "channel is always set to 0 — it defines cell boundaries and is "
                 "not something to gate on.",
        )

        rmethod = self._row(sec, "Method")
        self._auto_threshold_method_cb = QComboBox(rmethod)
        self._auto_threshold_method_cb.addItem("Otsu (whole dataset)", "otsu")
        self._auto_threshold_method_cb.addItem("Positive / negative controls",
                                               "controls")
        self._auto_threshold_method_cb.setToolTip(
            "Otsu: split each channel's per-cell signal from image background, "
            "pooled across the dataset — no controls needed, but it cannot tell "
            "which cells are biologically positive.\n"
            "Positive / negative controls: sweep for the cut that best separates "
            "the two named wells."
        )
        rmethod.layout().addWidget(self._auto_threshold_method_cb)
        rmethod.layout().addStretch(1)
        self._auto_threshold_method_row = rmethod.parentWidget()

        rpos = self._row(sec, "Positive control well")
        self._pos_control_well_edit = self._entry(rpos, "", width=160)
        self._pos_control_well_edit.setPlaceholderText("e.g. B02")
        self._pos_control_well_edit.setToolTip(
            "A well where the signal is known to be ON. Optional — but if you "
            "give one you must give the negative control too."
        )
        rpos.layout().addStretch(1)

        rneg = self._row(sec, "Negative control well")
        self._neg_control_well_edit = self._entry(rneg, "", width=160)
        self._neg_control_well_edit.setPlaceholderText("e.g. B11")
        self._neg_control_well_edit.setToolTip(
            "A well where the signal is known to be OFF. The threshold chosen "
            "for each channel is the one that best separates this well from the "
            "positive control."
        )
        rneg.layout().addStretch(1)

        # ``_row`` returns the right-hand cell; the labels live in its parent,
        # so hide the parents or the labels stay behind on their own.
        self._auto_threshold_rows = (rpos.parentWidget(), rneg.parentWidget())

        def _sync_auto_threshold(_=None) -> None:
            on = self._auto_threshold_cb.isChecked()
            self._auto_threshold_method_row.setVisible(on)
            self._refresh_section_summary("Auto-threshold")
            # Text is left alone — switching to Otsu and back must not cost the
            # user their wells.
            wells = on and self._auto_threshold_method() == "controls"
            for row in self._auto_threshold_rows:
                row.setVisible(wells)

        self._auto_threshold_cb.toggled.connect(_sync_auto_threshold)
        self._auto_threshold_method_cb.currentIndexChanged.connect(_sync_auto_threshold)
        _sync_auto_threshold()

    def _auto_threshold_summary(self) -> str:
        """The folded header's line: what the pass will do."""
        if not self._auto_threshold_cb.isChecked():
            return ""
        return self._auto_threshold_method_cb.currentText()

    def _auto_threshold_method(self) -> str:
        """``"otsu"`` or ``"controls"`` — the selected estimator."""
        return str(self._auto_threshold_method_cb.currentData() or "otsu")

    def _auto_threshold_validation_error(self, *, check_input: bool = True) -> str | None:
        """Block the run when the control wells are half-given or unusable.

        ``None`` when valid — including the pass being off or Otsu selected,
        neither of which reads the well fields.
        """
        if not self._auto_threshold_cb.isChecked():
            return None
        if self._auto_threshold_method() != "controls":
            return None       # Otsu needs no wells; any text is dormant
        pos = self._pos_control_well_edit.text().strip()
        neg = self._neg_control_well_edit.text().strip()
        if not pos or not neg:
            missing = "positive" if not pos else "negative"
            return (
                f"The control-well method needs both wells — the {missing} "
                f"control well is empty.\n\n"
                "The threshold is chosen from the separation between the two "
                "wells, so one on its own says nothing. Switch Method back to "
                "Otsu if you don't have a pair."
            )
        bad = [w for w in (pos, neg) if not is_valid_well_name(w)]
        if bad:
            label = ", ".join(f"'{w}'" for w in bad)
            return (f"Control well {label} is not a valid 96-well position.\n\n"
                    "Use a row letter A–H followed by a column number 1–12 "
                    "(e.g. B02).")
        if canonical_well_label(pos) == canonical_well_label(neg):
            return ("The positive and negative control wells are the same well "
                    f"('{pos}').\n\nThey have to differ — the threshold comes "
                    "from the separation between them.")
        input_dir = Path(self._input_edit.text().strip())
        from services.input_resolution_service import well_present_in_input
        missing_wells = [w for w in (pos, neg) if check_input  # False: input not readable yet
                         and not well_present_in_input(input_dir, w)]
        if missing_wells:
            label = ", ".join(f"'{w}'" for w in missing_wells)
            return (f"Control well {label} was not found in the input folder.\n\n"
                    f"The threshold pass reads each control well's measurements "
                    f"after the run — confirm they exist in:\n{input_dir}")
        return None

    def _collect_run_options(self) -> dict:
        """The form's fields as one run-options dict. Every numeric field is
        carried as the **raw text** the user typed: coercing here would put the
        value out of ``_numeric_option_errors``' reach, which cannot tell a typo
        from a deliberate blank once both have become ``0.0``.
        ``build_pipeline_args`` coerces once, after validation has had its say.
        """
        method = self._seg_method()
        cyto = self._cytoplasm_edit.text().strip()
        if method != "stardist_seeded_watershed_cell":
            cyto = ""
        sd_prob, sd_nms = self._stardist_threshold_values()
        opts = dict(
            raw=Path(raw_text) if (raw_text := self._input_edit.text().strip()) else "",
            scratch_dir=self._scratch_edit.text().strip() or None,
            nuclear_token=self._nuclear_token_edit.text().strip() or "NIR",
            fluor_tokens=self._fluor_tokens_list(),
            csv_prefix=self._csv_prefix_edit.text().strip() or "measurements",
            tophat_radius_nir=self._tophat_radius_nir_edit.text(),
            tophat_radius_fluor=self._tophat_radius_fluor_edit.text(),
            fft_small_nir=self._fft_small_nir_edit.text(),
            fft_small_fluor=self._fft_small_fluor_edit.text(),
            mesh_ratio=self._mesh_ratio_edit.text(),
            mesh_sigma=self._mesh_sigma_edit.text(),
            no_tophat_nir=self._no_tophat_nir_cb.isChecked(),
            no_tophat_fluor=self._no_tophat_fluor_cb.isChecked(),
            # Always-on (no longer user options): zip in/out.
            compress_input_well_folders=True,
            compress_output_well_folders=True,
            remote_batch_size=self._remote_batch_size_edit.text(),
            remote_upload_ahead=self._remote_upload_ahead_cb.value(),
            force=self._existing_output_cb.value() == "force",
            incremental=self._existing_output_cb.value() == "incremental",
            **well_scope_options(self),
            **storage_options(self),
            cpu_only=self._cpu_only_cb.isChecked(),
            allow_low_disk=self._allow_low_disk_cb.isChecked(),
            tf_threads=self._tf_threads_edit.text(),
            workers=self._workers_edit.text(),
            stardist_tile_px=self._stardist_tile_px_edit.text(),
            stardist_prob_thresh=sd_prob,
            stardist_nms_thresh=sd_nms,
            filename_schema=self._build_schema_arg(),
            filename_sep=self._schema_picker.separator(),
            smfish_tokens=self._smfish_tokens_list(),
            shape_features=list(self._shape_chips.checkedData()),
            **self._correction_options(),
            auto_threshold=self._auto_threshold_cb.isChecked(),
            run_check=self._run_check_cb.isChecked(),
            auto_threshold_method=self._auto_threshold_method(),
            pos_control_well=self._pos_control_well_edit.text().strip(),
            neg_control_well=self._neg_control_well_edit.text().strip(),
            qc_remove_debris=self._qc_remove_debris_cb.isChecked(),
            qc_min_area=self._qc_min_area_edit.text().strip(),
            qc_max_area=self._qc_max_area_edit.text().strip(),
            qc_min_solidity=self._qc_min_solidity_edit.text().strip(),
            measure_focus=self._focus_metric_cb.isChecked(),
            focus_min=self._focus_min_edit.text().strip(),
            phase_token=self._phase_token(),
            segmentation_method=method,
            cytoplasm_token=cyto,
            min_nucleus_area_px=self._min_nucleus_area_edit.text().strip(),
            cellpose_model=self._cellpose_model_cb.value(),
            cellpose_diameter=self._cellpose_diameter_edit.text().strip(),
            cellpose_flow_threshold=self._cellpose_flow_thr_edit.text().strip(),
            cellpose_cellprob_threshold=self._cellpose_cellprob_thr_edit.text().strip(),
            cellpose_nuclei_channel=self._cellpose_nuclei_channel_edit.text().strip(),
            **self._nanowell_option_values(),
        )
        return opts
