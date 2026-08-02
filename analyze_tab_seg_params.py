"""Analyze-tab per-method segmentation parameters, split from analyze_tab_form.py.

Each segmentation routine exposes a different set of knobs, so the form shows a
one-page-per-method stack and swaps the page when the routine changes; the
StarDist detection thresholds sit below the stack because both StarDist methods
share them. ``AnalyzeSegParamsMixin`` is mixed into ``AnalyzeTabFormMixin``, so
every widget it builds lands on the ``AnalyzeTab`` instance under the same
``self._<name>`` attribute it had when this code lived in the form — the
pipeline-info restore in ``analyze_tab_pinfo.py`` and the option collection in
``analyze_tab_form._collect_run_options`` reach them unchanged.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QSizePolicy, QVBoxLayout, QWidget

# The two routines that run StarDist, and therefore take its thresholds. Kept
# next to the controls that depend on it rather than imported from the pipeline:
# this is a GUI-side view of the method vocabulary, and the module must not pull
# in ``process_microscopy`` just to name two strings.
_STARDIST_METHODS = ("stardist_nuclei", "stardist_seeded_watershed_cell")

_PROB_TOOLTIP = (
    "StarDist object-probability threshold (0-1). Candidate nuclei whose "
    "predicted objectness falls below it are discarded before the polygon "
    "overlap pass.\n"
    "• LOWER detects more nuclei — dim, out-of-focus and marginal ones included. "
    "Turn it down when a plate is missing real cells.\n"
    "• HIGHER keeps only confident detections. Turn it up when debris is being "
    "counted as cells.\n"
    "Blank = use the threshold the pretrained model ships with (~0.479)."
)

_NMS_TOOLTIP = (
    "StarDist non-maximum-suppression overlap threshold (0-1), applied to the "
    "predicted nucleus polygons.\n"
    "• HIGHER lets more overlapping nuclei survive — recovers touching nuclei on "
    "a confluent field, at the risk of counting one nucleus twice.\n"
    "• LOWER merges overlapping polygons more aggressively — removes duplicates, "
    "but can fuse genuine neighbours into one.\n"
    "Blank = use the pretrained model's own value (0.3)."
)


class AnalyzeSegParamsMixin:
    """Builds the per-method segmentation parameter controls."""

    def _build_seg_param_stack(self, sec: QWidget) -> None:
        """The per-method parameter pages plus the shared StarDist thresholds.

        Called from ``_build_channel_tokens_section`` right after the fluorescent
        channel rows, so the parameters sit directly under the method picker that
        selects them.
        """
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
        # The StarDist thresholds live outside the stack (both StarDist methods
        # take them), so their visibility rides the same page change rather than
        # needing a second hook in _refresh_segmentation_hints.
        self._seg_param_stack.currentChanged.connect(
            lambda _=None: self._sync_stardist_param_rows())
        sec.layout().addWidget(self._seg_param_stack)

        def _page() -> QWidget:
            p = QWidget()
            v = QVBoxLayout(p)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            return p

        # StarDist (nuclei): no extra parameters beyond the shared thresholds.
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

        self._build_stardist_threshold_rows(sec)

    def _build_stardist_threshold_rows(self, sec: QWidget) -> None:
        """The two StarDist detection thresholds, shared by both StarDist pages.

        Blank means "use the threshold baked into the pretrained weights", which
        is what the pipeline does with its negative sentinel — so an untouched
        form runs exactly the segmentation it ran before these fields existed.
        """
        prob_cell = self._row(sec, "Nucleus probability")
        self._stardist_prob_thresh_edit = self._entry(prob_cell, "", width=70)
        self._stardist_prob_thresh_edit.setPlaceholderText("auto")
        self._stardist_prob_thresh_edit.setToolTip(_PROB_TOOLTIP)
        prob_cell.layout().addWidget(self._info_icon(prob_cell, _PROB_TOOLTIP))
        prob_cell.layout().addStretch(1)

        nms_cell = self._row(sec, "Overlap (NMS)")
        self._stardist_nms_thresh_edit = self._entry(nms_cell, "", width=70)
        self._stardist_nms_thresh_edit.setPlaceholderText("auto")
        self._stardist_nms_thresh_edit.setToolTip(_NMS_TOOLTIP)
        nms_cell.layout().addWidget(self._info_icon(nms_cell, _NMS_TOOLTIP))
        nms_cell.layout().addStretch(1)

        hint = QLabel("Both blank = the pretrained model's own thresholds. Lower "
                      "probability finds more (dimmer) nuclei; higher overlap "
                      "keeps more touching nuclei apart. Tune them against a real "
                      "frame in Review → Image Browser → Playground.", sec)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        sec.layout().addWidget(hint)

        # _row() returns the right-hand cell; the label lives in its parent, so
        # visibility is toggled on the parent rows (the same trap the QC section
        # documents — hiding only the cell strands the label).
        self._stardist_param_rows = (prob_cell.parentWidget(),
                                     nms_cell.parentWidget(), hint)
        self._sync_stardist_param_rows()

    def _sync_stardist_param_rows(self) -> None:
        """Show the StarDist thresholds only for the routines that use them."""
        rows = getattr(self, "_stardist_param_rows", None)
        if not rows:
            return
        on = self._seg_method() in _STARDIST_METHODS
        for w in rows:
            w.setVisible(on)

    def _restore_stardist_thresholds(self, d: dict) -> None:
        """Put a prior run's recorded thresholds back into the two fields.

        The sidecar records the pipeline's negative sentinel for "the model's own
        default", which this form spells as an empty field. Writing ``-1.0`` into
        the box would read as a real threshold — and would be one the next run
        then had to reject — so the sentinel is translated back to blank.
        """
        for attr, key in (("_stardist_prob_thresh_edit", "stardist_prob_thresh"),
                          ("_stardist_nms_thresh_edit", "stardist_nms_thresh")):
            widget = getattr(self, attr, None)
            if widget is None or key not in d:
                continue
            try:
                value = float(d[key])
            except (TypeError, ValueError):
                continue
            widget.setText("" if value < 0 else f"{value:g}")

    def _stardist_threshold_values(self) -> tuple[str, str]:
        """The two threshold fields as raw text (blank = model default).

        Returned as text rather than floats so ``build_pipeline_args`` applies
        the one blank/auto/out-of-range rule for every caller, instead of this
        form inventing a second one.
        """
        return (self._stardist_prob_thresh_edit.text().strip(),
                self._stardist_nms_thresh_edit.text().strip())
