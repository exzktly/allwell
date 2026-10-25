"""The Analyze form's **Image correction & QC** section: its widgets and their rules.

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

from PySide6.QtWidgets import QCheckBox, QLabel, QWidget

from widgets.value_combo import ValueCombo
from widgets.wrap_checkbox import WrapCheckBox

#: Flat-field choices, ``(pipeline value, label)``. The value is the
#: ``--flatfield`` argument and what ``pipeline_info.json`` records; the label is
#: what the form shows. ``none`` leads because it is the default.
FLATFIELD_OPTIONS = (
    ("none", "None"),
    ("empty_well", "From blank well(s)"),
    ("gaussian", "Self (Gaussian blur)"),
    ("basic", "Whole plate (BaSiC)"),
)

#: Background-subtraction choices, ``(pipeline value, label)``, as above.
BACKGROUND_OPTIONS = (
    ("none", "None"),
    ("robust_mesh", "Robust local mesh"),
    ("sliding_disk", "Rolling disk"),
    ("top_hat", "Top-hat"),
    ("fov_median", "Field median"),
    ("empty_well", "From blank well(s)"),
    ("fft_bandpass", "FFT band-pass"),
)

#: What a fresh form runs with: the command line's own defaults, so a run
#: started here and one started from ``process_microscopy.py`` with no flags
#: correct the same way — no flat-field, and a rolling-disk background. Neither
#: needs a blank well; the flat-field default used to be ``empty_well``, which
#: refused to run until one was named, in a section a new user had never opened.
DEFAULT_FLATFIELD = "none"
DEFAULT_BACKGROUND = "sliding_disk"


class AnalyzeQCSectionMixin:
    """The Image correction & QC section's builders. Binds to the ``AnalyzeTab``."""

    def _build_image_correction_section(self, parent: QWidget) -> None:
        sec = self._section(
            parent, "Image correction",
            collapsible=True, collapsed=False,
            summary=self._image_correction_summary,
            info="Applied per channel before measuring: flat-field (divide out "
                 "uneven illumination), then background subtraction (remove a "
                 "baseline). Both change the measured values, so they can only "
                 "be changed by re-running.",
        )

        r = self._row(sec, "Flat-field correction")
        self._flatfield_cb = ValueCombo(FLATFIELD_OPTIONS, r)
        # The dropdown doesn't persist, so this build default is what fresh runs
        # use; reprocessing a dataset still restores its saved pipeline_info value.
        self._flatfield_cb.setValue(DEFAULT_FLATFIELD)
        self._flatfield_cb.setToolTip(
            "Evens out uneven illumination by dividing each channel by a "
            "smooth, mean-1 field, before background subtraction.\n"
            "• None — no correction (the default).\n"
            "• From blank well(s) — the field is the Background well's own "
            "intensity. Needs a Background well.\n"
            "• Self (Gaussian blur) — each image's own heavy blur; no reference.\n"
            "• Whole plate (BaSiC) — the median of a sample across the plate, "
            "one field per channel.")
        r.layout().addWidget(self._flatfield_cb)
        r.layout().addStretch(1)

        rbg = self._row(sec, "Background subtraction")
        self._background_cb = ValueCombo(BACKGROUND_OPTIONS, rbg)
        # Robust local mesh is the method to reach for when background needs
        # removing — a sigma-clipped local background on a coarse grid
        # (SExtractor/Background2D style), robust to bright/confluent objects on
        # uneven background where the morphological methods (top-hat's square
        # halos, rolling disk) struggle — which is why it is listed first after
        # None. It is not the default: see DEFAULT_BACKGROUND.
        self._background_cb.setValue(DEFAULT_BACKGROUND)
        self._background_cb.setToolTip(
            "How a baseline is removed from each channel:\n"
            "• None — nothing removed.\n"
            "• Robust local mesh — a sigma-clipped background on a coarse grid; "
            "best on uneven background with bright or confluent objects.\n"
            "• Rolling disk — a smooth disk baseline (the default, as on the "
            "command line).\n"
            "• Top-hat — fast, but leaves square halos on uneven background.\n"
            "• Field median — one number per image; does not flatten gradients.\n"
            "• From blank well(s) — the Background well's measured background.\n"
            "• FFT band-pass — removes low frequencies whatever the object size.\n"
            "The radius below sets each method's scale.")
        rbg.layout().addWidget(self._background_cb)
        rbg.layout().addStretch(1)

        # Radius / enable controls — folded in from the former standalone
        # "Top-Hat Background Subtraction" section. Shown only for the
        # radius-based methods (top_hat / sliding_disk); hidden for empty_well /
        # none, which take no radius.
        nir_row = self._row(sec, "Segmentation-channel radius")
        self._tophat_radius_nir_edit = self._entry(nir_row, "100", width=60)
        self._no_tophat_nir_cb = QCheckBox("Disable", nir_row)
        nir_row.layout().addWidget(self._no_tophat_nir_cb)
        nir_row.layout().addStretch(1)
        fluor_row = self._row(sec, "Other channels' radius")
        self._tophat_radius_fluor_edit = self._entry(fluor_row, "100", width=60)
        fluor_row.layout().addWidget(self._info_icon(
            fluor_row,
            "Radius = background scale (px); the second radius applies to every "
            "channel other than the segmentation channel. For FFT band-pass it is "
            "the large-structure (low-frequency) cutoff; for Robust local mesh it "
            "is the tile size.",
        ))
        self._no_tophat_fluor_cb = QCheckBox("Disable", fluor_row)
        fluor_row.layout().addWidget(self._no_tophat_fluor_cb)
        fluor_row.layout().addStretch(1)
        # _row() returns the right-hand cell, not the full row — the
        # "Segmentation-channel radius" / "Other channels' radius" label lives in
        # the cell's parent. Toggle
        # visibility on the *parent* rows so the labels hide with their entries
        # (hiding only the cell left the labels stranded).
        self._bg_radius_rows = (nir_row.parentWidget(), fluor_row.parentWidget())

        # fft_bandpass-only small-structure (high-frequency) cutoffs — the
        # optional low-pass that denoises for a true band-pass. 0 = high-pass
        # only (preserves signal; the default, faithful for quantification).
        fft_nir_row = self._row(sec, "Segmentation small px")
        self._fft_small_nir_edit = self._entry(fft_nir_row, "0", width=60)
        fft_nir_row.layout().addStretch(1)
        fft_fluor_row = self._row(sec, "Other small px")
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
            "The empty well(s) a From blank well(s) correction reads: one (H12), "
            "several (H12, G12) or a block (H01:H12), mixed freely. Several are "
            "pooled, per timepoint, unless Pair blanks… gives each its own "
            "wells.")
        rbw.layout().addStretch(1)
        self._background_well_row = rbw.parentWidget()
        # Stated while the field is shown and empty: a correction that reads a
        # blank well cannot run without one, and the run would otherwise refuse
        # in a dialog about a field the user may never have looked at.
        self._background_well_needed_lbl = QLabel(
            "Required — name the blank well(s) this correction reads.", sec)
        self._background_well_needed_lbl.setObjectName("Error")
        self._background_well_needed_lbl.setWordWrap(True)
        sec.layout().addWidget(self._background_well_needed_lbl)
        self._build_background_map_row(sec)

        def _sync_bg(_=None) -> None:
            bg_method = self._background_cb.value()
            # Background well feeds empty_well flat-field and/or empty_well bg,
            # and is hidden — not greyed — when neither reads it, so a field that
            # cannot change the run does not sit on screen looking like one.
            uses_blank = "empty_well" in (self._flatfield_cb.value(), bg_method)
            self._background_well_edit.setEnabled(uses_blank)
            self._background_well_row.setVisible(uses_blank)
            self._background_well_needed_lbl.setVisible(
                uses_blank and not self._background_well_edit.text().strip())
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
        self._background_well_edit.textChanged.connect(_sync_bg)
        _sync_bg()

        self._refresh_section_summary("Image correction")

    def _image_correction_summary(self) -> str:
        """The folded header's line: the two corrections, by their labels."""
        return (f"{self._flatfield_cb.currentText()} · "
                f"{self._background_cb.currentText()}")

    def _build_measurements_section(self, parent: QWidget) -> None:
        """What is measured beyond intensity, and what is filtered out: shape
        metrics, the debris gate and the per-cell focus measurement."""
        sec = self._section(
            parent, "Measurements & filters",
            collapsible=True, collapsed=True,
            summary=lambda: self._changed_summary("Measurements & filters"),
            info="Per-cell shape metrics, a size / solidity filter for debris "
                 "(editable later in Review's Segmentation tab), and the "
                 "per-cell focus measurement.",
        )
        self._build_shape_metrics_row(sec)
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

        self._build_focus_rows(sec)

    def _build_shape_metrics_row(self, sec: QWidget) -> None:
        """The shape-metric dropdown: per-cell morphology features (emitted as
        ``shape_…`` columns; cell centroids are always recorded)."""
        import pipeline.shape_features as _sfc
        from widgets.checkable_dropdown import CheckableDropdown

        row = self._row(sec, "Shape metrics", tooltip=(
            "Per-cell morphology features to measure, written as shape_… "
            "columns. Cell centroids are always recorded."))
        self._shape_chips = CheckableDropdown(row, noun="metrics selected",
                                              empty_text="No metrics selected")
        for key in _sfc.SELECTABLE_KEYS:
            self._shape_chips.addItem(_sfc.BY_KEY[key].label, data=key)
        self._shape_chips.setCheckedData(list(self._SHAPE_DEFAULTS))
        row.layout().addWidget(self._shape_chips, 1)

    def _build_focus_rows(self, sec: QWidget) -> None:
        """Per-cell focus: measure it by default, and state a cut without
        applying one.

        **The measurement is on by default and the cut is not set**, and the
        asymmetry is the whole design.

        Measuring costs a few percent of quantification time and one CSV
        column, and it is the only evidence a later reader has that part of a
        field was soft. A run without it cannot be asked the question
        afterwards without being re-run over the images, so the default is on
        and the checkbox is there for a user who knows they do not want the
        column.

        The cut is the opposite. It is mostly a number about a
        **microscope** — it depends on the point-spread function, the pixel
        size and the nuclear contrast. The column is a transition width in
        pixels, so gain, pedestal, brightness, nucleus size and crowding each
        move it by under 5 %, and a cut carries from plate to plate on one
        instrument at one magnification. Two things do move it and a user
        carrying a number between plates should know both: a clipped nucleus
        reads sharper (to 1.45x), and chromatin texture reaches 1.14x. Either
        way this application cannot guess it, and measuring said so rather than leaving it unexamined:
        Otsu's method on the per-cell values returns a number for a plate with
        nothing wrong with it and throws away two fifths of its good cells,
        and on a uniformly defocused plate it splits the bad population
        against itself and keeps three quarters of the cells it exists to
        catch. Otsu's own separability cannot tell those two cases apart from
        the useful ones, so there is nothing to gate a recommendation on.
        Review's Image Playground draws the distribution instead, which is
        where a user finds their own number.

        And the run **records** that number rather than acting on it. A
        threshold is what a user gets wrong the first time; a pipeline that
        dropped the cells would make revisiting it cost a re-run over the
        images, while a value recorded in ``pipeline_info.json`` seeds Review's
        Cell Gating tab, where the same cut is one tick to change and one tick
        to undo.
        """
        self._focus_metric_cb = WrapCheckBox(
            "Measure per-cell focus (boundary sharpness)", sec)
        self._focus_metric_cb.setChecked(True)
        self._focus_metric_cb.setToolTip(
            "Adds one column per cell: how sharp the nucleus's own boundary is, "
            "in pixels, so it compares across cells, fields and plates and can "
            "flag a soft part of a field. Costs a few percent of the run; off, "
            "the dataset carries no focus evidence and asking later needs a "
            "re-run.")
        sec.layout().addWidget(self._focus_metric_cb)

        rf = self._row(sec, "Out of focus below")
        self._focus_min_edit = self._entry(rf, "", width=80)
        self._focus_min_edit.setPlaceholderText("none")
        self._focus_min_edit.setToolTip(
            "The focus value below which a cell is out of focus. Recorded with "
            "the dataset, not applied here: Review's Cell Gating excludes the "
            "cells and can undo it. Leave blank unless you have measured one — "
            "it depends mostly on the microscope, and Review ▸ Cell Gating "
            "shows where to cut.")
        rf.layout().addStretch(1)

        focus_hint = QLabel(
            "Recorded with the dataset, not applied here — Review's Cell Gating "
            "tab excludes the cells and can undo it. Find your number in "
            "Review ▸ Image Browser ▸ Playground.", sec)
        focus_hint.setObjectName("Muted")
        focus_hint.setWordWrap(True)
        sec.layout().addWidget(focus_hint)

        # The cut is meaningless without the column, so the whole group hides
        # with it rather than sitting greyed out — the same treatment the
        # debris bounds get above, and for the same reason. Toggle the *parent*
        # rows: hiding the ``_row()`` cell alone strands the "Out of focus
        # below" label, which lives in the cell's parent.
        self._focus_rows = (rf.parentWidget(), focus_hint)

        # Visible in the state where the rows above are NOT: the metric is off
        # and a cut has been typed anyway. Without it that number is collected
        # by the form, skipped by the validator (its gate is falsy), dropped by
        # the service, and never mentioned -- a value the user entered and
        # watched disappear with the row it sat in.
        self._focus_dropped_hint = QLabel(
            "The threshold you typed is not being recorded, because the "
            "measurement above is switched off. Tick it to keep the number "
            "with the dataset.", sec)
        self._focus_dropped_hint.setObjectName("Notice")
        self._focus_dropped_hint.setWordWrap(True)
        self._focus_dropped_hint.setVisible(False)
        sec.layout().addWidget(self._focus_dropped_hint)

        def _sync_focus(_=None) -> None:
            on = self._focus_metric_cb.isChecked()
            for row in self._focus_rows:
                row.setVisible(on)
            self._focus_dropped_hint.setVisible(
                not on and bool(self._focus_min_edit.text().strip()))
        self._focus_metric_cb.toggled.connect(_sync_focus)
        self._focus_min_edit.textChanged.connect(_sync_focus)

        # **Remembered between sessions**, alone among this form's fields, and
        # for the reason its own tooltip gives: the cut is a property of the
        # microscope rather than of the plate, so a user typing it afresh on
        # every run is being asked to re-derive a number the application just
        # told them carries between plates. Written on commit and seeded at
        # build; a dataset restore (``analyze.pinfo._apply_focus_settings``)
        # runs after the build and therefore still wins, which is the right
        # precedence -- what THIS dataset recorded beats what this user last
        # typed.
        self._focus_min_edit.editingFinished.connect(self._remember_focus_min)
        self._seed_focus_min_from_settings()
        _sync_focus()

    # ── the remembered cut ──────────────────────────────────────────────────

    #: Where the cut is kept between sessions. Namespaced under ``analyze/``
    #: so a second remembered field later is an obvious sibling rather than a
    #: bare key at the top level of everyone's settings file.
    FOCUS_MIN_SETTING = "analyze/focus_min"

    def _remember_focus_min(self) -> None:
        from core.app_settings import app_settings
        from core.safe import swallow

        text = self._focus_min_edit.text().strip()
        with swallow("remembering the focus cut"):
            settings = app_settings()
            if text:
                settings.setValue(self.FOCUS_MIN_SETTING, text)
            else:
                # A cleared box is a decision too, and leaving the old value in
                # the file would put it back on the next launch.
                settings.remove(self.FOCUS_MIN_SETTING)

    def _seed_focus_min_from_settings(self) -> None:
        from core.app_settings import app_settings
        from core.safe import swallow

        if self._focus_min_edit.text().strip():
            return
        with swallow("reading the remembered focus cut"):
            value = app_settings().value(self.FOCUS_MIN_SETTING, "")
            if value:
                self._focus_min_edit.setText(str(value))
