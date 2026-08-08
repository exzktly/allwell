"""Analyze-tab form construction (Qt widget builders), split from analyze_tab.py.

``AnalyzeTab`` mixes in :class:`AnalyzeTabFormMixin` to inherit the ``_build_*``
section builders verbatim — they still bind to ``self`` exactly as before, so
this is a pure relocation (no behaviour change), trimming the 1.8k-line god-class
down to its run / validation / log-poll core. The schema vocabulary constants
live here too (the form reads them) and are re-exported by ``analyze_tab``.

Restoring a prior run's settings from its ``pipeline_info.json`` sidecar is its
own cohesive job and lives in ``analyze_tab_pinfo.py``; the form's own chrome
(``_section``/``_row``/``_entry``/``_info_icon``/``reveal_section``) lives in
``analyze_tab_chrome.py``; the per-method segmentation params, thresholds and
hints in ``analyze_tab_seg_params.py``. This mixin inherits them all, so every
name stays reachable on the form exactly where callers expect it.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from analyze_tab_chrome import AnalyzeFormChromeMixin
from analyze_tab_fields import (  # noqa: F401 — re-exported for analyze_tab
    _INT_FIELDS,
    _numeric_option_errors,
    _safe_qc_float,
    _safe_seg_float,
)
from analyze_tab_pinfo import AnalyzePipelineInfoMixin
from analyze_tab_seg_params import AnalyzeSegParamsMixin
from services.remote_config import default_remote_index, load_remotes
from widgets.checkable_dropdown import CheckableDropdown
from widgets.run_dashboard import RunDashboard
from widgets.wrap_checkbox import WrapCheckBox
import pipeline.shape_features as _sfc

# ---------------------------------------------------------------------------
# Schema vocabulary — shared with process_microscopy and WellPlateZipper via
# util.schema, so the Analyze form's field picker offers exactly the tokens the
# pipeline parses. This module previously kept its own copy "in sync with
# process_microscopy.py" by hand, which is the arrangement util.schema exists
# to replace.
# ---------------------------------------------------------------------------
from util.schema import SCHEMA_FIELDS, DEFAULT_SCHEMA, DEFAULT_SEP
from util.well_token import canonical_well_label, is_valid_well_name

_SCHEMA_LABELS = ("Experiment", "Channel", "Well", "FOV", "Timepoint", "— ignore —")
# strict=: the two tuples are positionally paired, so a length mismatch is a
# real bug — a dropped label would silently make a schema field unreachable in
# the picker rather than raising.
_LABEL_TO_FIELD = dict(zip(_SCHEMA_LABELS, SCHEMA_FIELDS, strict=True))
_FIELD_TO_LABEL = dict(zip(SCHEMA_FIELDS, _SCHEMA_LABELS, strict=True))


class AnalyzeTabFormMixin(AnalyzeFormChromeMixin, AnalyzePipelineInfoMixin,
                          AnalyzeSegParamsMixin):
    """The Analyze-tab Qt form builders. Mixed into ``AnalyzeTab`` (with
    ``QWidget``); every method binds to the ``AnalyzeTab`` instance ``self``."""

    def _build_form(self, parent: QWidget) -> None:
        parent.layout().addWidget(self._build_resume_banner(parent))
        self._build_open_pinfo_row(parent)
        self._build_schema_section(parent)
        self._build_channel_tokens_section(parent)
        self._build_folders_section(parent)
        self._refresh_schema_preview()
        # Quality Control leads the optional sections and stays open: its
        # correction stages are common tuning knobs, not an afterthought.
        self._build_qc_section(parent)
        self._build_auto_threshold_section(parent)
        self._build_shape_features_section(parent)
        self._build_output_options_section(parent)
        self._build_compute_options_section(parent)
        self._build_scratch_section(parent)
        self._build_run_controls(parent)
        parent.layout().addStretch(1)

    def _build_schema_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Filename Schema")

        sep_row = self._row(sec, "Separator char")
        self._filename_sep_edit = self._entry(sep_row, DEFAULT_SEP, width=30)
        sep_row.layout().addStretch(1)
        self._filename_sep_edit.textChanged.connect(lambda _: self._refresh_schema_preview())

        default_fields = DEFAULT_SCHEMA.split(":")
        for pos_idx in range(5):
            row = self._row(sec, f"Position {pos_idx + 1}")
            cb = QComboBox(row)
            cb.addItems(list(_SCHEMA_LABELS))
            cb.setCurrentText(_FIELD_TO_LABEL.get(default_fields[pos_idx], "— ignore —"))
            cb.setFixedWidth(self._CB_W)
            row.layout().addWidget(cb)
            row.layout().addStretch(1)
            self._schema_cbs.append(cb)
            cb.currentIndexChanged.connect(lambda _i, c=cb: self._on_combobox_selected(c))

        schema_str_row = self._row(sec, "Schema string")
        self._schema_str_edit = QLineEdit(DEFAULT_SCHEMA, schema_str_row)
        # Expand to fill the row. The string never fits unscrolled at any width
        # this sidebar offers — a scrollable single-line edit (cursor pinned to
        # the start below), so the minimum just keeps a legible chunk on screen.
        self._schema_str_edit.setMinimumWidth(self._SCHEMA_STR_EDIT_MIN_W)
        self._schema_str_edit.setCursorPosition(0)  # show the start, not the tail
        schema_str_row.layout().addWidget(self._schema_str_edit, 1)
        self._schema_str_edit.editingFinished.connect(self._sync_dropdowns_from_string)

        self._schema_err_lbl = QLabel("", sec)
        self._schema_err_lbl.setObjectName("Error")
        self._schema_err_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_err_lbl)

        self._schema_preview_lbl = QLabel("", sec)
        self._schema_preview_lbl.setObjectName("Muted")
        self._schema_preview_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_preview_lbl)

    def _build_channel_tokens_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Channel Tokens")

        seg_row = self._row(sec, "Segmentation")
        self._seg_method_cb = QComboBox(seg_row)
        # Cellpose is a removable, torch-backed segmenter (profiles/full.toml). A
        # profile that doesn't list "Cellpose" (e.g. the StarDist-only thin build)
        # ships no torch/cellpose, so its methods can't run — drop them from the
        # picker instead of offering a choice that would fail at pipeline start.
        from well_viewer.core.profile import active_profile
        _prof = active_profile()
        _cellpose_ok = _prof.features is None or "Cellpose" in _prof.features
        for _value, _label in self._SEG_METHODS:
            if not _cellpose_ok and _value.startswith("cellpose"):
                continue
            self._seg_method_cb.addItem(_label, userData=_value)
        # Narrower than the longest item ("Label-free (Cellpose, brightfield)")
        # — Qt elides the closed box's text; the tooltip carries the full label.
        self._seg_method_cb.setFixedWidth(self._SEG_METHOD_CB_W)
        self._seg_method_cb.currentIndexChanged.connect(
            lambda _i: self._seg_method_cb.setToolTip(self._seg_method_cb.currentText()))
        self._seg_method_cb.setToolTip(self._seg_method_cb.currentText())
        seg_row.layout().addWidget(self._seg_method_cb)
        seg_row.layout().addStretch(1)
        self._seg_method_cb.currentIndexChanged.connect(lambda _: self._refresh_segmentation_hints())

        nuc_row = self._row(sec, "Segmentation channel")
        self._nuclear_row_widget = nuc_row
        self._nuclear_token_edit = self._entry(nuc_row, "NIR")
        nuc_row.layout().addStretch(1)
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_schema_preview())
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())

        fl_header = QWidget(sec)
        fl_hl = QHBoxLayout(fl_header)
        fl_hl.setContentsMargins(0, 6, 0, 2)
        fl_hl.setSpacing(4)
        fl_title = QLabel("Fluorescent channels", fl_header)
        fl_hl.addWidget(fl_title)
        fl_hl.addWidget(self._info_icon(
            fl_header,
            "Mark smFISH channels, or one channel as Phase for label-free "
            "segmentation, with the checkboxes on each row.",
        ))
        fl_hl.addStretch(1)
        sec.layout().addWidget(fl_header)
        self._fluor_header = fl_header  # hidden for OME input (role picker drives it)

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
        self._fluor_add_btn = add_btn  # hidden for OME input (role picker drives it)

        # Per-method parameter pages + the shared StarDist thresholds
        # (analyze_tab_seg_params.AnalyzeSegParamsMixin).
        self._build_seg_param_stack(sec)

        self._segmentation_hint_lbl = QLabel("", sec)
        self._segmentation_hint_lbl.setObjectName("Muted")
        self._segmentation_hint_lbl.setWordWrap(True)
        sec.layout().addWidget(self._segmentation_hint_lbl)
        self._refresh_segmentation_hints()

    def _build_open_pinfo_row(self, parent: QWidget) -> None:
        """Top-of-tab button: restore a prior run's settings (incl. input/output
        paths) from its pipeline_info.json sidecar — to re-run a failed run or
        append new data."""
        row = QWidget(parent)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 8, 12, 0)
        rl.setSpacing(4)
        self._open_pinfo_btn = QPushButton("Open previous run (pipeline_info.json)…", row)
        self._open_pinfo_btn.setProperty("variant", "secondary")
        self._open_pinfo_btn.clicked.connect(self._open_pipeline_info)
        rl.addWidget(self._open_pinfo_btn)
        rl.addStretch(1)
        parent.layout().addWidget(row)

    def _build_folders_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Folders")

        in_lbl = QLabel("Input folder", sec)
        in_lbl.setWordWrap(True)
        sec.layout().addWidget(in_lbl)

        in_pick = QWidget(sec)
        ipl = QHBoxLayout(in_pick)
        ipl.setContentsMargins(0, 0, 0, 4)
        ipl.setSpacing(4)
        self._input_edit = QLineEdit(in_pick)
        self._input_edit.setReadOnly(True)
        self._input_edit.setPlaceholderText("(not set)")
        self._input_edit.setEnabled(False)
        self._input_edit.setMinimumWidth(0)
        self._input_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ipl.addWidget(self._input_edit, 1)
        self._browse_btn = QPushButton("Browse…", in_pick)
        self._browse_btn.setProperty("variant", "secondary")
        self._browse_btn.setEnabled(False)
        self._browse_btn.clicked.connect(self._browse_input)
        ipl.addWidget(self._browse_btn)
        # Balancing stretch: input field gets ~50% of the row width
        # rather than stretching to fill the whole sidebar.
        ipl.addStretch(1)
        sec.layout().addWidget(in_pick)

        self._folder_lock_lbl = QLabel("Define the filename schema above first.", sec)
        self._folder_lock_lbl.setObjectName("Muted")
        self._folder_lock_lbl.setWordWrap(True)
        sec.layout().addWidget(self._folder_lock_lbl)

        out_lbl = QLabel("Output folder (auto)", sec)
        out_lbl.setContentsMargins(0, 6, 0, 0)
        out_lbl.setWordWrap(True)
        sec.layout().addWidget(out_lbl)
        self._output_lbl = QLabel("—", sec)
        self._output_lbl.setObjectName("Muted")
        self._output_lbl.setWordWrap(True)
        sec.layout().addWidget(self._output_lbl)

        self._layout_lbl = QLabel("", sec)
        self._layout_lbl.setWordWrap(True)
        sec.layout().addWidget(self._layout_lbl)

        self._input_edit.textChanged.connect(lambda _: self._refresh_output())

    def _build_shape_features_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Shape Metrics",
            collapsible=True, collapsed=True,
            info="Per-cell morphology features to measure (emitted as shape_… "
                 "columns; cell centroids are always recorded).",
        )

        # A compact checkbox dropdown rather than a wide chip grid — the
        # selectable-feature list is long enough to overflow the narrow
        # Analyze sidebar. ``checkedData()`` / ``setCheckedData()`` keep the
        # same contract the chip group exposed, so _collect_run_options needs
        # no change.
        self._shape_chips = CheckableDropdown(sec, noun="metrics selected",
                                              empty_text="No metrics selected")
        for key in _sfc.SELECTABLE_KEYS:
            feat = _sfc.BY_KEY[key]
            self._shape_chips.addItem(feat.label, data=key)
        self._shape_chips.setCheckedData(list(self._SHAPE_DEFAULTS))
        # Constrain width to match the other controls (e.g. the segmentation
        # combo's 230 px). Added directly to the section's QVBoxLayout the dropdown
        # button's Preferred policy stretched it to the full ~414 px sidebar, which
        # looked oversized; cap it and left-align with a trailing stretch.
        self._shape_chips.setMaximumWidth(self._SHAPE_CHIPS_MAX_W)
        chip_row = QWidget(sec)
        crl = QHBoxLayout(chip_row)
        crl.setContentsMargins(0, 0, 0, 0)
        crl.setSpacing(4)
        crl.addWidget(self._shape_chips)
        crl.addStretch(1)
        sec.layout().addWidget(chip_row)

    def _build_qc_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Quality Control",
            collapsible=False, collapsed=False,
            info="Optional pre-measurement clean-up, applied per channel at "
                 "quantification time. Two independent correction stages — "
                 "flat-field (divide out uneven illumination) then background "
                 "subtraction (remove a baseline) — plus a size/shape debris "
                 "filter. The pixel corrections change the measured values, so "
                 "they can only be changed by re-running; debris removal is "
                 "editable later in Segmentation.",
        )

        r = self._row(sec, "Flat-field correction")
        self._flatfield_cb = QComboBox(r)
        self._flatfield_cb.addItems(["none", "empty_well", "gaussian", "basic"])
        # Default to empty_well (illumination from the Background well). The
        # dropdown doesn't persist, so this build default is what fresh runs use;
        # reprocessing a dataset still restores its saved pipeline_info value.
        self._flatfield_cb.setCurrentText("empty_well")
        self._flatfield_cb.setToolTip(
            "Illumination (flat-field) correction — divides each channel by a "
            "mean-1 illumination field F (corrected = image / F) to even out "
            "vignetting / uneven excitation, applied before background "
            "subtraction. The field is floored so amplification of dim regions "
            "is capped at 10x.\n"
            "• none — no illumination correction.\n"
            "• empty_well — F is the smoothed per-pixel median of the Background "
            "well (that well's intensity is the illumination profile). Needs a "
            "Background well.\n"
            "• gaussian — per-image, self-referential: F is the heavily "
            "Gaussian-blurred image itself. No reference well needed.\n"
            "• basic — pure-numpy BaSiC: F is the smoothed per-pixel median of a "
            "whole-plate sample of the channel (one field per channel).\n"
            "(If both flat-field and background are empty_well, the two combine "
            "into one (raw-B)/(B/mean(B)) pass.)")
        r.layout().addWidget(self._flatfield_cb)
        r.layout().addStretch(1)

        rbg = self._row(sec, "Background subtraction")
        self._background_cb = QComboBox(rbg)
        self._background_cb.addItems(
            ["none", "top_hat", "sliding_disk", "fov_median", "empty_well",
             "robust_mesh", "fft_bandpass"])
        # Default to robust_mesh: a sigma-clipped local background on a coarse
        # grid (SExtractor/Background2D style), robust to bright/confluent objects
        # on uneven background where morphological methods (top_hat's square
        # halos, sliding_disk) struggle. The dropdown doesn't persist, so this
        # build default is what fresh runs use; reprocessing restores the saved
        # pipeline_info value.
        self._background_cb.setCurrentText("robust_mesh")
        self._background_cb.setToolTip(
            "How background is subtracted (per channel, where enabled below):\n"
            "• none — no background subtraction.\n"
            "• top_hat — fast square morphological; leaves square halos/hotspots "
            "on uneven background.\n"
            "• sliding_disk — smooth isotropic disk baseline (default; recommended "
            "for structured/uneven background; no square halos).\n"
            "• fov_median — subtract a single scalar, the median intensity of the "
            "whole field of view (a uniform offset; robust to bright cells, but "
            "does NOT flatten spatial gradients). No radius/reference needed.\n"
            "• empty_well — subtract a measured per-channel background from the "
            "Background well. (If flat-field is *also* empty_well, the two are "
            "combined into one correct (raw−B)/(B/mean(B)) pass.)\n"
            "• robust_mesh — sigma-clipped local background on a coarse grid "
            "(SExtractor/Background2D style), bicubically interpolated. Robust to "
            "bright/confluent objects on uneven background where morphological "
            "methods fail; uses the radius below as the background scale.\n"
            "• fft_bandpass — 2-D FFT Butterworth high-pass: removes the "
            "low-frequency background (illumination, gradients, large blobs) "
            "regardless of object size. Radius below = large-structure cutoff; "
            "small-structure (px) = optional high-frequency denoise (0 = off).")
        rbg.layout().addWidget(self._background_cb)
        rbg.layout().addStretch(1)

        # Radius / enable controls — folded in from the former standalone
        # "Top-Hat Background Subtraction" section. Shown only for the
        # radius-based methods (top_hat / sliding_disk); hidden for empty_well /
        # none, which take no radius.
        nir_row = self._row(sec, "Nuclear radius")
        self._tophat_radius_nir_edit = self._entry(nir_row, "100", width=60)
        self._no_tophat_nir_cb = QCheckBox("Disable", nir_row)
        nir_row.layout().addWidget(self._no_tophat_nir_cb)
        nir_row.layout().addStretch(1)
        fluor_row = self._row(sec, "Fluor radius")
        self._tophat_radius_fluor_edit = self._entry(fluor_row, "100", width=60)
        fluor_row.layout().addWidget(self._info_icon(
            fluor_row,
            "Radius = background scale (px); Fluor radius applies to all "
            "fluorescent channels. For fft_bandpass it is the large-structure "
            "(low-frequency) cutoff; for robust_mesh it is the tile size.",
        ))
        self._no_tophat_fluor_cb = QCheckBox("Disable", fluor_row)
        fluor_row.layout().addWidget(self._no_tophat_fluor_cb)
        fluor_row.layout().addStretch(1)
        # _row() returns the right-hand cell, not the full row — the "Nuclear
        # radius" / "Fluor radius" label lives in the cell's parent. Toggle
        # visibility on the *parent* rows so the labels hide with their entries
        # (hiding only the cell left the labels stranded).
        self._bg_radius_rows = (nir_row.parentWidget(), fluor_row.parentWidget())

        # fft_bandpass-only small-structure (high-frequency) cutoffs — the
        # optional low-pass that denoises for a true band-pass. 0 = high-pass
        # only (preserves signal; the default, faithful for quantification).
        fft_nir_row = self._row(sec, "Nuclear small px")
        self._fft_small_nir_edit = self._entry(fft_nir_row, "0", width=60)
        fft_nir_row.layout().addStretch(1)
        fft_fluor_row = self._row(sec, "Fluor small px")
        self._fft_small_fluor_edit = self._entry(fft_fluor_row, "0", width=60)
        fft_fluor_row.layout().addStretch(1)
        fft_hint = QLabel("Small-structure cutoff (px): 0 = high-pass only "
                          "(no denoise); >1 attenuates features smaller than "
                          "this for a true band-pass.", sec)
        fft_hint.setObjectName("Muted")
        fft_hint.setWordWrap(True)
        sec.layout().addWidget(fft_hint)
        self._fft_small_rows = (fft_nir_row.parentWidget(),
                                fft_fluor_row.parentWidget(), fft_hint)

        # robust_mesh-only tuning. These are algorithm constants (applied to all
        # channels), not per-channel like the radius: the tile size relative to
        # the radius, and the per-tile sigma-clip threshold.
        mesh_ratio_row = self._row(sec, "Mesh tile ratio")
        self._mesh_ratio_edit = self._entry(mesh_ratio_row, "3.0", width=60)
        mesh_ratio_row.layout().addStretch(1)
        mesh_sigma_row = self._row(sec, "Mesh sigma-clip")
        self._mesh_sigma_edit = self._entry(mesh_sigma_row, "3.0", width=60)
        mesh_sigma_row.layout().addStretch(1)
        mesh_hint = QLabel("Tile = radius / ratio (larger → coarser, smoother "
                           "background). Sigma-clip threshold: smaller rejects "
                           "bright object pixels more aggressively on crowded "
                           "fields. Defaults 3.0 / 3.0.", sec)
        mesh_hint.setObjectName("Muted")
        mesh_hint.setWordWrap(True)
        sec.layout().addWidget(mesh_hint)
        self._mesh_param_rows = (mesh_ratio_row.parentWidget(),
                                 mesh_sigma_row.parentWidget(), mesh_hint)

        rbw = self._row(sec, "Background well")
        self._background_well_edit = self._entry(rbw, "", width=160)
        self._background_well_edit.setPlaceholderText("H12 or H12, G12")
        self._background_well_edit.setToolTip(
            "Well label(s) of empty/background well(s) used as the reference for "
            "any empty_well mode above. Enter one (e.g. H12) or several "
            "comma-separated (e.g. H12, G12) — multiple wells are pooled for a "
            "cleaner estimate. Their smoothed per-pixel median supplies the "
            "illumination field (flat-field) and/or the measured additive "
            "background, computed PER TIMEPOINT (background drifts over time); a "
            "timepoint the blank wells lack falls back to the all-timepoint "
            "pooled median. Read from whichever input layout holds the well "
            "(per-well zip, per-well folder, or loose TIFs); enabled only when "
            "flat-field or background is empty_well.")
        rbw.layout().addStretch(1)

        def _sync_bg(_=None) -> None:
            bg_method = self._background_cb.currentText()
            # Background well feeds empty_well flat-field and/or empty_well bg.
            self._background_well_edit.setEnabled(
                self._flatfield_cb.currentText() == "empty_well"
                or bg_method == "empty_well")
            # Radius is the background scale for the morphological / sliding-disk
            # methods, the tile size for robust_mesh, and the large-structure
            # cutoff for fft_bandpass — shown for all of them.
            for w in self._bg_radius_rows:
                w.setVisible(bg_method in ("top_hat", "sliding_disk",
                                           "robust_mesh", "fft_bandpass"))
            # The small-structure (high-frequency) cutoff is fft_bandpass-only.
            for w in self._fft_small_rows:
                w.setVisible(bg_method == "fft_bandpass")
            # Tile-ratio / sigma-clip tuning is robust_mesh-only.
            for w in self._mesh_param_rows:
                w.setVisible(bg_method == "robust_mesh")
        self._flatfield_cb.currentIndexChanged.connect(_sync_bg)
        self._background_cb.currentIndexChanged.connect(_sync_bg)
        _sync_bg()

        self._qc_remove_debris_cb = WrapCheckBox(
            "Remove debris (drop regions outside the size / solidity bounds)", sec)
        sec.layout().addWidget(self._qc_remove_debris_cb)

        ra = self._row(sec, "Min / Max area (px)")
        self._qc_min_area_edit = self._entry(ra, "0", width=80)
        self._qc_max_area_edit = self._entry(ra, "0", width=80)
        ra.layout().addStretch(1)

        rs = self._row(sec, "Min solidity (0-1)")
        self._qc_min_solidity_edit = self._entry(rs, "0", width=80)
        rs.layout().addStretch(1)

        # The size / solidity bounds only apply when debris removal is on, so
        # hide them entirely until it's checked (rather than showing greyed-out
        # orphan fields) — the cleaning-step options then read as one coherent
        # group that appears only when relevant. Toggle the *parent* rows, not the
        # ``_row()`` right-hand cells: hiding only the cell strands the "Min / Max
        # area (px)" / "Min solidity (0-1)" labels (they live in the cell's parent),
        # exactly as the _bg_radius_rows / _fft_small_rows / _mesh_param_rows fixes above.
        self._qc_debris_rows = (ra.parentWidget(), rs.parentWidget())

        def _sync_debris(_=None) -> None:
            on = self._qc_remove_debris_cb.isChecked()
            for row in self._qc_debris_rows:
                row.setVisible(on)
        self._qc_remove_debris_cb.toggled.connect(_sync_debris)
        _sync_debris()

    def _build_output_options_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Output Options",
            collapsible=True, collapsed=True,
            info="Inputs are always grouped into per-well .zip files and outputs "
                 "are always written as per-well .zip files.",
        )

        r = self._row(sec, "CSV prefix")
        self._csv_prefix_edit = self._entry(r, "measurements", width=160)
        r.layout().addStretch(1)

    def _build_compute_options_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Compute Options", collapsible=True, collapsed=True)

        # Hint below its row, not inline — matches the Mesh tile/sigma note below.
        def _hint(text: str) -> None:
            lbl = QLabel(text, sec)
            lbl.setObjectName("Muted")
            lbl.setWordWrap(True)
            sec.layout().addWidget(lbl)

        tf_row = self._row(sec, "TF threads (0=auto)")
        self._tf_threads_edit = self._entry(tf_row, "0", width=40)
        tf_row.layout().addStretch(1)
        _hint("(0 → auto-select 4)")

        w_row = self._row(sec, "Workers (0=auto)")
        self._workers_edit = self._entry(w_row, "0", width=40)
        w_row.layout().addStretch(1)
        _hint("(process count override)")

        # StarDist prediction tiling — caps per-worker peak RAM on large frames
        # (an untiled ~4K predict can use several GB/worker, so parallel workers
        # exhaust memory). The value is the MAX pixels per tile SIDE (not the
        # number of tiles), 0 = off (default). Label kept short so it fits the
        # row label column; the px/side meaning is in the info-icon tooltip.
        tile_row = self._row(sec, "Max tile size (0=off)")
        # Same width as the TF threads / Workers entries above it — these three
        # small integer fields read as one uniform column.
        self._stardist_tile_px_edit = self._entry(tile_row, "0", width=40)
        tile_row.layout().addWidget(self._info_icon(
            tile_row,
            "Max pixels per tile SIDE for StarDist prediction — NOT the number "
            "of tiles. StarDist splits the frame into as many tiles as needed so "
            "no block exceeds this size, then stitches the result. 0 = off "
            "(predict the whole frame at once). On large frames (~4K) an untiled "
            "predict can use several GB per worker, so a few parallel workers "
            "exhaust RAM; e.g. 2048 splits a 4096-px frame into a 2×2 grid, 1024 "
            "into 4×4. Smaller = less RAM per worker, slightly slower."))
        tile_row.layout().addStretch(1)

        self._cpu_only_cb = QCheckBox("CPU only (disable GPU)", sec)
        sec.layout().addWidget(self._cpu_only_cb)
        self._force_cb = QCheckBox("Force reprocess all wells", sec)
        self._force_cb.setToolTip(
            "Reprocess every well from scratch, even one whose output already "
            "exists. Overrides the incremental option below — the pipeline "
            "ignores --incremental when --force is given.")
        sec.layout().addWidget(self._force_cb)
        # The run mode that finishes a partial run. Without it, a well whose
        # <well>_out.zip and CSV both exist is SKIPPED whole, however few of its
        # FOV-groups actually made it in — which is why a crashed run cannot be
        # completed by simply pressing Run again. Datasets → Validate & Repair's
        # "Complete this run in Analyze" ticks this on the way in.
        self._incremental_cb = QCheckBox("Incremental — process only missing FOVs", sec)
        self._incremental_cb.setToolTip(
            "Fill in the gaps a partial or crashed run left, instead of "
            "skipping any well that already has output. Each well's already-"
            "recorded (FOV, timepoint) pairs are read from the output folder "
            "and only the missing ones are processed, then merged into the "
            "existing per-well CSV and <well>_out.zip. Wells with no output at "
            "all process in full; wells that are already complete are left "
            "untouched.")
        sec.layout().addWidget(self._incremental_cb)
        # Per-well force, as a visible field rather than hidden run state: it
        # re-segments the named wells from scratch, which is expensive and
        # overwrites their outputs, so it must never be something the user
        # cannot see or clear. Validate & Repair's completion handoff fills it
        # with the wells an incremental run would otherwise skip forever.
        fw_row = self._row(sec, "Force these wells")
        self._force_wells_edit = QLineEdit(fw_row)
        self._force_wells_edit.setPlaceholderText("(none)")
        self._force_wells_edit.setToolTip(
            "Comma-separated wells to reprocess in full, whatever else the run "
            "decides. A well whose <WELL>_out.zip exists but whose measurements "
            "CSV is missing looks complete to an incremental run — its archive "
            "members are counted as already done — so only forcing it "
            "regenerates the CSV. Re-segments those wells and overwrites their "
            "outputs; everything else is unaffected.")
        fw_row.layout().addWidget(self._force_wells_edit, 1)

    def _build_scratch_section(self, parent: QWidget) -> None:
        """Scratch-disk picker — the very last option before the Run controls.

        Per-well working files (zip extraction + intermediate images + FOV
        checkpoints) are written here. It defaults to ``~/.allwell/scratch``
        (``pipeline.io.scratch_base_for``) and **no longer tracks the input
        folder**: seeding it with ``<input>/Temp`` put the run's whole GB-scale
        temporary I/O on the input volume, which is the configuration behind
        "Bad file descriptor" well failures when that volume is a network mount
        — a dropped SMB session invalidates every open handle. Keep this on
        local storage.
        """
        sec = self._section(
            parent, "Scratch Disk",
            collapsible=True, collapsed=True,
            info="Where per-well working files (zip extraction + intermediate "
                 "images) are written during a run. Leave blank to use "
                 "~/.allwell/scratch. Keep it on fast LOCAL storage — pointing "
                 "it at a network drive (NAS / SMB share) puts the run's "
                 "GB-scale temporary I/O on a volume whose dropped sessions "
                 "make wells fail with 'Bad file descriptor'.",
        )
        pick = QWidget(sec)
        pl = QHBoxLayout(pick)
        pl.setContentsMargins(0, 0, 0, 4)
        pl.setSpacing(4)
        self._scratch_edit = QLineEdit(pick)
        self._scratch_edit.setPlaceholderText("(defaults to ~/.allwell/scratch)")
        self._scratch_edit.setMinimumWidth(0)
        self._scratch_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        pl.addWidget(self._scratch_edit, 1)
        self._scratch_browse_btn = QPushButton("Browse…", pick)
        self._scratch_browse_btn.setProperty("variant", "secondary")
        self._scratch_browse_btn.clicked.connect(self._browse_scratch)
        pl.addWidget(self._scratch_browse_btn)
        pl.addStretch(1)
        sec.layout().addWidget(pick)
        # Typing / browsing pins the value; blank means the pipeline default
        # (~/.allwell/scratch). The field no longer follows the input dir — see
        # the docstring above for why that default was harmful.
        self._scratch_user_set = False
        self._scratch_edit.textEdited.connect(self._on_scratch_edited)

    def _on_scratch_edited(self, _text: str = "") -> None:
        self._scratch_user_set = True

    def _browse_scratch(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Select scratch folder", "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if d:
            self._scratch_edit.setText(d)
            self._scratch_user_set = True

    def _build_run_controls(self, parent: QWidget) -> None:
        # Remote-execution selector. Populated from ~/.config/allwell/remotes.toml
        # so users can flip between Local and any configured SSH host
        # without restarting the GUI.
        remote_widget = QWidget(parent)
        rl = QHBoxLayout(remote_widget)
        rl.setContentsMargins(12, 6, 12, 0)
        rl.setSpacing(8)
        rl.addWidget(QLabel("Run on:", remote_widget))
        self._remote_cb = QComboBox(remote_widget)
        self._remote_cb.addItem("Local", userData=None)
        try:
            for name in load_remotes():
                self._remote_cb.addItem(name, userData=name)
        except (ValueError, OSError) as exc:
            # Malformed remotes.toml shouldn't bring the GUI down — just
            # surface the error in the log and stick with Local.
            self._log_q.put((
                "line",
                f"[warn] could not load remotes.toml: {exc}\n",
            ))
        # Default to the first configured remote when any exist (else Local).
        self._remote_cb.setCurrentIndex(
            default_remote_index(self._remote_cb.count()))
        rl.addWidget(self._remote_cb)
        self._manage_remotes_btn = QPushButton("Manage…", remote_widget)
        self._manage_remotes_btn.setProperty("variant", "secondary")
        self._manage_remotes_btn.setToolTip(
            "Add, edit, delete, test, or bootstrap remote-execution hosts."
        )
        self._manage_remotes_btn.clicked.connect(self._open_manage_remotes)
        rl.addWidget(self._manage_remotes_btn)
        rl.addStretch(1)
        parent.layout().addWidget(remote_widget)

        # ── Remote-only parameters, grouped in one place ─────────────────────
        # Every knob that only matters for a remote run lives in this container:
        # the batch size (folded in from the former Output-Options section), the
        # auto-delete toggle, and its dry-run companion. The whole group is
        # shown only when a remote is selected (see _refresh_remote_controls),
        # so a Local run isn't cluttered with options that do nothing.
        self._remote_params_widget = QWidget(parent)
        rp = QVBoxLayout(self._remote_params_widget)
        rp.setContentsMargins(12, 4, 12, 0)
        rp.setSpacing(4)

        rp_title = QLabel("Remote options", self._remote_params_widget)
        rp_title.setObjectName("SectionTitle")
        rp.addWidget(rp_title)

        # Batched mode. ``0`` (default) uploads every well-zip up front and runs
        # the pipeline once across the whole plate — lowest total runtime. Any
        # positive integer processes that many wells at a time: peak remote disk
        # ≈ batch_size wells of input + ~1 well of output (per-well Fetcher
        # cleanup), at the cost of one StarDist reload per batch (~5–10 s
        # overhead each). Pick based on the remote's free disk.
        bs_row = self._row(self._remote_params_widget, "Batch size (0=off)")
        self._remote_batch_size_edit = self._entry(bs_row, "0", width=40)
        bs_hint = QLabel("(bounds peak disk to ~N wells of input)", bs_row)
        bs_hint.setObjectName("Muted"); bs_hint.setWordWrap(True)
        bs_row.layout().addWidget(bs_hint, 1)

        # ON by default: a remote that accumulates every run fills up, and a full
        # volume is how a run silently loses FOV-groups. Only deletes after a
        # verified-successful run, never failed work.
        self._cleanup_remote_cb = QCheckBox(
            "Auto-delete remote files when done", self._remote_params_widget)
        self._cleanup_remote_cb.setChecked(True)
        self._cleanup_remote_cb.setToolTip(
            "Checked (default) = after a verified-successful run, delete both the "
            "per-run output dir AND the cached input on the remote, so a shared "
            "machine does not fill up. Unchecked = leave it untouched.")
        rp.addWidget(self._cleanup_remote_cb)

        # Log what auto-delete WOULD remove without removing it. Only enabled
        # when auto-delete is checked.
        self._dry_run_cleanup_cb = WrapCheckBox(
            "Dry-run cleanup (show what would be deleted, don't delete)",
            self._remote_params_widget,
        )
        self._dry_run_cleanup_cb.setToolTip(
            "With auto-delete enabled, log path + file count + total bytes for "
            "each remote directory that WOULD be removed at end-of-run, without "
            "removing anything. Useful for confirming what auto-delete will do "
            "on a shared remote before committing to it."
        )
        rp.addWidget(self._dry_run_cleanup_cb)

        parent.layout().addWidget(self._remote_params_widget)

        # Show the remote-only group only when a remote is selected; dry-run
        # only makes sense when auto-delete is on.
        self._remote_cb.currentIndexChanged.connect(self._refresh_remote_controls)
        self._cleanup_remote_cb.toggled.connect(self._refresh_remote_controls)
        self._refresh_remote_controls()

        parent.layout().addWidget(self._build_run_row(parent))

    def _refresh_last_run_summary(self) -> None:
        """Populate the idle last-run card (C4) from the most recent run's
        pipeline_info.json. Hidden when no previous run is available."""
        card = getattr(self, "_last_run_card", None)
        if card is None:
            return
        out = self._last_output_dir  # a @property (Path | None)
        info_path = (out / "pipeline_info.json") if out else None
        if not info_path or not info_path.exists():
            card.setVisible(False)
            return
        try:
            import json
            info = json.loads(info_path.read_text())
        except Exception:
            card.setVisible(False)
            return
        method = str(info.get("segmentation_method", "—")).replace("_", " ")
        fluors = ", ".join(info.get("fluor_tokens", []) or []) or "—"
        n_tp = len(info.get("available_timepoints", []) or [])
        card.setText(
            f"Last run · {out.name}\n"
            f"Method: {method}   ·   Channels: {fluors}   ·   {n_tp} timepoint(s)"
        )
        card.setVisible(True)

    def _build_log(self, parent: QWidget) -> None:
        # The same control band every Review page carries, so the Analyze pane's
        # first row is drawn level with theirs when the user switches modes: the
        # pane's title is scope, the raw-log toggle and Clear are actions. It
        # replaces a hand-built header row that was 6px shorter than the band.
        from widgets.page_body import build_page_body
        band, body = build_page_body(parent)
        rl = body.layout()
        rl.setSpacing(0)

        title = QLabel("Pipeline Output", band)
        band.add_scope("", title)
        # F4: the dashboard above IS the summary view; the verbose text log is
        # off by default and revealed on demand, so progress reads cleanly.
        self._raw_log_cb = QCheckBox("Show raw log", band)
        self._raw_log_cb.setToolTip("Show the full per-image pipeline log beneath the progress dashboard")
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        band.add_action(self._raw_log_cb)
        clear_btn = QPushButton("Clear", band)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self._clear_log)
        band.add_action(clear_btn)

        r1 = QFrame(parent); r1.setFrameShape(QFrame.HLine); r1.setObjectName("HRule")
        rl.addWidget(r1)

        # Idle last-run summary (C4): a compact readout of the most recent run
        # so the pane isn't blank when nothing's running. Hidden during a run
        # and when there's no previous run to describe.
        self._last_run_card = QLabel("", parent)
        self._last_run_card.setObjectName("Muted")
        self._last_run_card.setWordWrap(True)
        self._last_run_card.setContentsMargins(12, 6, 12, 6)
        self._last_run_card.setVisible(False)
        rl.addWidget(self._last_run_card)
        self._refresh_last_run_summary()

        # Visual run dashboard — the single place run progress is reported:
        # status + ETA (header), overall % (headline + bar), per-stage rail and
        # the live per-well plate map. The raw log sits beneath it.
        self._dashboard = RunDashboard(parent)
        rl.addWidget(self._dashboard)

        self._log_sep = QFrame(parent)
        self._log_sep.setFrameShape(QFrame.HLine)
        self._log_sep.setObjectName("HRule")
        rl.addWidget(self._log_sep)

        self._log = QTextEdit(parent)
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.NoWrap)
        # Cap the QTextDocument so a multi-hour pipeline can't grow it to
        # hundreds of megabytes. Older lines drop off the top once we hit
        # the cap; the ring buffer in allwell captures everything for
        # the help-drawer log tab anyway.
        self._log.document().setMaximumBlockCount(10000)
        font = self._log.font()
        font.setFamily("Menlo" if _sys.platform == "darwin" else "Consolas")
        font.setPointSize(9)
        self._log.setFont(font)
        rl.addWidget(self._log, 1)

        # F4: raw log hidden until the user asks for it (dashboard is the summary).
        self._on_toggle_raw_log(False)

    def _on_toggle_raw_log(self, show: bool) -> None:
        """Show/hide the verbose text log (F4). The progress dashboard stays
        visible either way; this only toggles the raw per-image stream."""
        for w in (getattr(self, "_log", None), getattr(self, "_log_sep", None)):
            if w is not None:
                w.setVisible(bool(show))

    def _build_auto_threshold_section(self, parent: QWidget) -> None:
        """Auto-threshold — every threshold-related control in one place.

        Three controls, in the order they apply: the checkbox drives the
        pipeline's ``--no_auto_threshold``; the method combo picks how the cut is
        chosen; the two well fields name the controls the second method compares.

        The method is an explicit choice rather than being inferred from whether
        the wells are filled, so switching back to Otsu does not lose them.
        Collapsed by default, like the other optional sections.
        """
        sec = self._section(
            parent, "Auto-threshold",
            collapsible=True, collapsed=True,
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

        self._auto_threshold_cb = WrapCheckBox(
            "Calculate cell-gating thresholds when the run finishes", sec,
        )
        self._auto_threshold_cb.setChecked(True)
        self._auto_threshold_cb.setToolTip(
            "On (default): the pipeline writes a default per-channel threshold "
            "into pipeline_info.json at the end of the run. Off: no thresholds "
            "are written and the Cell Gating tab starts from its own defaults."
        )
        sec.layout().addWidget(self._auto_threshold_cb)

        rmethod = self._row(sec, "Method")
        self._auto_threshold_method_cb = QComboBox(rmethod)
        self._auto_threshold_method_cb.addItem("Otsu (whole dataset)", "otsu")
        self._auto_threshold_method_cb.addItem("Positive / negative controls",
                                               "controls")
        # Narrower than "Positive / negative controls" — Qt elides the closed
        # box's text; the tooltip below spells out both options regardless.
        self._auto_threshold_method_cb.setFixedWidth(self._AUTO_THRESHOLD_METHOD_CB_W)
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
            # Text is left alone — switching to Otsu and back must not cost the
            # user their wells.
            wells = on and self._auto_threshold_method() == "controls"
            for row in self._auto_threshold_rows:
                row.setVisible(wells)

        self._auto_threshold_cb.toggled.connect(_sync_auto_threshold)
        self._auto_threshold_method_cb.currentIndexChanged.connect(_sync_auto_threshold)
        _sync_auto_threshold()

    def _auto_threshold_method(self) -> str:
        """``"otsu"`` or ``"controls"`` — the selected estimator."""
        return str(self._auto_threshold_method_cb.currentData() or "otsu")

    def _auto_threshold_validation_error(self) -> str | None:
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
        sep = self._filename_sep_edit.text() or DEFAULT_SEP
        schema = [f.strip().lower() for f in self._build_schema_arg().split(":") if f.strip()]
        from services.input_resolution_service import well_present_in_input
        missing_wells = [w for w in (pos, neg)
                         if not well_present_in_input(input_dir, w, schema=schema, sep=sep)]
        if missing_wells:
            label = ", ".join(f"'{w}'" for w in missing_wells)
            return (f"Control well {label} was not found in the input folder.\n\n"
                    f"The threshold pass reads each control well's measurements "
                    f"after the run — confirm they exist in:\n{input_dir}")
        return None

    def _background_well_validation_error(self) -> str | None:
        """When empty_well drives flat-field and/or background, the background
        well must be specified and must exist in the input. Returns an error
        message to block the run, or None when the request is valid (or empty_well
        isn't selected)."""
        uses = []
        if self._flatfield_cb.currentText() == "empty_well":
            uses.append("flat-field correction")
        if self._background_cb.currentText() == "empty_well":
            uses.append("background subtraction")
        if not uses:
            return None
        which = " and ".join(uses)
        # One or several comma-separated wells (e.g. "H12, G12"), pooled.
        raw = self._background_well_edit.text().strip()
        seen, wells = set(), []
        for tok in raw.replace(";", ",").split(","):
            w = tok.strip()
            if w and w.lower() not in seen:
                seen.add(w.lower())
                wells.append(w)
        if not wells:
            return (f"Empty-well {which} requires a Background well.\n\n"
                    "Enter one empty/background well (e.g. H12) — or several "
                    "comma-separated (e.g. H12, G12) — so its measured "
                    "background can be sampled.")
        input_dir = Path(self._input_edit.text().strip())
        sep = self._filename_sep_edit.text() or DEFAULT_SEP
        schema = [f.strip().lower() for f in self._build_schema_arg().split(":") if f.strip()]
        from services.input_resolution_service import well_present_in_input
        missing = [w for w in wells
                   if not well_present_in_input(input_dir, w, schema=schema, sep=sep)]
        if missing:
            label = ", ".join(f"'{w}'" for w in missing)
            return (f"Background well {label} was not found in the input folder.\n\n"
                    f"Empty-well {which} reads its reference images from the named "
                    f"well(s) — confirm they exist in:\n{input_dir}")
        return None

    def _collect_run_options(self) -> dict:
        method = self._seg_method()
        try:
            min_area = int(self._min_nucleus_area_edit.text().strip() or "50")
        except ValueError:
            min_area = 50
        cyto = self._cytoplasm_edit.text().strip()
        if method != "stardist_seeded_watershed_cell":
            cyto = ""
        sd_prob, sd_nms = self._stardist_threshold_values()
        opts = dict(
            raw=Path(self._input_edit.text().strip()),
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
            # Always-on (no longer user options): zip in/out + move loose TIFs.
            compress_input_well_folders=True,
            compress_output_well_folders=True,
            move_source_tifs=True,
            remote_batch_size=self._remote_batch_size_edit.text(),
            force=self._force_cb.isChecked(),
            # services.input_resolution_service.select_incremental_scope may
            # still override this during input resolution (it forces a full
            # reprocess when loose images land under a cross-well flat-field /
            # background model), but it never clears a True the form set: with
            # nothing appended it returns before touching the key.
            incremental=self._incremental_cb.isChecked(),
            force_wells=[w.strip() for w in
                         self._force_wells_edit.text().replace(";", ",").split(",")
                         if w.strip()],
            cpu_only=self._cpu_only_cb.isChecked(),
            tf_threads=self._tf_threads_edit.text(),
            workers=self._workers_edit.text(),
            stardist_tile_px=self._stardist_tile_px_edit.text(),
            stardist_prob_thresh=sd_prob,
            stardist_nms_thresh=sd_nms,
            filename_schema=self._build_schema_arg(),
            filename_sep=self._filename_sep_edit.text() or DEFAULT_SEP,
            smfish_tokens=self._smfish_tokens_list(),
            shape_features=list(self._shape_chips.checkedData()),
            flatfield=self._flatfield_cb.currentText(),
            background=self._background_cb.currentText(),
            background_well=self._background_well_edit.text().strip(),
            auto_threshold=self._auto_threshold_cb.isChecked(),
            auto_threshold_method=self._auto_threshold_method(),
            pos_control_well=self._pos_control_well_edit.text().strip(),
            neg_control_well=self._neg_control_well_edit.text().strip(),
            qc_remove_debris=self._qc_remove_debris_cb.isChecked(),
            qc_min_area=_safe_qc_float(self._qc_min_area_edit.text()),
            qc_max_area=_safe_qc_float(self._qc_max_area_edit.text()),
            qc_min_solidity=_safe_qc_float(self._qc_min_solidity_edit.text()),
            phase_token=self._phase_token(),
            segmentation_method=method,
            cytoplasm_token=cyto,
            min_nucleus_area_px=min_area,
            cellpose_model=self._cellpose_model_cb.currentText(),
            cellpose_diameter=_safe_seg_float(self._cellpose_diameter_edit.text(), 0.0),
            cellpose_flow_threshold=_safe_seg_float(self._cellpose_flow_thr_edit.text(), 0.4),
            cellpose_cellprob_threshold=_safe_seg_float(self._cellpose_cellprob_thr_edit.text(), 0.0),
            cellpose_nuclei_channel=self._cellpose_nuclei_channel_edit.text().strip(),
        )
        return opts
