"""Analyze-tab per-method segmentation parameters, split from analyze/form.py.

Each segmentation routine exposes a different set of knobs, so the form shows a
one-page-per-method stack and swaps the page when the routine changes; the
StarDist detection thresholds sit below the stack because both StarDist methods
share them. ``AnalyzeSegParamsMixin`` is mixed into ``AnalyzeTabFormMixin``, so
every widget it builds lands on the ``AnalyzeTab`` instance under the same
``self._<name>`` attribute it had when this code lived in the form — the
pipeline-info restore in ``analyze/pinfo.py`` and the option collection in
``analyze.form._collect_run_options`` reach them unchanged.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QSizePolicy, QVBoxLayout, QWidget

from util.channel_roles import phase_role_conflicts

# The two routines that run StarDist, and therefore take its thresholds. Kept
# next to the controls that depend on it rather than imported from the pipeline:
# this is a GUI-side view of the method vocabulary, and the module must not pull
# in ``process_microscopy`` just to name two strings.
_STARDIST_METHODS = ("stardist_nuclei", "stardist_seeded_watershed_cell")
# Both take the cell diameter: general Cellpose and the label-free preset run the
# same model, and the diameter sets the scale each segments at.
_CELLPOSE_DIAMETER_METHODS = ("cellpose", "cellpose_labelfree")

#: What the Nucleus probability field starts with on a fresh form.
#:
#: The pretrained model's own threshold (~0.479) is conservative for these
#: plates, so the form ships a more sensitive starting point rather than asking
#: for it to be typed in every time. **This decides what an untouched run does**:
#: a run started without touching this field detects more nuclei — dim,
#: out-of-focus and marginal ones included — than the model's default would.
#:
#: Text, not a float, because the field is text and every rule about it (blank /
#: ``auto`` / out-of-range) is applied to text by ``parse_threshold``. Clearing
#: the field still means the model's own threshold; this is a starting value,
#: not a floor.
#:
#: The CLI's ``--stardist_prob_thresh`` deliberately keeps its own default (the
#: ``-1.0`` "use the model's" sentinel), so a script that passes no flag is
#: unaffected by this. The GUI emits the number explicitly, so a GUI run is
#: still fully described by its command line and its ``pipeline_info.json``.
DEFAULT_PROB_THRESH_TEXT = "0.2"


def _threshold_help() -> tuple[str, str]:
    """``(prob, nms)`` help text, from the pipeline's single definition.

    The Playground shows the same two strings, and two panels describing the
    same two numbers in their own words is how they drifted apart the first
    time. Imported inside the function rather than at module scope for the same
    reason the method names above are hardcoded: importing ``pipeline`` here
    would pull scipy and scikit-image into the Analyze form's import cost, and
    this runs only when the form is actually built.
    """
    from pipeline.stardist import NMS_THRESH_HELP, PROB_THRESH_HELP
    return PROB_THRESH_HELP, NMS_THRESH_HELP


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
        from widgets.page_stack import NamedPageStack
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
        self._seg_param_stack.currentChanged.connect(
            lambda _=None: self._sync_cellpose_diameter_row())
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

        # Label-free (Cellpose preset on the Phase channel). Its one exposed
        # parameter, the diameter, is the shared row below the stack.
        lf = _page()
        lf_lbl = QLabel("Segments the channel marked 'Phase' above, using a Cellpose "
                        "brightfield preset (cyto3).", lf)
        lf_lbl.setObjectName("Muted")
        lf_lbl.setWordWrap(True)
        lf.layout().addWidget(lf_lbl)
        self._seg_param_stack.addPage("cellpose_labelfree", lf)

        # Classical (Otsu + watershed): no extra parameters.
        self._seg_param_stack.addPage("classical_threshold", _page())

        self._build_cellpose_diameter_row(sec)
        self._build_stardist_threshold_rows(sec)

    def _build_cellpose_diameter_row(self, sec: QWidget) -> None:
        """The cell diameter, shared by both Cellpose methods.

        It lives outside the stack, like the StarDist thresholds, because the
        label-free preset needs it as much as general Cellpose does, and the
        label-free page had no way to set it at all.

        What it does is Cellpose's (``CellposeModel.eval``, 3.1): a diameter *d*
        resizes every image by ``diam_mean / d`` before segmenting, where
        ``diam_mean`` is the cell size the model was trained at (30 px for the
        cyto models, which is the ``model diam_mean`` line Cellpose logs). The
        time per field therefore scales with ``(30 / d)²`` — 15 px is about four
        times slower, 60 px about four times faster. At 0 nothing is resized and
        nothing is estimated: this path builds ``CellposeModel``, which has no
        size model, so the image is segmented as if its cells were 30 px.
        """
        tip = ("Typical cell diameter in pixels. Cellpose resizes each image so "
               "cells this size become the 30 px its model was trained on, so "
               "smaller values are slower (15 px: about 4x) and larger ones "
               "faster. 0 skips the resize and segments as if cells were 30 px.")
        d_cell = self._row(sec, "Cell diameter (px, 0=model's 30)", tooltip=tip)
        self._cellpose_diameter_edit = self._entry(d_cell, "0", width=70)
        self._cellpose_diameter_edit.setToolTip(tip)
        d_cell.layout().addStretch(1)
        # _row() returns the right-hand cell; the label is in its parent, so the
        # parent row is what gets hidden.
        self._cellpose_diameter_row = d_cell.parentWidget()
        self._sync_cellpose_diameter_row()

    def _sync_cellpose_diameter_row(self) -> None:
        """Show the diameter only for the methods that run Cellpose."""
        row = getattr(self, "_cellpose_diameter_row", None)
        if row is not None:
            row.setVisible(self._seg_method() in _CELLPOSE_DIAMETER_METHODS)

    def _build_stardist_threshold_rows(self, sec: QWidget) -> None:
        """The two StarDist detection thresholds, shared by both StarDist pages.

        Blank means "use the threshold baked into the pretrained weights", which
        is what the pipeline does with its negative sentinel.

        The two fields start differently, and the asymmetry is deliberate.
        Overlap starts blank, so an untouched form runs the model's own NMS.
        Probability starts at :data:`DEFAULT_PROB_THRESH_TEXT` — the model's own
        value is conservative for these plates — so an untouched form runs a
        **more sensitive detection** than the pretrained default. Clearing the
        field returns to the model's.
        """
        prob_help, nms_help = _threshold_help()
        # The help goes on the row too, so hovering the words "Nucleus
        # probability" answers — not only hovering the narrow entry box.
        prob_cell = self._row(sec, "Nucleus probability", tooltip=prob_help)
        self._stardist_prob_thresh_edit = self._entry(
            prob_cell, DEFAULT_PROB_THRESH_TEXT, width=70)
        self._stardist_prob_thresh_edit.setPlaceholderText("auto")
        self._stardist_prob_thresh_edit.setToolTip(prob_help)
        prob_cell.layout().addWidget(self._info_icon(prob_cell, prob_help))
        prob_cell.layout().addStretch(1)

        nms_cell = self._row(sec, "Overlap (NMS)", tooltip=nms_help)
        self._stardist_nms_thresh_edit = self._entry(nms_cell, "", width=70)
        self._stardist_nms_thresh_edit.setPlaceholderText("auto")
        self._stardist_nms_thresh_edit.setToolTip(nms_help)
        nms_cell.layout().addWidget(self._info_icon(nms_cell, nms_help))
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

    # ── hints + validation for the selected routine ──────────────────────────

    def _segmentation_validation_errors(self, *, channels: bool = True) -> list[str]:
        """What stops the selected routine running, as user-facing sentences.

        *channels* False skips every check that compares the form's channel
        tokens with each other — the segmentation channel, the Phase mark —
        because run-time detection is about to replace those tokens, and
        ``services.run_detect.channel_option_errors`` asks the same questions
        of the detected ones. What detection cannot supply (a cytoplasm token
        for watershed, a positive minimum area) is still required here.
        """
        errors: list[str] = []
        method = self._seg_method()
        if method == "stardist_seeded_watershed_cell":
            cyto = self._cytoplasm_edit.text().strip()
            nuc = self._nuclear_token_edit.text().strip()
            if not cyto:
                errors.append("Watershed mode requires a cytoplasm token.")
            if channels and cyto and cyto == nuc:
                errors.append("Cytoplasm token must differ from nuclear token.")
            try:
                area = int(self._min_nucleus_area_edit.text().strip())
                if area <= 0:
                    errors.append("Minimum nucleus area must be a positive integer.")
            except ValueError:
                errors.append("Minimum nucleus area must be a positive integer.")
        elif method == "cellpose_labelfree" and channels:
            if not self._phase_token():
                errors.append(
                    "Label-free segmentation requires exactly one channel marked "
                    "'Phase' (the brightfield image to segment).")
        if channels:
            errors.extend(self._phase_collision_errors(method))
        return errors

    def _phase_collision_errors(self, method: str) -> list[str]:
        """Reject a Phase channel that is also a segmentation input.

        The rule itself lives in :func:`util.channel_roles.phase_role_conflicts`,
        shared with ``process_microscopy``'s argument validation — the CLI and
        remote runs never pass through this form.
        """
        phase = self._phase_token()
        slots = phase_role_conflicts(
            method, phase_token=phase,
            nuclear_token=self._nuclear_token_edit.text(),
            cytoplasm_token=self._cytoplasm_edit.text(),
        )
        label = {"nuclear": "segmentation channel", "cytoplasm": "cytoplasm channel"}
        return [
            f"Channel '{phase}' is marked 'Phase' and is also the {label[slot]}. "
            "Phase/brightfield images skip background correction, so this routine "
            "would segment an uncorrected image — pick a different "
            f"{label[slot]}, or switch to the Label-free method."
            for slot in slots
        ]

    def _collapse_seg_param_stack(self) -> None:
        """Size the per-method parameter stack to its *current* page only.

        A ``QStackedWidget`` reports the max sizeHint over all pages, so without
        this the empty StarDist page reserves the tall Cellpose page's height
        (the gap the user saw under channel tokens). Giving every non-current
        page a vertically-Ignored policy drops it from the height calculation.
        """
        stack = getattr(self, "_seg_param_stack", None)
        if stack is None:
            return
        cur = stack.currentIndex()
        for i in range(stack.count()):
            page = stack.widget(i)
            pol = page.sizePolicy()
            pol.setVerticalPolicy(
                QSizePolicy.Preferred if i == cur else QSizePolicy.Ignored)
            page.setSizePolicy(pol)
        stack.adjustSize()
        stack.updateGeometry()

    def _refresh_segmentation_hints(self) -> None:
        if not hasattr(self, "_segmentation_hint_lbl"):
            return
        method = self._seg_method()
        # Show only the selected routine's parameter page.
        if hasattr(self, "_seg_param_stack"):
            self._seg_param_stack.setCurrentByName(method)
            self._collapse_seg_param_stack()
        # The Label-free preset segments the Phase channel — the explicit
        # Segmentation-channel field doesn't apply there (it's taken at runtime).
        phase_locked = method == "cellpose_labelfree"
        if hasattr(self, "_nuclear_row_widget"):
            self._nuclear_row_widget.setVisible(not phase_locked)
        errors = self._segmentation_validation_errors(channels=not self._auto_detect_on())
        if errors:
            self._segmentation_hint_lbl.setText("  ".join(errors))
            return
        # Each hint leads with *when to use this* so a first-time user can pick.
        hint = {
            "stardist_nuclei": "Best default for a nuclear stain (DAPI/Hoechst/NIR): StarDist segments the 'Segmentation channel' above into nuclei.",
            "stardist_seeded_watershed_cell": "Use for whole-cell (cytoplasm) measurements: StarDist seeds + a cytoplasm mask; also quantifies the cytoplasm channel.",
            "cellpose": "Use for non-nuclear or tricky morphologies: Cellpose on the 'Segmentation channel' above (fluorescent or brightfield).",
            "cellpose_labelfree": "Use when there's no stain at all: Cellpose (cyto3) on the channel marked 'Phase' — label-free. Set the cell diameter when your cells are far from 30 px.",
            "classical_threshold": "Fast model-free fallback for clean, separated objects: Otsu threshold + watershed on the 'Segmentation channel' above.",
        }.get(method, "")
        self._segmentation_hint_lbl.setText(hint)

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
