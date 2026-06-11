"""Analyze-tab form construction (Qt widget builders), split from analyze_tab.py.

``AnalyzeTab`` mixes in :class:`AnalyzeTabFormMixin` to inherit the ``_build_*``
section builders verbatim — they still bind to ``self`` exactly as before, so
this is a pure relocation (no behaviour change), trimming the 1.8k-line god-class
down to its run / validation / log-poll core. The schema vocabulary constants
live here too (the form reads them) and are re-exported by ``analyze_tab``.
"""

from __future__ import annotations

import sys as _sys

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from services.remote_config import load_remotes
from widgets.checkable_dropdown import CheckableDropdown
from widgets.run_dashboard import RunDashboard
from widgets.wrap_checkbox import WrapCheckBox
import shape_features_core as _sfc

# ---------------------------------------------------------------------------
# Schema vocabulary — kept in sync with process_microscopy.py
# ---------------------------------------------------------------------------
SCHEMA_FIELDS  = ("experiment", "channel", "well", "fov", "timepoint", "ignore")
DEFAULT_SCHEMA = "experiment:channel:well:fov:timepoint"
DEFAULT_SEP    = "_"

_SCHEMA_LABELS = ("Experiment", "Channel", "Well", "FOV", "Timepoint", "— ignore —")
_LABEL_TO_FIELD = dict(zip(_SCHEMA_LABELS, SCHEMA_FIELDS))
_FIELD_TO_LABEL = dict(zip(SCHEMA_FIELDS, _SCHEMA_LABELS))


