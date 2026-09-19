"""The Analyze form's **Quality Control** section: its widgets and their rules.

One section of the form, gathered into one module, the way
:mod:`analyze.schema_section` and :mod:`analyze.seg_params` already are. It
came out of ``analyze/form.py`` because that file sat one line under its
budget while this section had two controls still to gain, and because the
section is genuinely cohesive: everything here is a pre-measurement decision
about the pixels, and nothing here is about segmentation or output.

Three independent things live under one heading, and the section's own
tooltip says so, because collapsing any two of them is the substitution this
subsystem exists to prevent:

* **The pixel corrections** — flat-field, then background subtraction. These
  change the measured values, so they can only be revisited by re-running.
* **The debris gate** — a size / shape rule about regions, applied at
  quantification time and editable afterwards in Review's Segmentation tab.
* **The focus measurement** — a per-cell number the run records and *does not
  act on*. See :func:`_build_focus_rows` for why the threshold is recorded
  rather than applied, which is the one decision in this module that is not
  obvious from the control.

Mixed into ``AnalyzeTabFormMixin``, so every widget still lands on the
``AnalyzeTab`` instance under the ``self._<name>`` attribute the rest of the
pane reaches for: ``analyze/pinfo.py`` restores them by name,
``analyze/form.py`` reads them when it assembles the run's options, and
``analyze/bg_map.py`` owns the background-map row this section hosts.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QWidget

from widgets.wrap_checkbox import WrapCheckBox


class AnalyzeQCSectionMixin:
    """The Quality Control section's builders. Binds to the ``AnalyzeTab``."""

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
        self._background_well_edit.setPlaceholderText("H12, G12 or H01:H12")
        self._background_well_edit.setToolTip(
            "Well label(s) of empty/background well(s) used as the reference for "
            "any empty_well mode above. Enter one (e.g. H12), several "
            "comma-separated (e.g. H12, G12), or a rectangular block written "
            "with a colon (H01:H12 is the whole of row H, A01:D12 the top half) "
            "— and you can mix them (H01:H12, A01). Multiple wells are pooled "
            "for a cleaner estimate, unless Pair blanks… assigns each one its "
            "own block of wells. Their smoothed per-pixel median supplies the "
            "illumination field (flat-field) and/or the measured additive "
            "background, computed PER TIMEPOINT (background drifts over time); a "
            "timepoint the blank wells lack falls back to the all-timepoint "
            "pooled median. Read from whichever input layout holds the well "
            "(per-well zip, per-well folder, or loose TIFs); enabled only when "
            "flat-field or background is empty_well.")
        rbw.layout().addStretch(1)
        self._build_background_map_row(sec)

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
            self._sync_background_map_enabled()
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
