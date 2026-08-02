"""Analyze-tab **Preview** page — see the settings on a real frame before running.

The Analyze pane's right side is a two-page stack: **Run** (the progress
dashboard and log, unchanged) and **Preview**, which renders one frame from the
input folder exactly as the form is currently configured.

**The Preview page owns no parameter controls.** Flat-field, background, radius,
mesh, FFT and the StarDist thresholds are read straight off the form's own
widgets. That is the whole point: the previous arrangement kept a second copy of
those settings in the Review pane's Playground and a button to copy one into the
other, so a tuned preview could describe a run that never happened. With one set
of widgets there is nothing to copy, nothing to forget, and no way for "what I
tuned" and "what will run" to differ.

Nothing here is All-Well-specific plumbing invented for the occasion: frames are
found and processed by ``services.frame_preview`` (which calls the pipeline's own
``correct_single_frame`` / ``segment_nuclei``), rendered by
``well_viewer.core.frame_render.build_frame_rgb``, shown by
``widgets.frame_preview.FramePreview``, and segmented off-thread through
``well_viewer.core.worker_bridge.run_off_thread``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

_MAX_PREVIEW_FRAMES = 400

_PREVIEW_INTRO = (
    "Pick an input folder to preview a frame from it. Everything here is drawn "
    "with the settings in the form on the left — change one and the preview "
    "follows."
)


class AnalyzePreviewMixin:
    """Builds the Run | Preview stack and drives the Preview page."""

    # ── the two-page right pane ──────────────────────────────────────────────

    def _build_right_pane(self, parent: QWidget) -> None:
        """Replace what used to be a bare log panel with a Run | Preview stack."""
        from well_viewer.core.centre_view import NamedPageStack
        from widgets.segmented_control import SegmentedControl

        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        switch_row = QWidget(parent)
        srl = QHBoxLayout(switch_row)
        srl.setContentsMargins(12, 6, 12, 6)
        self._right_switch = SegmentedControl(switch_row)
        for label, data in (("Run", "run"), ("Preview", "preview")):
            self._right_switch.addSegment(label, data=data)
        self._right_switch.setCurrentByData("run")
        self._right_switch.setToolTip(
            "Run — the progress dashboard and pipeline log.\n"
            "Preview — one frame from the input folder, drawn with the settings "
            "in this form.")
        self._right_switch.currentChanged.connect(
            lambda _i: self._on_right_page_changed())
        srl.addWidget(self._right_switch)
        srl.addStretch(1)
        outer.addWidget(switch_row)

        rule = QFrame(parent)
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("HRule")
        outer.addWidget(rule)

        self._right_stack = NamedPageStack(parent)
        outer.addWidget(self._right_stack, 1)

        run_page = QWidget()
        self._right_stack.addPage("run", run_page)
        self._build_log(run_page)          # unchanged; this page IS the old pane

        preview_page = QWidget()
        self._right_stack.addPage("preview", preview_page)
        self._build_preview_page(preview_page)

    def _on_right_page_changed(self) -> None:
        name = self._right_switch.currentData() or "run"
        self._right_stack.setCurrentByName(str(name))
        if name == "preview":
            # Entering the page is the moment to reconcile with a form the user
            # may have edited while looking at the Run page.
            self._reload_preview_frames()

    def _show_run_page(self) -> None:
        """Bring the Run page forward — called when a run starts, so pressing Run
        from the Preview page does not leave the user watching a still image
        while the pipeline works."""
        switch = getattr(self, "_right_switch", None)
        if switch is not None:
            switch.setCurrentByData("run")

    # ── the Preview page ─────────────────────────────────────────────────────

    def _build_preview_page(self, parent: QWidget) -> None:
        from widgets.frame_preview import FramePreview

        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # Frame chooser. These are *view* controls — which image to look at —
        # not run parameters, so they are the only combos this page owns.
        chooser = QWidget(parent)
        cl = QHBoxLayout(chooser)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        cl.addWidget(QLabel("Frame", chooser))
        self._preview_frame_cb = QComboBox(chooser)
        self._preview_frame_cb.setMinimumWidth(220)
        self._preview_frame_cb.currentIndexChanged.connect(
            lambda _i: self.refresh_preview())
        cl.addWidget(self._preview_frame_cb, 1)
        reload_btn = QPushButton("Reload", chooser)
        reload_btn.setProperty("variant", "secondary")
        reload_btn.setToolTip("Re-scan the input folder for frames.")
        reload_btn.clicked.connect(lambda _=False: self._reload_preview_frames())
        cl.addWidget(reload_btn)
        layout.addWidget(chooser)

        # Display controls (also view-only).
        display = QWidget(parent)
        dl = QHBoxLayout(display)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        dl.addWidget(QLabel("LUT", display))
        self._preview_lo_edit = QLineEdit(display)
        self._preview_lo_edit.setPlaceholderText("auto")
        self._preview_lo_edit.setFixedWidth(60)
        self._preview_hi_edit = QLineEdit(display)
        self._preview_hi_edit.setPlaceholderText("auto")
        self._preview_hi_edit.setFixedWidth(60)
        for e in (self._preview_lo_edit, self._preview_hi_edit):
            e.editingFinished.connect(self.refresh_preview)
            dl.addWidget(e)
        self._preview_segment_btn = QPushButton("Segment", display)
        self._preview_segment_btn.setToolTip(
            "Run StarDist on this frame with the thresholds set in the form.\n"
            "The first Segment of a session loads TensorFlow (~10-30 s, and "
            "roughly 1-2 GB that stays in use until you quit); later ones take a "
            "few seconds.")
        self._preview_segment_btn.clicked.connect(
            lambda _=False: self._segment_preview())
        dl.addWidget(self._preview_segment_btn)
        for text, slot in (("−", "zoom_out"), ("+", "zoom_in"), ("Fit", "fit")):
            b = QPushButton(text, display)
            b.setProperty("variant", "secondary")
            if text != "Fit":
                b.setFixedWidth(28)
            b.clicked.connect(lambda _=False, s=slot: self._preview_zoom(s))
            dl.addWidget(b)
        dl.addStretch(1)
        layout.addWidget(display)

        self._preview_canvas = FramePreview(parent)
        self._preview_canvas.pixelHovered.connect(self._on_preview_hover)
        layout.addWidget(self._preview_canvas, 1)

        self._preview_status = QLabel(_PREVIEW_INTRO, parent)
        self._preview_status.setObjectName("Muted")
        self._preview_status.setWordWrap(True)
        layout.addWidget(self._preview_status)

        # Per-frame scratch, never persisted.
        self._preview_frames: list = []
        self._preview_arr = None
        self._preview_labels = None
        self._preview_labels_key = None
        self._preview_bridge = None

    # ── frame discovery ──────────────────────────────────────────────────────

    def _reload_preview_frames(self) -> None:
        """Re-scan the input folder and repopulate the frame chooser."""
        cb = getattr(self, "_preview_frame_cb", None)
        if cb is None:
            return
        from services.frame_preview import sample_input_frames

        folder = self._input_edit.text().strip()
        self._preview_frames = []
        if folder and Path(folder).is_dir():
            try:
                self._preview_frames = sample_input_frames(
                    folder, schema=self._preview_schema(),
                    sep=self._filename_sep_edit.text() or "_",
                    limit=_MAX_PREVIEW_FRAMES)
            except Exception as exc:
                self._preview_status.setText(f"Could not scan {folder}: {exc}")
                return
        # Lead with the segmentation channel — it is the one StarDist sees, so
        # it is the frame the thresholds are actually being chosen against.
        seg_token = (self._nuclear_token_edit.text() or "").strip().lower()
        if seg_token:
            self._preview_frames.sort(
                key=lambda r: (r.channel or "").lower() != seg_token)

        blocked = cb.blockSignals(True)
        try:
            cb.clear()
            for ref in self._preview_frames:
                cb.addItem(ref.label())
        finally:
            cb.blockSignals(blocked)

        if not self._preview_frames:
            self._preview_arr = None
            self._preview_canvas.clear()
            self._preview_status.setText(
                _PREVIEW_INTRO if not folder
                else f"No images found in {folder}.")
            return
        self.refresh_preview()

    def _preview_schema(self):
        from util.schema import parse_schema
        try:
            return parse_schema(self._build_schema_arg())
        except Exception:
            return None

    def _selected_preview_frame(self):
        idx = self._preview_frame_cb.currentIndex()
        if 0 <= idx < len(self._preview_frames):
            return self._preview_frames[idx]
        return None

    # ── render ───────────────────────────────────────────────────────────────

    def _preview_correction_kwargs(self) -> dict:
        """The correction settings **from the form**, not from this page."""
        from analyze_tab_fields import _safe_seg_float
        return dict(
            flatfield_mode=self._flatfield_cb.currentText(),
            background_method=self._background_cb.currentText(),
            radius=int(_safe_seg_float(self._tophat_radius_nir_edit.text(), 50.0)),
            fft_small=_safe_seg_float(self._fft_small_nir_edit.text(), 0.0),
            mesh_ratio=_safe_seg_float(self._mesh_ratio_edit.text(), 3.0),
            mesh_sigma=_safe_seg_float(self._mesh_sigma_edit.text(), 3.0),
        )

    def _preview_thresholds(self) -> tuple:
        """The StarDist thresholds **from the form**, normalised by the same
        ``parse_threshold`` the CLI uses."""
        from pipeline.stardist import parse_threshold
        out = []
        for edit in (self._stardist_prob_thresh_edit, self._stardist_nms_thresh_edit):
            try:
                out.append(parse_threshold(edit.text()))
            except ValueError:
                out.append(None)
        return tuple(out)

    def _preview_key(self) -> tuple:
        """Identity of what a set of preview labels describes: the frame plus
        every setting that decides the pixels StarDist is fed."""
        ref = self._selected_preview_frame()
        kw = self._preview_correction_kwargs()
        return (getattr(ref, "stem", None), tuple(sorted(kw.items())),
                self._preview_thresholds())

    def refresh_preview(self) -> None:
        """Redraw the Preview page from the form's current settings.

        Public because the form's own widgets call it: it is wired to their
        change signals in :meth:`_wire_preview_to_form`, which is what makes the
        preview follow the form with no explicit sync step.
        """
        if getattr(self, "_preview_canvas", None) is None:
            return
        if not self._right_stack_showing_preview():
            return                      # don't pay for a redraw nobody can see
        from services.frame_preview import correct_frame, load_frame
        from well_viewer.core.frame_render import build_frame_rgb

        ref = self._selected_preview_frame()
        if ref is None:
            self._preview_canvas.clear()
            self._preview_status.setText(_PREVIEW_INTRO)
            return
        arr = load_frame(ref)
        if arr is None:
            self._preview_canvas.clear()
            self._preview_status.setText(f"Could not read {ref.stem}.")
            return

        kwargs = self._preview_correction_kwargs()
        corrected = correct_frame(arr, **kwargs)
        self._preview_arr = corrected

        labels = None
        stale = False
        if self._preview_labels is not None:
            if self._preview_labels_key == self._preview_key():
                labels = self._preview_labels
            else:
                stale = True

        lo, hi = self._preview_lut(corrected)
        self._preview_canvas.set_rgb(build_frame_rgb(
            corrected, labels, lo=lo, hi=hi, cmap_name="Greys_r",
            show_boundary=labels is not None))
        self._preview_status.setText(self._preview_status_text(ref, kwargs, labels, stale))

    def _right_stack_showing_preview(self) -> bool:
        stack = getattr(self, "_right_stack", None)
        return stack is not None and stack.currentName() == "preview"

    def _preview_lut(self, arr) -> tuple:
        def _num(edit, fallback):
            text = str(edit.text() or "").strip().lower()
            if not text or text == "auto":
                return fallback
            try:
                return float(text)
            except ValueError:
                return fallback
        lo = _num(self._preview_lo_edit, float(arr.min()) if arr.size else 0.0)
        hi = _num(self._preview_hi_edit, float(arr.max()) if arr.size else 1.0)
        return (lo, hi + 1.0) if hi <= lo else (lo, hi)

    def _preview_status_text(self, ref, kwargs: dict, labels, stale: bool) -> str:
        bits = [f"{ref.label()} — {kwargs['flatfield_mode']}+"
                f"{kwargs['background_method']}"]
        # A correction the form asks for but one frame cannot do is NAMED, not
        # quietly skipped: otherwise the preview would show an uncorrected frame
        # while the form still claims a correction is applied.
        from services.frame_preview import correction_needs_a_plate
        dropped = correction_needs_a_plate(kwargs["flatfield_mode"],
                                           kwargs["background_method"])
        if dropped:
            bits.append(
                f"⚠ not applied in this preview: {' and '.join(dropped)} — the "
                f"field is estimated from a whole reference well, which one "
                f"frame cannot do. The run itself will apply it.")
        if labels is not None:
            prob, nms = self._preview_thresholds()
            import numpy as np
            bits.append(
                f"StarDist: {int(np.max(labels)) if labels.size else 0} nuclei "
                f"(prob={'model' if prob is None else f'{prob:g}'}, "
                f"nms={'model' if nms is None else f'{nms:g}'})")
        elif stale:
            bits.append("⚠ segmentation is stale (the frame or the settings "
                        "changed) — press Segment again")
        return "   ".join(bits)

    def _preview_zoom(self, slot: str) -> None:
        getattr(self._preview_canvas, slot)()

    def _on_preview_hover(self, x: int, y: int) -> None:
        arr = self._preview_arr
        if arr is None:
            return
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QToolTip
        QToolTip.showText(QCursor.pos(), f"({x}, {y})  =  {float(arr[y, x]):.1f}")

    # ── segmentation ─────────────────────────────────────────────────────────

    def _segment_preview(self) -> None:
        """Segment the displayed frame with the form's thresholds, off-thread."""
        if self._preview_arr is None:
            self._preview_status.setText("Nothing to segment — pick a frame first.")
            return
        from well_viewer.core.worker_bridge import run_off_thread

        prob, nms = self._preview_thresholds()
        key = self._preview_key()
        arr = self._preview_arr
        self._preview_segment_btn.setEnabled(False)
        self._preview_status.setText(
            "StarDist: segmenting… (the first run of a session also loads "
            "TensorFlow — ~10-30 s, and it stays resident)")

        def _on_done(labels) -> None:
            self._preview_segment_btn.setEnabled(True)
            self._preview_labels = labels
            self._preview_labels_key = key
            self.refresh_preview()

        def _on_failed(message: str) -> None:
            self._preview_segment_btn.setEnabled(True)
            self._preview_labels = None
            self._preview_labels_key = None
            self._preview_status.setText(f"StarDist failed: {message}")

        def _compute():
            from services.frame_preview import segment_frame
            return segment_frame(arr, prob_thresh=prob, nms_thresh=nms)

        self._preview_bridge = run_off_thread(_compute, on_done=_on_done,
                                              on_failed=_on_failed)

    # ── keeping the preview tied to the form ─────────────────────────────────

    def _wire_preview_to_form(self) -> None:
        """Redraw whenever a form widget the preview reads changes.

        Called once at the end of form construction. Segmentation is *not* wired
        here — inference takes seconds and these signals fire on every keystroke,
        so it stays behind the explicit Segment button.
        """
        for attr in ("_flatfield_cb", "_background_cb"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.currentIndexChanged.connect(lambda _i: self.refresh_preview())
        for attr in ("_tophat_radius_nir_edit", "_fft_small_nir_edit",
                     "_mesh_ratio_edit", "_mesh_sigma_edit",
                     "_stardist_prob_thresh_edit", "_stardist_nms_thresh_edit"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.editingFinished.connect(self.refresh_preview)
        # A new input folder means a whole new set of frames.
        if getattr(self, "_input_edit", None) is not None:
            self._input_edit.textChanged.connect(
                lambda _t: self._reload_preview_frames())