class AnalyzeTabFormMixin:
    """The Analyze-tab Qt form builders. Mixed into ``AnalyzeTab`` (with
    ``QWidget``); every method binds to the ``AnalyzeTab`` instance ``self``."""

    def _section(self, parent: QWidget, title: str, info: str = "") -> QWidget:
        """Add a labelled section to *parent* and return its body widget.

        When *info* is given, a small ``ⓘ`` marker is placed next to the title
        and the text is shown as its hover tooltip — keeping long explanatory
        notes available on demand instead of cluttering the form inline.
        """
        container = QWidget(parent)
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 4, 0, 4)
        cl.setSpacing(2)

        if info:
            header = QWidget(container)
            hl = QHBoxLayout(header)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(4)
            title_lbl = QLabel(title, header)
            title_lbl.setObjectName("SectionTitle")
            title_lbl.setContentsMargins(12, 6, 0, 2)
            hl.addWidget(title_lbl)
            info_lbl = QLabel("ⓘ", header)
            info_lbl.setObjectName("InfoHint")
            info_lbl.setToolTip(info)
            # A non-empty WhatsThis also surfaces the note via the ?-cursor and
            # keeps the tooltip readable (Qt wraps long tooltips on its own).
            info_lbl.setWhatsThis(info)
            hl.addWidget(info_lbl)
            hl.addStretch(1)
            cl.addWidget(header)
        else:
            title_lbl = QLabel(title, container)
            title_lbl.setObjectName("SectionTitle")
            title_lbl.setContentsMargins(12, 6, 12, 2)
            cl.addWidget(title_lbl)

        rule = QFrame(container)
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("HRule")
        cl.addWidget(rule)

        body = QWidget(container)
        body
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 4, 12, 8)
        bl.setSpacing(3)
        cl.addWidget(body)

        parent.layout().addWidget(container)
        return body

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
        self._build_schema_section(parent)
        self._build_channel_tokens_section(parent)
        self._build_folders_section(parent)
        self._refresh_schema_preview()
        self._build_shape_features_section(parent)
        self._build_qc_section(parent)
        self._build_output_options_section(parent)
        self._build_compute_options_section(parent)
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
        self._schema_str_edit.setFixedWidth(170)
        schema_str_row.layout().addWidget(self._schema_str_edit)
        schema_str_row.layout().addStretch(1)
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
        for _value, _label in self._SEG_METHODS:
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

        fl_title = QLabel("Fluorescent channels", sec)
        fl_title.setContentsMargins(0, 6, 0, 2)
        fl_title.setWordWrap(True)
        sec.layout().addWidget(fl_title)
        fl_hint = QLabel("Mark smFISH channels, or one channel as Phase for "
                         "label-free segmentation, with the checkboxes on each row.", sec)
        fl_hint.setObjectName("Muted")
        fl_hint.setWordWrap(True)
        sec.layout().addWidget(fl_hint)

        self._fluor_frame = QWidget(sec)
        self._fluor_frame
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
        # Default to the isotropic sliding_disk: top_hat's square footprint leaves
        # telltale square halos, and since the dropdown doesn't persist, a top_hat
        # default meant runs silently used the square method unless re-selected.
        self._background_cb.setCurrentText("sliding_disk")
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
        fluor_row.layout().addStretch(1)
        radius_hint = QLabel("Radius = background scale (px); Fluor radius "
                             "applies to all fluorescent channels. For "
                             "fft_bandpass it is the large-structure (low-"
                             "frequency) cutoff; for robust_mesh it is the tile "
                             "size.", sec)
        radius_hint.setObjectName("Muted")
        radius_hint.setWordWrap(True)
        sec.layout().addWidget(radius_hint)
        # _row() returns the right-hand cell, not the full row — the "Nuclear
        # radius" / "Fluor radius" label lives in the cell's parent. Toggle
        # visibility on the *parent* rows so the labels hide with their entries
        # (hiding only the cell left the labels stranded).
        self._bg_radius_rows = (nir_row.parentWidget(), fluor_row.parentWidget(),
                                radius_hint)

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
        # group that appears only when relevant.
        self._qc_debris_rows = (ra, rs)

        def _sync_debris(_=None) -> None:
            on = self._qc_remove_debris_cb.isChecked()
            for row in self._qc_debris_rows:
                row.setVisible(on)
        self._qc_remove_debris_cb.toggled.connect(_sync_debris)
        _sync_debris()

    def _build_output_options_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Output Options")

        self._compress_input_cb = WrapCheckBox(
            "Folder mode only: Compress input well folders to .zip", sec)
        self._compress_input_cb.setChecked(True)
        sec.layout().addWidget(self._compress_input_cb)

        # v5.x.x: opt-in MOVE semantics for the WellPlateZipper. When
        # off (default) the zipper copies loose TIFs into per-well
        # subfolders and leaves the originals at raw/, then PR #336's
        # on_success hook deletes them after a clean run — disk usage
        # peaks at ~2x. When on, ``--move-source-tifs`` is passed and
        # peak usage stays close to the input footprint, at the cost
        # of losing a clean-retry path on a mid-zipper crash.
        self._move_source_tifs_cb = WrapCheckBox(
            "Loose-TIF mode only: Move source TIFs into well folders "
            "(saves ~2× peak disk; sources gone if zipper crashes mid-run)",
            sec,
        )
        self._move_source_tifs_cb.setChecked(False)
        sec.layout().addWidget(self._move_source_tifs_cb)

        self._compress_output_cb = WrapCheckBox(
            "Folder mode only: Compress output well folders to .zip", sec)
        self._compress_output_cb.setChecked(True)
        sec.layout().addWidget(self._compress_output_cb)

        r = self._row(sec, "CSV prefix")
        self._csv_prefix_edit = self._entry(r, "measurements", width=160)
        r.layout().addStretch(1)

    def _build_compute_options_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Compute Options")

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

        self._cpu_only_cb = QCheckBox("CPU only (disable GPU)", sec)
        sec.layout().addWidget(self._cpu_only_cb)
        self._force_cb = QCheckBox("Force reprocess all wells", sec)
        sec.layout().addWidget(self._force_cb)

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
        btn_widget
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

    def _build_log(self, parent: QWidget) -> None:
        rl = QVBoxLayout(parent)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        hdr = QWidget(parent)
        hdr
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Pipeline Output", hdr))
        hl.addStretch(1)
        clear_btn = QPushButton("Clear", hdr)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self._clear_log)
        hl.addWidget(clear_btn)
        rl.addWidget(hdr)

        r1 = QFrame(parent); r1.setFrameShape(QFrame.HLine); r1.setObjectName("HRule")
        rl.addWidget(r1)

        # Visual run dashboard — the single place run progress is reported:
        # status + ETA (header), overall % (headline + bar), per-stage rail and
        # the live per-well plate map. The raw log sits beneath it.
        self._dashboard = RunDashboard(parent)
        rl.addWidget(self._dashboard)

        r2 = QFrame(parent); r2.setFrameShape(QFrame.HLine); r2.setObjectName("HRule")
        rl.addWidget(r2)

        self._log = QTextEdit(parent)
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.NoWrap)
        # Cap the QTextDocument so a multi-hour pipeline can't grow it to
        # hundreds of megabytes. Older lines drop off the top once we hit
        # the cap; the ring buffer in all_well captures everything for
        # the help-drawer log tab anyway.
        self._log.document().setMaximumBlockCount(10000)
        font = self._log.font()
        font.setFamily("Menlo" if _sys.platform == "darwin" else "Consolas")
        font.setPointSize(9)
        self._log.setFont(font)
        rl.addWidget(self._log, 1)

