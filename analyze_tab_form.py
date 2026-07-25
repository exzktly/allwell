"""Analyze-tab form construction (Qt widget builders), split from analyze_tab.py.

``AnalyzeTab`` mixes in :class:`AnalyzeTabFormMixin` to inherit the ``_build_*``
section builders verbatim — they still bind to ``self`` exactly as before, so
this is a pure relocation (no behaviour change), trimming the 1.8k-line god-class
down to its run / validation / log-poll core. The schema vocabulary constants
live here too (the form reads them) and are re-exported by ``analyze_tab``.
"""

from __future__ import annotations

import json
import sys as _sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from well_viewer.core.safe import swallowed

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

_SCHEMA_LABELS = ("Experiment", "Channel", "Well", "FOV", "Timepoint", "— ignore —")
# strict=: the two tuples are positionally paired, so a length mismatch is a
# real bug — a dropped label would silently make a schema field unreachable in
# the picker rather than raising.
_LABEL_TO_FIELD = dict(zip(_SCHEMA_LABELS, SCHEMA_FIELDS, strict=True))
_FIELD_TO_LABEL = dict(zip(SCHEMA_FIELDS, _SCHEMA_LABELS, strict=True))


class AnalyzeTabFormMixin:
    """The Analyze-tab Qt form builders. Mixed into ``AnalyzeTab`` (with
    ``QWidget``); every method binds to the ``AnalyzeTab`` instance ``self``."""

    def _section(self, parent: QWidget, title: str, info: str = "",
                 *, collapsible: bool = False, collapsed: bool = False) -> QWidget:
        """Add a labelled section to *parent* and return its body widget.

        When *info* is given, a small ``ⓘ`` marker is placed next to the title
        and the text is shown as its hover tooltip — keeping long explanatory
        notes available on demand instead of cluttering the form inline.

        When *collapsible*, the header carries a ▾/▸ caret and clicking it
        shows/hides the body; *collapsed* sets the initial state. Used (C3) to
        fold the optional/advanced sections (QC, Shape, Output, Compute,
        Scratch) by default so the required fields lead the form.
        """
        container = QWidget(parent)
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 4, 0, 4)
        cl.setSpacing(2)

        header = QWidget(container)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        caret = None
        if collapsible:
            caret = QLabel("▸" if collapsed else "▾", header)
            caret.setObjectName("SectionTitle")
            caret.setContentsMargins(12, 6, 0, 2)
            hl.addWidget(caret)
        title_lbl = QLabel(title, header)
        title_lbl.setObjectName("SectionTitle")
        title_lbl.setContentsMargins(2 if collapsible else 12, 6, 0, 2)
        hl.addWidget(title_lbl)
        if info:
            hl.addWidget(self._info_icon(header, info))
        hl.addStretch(1)
        cl.addWidget(header)

        rule = QFrame(container)
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("HRule")
        cl.addWidget(rule)

        body = QWidget(container)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 4, 12, 8)
        bl.setSpacing(3)
        cl.addWidget(body)

        if collapsible:
            body.setVisible(not collapsed)

            def _toggle(_ev=None, _body=body, _caret=caret) -> None:
                vis = not _body.isVisible()
                _body.setVisible(vis)
                _caret.setText("▾" if vis else "▸")

            header.setCursor(Qt.PointingHandCursor)
            header.mousePressEvent = _toggle  # type: ignore[assignment]

        parent.layout().addWidget(container)
        return body

    def _info_icon(self, parent: QWidget, info: str) -> QLabel:
        """A small ``ⓘ`` marker whose hover tooltip carries *info*.

        Keeps long explanatory notes available on demand instead of cluttering
        the form with inline paragraphs. A non-empty WhatsThis also surfaces the
        note via the ?-cursor; Qt wraps long tooltips on its own.
        """
        lbl = QLabel("ⓘ", parent)
        lbl.setObjectName("InfoHint")
        lbl.setToolTip(info)
        lbl.setWhatsThis(info)
        return lbl

    def _row(self, parent: QWidget, label: str) -> QWidget:
        """Append a two-column label+widget row to *parent*; return the right cell."""
        row = QWidget(parent)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(4)

        lbl = QLabel(label, row)
        lbl.setObjectName("Muted")
        lbl.setFixedWidth(150)
        rl.addWidget(lbl)

        right = QWidget(row)
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        rr = QHBoxLayout(right)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(4)
        rl.addWidget(right)

        parent.layout().addWidget(row)
        return right

    def _entry(self, parent: QWidget, default: str = "", width: int = 80) -> QLineEdit:
        """Create a QLineEdit, add it to *parent*'s layout, and return it."""
        e = QLineEdit(default, parent)
        e.setFixedWidth(width)
        parent.layout().addWidget(e)
        return e

    def _build_form(self, parent: QWidget) -> None:
        self._build_open_pinfo_row(parent)
        self._build_schema_section(parent)
        self._build_channel_tokens_section(parent)
        self._build_folders_section(parent)
        self._refresh_schema_preview()
        # Quality Control leads the optional sections (above Shape Metrics) and
        # stays open — the correction stages it configures are the common tuning
        # knobs, not an advanced afterthought.
        self._build_qc_section(parent)
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
            cb.setFixedWidth(160)
            row.layout().addWidget(cb)
            row.layout().addStretch(1)
            self._schema_cbs.append(cb)
            cb.currentIndexChanged.connect(lambda _i, c=cb: self._on_combobox_selected(c))

        schema_str_row = self._row(sec, "Schema string")
        self._schema_str_edit = QLineEdit(DEFAULT_SCHEMA, schema_str_row)
        # Expand to fill the row — a fixed 170 px clipped the full schema string
        # (e.g. "experiment:channel:well:fov:timepoint" → "nnel:well:fov:…").
        self._schema_str_edit.setMinimumWidth(220)
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
        self._seg_method_cb.setFixedWidth(230)
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

        self._fluor_frame = QWidget(sec)
        self._fluor_frame_layout = QVBoxLayout(self._fluor_frame)
        self._fluor_frame_layout.setContentsMargins(0, 0, 0, 0)
        self._fluor_frame_layout.setSpacing(2)
        sec.layout().addWidget(self._fluor_frame)
        self._fluor_add_row("GFP")

        add_btn = QPushButton("+ Add channel", sec)
        add_btn.setProperty("variant", "secondary")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(lambda _=False: self._fluor_add_row())
        sec.layout().addWidget(add_btn)

        # Per-method parameter panel — swapped by _refresh_segmentation_hints so
        # only the selected routine's controls are shown.
        from well_viewer.core.centre_view import NamedPageStack
        self._seg_param_stack = NamedPageStack(sec)
        # A QStackedWidget's height is the max over *all* pages, so the short
        # default (StarDist, no params) would still reserve the tall Cellpose
        # page's height — a big empty gap under the channel-tokens section.
        # Collapse it to the current page by giving the hidden pages a vertical
        # size policy of Ignored, re-applied on every page switch.
        self._seg_param_stack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._seg_param_stack.currentChanged.connect(
            lambda _=None: self._collapse_seg_param_stack())
        sec.layout().addWidget(self._seg_param_stack)

        def _page() -> QWidget:
            p = QWidget()
            v = QVBoxLayout(p)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            return p

        # StarDist (nuclei): no extra parameters.
        self._seg_param_stack.addPage("stardist_nuclei", _page())

        # StarDist + watershed: cytoplasm token + min nucleus area.
        ws = _page()
        cyto_cell = self._row(ws, "Cytoplasm token")
        self._cytoplasm_edit = self._entry(cyto_cell, "", width=80)
        cyto_cell.layout().addStretch(1)
        self._cytoplasm_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())
        area_cell = self._row(ws, "Min nucleus area")
        self._min_nucleus_area_edit = self._entry(area_cell, "50", width=60)
        _px = QLabel("pixels", area_cell)
        _px.setObjectName("Muted")
        area_cell.layout().addWidget(_px)
        area_cell.layout().addStretch(1)
        self._min_nucleus_area_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())
        self._seg_param_stack.addPage("stardist_seeded_watershed_cell", ws)

        # Cellpose (general): model, diameter, flow/cell-prob thresholds, optional
        # second (nucleus) channel for cyto models. Segments the Segmentation
        # channel above (fluorescent or brightfield).
        cp = _page()
        m_cell = self._row(cp, "Model")
        self._cellpose_model_cb = QComboBox(cp)
        self._cellpose_model_cb.addItems(["cyto3", "cyto2", "cyto", "nuclei"])
        m_cell.layout().addWidget(self._cellpose_model_cb)
        m_cell.layout().addStretch(1)
        d_cell = self._row(cp, "Diameter (px, 0=auto)")
        self._cellpose_diameter_edit = self._entry(d_cell, "0", width=70)
        d_cell.layout().addStretch(1)
        f_cell = self._row(cp, "Flow threshold")
        self._cellpose_flow_thr_edit = self._entry(f_cell, "0.4", width=70)
        f_cell.layout().addStretch(1)
        c_cell = self._row(cp, "Cell-prob threshold")
        self._cellpose_cellprob_thr_edit = self._entry(c_cell, "0.0", width=70)
        c_cell.layout().addStretch(1)
        n_cell = self._row(cp, "Nucleus channel (opt)")
        self._cellpose_nuclei_channel_edit = self._entry(n_cell, "", width=80)
        n_cell.layout().addStretch(1)
        self._seg_param_stack.addPage("cellpose", cp)

        # Label-free (Cellpose preset on the Phase channel) — no exposed params.
        lf = _page()
        lf_lbl = QLabel("Segments the channel marked 'Phase' above, using a Cellpose "
                        "brightfield preset (cyto3, auto diameter).", lf)
        lf_lbl.setObjectName("Muted")
        lf_lbl.setWordWrap(True)
        lf.layout().addWidget(lf_lbl)
        self._seg_param_stack.addPage("cellpose_labelfree", lf)

        # Classical (Otsu + watershed): no extra parameters.
        self._seg_param_stack.addPage("classical_threshold", _page())

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
        self._shape_chips.setMaximumWidth(230)
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
        self._no_tophat_fluor_cb = QCheckBox("Disable", fluor_row)
        fluor_row.layout().addWidget(self._no_tophat_fluor_cb)
        fluor_row.layout().addWidget(self._info_icon(
            fluor_row,
            "Radius = background scale (px); Fluor radius applies to all "
            "fluorescent channels. For fft_bandpass it is the large-structure "
            "(low-frequency) cutoff; for robust_mesh it is the tile size.",
        ))
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

        tf_row = self._row(sec, "TF threads (0=auto)")
        self._tf_threads_edit = self._entry(tf_row, "0", width=40)
        tf_hint = QLabel("(0 → auto-select 4)", tf_row)
        tf_hint.setObjectName("Muted")
        tf_row.layout().addWidget(tf_hint)
        tf_row.layout().addStretch(1)

        w_row = self._row(sec, "Workers (0=auto)")
        self._workers_edit = self._entry(w_row, "0", width=40)
        w_hint = QLabel("(process count override)", w_row)
        w_hint.setObjectName("Muted")
        w_row.layout().addWidget(w_hint)
        w_row.layout().addStretch(1)

        # StarDist prediction tiling — caps per-worker peak RAM on large frames
        # (an untiled ~4K predict can use several GB/worker, so parallel workers
        # exhaust memory). The value is the MAX pixels per tile SIDE (not the
        # number of tiles): StarDist splits the frame into as many tiles as
        # needed so no block exceeds it. 0 = off (default).
        # Label kept short so it fits the 150 px label column — a longer string
        # clips mid-word and leaves a stray "=" hanging at the edge. The px/side
        # meaning is spelled out in the info-icon tooltip below.
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
        sec.layout().addWidget(self._force_cb)

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

    # ── Load a prior run's settings from its pipeline_info.json sidecar ───────
    def _open_pipeline_info(self) -> None:
        """Pick a pipeline_info.json and repopulate every form field from it."""
        start = ""
        try:
            start = self._input_edit.text().strip()
        except Exception:
            swallowed()
        path, _ = QFileDialog.getOpenFileName(
            self, "Open pipeline_info.json", start,
            "Pipeline info (pipeline_info.json);;JSON (*.json);;All files (*.*)",
        )
        if not path:
            return
        try:
            info = json.loads(Path(path).read_text())
            if not isinstance(info, dict):
                raise ValueError("not a pipeline_info object")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Open failed",
                                 f"Could not read pipeline_info:\n{exc}")
            return
        try:
            self._apply_pipeline_info(info, source=Path(path))
        except Exception as exc:  # never crash the slot
            QMessageBox.warning(self, "Partial load",
                                f"Some fields could not be restored:\n{exc}")

    @staticmethod
    def _pinfo_flat(info: dict) -> dict:
        """Flatten a pipeline_info dict to argparse-style keys.

        Prefers ``execution_options`` (the full ``vars(args)`` snapshot recorded
        by the pipeline, v2+); falls back to the top-level / ``qc_options`` keys
        for older sidecars that predate that block.
        """
        exec_opts = info.get("execution_options")
        if isinstance(exec_opts, dict) and exec_opts:
            return dict(exec_opts)
        qc = info.get("qc_options") or {}
        flat = {
            "filename_schema": info.get("schema"),
            "filename_sep": info.get("separator"),
            "nuclear_token": info.get("nuclear_token"),
            "fluor_tokens": info.get("fluor_tokens"),
            "smfish_tokens": info.get("smfish_tokens"),
            "shape_features": info.get("shape_features"),
            "segmentation_method": info.get("segmentation_method"),
            "cytoplasm_token": info.get("cytoplasm_token"),
            "min_nucleus_area_px": info.get("min_nucleus_area_px"),
            "phase_token": info.get("phase_token"),
            "flatfield": qc.get("flatfield"),
            "background": qc.get("background"),
            "background_well": qc.get("background_well"),
            "fft_small_nir": qc.get("fft_small_nir"),
            "fft_small_fluor": qc.get("fft_small_fluor"),
            "mesh_ratio": qc.get("mesh_ratio"),
            "mesh_sigma": qc.get("mesh_sigma"),
            "qc_remove_debris": qc.get("remove_debris"),
            "qc_min_area": qc.get("min_area"),
            "qc_max_area": qc.get("max_area"),
            "qc_min_solidity": qc.get("min_solidity"),
        }
        return {k: v for k, v in flat.items() if v is not None}

    def _apply_pipeline_info(self, info: dict, *, source: Path | None = None) -> None:
        d = self._pinfo_flat(info)

        def s(attr, val):
            w = getattr(self, attr, None)
            if w is not None and val is not None:
                w.setText(str(val))

        def chk(attr, val):
            w = getattr(self, attr, None)
            if w is not None and val is not None:
                w.setChecked(bool(val))

        def combo(attr, val):
            w = getattr(self, attr, None)
            if w is not None and val:
                w.setCurrentText(str(val))

        # Schema first (separator, then the string → sync the per-field combos).
        s("_filename_sep_edit", d.get("filename_sep"))
        if d.get("filename_schema"):
            self._schema_str_edit.setText(str(d["filename_schema"]))
            try:
                self._sync_dropdowns_from_string()
            except Exception:
                swallowed()

        # Segmentation method — the combo carries the canonical value as itemData.
        method = d.get("segmentation_method")
        if method:
            idx = self._seg_method_cb.findData(method)
            if idx >= 0:
                self._seg_method_cb.setCurrentIndex(idx)

        # Channel tokens.
        s("_nuclear_token_edit", d.get("nuclear_token"))
        self._set_fluor_rows(
            list(d.get("fluor_tokens") or []),
            smfish=list(d.get("smfish_tokens") or []),
            phase=str(d.get("phase_token") or "").strip(),
        )

        # Cellpose / watershed params.
        combo("_cellpose_model_cb", d.get("cellpose_model"))
        s("_cellpose_diameter_edit", d.get("cellpose_diameter"))
        s("_cellpose_flow_thr_edit", d.get("cellpose_flow_threshold"))
        s("_cellpose_cellprob_thr_edit", d.get("cellpose_cellprob_threshold"))
        s("_cellpose_nuclei_channel_edit", d.get("cellpose_nuclei_channel"))
        s("_cytoplasm_edit", d.get("cytoplasm_token"))
        s("_min_nucleus_area_edit", d.get("min_nucleus_area_px"))

        # Shape features.
        feats = d.get("shape_features")
        if feats is not None and hasattr(self, "_shape_chips"):
            try:
                self._shape_chips.setCheckedData(list(feats))
            except Exception:
                swallowed()

        # QC.
        combo("_flatfield_cb", d.get("flatfield"))
        combo("_background_cb", d.get("background"))
        s("_background_well_edit", d.get("background_well"))
        chk("_qc_remove_debris_cb", d.get("qc_remove_debris"))
        s("_qc_min_area_edit", d.get("qc_min_area"))
        s("_qc_max_area_edit", d.get("qc_max_area"))
        s("_qc_min_solidity_edit", d.get("qc_min_solidity"))
        s("_tophat_radius_nir_edit", d.get("tophat_radius_nir"))
        s("_tophat_radius_fluor_edit", d.get("tophat_radius_fluor"))
        chk("_no_tophat_nir_cb", d.get("no_tophat_nir"))
        chk("_no_tophat_fluor_cb", d.get("no_tophat_fluor"))
        s("_fft_small_nir_edit", d.get("fft_small_nir"))
        s("_fft_small_fluor_edit", d.get("fft_small_fluor"))
        s("_mesh_ratio_edit", d.get("mesh_ratio"))
        s("_mesh_sigma_edit", d.get("mesh_sigma"))

        # Output / compute.
        s("_csv_prefix_edit", d.get("csv_prefix"))
        s("_tf_threads_edit", d.get("tf_threads"))
        s("_workers_edit", d.get("workers"))
        s("_stardist_tile_px_edit", d.get("stardist_tile_px"))
        chk("_cpu_only_cb", d.get("cpu_only"))
        chk("_force_cb", d.get("force"))

        # Scratch disk — a recorded value pins the field; absent, it stays blank
        # so the pipeline default (~/.allwell/scratch) applies.
        scratch = d.get("scratch_dir")
        if scratch and hasattr(self, "_scratch_edit"):
            self._scratch_edit.setText(str(scratch))
            self._scratch_user_set = True

        # Paths last: prefer the recorded input_dir; else infer from the
        # sidecar's own folder (it lives in the output dir).
        in_dir = d.get("input_dir")
        if not in_dir and source is not None:
            out = source.parent
            sib = out.parent / "in"
            in_dir = str(sib if sib.is_dir() else out.parent)
        if in_dir:
            self._input_edit.setEnabled(True)
            if getattr(self, "_browse_btn", None) is not None:
                self._browse_btn.setEnabled(True)
            self._input_edit.setText(str(in_dir))
        for refresh in ("_refresh_output", "_refresh_schema_preview",
                        "_refresh_segmentation_hints"):
            fn = getattr(self, refresh, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    swallowed()
        try:
            self._dashboard.set_status(
                f"Loaded settings from {source.name}" if source else "Loaded settings")
        except Exception:
            swallowed()

    def _set_fluor_rows(self, tokens, *, smfish, phase) -> None:
        """Repopulate the fluor-channel rows to *tokens*, restoring smFISH/Phase flags."""
        tokens = [str(t).strip() for t in tokens if str(t).strip()] or ["GFP"]
        # Collapse to a single row, then grow to match the loaded tokens.
        while len(self._fluor_rows) > 1:
            _e, _s, _p, b = self._fluor_rows[-1]
            self._fluor_remove_row(b.parent(), b)
        self._fluor_rows[0][0].setText(tokens[0])
        for tok in tokens[1:]:
            self._fluor_add_row(tok)
        sm, ph = set(smfish or []), (phase or "").strip()
        for e, s_cb, p_cb, _b in self._fluor_rows:
            tok = e.text().strip()
            s_cb.setChecked(tok in sm)
            p_cb.setChecked(bool(ph) and tok == ph)

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
        bs_hint.setObjectName("Muted")
        bs_hint.setWordWrap(True)
        bs_row.layout().addWidget(bs_hint, 1)

        # Auto-delete checkbox.
        self._cleanup_remote_cb = QCheckBox(
            "Auto-delete remote files when done", self._remote_params_widget,
        )
        self._cleanup_remote_cb.setToolTip(
            "v5.2.11+: unchecked (default) = leave everything on the remote "
            "untouched. Safe for shared machines. Checked = after a "
            "verified-successful run, delete both the per-run output dir "
            "AND the cached input on the remote."
        )
        rp.addWidget(self._cleanup_remote_cb)

        # Dry-run cleanup (v5.2.23): log what auto-delete WOULD remove without
        # actually removing anything. Only enabled when auto-delete is checked.
        self._dry_run_cleanup_cb = WrapCheckBox(
            "Dry-run cleanup (show what would be deleted, don't delete)",
            self._remote_params_widget,
        )
        self._dry_run_cleanup_cb.setToolTip(
            "v5.2.23: with auto-delete enabled, log path + file count + "
            "total bytes for each remote directory that WOULD be removed "
            "at end-of-run, without actually removing anything. Useful "
            "for confirming what auto-delete will do on a shared remote "
            "before committing to it."
        )
        rp.addWidget(self._dry_run_cleanup_cb)

        parent.layout().addWidget(self._remote_params_widget)

        # Show the remote-only group only when a remote is selected.
        self._remote_cb.currentIndexChanged.connect(self._refresh_remote_controls)
        # Dry-run only makes sense when auto-delete is on.
        self._cleanup_remote_cb.toggled.connect(self._refresh_remote_controls)
        self._refresh_remote_controls()

        btn_widget = QWidget(parent)
        bl = QHBoxLayout(btn_widget)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(8)

        self._run_btn = QPushButton("▶  Run Pipeline", btn_widget)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.clicked.connect(self._run)
        bl.addWidget(self._run_btn)

        self._stop_btn = QPushButton("■  Stop", btn_widget)
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        bl.addWidget(self._stop_btn)
        bl.addStretch(1)

        parent.layout().addWidget(btn_widget)

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
        rl = QVBoxLayout(parent)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        hdr = QWidget(parent)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Pipeline Output", hdr))
        hl.addStretch(1)
        # F4: the dashboard above IS the summary view; the verbose text log is
        # off by default and revealed on demand, so progress reads cleanly.
        self._raw_log_cb = QCheckBox("Show raw log", hdr)
        self._raw_log_cb.setToolTip("Show the full per-image pipeline log beneath the progress dashboard")
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        hl.addWidget(self._raw_log_cb)
        clear_btn = QPushButton("Clear", hdr)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self._clear_log)
        hl.addWidget(clear_btn)
        rl.addWidget(hdr)

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

